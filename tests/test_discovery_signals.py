"""등록된 신호 28종이 **실제로 터지는가** (T153).

⚠️ 등록만 되고 한 번도 안 터지는 신호는 격자에서 **조용히 사라진다.** 그 칸은 표에
안 나오는데 FDR 분모에서도 빠진다 — 즉 시행 횟수를 몰래 줄여 준다. 등록했으면
터져야 하고, 안 터지면 그 사실을 여기서 알아야 한다.

## 🔴 입력에 **변동성 군집**을 넣는다

처음에 순수 랜덤워크로 시험했더니 OSC-10(ATR 급증)이 0건이었다. 버그가 아니라
**입력이 부실**했다 — 변동성이 일정한 계열에서는 ATR 이 중앙값의 2배가 될 일이
없다. 실제 시장에는 조용한 구간과 격한 구간이 번갈아 오고(변동성 군집), 그것이
없는 입력으로는 국면 의존 신호를 시험할 수 없다.

⇒ 이 파일의 합성 계열은 **변동성이 구간마다 바뀐다.** 그래도 방향에는 정보가 없어
  신호 중 무엇도 유리하지 않다.
"""

import random
from dataclasses import replace

import pytest

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.cheats import cheats
from updown.orchestration.discovery.frames import Frame
from updown.orchestration.discovery.independence import correlation, effective_tests
from updown.orchestration.discovery.signals import Board, registry
from updown.orchestration.discovery.signals.base import (
    SOURCES,
    SignalMeta,
    declare,
    declared,
)


def wobble(count: int, seed: int = 17) -> Frame:
    """변동성이 구간마다 바뀌는 무방향 계열.

    Note:
        ⭐ 방향에는 정보가 없다 (평균 0). 바뀌는 것은 **변동성의 크기**뿐이라,
        국면 의존 신호(ATR 급증·볼밴 압축)가 터질 자리는 생기되 어느 신호도
        수익 면에서 유리하지 않다.
    """
    dice = random.Random(seed)
    price = 100.0
    calm = 0.002
    wild = 0.012
    open_: list[float] = []
    high: list[float] = []
    low: list[float] = []
    close: list[float] = []
    turnover: list[float] = []
    spread = calm
    for index in range(count):
        # 200봉쯤마다 변동성 국면이 바뀐다.
        if index % 200 == 0:
            spread = wild if dice.random() < 0.35 else calm
        start = price
        end = price * (1 + dice.gauss(0, spread))
        open_.append(start)
        high.append(max(start, end) * (1 + abs(dice.gauss(0, spread / 2))))
        low.append(min(start, end) * (1 - abs(dice.gauss(0, spread / 2))))
        close.append(end)
        # ⚠️ 거래량도 움직여야 한다. 상수로 두면 DIV-05(거래량 다이버전스)가
        #    영영 안 터지고, 그것은 신호의 문제가 아니라 **입력의 문제**다
        #    (OSC-10 을 랜덤워크로 시험했을 때와 같은 실수).
        turnover.append(1000.0 * (1 + abs(dice.gauss(0, 0.6))))
        price = end
    return Frame(
        timeframe=Timeframe.M15,
        ts=[900_000 * i for i in range(count)],
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=turnover,
        sources=[15] * count,
    )


@pytest.fixture(scope="module")
def board() -> Board:
    return Board(symbol="BTCUSDT", timeframe=Timeframe.M15, frame=wobble(4000))


class TestTheRegistry:
    def test_every_signal_fires_at_least_once(self, board: Board) -> None:
        """🔴 안 터지는 신호는 시행 횟수를 몰래 줄인다."""
        silent = [name for name, one in sorted(registry().items()) if not one.fire(board)]
        assert not silent, f"4,000봉에서 한 번도 안 터진 신호: {silent}"

    def test_triggers_are_in_range_and_ascending(self, board: Board) -> None:
        """⚠️ 순서가 어긋나면 `observe` 가 엉뚱한 봉에서 진입한다."""
        for name, signal in sorted(registry().items()):
            fired = signal.fire(board)
            assert all(0 <= one.index < len(board.frame) for one in fired), name
            assert fired == sorted(fired, key=lambda one: one.index), f"{name} 오름차순 아님"

    def test_names_are_unique_and_match_the_catalog(self) -> None:
        """이름이 `trading_criteria.md` 코드와 짝이 맞아야 표를 문서와 대조할 수 있다."""
        names = sorted(registry())
        assert len(names) == len(set(names))
        for name in names:
            stem = name.split("(")[0]
            assert "-" in stem, name
            assert stem.split("-")[0] in {"OSC", "MA", "BND", "DIV", "LVL", "STR", "CTRL"}, name

    def test_the_control_is_present(self) -> None:
        """🔴 대조군이 빠지면 *"Tier A 0"* 이 무슨 뜻인지 알 수 없다."""
        assert "CTRL-01" in registry()

    def test_the_grid_is_not_secretly_one_cell(self, board: Board) -> None:
        """⚠️ 신호들이 사실상 같은 것이면 격자가 넓어 보일 뿐 한 칸이다.

        ⭐ 겹침을 **최다 신호와의 교집합**으로 재면 안 된다 — 자주 터지는 신호가
        하나 있으면 나머지가 전부 그 부분집합처럼 보인다 (실측: OSC-03 이
        MA-04(20) 과 96% 겹치는 것처럼 나왔는데, MA-04 가 봉의 30% 에서 터져서였다).

        ⇒ 종목 독립성과 **같은 도구**로 잰다 (참여비). 2년 실측은 16종 → 14.15 였다.
        """
        size = len(board.frame)
        series: dict[str, list[float]] = {}
        for name, signal in registry().items():
            flags = [0.0] * size
            for one in signal.fire(board):
                flags[one.index] = 1.0 if one.direction.sign > 0 else -1.0
            if any(flags):
                series[name] = flags
        _, matrix = correlation(series)
        got = effective_tests(matrix)
        assert got.participation > got.count * 0.5, (
            f"유효 {got.participation:.2f} / 명목 {got.count} — 신호들이 서로 너무 겹친다"
        )


class TestEverySignalIsCausal:
    """🔴 **방아쇠는 그 봉까지의 정보만으로 나와야 한다.**

    이 시험이 없을 때 다이버전스 4종이 전부 미래를 보고 있었고, 그것이 성과의
    **전부**였다 (2026-08-31 실측):

        DIV-03 1h 손절+익절   미래 참조 있을 때 마진 2.38  ·  고친 뒤 **0.77**
        2.0 을 넘는 해        5/7년 → **0/7년**

    원인은 `prior_swings` 의 교대 정리였다. 같은 방향 구간에서 더 극단적인 점이
    나중에 나오면 앞의 점을 **교체**하는데, 완성된 열을 쓰면 그 교체가 이미 반영돼
    있다. 즉 *"나중에 더 좋은 극값이 온다"* 를 미리 아는 자리들이었고, 하필 그
    0.7% 가 엣지의 정체였다.

    ⚠️ 자르기 시험을 **여유를 두고** 하면 못 잡는다. 처음엔 끝 8봉을 빼고 비교했는데
    다이버전스의 확인 지연이 2봉이라 통째로 사각지대였다 — 일부러 심은 CHEAT(다음
    봉을 보는 가짜 신호)도 안 잡혔다. 그래서 여기서는 **딱 그 봉 하나**를 본다.
    """

    @staticmethod
    def _board(count: int = 320) -> Board:
        """되돌림과 추세가 섞인 판 — 스윙이 실제로 교체되게 만든다."""
        dice = random.Random(20260831)
        price = 100.0
        ts: list[int] = []
        o: list[float] = []
        h: list[float] = []
        low: list[float] = []
        c: list[float] = []
        v: list[float] = []
        for index in range(count):
            drift = 0.4 if (index // 25) % 2 == 0 else -0.4
            step = drift + dice.gauss(0, 1.4)
            opened = price
            price = max(1.0, price + step)
            top = max(opened, price) + abs(dice.gauss(0, 0.9))
            bottom = min(opened, price) - abs(dice.gauss(0, 0.9))
            ts.append(index * 3_600_000)
            o.append(opened)
            h.append(top)
            low.append(max(0.5, bottom))
            c.append(price)
            v.append(1_000 + abs(dice.gauss(0, 400)))
        frame = Frame(
            timeframe=Timeframe.H1,
            ts=ts,
            open=o,
            high=h,
            low=low,
            close=c,
            volume=v,
            sources=[60] * count,
        )
        return Board(symbol="TEST", timeframe=Timeframe.H1, frame=frame)

    @staticmethod
    def _cut(board: Board, count: int) -> Board:
        """앞 `count` 봉만 든 **새** 판 — 캐시가 따라오면 시험이 항상 통과한다."""
        frame = board.frame
        return Board(
            symbol=board.symbol,
            timeframe=board.timeframe,
            frame=replace(
                frame,
                ts=frame.ts[:count],
                open=frame.open[:count],
                high=frame.high[:count],
                low=frame.low[:count],
                close=frame.close[:count],
                volume=frame.volume[:count],
                sources=frame.sources[:count],
            ),
        )

    def test_no_signal_uses_a_bar_it_cannot_see(self) -> None:
        board = self._board()
        probes = range(240, len(board.frame), 4)
        for name, signal in sorted(registry().items()):
            truth = {(one.index, one.direction) for one in signal.fire(board)}
            for k in probes:
                short = self._cut(board, k + 1)
                here = {(one.index, one.direction) for one in signal.fire(short) if one.index == k}
                there = {one for one in truth if one[0] == k}
                assert here == there, f"{name} 가 {k}봉에서 미래를 본다: {here} != {there}"

    def test_every_planted_cheat_is_caught(self) -> None:
        """⭐ 시험 자신을 시험한다 — 하나라도 안 잡히면 위 시험은 아무것도 안 지킨다.

        T158 §0-B 가 요구하는 여섯 유형이다. 실제로 첫 검사기가 C1 을 못 잡았고,
        심어 두지 않았으면 *"33종 전부 통과"* 를 믿었을 것이다.

        ⚠️ **방아쇠 자리를 물어야** C5(사후 교체형)가 잡힌다. 무작위 봉만 물으면
        스윙 429개 중 3개짜리 결함은 표본에 안 들어온다 — 그런데 그 3개가 성과의
        전부였다.
        """
        board = self._board(600)
        missed: list[str] = []
        for name, cheat in sorted(cheats().items()):
            truth = {(one.index, one.direction) for one in cheat.fire(board)}
            fired = sorted(one[0] for one in truth if one[0] >= 240)
            asked = fired[:: max(1, len(fired) // 60)] if fired else []
            caught = 0
            for k in asked:
                short = self._cut(board, k + 1)
                here = {(one.index, one.direction) for one in cheat.fire(short) if one.index == k}
                if here != {one for one in truth if one[0] == k}:
                    caught += 1
            if caught == 0:
                missed.append(name)
        assert not missed, f"검사기가 못 잡은 가짜 신호: {missed} — 검사기부터 고친다"


class TestEverySignalDeclaresWhenItIsConfirmed:
    """🔴 확정 시점을 **선언하지 않은 신호는 CI 를 통과 못 한다** (오더 3-E-2).

    2026-08-31 에 다이버전스 4종이 상수 지연 2 를 쓰다 전부 미래를 봤고, 그것이
    성과의 전부였다 (DIV-03 마진 2.38 → 0.77). 원인은 **상수로 못 덮는 것을 상수로
    덮은 것**이다 — 교대 정리는 프랙탈 폭과 무관하게 과거 스윙을 교체한다.

    ⇒ 새 신호를 넣을 때 *"이건 언제 확정되나"* 를 **반드시 지나가게** 한다.
    """

    def test_the_declarations_match_the_registry_exactly(self) -> None:
        listed = set(registry())
        said = set(declared())
        assert not listed - said, f"확정 시점을 선언 안 한 신호: {sorted(listed - said)}"
        assert not said - listed, f"등록도 안 됐는데 선언된 코드: {sorted(said - listed)}"

    def test_a_constant_delay_is_never_claimed_for_swing_based_signals(self) -> None:
        """스윙에 기대는 신호가 상수 지연을 주장하면 그것이 바로 그때의 결함이다."""
        for code, meta in sorted(declared().items()):
            if meta.confirm_event_source == "zigzag_events":
                assert meta.confirm_delay_bars is None, (
                    f"{code}: 교대 정리는 상수 지연으로 못 덮는다 — 실측에서 3개 스윙이"
                    " 6·7·9봉 걸렸고 상한의 근거가 없다"
                )

    def test_the_source_is_one_we_know(self) -> None:
        for code, meta in sorted(declared().items()):
            assert meta.confirm_event_source in SOURCES, f"{code}: {meta.confirm_event_source}"

    def test_declaring_twice_raises(self) -> None:
        with pytest.raises(ValueError, match="두 번 선언"):
            declare(SignalMeta("OSC-01", 0, "bar_close"))

    def test_an_unknown_source_raises(self) -> None:
        with pytest.raises(ValueError, match="모르는 확정 출처"):
            declare(SignalMeta("ZZZ-99", 0, "vibes"))
