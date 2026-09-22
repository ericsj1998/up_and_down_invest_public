"""주식 **일봉** 돌파 후보 셋 (T294 · 2026-09-22) — 탐지기가 판정 축을 지키고 정의대로 내나.

    C1 `private_strategy`  룰 0.3 그대로 · `base_timeframe: 1d`
    C2 `private_strategy`          직전 55봉 최고가 위 마감 · 손절 2 ATR
    C3 `private_strategy`          직전 20봉 최고가 위 마감

가설(측정 §9): 주식 돌파의 고전은 일봉·주봉이다 — 1시간봉 돌파는 배당비가 무너진다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from test_private_strategy import INSTRUMENT, ROUND_TRIP, rising_window
from updown.analysis.detectors.private_strategy import RULE_ID_1D, private_strategy
from updown.analysis.detectors.donchian_break import RULE_ID_20, RULE_ID_55, donchian_setup
from updown.analysis.detectors.registry import discovered_detectors
from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.domain.setup import TradeSetup

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def day(i: int, close: Decimal, *, high: Decimal | None = None) -> Candle:
    return Candle(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        ts=T0 + timedelta(days=i),
        open=close - Decimal("0.5"),
        high=high if high is not None else close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal(100),
    )


def flat_then_break(n: int = 80, jump: Decimal = Decimal(5)) -> list[Candle]:
    """평평한 채널(고가 101) 뒤 마지막 봉이 위로 뛴다."""
    rows = [day(i, Decimal(100)) for i in range(n - 1)]
    rows.append(day(n - 1, Decimal(100) + jump))
    return rows


class TestRule03OnDaily:
    def test_the_daily_variant_fires_only_on_daily_windows(self) -> None:
        rows = [
            Candle(
                instrument=INSTRUMENT,
                timeframe=Timeframe.D1,
                ts=T0 + timedelta(days=i),
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for i, c in enumerate(rising_window())
        ]

        def run(window: list[Candle], tf: Timeframe, base: Timeframe) -> TradeSetup | None:
            return private_strategy(
                window,
                tf,
                ROUND_TRIP,
                bb_period=20,
                bb_k=Decimal(2),
                vol_period=20,
                vol_multiple=Decimal("2.0"),
                sl_atr=Decimal("0.2"),
                dir_period=20,
                dir_bars=5,
                base_timeframe=base,
            )

        assert run(rows, Timeframe.D1, Timeframe.D1) is not None, (
            "같은 룰이 일봉 창에서 셋업을 낸다"
        )
        assert run(rows, Timeframe.D1, Timeframe.H1) is None, "기본(1h) 탐지기는 일봉 창을 무시한다"
        assert run(rising_window(), Timeframe.H1, Timeframe.D1) is None, (
            "일봉 변형은 1h 창을 무시한다"
        )


class TestDonchian:
    def _setup(self, rows: list[Candle], bars: int, tf: Timeframe = Timeframe.D1):
        return donchian_setup(
            rows,
            tf,
            ROUND_TRIP,
            base_timeframe=Timeframe.D1,
            entry_bars=bars,
            sl_atr=Decimal("2.0"),
        )

    def test_close_above_the_prior_channel_high_is_a_long(self) -> None:
        made = self._setup(flat_then_break(), 55)
        assert made is not None
        assert made.avg_entry == Decimal(105)
        assert made.stop_loss < made.avg_entry
        # 손절 = 종가 - 2 x ATR14(직전 봉까지). 직전 봉들의 참범위는 2(고저) → ATR 2 → 손절 101.
        assert made.stop_loss == Decimal(105) - Decimal(4)

    def test_the_breakout_bar_is_not_in_its_own_channel(self) -> None:
        """돌파 봉 자신의 고가를 채널에 넣으면 종가가 그 고가를 못 넘어 셋업이 영영 안 난다."""
        rows = flat_then_break()
        rows[-1] = day(len(rows) - 1, Decimal(105), high=Decimal(120))
        assert self._setup(rows, 55) is not None

    def test_inside_the_channel_is_nothing(self) -> None:
        rows = [day(i, Decimal(100)) for i in range(80)]
        assert self._setup(rows, 55) is None
        assert self._setup(rows, 20) is None

    def test_a_recent_higher_high_blocks_the_55_but_not_a_shorter_channel(self) -> None:
        rows = flat_then_break(jump=Decimal(5))
        rows[30] = day(
            30, Decimal(100), high=Decimal(110)
        )  # 49봉 전 고가 110 — 55 채널 안 · 20 채널 밖
        assert self._setup(rows, 55) is None
        assert self._setup(rows, 20) is not None

    def test_other_timeframes_are_ignored(self) -> None:
        assert self._setup(flat_then_break(), 55, Timeframe.H1) is None

    def test_needs_warmup(self) -> None:
        assert self._setup(flat_then_break(n=30), 55) is None


class TestWiring:
    def test_rules_and_playbooks_are_declared(self) -> None:
        found = dict(discovered_detectors())
        assert {RULE_ID_1D, RULE_ID_55, RULE_ID_20} <= set(found)
        rules = load_rules()
        assert rules[RULE_ID_1D].params["base_timeframe"] == "1d"
        assert rules[RULE_ID_55].params["entry_bars"] == 55
        assert rules[RULE_ID_20].params["entry_bars"] == 20
        books = {b.playbook_id: b for b in load_playbooks()}
        for pid, rule in (
            ("private_strategy", RULE_ID_1D),
            ("private_strategy", RULE_ID_55),
            ("private_strategy", RULE_ID_20),
        ):
            book = books[pid]
            assert book.timeframe is Timeframe.D1
            assert book.setups == (rule,)
            assert book.listed is False, "측정 전에는 화면에 안 올린다"
            assert book.leverage == Decimal(1), "주식 현물 — 배율 1"
            assert book.ma_exit_below_long is not None, (
                "청산은 셋 다 이동평균 이탈 — 한 축만 다르다"
            )
