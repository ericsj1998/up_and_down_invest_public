"""과거 캔들 백필 (P0-8-2·3 · spec §4.2 "캔들 적재 = 백테스트 원천").

## 왜 창(window)으로 쪼개는가

`UpbitAdapter.get_candles` 는 내부에서 커서를 돌려 구간을 채우지만 **페이지 상한이
있다**(`MAX_CURSOR_PAGES = 200`). 5m 1년치는 105,120봉 = 526페이지라 한 번에 요청하면
그 상한에 걸린다. 상한을 올리는 방법도 있지만, 창으로 쪼개면 세 가지가 함께 해결된다:

1. **재개 가능**: 창 단위로 즉시 적재하므로 중간에 죽어도 그때까지가 남는다 (P0-8-3)
2. **메모리 한정**: 10만 봉을 한꺼번에 들고 있지 않는다
3. **진행 관측**: 창마다 로그가 남아 1년치 백필이 어디까지 갔는지 보인다

## 재개는 "이미 채워진 창을 건너뛰기"다

마지막 성공 커서를 파일에 적어 두는 방식은 파일과 DB 가 어긋날 수 있다. 대신 **DB 자체를
진행 상태로 쓴다** — 창의 기대 봉 수와 실제 적재 수가 같으면 건너뛴다. 코인은 24시간 장이라
기대 봉 수가 정확히 계산되므로(§7) 이 판정이 성립한다.

## 미완성 봉을 저장하지 않는다

브로커는 **지금 만들어지고 있는 봉도** 응답에 넣어 준다 (업비트 실측). 그 봉은 종가가
확정되지 않았는데 완성된 봉과 형태가 같아서, 저장하면 지표·구조물이 미완성 봉을 진짜 봉으로
읽는다. spec §4.2 의 "탐지는 봉마감 기준" 전제를 데이터 계층에서 지킨다.

## 시작일은 고정, 종료일만 전진한다 (D-15)

"오늘 기준 뒤로 1년"은 실행할 때마다 커버리지 하한이 움직인다. 그러면 같은 백테스트가
다른 데이터 시작점을 보게 되어 결과가 바뀌고, **원칙 P1 이 데이터 쪽에서 깨진다.**
`config/backfill.yml` 의 `history_anchor` 가 그 하한을 고정한다 — 커버리지는 늘어날 뿐
줄지 않는다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import BrokerAdapter
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.timeframes import expected_bar_count, interval, last_closed_ts

_logger = get_logger("marketdata.ingest.backfill")

DEFAULT_SCOPE_PATH = Path("config/backfill.yml")
"""기본 수집 범위 설정 파일 (D-15 — 코드에 박지 않는다)."""

#: 한 창에 담을 봉 수.
#:
#: 어댑터의 1회 응답 상한이 200봉이므로 이 값은 "어댑터 내부 페이지 50장"에 해당한다
#: (상한 200장의 1/4). 창을 더 키우면 요청 수가 줄지만 재개 단위가 거칠어진다.
DEFAULT_WINDOW_BARS = 10_000


class BackfillScopeError(ValueError):
    """수집 범위 설정을 읽을 수 없다.

    Note:
        기본값으로 조용히 넘어가지 않는다 (spec §7). 앵커를 잘못 읽고 도는 것은
        "커버리지 하한이 조용히 바뀐" 상태이며, 그것이 D-15 가 막으려는 바로 그 상황이다.
    """


@dataclass(frozen=True, slots=True)
class BackfillScope:
    """수집 범위 (D-15).

    Attributes:
        history_anchor: **고정** 수집 시작 시각 (UTC). 실행 시점과 무관하다.
        universe: 대상 마켓 코드.
        timeframes: 대상 시간축.
        gap_lookback_bars: 주기 수집이 매번 되돌아가 다시 받는 봉 수 (P0-8-6).
        max_catchup_bars: 주기 수집 1회가 따라잡을 최대 봉 수 (P0-8-6).

    Note:
        `history_anchor` 가 고정인 것이 이 클래스의 존재 이유다. 종료일은 항상 "지금"이라
        설정에 두지 않는다 — 커버리지 **상한만 전진**하고 하한은 그대로다.

        앵커를 **더 과거로 내리는 것은 안전**하다 (옛 봉이 추가될 뿐 기존 봉은 불변).
        **앞으로 올리는 것은 금지**다 — 적재된 구간을 커버리지에서 잘라내면 그 구간을 쓰던
        백테스트가 재현되지 않는다.
    """

    history_anchor: datetime
    universe: tuple[str, ...]
    timeframes: tuple[Timeframe, ...]
    gap_lookback_bars: int = 60
    max_catchup_bars: int = 400

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "BackfillScope":
        """Dict 에서 만든다.

        Args:
            raw: YAML 파싱 결과.

        Returns:
            수집 범위.

        Raises:
            BackfillScopeError: 필드 부재·형식 오류·타임존 누락.
        """
        anchor_raw = raw.get("history_anchor")
        if not isinstance(anchor_raw, str):
            raise BackfillScopeError(
                f"history_anchor 가 없거나 문자열이 아니다: {anchor_raw!r}. "
                "'2025-08-01T00:00:00Z' 형식으로 적는다"
            )
        try:
            anchor = datetime.fromisoformat(anchor_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BackfillScopeError(f"history_anchor 를 해석할 수 없다: {anchor_raw!r}") from exc
        if anchor.tzinfo is None:
            # UTC 로 가정하면 KST 로 적은 값이 9시간 어긋난 채 하한이 된다 (§12.3, 규칙 #7).
            raise BackfillScopeError(
                f"history_anchor 에 타임존을 명시하라: {anchor_raw!r} → 끝에 'Z' 를 붙인다"
            )

        universe = raw.get("universe")
        if not isinstance(universe, list) or not universe:
            raise BackfillScopeError(f"universe 가 비었거나 목록이 아니다: {universe!r}")

        timeframes_raw = raw.get("timeframes")
        if not isinstance(timeframes_raw, list) or not timeframes_raw:
            raise BackfillScopeError(f"timeframes 가 비었거나 목록이 아니다: {timeframes_raw!r}")
        try:
            timeframes = tuple(Timeframe(str(value)) for value in timeframes_raw)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        except ValueError as exc:
            raise BackfillScopeError(
                f"지원하지 않는 timeframe 이 있다: {timeframes_raw!r} — "
                f"가능한 값: {[tf.value for tf in Timeframe]}"
            ) from exc

        try:
            lookback = int(raw.get("gap_lookback_bars", 60))
            catchup = int(raw.get("max_catchup_bars", 400))
        except (TypeError, ValueError) as exc:
            raise BackfillScopeError(f"주기 수집 봉 수를 해석할 수 없다: {exc}") from exc
        if lookback < 1:
            raise BackfillScopeError(
                f"gap_lookback_bars 는 1 이상이어야 한다: {lookback}. "
                "0 이면 최신 봉만 받아 일시 장애 구멍이 영구히 남는다"
            )
        if catchup < lookback:
            raise BackfillScopeError(
                f"max_catchup_bars({catchup}) 가 gap_lookback_bars({lookback}) 보다 작다 — "
                "되돌아볼 구간이 상한에 먼저 잘려 갭 메움이 동작하지 않는다"
            )

        return cls(
            history_anchor=anchor.astimezone(UTC),
            universe=tuple(str(symbol) for symbol in universe),  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            timeframes=timeframes,
            gap_lookback_bars=lookback,
            max_catchup_bars=catchup,
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_SCOPE_PATH) -> "BackfillScope":
        """YAML 파일에서 읽는다.

        Args:
            path: 설정 파일 경로.

        Returns:
            수집 범위.

        Raises:
            BackfillScopeError: 파일 부재·파싱 실패·형식 오류.
        """
        try:
            raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BackfillScopeError(f"수집 범위 파일을 읽을 수 없다({path}): {exc}") from exc
        except yaml.YAMLError as exc:
            raise BackfillScopeError(f"수집 범위 YAML 파싱 실패({path}): {exc}") from exc
        if not isinstance(raw, dict):
            raise BackfillScopeError(
                f"수집 범위 파일이 매핑이 아니다({path}): {type(raw).__name__}"
            )
        return cls.from_mapping(raw)  # pyright: ignore[reportUnknownArgumentType]


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """백필 결과.

    Attributes:
        instrument: 대상 종목.
        timeframe: 시간축.
        windows: 처리한 창 수.
        skipped_windows: 이미 채워져 건너뛴 창 수. 재개 시 여기가 커진다.
        fetched: 브로커에서 받은 봉 수.
        stored: 적재를 시도한 봉 수.
        dropped_unclosed: 미완성 봉이라 버린 수.
        expected: 구간의 기대 봉 수 (24시간 장 기준).

    Note:
        `stored` 는 신규/갱신을 구분하지 않는다 — upsert 라 구분이 불가능하고, 중복 없음은
        PK 가 보장한다. 실제 적재량은 커버리지 쿼리로 확인한다 (DoD 1·3).
    """

    instrument: Instrument
    timeframe: Timeframe
    windows: int
    skipped_windows: int
    fetched: int
    stored: int
    dropped_unclosed: int
    expected: int


def _windows(
    start: datetime, end: datetime, timeframe: Timeframe, window_bars: int
) -> list[tuple[datetime, datetime]]:
    """구간을 창으로 쪼갠다 (과거 → 미래 순서).

    Args:
        start: 시작 (포함).
        end: 끝 (포함).
        timeframe: 시간축.
        window_bars: 창당 봉 수.

    Returns:
        `(창 시작, 창 끝)` 목록. 창끼리 겹치지 않는다.

    Note:
        과거→미래 순서로 돌린다. 실패해도 **오래된 쪽이 먼저 완성**되므로, 부분 백필
        상태에서도 "어디까지는 신뢰할 수 있다"가 연속 구간으로 남는다. 미래→과거로 돌면
        구멍이 앞뒤로 흩어져 그 판단이 어려워진다.
    """
    step = interval(timeframe)
    span = step * window_bars
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + span - step)
        windows.append((cursor, window_end))
        cursor = window_end + step
    return windows


def drop_unclosed(candles: list[Candle], now: datetime, timeframe: Timeframe) -> list[Candle]:
    """아직 마감되지 않은 봉을 걸러낸다.

    Args:
        candles: 브로커에서 받은 봉.
        now: 현재 시각 (UTC aware). **인자로 받는다** (원칙 P1).
        timeframe: 시간축.

    Returns:
        마감된 봉만.

    Note:
        미완성 봉은 종가가 확정되지 않았는데 완성된 봉과 형태가 같다. 저장하면
        지표·구조물이 그것을 진짜 봉으로 읽고, 다음 수집에서 값이 바뀐다 —
        "동일 입력 → 동일 출력"(P1)이 데이터 쪽에서 깨지는 지점이다.
    """
    boundary = last_closed_ts(now, timeframe)
    return [candle for candle in candles if candle.ts <= boundary]


async def run_backfill(
    adapter: BrokerAdapter,
    repository: CandleRepository,
    instrument: Instrument,
    instrument_id: int,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    window_bars: int = DEFAULT_WINDOW_BARS,
    resume: bool = True,
    on_window: Callable[[int, int], None] | None = None,
) -> BackfillResult:
    """구간의 캔들을 받아 적재한다.

    Args:
        adapter: 시세 어댑터. **`OrderGateway` 와 무관하다** — 조회 전용 경로다.
        repository: 캔들 저장소.
        instrument: 대상 종목.
        instrument_id: `instruments.id`.
        timeframe: 시간축.
        start: 시작 (UTC aware, 포함).
        end: 끝 (UTC aware, 포함).
        now: 마감 판정 기준 시각. None 이면 `datetime.now(UTC)`.
        window_bars: 창당 봉 수.
        resume: True 면 이미 채워진 창을 건너뛴다 (P0-8-3 재개).
        on_window: 창 시작마다 `(현재, 전체)` 로 불린다. 진행률 표시용이며 없으면 무시된다 —
            창 하나가 수 분 걸려(주식 분봉은 특히) 조합 단위 표시만으로는 멈춤과 구분되지
            않기 때문이다.

    Returns:
        백필 결과.

    Raises:
        ValueError: naive datetime, `start > end`, 또는 `window_bars < 1`.

    Note:
        `end` 가 미래여도 된다 — 마감 경계로 잘리므로 "지금까지 전부"를 `end=now` 로
        표현할 수 있다.

        **부분 실패 시 예외를 그대로 올린다.** 조용히 넘기고 다음 창으로 가면 구멍이
        생긴 채 "성공"으로 보고된다 (spec §7). 재실행하면 채워진 창을 건너뛰고 이어간다.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError(f"start/end 는 timezone-aware 여야 한다: {start!r}, {end!r}")
    if start > end:
        raise ValueError(f"start 가 end 보다 늦다: {start.isoformat()} > {end.isoformat()}")
    if window_bars < 1:
        raise ValueError(f"window_bars 는 1 이상이어야 한다: {window_bars}")

    moment = now or datetime.now(UTC)
    boundary = last_closed_ts(moment, timeframe)
    effective_end = min(end, boundary)
    if effective_end < start:
        _logger.info(
            "backfill_nothing_closed_yet",
            payload={
                "symbol": instrument.symbol,
                "timeframe": timeframe.value,
                "start": start.isoformat(),
                "requested_end": end.isoformat(),
                "last_closed": boundary.isoformat(),
            },
        )
        return BackfillResult(
            instrument=instrument,
            timeframe=timeframe,
            windows=0,
            skipped_windows=0,
            fetched=0,
            stored=0,
            dropped_unclosed=0,
            expected=0,
        )

    windows = _windows(start, effective_end, timeframe, window_bars)
    totals = {"fetched": 0, "stored": 0, "dropped": 0, "skipped": 0}

    for index, (window_start, window_end) in enumerate(windows, start=1):
        # 창 하나가 수 분 걸린다 (주식 분봉은 거래일마다 페이지가 끊겨 특히 느리다).
        # 조합 단위로만 진행률을 찍으면 그 사이가 통째로 무신호라 **멈춤과 구분되지
        # 않는다** — 실제로 프론트가 매번 '멈춤 의심'을 띄웠다.
        if on_window is not None:
            on_window(index, len(windows))

        expected = expected_bar_count(window_start, window_end, timeframe)

        if resume and await _window_is_complete(
            repository, instrument, instrument_id, timeframe, window_start, window_end, expected
        ):
            totals["skipped"] += 1
            continue

        received = await adapter.get_candles(instrument, timeframe, window_start, window_end)
        closed = drop_unclosed(received, moment, timeframe)
        totals["fetched"] += len(received)
        totals["dropped"] += len(received) - len(closed)
        totals["stored"] += await repository.upsert_candles(instrument_id, closed)

        _logger.info(
            "backfill_window_done",
            payload={
                "symbol": instrument.symbol,
                "timeframe": timeframe.value,
                "window": f"{index}/{len(windows)}",
                "range": f"{window_start.isoformat()}~{window_end.isoformat()}",
                "expected": expected,
                "received": len(received),
                "stored": len(closed),
            },
        )

    result = BackfillResult(
        instrument=instrument,
        timeframe=timeframe,
        windows=len(windows),
        skipped_windows=totals["skipped"],
        fetched=totals["fetched"],
        stored=totals["stored"],
        dropped_unclosed=totals["dropped"],
        expected=expected_bar_count(start, effective_end, timeframe),
    )
    _logger.info(
        "backfill_done",
        payload={
            "symbol": instrument.symbol,
            "timeframe": timeframe.value,
            "windows": result.windows,
            "skipped_windows": result.skipped_windows,
            "stored": result.stored,
            "expected": result.expected,
            "dropped_unclosed": result.dropped_unclosed,
        },
    )
    return result


async def _window_is_complete(
    repository: CandleRepository,
    instrument: Instrument,
    instrument_id: int,
    timeframe: Timeframe,
    window_start: datetime,
    window_end: datetime,
    expected: int,
) -> bool:
    """창이 이미 완전히 채워져 있는가.

    Args:
        repository: 저장소.
        instrument: 종목.
        instrument_id: 종목 id.
        timeframe: 시간축.
        window_start: 창 시작.
        window_end: 창 끝.
        expected: 기대 봉 수.

    Returns:
        기대 봉 수만큼 이미 있으면 True.

    Note:
        `==` 이 아니라 **`>=`** 로 본다. PK 가 중복을 막으므로 기대보다 많아질 수는 없지만,
        경계 계산이 1봉 어긋났을 때 `==` 이면 그 창을 **영원히 다시 받는다**. 느슨한
        부등호가 그 무한 재수집을 막는다.

        DB 를 진행 상태로 쓰는 이유는 파일 커서와 DB 가 어긋나는 경우를 없애기 위해서다.
    """
    stored = await repository.fetch_candles(
        instrument, instrument_id, timeframe, window_start, window_end
    )
    return len(stored) >= expected


def days_ago(now: datetime, *, days: int) -> datetime:
    """N일 전 시각 — **임시 조회용**이다.

    Args:
        now: 기준 시각.
        days: 며칠 전.

    Returns:
        `now - days`.

    Note:
        ⚠️ **이 값을 정규 수집의 시작점으로 쓰지 않는다.** 실행할 때마다 하한이 밀려
        커버리지가 재현되지 않는다 (D-15). 정규 수집은 `BackfillScope.history_anchor` 를
        쓴다 — CLI 의 `--anchor` / `--full` 경로가 그것이다.

        `--days` 는 "최근 며칠 훑어보기" 같은 임시 확인에만 쓴다.
    """
    return now - timedelta(days=days)
