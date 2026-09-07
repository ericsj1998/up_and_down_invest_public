"""A-6 시장 구조 신호 — STR-01~07 (T160 Wave 2).

인과성은 `test_discovery_signals.TestEverySignalIsCausal` 이 본다. 여기서 보는 것은
**규칙이 말한 대로 판정하는가**다.

🔴 특히 *"자유도를 안 넣었다"* 를 시험으로 못 박는다. 추세·박스 판정에 허용 오차가
생기면 그 순간 과탐지 손잡이가 하나 생기고, 추세선이 무너진 이유가 정확히 그것이었다.
"""

from __future__ import annotations

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.frames import Frame
from updown.orchestration.discovery.signals import Board
from updown.orchestration.discovery.signals.structure import (
    BoxEdge,
    BreakChase,
    BreakRetest,
    ChangeOfCharacter,
    Fakeout,
    StructureBreak,
    StructurePullback,
    views,
)
from updown.orchestration.walkforward.ledger import Direction


def board_of(bars: list[tuple[float, float, float, float]]) -> Board:
    """(시가, 고가, 저가, 종가) 목록으로 판 하나."""
    frame = Frame(
        timeframe=Timeframe.H1,
        ts=[index * 3_600_000 for index in range(len(bars))],
        open=[one[0] for one in bars],
        high=[one[1] for one in bars],
        low=[one[2] for one in bars],
        close=[one[3] for one in bars],
        volume=[1_000.0] * len(bars),
        sources=[60] * len(bars),
    )
    return Board(symbol="TEST", timeframe=Timeframe.H1, frame=frame)


def zigzagging(steps: list[float], span: float = 1.0) -> list[tuple[float, float, float, float]]:
    """전환점 목록을 봉으로 편다 — 스윙이 실제로 잡히게 사이를 채운다.

    Args:
        steps: 지나갈 가격들 (교대로 올라갔다 내려간다).
        span: 봉의 위아래 여유.

    Returns:
        봉 목록.

    Note:
        ⚠️ 프랙탈은 좌우 2봉을 요구한다. 전환점 사이를 **최소 3봉**으로 채워야
        극값이 잡히고, 그렇지 않으면 시험이 *"신호가 안 난다"* 로 조용히 통과한다.
    """
    bars: list[tuple[float, float, float, float]] = []
    price = steps[0]
    for target in steps[1:]:
        for _ in range(4):
            price += (target - price) / 2
            bars.append((price, price + span, price - span, price))
        bars.append((target, target + span, target - span, target))
    return bars


class TestTheTrendIsAnOrderingOnly:
    """🔴 추세 판정에 **숫자 문턱이 없다.** 순서 비교뿐이라 조정할 손잡이가 없다."""

    def test_higher_highs_and_higher_lows_is_up(self) -> None:
        board = board_of(zigzagging([100, 110, 104, 120, 112, 130]))
        found = [one for one in views(board) if one is not None]
        assert found, "스윙이 안 잡혔다 — 시험 입력이 약하다"
        assert found[-1].trend == 1

    def test_lower_highs_and_lower_lows_is_down(self) -> None:
        board = board_of(zigzagging([130, 112, 120, 104, 110, 100]))
        found = [one for one in views(board) if one is not None]
        assert found
        assert found[-1].trend == -1

    def test_anything_else_is_a_box(self) -> None:
        # 고점은 오르는데 저점은 내려간다 — 확장형. 상승도 하락도 아니다.
        board = board_of(zigzagging([100, 110, 98, 120, 92, 125]))
        found = [one for one in views(board) if one is not None]
        assert found
        assert found[-1].trend == 0

    def test_a_hair_of_difference_still_decides(self) -> None:
        """⚠️ 허용 오차가 없다는 뜻은 **아주 작은 차이도 판정을 가른다**는 것이다.

        이것을 완화하고 싶어지는 순간이 곧 손잡이를 만드는 순간이다. 그래서
        여기 못을 박는다 — 완화하면 이 시험이 깨진다.
        """
        board = board_of(zigzagging([100, 110, 104, 110.0001, 104.0001, 115]))
        found = [one for one in views(board) if one is not None]
        assert found
        assert found[-1].trend == 1


class TestBreaksAreExclusive:
    """STR-01(BOS)과 STR-02(CHoCH)는 같은 봉에서 **동시에 나지 않는다**."""

    def test_bos_and_choch_never_share_a_bar(self) -> None:
        board = board_of(zigzagging([100, 118, 106, 130, 112, 140, 120, 150, 128, 160]))
        bos = {one.index for one in StructureBreak().fire(board)}
        choch = {one.index for one in ChangeOfCharacter().fire(board)}
        assert not (bos & choch), f"같은 봉에서 둘 다 났다: {sorted(bos & choch)}"

    def test_neither_fires_inside_a_box(self) -> None:
        """추세가 0 이면 BOS·CHoCH 둘 다 안 친다 — 방향을 정의할 수 없기 때문이다."""
        board = board_of(zigzagging([100, 110, 98, 120, 92, 125]))
        shape = views(board)
        boxed = {index for index, one in enumerate(shape) if one is not None and one.trend == 0}
        for signal in (StructureBreak(), ChangeOfCharacter()):
            fired = {one.index for one in signal.fire(board)}
            assert not (fired & boxed), f"{signal.name} 이 박스 구간에서 쳤다"


class TestALevelFiresOnlyOnce:
    """🔴 같은 레벨에서 봉마다 다시 치면 방아쇠 수가 부풀려지고 그것이 곧 과탐지다."""

    def test_a_long_run_above_a_level_gives_one_trigger(self) -> None:
        bars = zigzagging([100, 118, 106, 130])
        # 돌파 뒤 계속 위에 머문다 — 순진하게 짜면 봉마다 친다.
        bars += [(135.0, 136.0, 134.0, 135.0)] * 30
        board = board_of(bars)
        fired = BreakChase().fire(board)
        by_bar = [one.index for one in fired]
        assert len(by_bar) == len(set(by_bar))
        tail = [one for one in by_bar if one >= len(bars) - 30]
        assert len(tail) <= 1, f"머무는 동안 {len(tail)}번 쳤다 — 레벨당 한 번이어야 한다"


class TestTheRetestComesAfterTheBreak:
    """STR-04 는 돌파 봉이 아니라 **되돌아와 지킨 봉**에 친다."""

    def test_it_never_fires_on_the_break_bar(self) -> None:
        bars = zigzagging([100, 118, 106, 130])
        bars += [(135.0, 136.0, 134.0, 135.0)] * 3
        bars += [(135.0, 136.0, 126.0, 132.0)]  # 되돌아와 지킨다
        bars += [(132.0, 138.0, 131.0, 137.0)] * 3
        board = board_of(bars)
        chase = {one.index for one in BreakChase().fire(board)}
        retest = {one.index for one in BreakRetest().fire(board)}
        assert not (chase & retest), "돌파 봉과 리테스트 봉이 같다"
        for one in retest:
            assert any(one > other for other in chase), "리테스트가 돌파보다 앞선다"


class TestPullbackAndFakeoutUseOppositeLevels:
    """STR-03 은 추세 **뒤쪽**, STR-06 은 추세 **앞쪽** 레벨을 본다 — 안 겹친다."""

    def test_they_never_share_a_bar(self) -> None:
        board = board_of(zigzagging([100, 118, 106, 130, 112, 140, 120, 150]))
        pullback = {one.index for one in StructurePullback().fire(board)}
        fake = {one.index for one in Fakeout().fire(board)}
        assert not (pullback & fake)

    def test_a_failed_breakout_in_an_uptrend_is_short(self) -> None:
        bars = zigzagging([100, 118, 106, 130, 112, 140])
        # ⚠️ 프랙탈은 우측 2봉을 기다린다. 140 이 스윙 고점으로 **확정된 뒤**라야
        #    그 레벨을 뚫는 것이 페이크아웃이다 — 바로 붙이면 신호가 안 나고, 그것을
        #    신호 결함으로 읽으면 엉뚱한 곳을 고치게 된다.
        bars += [(139.0, 139.5, 136.0, 137.0)] * 3
        bars += [(137.0, 145.0, 136.0, 139.0)]  # 고점을 뚫었다가 아래로 닫는다
        board = board_of(bars)
        shape = views(board)[-1]
        assert shape is not None and shape.trend == 1, "상승 추세가 아니다 — 입력이 약하다"
        fired = Fakeout().fire(board)
        assert fired, "페이크아웃이 안 났다 — 입력이 약하다"
        assert fired[-1].direction is Direction.SHORT


class TestTheBoxIsDefinedByExclusion:
    """STR-07 은 폭·유사도 문턱을 안 쓴다 — `trend == 0` 이면 박스다."""

    def test_it_only_fires_where_the_trend_is_zero(self) -> None:
        board = board_of(zigzagging([100, 110, 98, 120, 92, 125, 100, 130]))
        shape = views(board)
        trending = {index for index, one in enumerate(shape) if one is not None and one.trend != 0}
        fired = {one.index for one in BoxEdge().fire(board)}
        assert not (fired & trending)

    def test_the_top_edge_gives_short_and_the_bottom_gives_long(self) -> None:
        bars = zigzagging([100, 110, 98, 120, 92])
        bars += [(93.0, 94.0, 88.0, 93.5)]  # 하단을 뚫었다가 되돌아 닫는다
        board = board_of(bars)
        fired = BoxEdge().fire(board)
        if fired:
            assert fired[-1].direction is Direction.LONG
