"""합류 가산 — **개수가 아니라 등급을 올린다**.

막아야 하는 실패 셋:

1. 같은 종류끼리 겹친 것을 합류로 세는 것 (박스 두 개는 증거 둘이 아니다)
2. 접힌 근거를 지워 "무엇과 무엇이 겹쳤나"를 잃는 것
3. 근거가 갈린 계열에 가산을 얹어 갈린 상태를 더 확신하는 것
"""

from decimal import Decimal

import pytest

from updown.analysis.evidence.confluence import apply_confluence
from updown.common.domain.evidence import Evidence, Family, Grade

ATR = Decimal(10)  # 허용 거리 = 0.5 x 10 = 5


def lv(source: str, grade: Grade, price: str) -> Evidence:
    """LEVEL 근거."""
    return Evidence(source=source, family=Family.LEVEL, grade=grade, price=Decimal(price))


class TestConfluence:
    def test_different_kinds_nearby_raise_the_best(self) -> None:
        """서로 다른 종류가 겹치면 가장 강한 것이 한 단계 오른다."""
        got = apply_confluence(
            [
                lv("structure.box.support", Grade.MEDIUM_BULL, "100"),
                lv("indicator.vwap.reclaim", Grade.WEAK_BULL, "102"),
            ],
            ATR,
        )
        best = next(e for e in got if e.source == "structure.box.support")
        other = next(e for e in got if e.source == "indicator.vwap.reclaim")
        assert best.grade is Grade.STRONG_BULL
        assert "합류" in best.detail
        assert other.suppressed_by == "structure.box.support"

    def test_same_kind_is_not_confluence(self) -> None:
        """🔴 박스 두 개가 겹치는 것은 증거 둘이 아니다."""
        got = apply_confluence(
            [
                lv("structure.box.support", Grade.MEDIUM_BULL, "100"),
                lv("structure.box.resistance", Grade.WEAK_BULL, "102"),
            ],
            ATR,
        )
        assert all(e.suppressed_by is None for e in got)
        assert all(e.grade in (Grade.MEDIUM_BULL, Grade.WEAK_BULL) for e in got)

    def test_far_apart_is_not_confluence(self) -> None:
        """허용 거리(0.5xATR) 밖이면 다른 자리다."""
        got = apply_confluence(
            [
                lv("structure.box.support", Grade.MEDIUM_BULL, "100"),
                lv("indicator.vwap.reclaim", Grade.WEAK_BULL, "120"),
            ],
            ATR,
        )
        assert all(e.suppressed_by is None for e in got)

    def test_cap_at_strong_bull(self) -> None:
        """⛔ 상한을 넘지 않는다."""
        got = apply_confluence(
            [
                lv("structure.box.support", Grade.STRONG_BULL, "100"),
                lv("indicator.vwap.reclaim", Grade.WEAK_BULL, "101"),
            ],
            ATR,
        )
        assert max(e.grade for e in got) is Grade.STRONG_BULL

    def test_suppressed_is_not_deleted(self) -> None:
        """🔴 접어도 지우지 않는다 — 로그에 남아야 한다."""
        got = apply_confluence(
            [
                lv("structure.box.support", Grade.MEDIUM_BULL, "100"),
                lv("indicator.vwap.reclaim", Grade.WEAK_BULL, "101"),
            ],
            ATR,
        )
        assert len(got) == 2


class TestGuards:
    def test_conflicted_family_gets_no_bonus(self) -> None:
        """⛔ 근거가 갈렸으면 "같은 자리를 가리킨다"는 전제가 이미 깨졌다."""
        got = apply_confluence(
            [
                lv("structure.box.support", Grade.MEDIUM_BULL, "100"),
                lv("indicator.vwap.reclaim", Grade.WEAK_BULL, "101"),
                lv("structure.trendline.break", Grade.STRONG_BEAR, "99"),
            ],
            ATR,
        )
        assert all(e.suppressed_by is None for e in got)
        assert next(e for e in got if "box" in e.source).grade is Grade.MEDIUM_BULL

    def test_no_atr_means_no_bonus(self) -> None:
        """거리를 잴 자가 없으면 아무것도 하지 않는다."""
        rows = [
            lv("structure.box.support", Grade.MEDIUM_BULL, "100"),
            lv("indicator.vwap.reclaim", Grade.WEAK_BULL, "101"),
        ]
        assert apply_confluence(rows, None) == rows

    def test_other_families_pass_through(self) -> None:
        """`LEVEL` 이 아닌 것은 손대지 않는다."""
        trend = Evidence("indicator.ma_stack", Family.TREND, Grade.MEDIUM_BULL)
        got = apply_confluence([trend], ATR)
        assert got == [trend]

    @pytest.mark.parametrize("order", [(0, 1), (1, 0)])
    def test_result_is_order_independent(self, order: tuple[int, int]) -> None:
        """🔴 입력 순서가 결과를 바꾸지 않는다 (절대 규칙 #5)."""
        rows = [
            lv("structure.box.support", Grade.MEDIUM_BULL, "100"),
            lv("indicator.vwap.reclaim", Grade.WEAK_BULL, "101"),
        ]
        got = apply_confluence([rows[order[0]], rows[order[1]]], ATR)
        raised = {e.source: e.grade for e in got}
        assert raised["structure.box.support"] is Grade.STRONG_BULL
