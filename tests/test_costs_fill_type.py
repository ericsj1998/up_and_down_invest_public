"""T42 ④ — 체결 유형별 왕복 비용이 **산수대로** 나오는가.

⛔ 이것은 비용을 낮춰 전략을 살리는 것이 아니다. 전부-테이커 모델(`round_trip_pct`)은
그대로 남고, 이 값은 *"실제로 어떤 다리로 채워졌나"* 를 세는 별도 칸이다.
"""

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.instrument import Market


def test_both_maker_is_the_cheapest_and_both_taker_matches_the_old_exit_model() -> None:
    """메이커 진입 + 메이커 익절이 가장 싸고, 테이커 둘은 `round_trip_for` 와 같다."""
    gate = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE)
    both_maker = gate.round_trip_by(entry_is_maker=True, exit_is_maker=True)
    both_taker = gate.round_trip_by(entry_is_maker=False, exit_is_maker=False)
    mixed = gate.round_trip_by(entry_is_maker=True, exit_is_maker=False)
    assert both_maker == gate.maker * 2 + gate.tax_pct_sell + gate.slippage_pct_one_way * 2
    assert both_taker == gate.round_trip_for(exit_is_maker=False)
    assert both_maker < mixed < both_taker
    # Gate 실측 요율(테이커 0.05 · 메이커 0.02)은 전부-테이커 가정(0.075 x 2)보다 싸다.
    assert both_taker < gate.round_trip_pct


def test_markets_without_maker_taker_split_collapse_to_one_number() -> None:
    """업비트처럼 요율이 하나면 어떤 조합이든 같은 값이다 — 기존 시장 블록은 그대로."""
    upbit = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.UPBIT)
    assert (
        upbit.round_trip_by(entry_is_maker=True, exit_is_maker=True)
        == upbit.round_trip_by(entry_is_maker=False, exit_is_maker=False)
        == upbit.round_trip_pct
    )
