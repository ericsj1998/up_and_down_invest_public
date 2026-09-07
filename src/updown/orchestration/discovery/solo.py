"""**동시 포지션 1개** — 격자 전체에 거는 제약 (오더 1-D · 사용자 확정).

## 왜 격자에 걸어야 하나

T156 Tier A 는 포지션이 **38배까지** 겹쳐 있었다. 겹치는 매매의 평균은 예측력의
통계이지 **굴릴 수 있는 자산 곡선이 아니다**.

후보 10칸을 실제로 1개 제약으로 다시 재 보니 **전부 Net 음수**였고, 그중 일곱은
무작위 진입보다도 나빴다. 즉 제약을 안 걸면 판정이 통째로 다르다.

## ⭐ 관측 없이 **방아쇠 시각만으로** 정할 수 있다

지평 청산은 청산 시각이 `진입 + 지평` 으로 **고정**이다. 그러면 어느 자리를 잡을지는
가격과 무관하고 **시각만의 문제**다:

    방아쇠들을 시각순으로 놓고, 자리가 비었으면 잡고, 아니면 버린다.

⇒ 무거운 관측 계산을 하기 **전에** 정할 수 있다. 캐시된 방아쇠(`cache.Fired`)만
읽으면 되고 프레임을 다시 접을 필요가 없다.

## 🔴 겹침 해소 규칙 — **선입 우선** (미리 고정한다)

    먼저 열린 자리가 이긴다. 같은 시각이면 **종목 이름 오름차순**.

신호 강도 우선을 안 쓰는 이유: 지금 신호들은 강도를 안 낸다. 없는 값을 만들어
순위를 매기면 그 순위가 곧 새 자유도다 (오더 §1-D 가 *"하나를 사전에 고정"* 요구).

## ⚠️ 결과는 **비트맵**이다

종목마다 *"이 방아쇠를 잡았나"* 를 `bytearray` 로 남긴다. 5분봉 격자에서 방아쇠가
1,500만 개인데, 집합으로 들면 수백 MB 이고 비트맵이면 2MB 다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from updown.orchestration.discovery.cache import Fired

__all__ = ["Accepted", "serialize"]

MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class Accepted:
    """어느 방아쇠를 실제로 잡았나.

    Attributes:
        taken: 종목 → 비트맵. `taken[symbol][i]` 가 1 이면 그 종목의 `i` 번째
            방아쇠를 잡았다 (그 종목 방아쇠 목록에서의 순번).
        kept: 잡은 수.
        dropped: 겹쳐서 버린 수.

    Note:
        ⭐ 순번은 **그 종목의 방아쇠 목록 안에서의 위치**다. 봉 번호가 아니다 —
        관측 루프가 같은 목록을 같은 순서로 돌기 때문에 이 편이 싸고 헷갈릴 일이 없다.
    """

    taken: Mapping[str, bytearray]
    kept: int
    dropped: int

    def has(self, symbol: str, position: int) -> bool:
        """그 종목의 `position` 번째 방아쇠를 잡았나.

        Args:
            symbol: 종목.
            position: 방아쇠 번호.

        Returns:
            잡았으면 True. 기록이 없거나 범위 밖이면 False.
        """
        bits = self.taken.get(symbol)
        if bits is None or position >= len(bits):
            return False
        return bool(bits[position])


def serialize(
    streams: Mapping[str, Sequence[Fired]],
    *,
    horizon_minutes: int,
    entry_lag_ms: int,
) -> Accepted:
    """여러 종목의 방아쇠를 합쳐 **한 번에 하나만** 잡는다.

    Args:
        streams: 종목 → 방아쇠들 (봉 번호 오름차순).
        horizon_minutes: 지평(분). 청산 시각을 정한다.
        entry_lag_ms: 방아쇠에서 진입까지의 간격(ms) — 다음 봉 시가이므로 한 봉이다.

    Returns:
        어느 방아쇠를 잡았는지.

    Raises:
        ValueError: 지평이 0 이하인 경우 — 그러면 자리가 안 비어 무한히 잡는다.

    Note:
        🔴 **진입 시각으로 정렬한다** (방아쇠 시각이 아니라). 실제로 자리를 차지하는
        것은 진입이고, 지평 청산은 `진입 + 지평` 에 끝난다.

        ⚠️ 같은 시각이면 **종목 이름 오름차순**으로 가른다. 딕셔너리 순서에 맡기면
        같은 입력에서 다른 답이 나올 수 있고, 그것은 절대 규칙 #5 위반이다.
    """
    if horizon_minutes <= 0:
        raise ValueError(f"지평은 양수여야 한다: {horizon_minutes}")

    span = horizon_minutes * MINUTE_MS
    rows: list[tuple[int, str, int]] = []
    for symbol in sorted(streams):
        for position, one in enumerate(streams[symbol]):
            rows.append((one.ts + entry_lag_ms, symbol, position))
    rows.sort()

    taken = {symbol: bytearray(len(streams[symbol])) for symbol in streams}
    kept = 0
    free_at = -1
    for opened, symbol, position in rows:
        if opened < free_at:
            continue
        taken[symbol][position] = 1
        kept += 1
        free_at = opened + span
    return Accepted(taken=taken, kept=kept, dropped=len(rows) - kept)
