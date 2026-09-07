"""**음성 대조군** — 파이프라인이 잡음을 거르는지 증명한다 (T153 · 2026-08-30).

## 🔴 왜 신호가 아닌 것을 격자에 넣나

첫 스캔이 **Tier A 0개**를 냈다. 그런데 그 0 이 무엇을 뜻하는지 알 수 없었다:

    ① 이 신호들에 엣지가 없다        ← 그렇다면 정상 작동이다
    ② 파이프라인이 뭐든 다 떨어뜨린다  ← 그렇다면 도구가 고장난 것이다

둘을 가르는 방법은 **답을 아는 입력**을 넣어 보는 것뿐이다. 무작위 진입은 엣지가
**0 인 것이 확실**하므로:

    이것이 Tier A/B 로 뜬다   →  🔴 파이프라인이 고장났다. 표 전체를 못 믿는다
    항상 Tier C 로 떨어진다   →  ⭕ 잡음은 거른다. 그러면 0 은 ①의 뜻이다

⭐ 같은 수법을 이미 한 번 썼다. 2026-08-30 에 **신호 없이 동전만 던져 승률 92.7%** 를
만들어 봤고, 그때 합계가 -3,915% 인 것이 승률이라는 지표를 무너뜨렸다.

## ⚠️ 발생 빈도를 진짜 신호와 비슷하게 맞춘다

너무 드물면 표본 미달로 떨어지고, 그러면 *"파이프라인이 잘 걸렀다"* 가 아니라
*"표본이 없어서 떨어졌다"* 가 된다. 그것은 아무것도 증명하지 않는다.

## ⚠️ 이 신호도 **격자의 분모에 든다**

대조군이라고 FDR 계산에서 빼지 않는다. 빼면 그 순간 분모를 고르는 것이 되고,
그것이 바로 이 프로젝트가 막으려는 조작이다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = ["RandomEntry"]

FIRE_RATE = 0.02
"""봉당 발생 확률.

⭐ 진짜 신호들의 발생률과 같은 자리에 둔다 (15분봉 2년 실측: OSC-01 891건/70,080봉
= 1.3% · MA-04 21,282건 = 30%). 2% 면 흔한 쪽은 아니지만 표본 미달은 확실히 면한다.
"""

SEED = 20260830


@dataclass(frozen=True, slots=True)
class RandomEntry:
    """CTRL-01 — **무작위 진입**. 엣지가 0 인 것이 확실한 입력.

    Attributes:
        rate: 봉당 발생 확률.
        seed: 씨앗.

    Note:
        🔴 이 신호가 Tier A 나 B 로 뜨면 **표 전체를 버려야 한다.** 그것이 이
        신호의 유일한 용도다.

        ⭐ 난수를 `random` 으로 뽑지 않고 **해시**로 뽑는다. 전역 난수 상태는 호출
        순서에 의존하므로 신호 하나를 빼고 다시 돌리면 대조군까지 달라진다 —
        그러면 대조군이 대조군 노릇을 못 한다 (`fill._queue_missed` 와 같은 논거).

        ⚠️ 종목·축을 해시에 넣는다. 안 넣으면 8종이 **같은 봉 번호에서 동시에**
        발생해 하루에 8건이 몰리고, 날짜 블록 부트스트랩이 그것을 하나의 사건으로
        세어 신뢰구간이 부풀려진다.
    """

    rate: float = FIRE_RATE
    seed: int = SEED

    @property
    def name(self) -> str:
        """CTRL-01."""
        return "CTRL-01"

    def fire(self, board: Board) -> list[Trigger]:
        """무작위 봉에서 무작위 방향으로.

        Args:
            board: 봉과 지표. **아무것도 읽지 않는다** — 그것이 요점이다.

        Returns:
            방아쇠들.
        """
        stem = f"{self.seed}:{board.symbol}:{board.timeframe.value}"
        found: list[Trigger] = []
        for index in range(len(board.frame)):
            digest = hashlib.blake2b(f"{stem}:{index}".encode(), digest_size=8).digest()
            draw = int.from_bytes(digest, "big") / 2**64
            if draw < self.rate:
                # 방향도 같은 추첨에서 뽑되 다른 자리를 쓴다.
                up = int.from_bytes(digest[:2], "big") % 2 == 0
                found.append(
                    Trigger(index=index, direction=Direction.LONG if up else Direction.SHORT)
                )
        return found


register(RandomEntry())

declare(
    SignalMeta("CTRL-01", 0, "bar_close"),
)
