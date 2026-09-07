"""그 시점 **이후** 실제로 어떻게 됐나 — 봉인된 절반.

## 🔴 왜 봉인하는가

결과를 알고 그은 추세선은 항상 잘 맞는다. 이것은 정직함의 문제가 아니라 **인지의**
문제라 의지로 못 이긴다 — 이후 전개를 본 사람은 그것을 안 본 사람이 될 수 없다.

그래서 기본 흐름은 **이의제기를 먼저 내고 그다음 결과를 본다**.

## ⚠️ 그런데 하드 블록을 걸지는 않았다

막으면 우회한다(다른 창에서 차트를 보거나 시점을 메모해 두거나). 그리고 **우회한
기록은 남지 않는다.** 남은 기록이 거짓인 것보다, 보되 본 사실이 남는 편이 낫다.

    이의제기 후 공개  → 깨끗한 이의제기
    먼저 공개         → 그 시점의 이후 이의제기에 `saw_outcome=True` 가 붙는다

## ⛔ 여기 수익률은 전략 성과가 아니다

`hold_pnl` 은 **그 시점에 사서 창 끝까지 들고 있었으면** 이다. 진입·손절·익절이 없는
숫자이고, 이것을 성과로 인용하면 §1-0s 가 경고한 그 실수다 — 코인 +50~98% 가 전략
성과가 아니라 대칭 동전 던지기였던 사건.

무엇을 재는 값인지 이름과 문서에 박아 두고, 화면에도 그대로 적는다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from updown.common.numeric import fixed_context
from updown.common.wire import candle_json

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.candle import Candle


@dataclass(frozen=True, slots=True)
class Outcome:
    """시점 이후의 전개.

    Attributes:
        timeframe: 시간축.
        candles: 시점 **이후** 봉들 (앞쪽 창과 이어 붙이면 전체가 된다).
        entry: 시점 직후 첫 봉의 시가 — 보유 손익의 기준가.
        last: 창 끝 종가.
        high: 창 안 최고가.
        low: 창 안 최저가.
        hold_pnl: 금액 기준 보유 손익. ⛔ **전략 성과가 아니다.**
        hold_pct: 같은 것의 비율.
        max_gain_pct: 창 안에서 가장 유리했던 지점 (MFE 에 해당).
        max_drop_pct: 창 안에서 가장 불리했던 지점 (MAE 에 해당).
    """

    timeframe: str
    candles: tuple["Candle", ...]
    entry: Decimal
    last: Decimal
    high: Decimal
    low: Decimal
    hold_pnl: Decimal
    hold_pct: Decimal
    max_gain_pct: Decimal
    max_drop_pct: Decimal

    def to_dict(self) -> dict[str, Any]:
        """JSON 으로 — 가격은 문자열.

        Returns:
            시간축·봉 수·진입가·마지막가·고저·결말.
        """
        return {
            "timeframe": self.timeframe,
            "bars": len(self.candles),
            "entry": str(self.entry),
            "last": str(self.last),
            "high": str(self.high),
            "low": str(self.low),
            "hold_pnl": str(self.hold_pnl),
            "hold_pct": str(self.hold_pct),
            "max_gain_pct": str(self.max_gain_pct),
            "max_drop_pct": str(self.max_drop_pct),
            # ⭐ 스냅샷과 **같은 함수**가 짓는다. 전에는 *"스냅샷과 같은 키 규약"* 이라고
            #    주석으로 약속했는데, 약속은 코드가 아니라 사람이 지켜야 하는 것이었다.
            "candles": [candle_json(candle) for candle in self.candles],
        }


def forward(
    candles: "Sequence[Candle]",
    timeframe: str,
    as_of: datetime,
    amount: Decimal,
) -> Outcome | None:
    """시점 이후 봉들로 보유 손익과 최대 유·불리를 낸다.

    Args:
        candles: 시점 **이후**의 봉들 (`ts > as_of`). 호출부가 걸러 넘긴다.
        timeframe: 시간축 문자열.
        as_of: 점검 시점. 기록용이다.
        amount: 투자 금액.

    Returns:
        전개. 봉이 없으면 None — 시점이 데이터 끝에 붙어 있으면 정상적으로 일어난다.

    Note:
        기준가를 **첫 봉의 시가**로 잡는다. 시점의 종가로 잡으면 그 종가는 이미 알던
        값이라 "그 시점에 샀다"가 아니라 "그 시점을 알고 샀다"가 된다.

        ⛔ 비용(수수료·슬리피지)을 빼지 않았다. 보유 손익은 진입·청산이 한 번뿐이라
        비용 영향이 작고, 무엇보다 **이 값을 성과로 쓰지 않기 때문**이다. 전략 성과는
        백테스트가 `common/costs.py` 로 낸다.
    """
    if not candles:
        return None
    _ = as_of
    entry = candles[0].open
    if entry <= 0:
        return None
    last = candles[-1].close
    high = max(candle.high for candle in candles)
    low = min(candle.low for candle in candles)
    with fixed_context():
        units = amount / entry
        return Outcome(
            timeframe=timeframe,
            candles=tuple(candles),
            entry=entry,
            last=last,
            high=high,
            low=low,
            hold_pnl=(last - entry) * units,
            hold_pct=(last - entry) / entry * 100,
            max_gain_pct=(high - entry) / entry * 100,
            max_drop_pct=(low - entry) / entry * 100,
        )
