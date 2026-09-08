"""잡 등록 지점 (P0-9-2 · spec §4.2, §12.5).

## 잡 등록 규약

- **`max_instances=1`** — 같은 잡이 겹쳐 돌면 캔들을 두 번 수집한다. 락은 프로세스 **간**
  중복을, 이것은 프로세스 **내** 중복을 막는다
- **`coalesce=True`** — 밀린 실행을 하나로 합친다. 5분 멈췄다 살아났을 때 5m 잡을 몰아 돌릴
  이유가 없다. 마지막 한 번이 갭을 메운다
- **`misfire_grace_time` = TF 간격의 절반** — 이보다 늦은 실행은 **건너뛴다.** 봉마감 기준
  잡(§4.2)이 다음 봉 시각에 도달했다면 그 실행은 이미 의미가 없다

## 봉마감 "직후"에 돌린다

cron 을 정확히 경계(`second=0`)에 두지 않고 20초 늦춘다. 거래소가 마감 봉을 확정하는 데
시간이 걸리고, 경계에 요청하면 아직 형성 중인 봉을 받는다 (업비트 실측). `drop_unclosed` 가
걸러내지만 그러면 최신 봉을 **한 주기 늦게** 받게 된다.
"""

from collections.abc import Callable, Coroutine
from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from updown.common.config import Settings

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from updown.common.domain.instrument import Timeframe
from updown.common.logging.context import trace_context
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import BrokerAdapter
from updown.marketdata.ingest.backfill import BackfillScope
from updown.marketdata.ingest.collector import collect_timeframe
from updown.marketdata.ingest.integrity import IntegrityThresholds
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.timeframes import interval

_logger = get_logger("apps.engine.scheduler")

AsyncJob = Callable[[], Coroutine[Any, Any, object]]
"""등록 가능한 잡 — 인자 없는 async 함수.

인자는 `functools.partial` 로 묶어 넘긴다. 스케줄러가 인자를 들고 있으면 잡 정의와
호출 규약이 두 곳에 흩어진다.

반환 타입이 `None` 이 아니라 `object` 인 이유: 스케줄러는 반환값을 쓰지 않으므로 잡이
결과 객체를 돌려줘도 된다. `None` 으로 못박으면 `collect_timeframe`(→`CollectionRun`)
처럼 결과를 반환하는 함수를 등록할 수 없고, 결과를 버리는 래퍼를 하나 더 만들어야 한다.
"""

#: Timeframe 별 봉마감 직후 실행을 나타내는 cron 표현.
#:
#: **봉마감 "직후"에 돌리려면 약간 늦춰야 한다** — 거래소가 마감 봉을 확정하는 데 시간이
#: 걸리고, 정확히 경계에 요청하면 아직 미완성인 봉을 받는다 (업비트 실측: 응답에 형성 중인
#: 봉이 포함된다). `drop_unclosed` 가 그것을 걸러내지만, 그러면 최신 봉을 한 주기 늦게
#: 받게 된다. 20초 지연이 그 낭비를 없앤다.
_CRON_BY_TIMEFRAME: dict[Timeframe, dict[str, str]] = {
    Timeframe.M5: {"minute": "*/5", "second": "20"},
    Timeframe.M15: {"minute": "*/15", "second": "20"},
    Timeframe.H1: {"minute": "0", "second": "20"},
    Timeframe.H4: {"hour": "*/4", "minute": "0", "second": "20"},
    Timeframe.D1: {"hour": "0", "minute": "1", "second": "0"},
}


def job_ids(scheduler: AsyncIOScheduler) -> list[str]:
    """등록된 잡 id 목록.

    Args:
        scheduler: 대상 스케줄러.

    Returns:
        잡 id 목록.

    Note:
        APScheduler 는 타입 스텁이 없어 `get_jobs()` 의 원소 타입을 pyright 가 모른다.
        무시 주석을 호출부마다 흩뿌리지 않도록 **여기 한 곳에 격리**한다.
    """
    return [str(job.id) for job in scheduler.get_jobs()]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]


def build_scheduler() -> AsyncIOScheduler:
    """스케줄러를 만든다.

    Returns:
        아직 시작되지 않은 스케줄러.

    Note:
        타임존을 **UTC 로 고정**한다 (spec §12.3, 절대 규칙 #7). 기본값은 시스템 타임존이라
        컨테이너와 로컬에서 잡 실행 시각이 달라진다 — 봉마감 기준 잡에서는 그 차이가
        "왜 봉이 하나 늦게 들어오지"로 나타난다.
    """
    return AsyncIOScheduler(timezone="UTC")


def _traced(name: str, job: AsyncJob) -> AsyncJob:
    """잡을 trace 컨텍스트로 감싼다 (spec §4.14).

    Args:
        name: 잡 이름 (로그용).
        job: 원본 잡.

    Returns:
        감싼 잡.

    Note:
        **잡 실행 1회마다 새 trace_id** 다 (§4.14) — 웹 요청 없는 경로도 추적 가능해야 한다.
        블록을 벗어나면 자동 복원되므로 다음 잡 로그에 이전 잡의 trace_id 가 새지 않는다.

        예외를 삼키지 않는다. APScheduler 가 잡 예외를 잡아 로그를 남기고 다음 실행을
        계속하는데, 여기서 삼키면 "실패했는데 성공처럼 보이는" 로그가 된다 (spec §7).
    """

    async def wrapper() -> None:
        """잡 한 번 — trace_id 를 새로 열고 시작·실패·종료를 로그로 남긴다.

        Raises:
            Exception: 잡의 예외를 로그로 남긴 뒤 그대로 올린다 — APScheduler 가 실패로 센다.
        """
        with trace_context() as trace_id:
            _logger.info("job_started", payload={"job": name, "trace_id": trace_id})
            try:
                await job()
            except Exception as exc:
                _logger.error(
                    "job_failed",
                    payload={"job": name, "error": f"{type(exc).__name__}: {exc}"},
                )
                raise
            _logger.info("job_finished", payload={"job": name})

    return wrapper


def register_timeframe_job(
    scheduler: AsyncIOScheduler,
    name: str,
    timeframe: Timeframe,
    job: AsyncJob,
) -> None:
    """봉마감 직후 실행되는 잡을 등록한다.

    Args:
        scheduler: 대상 스케줄러.
        name: 잡 id. 같은 id 로 다시 등록하면 교체된다.
        timeframe: 실행 주기를 정하는 시간축.
        job: 인자 없는 async 함수.

    Raises:
        KeyError: cron 표가 없는 시간축 (새 Timeframe 추가 시 표를 안 채운 경우).

    Note:
        `misfire_grace_time` 을 **TF 간격의 절반**으로 둔다. 그보다 늦은 실행은 이미 다음 봉
        구간에 들어섰다는 뜻이라 건너뛰는 편이 맞다 — 밀린 실행이 최신 봉을 받는 잡을
        뒤로 밀면 갭이 더 커진다. 밀린 구간은 다음 실행의 **갭 메움**이 처리한다 (P0-8-6).
    """
    cron = _CRON_BY_TIMEFRAME[timeframe]
    grace = int(interval(timeframe).total_seconds() / 2)
    # APScheduler 는 타입 스텁이 없어 pyright strict 가 인자 타입을 모른다. 우리 쪽
    # 시그니처는 위에서 이미 좁혀 두었으므로 여기서만 무시한다.
    scheduler.add_job(  # pyright: ignore[reportUnknownMemberType]
        _traced(name, job),
        trigger=CronTrigger(timezone="UTC", **cron),
        id=name,
        name=name,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=grace,
        replace_existing=True,
    )
    _logger.info(
        "job_registered",
        payload={
            "job": name,
            "timeframe": timeframe.value,
            "cron": cron,
            "misfire_grace_seconds": grace,
        },
    )


def register_candle_collection(
    scheduler: AsyncIOScheduler,
    adapter: BrokerAdapter,
    repository: CandleRepository,
    scope: BackfillScope,
    thresholds: IntegrityThresholds,
) -> list[str]:
    """캔들 주기 수집 잡을 시간축별로 등록한다 (P0-9-7).

    Args:
        scheduler: 대상 스케줄러.
        adapter: 시세 어댑터.
        repository: 캔들 저장소.
        scope: 수집 범위 (유니버스·시간축·갭 파라미터).
        thresholds: 무결성 임계값.

    Returns:
        등록한 잡 id 목록.

    Note:
        **시간축당 잡 1개**이고 그 안에서 유니버스를 순회한다. 종목마다 잡을 만들면
        `max_instances=1` 이 종목 단위가 되어 같은 시간축의 여러 종목이 동시에 요청을 쏘고,
        rate limit 스로틀이 그 경합을 흡수하느라 실행이 길어진다. 순회는 순차이므로
        스로틀이 자연스럽게 간격을 만든다.

        `scope.timeframes` 에서 읽으므로 **설정에 시간축을 추가하면 그대로 등록된다** —
        코드를 고칠 필요가 없다 (D-15).
    """
    registered: list[str] = []
    for timeframe in scope.timeframes:
        name = f"collect_candles_{timeframe.value}"
        register_timeframe_job(
            scheduler,
            name,
            timeframe,
            partial(collect_timeframe, adapter, repository, scope, thresholds, timeframe),
        )
        registered.append(name)

    _logger.info(
        "candle_collection_registered",
        payload={
            "jobs": registered,
            "universe": list(scope.universe),
            "gap_lookback_bars": scope.gap_lookback_bars,
            "all_jobs": job_ids(scheduler),
        },
    )
    return registered


def register_daily_report(
    scheduler: AsyncIOScheduler,
    factory: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
) -> None:
    """일간 이메일 리포트 잡 (T35) — 지정 시각(KST)에 지난 24시간을 보낸다.

    Args:
        scheduler: 엔진 스케줄러.
        factory: DB 세션 팩토리.
        settings: 앱 설정 — `report_hour_kst` · SMTP · 기본 수신자.

    Note:
        🔴 **시각은 KST 로 받고 스케줄은 tz 를 명시한다** (절대 규칙 #7). 하드코딩한
        UTC 시(hour) 로 바꿔 적으면 서머타임은 없어도 사람이 읽을 때 틀린다.

        ⚠️ 실패해도 매매를 막지 않는다 (§1.2.1) — 발송기가 거짓을 돌려주고 이력에
        남긴다. 다음 날 다시 시도한다. 설정이 없으면 잡을 **등록하지 않고** 로그로
        말한다 — 매일 실패 로그를 쌓는 것보다 낫다.
    """
    # 🔴 **이 잡은 더 이상 등록하지 않는다** (사용자 확정 2026-08-30).
    #
    #    여기서 보내던 리포트는 `send_report(...)` 를 `html` 없이 불렀고, 그래서
    #    **평문만** 나갔다 — 차트·주문표·계좌 요약이 빠진 옛 형식이다. 기능이 없어진
    #    것이 아니라 자동 경로에 배선이 안 돼 있었다.
    #
    # ⚠️ 엔진에서 고칠 수 없는 이유: 계좌 요약이 **살아 있는 러너**에 있고, 러너는
    #    API 프로세스의 태스크다. 엔진이 조립하면 그 칸이 빈칸이 되는데, 그것이 바로
    #    사용자가 요구한 "손익 등등" 의 절반이다.
    #
    # ⇒ `apps/api/report.daily_report_loop` 가 맡는다. 둘 다 돌면 매일 두 통이 가고
    #   한 통은 옛 형식이므로, 여기서는 **아무것도 안 한다.**
    del scheduler, factory, settings
    _logger.info(
        "daily_report_owned_by_api",
        payload={"note": "리포트는 API 가 보낸다 — 계좌 요약이 그쪽 러너에 있다"},
    )
