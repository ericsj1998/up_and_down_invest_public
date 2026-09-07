"""**청산 판정** — 발굴 파이프라인의 하드 제약 (T151 · Stage 0).

## 🔴 이것은 점수가 아니라 문이다

테스트 계획서 §0-2: 하드 제약은 **청산 회피**와 **파산 회피** 둘뿐이다. 나머지는 전부
목적함수(순손익·MDD) 안에서 판단한다.

    이 전략에 **청산 경로가 한 번이라도 존재하는가**

⛔ 존재하면 손익이 얼마든 그 전략은 **탈락**이다. 좋은 성적으로 상쇄되지 않는다 —
계좌가 사라지면 그 뒤의 성적이 없기 때문이다.

## ⚠️ "안 났다" 와 "안 난다" 는 다르다

2026-08-30 실측: 1분봉 45일에서 최대 역행이 **3.89%** 였고 20배 청산 거리는 4.5% 다.
청산 0건이지만 여유가 **0.6%p** 뿐이었다.

⇒ 그래서 이 모듈은 두 가지를 따로 답한다:

    ① 실제로 청산에 **닿았나**        — 백테스트 구간에서 일어난 일
    ② 여유가 **충분한가** (D-2)       — 다음 구간에도 안 날 것인가

②가 없으면 운 좋은 구간을 통과시킨다.

## 사용자 확정 (2026-08-30 · T150 D-2)

    청산가 최소 여유 배수 = **2배**

최대 예상 역행의 2배가 청산 거리 안에 들어와야 한다. 역행 4% 면 청산은 8% 밖이어야
하고, 그것은 **배율 12 이하**를 뜻한다.

⚠️ 사용자가 실제로 쓰던 **20배는 이 기준에서 탈락**이다 (청산 4.5% < 3.89% x 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from updown.decision.sizing import liquidation_distance

SAFETY = Decimal(2)
"""청산가 최소 여유 배수 (T150 D-2 · 사용자 확정 2026-08-30).

🔴 최대 역행 x 이 배수가 청산 거리 안에 들어와야 한다.

⚠️ **1배는 안 된다.** 최대 역행이 청산 거리와 같다는 것은 *"이번 구간에서는 딱 안
닿았다"* 는 뜻이고, 다음 구간에 조금만 더 나가면 계좌가 없다.
"""


@dataclass(frozen=True, slots=True)
class Verdict:
    """청산 판정 결과.

    Attributes:
        hit: 백테스트 구간에서 **실제로** 청산에 닿았나.
        worst_mae: 최대 역행폭(%).
        distance: 이 배율의 청산 거리(%).
        margin: 여유 배수 = 청산 거리 / 최대 역행. 무한이면 역행이 0 이었다.
        safe: 하드 제약을 **통과**했나 (`not hit` 이고 `margin >= SAFETY`).
    """

    hit: bool
    worst_mae: Decimal
    distance: Decimal
    margin: Decimal
    safe: bool

    def why(self) -> str:
        """왜 떨어졌나 — 통과했으면 빈 문자열.

        Returns:
            사람이 읽는 이유.

        Note:
            ⚠️ 이유를 **문자열로 남긴다.** 불리언만 돌려주면 리포트에 *"탈락"* 만
            찍히고, 청산에 닿아서인지 여유가 모자라서인지 구별이 안 된다 (규칙 #8).
        """
        if self.hit:
            return f"청산에 닿았다 (역행 {self.worst_mae:.2f}% ≥ 거리 {self.distance:.2f}%)"
        if not self.safe:
            return (
                f"여유 부족 — 역행 {self.worst_mae:.2f}% · 거리 {self.distance:.2f}%"
                f" · 배수 {self.margin:.2f} < {SAFETY}"
            )
        return ""


def judge(worst_mae_pct: Decimal, leverage: Decimal, *, safety: Decimal = SAFETY) -> Verdict:
    """최대 역행과 배율로 하드 제약을 판정한다.

    Args:
        worst_mae_pct: 백테스트 구간의 **최대 역행폭**(%). 매매 하나가 아니라
            전 매매 중 최악이다 — 한 번만 나면 계좌가 없기 때문이다.
        leverage: 배율.
        safety: 요구 여유 배수. 기본은 T150 D-2 의 2배.

    Returns:
        판정.

    Raises:
        ValueError: 배율이 0 이하이거나 역행이 음수인 경우.

    Note:
        🔴 **평균이 아니라 최악을 본다.** 평균 역행이 0.1% 여도 한 번의 5% 가 계좌를
        지운다. 하드 제약에서 평균은 뜻이 없다.

        ⚠️ `liquidation_distance` 를 **다시 만들지 않는다** — `decision/sizing.py` 의
        그것을 쓴다. 같은 값을 두 곳에서 계산하면 언젠가 갈리고, 갈린 쪽이 하필
        이 판정이면 청산 나는 전략을 통과시킨다.
    """
    if worst_mae_pct < 0:
        raise ValueError(f"역행폭은 음수일 수 없다: {worst_mae_pct}")
    distance = liquidation_distance(leverage) * 100
    hit = worst_mae_pct >= distance
    # ⚠️ 역행이 0 이면 나눌 수 없다 — 그때는 여유가 무한이다 (포지션이 한 번도
    #    불리하게 안 갔다는 뜻이고, 그것은 청산 위험이 없다는 뜻이다).
    margin = (distance / worst_mae_pct) if worst_mae_pct > 0 else Decimal("Infinity")
    return Verdict(
        hit=hit,
        worst_mae=worst_mae_pct,
        distance=distance,
        margin=margin,
        safe=not hit and margin >= safety,
    )


def max_leverage(worst_mae_pct: Decimal, *, safety: Decimal = SAFETY) -> Decimal:
    """이 역행을 견디려면 배율이 얼마 **이하**여야 하나.

    Args:
        worst_mae_pct: 최대 역행폭(%).
        safety: 요구 여유 배수.

    Returns:
        허용 최대 배율. 역행이 0 이면 제한이 없다는 뜻으로 큰 값을 낸다.

    Raises:
        ValueError: 역행폭이 음수다.

    Note:
        ⭐ **판정을 뒤집어 쓰는 함수다.** `judge` 는 *"이 배율이 되나"* 를 묻고
        이것은 *"몇 배까지 되나"* 를 묻는다. 후자가 있어야 사이징 레이어가
        배율을 **결과로** 뽑을 수 있다 (계획서 §5: *"레버리지는 사이징의 결과지
        입력값이 아니다"*).
    """
    if worst_mae_pct < 0:
        raise ValueError(f"역행폭은 음수일 수 없다: {worst_mae_pct}")
    if worst_mae_pct == 0:
        return Decimal(125)  # 거래소 상한. "제한 없음" 을 무한으로 내면 쓰는 쪽이 터진다
    from updown.decision.sizing import MAINTENANCE_MARGIN

    # 필요한 청산 거리 = 역행 x 안전배수
    need = worst_mae_pct * safety / 100
    # 1/lev - maintenance >= need  →  lev <= 1 / (need + maintenance)
    room = need + MAINTENANCE_MARGIN
    if room <= 0:
        return Decimal(125)
    # 🔴 **내림한다.** 반올림하면 상한을 넘고, 그 값을 그대로 쓰면 판정이 거절한다.
    #    함수가 내주는 값은 **그대로 써도 안전**해야 한다 — 쓰는 쪽이 어느 방향으로
    #    깎아야 하는지 알아야 하면 그것은 언젠가 틀린다 (시험에서 실제로 걸렸다:
    #    12.0773 → 반올림 12.08 → 여유 1.9995 로 탈락).
    return (Decimal(1) / room).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
