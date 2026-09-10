"""api 프로세스 진입점 (P0-9-1 · spec §2.1).

## 왜 api 와 engine 이 별도 프로세스인가

spec §2.1 의 요구는 한 문장이다 — **웹 재배포가 손절 감시를 죽이면 안 된다.** 한 프로세스에
FastAPI 와 스케줄러를 함께 두면 API 를 고치려고 재시작할 때마다 감시 루프가 끊긴다.
그 순간 손절가에 닿으면 아무도 대응하지 않는다.

**같은 이미지, 다른 커맨드**로 띄운다 (§2.1 동일 코드베이스) — 전략 코드가 갈라지지 않게.

## `/health` 가 감사 로그를 남기는 규칙

G0-6 은 "`/health` 호출이 `event_logs` 에 trace_id 와 함께 적재"를 요구한다. 그런데 컨테이너
healthcheck 가 10초마다 부르므로 **매 호출을 적재하면 하루 8천 행**이 쌓인다 — 감사 로그가
의미 없는 노이즈로 덮인다.

그래서 **부팅 후 첫 호출과 상태가 바뀔 때만** 적재한다. 상태 변화는 감사 로그가 실제로
답해야 하는 질문이고("언제부터 DB 가 죽었나"), 매 호출 기록은 그 답에 기여하지 않는다.
G0-6 검증은 기동 후 `/health` 를 한 번 부르면 그대로 충족된다.
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from updown.apps.api.admin import router as admin_router
from updown.apps.api.ai_analysis import router as ai_router
from updown.apps.api.ai_chat import router as ai_chat_router
from updown.apps.api.ai_report import router as ai_report_router
from updown.apps.api.analysis import router as analysis_router
from updown.apps.api.assistant import router as assistant_router
from updown.apps.api.auth import attach_accounts
from updown.apps.api.auth import guard as auth_guard
from updown.apps.api.auth import router as auth_router
from updown.apps.api.backtest import router as backtest_router
from updown.apps.api.evidence import router as evidence_router
from updown.apps.api.exchange import router as exchange_router
from updown.apps.api.fundamentals import attach_fundamentals
from updown.apps.api.fundamentals import router as fundamentals_router
from updown.apps.api.gates import router as gates_router
from updown.apps.api.health import (
    DependencyStatus,
    HealthReport,
    check_audit_log,
    check_database,
    check_redis,
)
from updown.apps.api.labels import router as labels_router
from updown.apps.api.live_stream import router as live_stream_router
from updown.apps.api.logs_admin import router as logs_admin_router
from updown.apps.api.macro import router as macro_router
from updown.apps.api.middleware import trace_id_middleware
from updown.apps.api.rebalancer import router as rebalancer_router
from updown.apps.api.report import router as report_router
from updown.apps.api.resources_admin import router as resources_admin_router
from updown.apps.api.walkforward import router as walkforward_router
from updown.common import paths as log_paths
from updown.common.config import Settings, load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.logging.audit import AuditLogger, LogHealthState
from updown.common.logging.event_sink import PostgresEventSink
from updown.common.logging.fallback_sink import FallbackSink
from updown.common.logging.policy import ActionRisk
from updown.common.logging.setup import configure_logging, get_logger
from updown.common.paths import describe as describe_logs

_logger = get_logger("apps.api.main")

HTTP_SERVICE_UNAVAILABLE = 503
"""readiness 실패 응답 코드."""


class ApiState:
    """앱 수명주기 동안 유지되는 자원 (P0-9-1).

    Note:
        `app.state` 에 흩어 두지 않고 한 객체로 묶는다 — 종료 시 무엇을 닫아야 하는지가
        한곳에 보이고, 테스트가 이 객체만 갈아끼우면 된다.
    """

    def __init__(self, settings: Settings) -> None:
        """자원을 만든다.

        Args:
            settings: 설정.
        """
        self.settings = settings
        self.engine: AsyncEngine = create_engine(settings.database_url)
        self.session_factory = create_session_factory(self.engine)
        self.redis: Redis = Redis.from_url(settings.redis_url)  # pyright: ignore[reportUnknownMemberType]
        self.log_health = LogHealthState()
        self.audit = AuditLogger(
            PostgresEventSink(self.session_factory),
            FallbackSink(),
            health=self.log_health,
        )
        #: 마지막으로 감사 로그에 남긴 종합 상태. None 이면 아직 한 번도 남기지 않았다.
        self.last_reported_status: DependencyStatus | None = None

    async def aclose(self) -> None:
        """자원을 닫는다."""
        await self.engine.dispose()
        await self.redis.aclose()

    async def collect_health(self) -> HealthReport:
        """의존성 상태를 모은다.

        Returns:
            종합 리포트.
        """
        return HealthReport(
            checks=(
                await check_database(self.session_factory),
                await check_redis(self.redis),
                check_audit_log(self.log_health),
            )
        )

    async def record_status_change(self, report: HealthReport) -> None:
        """상태가 바뀌었을 때만 감사 로그를 남긴다.

        Args:
            report: 방금 모은 리포트.

        Note:
            healthcheck 가 10초마다 부르므로 **매 호출 적재는 하루 8천 행**이다. 상태 변화만
            남기면 "언제부터 죽었나"라는 감사 로그가 답해야 할 질문에 그대로 답하면서 노이즈가
            없다. 부팅 후 첫 호출은 `last_reported_status is None` 이라 항상 남는다 (G0-6).

            분류는 `READ_ONLY` 다 — 상태 조회는 리스크를 늘리지 않으므로, 로그 적재가
            실패해도 보류할 행동 자체가 없다 (§1.2.1).
        """
        if report.status is self.last_reported_status:
            return
        previous = self.last_reported_status
        self.last_reported_status = report.status
        await self.audit.record(
            event_type="HEALTH_STATUS_CHANGED" if previous else "HEALTH_CHECKED",
            module="apps.api.health",
            risk=ActionRisk.READ_ONLY,
            payload={
                "status": report.status.value,
                "previous_status": previous.value if previous else None,
                "dependencies": report.to_json_dict()["dependencies"],
            },
        )


def create_app(state: ApiState | None = None) -> FastAPI:
    """FastAPI 앱을 만든다 (앱 팩토리).

    Args:
        state: 주입할 자원. None 이면 설정에서 만든다. **테스트가 여기로 대역을 넣는다.**

    Returns:
        구성된 앱.

    Note:
        팩토리로 두는 이유는 테스트다. 모듈 임포트 시점에 앱을 만들면 그 순간 DB·Redis
        연결이 생겨, 컨테이너 없이는 임포트조차 못 하게 된다.
    """
    resolved_state = state

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        """기동·종료 시 자원을 관리한다.

        Args:
            app: FastAPI 앱. `app.state.updown` 에 공유 상태를 건다.

        Yields:
            기동이 끝난 뒤 한 번 — 이 사이에 서버가 요청을 받는다. 빠져나오면 정리한다.
        """
        nonlocal resolved_state
        if resolved_state is None:
            settings = load_settings()
            # T211 — stderr(Docker) + `logs/app/api-YYYY-MM-DD.jsonl` 회전 파일. 파일 싱크가
            # 실패해도 기동은 계속된다 (규칙 #8-1 · setup.py 참조).
            configure_logging(settings.log_level, file_dir=log_paths.under("app"), proc="api")
            resolved_state = ApiState(settings)
        app.state.updown = resolved_state
        _logger.info(
            "api_started",
            payload={"app_env": resolved_state.settings.app_env.value},
        )
        # 🔴 **라이브를 다시 붙인다** (사용자 지적 2026-08-18: *"이제 그냥 내내 돌아야
        #    하는 거잖아"*). 러너는 이 프로세스의 태스크라 재시작마다 죽는다 —
        #    `make dev` 의 기동은 한 번뿐이라 `src/` 리로드 때마다 판이 사라졌다.
        #
        # ⛔ `AUTO_LIVE=0` 으로 끈다. 실패해도 기동을 막지 않는다.
        from updown.apps.api.walkforward import attach_store
        from updown.execution.stock_paper import DbStateStore, attach_state_store

        # 🔴 **판 저장소를 먼저 붙인다** (T16 ②). 없으면 라이브가 저장 없이 돌고,
        #    다음 리로드에 원장이 통째로 사라진 채 거래소 포지션만 남는다.
        attach_store(resolved_state.session_factory)
        # ⭐ 주식 페이퍼 계좌(T240) — 판 저장소와 같은 풀. 없으면 게이트가 주식 어댑터를 안 준다.
        attach_state_store(DbStateStore(resolved_state.session_factory))
        # ⭐ 재무 사실(T243) — 같은 풀. 설정은 EDGAR User-Agent 때문에 넘긴다.
        attach_fundamentals(resolved_state.session_factory, resolved_state.settings)
        # 🔴 계정 저장소 — 미들웨어가 **매 요청** 등급을 여기서 읽는다. 안 붙으면
        #    아무도 로그인할 수 없다 (조용히 통과시키지 않는다 · 규칙 #8).
        # ⭐ `ACCOUNTS_DATABASE_URL` 이 있으면 계정·문의·관리자 설정만 **그 DB** 에서 연다
        #    (사용자 2026-09-07 "실계좌와 데모가 유저 풀을 공유"). 데모 API 가 실계좌 DB 의
        #    계정 표를 함께 쓴다 — 원장은 그대로 자기 DB.
        accounts_engine: AsyncEngine | None = None
        shared_url = resolved_state.settings.accounts_database_url
        if shared_url:
            accounts_engine = create_engine(shared_url)
            attach_accounts(create_session_factory(accounts_engine))
            _logger.info(
                "accounts_store_shared",
                payload={"note": "계정 저장소를 ACCOUNTS_DATABASE_URL 에서 연다"},
            )
        else:
            attach_accounts(resolved_state.session_factory)

        # 🔴 **거래는 단일 인스턴스만** (리뷰 갭 1 · 2026-09-01). 거래 러너가 API
        #    프로세스에서 도는데 락은 엔진에만 있어, API 두 개가 뜨면(배포 겹침) 같은
        #    포지션에 이중 주문이 났다. 트레이더 락(엔진과 다른 키)으로 리더 하나만
        #    거래하게 한다 — 팔로워는 UI·조회만 제공하고 리더가 놓으면 승격한다.
        #    ⚠️ 조회·로그인은 **락과 무관**하게 계속 뜬다 — 락은 거래 시작만 게이트한다.
        from updown.apps.api.leader import TradingLeader

        _trade_tasks: list[asyncio.Task[None]] = []
        # 1 GB 호스트 — engine 잡(자원 비트)을 리더 api 안에서 돌린다 (`inproc_engine` 참조).
        #    플래그가 꺼져 있으면 None 이고 아무 일도 없다 (dev·paper).
        from updown.apps.api import inproc_engine

        _inproc = (
            inproc_engine.InprocEngine(getattr(resolved_state, "redis", None))
            if inproc_engine.enabled() and getattr(resolved_state, "redis", None) is not None
            else None
        )

        async def _start_trading() -> None:
            """리더가 될 때 — 라이브 복구 + 펀드 + 관리 루프들을 띄운다."""
            if _inproc is not None:
                _inproc.start()
            await _autostart_and_loops(_trade_tasks)

        async def _stop_trading() -> None:
            """팔로워로 내려갈 때 — 관리 루프들을 거둔다 (포지션은 브로커 손절이 지킨다)."""
            if _inproc is not None:
                _inproc.stop()
            for task in _trade_tasks:
                task.cancel()
            for task in _trade_tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            _trade_tasks.clear()

        # ⚠️ redis 가 없는 상태(테스트 대역)면 게이트를 건너뛴다 — 거래도 시작하지 않는다.
        #    락 없이 거래하면 이중매매 방지가 무의미하므로 **없으면 안 한다**(fail-safe).
        #    운영 `ApiState` 는 항상 redis 를 만든다.
        redis = getattr(resolved_state, "redis", None)
        leader = (
            TradingLeader(redis, start=_start_trading, stop=_stop_trading)
            if redis is not None
            else None
        )
        # 🔴 문(auth.guard)과 /health 가 리더 여부를 읽는다 (T212 블루그린). 팔로워는 거래
        #    POST 를 503 으로 거부해야 한다 — 겹침이 설계인 배포에서 팔로워가 자기(빈)
        #    세션으로 RUN 을 시작하면 그것이 곧 이중매매다.
        app.state.trading_leader = leader
        # ⛔ 실패해도 기동을 막지 않는다 — 조회 API 는 떠야 한다 (게이트 안에서 로그로 남긴다).
        if leader is not None:
            with contextlib.suppress(Exception):
                await leader.begin()
        try:
            yield
        finally:
            _logger.info("api_stopping", payload={})
            if leader is not None:
                with contextlib.suppress(Exception):
                    await leader.shutdown()
            attach_store(None)
            attach_state_store(None)
            attach_fundamentals(None)
            attach_accounts(None)
            if accounts_engine is not None:
                with contextlib.suppress(Exception):
                    await accounts_engine.dispose()
            await resolved_state.aclose()
        return

    async def _autostart_and_loops(tasks: list[asyncio.Task[None]]) -> None:
        """거래 리더의 실제 시작 — 옛 lifespan 본문 그대로, 태스크만 `tasks` 에 모은다."""
        from updown.apps.api.walkforward import autostart_live, watch_forever

        await autostart_live()
        # 🔴 **펀드를 되살린다** (T61 영속화). autostart 가 되살린 멤버 세션을 펀드로
        #    묶고 TWR 을 이어받는다 — 없으면 재시작마다 펀드가 증발한다. 실패해도 기동 안 막는다.
        from updown.apps.api.rebalancer import (
            fund_retry_loop,
            rebalance_loop,
            restore_funds,
        )

        with contextlib.suppress(Exception):
            await restore_funds()
        # 🔴 **못 붙인 펀드를 다시 붙인다** (2026-08-29 실측). 기동 순간 거래소가
        #    418(요율 제한)을 주자 BINANCE 펀드 복구가 통째로 실패했고, 경고 한 줄만
        #    남긴 채 그 판 6개가 **예산 없이** 계속 돌았다 — 원장이 저마다 계좌 전액을
        #    자기 것으로 여겨 감사가 wallet_drift 를 외쳤다.
        #
        #    일시적 실패를 영구 반쪽 상태로 만든 것이 결함이다. 붙을 때까지 다시 한다.
        tasks.append(asyncio.create_task(fund_retry_loop(), name="fund-retry"))
        # 🔴 **4h 자동 리밸런싱 루프** (T61). 예산만 갱신하는 저위험 틱 — 세션이 매매한다.
        tasks.append(asyncio.create_task(rebalance_loop(), name="fund-rebalancer"))
        # 🔴 **판이 도는지 밖에서 본다** (T20 ③). 러너 안의 자가 점검은 러너가 죽으면
        #    같이 죽는다 — 2026-08-19 에 죽은 두 판은 자기가 죽었다고 말할 수 없었다.
        tasks.append(asyncio.create_task(watch_forever(), name="run-watchdog"))
        # 🔴 **거래소 대조 루프** (2026-08-30 배포 준비). 원장과 거래소가 갈리면 경보를
        #    띄우고 **그 종목의 신규 진입을 보류한다** — 나가는 길은 안 막는다 (§1.2.1).
        #    기동 직후 한 번 즉시 돌린다: 재시작 중에 생긴 고아가 가장 위험하고,
        #    그것을 첫 주기(2분)까지 모른 채로 두면 그 사이에 새 진입이 나갈 수 있다.
        from updown.apps.api.walkforward import (
            load_reconcile,
            reconcile_loop,
            reconcile_once,
        )

        # ⭐ 누적 계수기를 먼저 되살린다 — 대조가 돌기 전에 있어야 두 번 안 센다.
        with contextlib.suppress(Exception):
            load_reconcile()
        with contextlib.suppress(Exception):
            await reconcile_once()
        tasks.append(asyncio.create_task(reconcile_loop(), name="exchange-reconcile"))
        # 🔴 **일간 리포트가 여기서 돈다** (사용자 확정 2026-08-30). 엔진 스케줄러가
        #    보내던 것은 `html` 을 안 넘겨 **평문만** 나갔다 — 차트·주문표·계좌 요약이
        #    빠진 옛 형식이었다.
        #
        #    계좌 요약은 **살아 있는 러너**에 있으므로 조립도 그 러너가 있는 프로세스에서
        #    해야 한다. 엔진은 다른 프로세스라 그 값을 못 본다.
        from updown.apps.api.report import daily_report_loop, equity_snapshot_loop

        tasks.append(asyncio.create_task(daily_report_loop(), name="daily-report"))
        # ⭐ 자산 시계열 — 매일 한 점 (사용자 2026-09-07: 월별 자산 꺾은선). 리포트와 같은 자리다.
        tasks.append(asyncio.create_task(equity_snapshot_loop(), name="equity-snapshot"))

    app = FastAPI(title="업 앤 다운 API", lifespan=lifespan)
    # 🔴 **인증 문이 가장 바깥이다** (2026-08-30 배포 준비). 미들웨어는 나중에 등록한
    #    것이 바깥을 감싸므로, `trace_id` 보다 **뒤에** 붙여야 인증이 먼저 돈다.
    #    URL 을 아는 사람이 API 를 직접 부르는 것을 막는 유일한 지점이다 —
    #    화면에 로그인을 붙이는 것만으로는 아무 소용이 없다.
    app.middleware("http")(trace_id_middleware)
    app.middleware("http")(auth_guard)
    app.include_router(auth_router)
    # 백테스트 **조회** 라우트 (Phase 5 A2). 실행 엔드포인트는 두지 않는다 —
    # 매트릭스는 수 시간을 쓰므로 HTTP 요청 하나로 시작될 수 있으면 안 된다.
    app.include_router(backtest_router)
    # AI 차트 분석 (Phase 5). 🔴 여기서 나오는 plan 은 LlmProposal 이며 **주문이 아니다**
    # (스펙 §5.3.1) — import-linter 계약 4 가 llm/ 의 decision·execution 접근을 막는다.
    # 🔴 산출물을 어디서 읽는지 기동 때 남긴다. 컨테이너와 호스트가 서로 다른
    #    `logs/` 를 보던 사고를 로그만 보고도 잡을 수 있어야 한다.
    get_logger(__name__).info("logs.source", **describe_logs())
    app.include_router(ai_router)
    # 이메일 성과 리포트 (T35) — 미리보기·수동 발송
    app.include_router(report_router)
    # 플래그 점검기 — 조회·계산만 한다. 값을 바꾸는 경로가 없다.
    app.include_router(admin_router)
    # 로그 내려받기 (T211) — `/admin/logs` 는 `roles.ADMIN_PREFIXES` 로 관리자만 통과한다.
    app.include_router(logs_admin_router)
    # 자원 창 (T215) — `/admin/resources` 도 관리자 접두어.
    app.include_router(resources_admin_router)
    # 실거래 관문 (2026-09-04) — 읽기 권한. 돈 데이터 없음 · 문을 여는 경로 없음.
    app.include_router(gates_router)
    # 근거 (T222) — 라이브 경로 vs 45미래. 시장 가격·연구 결과만 · 읽기 권한 · 게스트도 본다.
    app.include_router(evidence_router)
    # 🔴 **차트 주문 탭의 분석 입구** (2026-08-30). `/admin/inspect` 는 연구용이라
    #    2026-01-01 이후가 봉인돼 있다 — 그 봉인은 옳고 건드리지 않는다. 매매 화면은
    #    지금 시세를 보므로 목적이 다르고, 그래서 입구를 나눴다.
    app.include_router(analysis_router)
    # ⭐ 매매 라벨 (2026-09-03) — 사람이 차트에 표시한 판단을 담는다. 표시는 **가설**
    #    이고 정답지가 아니다 (규칙 #11 예외 조건: 규칙으로 환원되어 코드에 남는가).
    #
    # 🔴 **배포에서는 뺀다** (사용자 2026-09-04: *"라벨(임시) 기능도 배포에서는 빠지게"*).
    #    `UPDOWN_LABELS=1` 일 때만 등록 — `compose.dev.yml` 만 켠다. 라이브 env 파일엔 두지
    #    않는다. 화면 쪽은 `VITE_LABELS` 빌드 인자가 같은 문이다 (`web/src/App.tsx`).
    #    코드는 지우지 않는다 — 연구 도구로 남긴다.
    if os.environ.get("UPDOWN_LABELS") == "1":
        app.include_router(labels_router)
    # ⭐ 실시간 봉(SSE) — 같은 `/analysis` 접두사지만 파일을 나눈다. 스트림은
    #    수명·구독 관리가 있어 요청-응답 코드와 섞으면 둘 다 읽기 어려워진다.
    app.include_router(live_stream_router)
    app.include_router(walkforward_router)
    app.include_router(rebalancer_router)
    # 🔴 거래소 콘솔 — **원장이 아니라 거래소가 말하는 것**을 보여 주고 앱에서 정리한다.
    #    RUN 이 목록에서 사라진 뒤 포지션이 남으면 손댈 방법이 없었다 (2026-08-18).
    app.include_router(exchange_router)
    # ⭐ 재무 표(T243) — 읽기는 열람자도(`need_for` READ), 새로고침(POST)은 거래자부터.
    app.include_router(fundamentals_router)
    # ⭐ 거시 지표(T262) — VIX·선물·환율·금리·물가. 시장 공개 값이라 읽기 권한.
    app.include_router(macro_router)
    # ⭐ 온보딩 위저드(T247) — 초안은 계정 저장소, 생성은 펀드 API 를 그대로 부른다.
    app.include_router(assistant_router)
    # ⭐ AI 채팅(T248) — 작업 레지스트리 + `/ai/jobs/{id}/events` SSE 를 그대로 쓴다.
    app.include_router(ai_chat_router)
    # ⭐ AI 퍼포먼스 리포트(T249) — 참가자 성적표 · 시작 스위치.
    app.include_router(ai_report_router)

    @app.get("/health")
    async def health() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """Liveness — 프로세스가 살아 있는가.

        Returns:
            **항상 200.** 본문의 `status` 로 의존성 상태를 알린다.

        Note:
            DB 장애에 503 을 주면 컨테이너가 API 를 계속 재시작한다. 재시작이 DB 를 살리지
            못하므로 플래핑만 늘고, 로그가 재시작으로 덮여 원인 파악이 더 어려워진다.
            "트래픽을 받아도 되는가"는 `/health/ready` 가 답한다.
        """
        current: ApiState = app.state.updown
        report = await current.collect_health()
        await current.record_status_change(report)
        body: dict[str, Any] = report.to_json_dict()
        # T212 — 배포 스크립트가 "이 슬롯이 거래 리더인가" 를 여기서 읽는다.
        gate = getattr(app.state, "trading_leader", None)
        body["trading_leader"] = None if gate is None else bool(gate.is_leader)
        return JSONResponse(content=body)

    @app.get("/health/ready")
    async def ready() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """Readiness — 지금 트래픽을 받아도 되는가.

        Returns:
            정상이면 200, 의존성이 하나라도 정상이 아니면 **503**.
        """
        current: ApiState = app.state.updown
        report = await current.collect_health()
        body: dict[str, Any] = report.to_json_dict()
        code = 200 if report.is_ready else HTTP_SERVICE_UNAVAILABLE
        return JSONResponse(content=body, status_code=code)

    return app


app = create_app()
"""uvicorn 이 임포트하는 앱 (`uvicorn updown.apps.api.main:app`).

자원 생성은 `lifespan` 안에서 일어나므로, 임포트만으로 DB·Redis 에 붙지 않는다.
"""
