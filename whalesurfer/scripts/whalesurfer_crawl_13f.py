"""WhaleSurfer 13F 역사 적재 (T443 재료 · 2026-10-09) — 추적 보고자 전부의 13F-HR 을 2013 년부터 끝까지 받아 파일로 둔다.

- 보고자마다 `cache/whalesurfer/13f/{cik}.json`: `{cik, entity, reports: [{accession, filed, period, total_value_usd, holdings: [{cusip, issuer, value_usd, shares, put_call}]}]}`
  (같은 CUSIP · Put/Call 줄은 합친다 · 새것부터). 이미 있고 `--force` 가 아니면 건너뛴다(이어 돌리기).
- 끝나면 보유 CUSIP 전부를 모아 OpenFIGI 로 티커를 찾는다(키 없이 분당 25 요청 · `cache/whalesurfer/cusip_figi.json`).
- EDGAR 는 초당 10 요청(클라이언트가 지킨다). 진행은 stderr 로.

    set -a; . ./.env.dev; set +a; uv run --no-sync python whalesurfer/scripts/whalesurfer_crawl_13f.py [--force] [--no-figi] [--since 2013-01-01]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from updown.common.config import load_settings
from updown.marketdata.provider import edgar_client
from whalesurfer.api.routes import merge_rows
from whalesurfer.edgar.figi import FigiClient
from whalesurfer.edgar.thirteen_f import ThirteenFError, recent_reports

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "whalesurfer" / "managers.yml"
OUT = ROOT / "cache" / "whalesurfer" / "13f"
FIGI_CACHE = ROOT / "cache" / "whalesurfer" / "cusip_figi.json"
MAX_QUARTERS = 60


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


async def crawl_one(client: Any, cik: str, since: date, force: bool) -> tuple[int, set[str]]:
    path = OUT / f"{cik}.json"
    if path.exists() and not force:
        d = json.loads(path.read_text(encoding="utf-8"))
        cus = {h["cusip"] for r in d["reports"] for h in r["holdings"]}
        return len(d["reports"]), cus
    # strict=False — 2013-06 전 텍스트 13F(XML 없음)는 건너뛴다. SEC 의 XML 정보표는 2013 Q2 보고부터다.
    reports = await recent_reports(client, cik, MAX_QUARTERS, deep=True, strict=False)
    rows: list[dict[str, Any]] = []
    cus: set[str] = set()
    for r in reports:
        if r.filed < since:
            continue
        holdings = [
            {
                "cusip": h["cusip"],
                "issuer": h["issuer"],
                "value_usd": h["value_usd"],
                "shares": h["shares"],
                "put_call": h["put_call"],
            }
            for h in merge_rows(r.holdings)
        ]
        cus |= {h["cusip"] for h in holdings}
        rows.append(
            {
                "accession": r.accession,
                "filed": r.filed.isoformat(),
                "period": r.period.isoformat() if r.period else None,
                "total_value_usd": r.total_value_usd,
                "holdings": holdings,
            }
        )
    entity = reports[0].entity if reports else cik
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cik": cik, "entity": entity, "reports": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(rows), cus


async def main() -> int:
    force = "--force" in sys.argv
    no_figi = "--no-figi" in sys.argv
    since = (
        date.fromisoformat(sys.argv[sys.argv.index("--since") + 1])
        if "--since" in sys.argv
        else date(2013, 7, 1)  # SEC XML 정보표는 2013 Q2 보고(2013-08 접수)부터
    )
    managers = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["managers"]
    client = edgar_client(load_settings())
    t0 = time.time()
    union: set[str] = set()
    failures: list[tuple[str, str]] = []
    for i, m in enumerate(managers, 1):
        cik = str(m["cik"]).zfill(10)
        try:
            n, cus = await crawl_one(client, cik, since, force)
        except (ThirteenFError, Exception) as exc:
            failures.append((cik, str(exc)[:120]))
            log(f"🔴 {i}/{len(managers)} {cik} {m.get('label')} 실패 {str(exc)[:80]}")
            continue
        union |= cus
        log(
            f"{i}/{len(managers)} {cik} {str(m.get('label'))[:30]:30s} 보고 {n:3d} · 누적 CUSIP {len(union):6d} · {time.time() - t0:5.0f}s"
        )
    log(
        f"13F 끝 · 보고자 {len(managers) - len(failures)}/{len(managers)} · CUSIP {len(union)} · 실패 {len(failures)}"
    )
    (OUT.parent / "crawl_summary.json").write_text(
        json.dumps(
            {
                "managers": len(managers),
                "failures": failures,
                "cusips": len(union),
                "since": since.isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if no_figi:
        return 0
    figi = FigiClient(FIGI_CACHE)
    todo = [c for c in union if c not in figi.cache]
    log(
        f"OpenFIGI · 모를 CUSIP {len(todo)} / {len(union)} · 키 없이 분당 250건 → 약 {len(todo) / 250:.0f}분"
    )
    got = await figi.map_cusips(union)
    hit = sum(1 for v in got.values() if v.get("ticker"))
    log(f"OpenFIGI 끝 · 티커 있음 {hit} / {len(got)}")
    print("CRAWL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
