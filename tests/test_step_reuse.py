"""걸음 안에서 같은 계산을 두 번 하지 않는 통로 — **넘겨 쓴 것 == 다시 계산한 것** 을 잠근다 (T833).

걸음(판정)은 이벤트 루프 위에서 돈다. 그 시간만큼 모든 판의 감시가 서므로, 같은 봉으로 같은
값을 두 번 만드는 자리를 없앴다:

| 통로 | 없앤 중복 |
|---|---|
| `snapshot.compute(..., pivots=)` | 지표 계산 안의 피벗 탐색 |
| `trend.evaluate(..., series=, pivots=)` | 추세 판정 안의 지표 계산 · 피벗 탐색 |
| 볼린저에 꼬리만 넣기 | 마지막 값 하나를 위해 전 구간 끝마다 편차를 재던 것 |
| `IndicatorTable` | 아무 계산의 재료도 아닌 걸음 축 지표를 걸음마다 미리 재던 것 |
| 추세 걸음의 스윙 · 구조 이어 쓰기 | 새 피벗이 없는 봉에서 같은 스윙 열을 다시 접고 다시 읽던 것 |

⚠️ 이 파일이 깨지면 판정이 **조용히** 달라진다 (절대 규칙 #5·#8). 그때 할 일은 시험을 고치는
것이 아니라 통로를 되돌리는 것이다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.indicators import snapshot
from updown.analysis.indicators.bands import bollinger
from updown.analysis.playbook.types import Family, Playbook
from updown.analysis.structures.params import SwingParams
from updown.analysis.structures.swing import find_pivots, prior_swings
from updown.analysis.trend.market_structure import read_structure
from updown.analysis.trend.service import MIN_BARS_FOR_TREND, TrendError, evaluate
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.session import FrameState, IndicatorTable

FRAME = Timeframe.H1
BASE = datetime(2026, 1, 1, tzinfo=UTC)
COIN = Instrument(
    symbol="BTC_USDT",
    name="비트코인 무기한",
    market=Market.GATE,
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
BARS = 460


def _path(index: int) -> int:
    """오르다 · 내리다 · 다시 오르는 뼈대 — 추세 상태가 한 번은 바뀌어야 비교가 뜻이 있다."""
    if index < 170:
        return 2 * index
    if index < 320:
        return 340 - 3 * (index - 170)
    return -110 + 4 * (index - 320)


def _bars(
    count: int = BARS,
    *,
    frame: Timeframe = FRAME,
    start: datetime = BASE,
    span: timedelta = timedelta(hours=1),
) -> list[Candle]:
    """결정론 봉 — 난수를 쓰면 실패를 재현할 수 없다 (절대 규칙 #5).

    종가 · 거래량도 봉마다 달라야 한다: 지표(이평 · RSI · 거래량 배수)와 다이버전스가 실제로
    값을 내야 "같다" 는 비교가 빈 것끼리의 비교가 되지 않는다.
    """
    rows: list[Candle] = []
    for index in range(count):
        mid = 1000 + _path(index) + 2 * ((index * 7) % 11)
        rows.append(
            Candle(
                instrument=COIN,
                timeframe=frame,
                ts=start + index * span,
                open=Decimal(mid),
                high=Decimal(mid + 2 + (index * 3) % 5),
                low=Decimal(mid - 2 - (index * 5) % 7),
                close=Decimal(mid + (index * 13) % 5 - 2),
                volume=Decimal(100 + (index * 17) % 53),
            )
        )
    return rows


@pytest.mark.parametrize("params", [None, SwingParams(left_bars=3, right_bars=3)])
def test_snapshot_with_given_pivots_equals_plain(params: SwingParams | None) -> None:
    """피벗을 넘겨받은 지표 계산 == 안에서 피벗을 찾은 지표 계산."""
    rows = _bars()
    pivots = find_pivots(rows, FRAME, params)

    plain = snapshot.compute(rows, params)
    given = snapshot.compute(rows, params, pivots=pivots)

    assert given == plain
    assert plain.divergences or plain.crosses, "비교할 것이 없는 봉이다 — 시험 봉을 고친다"


@pytest.mark.parametrize("params", [None, SwingParams(left_bars=3, right_bars=3)])
def test_trend_with_given_series_and_pivots_equals_plain(params: SwingParams | None) -> None:
    """지표 · 피벗을 넘겨받은 추세 판정 == 안에서 둘 다 만든 추세 판정."""
    rows = _bars()
    pivots = find_pivots(rows, FRAME, params)
    series = snapshot.compute(rows, params, pivots=pivots)

    plain = evaluate(COIN, FRAME, rows, params)

    assert evaluate(COIN, FRAME, rows, params, series=series, pivots=pivots) == plain
    assert evaluate(COIN, FRAME, rows, params, series=series) == plain
    assert evaluate(COIN, FRAME, rows, params, pivots=pivots) == plain
    assert len({state.state for state in plain.states if state is not None}) > 1, (
        "추세가 한 번도 안 바뀌는 봉이다 — 시험 봉을 고친다"
    )


def test_trend_reuse_holds_on_every_window_length() -> None:
    """창이 한 봉씩 자라도 같다 — 걸음마다 마지막 상태만 읽는 쓰임을 그대로 흉내 낸다."""
    rows = _bars(MIN_BARS_FOR_TREND + 60)
    for length in range(MIN_BARS_FOR_TREND - 3, len(rows) + 1, 7):
        window = rows[:length]
        pivots = find_pivots(window, FRAME)
        series = snapshot.compute(window, pivots=pivots)
        assert evaluate(COIN, FRAME, window, series=series, pivots=pivots) == evaluate(
            COIN, FRAME, window
        ), f"길이 {length} 에서 갈렸다"


def test_trend_structure_at_each_bar_equals_fresh_read_of_prefix() -> None:
    """봉마다 적힌 구조 == 그 봉까지만 잘라 처음부터 읽은 구조.

    추세 걸음은 새 피벗이 확인된 봉에서만 스윙을 다시 접고 구조를 다시 읽는다. 그 사이 봉에서
    직전 값을 이어 쓰는 것이 맞는지를, 이어 쓰지 않는 길(앞토막을 통째로 다시 읽기)과 맞댄다.
    """
    rows = _bars()
    history = evaluate(COIN, FRAME, rows)

    seen: set[object] = set()
    for index in range(MIN_BARS_FOR_TREND - 1, len(rows)):
        state = history.states[index]
        assert state is not None
        fresh = read_structure(prior_swings(rows[: index + 1], FRAME))
        assert state.evidence.structure == fresh.pattern, f"봉 {index} 에서 갈렸다"
        seen.add(fresh.pattern)
    assert len(seen) > 1, "구조가 한 번도 안 바뀌는 봉이다 — 시험 봉을 고친다"


def test_trend_rejects_series_of_other_candles() -> None:
    """남의 봉으로 만든 지표를 받으면 멈춘다 — 조용히 틀린 추세를 내지 않는다 (규칙 #8)."""
    rows = _bars()
    shorter = snapshot.compute(rows[:-1])
    with pytest.raises(TrendError):
        evaluate(COIN, FRAME, rows, series=shorter)


def test_trend_rejects_series_of_other_swing_params() -> None:
    """확인 지연이 다른 지표를 받으면 멈춘다 — 다이버전스를 아는 시점이 달라지기 때문이다."""
    rows = _bars()
    other = snapshot.compute(rows, SwingParams(left_bars=3, right_bars=3))
    with pytest.raises(TrendError):
        evaluate(COIN, FRAME, rows, series=other)


def test_indicator_table_measures_late_but_same() -> None:
    """안 잰 축은 읽힐 때 잰다 — 값은 미리 잰 것과 같고, 한 번만 잰다."""
    rows = _bars()
    eager = snapshot.compute(rows).at(len(rows) - 1)
    ready = FrameState(bars=len(rows), rows=rows, trend=None, indicators=eager, has_box=False)
    late = FrameState(bars=len(rows), rows=rows, trend=None, indicators=None, has_box=False)

    table = IndicatorTable({Timeframe.M5: late, Timeframe.H1: ready})

    assert list(table) == [Timeframe.M5, Timeframe.H1]
    assert len(table) == 2
    assert table[Timeframe.H1] is eager
    assert table[Timeframe.M5] == eager
    assert table[Timeframe.M5] is table[Timeframe.M5]
    assert table.get(Timeframe.D1) is None
    assert dict(table) == {Timeframe.M5: eager, Timeframe.H1: eager}


def test_session_context_indicators_equal_eager_on_every_frame() -> None:
    """세션이 내놓는 지표 표 == 축마다 그 봉으로 바로 잰 지표 (걸음 축은 미뤄 잰다)."""
    hours = _bars(300)
    minutes = _bars(
        600, frame=Timeframe.M5, start=BASE + timedelta(hours=250), span=timedelta(minutes=5)
    )
    inert = Playbook(
        playbook_id="잠금용",
        version="0",
        market_groups=(),
        timeframe=Timeframe.H1,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
    )
    session = Session(
        instrument=COIN,
        playbooks=(inert,),
        feed=SealedFeed(
            {Timeframe.M5: minutes, Timeframe.H1: hours},
            Seal(start=hours[290].ts, end=hours[-1].ts),
        ),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )

    context = session.context()

    step_axis = session._frame(Timeframe.M5, None)  # pyright: ignore[reportPrivateUsage]
    entry_axis = session._frame(Timeframe.H1, None)  # pyright: ignore[reportPrivateUsage]
    assert step_axis is not None and step_axis.indicators is None, "걸음 축을 미리 쟀다"
    assert entry_axis is not None and entry_axis.indicators is not None
    assert set(context.indicators) == {Timeframe.M5, Timeframe.H1}
    for frame, state in ((Timeframe.M5, step_axis), (Timeframe.H1, entry_axis)):
        eager = snapshot.compute(state.rows).at(len(state.rows) - 1)
        assert context.indicators[frame] == eager, frame


@pytest.mark.parametrize("period", [5, 20, 50])
@pytest.mark.parametrize("drop_last", [False, True])
def test_bollinger_tail_gives_the_same_last_band(period: int, drop_last: bool) -> None:
    """꼬리 `period` 개만 넣은 볼린저의 마지막 값 == 전 구간을 넣은 볼린저의 마지막 값.

    `drop_last` 는 닫히지 않은 마지막 봉을 빼고 재는 쓰임이다. 값만이 아니라 **자릿수 표현까지**
    같아야 한다 — 뒤에서 이 값으로 나누고 반올림하므로 표현이 다르면 결과가 갈릴 수 있다.
    """
    closes = [candle.close for candle in _bars(240)]
    if drop_last:
        closes = closes[:-1]

    whole = bollinger(closes, period=period, multiple=Decimal("2.5"))
    tail = bollinger(closes[-period:], period=period, multiple=Decimal("2.5"))

    for name in ("middle", "upper", "lower", "width", "position"):
        full_value = getattr(whole, name)[-1]
        tail_value = getattr(tail, name)[-1]
        assert full_value is not None
        assert tail_value is not None
        assert tail_value.as_tuple() == full_value.as_tuple(), name
