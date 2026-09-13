"""공시를 사건으로 읽기 — 8-K 항목 번호 분류 (T277 1단계 · 2026-09-12).

Note:
    값은 2026-09-12 AAPL `submissions` **실호출**에서 그대로 가져왔다 — 손으로 지어낸 모양이 아니다.
    한 번 호출에 최근 1,000건이 오고 `items` 칸이 함께 온다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from updown.marketdata.fundamentals.events import (
    FilingEvent,
    document_url,
    is_material,
    parse_items,
    parse_submissions,
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


class TestSubmissions:
    """`submissions` 실호출(AAPL · 2026-09-14 · 앞 40건) 픽스처."""

    FIXTURE = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "fundamentals"
        / "submissions_aapl.json"
    )

    def _body(self) -> dict[str, Any]:
        return cast("dict[str, Any]", json.loads(self.FIXTURE.read_text(encoding="utf-8")))

    def test_rows_are_events_with_document_links(self) -> None:
        rows = parse_submissions(self._body())
        assert 30 <= len(rows) <= 40
        assert rows == sorted(rows, key=lambda r: r.filed_at, reverse=True)
        eight_k = [r for r in rows if r.event.form == "8-K"]
        assert eight_k, "8-K 가 하나는 있어야 한다"
        first = eight_k[0]
        assert first.event.headline in {
            "실적 발표",
            "임원 변경",
            "주주총회 표결 결과",
            "기타 중요 사항",
        }
        assert first.url.startswith(
            "https://www.sec.gov/Archives/edgar/data/320193/"
        ) and first.url.endswith(".htm")
        assert first.material is True
        body = first.as_json()
        assert set(body) == {
            "filed_at",
            "accession",
            "form",
            "form_label",
            "items",
            "labels",
            "headline",
            "url",
            "description",
            "material",
        }
        assert not {"direction", "score", "sentiment", "bias", "probability"} & set(body)

    def test_limit_and_every_row_has_a_link(self) -> None:
        rows = parse_submissions(self._body(), limit=5)
        assert len(rows) == 5 and all(r.url for r in rows)
        # Form 4(내부자 거래)도 사건이다 — 8-K 만 보던 계획보다 넓다(실측).
        assert any(
            r.event.form == "4" and r.event.form_label == "내부자 거래"
            for r in parse_submissions(self._body())
        )

    def test_broken_rows_are_dropped_not_guessed(self) -> None:
        body = {
            "cik": "320193",
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K", "10-Q"],
                    "filingDate": ["2026-07-30", "not-a-date", "2026-08-01"],
                    "items": ["2.02,9.01", "5.02", ""],
                    "accessionNumber": ["0000320193-26-000068", "0000320193-26-000070", ""],
                    "primaryDocument": ["aapl-20260730.htm"],
                }
            },
        }
        rows = parse_submissions(body)
        assert [r.accession for r in rows] == ["0000320193-26-000068"]
        assert (
            rows[0].url
            == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000068/aapl-20260730.htm"
        )
        assert document_url("0000320193", "0000320193-26-000068") == (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000068/"
        )
        assert parse_submissions({}) == []
