"""추세 전환 신호 — **연속 캔들 · 장악형** (playbooks.md 🆕 필요 플래그 5·6).

## 왜 필요한가

사용자가 실제 매매 순서를 이렇게 적었다:

```
② 하단 지지에서 매수
③ 박스 절반에서 반익절
④ 상단 저항에서 **추세 전환 확인**(3개 이어진 캔들 색 · 장악 캔들)으로 나머지 익절
⑥ 하단에서 **추세전환 + 변곡 확인** 후 매수
```

⇒ *"지지에 닿았다"* 로 사는 것이 아니라 **"닿고 나서 전환을 확인하면"** 산다. ④⑥이
그 확인이고, 지금까지 코드에 **없었다** — 지정가만 걸고 있었다.

## 표준 그대로다

| 표준 | 여기서 |
|---|---|
| **Engulfing** (장악형) | 몸통이 직전 봉 몸통을 완전히 감싼다 |
| **연속 같은 색** | N봉 연속 같은 방향 — 소진·전환의 고전 신호 |

⚠️ 델타 볼륨은 **아직 못 쓴다.** 근사식이 확정되기 전에 진입 조건에 넣으면, 식을
고쳤을 때 그때까지의 측정이 무효가 된다 (확정 8). 그것만 빠져 있다.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from updown.common.domain.candle import Candle

STREAK_BARS = 3
"""연속으로 볼 최소 봉 수 — 사용자 표현 *"3개 이어진 캔들 색"* 그대로다.

## ⛔ "첫 봉 무시 + 잔봉 묶기" 를 넣었다가 **되돌렸다** (2026-08-17)

한때 이렇게 셌다 — 양봉→음봉 전환 **첫 봉은 안 세고**, 직전과 별 차이 없는 봉
(`ATR x 0.25` 미만 이동 또는 몸통이 범위의 30% 미만)은 앞 틱에 묶었다. 근거는
*"되돌림 첫 음봉은 전환이 아니다"* 였고, 말로는 맞는 이야기였다.

**측정이 반대로 나왔다.** 세 구간 전부에서 손익이 매매 수보다 크게 줄었다:

```
             매매 수      손익
2025-07-16    -15%   ->   -41%
2024-03-01    -14%   ->   -49%
2022-10-05    -14%   ->   -32%
```

⇒ 걸러낸 매매가 **평균보다 좋은** 것들이었다. 건당 중앙값은 그대로여서 질은 안
올리고 양만 줄였다. 사용자도 하락장을 포함해 따로 재고 같은 결론을 냈다.

🔴 **말이 되는 것과 돈이 되는 것은 다르다.** §5.6.7 *"권위가 아니라 성과가
판정한다"* 가 정확히 이 경우다 — 그럴듯한 근거로 규칙을 넣었고, 그것이 수익을
만들던 신호를 껐다.

⚠️ 다시 넣고 싶어지면 **먼저 측정한다.** 위 표가 그때의 기준선이다.
"""


def is_bullish(candle: Candle) -> bool:
    """양봉인가.

    Args:
        candle: 대상 봉.

    Returns:
        종가가 시가보다 높으면 True.

    Note:
        ⚠️ 도지(종가 == 시가)는 **양봉이 아니다.** 방향이 없는 것을 한쪽으로 세면
        연속 판정이 그 봉에서 조용히 이어진다.
    """
    return candle.close > candle.open


def streak(candles: Sequence[Candle], *, bullish: bool, bars: int = STREAK_BARS) -> bool:
    """마지막 `bars` 봉이 **연속 같은 색**인가.

    Args:
        candles: `ts` 오름차순 캔들.
        bullish: 양봉 연속을 볼지.
        bars: 연속 봉 수.

    Returns:
        연속이면 True. 봉이 모자라면 False.

    Note:
        🔴 **한 방향 소진의 고전 신호다.** 하단에서 양봉 3연속이면 매수 전환의 확인이고,
        상단에서 음봉 3연속이면 익절 신호다.

        ⛔ 봉이 모자랄 때 True 를 내지 않는다 — "모른다"를 신호로 세면 워밍업 구간이
        전부 전환으로 잡힌다 (절대 규칙 #8).

        ⚠️ **첫 봉 무시·잔봉 묶기를 넣었다가 되돌렸다** — 근거와 실측은
        `STREAK_BARS` 에 남겼다. 다시 넣기 전에 그 표를 읽는다.
    """
    if bars < 1 or len(candles) < bars:
        return False
    return all(is_bullish(item) is bullish for item in candles[-bars:])


def engulfing(candles: Sequence[Candle], *, bullish: bool) -> bool:
    """마지막 봉이 **장악형**인가 (표준 Engulfing).

    Args:
        candles: `ts` 오름차순 캔들.
        bullish: 상승 장악을 볼지.

    Returns:
        장악형이면 True. 봉이 둘 미만이면 False.

    Note:
        ⭐ **표준 정의 그대로다** — 방향이 반대이고, 몸통이 직전 봉 몸통을 **완전히
        감싼다.**

        ```
        상승 장악   직전 음봉 · 지금 양봉 · 지금 몸통이 직전 몸통을 덮는다
        하락 장악   직전 양봉 · 지금 음봉 · 같은 조건
        ```

        ⚠️ **꼬리가 아니라 몸통으로 판정한다.** 꼬리까지 넣으면 변동성 큰 봉이 거의
        항상 장악형이 되어 신호가 아니라 잡음이 된다.

        ⛔ 몸통이 0 인 도지는 장악형이 아니다. 감싸는 것이 없다.
    """
    if len(candles) < 2:
        return False
    prior, last = candles[-2], candles[-1]
    if is_bullish(last) is not bullish or is_bullish(prior) is bullish:
        return False
    prior_high = max(prior.open, prior.close)
    prior_low = min(prior.open, prior.close)
    last_high = max(last.open, last.close)
    last_low = min(last.open, last.close)
    if last_high == last_low or prior_high == prior_low:
        return False
    return last_low <= prior_low and last_high >= prior_high


DOJI_BODY_RATIO = Decimal("0.2")
"""도지 — 몸통이 전체 범위의 20% 이하. 추세 소진·망설임의 표준 신호 (전환 탈출용)."""


def doji(candle: Candle) -> bool:
    """마지막 봉이 도지인가 — 몸통이 전체 범위의 20% 이하.

    Args:
        candle: 판정할 봉.

    Returns:
        도지면 True. 범위가 0 이면 False.
    """
    span = candle.high - candle.low
    if span <= 0:
        return False
    return abs(candle.close - candle.open) / span <= DOJI_BODY_RATIO


WICK_RATIO = Decimal("0.6")
PIN_WICK_RATIO = Decimal("0.5")
"""핀바 — 거부 꼬리가 범위의 절반 이상. 도지(0.6)보다 느슨: 핀바는 몸통이 있어도 된다."""
"""방향 도지 — 한쪽 꼬리가 전체 범위의 60% 이상. 스피닝(양쪽)·망치(몸통 큼)는 걸러진다."""


def gravestone(candle: Candle) -> bool:
    """긴 위꼬리 도지 (비석형) — 위를 찔렀다 거부. 상단이면 하락 전환(롱 청산) 신호.

    Args:
        candle: 판정할 봉.

    Returns:
        몸통 ≤ 범위 20% 이고 위꼬리 ≥ 범위 60% 면 True.
    """
    span = candle.high - candle.low
    if span <= 0:
        return False
    body = abs(candle.close - candle.open)
    upper = candle.high - max(candle.open, candle.close)
    return body / span <= DOJI_BODY_RATIO and upper / span >= WICK_RATIO


def dragonfly(candle: Candle) -> bool:
    """긴 아래꼬리 도지 (잠자리형) — 아래를 찔렀다 거부. 하단이면 상승 전환(숏 청산) 신호.

    Args:
        candle: 판정할 봉.

    Returns:
        몸통 ≤ 범위 20% 이고 아래꼬리 ≥ 범위 60% 면 True.
    """
    span = candle.high - candle.low
    if span <= 0:
        return False
    body = abs(candle.close - candle.open)
    lower = min(candle.open, candle.close) - candle.low
    return body / span <= DOJI_BODY_RATIO and lower / span >= WICK_RATIO


def pin_bar(candle: Candle, *, lower: bool) -> bool:
    """핀바(거부 캔들) — 지정한 쪽 꼬리가 몸통·반대 꼬리보다 크고 범위의 40% 이상.

    Args:
        candle: 판정할 봉.
        lower: 아래꼬리(지지 거부·롱)를 볼지 · 거짓이면 위꼬리(저항 거부·숏).

    Returns:
        핀바면 True. 지지에서 긴 아래꼬리 = 매수가 저점을 거부 = 반등 확인.
    """
    span = candle.high - candle.low
    if span <= 0:
        return False
    body = abs(candle.close - candle.open)
    upper = candle.high - max(candle.open, candle.close)
    low_wick = min(candle.open, candle.close) - candle.low
    wick = low_wick if lower else upper
    other = upper if lower else low_wick
    return wick >= body and wick >= other and wick / span >= PIN_WICK_RATIO


SPIN_BODY_RATIO = Decimal("0.3")
SPIN_WICK_RATIO = Decimal("0.25")
MARUBOZU_BODY_RATIO = Decimal("0.8")


def spinning(candle: Candle) -> bool:
    """양쪽 꼬리 스피닝(팽이) — 몸통이 작고 위·아래 꼬리가 둘 다 길다 = 우유부단.

    Args:
        candle: 판정할 봉.

    Returns:
        스피닝이면 True. 사용자 분류 ③ — 방향이 안 정해진 상태 = 횡보 시작 신호.
    """
    span = candle.high - candle.low
    if span <= 0:
        return False
    body = abs(candle.close - candle.open)
    upper = candle.high - max(candle.open, candle.close)
    lower = min(candle.open, candle.close) - candle.low
    return (
        body / span <= SPIN_BODY_RATIO
        and upper / span >= SPIN_WICK_RATIO
        and lower / span >= SPIN_WICK_RATIO
    )


def marubozu(candle: Candle, *, up: bool) -> bool:
    """무꼬리 장대봉 — 몸통이 범위의 80% 이상 = 한 방향으로 강한 의지 (사용자 분류 ④).

    Args:
        candle: 판정할 봉.
        up: 양봉(상승 장대봉)을 볼지 · 거짓이면 음봉(하락 장대봉).

    Returns:
        지정한 방향의 장대봉이면 True. 추세 지속 신호 = 그 반대로는 진입하지 않는다.
    """
    span = candle.high - candle.low
    if span <= 0:
        return False
    body = candle.close - candle.open
    if abs(body) / span < MARUBOZU_BODY_RATIO:
        return False
    return (body > 0) if up else (body < 0)


def reversal_confirmed(candles: Sequence[Candle], *, bullish: bool) -> bool:
    """전환이 확인됐는가 — **둘 중 하나면 인정**한다.

    Args:
        candles: `ts` 오름차순 캔들.
        bullish: 상승 전환을 볼지.

    Returns:
        3연속 **또는** 장악형이면 True.

    Note:
        🔴 **둘을 AND 로 묶지 않는다.** 장악형은 한 봉이고 연속은 세 봉이라 동시에
        성립하기 어렵다 — AND 로 두면 신호가 거의 안 난다.

        ⭐ **모의 라이브 청산(`session._turning`)이 이것을 쓴다.** 한때 장악형을 빼고
        `streak` 만 쓰다가 되돌렸다 — 사용자 설명이 *"장악캔들은 혼자로써 추세 전환으로
        치는 거"* 였고, 오더블록은 **레벨 원장**에서 제 일을 하므로 이것과 경쟁하지 않는다.

        ⚠️ 델타 볼륨이 빠져 있다 (확정 8: 근사식 확정 전에는 진입 조건에 안 넣는다).
        그것이 들어오면 여기에 OR 로 붙는다.
    """
    return streak(candles, bullish=bullish) or engulfing(candles, bullish=bullish)
