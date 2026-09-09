"""리스크 기반 사이징 — **배율은 입력이 아니라 출력이다** (T49 · 사용자 목표 ②③).

```
건당 리스크  r  (자본 대비)
손절 거리    s  = |진입 - 손절| / 진입
명목/자본    = r / s            ← 이것이 배율이다
```

사용자 2026-08-22: *"확실한 자리에 기계적으로 들어간다면 래버리지는 같은 리스크로 배율을
극대화할 수 있는 방법이야."* — 맞다. 단, 배율을 **고정**하면 그 문장이 거짓이 된다:
손절이 0.35% 인 자리에 20배 고정은 리스크 7% 이고, 같은 20배가 손절 1% 면 20% 다.
확실한 자리 = 좁은 손절 = **저절로** 높은 배율. 그래서 배율은 r 과 s 에서 도출한다.

## 청산 검증

격리 마진의 청산 거리는 `1/L - 유지증거금` 이다. 손절이 그 밖에 있으면 손절은 장식이고
청산이 먼저 온다 — 그런 자리는 **안 간다.** T24 실측: 20배 청산 거리 4.58%, 125배 0.3%.

## 왜 `decision/` 인가

수량·배율은 손절·익절과 같이 **RiskManager 의 SSoT** 다 (절대 규칙 #4). 세션·러너는 이
값을 받아 쓸 뿐 계산하지 않는다 — 한때 세션이 `증거금 x 배율` 로 직접 냈고, 그래서 배율이
손절 거리와 무관한 고정값이었다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

MAINTENANCE_MARGIN = Decimal("0.005")
"""유지증거금률 — 청산가 계산에 쓴다 (0.5%).

⭐ Gate.io 무기한 선물 BTC 하위 티어의 표준값대다. 원장(`ledger.liquidation_price`)이 같은
값을 **여기서** 가져다 쓴다 — 두 곳에 적으면 한쪽만 바뀐다.
"""

DEFAULT_LEVERAGE_CAP = Decimal(20)
"""도출된 배율의 상한 — 설정(`config/risk.yml` `max_leverage`)이 없을 때의 값.

🔴 T24 실측: 연속 손절 21회(-5.44% 가격 변동)에 20배는 -108.8%(전멸). 이 상한은 "여기까지는
안전"이 아니라 **"이 위는 산수로 이미 죽는다"** 는 선이다. r 이 작으면 배율은 저절로 낮다.
"""


@dataclass(frozen=True, slots=True)
class Sizing:
    """한 자리의 크기 결정.

    Attributes:
        exposure: **명목 / 자본** = r / s 를 상한으로 자른 값. 1 미만이면 자본 일부만 쓴다.
        stop_pct: 손절 거리 (진입 대비 비율).
        liquidation_room: 청산까지 거리 (진입 대비 비율). 배율 1 이하면 None (청산 없음).
        safe: 손절이 청산보다 **안쪽**인가. 거짓이면 이 자리는 가지 않는다.
        capped: 상한에 걸려 r 보다 작은 리스크로 들어가는가 (좁은 손절인데 배율을 못 올림).
    """

    exposure: Decimal
    stop_pct: Decimal
    liquidation_room: Decimal | None
    safe: bool
    capped: bool

    @property
    def risk_taken(self) -> Decimal:
        """실제로 거는 리스크 (자본 대비) = exposure x s. 상한에 안 걸리면 r 과 같다."""
        return self.exposure * self.stop_pct


def liquidation_distance(leverage: Decimal, maintenance: Decimal = MAINTENANCE_MARGIN) -> Decimal:
    """격리 마진 청산 거리 (진입가 대비 비율).

    Args:
        leverage: 배율.
        maintenance: 유지증거금률. 기본은 모듈 상수 `MAINTENANCE_MARGIN`.

    Returns:
        `1/배율 - 유지증거금률`, 0 아래로는 내려가지 않는다. 3x → 32.8% · 6x → 16.2%.

    Raises:
        ValueError: 배율이 양수가 아닌 경우.
    """
    if leverage <= 0:
        raise ValueError(f"배율은 양수다: {leverage}")
    return max(Decimal(1) / leverage - maintenance, Decimal(0))


def protect_stop(
    *,
    entry: Decimal,
    stop: Decimal,
    leverage: Decimal,
    ratio: Decimal,
    short: bool,
    maintenance: Decimal = MAINTENANCE_MARGIN,
) -> Decimal:
    """보호 손절 자리 — `stop_mode: close` 매매법의 라이브가 거래소에 거는 조건부 (T233 ②).

    Args:
        entry: 진입가.
        stop: 원장의 계획 손절 (마감 몸통으로 판정하는 정상 손절).
        leverage: 이 판의 배율.
        ratio: 청산거리 대비 보호 자리 비율 (`stop_protect_ratio` · β 보다 커야 한다).
        short: 숏인가.
        maintenance: 유지증거금률.

    Returns:
        보호 손절가. 계획 손절이 이미 그보다 멀면 **계획 손절 그대로** — 조이는 쪽으로는 안 간다.

    Raises:
        ValueError: 진입가나 배율이 양수가 아닌 경우.

    Note:
        정상 손절은 세션이 봉 마감 몸통으로 판정해 시장가로 나간다(백테스트와 같은 규칙). 이 자리는
        **봉이 마감되기 전에 청산가까지 밀리는 사고**만 막는다 — β 자리에 걸면 β 에 걸린 손절이
        터치로 나가 close 가 아니게 되므로 정책이 `ratio > β` 를 강제한다.
    """
    if entry <= 0 or leverage <= 0:
        raise ValueError(f"진입가·배율은 양수여야 한다 — entry={entry} leverage={leverage}")
    room = liquidation_distance(leverage, maintenance)
    if room <= 0:
        return stop
    limit = entry * (Decimal(1) + ratio * room) if short else entry * (Decimal(1) - ratio * room)
    return max(stop, limit) if short else min(stop, limit)


def capped_stop(
    *,
    entry: Decimal,
    stop: Decimal,
    leverage: Decimal,
    ratio: Decimal,
    short: bool,
    maintenance: Decimal = MAINTENANCE_MARGIN,
) -> Decimal:
    """제안 손절을 **청산 안쪽으로 당긴다** — 조이는 방향만 (T120~T146).

    Args:
        entry: 계획 진입가.
        stop: 분석이 제안한 손절가.
        leverage: 이 판의 배율.
        ratio: 청산거리 대비 손절 상한 비율 (β). 0.40 이면 청산거리의 40% 지점.
        short: 숏인가.
        maintenance: 유지증거금률.

    Returns:
        확정 손절가. 제안이 이미 상한 안쪽이면 **제안 그대로**다.

    Raises:
        ValueError: 진입가나 배율이 양수가 아닌 경우.

    Note:
        🔴 **왜 필요한가** (T120 진단): 청산난 판의 **81~84%** 가 *"손절이 청산보다
        바깥"* 인 판이었다. 손절거리가 t=+13~16 으로 청산을 예측했고 ADX 도 보유기간도
        아니었다. 6x 면 청산이 16.2% 인데 SMA200 트레일이 20% 밖에 서면 손절은
        **장식**이고 청산이 먼저 온다.

        🔴 **왜 자유 %가 아니라 청산거리의 비율인가**: `max_stop_pct = 8%` 같은 자유
        상수를 두면 그것이 셋업별 튜닝 통로가 된다 (정책이 `atr_stop_k` 를 룰 설정에서
        막는 이유와 같다 · §6.1). 청산거리에 묶으면 다이얼이 하나이고 배율에서 자동으로
        따라온다 — **조작할 자리가 없다.**

        ⚠️ **넓히지 않는다** (절대 규칙 #3). 제안이 이미 가까우면 손대지 않는다.

        ⭐ β 는 **경로가 골랐다**: 6x 에서 4h 격자로는 β 넷이 다 청산 0 이었는데
        15m 경로로 풀면 **0.40 만** 청산 0 을 지켰다 (T146). 손절이 청산에서 멀수록
        봉 안에서 청산이 먼저 닿을 여지가 커진다.
    """
    if entry <= 0:
        raise ValueError(f"진입가는 양수다: {entry}")
    if ratio <= 0:
        return stop
    room = liquidation_distance(leverage, maintenance)
    if room <= 0:
        raise ValueError(f"배율 {leverage} 에서는 청산 여유가 없다")
    limit = entry * (Decimal(1) + ratio * room) if short else entry * (Decimal(1) - ratio * room)
    # 진입에서 더 가까운 쪽을 남긴다 (조이는 방향만)
    if short:
        return min(stop, limit)
    return max(stop, limit)


def size_for(
    *,
    risk_pct: Decimal,
    entry: Decimal,
    stop: Decimal,
    leverage_cap: Decimal = DEFAULT_LEVERAGE_CAP,
    maintenance: Decimal = MAINTENANCE_MARGIN,
) -> Sizing:
    """R 과 손절 거리에서 배율을 **도출**한다.

    Args:
        risk_pct: 건당 리스크 (자본 대비 · 0.01 = 1%).
        entry: 계획 진입가 (분할이면 **계획 평단** · spec §4.6).
        stop: 손절가.
        leverage_cap: 배율 상한.
        maintenance: 유지증거금률.

    Returns:
        크기 결정.

    Raises:
        ValueError: 손절이 진입과 같거나 r 이 0 이하인 경우 — 분모가 없다.

    Note:
        🪞 거울상은 공짜다 — `|진입 - 손절|` 이라 롱·숏이 같은 식이다.
        ⚠️ 청산 검증은 **진입 수량 기준**(remaining=1)이다 — 반익 뒤에는 청산이 더 멀어지므로
        진입 시점 검사가 가장 엄하다.
    """
    if risk_pct <= 0:
        raise ValueError(f"건당 리스크는 양수다: {risk_pct}")
    if entry <= 0:
        raise ValueError(f"진입가는 양수다: {entry}")
    stop_pct = abs(entry - stop) / entry
    if stop_pct <= 0:
        raise ValueError("손절이 진입과 같다 — 리스크 분모가 없다")
    wanted = risk_pct / stop_pct
    exposure = min(wanted, leverage_cap)
    liquidation_room: Decimal | None = None
    safe = True
    if exposure > 1:
        room = max(Decimal(1) / exposure - maintenance, Decimal(0))
        liquidation_room = room
        safe = stop_pct < room
    return Sizing(
        exposure=exposure,
        stop_pct=stop_pct,
        liquidation_room=liquidation_room,
        safe=safe,
        capped=wanted > leverage_cap,
    )
