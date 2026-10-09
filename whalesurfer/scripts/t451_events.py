"""T451 사건 만들기 — 사전 등록 §1 그대로 (`docs/planning/tasks/T451_whalesurfer_fast_filings.md` · 2026-10-10).

Form 4(내부자 자료 묶음): 원본 4 만(4/A 제외) · 비파생 거래 · 접수일(FILING_DATE)이 사건 시각.
  F4-BUY      P(장내 매수) · 취득 A · 보고자에 임원 또는 이사 · (발행사, 접수일) 하나 = 한 사건(금액 합)
  F4-CLUSTER  F4-BUY 의 보고자 단위 매수에서 30일 안 서로 다른 3명 — 셋째 접수일 · 같은 발행사 90일 쉼
  F4-CEOCFO   F4-BUY 사건 중 매수자 직함에 CEO · Chief Executive · CFO · Chief Financial
  F4-BIG      F4-BUY 사건 중 금액(주 x 가격 합) ≥ $100,000
  F4-10PCT    P · 취득 A · 보고자가 10% 주주이고 임원 · 이사가 아님
  F4-SELL     S(장내 매도) · 처분 D · 임원 또는 이사 (진단 · 판정 밖)
13D(전체 색인 master.gz): 양식 SC 13D · SCHEDULE 13D(원본만) · 한 접수의 CIK 중 SEC 상장표에 있는 것이 대상 —
  둘 이상이면 13D 전체에서 가장 적게 나온 CIK(자주 나오는 쪽은 제출자 · 예: 아이칸 엔터프라이즈) · 같으면 뺀다.
티커 = 발행사 CIK → SEC 지금 상장표(`company_tickers_exchange.json` · Nasdaq · NYSE).

    uv run --no-sync python whalesurfer/scripts/t451_events.py
    → cache/whalesurfer/t451_events.json · logs/t279/t451/events.md
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache" / "whalesurfer"
OUT = ROOT / "logs" / "t279" / "t451"
EXCH = CACHE / "company_tickers_exchange.json"
CEO_CFO = re.compile(r"\b(CEO|CFO)\b|chief\s+executive|chief\s+financial", re.IGNORECASE)
BIG_USD = 100_000.0
CLUSTER_N = 3
CLUSTER_DAYS = 30
CLUSTER_REST = 90
F13D = {"SC 13D", "SCHEDULE 13D"}
csv.field_size_limit(10**8)


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


def tsv(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(
            io.TextIOWrapper(f, encoding="utf-8", errors="replace"),
            delimiter="\t",
            quoting=csv.QUOTE_NONE,
        )


def ddate(s: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def fnum(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def ticker_map() -> dict[int, str]:
    raw = json.loads(EXCH.read_text(encoding="utf-8"))
    f = raw["fields"]
    i_c, i_t, i_e = f.index("cik"), f.index("ticker"), f.index("exchange")
    out: dict[int, str] = {}
    for row in raw["data"]:
        if str(row[i_e]) in ("Nasdaq", "NYSE") and int(row[i_c]) not in out:
            out[int(row[i_c])] = str(row[i_t]).upper()
    return out


def form4(tick: dict[int, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    buy_ev: dict[tuple[int, date], dict[str, Any]] = {}
    pct_ev: dict[tuple[int, date], dict[str, Any]] = {}
    sell_ev: dict[tuple[int, date], dict[str, Any]] = {}
    owner_buys: dict[int, list[tuple[date, str]]] = defaultdict(list)
    stats: Counter[str] = Counter()
    for zpath in sorted((CACHE / "form345").glob("*_form345.zip")):
        z = zipfile.ZipFile(zpath)
        subs: dict[str, tuple[date, int]] = {}
        for r in tsv(z, "SUBMISSION.tsv"):
            if r.get("DOCUMENT_TYPE") != "4":
                continue
            d = ddate(r.get("FILING_DATE", ""))
            cik = r.get("ISSUERCIK", "").strip()
            if d is None or not cik.isdigit():
                continue
            subs[r["ACCESSION_NUMBER"]] = (d, int(cik))
        owners: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for r in tsv(z, "REPORTINGOWNER.tsv"):
            acc = r["ACCESSION_NUMBER"]
            if acc in subs:
                owners[acc].append(
                    (
                        r.get("RPTOWNERCIK", ""),
                        r.get("RPTOWNER_RELATIONSHIP", "") or "",
                        r.get("RPTOWNER_TITLE", "") or "",
                    )
                )
        for r in tsv(z, "NONDERIV_TRANS.tsv"):
            acc = r["ACCESSION_NUMBER"]
            code = (r.get("TRANS_CODE") or "").strip()
            if acc not in subs or code not in ("P", "S"):
                continue
            ad = (r.get("TRANS_ACQUIRED_DISP_CD") or "").strip()
            if (code == "P" and ad != "A") or (code == "S" and ad != "D"):
                continue
            d, cik = subs[acc]
            ows = owners.get(acc, [])
            rel = ",".join(o[1] for o in ows)
            insider = "Officer" in rel or "Director" in rel
            value = fnum(r.get("TRANS_SHARES", "")) * fnum(r.get("TRANS_PRICEPERSHARE", ""))
            key = (cik, d)
            if code == "P" and insider:
                ev = buy_ev.setdefault(key, {"value": 0.0, "owners": set(), "ceocfo": False})
                ev["value"] += value
                for oc, _rel, title in ows:
                    ev["owners"].add(oc)
                    if CEO_CFO.search(title):
                        ev["ceocfo"] = True
                    owner_buys[cik].append((d, oc))
                stats["p_insider_rows"] += 1
            elif code == "P" and "TenPercentOwner" in rel:
                ev = pct_ev.setdefault(key, {"value": 0.0, "owners": set(), "ceocfo": False})
                ev["value"] += value
                ev["owners"].update(o[0] for o in ows)
                stats["p_10pct_rows"] += 1
            elif code == "S" and insider:
                ev = sell_ev.setdefault(key, {"value": 0.0, "owners": set(), "ceocfo": False})
                ev["value"] += value
                ev["owners"].update(o[0] for o in ows)
                stats["s_insider_rows"] += 1
        log(
            f"{zpath.name} · 매수 사건 {len(buy_ev):,} · 10% {len(pct_ev):,} · 매도 {len(sell_ev):,}"
        )
    events: list[dict[str, Any]] = []

    def emit(rule: str, key: tuple[int, date], ev: dict[str, Any]) -> None:
        cik, d = key
        events.append(
            {
                "rule": rule,
                "cik": cik,
                "ticker": tick.get(cik),
                "filed": d.isoformat(),
                "value": round(ev["value"], 2),
                "owners": len(ev["owners"]),
            }
        )

    for key, ev in buy_ev.items():
        emit("F4-BUY", key, ev)
        if ev["ceocfo"]:
            emit("F4-CEOCFO", key, ev)
        if ev["value"] >= BIG_USD:
            emit("F4-BIG", key, ev)
    for key, ev in pct_ev.items():
        emit("F4-10PCT", key, ev)
    for key, ev in sell_ev.items():
        emit("F4-SELL", key, ev)
    # 군집 — 30일 안 서로 다른 3명 · 셋째 접수일 · 90일 쉼
    for cik, rows in owner_buys.items():
        rows.sort()
        last_event: date | None = None
        for i, (d, _oc) in enumerate(rows):
            if last_event is not None and d < last_event + timedelta(days=CLUSTER_REST):
                continue
            window = {oc for dd, oc in rows[: i + 1] if dd >= d - timedelta(days=CLUSTER_DAYS)}
            if len(window) >= CLUSTER_N:
                ev = buy_ev.get((cik, d), {"value": 0.0, "owners": window, "ceocfo": False})
                emit("F4-CLUSTER", (cik, d), {**ev, "owners": window})
                last_event = d
    return events, dict(stats)


def schedule_13d(tick: dict[int, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_acc: dict[str, list[tuple[int, date]]] = defaultdict(list)
    for gz in sorted((CACHE / "fullindex").glob("*_master.gz")):
        with gzip.open(gz, "rt", encoding="latin-1") as f:
            for line in f:
                parts = line.rstrip("\n").split("|")
                if len(parts) != 5 or parts[2] not in F13D or not parts[0].isdigit():
                    continue
                d = ddate(parts[3])
                if d is None:
                    continue
                acc = parts[4].rsplit("/", 1)[-1].removesuffix(".txt")
                by_acc[acc].append((int(parts[0]), d))
    seen = Counter(cik for rows in by_acc.values() for cik in {c for c, _d in rows})
    stats: Counter[str] = Counter()
    keys: dict[tuple[int, date], int] = {}
    for rows in by_acc.values():
        ciks = {c for c, _d in rows}
        d = rows[0][1]
        listed = [c for c in ciks if c in tick]
        if not listed:
            stats["no_listed_cik"] += 1
            continue
        if len(listed) > 1:
            listed.sort(key=lambda c: seen[c])
            if seen[listed[0]] == seen[listed[1]]:
                stats["ambiguous_skipped"] += 1
                continue
            stats["resolved_by_rarity"] += 1
        keys[(listed[0], d)] = keys.get((listed[0], d), 0) + 1
    stats["filings"] = len(by_acc)
    events = [
        {
            "rule": "13D-NEW",
            "cik": c,
            "ticker": tick.get(c),
            "filed": d.isoformat(),
            "value": 0.0,
            "owners": n,
        }
        for (c, d), n in keys.items()
    ]
    return events, dict(stats)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tick = ticker_map()
    f4, s4 = form4(tick)
    d13, s13 = schedule_13d(tick)
    events = f4 + d13
    (CACHE / "t451_events.json").write_text(json.dumps(events), encoding="utf-8")
    by = Counter(e["rule"] for e in events)
    with_t = Counter(e["rule"] for e in events if e["ticker"])
    lines = [
        "# T451 사건 수 (가격 붙이기 전)",
        "",
        f"- Form 4 행: {s4}",
        f"- 13D: {s13}",
        "",
        "| 규칙 | 사건 | 지금 상장 티커 있음 |",
        "|---|---|---|",
    ]
    for rule in ("F4-BUY", "F4-CLUSTER", "F4-CEOCFO", "F4-BIG", "F4-10PCT", "13D-NEW", "F4-SELL"):
        lines.append(
            f"| {rule} | {by[rule]:,} | {with_t[rule]:,}({with_t[rule] / max(1, by[rule]) * 100:.0f}%) |"
        )
    (OUT / "events.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("EVENTS_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
