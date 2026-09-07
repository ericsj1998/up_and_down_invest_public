"""리스크 정책 로딩·보간 검증 (P1-7-1 · spec §4.6 v1.9).

## 왜 앵커 수치를 테스트가 다시 적는가

§4.6 표가 사용자 성향 선언의 **유일한 근거**다. 설정 파일이 조용히 바뀌면 "내가 보수적으로
설정했는데 왜 이만큼 들어갔지"가 되고, 그때 원인을 찾을 방법이 없다. 테스트가 표를 붙들어
둔다 — 값을 바꾸려면 **테스트도 같이 고쳐야** 하므로 커밋에 의도가 남는다.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from updown.common.domain.instrument import Bucket
from updown.common.domain.proposal import RiskPresetName
from updown.decision.risk.policy import (
    DEFAULT_CONFIG_PATH,
    RiskAnchor,
    RiskConfigError,
    RiskSettings,
    interpolate,
    load_settings,
    parse_settings,
)


@pytest.fixture(scope="module")
def settings() -> RiskSettings:
    """저장소의 실제 설정을 쓴다 — 테스트가 운영 값과 어긋나지 않게 한다."""
    return load_settings()


def policy_at(settings: RiskSettings, slider: str, bucket: Bucket = Bucket.SCALP):
    """슬라이더 위치의 정책 (축약)."""
    return interpolate(settings, Decimal(slider), user_id="u1", bucket=bucket)


class TestAnchors:
    """§4.6 표를 붙들어 둔다."""

    def test_anchor_values_match_the_spec_table(self, settings: RiskSettings) -> None:
        """보수 0.5%/2.0/3/-2%, 표준 1%/1.75/5/-3%, 공격 3%/1.5/8/-6%."""
        expected = {
            RiskPresetName.CONSERVATIVE: (Decimal("0.005"), Decimal("2.0"), 3, Decimal("-0.02")),
            RiskPresetName.STANDARD: (Decimal("0.01"), Decimal("1.75"), 5, Decimal("-0.03")),
            RiskPresetName.AGGRESSIVE: (Decimal("0.03"), Decimal("1.5"), 8, Decimal("-0.06")),
        }
        for preset, (risk, rr, positions, limit) in expected.items():
            anchor = settings.anchors[preset]
            assert (anchor.risk_pct, anchor.min_rr) == (risk, rr), preset
            assert (anchor.max_positions, anchor.daily_loss_limit_pct) == (positions, limit)

    def test_slider_endpoints_land_exactly_on_anchors(self, settings: RiskSettings) -> None:
        """0.0 / 0.5 / 1.0 은 보간이 아니라 앵커 그 자체여야 한다."""
        for slider, preset in (
            ("0", RiskPresetName.CONSERVATIVE),
            ("0.5", RiskPresetName.STANDARD),
            ("1", RiskPresetName.AGGRESSIVE),
        ):
            policy = policy_at(settings, slider)
            anchor = settings.anchors[preset]
            assert policy.risk_pct == anchor.risk_pct, slider
            assert policy.min_rr == anchor.min_rr, slider
            assert policy.max_positions == anchor.max_positions, slider
            assert policy.preset is preset, slider


class TestInterpolation:
    """§4.6 보간 규칙 — 연속=선형, 정수=내림."""

    def test_continuous_parameters_are_linear(self, settings: RiskSettings) -> None:
        """보수↔표준 중간(0.25)은 두 앵커의 정확한 중간이다."""
        policy = policy_at(settings, "0.25")
        assert policy.risk_pct == Decimal("0.0075"), "0.5% ~ 1% 의 중간"
        assert policy.min_rr == Decimal("1.875"), "2.0 ~ 1.75 의 중간"
        assert policy.daily_loss_limit_pct == Decimal("-0.025")

    def test_min_rr_decreases_as_slider_rises(self, settings: RiskSettings) -> None:
        """**최소 RR 은 방향이 반대다** — 공격적일수록 낮아진다.

        Note:
            여기에 "안전한 쪽 내림"을 적용하면 **오히려 위험해진다.** 그래서 내림은
            정수 파라미터에만 쓴다 (모듈 docstring).
        """
        values = [policy_at(settings, s).min_rr for s in ("0", "0.25", "0.5", "0.75", "1")]
        assert values == sorted(values, reverse=True), f"단조 감소여야 한다: {values}"

    def test_integer_parameter_floors_never_rounds_up(self, settings: RiskSettings) -> None:
        """동시 포지션 수는 **내림**이다 — 올림하면 의도보다 공격적이 된다 (§4.6).

        Note:
            보수(3)↔표준(5) 구간에서 슬라이더 0.4 는 정확히 4.6 이다. 올림하면 5 —
            사용자가 "표준보다 보수적"으로 설정했는데 표준과 같은 포지션 수가 된다.
        """
        # 실측 표 그대로 못박는다 — 부등식만 걸면 자명해서 아무것도 못 잡는다.
        expected = {
            "0": 3,  # 앵커
            "0.1": 3,  # 3.4 -> 3
            "0.2": 3,  # 3.8 -> 3  (올림하면 4 — 의도보다 공격적)
            "0.25": 4,  # 4.0 정확히
            "0.4": 4,  # 4.6 -> 4  (올림하면 표준과 같아진다)
            "0.5": 5,  # 앵커
            "0.7": 6,  # 6.2 -> 6
            "0.9": 7,  # 7.4 -> 7
            "1": 8,  # 앵커
        }
        for slider, wanted in expected.items():
            assert policy_at(settings, slider).max_positions == wanted, slider

    def test_risk_pct_rises_monotonically(self, settings: RiskSettings) -> None:
        """회당 리스크는 슬라이더와 같은 방향이다."""
        values = [policy_at(settings, s).risk_pct for s in ("0", "0.3", "0.5", "0.8", "1")]
        assert values == sorted(values), f"단조 증가여야 한다: {values}"


class TestClamping:
    """§4.6 "슬라이더는 끝점을 넘어갈 수 없다"."""

    def test_out_of_range_slider_is_clamped(self, settings: RiskSettings) -> None:
        """음수·1 초과는 끝점으로 눌린다 — 예외가 아니라 클램프다 (§4.6 문구)."""
        assert policy_at(settings, "-5").risk_pct == policy_at(settings, "0").risk_pct
        assert policy_at(settings, "9").risk_pct == policy_at(settings, "1").risk_pct

    def test_risk_pct_never_exceeds_the_hard_cap(self, settings: RiskSettings) -> None:
        """회당 리스크 3% 절대 상한 (§4.6 참고 산수가 근거다).

        Note:
            회당 3% 로 5연속 손절 ≈ 계좌 -14%, 회당 10% 면 -41%. 그 산수가 상한의 근거다.
        """
        assert settings.risk_pct_hard_cap == Decimal("0.03")
        for slider in ("0", "0.5", "1", "99"):
            assert policy_at(settings, slider).risk_pct <= settings.risk_pct_hard_cap

    def test_hard_cap_survives_a_raised_anchor(self) -> None:
        """앵커가 상한을 넘으면 **설정 로딩이 거부**된다 — 상한이 무의미해지지 않게.

        Note:
            상한을 앵커와 별도로 둔 이유가 이것이다. 앵커 수치를 나중에 올려도 상한이
            살아남아야 한다 (§4.6 "안전장치는 프리셋으로 끌 수 없다").
        """
        with pytest.raises(RiskConfigError, match="절대 상한"):
            RiskSettings(
                anchors={
                    preset: RiskAnchor(
                        risk_pct=Decimal("0.10"),  # 10% — 상한 초과
                        min_rr=Decimal("1.5"),
                        max_positions=5,
                        daily_loss_limit_pct=Decimal("-0.03"),
                    )
                    for preset in RiskPresetName
                },
                risk_pct_hard_cap=Decimal("0.03"),
                trailing_enabled_buckets=frozenset(),
            )


class TestTrailingIsPerBucket:
    """§6.9 — 트레일링은 성향이 아니라 **버킷의 성질**이다."""

    def test_scalp_and_swing_enabled_longterm_not(self, settings: RiskSettings) -> None:
        """장투에 트레일링을 걸면 정상적인 조정에서 털린다."""
        assert policy_at(settings, "0.5", Bucket.SCALP).trailing_enabled is True
        assert policy_at(settings, "0.5", Bucket.SWING).trailing_enabled is True
        assert policy_at(settings, "0.5", Bucket.LONGTERM).trailing_enabled is False

    def test_slider_does_not_change_trailing(self, settings: RiskSettings) -> None:
        """슬라이더를 끝까지 올려도 장투 트레일링은 켜지지 않는다."""
        for slider in ("0", "0.5", "1"):
            assert policy_at(settings, slider, Bucket.LONGTERM).trailing_enabled is False


class TestPresetLabel:
    """표시용 이름 — 사용자에게 의미를 주는 값이다 (§4.6)."""

    def test_nearest_anchor_wins(self, settings: RiskSettings) -> None:
        assert policy_at(settings, "0.1").preset is RiskPresetName.CONSERVATIVE
        assert policy_at(settings, "0.45").preset is RiskPresetName.STANDARD
        assert policy_at(settings, "0.9").preset is RiskPresetName.AGGRESSIVE

    def test_ties_pick_the_more_conservative_side(self, settings: RiskSettings) -> None:
        """동거리면 보수적인 쪽 — 이름이 실제보다 공격적으로 보이지 않게 한다."""
        assert policy_at(settings, "0.25").preset is RiskPresetName.CONSERVATIVE
        assert policy_at(settings, "0.75").preset is RiskPresetName.STANDARD


class TestAtrStopCandidates:
    """ATR 손절 배수 k — **후보로 두고 하나로 확정하지 않는다** (축 G-k)."""

    def test_three_standard_candidates_are_registered(self, settings: RiskSettings) -> None:
        """표준 관행값 1.5 / 2.0 / 2.5 (§6.1 범위 안).

        Note:
            k 는 축 I·J 와 달리 **형태 논거로 정해지지 않는다** — §6.1 이 범위만 주고
            그 안의 위치는 손절폭 vs 익절 도달의 트레이드오프 판단이다. 억지 근거를
            만드는 것보다 성과가 고르게 한다 (§5.6.7).

            ⛔ **후보를 3개로 고정한다.** 전부 실패했을 때 4번째를 붙이면 그것이
            "오더블록을 살리려 k 를 조작"하는 것이다.
        """
        assert settings.atr_stop_multiple_candidates == (
            Decimal("1.5"),
            Decimal("2.0"),
            Decimal("2.5"),
        )

    def test_out_of_range_multiple_is_rejected(self) -> None:
        """§6.1 범위(1.5~3.0) 밖은 거부한다 — 범위 확장은 스펙 개정이 먼저다."""
        anchors = {
            preset: RiskAnchor(
                risk_pct=Decimal("0.01"),
                min_rr=Decimal("1.75"),
                max_positions=5,
                daily_loss_limit_pct=Decimal("-0.03"),
            )
            for preset in RiskPresetName
        }
        for bad in (Decimal("1.0"), Decimal("4.0")):
            with pytest.raises(RiskConfigError, match=r"§6.1 범위"):
                RiskSettings(
                    anchors=anchors,
                    risk_pct_hard_cap=Decimal("0.03"),
                    trailing_enabled_buckets=frozenset(),
                    atr_stop_multiple_candidates=(bad,),
                )

    def test_k_never_appears_in_rule_configs(self) -> None:
        """⭐ **k 는 룰 설정에 없어야 한다** — 셋업별 k 는 조작 통로다.

        Note:
            문서 약속만으로는 막히지 않는다. `config/rules/<setup>.yml` 에 k 가 들어가면
            "오더블록만 k 를 낮춰 RR 을 통과시키는" 경로가 생기고, 그것이 확정 7번이
            금지한 것이다 (판정 대상이 판정자가 된다).

            손절은 `decision` 소관이므로(절대 규칙 #4) 룰 설정에 있으면 계층도 어긋난다.
            k 의 유일한 자리는 `config/risk.yml` 이다.
        """
        forbidden = ("atr_stop_multiple", "stop_atr_multiple", "atr_stop_k")
        for path in sorted(Path("config/rules").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            for key in forbidden:
                assert key not in text, (
                    f"{path.name} 에 `{key}` 가 있다 — k 는 셋업별 값이 될 수 없다. "
                    f"유일한 자리는 config/risk.yml 이다 (축 G-k ①)"
                )


class TestConfigContract:
    """설정 오류는 조용히 넘기지 않는다 (절대 규칙 #8)."""

    def test_missing_file_is_rejected_not_defaulted(self, tmp_path: Path) -> None:
        """⚠️ 구조물 파라미터와 **다르게** 파일 부재를 허용하지 않는다.

        Note:
            리스크 정책에는 "안전한 기본값"이 존재하지 않는다 — 없으면 기본값으로
            진행하는 것이 **의도보다 큰 포지션**으로 이어질 수 있다.
        """
        with pytest.raises(RiskConfigError, match="안전한 기본값"):
            load_settings(tmp_path / "없는파일.yml")

    def test_unknown_preset_is_rejected(self) -> None:
        """오타 난 프리셋을 무시하면 '설정을 바꿨는데 안 바뀐다'가 된다."""
        with pytest.raises(RiskConfigError, match="알 수 없는 프리셋"):
            parse_settings({"anchors": {"conservatvie": {}}})

    def test_unknown_field_is_rejected(self) -> None:
        """앵커 안의 오타도 거부한다."""
        with pytest.raises(RiskConfigError, match="알 수 없는 키"):
            parse_settings(
                {
                    "anchors": {
                        "standard": {
                            "risk_pct": 0.01,
                            "min_rr": 1.75,
                            "max_positions": 5,
                            "daily_loss_limit_pct": -0.03,
                            "risk_pcnt": 0.02,
                        }
                    }
                }
            )

    def test_missing_anchor_is_rejected(self) -> None:
        """3개가 있어야 보간이 성립한다."""
        with pytest.raises(RiskConfigError, match="앵커가 빠졌다"):
            parse_settings(
                {
                    "anchors": {
                        "standard": {
                            "risk_pct": 0.01,
                            "min_rr": 1.75,
                            "max_positions": 5,
                            "daily_loss_limit_pct": -0.03,
                        }
                    }
                }
            )

    def test_positive_loss_limit_is_rejected(self) -> None:
        """일일 손실 한도는 음수다 — 부호 실수가 킬 스위치를 무력화한다."""
        with pytest.raises(RiskConfigError, match="음수"):
            RiskAnchor(
                risk_pct=Decimal("0.01"),
                min_rr=Decimal("1.75"),
                max_positions=5,
                daily_loss_limit_pct=Decimal("0.03"),
            )

    def test_yaml_floats_do_not_leak_binary_error(self, settings: RiskSettings) -> None:
        """YAML float 를 Decimal 로 정확히 옮긴다.

        Note:
            `Decimal(0.005)` 는 `0.005000000000000000104...` 다. 그 오차가 리스크 비율에
            들어가면 수량 계산으로 번진다.
        """
        assert settings.anchors[RiskPresetName.CONSERVATIVE].risk_pct == Decimal("0.005")

    def test_default_path_is_the_repository_config(self) -> None:
        """운영 경로가 저장소 설정을 가리킨다."""
        assert Path("config/risk.yml") == DEFAULT_CONFIG_PATH
