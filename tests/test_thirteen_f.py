"""13F 읽기(T442) — 정보표 XML 파싱 · 접수 고르기 · 직전 분기 대비 변화."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from updown.apps.api.whalesurfer import diff_holdings, links_for, load_managers, merge_rows
from updown.marketdata.fundamentals.thirteen_f import (
    ThirteenFError,
    all_filings,
    infotable_name,
    parse_infotable,
    pick_reports,
    recent_reports,
)

XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip>
    <value>1000</value><shrsOrPrnAmt><sshPrnamt>50</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
  <infoTable>
    <nameOfIssuer>SPDR S&amp;P 500</nameOfIssuer><titleOfClass>PUT</titleOfClass>
    <cusip>78462f103</cusip>
    <value>300</value><shrsOrPrnAmt><sshPrnamt>10</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <putCall>Put</putCall><investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
</informationTable>
"""


def test_parse_infotable_reads_rows_and_put_call() -> None:
    rows = parse_infotable(XML, dollars=True)
    assert [r.cusip for r in rows] == ["037833100", "78462F103"]
    assert rows[0].value_usd == 1000 and rows[0].shares == 50 and rows[0].put_call is None
    assert rows[1].put_call == "Put"


def test_parse_infotable_scales_pre_2023_values_to_dollars() -> None:
    rows = parse_infotable(XML, dollars=False)
    assert rows[0].value_usd == 1_000_000


def test_parse_infotable_rejects_non_xml() -> None:
    with pytest.raises(ThirteenFError):
        parse_infotable("not xml")


def test_pick_reports_takes_only_13f_hr_newest_first() -> None:
    subs = {
        "filings": {
            "recent": {
                "form": ["13F-HR/A", "13F-HR", "10-K", "13F-HR"],
                "accessionNumber": ["a", "b", "c", "d"],
                "filingDate": ["2026-08-20", "2026-08-14", "2026-03-01", "2026-05-15"],
                "reportDate": ["2026-06-30", "2026-06-30", "2025-12-31", "2026-03-31"],
            }
        }
    }
    got = pick_reports(subs, 5)
    assert [g["accession"] for g in got] == ["b", "d"]
    assert got[0]["filed"] == date(2026, 8, 14) and got[0]["period"] == date(2026, 6, 30)


def test_infotable_name_prefers_infotable_over_other_xml() -> None:
    index = {
        "directory": {
            "item": [
                {"name": "primary_doc.xml"},
                {"name": "other.xml"},
                {"name": "form13fInfoTable.xml"},
            ]
        }
    }
    assert infotable_name(index) == "form13fInfoTable.xml"
    assert (
        infotable_name({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "x.xml"}]}})
        == "x.xml"
    )
    assert infotable_name({"directory": {"item": [{"name": "primary_doc.xml"}]}}) is None


class FakeEdgar:
    """submissions · index.json · 정보표를 메모리에서 준다."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def submissions(self, cik: str) -> dict[str, Any]:  # noqa: ARG002 — 가짜는 CIK 를 안 본다
        return {
            "name": "TEST FUND",
            "filings": {
                "recent": {
                    "form": ["13F-HR"],
                    "accessionNumber": ["0001-26-000001"],
                    "filingDate": ["2026-08-14"],
                    "reportDate": ["2026-06-30"],
                }
            },
        }

    async def get_json(self, url: str) -> object:
        self.urls.append(url)
        return {
            "form": ["13F-HR"],
            "accessionNumber": ["0001-13-000001"],
            "filingDate": ["2013-05-15"],
            "reportDate": ["2013-03-31"],
        }

    async def get_text(self, url: str) -> str:
        self.urls.append(url)
        if url.endswith("/index.json"):
            return json.dumps(
                {"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "infotable.xml"}]}}
            )
        return XML


@pytest.mark.asyncio
async def test_recent_reports_walks_submissions_index_and_infotable() -> None:
    fake = FakeEdgar()
    reports = await recent_reports(fake, "0001234567", 1)
    assert len(reports) == 1
    rep = reports[0]
    assert rep.entity == "TEST FUND" and rep.period == date(2026, 6, 30)
    assert rep.total_value_usd == 1300
    assert fake.urls == [
        "https://www.sec.gov/Archives/edgar/data/1234567/000126000001/index.json",
        "https://www.sec.gov/Archives/edgar/data/1234567/000126000001/infotable.xml",
    ]


@pytest.mark.asyncio
async def test_all_filings_appends_older_chunks() -> None:
    fake = FakeEdgar()
    subs = await fake.submissions("0001234567")
    subs["filings"]["files"] = [{"name": "CIK0001234567-submissions-001.json"}]
    merged = await all_filings(fake, subs)
    assert merged["filings"]["recent"]["accessionNumber"] == ["0001-26-000001", "0001-13-000001"]
    assert fake.urls[-1] == "https://data.sec.gov/submissions/CIK0001234567-submissions-001.json"
    assert [g["accession"] for g in pick_reports(merged, 9)] == ["0001-26-000001", "0001-13-000001"]


def test_figi_pick_prefers_us_composite_and_file_title() -> None:
    from updown.apps.api.whalesurfer_images import file_title
    from updown.marketdata.fundamentals.figi import pick

    rows = [{"ticker": "AAPL", "exchCode": "UA"}, {"ticker": "AAPL", "exchCode": "US"}]
    assert pick(rows) == {"ticker": "AAPL", "exchCode": "US"}
    assert pick([{"ticker": "X", "exchCode": "UW"}]) == {"ticker": "X", "exchCode": "UW"}
    assert pick([]) is None
    assert (
        file_title(
            "https://upload.wikimedia.org/wikipedia/commons/5/5f/Warren_Buffett_KU_Visit.jpg"
        )
        == "File:Warren_Buffett_KU_Visit.jpg"
    )
    assert file_title("https://x/y/Some%20Name.png") == "File:Some Name.png"
    assert (
        file_title("https://x/y/Pic.jpg?campaign=api&utm_content=thumbnail_unscaled")
        == "File:Pic.jpg"
    )


def _rep(rows: list[tuple[str, int, int]]) -> dict[str, Any]:
    total = sum(v for _, _, v in rows) or 1
    return {
        "holdings": [
            {
                "cusip": c,
                "issuer": c,
                "put_call": None,
                "shares": s,
                "value_usd": v,
                "weight": v / total,
            }
            for c, s, v in rows
        ]
    }


def test_diff_holdings_classifies_new_added_reduced_exited() -> None:
    now = _rep([("A", 10, 100), ("B", 20, 50), ("C", 5, 10)])
    prev = _rep([("A", 5, 90), ("B", 25, 60), ("D", 1, 1)])
    kinds = {r["cusip"]: r["kind"] for r in diff_holdings(now, prev)}
    assert kinds == {"A": "added", "B": "reduced", "C": "new", "D": "exited"}
    assert all(r["kind"] == "held" for r in diff_holdings(now, None))


def test_merge_rows_sums_split_lines_of_the_same_holding() -> None:
    # 13F 는 한 종목을 운용 주체마다 여러 줄로 적는다(버크셔 애플 2줄) — 합쳐야 비중 · 변화가 맞다.
    rows = parse_infotable(XML) + parse_infotable(XML)
    merged = merge_rows(rows)
    assert [(m["cusip"], m["rows"], m["shares"], m["value_usd"]) for m in merged] == [
        ("037833100", 2, 100, 2000),
        ("78462F103", 2, 20, 600),
    ]


def test_load_managers_reads_the_shipped_table() -> None:
    managers, tickers = load_managers()
    ciks = [m["cik"] for m in managers]
    assert len(ciks) >= 20 and len(set(ciks)) == len(ciks)
    assert all(len(c) == 10 and c.isdigit() for c in ciks)
    assert all(m["person"] and m["label"] for m in managers)
    assert tickers.get("037833100") == "AAPL"


def test_links_for_only_binance_when_stock_perp_exists() -> None:
    assert links_for("AAPL", ["AAPLUSDT", "TSLAUSDT"]) == {
        "toss": None,
        "binance": "https://www.binance.com/en/futures/AAPLUSDT",
        "gate": None,
    }
    assert links_for("ZZZZ", ["AAPLUSDT"])["binance"] is None
    assert links_for(None, ["AAPLUSDT"])["binance"] is None
