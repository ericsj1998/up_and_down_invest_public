"""피벗 앞토막 항등식 — 추세 평가 O(n²) 제거의 **근거**를 잠근다.

`find_pivots` 의 판정은 국소적이다: 위치 `p` 는 `[p-left, p+right]` 만 본다. 그래서
뒤에 봉이 더 붙어도 이미 내려진 판정은 바뀌지 않는다. 이 파일은 그 성질을 성질
테스트로 고정한다 — `trend.evaluate` 가 봉마다 전체를 다시 훑지 않아도 되는 근거가
바로 이것이기 때문이다.

⚠️ 이 항등식이 깨지면 `evaluate` 의 결과가 **조용히** 달라진다 (절대 규칙 #5·#8).
그때 할 일은 테스트를 고치는 것이 아니라 최적화를 되돌리는 것이다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.structures.params import SwingParams
from updown.analysis.structures.swing import RollingZigzag, find_pivots, prior_swings, zigzag
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

FRAME = Timeframe.M5
BASE = datetime(2026, 1, 1, tzinfo=UTC)
COIN = Instrument(
    symbol="BTC_USDT",
    name="비트코인 무기한",
    market=Market.GATE,
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)


def _bar(index: int, high: float, low: float, *, skip: int = 0) -> Candle:
    """고가·저가만 의미 있는 봉 — 피벗 판정은 그 둘만 본다."""
    mid = Decimal(str((high + low) / 2))
    return Candle(
        instrument=COIN,
        timeframe=FRAME,
        ts=BASE + timedelta(minutes=5 * (index + skip)),
        open=mid,
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=mid,
        volume=Decimal(100),
    )


def _wave(count: int, *, gap_at: int | None = None) -> list[Candle]:
    """결정론적 톱니 — 난수를 쓰면 실패를 재현할 수 없다 (절대 규칙 #5).

    `gap_at` 을 주면 그 지점부터 시각을 건너뛴다. `find_pivots` 는 결측 구간을
    나눠서 훑으므로, **구간 경계를 넘어서도** 항등식이 성립하는지가 진짜 관문이다.
    """
    bars: list[Candle] = []
    skip = 0
    for index in range(count):
        if gap_at is not None and index == gap_at:
            skip = 40
        # 서로 다른 주기(7·5·11·9)를 겹쳐 평평한 고점·이중 바닥이 섞이게 한다 —
        # 좌엄격/우느슨 규칙이 실제로 걸리는 자리를 만들기 위함이다.
        bars.append(_bar(index, 100 + (index * 7) % 11, 90 - (index * 5) % 9, skip=skip))
    return bars


@pytest.mark.parametrize("params", [SwingParams(), SwingParams(left_bars=3, right_bars=3)])
@pytest.mark.parametrize("gap_at", [None, 60])
def test_prefix_equals_filtered_whole(params: SwingParams, gap_at: int | None) -> None:
    """앞토막으로 계산한 것 == 전체에서 우측 확인이 끝난 것만 고른 것."""
    bars = _wave(150, gap_at=gap_at)
    whole = find_pivots(bars, FRAME, params)
    right = params.right_bars

    for length in range(20, len(bars) + 1, 7):
        assert find_pivots(bars[:length], FRAME, params) == [
            pivot for pivot in whole if pivot.index + right < length
        ], f"길이 {length} 에서 앞토막이 깨졌다"


@pytest.mark.parametrize("gap_at", [None, 60])
def test_prior_swings_follows_the_prefix(gap_at: int | None) -> None:
    """`zigzag` 는 좌→우 접기라 앞토막 위에서 그대로 성립한다."""
    bars = _wave(150, gap_at=gap_at)
    params = SwingParams()
    whole = find_pivots(bars, FRAME, params)
    right = params.right_bars

    for length in range(20, len(bars) + 1, 11):
        cut = [pivot for pivot in whole if pivot.index + right < length]
        assert prior_swings(bars[:length], FRAME, params) == zigzag(cut)


@pytest.mark.parametrize("gap_at", [None, 60])
def test_rolling_zigzag_matches_batch(gap_at: int | None) -> None:
    """이어붙인 접기 == 매번 처음부터 접기. 갈라지면 결정론이 깨진다 (규칙 #5)."""
    whole = find_pivots(_wave(150, gap_at=gap_at), FRAME, SwingParams())
    roller = RollingZigzag()

    for count in range(len(whole) + 1):
        # 같은 봉의 피벗은 함께 확정되므로 묶음 경계에서만 비교한다 — 묶음을 쪼개
        # 넣는 것은 `evaluate` 가 하지 않는 호출이다.
        if count and count < len(whole) and whole[count].index == whole[count - 1].index:
            continue
        assert roller.upto(whole, count) == zigzag(whole[:count]), f"{count} 개에서 갈라졌다"


def test_rolling_zigzag_hands_out_copies() -> None:
    """돌려준 결과가 다음 호출에 **소급 변경되면 안 된다**.

    `_fold_into` 는 `kept[-1] = pivot` 으로 제자리를 고친다. 내부 리스트를 그대로
    넘겼다면 이미 판정에 쓰인 스윙 열이 나중에 바뀐다.
    """
    whole = find_pivots(_wave(150), FRAME, SwingParams())
    roller = RollingZigzag()
    early = roller.upto(whole, 10)
    snapshot = list(early)
    roller.upto(whole, len(whole))
    assert early == snapshot, "먼저 돌려준 결과가 뒤에 바뀌었다"


def test_rolling_zigzag_refuses_to_go_backwards() -> None:
    """창이 미끄러지면 이어붙이기가 성립하지 않는다 — 조용히 틀리지 않는다 (규칙 #8)."""
    whole = find_pivots(_wave(150), FRAME, SwingParams())
    roller = RollingZigzag()
    roller.upto(whole, 20)
    with pytest.raises(ValueError, match="뒤로 물러났다"):
        roller.upto(whole, 10)


def test_the_prefix_is_a_true_prefix() -> None:
    """앞토막이 **연속된 앞부분**이어야 포인터 하나로 전진할 수 있다.

    `find_pivots` 는 `(index, kind)` 로 정렬해 돌려주므로 `index + right` 도 단조
    증가한다. 중간이 비면 슬라이스가 아니라 매번 필터가 필요해진다.
    """
    whole = find_pivots(_wave(150, gap_at=60), FRAME, SwingParams())
    keys = [pivot.index for pivot in whole]
    assert keys == sorted(keys), "인덱스가 단조가 아니면 앞토막 전진이 성립하지 않는다"
