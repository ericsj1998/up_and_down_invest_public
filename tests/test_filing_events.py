"""공시를 사건으로 읽기 — 8-K 항목 번호 분류 (T277 1단계 · 2026-09-12).

Note:
    값은 2026-09-12 AAPL `submissions` **실호출**에서 그대로 가져왔다 — 손으로 지어낸 모양이 아니다.
    한 번 호출에 최근 1,000건이 오고 `items` 칸이 함께 온다.
"""

from __future__ import annotations

from updown.marketdata.fundamentals.events import (
    FilingEvent,
    is_material,
    parse_items,
    read_filing,
)


def test_earnings_filing_reads_as_earnings_not_attachment() -> None:
    """실적은 `2.02,9.01` 로 온다 — 첨부(9.01)가 제목이 되면 무슨 일인지 안 보인다."""
    got = read_filing("8-K", "2.02,9.01")
    assert got.headline == "실적 발표"
    assert got.items == ("2.02", "9.01")
    assert got.labels == ("실적 발표", "재무제표·첨부")


def test_officer_change() -> None:
    assert read_filing("8-K", "5.02").headline == "임원 변경"


def test_shareholder_vote() -> None:
    assert read_filing("8-K", "5.07,9.01").headline == "주주총회 표결 결과"


def test_unknown_item_keeps_its_number() -> None:
    """새 항목이 생겨도 조용히 사라지지 않는다 — 번호가 그대로 보인다 (규칙 #8)."""
    got = read_filing("8-K", "1.99")
    assert got.headline == "1.99"
    assert got.labels == ("1.99",)


def test_other_forms_use_the_form_name() -> None:
    """8-K 가 아니어도 사건이 되는 것이 있다 — Form 4 는 내부자 거래다."""
    assert read_filing("4").headline == "내부자 거래"
    assert read_filing("10-Q").headline == "분기 보고서"


def test_parse_items_tolerates_spacing_and_empties() -> None:
    assert parse_items("2.02, 9.01") == ("2.02", "9.01")
    assert parse_items("") == ()
    assert parse_items(" , ") == ()


def test_attachment_only_filing_is_not_material() -> None:
    """첨부 목록만 있는 8-K 는 목록에 안 올린다 — 잡음 거르기이지 호재·악재 판단이 아니다."""
    assert not is_material(read_filing("8-K", "9.01"))
    assert is_material(read_filing("8-K", "2.02,9.01"))


def test_unknown_form_is_not_material() -> None:
    """모르는 서식은 올리지 않는다 — 목록이 알 수 없는 것으로 차면 사람이 안 본다."""
    assert not is_material(read_filing("ZZZ"))


def test_it_says_nothing_about_direction() -> None:
    """🔴 이 모듈은 **무슨 일이 있었나** 까지다 — 호재·악재를 말하지 않는다 (규칙 #2)."""
    fields = set(FilingEvent.__dataclass_fields__)
    for banned in ("sentiment", "score", "bullish", "positive", "direction"):
        assert banned not in fields
