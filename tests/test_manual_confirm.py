"""손으로 그은 계획의 **확정** — 차트 주문이 돈으로 나가는 문 (2026-08-30).

## 이 파일이 지키는 것

사용자 확정: *"그냥 사람이 정하는대로 다 들어가는 거야. … 해당 RUN의 주체는 그냥
사용자인거야."*

그래서 시험의 절반은 **안 막는 것**을 지킨다 — RR 이 낮아도, 필요 승률이 60% 여도
사람이 가겠다면 간다. 확정기가 "좋은 매매"를 강요하기 시작하면 그 순간 판의 주체가
사람이 아니게 되고, 이 기능이 있을 이유가 사라진다.

나머지 절반은 **막아야만 하는 것**을 지킨다. 셋의 공통점은 *"판단이 아니라 사실"* 이다:

  ① 필요 승률 100% 초과 — **산수**다. 실력으로 못 넘는다
  ② 손절이 청산 밖   — 그 손절은 **체결되지 않는다** (T120: 청산난 판의 81~84%)
  ③ 기하가 뒤집힘    — 이익이 **날 수 없다** (2026-08-18: 4건 -4.48%)

🪞 롱·숏을 나란히 둔다. 부호 하나로 갈리는 것을 한쪽만 시험하면 반대쪽이 조용히 썩는다.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from updown.decision.risk.manual import Confirmed, confirm
from updown.decision.risk.policy import RiskSettings, load_settings

COST = Decimal("0.00157")
"""UPBIT 왕복 실측 0.157% — 가정이 아니라 측정값이다 (`scripts/runtime/measure_costs.py`)."""


def settings(beta: str | None = "0.40") -> RiskSettings:
    """**실제 설정**에서 β 만 갈아 끼운다.

    ⚠️ 빈 `RiskSettings` 를 손으로 만들지 않는다 — 앵커 검사(§4.6)에 걸리기도 하지만,
    더 큰 이유는 `leverage_needing_stop_cap` 같은 **안전 문턱이 설정에 있기 때문**이다.
    시험이 자기 설정을 지어내면 설정을 바꿨을 때 시험이 안 따라오고, 그러면 이 시험은
    "지금 도는 것"이 아니라 "예전에 돌던 것"을 지킨다.
    """
    return replace(
        load_settings(),
        stop_liquidation_cap_ratio=None if beta is None else Decimal(beta),
    )


def plan(
    *,
    entry: Decimal = Decimal(100),
    stop: Decimal = Decimal(98),
    first: Decimal = Decimal(104),
    target: Decimal = Decimal(110),
    leverage: Decimal = Decimal(3),
    short: bool = False,
    round_trip: Decimal = COST,
    risk: RiskSettings | None = None,
) -> Confirmed:
    """롱 기본 계획을 확정한다 — 진입 100 · 손절 98 · 1차 104 · 최종 110 · 3배.

    Args:
        entry: 진입가.
        stop: 손절가.
        first: 1차 익절가.
        target: 최종 익절가.
        leverage: 배율.
        short: 숏인가.
        round_trip: 왕복 비용.
        risk: 리스크 설정. 안 주면 β0.40.

    Returns:
        확정 결과.

    Note:
        ⚠️ 사전을 만들어 `**` 로 펼치지 않는다 — 그러면 타입 검사기가 인자를 못 보고,
        인자 이름을 바꿔도 시험이 조용히 통과한다.
    """
    return confirm(
        entry=entry,
        stop=stop,
        first=first,
        target=target,
        leverage=leverage,
        short=short,
        round_trip=round_trip,
        settings=settings() if risk is None else risk,
    )


class TestPassesTheHumanValuesThrough:
    """🔴 **사람이 낸 값이 그대로 나간다** — 이 판의 주체는 사람이다."""

    def test_clean_plan_is_untouched(self) -> None:
        got = plan()
        assert got.ok
        assert got.stop == Decimal(98), "성립하는 계획의 손절은 손대지 않는다"
        assert not got.moved

    def test_low_rr_is_allowed(self) -> None:
        """RR 이 낮아도 **막지 않는다** — 그것은 판단이고 판단은 사람 것이다."""
        got = plan(first=Decimal("100.8"))
        assert got.ok, f"막으면 안 된다: {got.blocked}"
        assert got.rr < 1
        assert any("필요 승률" in item for item in got.warnings), "다만 **말은 해야** 한다"

    def test_wide_stop_within_liquidation_is_kept(self) -> None:
        """청산 안쪽이면 넓은 손절도 그대로 — β 는 상한이지 목표가 아니다."""
        got = plan(stop=Decimal(95), leverage=Decimal(2))
        assert got.ok
        assert got.stop == Decimal(95)


class TestBlocksWhatIsNotJudgementButFact:
    """⛔ 막는 셋 — 전부 *"판단이 아니라 사실"* 이다."""

    def test_required_win_rate_over_100_is_blocked(self) -> None:
        """5m 단독 진입을 폐기한 것이 정확히 이 계산이다 (필요 승률 100.7~100.9%).

        🔴 **5m 이 죽은 모양 그대로** 만든다: 손절이 좁아 비용이 R 을 통째로 먹는다.
        손절 0.2% · 1차 익절 0.2% → RR 1 · 비용 0.157%/0.2% = 0.785 R
        → (1+0.785)/(1+1) = **89%**… 아직 모자라니 RR 을 더 낮춘다.
        """
        got = plan(stop=Decimal("99.8"), first=Decimal("100.02"), target=Decimal("100.1"))
        assert not got.ok, f"필요 승률 {got.need_pct:.1f}%"
        assert any("산술적으로 이길 수 없다" in item for item in got.blocked)

    def test_target_below_entry_is_blocked_for_long(self) -> None:
        """2026-08-18: 롱인데 1차 익절이 진입 아래인 계획 4건이 -4.48% 로 끝났다."""
        got = plan(first=Decimal(96))
        assert not got.ok
        assert any("이익이 날 수 없다" in item for item in got.blocked)

    def test_stop_on_the_wrong_side_is_blocked(self) -> None:
        got = plan(stop=Decimal(102))
        assert not got.ok
        assert any("즉시 체결된다" in item for item in got.blocked)

    def test_targets_out_of_order_are_blocked(self) -> None:
        """최종이 1차보다 앞이면 사다리가 뒤집힌다 — 값이 아니라 순서의 오류다."""
        got = plan(first=Decimal(108), target=Decimal(104))
        assert not got.ok
        assert any("순서가 뒤집혔다" in item for item in got.blocked)

    def test_high_leverage_without_beta_is_refused(self) -> None:
        """T144 실측: 6배 β0 은 **청산 25건**이었다. 조용히 넘기지 않는다."""
        got = plan(leverage=Decimal(6), risk=settings(None))
        assert not got.ok
        assert got.beta is None


class TestPullsStopInsideLiquidation:
    """② 청산 밖 손절은 **닿을 수 없는 값**이라 닿는 값으로 옮긴다."""

    def test_stop_beyond_liquidation_is_tightened(self) -> None:
        # 6배 → 청산 16.2% · β0.40 → 손절 상한 6.5%. 20% 손절은 그 밖이다.
        got = plan(stop=Decimal(80), leverage=Decimal(6))
        assert got.moved
        assert got.stop > Decimal(80), "조이는 방향으로만 움직인다 (절대 규칙 #3)"
        assert got.stop < Decimal(100)

    def test_the_move_is_always_announced(self) -> None:
        """⚠️ 옮겨 놓고 말 안 하면 그것이 거짓말이다 (절대 규칙 #8)."""
        got = plan(stop=Decimal(80), leverage=Decimal(6))
        assert any("당겼다" in item for item in got.reasons)

    def test_tightening_alone_does_not_block(self) -> None:
        """당긴 것은 **경고**다 — 주문은 여전히 나간다."""
        got = plan(stop=Decimal(80), leverage=Decimal(6))
        assert got.ok
        assert got.moved


class TestShortIsTheMirror:
    """🪞 부호 하나로 갈린다 — 한쪽만 시험하면 반대쪽이 조용히 썩는다."""

    def short(
        self,
        *,
        stop: Decimal = Decimal(102),
        first: Decimal = Decimal(96),
        leverage: Decimal = Decimal(3),
    ) -> Confirmed:
        """숏 기본 계획 — 진입 100 · 손절 102 · 1차 96 · 최종 90."""
        return plan(stop=stop, first=first, target=Decimal(90), short=True, leverage=leverage)

    def test_clean_short_passes(self) -> None:
        got = self.short()
        assert got.ok, got.blocked
        assert got.stop == Decimal(102)
        assert got.rr == pytest.approx(Decimal(2))

    def test_short_stop_below_entry_is_blocked(self) -> None:
        got = self.short(stop=Decimal(98))
        assert not got.ok
        assert any("숏인데 손절이 진입 아래" in item for item in got.blocked)

    def test_short_target_above_entry_is_blocked(self) -> None:
        got = self.short(first=Decimal(104))
        assert not got.ok
        assert any("이익이 날 수 없다" in item for item in got.blocked)

    def test_short_stop_beyond_liquidation_is_tightened_downward(self) -> None:
        got = self.short(stop=Decimal(120), leverage=Decimal(6))
        assert got.moved
        assert got.stop < Decimal(120), "숏은 **내리는** 쪽이 조이는 쪽이다"
        assert got.stop > Decimal(100)


class TestReportsTheNumbersItUsed:
    """§1-0s — 규칙이 값을 **만들면** 그 값을 싣는다. 안 실으면 결함에 눈이 없다."""

    def test_carries_stop_pct_rr_and_need(self) -> None:
        got = plan()
        assert got.stop_pct == pytest.approx(Decimal(2))
        assert got.rr == pytest.approx(Decimal(2))
        # 🔴 **R 단위** 비용이다: c = 0.157% / 2% = 0.0785
        #    → (1 + 0.0785) / (1 + 2) = 35.95%. 가격 대비로 넣으면 33.4% 로 **낮게** 나온다.
        assert Decimal(35) < got.need_pct < Decimal(37)
        assert got.liq_pct > Decimal(30), "3배 청산은 32.8% 다"

    def test_narrow_stop_is_flagged_not_blocked(self) -> None:
        """T173: BTC 1h 계획의 83% 가 하한 아래였고 그 RR 3~8 은 종잇조각이었다."""
        got = plan(stop=Decimal("99.8"))
        assert got.ok, "스캘핑이면 의도일 수 있다 — 판단은 사람 것이다"
        assert any("하한" in item for item in got.warnings)


class TestRejectsInputErrors:
    """값이 아니라 **입력**이 틀린 것은 예외다 — 조용히 0 으로 떨어뜨리지 않는다."""

    @pytest.mark.parametrize("bad", [Decimal(0), Decimal(-1)])
    def test_entry_must_be_positive(self, bad: Decimal) -> None:
        with pytest.raises(ValueError, match="진입가"):
            plan(entry=bad)

    @pytest.mark.parametrize("bad", [Decimal(0), Decimal(-3)])
    def test_leverage_must_be_positive(self, bad: Decimal) -> None:
        with pytest.raises(ValueError, match="배율"):
            plan(leverage=bad)
