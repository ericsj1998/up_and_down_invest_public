"""보유 비용 — 펀딩은 **경계 통과 횟수**다 (T151 · 계획서 §0-2, §0-4).

`common/costs.MarketCosts.funding_cost_pct` 는 시간 비례로 세고, 그 독스트링이 스스로
근사라고 밝혀 두었다. 단타에서는 그 근사가 비용의 전부를 바꾼다 — 7시간 55분을 들고
있어도 경계를 안 지났으면 0원이고, 10분이라도 08:00 을 지났으면 한 번 낸다.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from updown.common.costs import load_cost_table
from updown.common.domain.instrument import Market
from updown.orchestration.discovery.costs import (
    Charges,
    Funding,
    FundingUnknownError,
    Overnight,
    load_funding,
)
from updown.orchestration.discovery.fill import Exit
from updown.orchestration.walkforward.ledger import Direction


def when(hour: int, minute: int = 0, day: int = 30) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


def schedule() -> Funding:
    """00 · 08 · 16 UTC 세 번, 요율은 전부 +0.01%."""
    return Funding(
        symbol="TEST_USDT",
        schedule={
            when(hour, day=day): Decimal("0.0001") for day in (30, 31) for hour in (0, 8, 16)
        },
    )


class TestFundingCountsBoundaries:
    def test_a_short_hold_that_misses_the_boundary_pays_nothing(self) -> None:
        """🔴 7시간 55분을 들고도 **0원**이다. 비례식은 여기서 없는 비용을 매긴다."""
        got = schedule().crossings(when(0, 1), when(7, 56))
        assert got == []

    def test_ten_minutes_across_the_boundary_pays_once(self) -> None:
        """🔴 그리고 10분이라도 경계를 지나면 **한 번 낸다**."""
        got = schedule().crossings(when(7, 55), when(8, 5))
        assert got == [when(8)]

    def test_entering_exactly_on_the_boundary_does_not_pay(self) -> None:
        """⚠️ 정산 시각에 들어간 것은 그 정산에 안 걸린다 — 포지션이 아직 없었다."""
        assert schedule().crossings(when(8), when(15)) == []

    def test_exiting_exactly_on_the_boundary_pays(self) -> None:
        """⚠️ 그 순간까지 들고 있었다."""
        assert schedule().crossings(when(1), when(8)) == [when(8)]

    def test_a_long_hold_pays_every_boundary(self) -> None:
        assert len(schedule().crossings(when(0, 1), when(23))) == 2

    def test_the_two_edges_do_not_double_count(self) -> None:
        """⚠️ 연속 매매에서 앞 매매의 청산과 뒤 매매의 진입이 같은 시각이면,
        양쪽 다 포함하는 규칙은 같은 정산을 **두 번** 센다.
        """
        first = schedule().crossings(when(1), when(8))
        second = schedule().crossings(when(8), when(15))
        assert set(first) & set(second) == set()


class TestFundingSign:
    def test_long_pays_a_positive_rate(self) -> None:
        got = schedule().cost_pct(when(7), when(9), Direction.LONG)
        assert got == Decimal("0.01")

    def test_short_receives_it(self) -> None:
        """⭐ 부호를 `Direction.sign` 으로 접는다 — 롱/숏 분기를 흩지 않는다."""
        got = schedule().cost_pct(when(7), when(9), Direction.SHORT)
        assert got == Decimal("-0.01")


class TestLoadingFunding:
    def test_a_missing_symbol_raises(self, tmp_path: Path) -> None:
        """🔴 조용히 0 으로 넘어가지 않는다 (절대 규칙 #8).

        펀딩 0 인 백테스트는 보유가 길수록 유리해지고, 그 오류는 라이브에서만 드러난다.
        """
        with pytest.raises(FundingUnknownError, match="펀딩 이력이 없다"):
            load_funding("BCH_USDT", root=tmp_path)

    def test_it_reads_the_real_shape(self, tmp_path: Path) -> None:
        (tmp_path / "X_USDT.json").write_text(
            json.dumps([[1643587200, "0.00008927"], [1643616000, "-0.00007369"]]),
            encoding="utf-8",
        )
        got = load_funding("X_USDT", root=tmp_path)
        assert len(got.schedule) == 2
        assert got.schedule[datetime.fromtimestamp(1643616000, tz=UTC)] == Decimal("-0.00007369")

    def test_broken_rows_raise(self, tmp_path: Path) -> None:
        (tmp_path / "X_USDT.json").write_text(json.dumps([[1643587200]]), encoding="utf-8")
        with pytest.raises(ValueError, match="한 줄"):
            load_funding("X_USDT", root=tmp_path)

    def test_the_shipped_history_loads(self) -> None:
        """실제 이력 파일이 이 형식인지 확인한다 — 형식이 바뀌면 여기서 걸린다."""
        try:
            got = load_funding("BTC_USDT")
        except FileNotFoundError:
            # 실측 이력(logs/funding · 비추적)은 연구 PC 에만 있다 — 공개본 트리에서는 건너뛴다.
            pytest.skip("logs/funding 실측 이력이 없는 트리")
        assert len(got.schedule) > 1000


class TestOvernightHasNoDefault:
    """🔴 계획서 §9 가 *"오버나이트 페널티의 구체 수치화 방식"* 을 미결로 남겼다."""

    def test_none_is_an_explicit_choice(self) -> None:
        model = Overnight.none()
        assert not model.active, "가산 없음을 골랐다는 사실이 결과에 남아야 한다"

    def test_a_multiple_below_one_raises(self) -> None:
        """⚠️ 야간이 **더 싸다**는 모델은 근거가 없다."""
        with pytest.raises(ValueError, match="배수는 1 이상"):
            Overnight(hours_utc=frozenset({18}), spread_multiple=0.5, tail_pct_per_day=0.0)

    def test_a_negative_tail_raises(self) -> None:
        with pytest.raises(ValueError, match="음수"):
            Overnight(hours_utc=frozenset({18}), spread_multiple=1.0, tail_pct_per_day=-1.0)

    def test_asian_dawn_is_utc_evening(self) -> None:
        """KST 02~07 = UTC 17~22."""
        model = Overnight(
            hours_utc=frozenset(range(17, 22)), spread_multiple=2.0, tail_pct_per_day=0.0
        )
        assert model.is_night(when(18))
        assert not model.is_night(when(9))

    def test_naive_time_raises(self) -> None:
        model = Overnight.none()
        with pytest.raises(ValueError, match="UTC aware"):
            model.is_night(datetime(2026, 8, 30, 18))


class TestCharges:
    def market(self) -> Charges:
        table = load_cost_table()
        return Charges(
            market=table.for_market(Market.BINANCE),
            funding=schedule(),
            overnight=Overnight.none(),
        )

    def test_the_target_leg_is_maker_and_the_stop_leg_is_taker(self) -> None:
        """🔴 결말이 비용을 바꾼다 — 익절은 걸어 둔 지정가, 손절은 발동 시 시장가다."""
        charges = self.market()
        won = charges.of(
            entry=when(9),
            exit_at=when(10),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        lost = charges.of(
            entry=when(9),
            exit_at=when(10),
            direction=Direction.LONG,
            outcome=Exit.STOP,
            entry_is_maker=True,
        )
        assert won.fee_pct < lost.fee_pct

    def test_it_does_not_double_count_slippage(self) -> None:
        """⚠️ `round_trip_by` 안에 이미 슬리피지 x2 가 들어 있다."""
        charges = self.market()
        got = charges.of(
            entry=when(9),
            exit_at=when(10),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        market = charges.market
        expected = market.round_trip_by(entry_is_maker=True, exit_is_maker=True) * 100
        assert got.fee_pct + got.slippage_pct == expected

    def test_night_widens_only_the_legs_that_are_at_night(self) -> None:
        table = load_cost_table()
        charges = Charges(
            market=table.for_market(Market.BINANCE),
            funding=None,
            overnight=Overnight(
                hours_utc=frozenset({18}), spread_multiple=3.0, tail_pct_per_day=0.0
            ),
        )
        day = charges.of(
            entry=when(9),
            exit_at=when(10),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        one_leg = charges.of(
            entry=when(17),
            exit_at=when(18),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        both = charges.of(
            entry=when(18),
            exit_at=when(18, 30),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        assert day.slippage_pct < one_leg.slippage_pct < both.slippage_pct

    def test_the_tail_penalty_is_per_day(self) -> None:
        table = load_cost_table()
        charges = Charges(
            market=table.for_market(Market.BINANCE),
            funding=None,
            overnight=Overnight(hours_utc=frozenset(), spread_multiple=1.0, tail_pct_per_day=0.12),
        )
        got = charges.of(
            entry=when(0),
            exit_at=when(12),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        assert got.tail_pct == Decimal("0.06")

    def test_it_records_whether_the_night_model_was_on(self) -> None:
        """⚠️ 가산 없이 잰 성적은 그 사실과 함께 읽어야 한다 (관측 규약 §1-0s)."""
        got = self.market().of(
            entry=when(9),
            exit_at=when(10),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        assert not got.has_night_model

    def test_overnight_is_flagged(self) -> None:
        """§0-4 가 남기라고 한 지표: 오버나이트 보유 **비율**."""
        charges = self.market()
        inside = charges.of(
            entry=when(9),
            exit_at=when(10),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        across = charges.of(
            entry=when(23),
            exit_at=when(1, day=31),
            direction=Direction.LONG,
            outcome=Exit.TARGET,
            entry_is_maker=True,
        )
        assert not inside.overnight
        assert across.overnight
        assert across.crossings == 1

    def test_backwards_time_raises(self) -> None:
        with pytest.raises(ValueError, match="청산이 진입보다 앞선다"):
            self.market().of(
                entry=when(10),
                exit_at=when(9),
                direction=Direction.LONG,
                outcome=Exit.TARGET,
                entry_is_maker=True,
            )

    def test_the_breakdown_adds_up(self) -> None:
        """🔴 합계만 남기면 *"오버나이트가 손익에 기여하는가"* 를 못 묻는다 (§0-4)."""
        got = self.market().of(
            entry=when(7),
            exit_at=when(9),
            direction=Direction.LONG,
            outcome=Exit.STOP,
            entry_is_maker=False,
        )
        assert got.total_pct == got.fee_pct + got.slippage_pct + got.funding_pct + got.tail_pct
        assert got.crossings == 1
