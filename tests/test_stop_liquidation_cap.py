"""β — 손절을 청산거리 안쪽으로 당기는 상한 (T120~T146).

## 왜 이것이 있나

T120 진단: 청산난 판의 **81~84%** 가 *"손절이 청산보다 바깥"* 인 판이었다. 손절거리가
t=+13~16 으로 청산을 예측했고 ADX 도 보유기간도 아니었다. 6x 면 청산이 16.2% 인데
SMA200 트레일이 20% 밖에 서면 **손절은 장식이고 청산이 먼저 온다.**

## 이 파일이 지키는 것

1. **조이는 방향으로만** 움직인다 — 넓히면 절대 규칙 #3 위반이다
2. **3x 를 넘는 배율은 β 없이 못 간다** — 측정이 말하는 건 "6x 가 좋다"가 아니라
   "**β 를 켠** 6x 가 좋다" 이다. 6x β0 은 청산 25건이다 (T144). 둘을 따로 켤 수
   있게 두면 언젠가 반쪽만 켜지고, 그 반쪽이 하필 위험한 쪽이다
3. **β 는 룰 설정에 없다** — 셋업별 β 는 `atr_stop_k` 와 같은 조작 통로다
"""

from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
import yaml

from updown.decision.risk.policy import (
    RiskConfigError,
    RiskSettings,
    load_settings,
    parse_settings,
    require_stop_cap,
)
from updown.decision.sizing import capped_stop, liquidation_distance

ENTRY = Decimal(100)


class TestLiquidationDistance:
    @pytest.mark.parametrize(
        ("leverage", "expected"),
        [(3, "0.328333"), (5, "0.195000"), (6, "0.161666"), (10, "0.095000")],
    )
    def test_it_matches_the_arithmetic(self, leverage: int, expected: str) -> None:
        """`1/L - 유지증거금(0.5%)` — 배율 표의 근거 그대로."""
        got = liquidation_distance(Decimal(leverage))
        assert abs(got - Decimal(expected)) < Decimal("0.00001")

    def test_a_nonpositive_leverage_raises(self) -> None:
        with pytest.raises(ValueError, match="배율"):
            liquidation_distance(Decimal(0))


class TestTheCapOnlyTightens:
    """🔴 절대 규칙 #3 — 손절은 조이는 쪽으로만 움직인다."""

    def test_a_far_long_stop_is_pulled_in(self) -> None:
        """6x 청산거리 16.2% · β0.40 → 상한 6.5%. 20% 밖 손절은 당겨진다."""
        got = capped_stop(
            entry=ENTRY,
            stop=Decimal(80),
            leverage=Decimal(6),
            ratio=Decimal("0.40"),
            short=False,
        )
        assert got > Decimal(80)
        assert abs(got - Decimal("93.53")) < Decimal("0.05")

    def test_a_near_long_stop_is_left_alone(self) -> None:
        """이미 안쪽이면 **손대지 않는다** — 넓히면 규칙 위반이다."""
        near = Decimal(98)
        got = capped_stop(
            entry=ENTRY,
            stop=near,
            leverage=Decimal(6),
            ratio=Decimal("0.40"),
            short=False,
        )
        assert got == near

    def test_a_far_short_stop_is_pulled_in(self) -> None:
        got = capped_stop(
            entry=ENTRY,
            stop=Decimal(120),
            leverage=Decimal(6),
            ratio=Decimal("0.40"),
            short=True,
        )
        assert got < Decimal(120)
        assert abs(got - Decimal("106.47")) < Decimal("0.05")

    def test_a_near_short_stop_is_left_alone(self) -> None:
        near = Decimal(102)
        assert (
            capped_stop(
                entry=ENTRY,
                stop=near,
                leverage=Decimal(6),
                ratio=Decimal("0.40"),
                short=True,
            )
            == near
        )

    def test_zero_ratio_is_off(self) -> None:
        """β=0 이면 아무 일도 안 한다 — 기존 동작 그대로."""
        far = Decimal(50)
        assert (
            capped_stop(entry=ENTRY, stop=far, leverage=Decimal(6), ratio=Decimal(0), short=False)
            == far
        )

    def test_at_3x_the_cap_rarely_binds(self) -> None:
        """3x 청산거리는 32.8% 라 β0.40 상한이 13.1% — 보통 손절(2~5%)은 안 걸린다.

        이것이 T124 에서 3x β 가 **2/10 · 대부분 차이 0** 이었던 이유다.
        """
        typical = ENTRY * Decimal("0.97")  # 3% 손절
        assert (
            capped_stop(
                entry=ENTRY,
                stop=typical,
                leverage=Decimal(3),
                ratio=Decimal("0.40"),
                short=False,
            )
            == typical
        )


class TestHighLeverageNeedsTheCap:
    """🔴 이 클래스가 이 기능의 안전장치다."""

    def _settings(self, ratio: object) -> RiskSettings:
        raw: dict[str, object] = {
            "anchors": {
                "conservative": {
                    "risk_pct": "0.005",
                    "min_rr": "2.0",
                    "max_positions": 3,
                    "daily_loss_limit_pct": "-0.02",
                },
                "standard": {
                    "risk_pct": "0.01",
                    "min_rr": "1.75",
                    "max_positions": 5,
                    "daily_loss_limit_pct": "-0.03",
                },
                "aggressive": {
                    "risk_pct": "0.03",
                    "min_rr": "1.5",
                    "max_positions": 8,
                    "daily_loss_limit_pct": "-0.06",
                },
            },
            "stop_liquidation_cap_ratio": ratio,
        }
        return parse_settings(raw)

    def test_high_leverage_without_a_cap_is_refused(self) -> None:
        """⛔ β 없이 배율만 올리면 **터뜨린다** (절대 규칙 #8 — 조용한 실패 금지).

        6x β0 은 청산 25건이다 (T144). 이 조합이 조용히 성립하면 안 된다.
        """
        settings = self._settings(None)
        with pytest.raises(RiskConfigError, match="β"):
            require_stop_cap(settings, Decimal(6))

    def test_high_leverage_with_a_cap_is_allowed(self) -> None:
        settings = self._settings("0.40")
        assert require_stop_cap(settings, Decimal(6)) == Decimal("0.40")

    def test_the_live_leverage_is_allowed_without_a_cap(self) -> None:
        """현행 3x 는 β 없이도 간다 — 3x 에서 β 는 아무 일도 안 했다 (T124)."""
        settings = self._settings(None)
        assert require_stop_cap(settings, Decimal(3)) is None

    @pytest.mark.parametrize("bad", ["0", "-0.1", "1.5"])
    def test_a_ratio_outside_the_range_is_refused(self, bad: str) -> None:
        """1 을 넘으면 손절이 청산 **밖**이라 상한이 아니라 구멍이다."""
        with pytest.raises(RiskConfigError, match="stop_liquidation_cap_ratio"):
            self._settings(bad)


class TestTheConfigKeepsItInOnePlace:
    def test_beta_never_appears_in_rule_configs(self) -> None:
        """⛔ 셋업별 β 는 `atr_stop_k` 와 똑같은 조작 통로다 (§6.1)."""
        for path in Path("config/rules").glob("*.yml"):
            loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                continue
            body = cast(dict[str, object], loaded)
            raw_params = body.get("params")
            if not isinstance(raw_params, dict):
                continue
            params = cast(dict[str, object], raw_params)
            for key in ("stop_liquidation_cap_ratio", "stop_cap_frac", "beta"):
                assert key not in params, (
                    f"{path.name} 에 {key} 가 있다 — β 의 유일한 자리는 config/risk.yml 이다"
                )

    def test_the_shipped_config_parses(self) -> None:
        """실제 `config/risk.yml` 이 읽히고 문턱이 3 이다."""
        settings = load_settings()
        assert settings.leverage_needing_stop_cap == Decimal(3)


class TestTheRunnerActuallyFillsIt:
    """🔴 필드만 있고 아무도 안 채우면 β 는 **조용히 꺼져 있다**.

    2026-08-30 하루에 같은 모양의 버그가 셋이었다 — 설정은 있는데 실행 경로가 안
    읽는 것. 그래서 여기서는 **배선 자체**를 소스로 확인한다.
    """

    def test_the_api_sets_stop_cap_on_every_session(self) -> None:
        """Session 을 만드는 **모든** 경로가 β 를 넣어야 한다.

        하나라도 빠지면 그 경로로 만든 판은 고배율에서 β 없이 돈다 — 가드가
        `require_stop_cap` 안에 있으므로, 호출을 빠뜨리면 가드도 같이 빠진다.
        """
        import updown.apps.api.walkforward as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        wired = source.count("session.stop_cap_ratio = require_stop_cap(")
        built = source.count("session = Session(")
        assert wired == built, (
            f"Session 을 {built}곳에서 만드는데 β 배선은 {wired}곳뿐이다 — "
            f"빠진 경로에서는 고배율이 β 없이 돈다"
        )

    def test_the_session_applies_it_before_the_wick_stop(self) -> None:
        """β 는 `wick_stop` 스위치와 무관하게 **항상** 돌아야 한다."""
        import updown.orchestration.walkforward.session as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        body = source[source.index("def _tighten(") :]
        cap = body.index("self._cap_to_liquidation(record)")
        switch = body.index("if not self.wick_stop:")
        assert cap < switch, "β 가 wick_stop 스위치 뒤에 있다 — 스위치가 꺼지면 β 도 꺼진다"
