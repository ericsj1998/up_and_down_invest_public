"""체결 한 건과 봉 단위 델타 — **매수/매도를 나눠 센다**.

## 왜 필요한가 — 캔들은 방향을 지운다

캔들의 `volume` 은 매수·매도를 합친 총량이다. "이 봉에서 누가 밀었나"는 그 안에
없다. 방향은 **체결 단위**에만 있다 (업비트 `ask_bid` · Gate `side`).

## 🔴 과거는 못 받는다 — 그래서 지금부터 쌓는다

업비트 `/trades/ticks` 의 `to` 는 **`HH:mm:ss` 만** 받는다 (실측 — 날짜를 못 넘긴다).
그리고 한 요청 500건이 BTC 기준 **9분치**다. 과거 1년을 REST 로 파고드는 것은
불가능하다.

⇒ **WebSocket 으로 지금부터 받아 쌓는 수밖에 없다.** 오늘 안 켜면 오늘 체결은
영영 없다.

## 원시 틱을 저장하지 않는다

BTC 하나가 하루 8만 건, 6종이면 48만 건이다. 캔들(하루 576봉)과 차원이 다르다.
**봉 단위로 즉시 접어서 델타만 남긴다** — 하루 576행이 된다.

⚠️ 나중에 틱 단위로 다른 것을 재고 싶어지면 아쉽겠지만, 지금 목적(델타)에는 봉
집계로 충분하고 저장 비용이 세 자릿수 배 차이다.

## 이 값의 용도 — 근사식의 **정답지**, 그다음 **감시**

⭐ **최종적으로는 판단에 쓴다.** 다만 순서가 있다.

    1. 진짜 델타를 모은다                    <- 지금
    2. 캔들에서 그것을 맞히는 식을 뽑는다
    3. 식을 검증하고 **확정**한다
    4. 백테스트와 라이브가 **같은 식**으로 판단한다   <- 여기서 판단에 들어간다
    5. 진짜 델타는 계속 기록해 근사식과의 괴리를 감시한다

🔴 **3번 전에 진입 조건에 넣지 않는다.** 지금 넣고 백테스트를 돌리면, 실측으로 식을
고쳤을 때 그때까지의 측정이 전부 무효가 된다 — 델타를 모으는 목적 자체가 식을 고치는
것이므로 **곧 버릴 숫자를 만드는 셈**이다.

⚠️ **"라이브만 진짜 델타"는 안 된다.** 백테스트와 라이브가 다른 지표로 돌면 승률이
갈렸을 때 전략 탓인지 지표 차이인지 못 가른다 (원칙 P3 전략 코드 단일화).
진짜 델타는 **감시용**이고 판단은 양쪽 다 근사식으로 한다.

⭐ 대신 **캔들에서 델타를 추정하는 식을 여기서 역으로 뽑는다.** 표준 근사식
(`(종가-저가)/(고가-저가)`)은 근거가 약한 값인데, 실제 체결로 회귀하면 우리 종목·
우리 시간축에 맞는 식이 나온다 (절대 규칙 #12 — 권위가 아니라 성과).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Instrument, Timeframe


class TradeSide(StrEnum):
    """체결을 일으킨 쪽.

    Attributes:
        BUY: 매수가 호가를 때렸다 (업비트 `BID` · Gate `buy`).
        SELL: 매도가 호가를 때렸다 (업비트 `ASK` · Gate `sell`).

    Note:
        🔴 업비트의 `ask_bid` 는 **체결을 일으킨 주문의 종류**다. `BID` 면 매수 주문이
        매도 호가를 때린 것이라 **매수 우위**로 센다. 이름만 보고 `ASK`=매수로 뒤집으면
        델타의 부호가 통째로 반대가 되고, 그 오류는 값이 그럴듯해서 안 보인다.
    """

    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class TradeTick:
    """체결 한 건.

    Attributes:
        instrument: 대상 종목.
        ts: 체결 시각 (UTC aware).
        price: 체결가.
        volume: 체결량.
        side: 체결을 일으킨 쪽.

    Note:
        **저장하지 않는다.** 봉 단위로 접는 중간 표현이다 (모듈 docstring).
    """

    instrument: Instrument
    ts: datetime
    price: Decimal
    volume: Decimal
    side: TradeSide


@dataclass(frozen=True, slots=True)
class BarDelta:
    """봉 하나의 매수/매도 체결량.

    Attributes:
        instrument: 대상 종목.
        timeframe: 시간축.
        ts: **봉 시작 시각** (UTC aware). `Candle.ts` 와 같은 좌표계다.
        buy_volume: 매수가 때린 체결량 합.
        sell_volume: 매도가 때린 체결량 합.
        trades: 접은 체결 건수. **완결 판정의 근거**다.

    Note:
        🔴 `trades` 를 함께 드는 이유: WS 는 끊긴다. 재접속 사이의 체결이 빠지면 델타가
        **조용히 작아진다.** 건수를 같이 보면 "거래가 없었다"와 "우리가 못 받았다"를
        구분할 단서가 생긴다 (절대 규칙 #8).

        ⚠️ 그래도 완벽히는 못 가른다 — 그래서 이 값을 진입 조건이 아니라 근사식의
        정답지로만 쓴다.
    """

    instrument: Instrument
    timeframe: Timeframe
    ts: datetime
    buy_volume: Decimal
    sell_volume: Decimal
    trades: int

    @property
    def delta(self) -> Decimal:
        """매수 - 매도.

        Returns:
            양수면 매수 우위.
        """
        return self.buy_volume - self.sell_volume

    @property
    def total(self) -> Decimal:
        """매수 + 매도 — 캔들의 `volume` 과 대조할 값.

        Returns:
            총 체결량.

        Note:
            ⚠️ 캔들 `volume` 과 **정확히 같아야 정상**이다. 다르면 WS 가 놓친 체결이
            있다는 뜻이고, 그 봉의 델타는 믿을 수 없다. 근사식을 학습할 때 이 검사를
            통과한 봉만 쓴다.
        """
        return self.buy_volume + self.sell_volume

    @property
    def buy_ratio(self) -> Decimal | None:
        """매수 비중 — **근사식이 맞혀야 할 목표값**.

        Returns:
            `buy / (buy + sell)`. 체결이 없으면 None.

        Note:
            캔들에서 이 값을 추정하는 것이 근사식의 과제다. 표준 근사는
            `(close - low) / (high - low)` 인데, 그것이 실제로 이 값을 맞히는지는
            **재 봐야 안다.**

            ⛔ 0 으로 나누지 않는다. 체결이 없으면 "비중 0"이 아니라 **모른다**이며,
            0 으로 채우면 학습 데이터가 매도 쪽으로 치우친다.
        """
        total = self.total
        if total == 0:
            return None
        return self.buy_volume / total
