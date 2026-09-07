"""파라미터 덮어쓰기 (`inspection/overrides.py`).

여기서 지키려는 성질은 둘이다.

    **범위를 벗어나면 자르지 않고 거부한다** — 자르면 사용자는 넣은 값으로 보고 있다고
    믿고, 그 상태의 관찰은 관찰이 아니다.

    **바뀐 것이 응답에 남는다** — 안 남으면 며칠 뒤 그 화면을 판정 설정이라 믿는다.
"""

from decimal import Decimal

import pytest

from updown.analysis.structures.balance import ZIGZAG_ATR_MULTIPLE
from updown.analysis.structures.params import StructureParams
from updown.orchestration.inspection.overrides import (
    EDITABLE,
    ZIGZAG_KEY,
    OverrideError,
    keys_for,
)
from updown.orchestration.inspection.overrides import (
    apply_overrides as apply,
)
from updown.orchestration.inspection.overrides import (
    override_defaults as defaults,
)


class TestApply:
    def test_no_overrides_keeps_the_standard(self) -> None:
        got = apply({})
        assert got.changed == {}
        assert got.params == StructureParams()
        assert got.deviation == ZIGZAG_ATR_MULTIPLE

    def test_blank_values_are_ignored(self) -> None:
        """화면의 빈 입력칸이 0 으로 읽히면 안 된다."""
        got = apply({"trendline.min_touches": "", "swing.left_bars": "   "})
        assert got.changed == {}

    def test_int_field_is_applied(self) -> None:
        got = apply({"trendline.min_touches": "4"})
        assert got.params.trendline.min_touches == 4
        assert got.changed == {"trendline.min_touches": "4"}

    def test_decimal_field_keeps_precision(self) -> None:
        """float 로 받으면 0.25 가 0.25000000000000001 이 된다."""
        got = apply({"trendline.touch_atr_multiple": "0.35"})
        assert got.params.trendline.touch_atr_multiple == Decimal("0.35")

    def test_zigzag_is_returned_separately(self) -> None:
        """편차는 StructureParams 밖에 산다."""
        got = apply({ZIGZAG_KEY: "1.85"})
        assert got.deviation == Decimal("1.85")
        assert got.params == StructureParams()

    def test_several_sections_at_once(self) -> None:
        got = apply(
            {
                "swing.left_bars": "3",
                "box.min_touches": "4",
                "channel.min_opposite_touches": "1",
            }
        )
        assert got.params.swing.left_bars == 3
        assert got.params.box.min_touches == 4
        assert got.params.channel.min_opposite_touches == 1
        assert len(got.changed) == 3

    def test_right_bars_is_untouched_when_only_left_changes(self) -> None:
        """한 절의 다른 필드를 날리면 안 된다 (`replace` 를 쓰는 이유)."""
        got = apply({"swing.left_bars": "5"})
        assert got.params.swing.right_bars == StructureParams().swing.right_bars


class TestRejection:
    def test_unknown_key_is_refused(self) -> None:
        """임의 설정 키를 열면 '무엇으로 계산한 그림인가'를 응답만 보고 알 수 없다."""
        with pytest.raises(OverrideError, match="바꿀 수 없는 키"):
            apply({"risk.max_position": "999"})

    def test_out_of_range_is_refused_not_clamped(self) -> None:
        """🔴 조용히 자르면 넣은 값과 다른 값으로 본 그림이 된다."""
        with pytest.raises(OverrideError, match="범위를 벗어났다"):
            apply({"trendline.min_touches": "99"})

    def test_below_range_is_refused(self) -> None:
        with pytest.raises(OverrideError, match="범위를 벗어났다"):
            apply({ZIGZAG_KEY: "0.1"})

    def test_non_numeric_is_refused(self) -> None:
        with pytest.raises(OverrideError, match="숫자가 아니다"):
            apply({"swing.left_bars": "둘"})

    def test_infinity_is_refused(self) -> None:
        """`Decimal('Infinity')` 는 파싱을 통과한다 — 따로 막아야 한다."""
        with pytest.raises(OverrideError, match="유한한 값이 아니다"):
            apply({ZIGZAG_KEY: "Infinity"})

    def test_fraction_for_an_int_field_is_refused(self) -> None:
        """2.5 접점은 뜻이 없다. 반올림하면 사용자가 넣은 값이 아니다."""
        with pytest.raises(OverrideError, match="정수여야 한다"):
            apply({"trendline.min_touches": "2.5"})


class TestCatalogIntegrity:
    def test_keys_are_unique(self) -> None:
        keys = [item.key for item in EDITABLE]
        assert len(keys) == len(set(keys))

    def test_every_editable_has_a_default(self) -> None:
        """placeholder 가 없으면 화면이 빈 칸을 보여 주고, 빈 칸은 0 처럼 읽힌다."""
        assert set(defaults()) == {item.key for item in EDITABLE}

    def test_every_default_is_inside_its_own_range(self) -> None:
        """표준값이 범위 밖이면 아무것도 안 바꿔도 거부당한다."""
        current = defaults()
        for item in EDITABLE:
            value = Decimal(current[item.key])
            assert item.low <= value <= item.high, item.key

    def test_every_editable_points_at_a_real_flag(self) -> None:
        """화면이 파라미터를 플래그 밑에 붙이므로 짝이 없으면 안 보인다."""
        from updown.orchestration.inspection.catalog import CATALOG

        known = {flag.id for flag in CATALOG}
        for item in EDITABLE:
            assert item.flag in known, item.flag

    def test_applying_the_defaults_changes_nothing_but_is_recorded(self) -> None:
        """표준값을 그대로 넣어도 사용자가 '건드렸다'는 사실은 남는다."""
        got = apply(defaults())
        assert got.params == StructureParams()
        assert got.deviation == ZIGZAG_ATR_MULTIPLE
        assert set(got.changed) == set(defaults())


class TestKeysFor:
    """🔴 **소유자 ≠ 의존**.

    `Editable.flag` 는 입력칸을 어느 플래그 밑에 붙일지를 정하는 소유자 표시다. 재현
    탐색은 다른 것을 물어야 한다 — *이 플래그를 실제로 움직이는 값이 무엇인가.*
    """

    def test_owned_parameters_come_first(self) -> None:
        got = keys_for("structure.box")
        assert got == ("box.cluster_atr_multiple", "box.min_touches")

    def test_swing_trendline_is_moved_by_the_zigzag_deviation(self) -> None:
        """🔴 소유 파라미터가 0개라 예전엔 '탐색할 것이 없다'만 나왔다.

        그 선의 앵커는 마디 전환점이고, 마디는 `zigzag.deviation` 이 끊는다 —
        **움직이는데 목록에 없었다.** 그 상태로는 사람이 선을 고쳐 내도 시스템이
        되물을 것이 없어 이의제기가 규칙으로 환원될 길이 막힌다 (절대 규칙 #11).
        """
        assert keys_for("structure.swing_trendline") == (ZIGZAG_KEY,)
        assert keys_for("structure.leg_channel") == (ZIGZAG_KEY,)

    def test_pivot_moves_with_the_swing_parameters(self) -> None:
        """프랙탈 전부는 스윙과 **같은 탐지**에서 나온다 — 좌우 봉 수가 그대로 움직인다."""
        assert keys_for("structure.pivot") == ("swing.left_bars", "swing.right_bars")

    def test_unknown_flag_has_nothing_to_sweep(self) -> None:
        """빈 튜플이면 탐색기가 '탐색할 것이 없다'고 **말한다** — 조용히 0건이 아니다."""
        assert keys_for("indicator.rsi") == ()

    def test_no_duplicates_when_owned_and_declared_overlap(self) -> None:
        """같은 키를 두 번 훑으면 후보 목록에 같은 값이 두 줄 뜬다."""
        for flag in {item.flag for item in EDITABLE} | {"structure.swing_trendline"}:
            got = keys_for(flag)
            assert len(got) == len(set(got)), flag

    def test_every_key_it_hands_out_is_real(self) -> None:
        """오타가 나면 그 플래그만 **조용히** 안 훑는다 — 화면은 멀쩡해 보인다."""
        from updown.orchestration.inspection.catalog import CATALOG

        known = {item.key for item in EDITABLE}
        for flag in CATALOG:
            for key in keys_for(flag.id):
                assert key in known, f"{flag.id} -> {key}"
