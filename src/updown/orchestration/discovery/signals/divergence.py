"""A-2 다이버전스 — DIV-01/02/03/05 (T153).

## ⭐ 정규 다이버전스는 **이미 있는 것을 쓴다**

`analysis/indicators/rsi.find_divergences()` 가 스윙 열과 오실레이터를 대조해
정규 다이버전스를 찾는다. 여기서 다시 짜면 두 벌이 되고, 그 둘이 갈리면 어느 쪽이
기준인지 흐려진다.

⚠️ 그 함수는 **`prior_swings()` 결과**를 요구한다 — 교대 정리된 열이다.
`find_pivots()` 를 넘기면 같은 봉우리의 여러 점이 각각 신저점으로 세어져
다이버전스가 부풀려진다.

## 🔴 히든 다이버전스는 **정규의 반대**다 — 새 규칙이다

    정규(반전)   가격 신저점 · 오실레이터 저점 **상승**   → 하락 동력 약화
    히든(지속)   가격 저점 **상승** · 오실레이터 신저점   → 추세 지속

`find_divergences()` 는 정규만 본다. 히든은 조건이 반대라 여기서 새로 쓴다 —
**중복이 아니라 다른 규칙**이다. 스윙 열은 같은 것을 쓴다.

## ⛔ DIV-04(CVD/델타)는 여기 없다

체결 단위 데이터(매수·매도 플래그)가 필요한데 우리가 든 것은 OHLCV 뿐이다.
`logs/scalp_data/aggtrades/` 에 BTC 6년치가 있지만 플래그가 빠져 있어 다시 받아야
한다 (3순위). **0 으로 채우거나 근사로 대신하지 않는다** — 없는 것은 없는 것이다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from updown.analysis.indicators.rsi import DivergenceKind, find_divergences
from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = ["HiddenDivergence", "MacdDivergence", "RsiDivergence", "VolumeDivergence"]


@dataclass(frozen=True, slots=True)
class RsiDivergence:
    """DIV-01 — RSI 정규 다이버전스 (반전).

    Note:
        ⚠️ 방아쇠 봉은 **뒤따르는 스윙**(`second_index`)이다. 그런데 스윙은 우측
        봉이 있어야 확정되므로, 그 시점에 실제로 알 수 있는 것은 몇 봉 뒤다.
        `prior_swings` 가 쓰는 확인 지연만큼 **늦춰서** 방아쇠를 놓는다 —
        스윙 봉에 바로 놓으면 미래 참조다 (계획서 §0-3).
    """

    @property
    def name(self) -> str:
        """DIV-01."""
        return "DIV-01"

    def fire(self, board: Board) -> list[Trigger]:
        """정규 다이버전스가 **확인된** 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        return _stream(board, board.rsi14)


@dataclass(frozen=True, slots=True)
class MacdDivergence:
    """DIV-03 — MACD 히스토그램 다이버전스.

    Note:
        ⭐ 같은 `find_divergences()` 를 쓴다 — 그 함수는 RSI 전용이 아니라 **스윙과
        오실레이터를 대조**하는 것이고, 히스토그램도 오실레이터다.

        ⚠️ 그래서 DIV-01 과 **같은 스윙 열**을 본다. 두 신호가 같은 봉에서 자주
        같이 터질 수 있고, 겹치는지는 유효 신호 수가 답한다.
    """

    @property
    def name(self) -> str:
        """DIV-03."""
        return "DIV-03"

    def fire(self, board: Board) -> list[Trigger]:
        """MACD 다이버전스가 확인된 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        histogram = [None if one is None else float(one) for one in board.macd.histogram]
        return _stream(board, histogram)


@dataclass(frozen=True, slots=True)
class HiddenDivergence:
    """DIV-02 — 히든 다이버전스 (추세 지속).

    Note:
        🔴 정규의 **반대 조건**이다: 가격 저점은 올라가는데 오실레이터 저점은
        내려간다. `find_divergences()` 가 정규만 보므로 여기서 새로 쓴다 —
        중복이 아니라 다른 규칙이다. 스윙 열은 같은 것을 쓴다.

        ⚠️ 정규(반전)와 히든(지속)은 **정반대 전략**이다. 같은 자리를 하나는
        되돌림으로, 하나는 지속으로 읽는다 — 어느 쪽이 맞는지는 국면이 정하고
        그것을 가르는 것은 Stage 3 이다 (BND-03/04 와 같은 구조).
    """

    @property
    def name(self) -> str:
        """DIV-02."""
        return "DIV-02"

    def fire(self, board: Board) -> list[Trigger]:
        """히든 다이버전스가 확인된 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        return _stream(board, board.rsi14, hidden=True)


@dataclass(frozen=True, slots=True)
class VolumeDivergence:
    """DIV-05 — 거래량 다이버전스.

    Note:
        ⭐ 가격은 신고점인데 **거래량이 안 따라오는** 자리. 오실레이터 자리에
        거래량을 넣은 것이라 `find_divergences()` 를 그대로 쓴다.

        ⚠️ 거래량은 추세가 있고 규모가 종목마다 다르다. 그래도 이 함수는 **두 스윙의
        비교**만 하므로 규모는 상관없고, 긴 추세는 인접 스윙 사이에서는 거의 안
        움직인다.
    """

    @property
    def name(self) -> str:
        """DIV-05."""
        return "DIV-05"

    def fire(self, board: Board) -> list[Trigger]:
        """거래량 다이버전스가 확인된 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        volume = [float(one) for one in board.frame.volume]
        return _stream(board, volume)


def _pairs(board: Board) -> Iterator[tuple[int, list[SwingPoint]]]:
    """교대 정리가 **바뀌는 순간마다** `(그 봉, 마지막 같은 종류 두 스윙)`.

    Args:
        board: 봉과 캔들.

    Yields:
        `(알게 되는 봉, [앞 스윙, 뒤 스윙])` — 둘은 같은 종류다.

    Note:
        🔴 **전 구간 스윙 열을 쓰지 않는다.** 교대 정리는 같은 방향 구간에서 더
        극단적인 점이 나중에 나오면 앞의 점을 **교체**하므로, 완성된 열의 어떤
        원소도 *"그 시점에 그 값이었다"* 를 보장하지 않는다.

        실측(2026-08-31): 스윙 429개 중 3개가 굳는 데 6·7·9봉이 걸렸고, 그 0.7% 가
        다이버전스 4종 전부에서 미래 참조로 잡혔다 (`lookahead.py`). **상수 지연을
        키워도, 다음 스윙을 기다려도 안 풀렸다** — 다음 스윙 자신이 또 교체된다.

        ⭐ 그래서 접히는 **순간**을 쓴다. `zigzag_events` 가 내주는 각 단계는
        정의상 그 봉까지의 정보만으로 만들어진 것이다.

        ⚠️ 교대 열이므로 마지막과 **세 번째 뒤**가 같은 종류다 (`kept[-1]`·`kept[-3]`).
        바로 앞(`kept[-2]`)은 반대 종류라 비교 대상이 아니다.
    """
    size = len(board.frame)
    for known_at, kept in board.swing_events():
        if known_at >= size or len(kept) < 3:
            continue
        first, second = kept[-3], kept[-1]
        if first.kind is second.kind:
            yield known_at, [first, second]


def _stream(board: Board, values: Sequence[float | None], *, hidden: bool = False) -> list[Trigger]:
    """같은 종류 스윙 쌍이 생길 때마다 다이버전스를 묻는다.

    Args:
        board: 봉과 지표.
        values: 오실레이터 열 — 봉과 같은 좌표계.
        hidden: 참이면 히든(지속), 거짓이면 정규(반전).

    Returns:
        방아쇠들 — 오름차순.

    Note:
        ⭐ 정규는 판정을 **다시 쓰지 않고** `find_divergences()` 에 쌍 하나를
        그대로 넘긴다. 여기서 조건을 다시 적으면 정의가 두 벌이 되고, 그 둘이
        갈리면 어느 쪽이 기준인지 흐려진다.

        ⚠️ 같은 쌍은 **한 번만** 낸다. 교대 열이 다른 이유로 바뀌어도 그 쌍은
        이미 알던 것이라 다시 세면 방아쇠가 부풀려진다.
    """
    seen: set[tuple[int, int]] = set()
    found: list[Trigger] = []
    for known_at, pair in _pairs(board):
        key = (pair[0].index, pair[1].index)
        if key in seen:
            continue
        before, after = values[pair[0].index], values[pair[1].index]
        if before is None or after is None:
            continue
        if hidden:
            if pair[0].kind is SwingKind.LOW:
                # 가격 저점은 올라가고 오실레이터 저점은 내려간다.
                ok = pair[1].price > pair[0].price and after < before
            else:
                ok = pair[1].price < pair[0].price and after > before
            if not ok:
                continue
            way = Direction.LONG if pair[0].kind is SwingKind.LOW else Direction.SHORT
            seen.add(key)
            found.append(Trigger(index=known_at, direction=way))
            continue
        for one in find_divergences(list(pair), values):
            seen.add(key)
            way = Direction.LONG if one.kind is DivergenceKind.BULLISH else Direction.SHORT
            found.append(Trigger(index=known_at, direction=way))
    return sorted(found, key=lambda one: one.index)


for _signal in (RsiDivergence(), HiddenDivergence(), MacdDivergence(), VolumeDivergence()):
    register(_signal)

declare(
    SignalMeta("DIV-01", None, "zigzag_events"),
    SignalMeta("DIV-02", None, "zigzag_events"),
    SignalMeta("DIV-03", None, "zigzag_events"),
    SignalMeta("DIV-05", None, "zigzag_events"),
)
