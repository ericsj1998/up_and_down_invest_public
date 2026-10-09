"""T451 재료 받기 — SEC 내부자 거래 자료 묶음(Form 3/4/5 · 분기 ZIP) · EDGAR 전체 색인(분기 master.gz) (2026-10-10).

- 2013Q3 ~ 2026Q3 · 이미 받은 파일은 건너뛴다(이어 받기) · 요청 사이 0.3초.
- 저장 `cache/whalesurfer/form345/{yyyy}q{n}_form345.zip` · `cache/whalesurfer/fullindex/{yyyy}Q{n}_master.gz`.
- SEC 공개 자료(www.sec.gov) — 실계좌 서버 · 토스 프록시와 무관. 획득 지점 `provider.edgar_client`(HTTP/2 + 브라우저 헤더).

    set -a; . ./.env.dev; set +a; uv run --no-sync python whalesurfer/scripts/t451_fetch.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

from updown.common.config import load_settings
from updown.marketdata.fundamentals.client import EdgarApiError, UnknownEntityError
from updown.marketdata.provider import edgar_client

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache" / "whalesurfer"
F345 = CACHE / "form345"
FIDX = CACHE / "fullindex"
PAGE = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
IDX_URL = "https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/master.gz"
FIRST = (2013, 3)
LAST = (2026, 3)
PAUSE = 0.3


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


def quarters() -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, q = FIRST
    while (y, q) <= LAST:
        out.append((y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


async def main() -> int:
    F345.mkdir(parents=True, exist_ok=True)
    FIDX.mkdir(parents=True, exist_ok=True)
    client = edgar_client(load_settings())
    html = await client.get_text(PAGE)
    links = {
        m.group(2): m.group(1) for m in re.finditer(r'href="([^"]*/(\d{4}q\d)_form345\.zip)"', html)
    }
    t0 = time.time()
    got = skipped = failed = 0
    total_bytes = 0
    for y, q in quarters():
        key = f"{y}q{q}"
        jobs = []
        rel = links.get(key)
        if rel:
            jobs.append((F345 / f"{key}_form345.zip", f"https://www.sec.gov{rel}"))
        else:
            log(f"🔴 {key} 내부자 묶음 링크 없음")
            failed += 1
        jobs.append((FIDX / f"{y}Q{q}_master.gz", IDX_URL.format(y=y, q=q)))
        for path, url in jobs:
            if path.exists() and path.stat().st_size > 0:
                skipped += 1
                continue
            try:
                body = await client.get_bytes(url)
            except (EdgarApiError, UnknownEntityError) as exc:
                log(f"🔴 {path.name} 실패 {str(exc)[:80]}")
                failed += 1
                continue
            path.write_bytes(body)
            got += 1
            total_bytes += len(body)
            await asyncio.sleep(PAUSE)
        log(
            f"{key} · 받음 {got} · 건너뜀 {skipped} · 실패 {failed} · {total_bytes / 1e6:.0f} MB · {time.time() - t0:.0f}s"
        )
    log(f"끝 · 받음 {got} · 건너뜀 {skipped} · 실패 {failed} · {total_bytes / 1e6:.0f} MB")
    print("FETCH_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
