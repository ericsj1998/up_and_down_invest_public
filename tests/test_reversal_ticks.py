"""3틱 룰 — **"첫 봉 무시 + 잔봉 묶기" 는 되돌렸다** (2026-08-17).

이 파일은 그 결정을 **지키는** 검사다. 규칙 자체를 지우면 다음 세션이 같은 근거로
다시 넣기 쉬운데, 그때 잃는 것이 무엇인지 기록이 없으면 또 측정 없이 들어간다.

```
             매매 수      손익
2025-07-16    -15%   ->   -41%
2024-03-01    -14%   ->   -49%
2022-10-05    -14%   ->   -32%
```

걸러낸 매매가 **평균보다 좋은** 것들이었고, 건당 중앙값은 그대로여서 질은 안 올리고
양만 줄었다. 사용자도 하락장을 포함해 따로 재고 같은 결론을 냈다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.indicators import reversal
from updown.analysis.indicators.reversal import engulfing, reversal_confirmed, streak
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
START = datetime(2026, 8, 17, tzinfo=UTC)


def bar(index: int, open_: str, close: str, *, wick: str = "1") -> Candle:
    """시가·종가로 봉 하나. 꼬리는 몸통 밖으로 `wick` 만큼."""
    top = max(Decimal(open_), Decimal(close))
    bottom = min(Decimal(open_), Decimal(close))
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M15,
        ts=START + timedelta(minutes=15 * index),
        open=Decimal(open_),
        high=top + Decimal(wick),
        low=bottom - Decimal(wick),
        close=Decimal(close),
        volume=Decimal(10),
    )


class TestThreeInARow:
    """지금 규칙 — **마지막 3봉이 같은 색이면 끝**이다."""

    def test_three_bars_confirm(self) -> None:
        """앞에 반대색이 없어도, 전환 첫 봉을 빼지 않아도 성립한다."""
        rows = [bar(i, "120", "112") for i in range(3)]
        assert streak(rows, bullish=False)

    def test_transition_bar_counts(self) -> None:
        """🔴 되돌린 규칙의 핵심 — **전환 첫 음봉도 1로 센다**.

        `[음] 음 음` 이 곧 3연속이다. 이것을 빼는 규칙이 수익을 만들던 신호를 껐다.
        """
        rows = [bar(0, "100", "110"), bar(1, "110", "104"), bar(2, "104", "98"), bar(3, "98", "92")]
        assert streak(rows, bullish=False)

    def test_quiet_bars_still_count(self) -> None:
        """🔴 잔봉도 **각각** 센다 — 묶지 않는다."""
        rows = [bar(0, "104", "103.9"), bar(1, "103.9", "103.8"), bar(2, "103.8", "103.7")]
        assert streak(rows, bullish=False)

    def test_not_enough_bars_is_false(self) -> None:
        """⛔ "모른다"를 신호로 세지 않는다 (절대 규칙 #8)."""
        assert not streak([bar(0, "120", "112")], bullish=False)


class TestRevertedRuleIsGone:
    """⛔ 되돌린 규칙의 **잔재를 남기지 않는다**.

    남겨 두면 다음 세션이 *"이거 왜 안 불려?"* 하고 배선한다 — 이 저장소에서 실제로
    여러 번 일어난 사고다.
    """

    def test_tick_helpers_are_removed(self) -> None:
        assert not hasattr(reversal, "ticks")
        assert not hasattr(reversal, "is_quiet")
        assert not hasattr(reversal, "QUIET_ATR")
        assert not hasattr(reversal, "QUIET_BODY")

    def test_reason_is_recorded(self) -> None:
        """🔴 **왜 되돌렸는지가 코드에 남아 있어야 한다.**

        규칙만 지우면 근거가 사라지고, 그러면 같은 논리로 다시 들어온다.

        ⚠️ 상수 뒤의 문자열은 `__doc__` 으로 붙지 않으므로(정수의 docstring 이 잡힌다)
        **파일을 읽어** 검사한다.
        """
        from pathlib import Path

        source = Path("src/updown/analysis/indicators/reversal.py").read_text(encoding="utf-8")
        assert "되돌렸다" in source
        # 실측 표가 같이 있어야 "다시 넣기 전에 먼저 측정한다"가 가능하다
        assert "-41%" in source


class TestEngulfingIsSeparate:
    """장악캔들은 여전히 **혼자로** 전환이다 — 다만 청산은 이걸 안 쓴다."""

    def test_engulfing_alone_confirms(self) -> None:
        rows = [bar(0, "100", "110"), bar(1, "110", "120"), bar(2, "121", "99")]
        assert engulfing(rows, bullish=False)
        assert reversal_confirmed(rows, bullish=False)

    def test_walkforward_exit_keeps_engulfing(self) -> None:
        """🔴 **장악형이 청산 경로에 살아 있어야 한다** (사용자 확정 2026-08-17).

        > *"장악캔들은 혼자로써 추세 전환으로 치는 거니까."*

        ⛔ 한때 *"한 봉이 두 일을 하지 않는다"* 는 이유로 뺐다가 되돌렸다. 오더블록은
        **레벨 원장**에서 박스 변이 되는 것으로 제 일을 하며, 청산 신호와 경쟁하지
        않는다.
        """
        from pathlib import Path

        source = Path("src/updown/orchestration/walkforward/session.py").read_text(encoding="utf-8")
        assert "reversal_confirmed(" in source
