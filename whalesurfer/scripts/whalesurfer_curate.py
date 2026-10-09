"""WhaleSurfer 보고자 큐레이션(T442 D1 · 사용자 2026-10-08 "큐레이션 28 → 100") — 이름 → EDGAR CIK 를 자동으로 고르고 검증한다.

고르는 규칙(결과 전 고정):
  1. EDGAR 이름 검색(efts `search-index?keysTyped=`)의 상위 6 후보를 받는다.
  2. 후보마다 submissions 를 읽어 **가장 최근 13F-HR 접수일**이 가장 늦은 것을 고른다(같은 이름의 옛 CIK · 자회사를 걸러낸다).
  3. 그 접수가 400일보다 오래됐으면 "끊김" 으로 빼고 표에 남긴다.
기존 28곳은 그대로 두고 새 후보만 더한다. 결과: `config/whalesurfer/managers.yml`(다시 씀 · 수동 티커 표는 유지) · `logs/t279/t442/curation.md`.

    set -a; . ./.env.dev; set +a; uv run --no-sync python whalesurfer/scripts/whalesurfer_curate.py [--dry]
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from updown.common.config import load_settings
from updown.marketdata.fundamentals.client import EdgarClient
from updown.marketdata.provider import edgar_client
from whalesurfer.edgar.thirteen_f import pick_reports

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "whalesurfer" / "managers.yml"
OUT = ROOT / "logs" / "t279" / "t442" / "curation.md"
STALE_DAYS = 400
TOP_HITS = 6

# (검색어, 표시 이름, 인물, 위키 제목(없으면 인물 이름 · "" 이면 사진 안 찾음))
CANDIDATES: list[tuple[str, str, str, str | None]] = [
    ("Appaloosa LP", "Appaloosa LP", "David Tepper", None),
    ("Greenlight Capital", "Greenlight Capital", "David Einhorn", None),
    ("Tudor Investment Corp", "Tudor Investment Corp", "Paul Tudor Jones", None),
    ("Paulson & Co", "Paulson & Co.", "John Paulson", None),
    ("Glenview Capital Management", "Glenview Capital Management", "Larry Robbins", None),
    ("Maverick Capital", "Maverick Capital", "Lee Ainslie", None),
    ("Jana Partners", "JANA Partners", "Barry Rosenstein", None),
    ("Starboard Value", "Starboard Value", "Jeffrey Smith", "Jeffrey Smith (investor)"),
    ("ValueAct", "ValueAct Capital", "Mason Morfit", None),
    ("Baillie Gifford", "Baillie Gifford & Co.", "Baillie Gifford", ""),
    ("Dodge & Cox", "Dodge & Cox", "Dodge & Cox", ""),
    ("Fisher Asset Management", "Fisher Asset Management", "Ken Fisher", "Kenneth Fisher"),
    ("Harris Associates", "Harris Associates (Oakmark)", "Bill Nygren", None),
    ("Tweedy Browne", "Tweedy, Browne Co.", "Tweedy, Browne", ""),
    ("Yacktman Asset Management", "Yacktman Asset Management", "Stephen Yacktman", ""),
    ("Gotham Asset Management", "Gotham Asset Management", "Joel Greenblatt", None),
    ("Aquamarine Capital", "Aquamarine Capital", "Guy Spier", None),
    ("Giverny Capital", "Giverny Capital", "François Rochon", ""),
    ("Markel", "Markel Group", "Tom Gayner", "Thomas Gayner"),
    ("Fundsmith", "Fundsmith", "Terry Smith", "Terry Smith (fund manager)"),
    ("Polen Capital", "Polen Capital Management", "Polen Capital", ""),
    ("Ruane Cunniff", "Ruane, Cunniff & Goldfarb (Sequoia)", "Ruane Cunniff", ""),
    (
        "Southeastern Asset Management",
        "Southeastern Asset Management (Longleaf)",
        "Mason Hawkins",
        "O. Mason Hawkins",
    ),
    ("First Eagle Investment Management", "First Eagle Investment Management", "First Eagle", ""),
    ("Fairfax Financial Holdings", "Fairfax Financial Holdings", "Prem Watsa", None),
    ("Brave Warrior Advisors", "Brave Warrior Advisors", "Glenn Greenberg", ""),
    (
        "Abrams Capital Management",
        "Abrams Capital Management",
        "David Abrams",
        "David Abrams (investor)",
    ),
    ("D1 Capital Partners", "D1 Capital Partners", "Dan Sundheim", "Daniel Sundheim"),
    ("Altimeter Capital Management", "Altimeter Capital", "Brad Gerstner", None),
    ("Dragoneer Investment Group", "Dragoneer Investment Group", "Marc Stad", ""),
    ("Whale Rock Capital Management", "Whale Rock Capital", "Alex Sacerdote", ""),
    ("Light Street Capital Management", "Light Street Capital", "Glen Kacher", ""),
    ("TCI Fund Management", "TCI Fund Management", "Chris Hohn", None),
    ("Egerton Capital", "Egerton Capital", "John Armitage", ""),
    ("Lansdowne Partners", "Lansdowne Partners", "Lansdowne Partners", ""),
    ("Marshall Wace", "Marshall Wace", "Paul Marshall", "Paul Marshall (investor)"),
    ("AQR Capital Management", "AQR Capital Management", "Cliff Asness", "Clifford Asness"),
    ("Balyasny Asset Management", "Balyasny Asset Management", "Dmitry Balyasny", None),
    ("ExodusPoint Capital Management", "ExodusPoint Capital", "Michael Gelband", ""),
    ("Schonfeld Strategic Advisors", "Schonfeld Strategic Advisors", "Schonfeld", ""),
    ("Moore Capital Management", "Moore Capital Management", "Louis Bacon", None),
    ("Caxton Associates", "Caxton Associates", "Bruce Kovner", None),
    ("Saba Capital Management", "Saba Capital Management", "Boaz Weinstein", None),
    ("Davis Selected Advisers", "Davis Selected Advisers", "Chris Davis", "Christopher C. Davis"),
    ("MSD Partners", "MSD Partners", "Michael Dell", None),
    ("Sachem Head Capital Management", "Sachem Head Capital", "Scott Ferguson", ""),
    ("Engaged Capital", "Engaged Capital", "Glenn Welling", ""),
    ("Corvex Management", "Corvex Management", "Keith Meister", None),
    ("Eminence Capital", "Eminence Capital", "Ricky Sandler", ""),
    ("Lakewood Capital Management", "Lakewood Capital", "Anthony Bozza", ""),
    ("Hudson Bay Capital Management", "Hudson Bay Capital", "Sander Gerber", ""),
    ("Arrowstreet Capital", "Arrowstreet Capital", "Arrowstreet", ""),
    ("Adage Capital Partners", "Adage Capital Management", "Phill Gross", ""),
    ("Soroban Capital Partners", "Soroban Capital Partners", "Eric Mandelblatt", ""),
    ("Atlantic Investment Management", "Atlantic Investment Management", "Alexander Roepers", ""),
    ("Cantillon Capital Management", "Cantillon Capital Management", "William von Mueffling", ""),
    ("Durable Capital Partners", "Durable Capital Partners", "Henry Ellenbogen", ""),
    ("Sands Capital Management", "Sands Capital Management", "Frank Sands", ""),
    ("Eagle Capital Management", "Eagle Capital Management", "Eagle Capital", ""),
    ("Pzena Investment Management", "Pzena Investment Management", "Richard Pzena", ""),
    ("Lyrical Asset Management", "Lyrical Asset Management", "Andrew Wellington", ""),
    ("Wedgewood Partners", "Wedgewood Partners", "David Rolfe", ""),
    ("Punch Card Management", "Punch Card Management", "Norbert Lou", ""),
    ("Semper Augustus Investments", "Semper Augustus Investments Group", "Chris Bloomstran", ""),
    ("Smead Capital Management", "Smead Capital Management", "Bill Smead", ""),
    ("Miller Value Partners", "Miller Value Partners", "Bill Miller", "Bill Miller (investor)"),
    ("GAMCO Investors", "GAMCO Investors", "Mario Gabelli", None),
    ("Omega Advisors", "Omega Advisors", "Leon Cooperman", None),
    ("BAMCO", "Baron Capital (BAMCO)", "Ron Baron", None),
    ("Gardner Russo", "Gardner Russo & Quinn", "Thomas Russo", "Thomas Russo (investor)"),
    ("Weitz Investment Management", "Weitz Investment Management", "Wally Weitz", ""),
    ("Royce & Associates", "Royce & Associates", "Chuck Royce", "Charles M. Royce"),
    ("Ariel Investments", "Ariel Investments", "John W. Rogers", "John W. Rogers Jr."),
    ("Grantham Mayo Van Otterloo", "GMO (Grantham, Mayo, Van Otterloo)", "Jeremy Grantham", None),
    (
        "BlueCrest Capital Management",
        "BlueCrest Capital Management",
        "Michael Platt",
        "Michael Platt (financier)",
    ),
    ("Winton Group", "Winton Group", "David Harding", "David Harding (financier)"),
    (
        "Discovery Capital Management",
        "Discovery Capital Management",
        "Rob Citrone",
        "Robert Citrone",
    ),
    ("Sculptor Capital LP", "Sculptor Capital", "Sculptor", ""),
    ("Daily Journal Corp", "Daily Journal Corp.", "Charlie Munger", None),
    # Cascade Investment(게이츠 개인 회사)는 검색이 다른 회사(CASCADE INVESTMENT GROUP, INC.)를 골라 뺐다 — 게이츠 재단 신탁으로 충분.
    ("Kingdon Capital", "Kingdon Capital Management", "Mark Kingdon", ""),
]


def recent_13f(subs: dict[str, Any]) -> date | None:
    got = pick_reports(subs, 1)
    return got[0]["filed"] if got else None


async def resolve(client: EdgarClient, query: str) -> list[dict[str, Any]]:
    """이름 검색 상위 후보 → 각 CIK 의 이름 · 최근 13F-HR."""
    text = await client.get_text(
        "https://efts.sec.gov/LATEST/search-index?keysTyped=" + urllib.parse.quote(query)
    )
    body = cast("dict[str, Any]", json.loads(text))
    hits = cast(
        "list[dict[str, Any]]", cast("dict[str, Any]", body.get("hits") or {}).get("hits") or []
    )[:TOP_HITS]
    out: list[dict[str, Any]] = []
    for h in hits:
        cik = str(h.get("_id", "")).zfill(10)
        try:
            subs = await client.submissions(cik)
        except Exception as exc:
            out.append(
                {
                    "cik": cik,
                    "name": h.get("_source", {}).get("entity"),
                    "last": None,
                    "error": str(exc)[:60],
                }
            )
            continue
        out.append({"cik": cik, "name": subs.get("name"), "last": recent_13f(subs)})
    return out


async def main() -> int:
    dry = "--dry" in sys.argv
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    have = {str(m["cik"]).zfill(10): m for m in raw["managers"]}
    client = edgar_client(load_settings())
    today = datetime.now(UTC).date()
    lines = [
        "# WhaleSurfer 큐레이션 — 이름 → CIK 자동 고르기(최근 13F-HR 이 가장 늦은 후보) · "
        + today.isoformat(),
        "",
        "| 검색어 | 고른 CIK | EDGAR 이름 | 최근 13F-HR | 판정 | 다른 후보 |",
        "|---|---|---|---|---|---|",
    ]
    added: list[dict[str, Any]] = []
    for query, label, person, wiki in CANDIDATES:
        if label.startswith("("):
            continue
        cands = await resolve(client, query)
        live = [c for c in cands if c["last"] is not None]
        best = max(live, key=lambda c: c["last"]) if live else None
        others = " · ".join(
            f"{c['cik']} {str(c['name'])[:28]}({c['last'] or '-'})" for c in cands if c is not best
        )
        if best is None:
            lines.append(f"| {query} | — | — | — | ⛔ 13F 없음 | {others} |")
            continue
        stale = (today - best["last"]).days > STALE_DAYS
        if best["cik"] in have:
            verdict = "이미 있음"
        elif stale:
            verdict = f"⛔ 끊김({(today - best['last']).days}일)"
        else:
            verdict = "✅ 추가"
            added.append(
                {
                    "cik": best["cik"],
                    "label": label,
                    "person": person,
                    "wiki": wiki if wiki is not None else person,
                    "edgar": best["name"],
                }
            )
        lines.append(
            f"| {query} | {best['cik']} | {best['name']} | {best['last']} | {verdict} | {others} |"
        )
        print(
            f"{query:34s} → {best['cik']} {str(best['name'])[:36]:36s} {best['last']} {verdict}",
            flush=True,
        )
    lines += ["", f"기존 {len(have)} + 추가 {len(added)} = {len(have) + len(added)}"]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n기존 {len(have)} + 추가 {len(added)} = {len(have) + len(added)} → {OUT}")
    if dry:
        return 0
    text = CONFIG.read_text(encoding="utf-8")
    head, _, tail = text.partition("managers:\n")
    rows_end = tail.index("\n# 13F 에는 티커가 없다")
    rows = tail[:rows_end].rstrip("\n")
    extra = "\n".join(
        f'  - {{ cik: "{a["cik"]}", label: "{a["label"]}", person: "{a["person"]}"'
        + (f', wiki: "{a["wiki"]}"' if a["wiki"] != a["person"] else "")
        + " }"
        for a in added
    )
    new_text = (
        head
        + "managers:\n"
        + rows
        + "\n  # ── 큐레이션 2차(whalesurfer/scripts/whalesurfer_curate.py · EDGAR 이름 검색 → 최근 13F-HR 가장 늦은 CIK · 표 logs/t279/t442/curation.md) ──\n"
        + extra
        + "\n"
        + tail[rows_end:]
    )
    CONFIG.write_text(new_text, encoding="utf-8")
    print("managers.yml 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
