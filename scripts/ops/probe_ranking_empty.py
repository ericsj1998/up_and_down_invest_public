"""종목 순위가 왜 비었나 — 엔드포인트가 실제로 무엇을 돌려주는지 (읽기 전용 · api 컨테이너 안).

    bash scripts/ops/remote.sh scripts/ops/probe_ranking_empty.py      (실계좌 서버)
    docker exec updown-api-1 python /app/scripts/ops/probe_ranking_empty.py   (로컬)

화면이 머리글만 남고 줄이 하나도 없다(2026-09-22). 후보 둘을 가른다:
  ① Gate 조회 연결이 없어 서버가 **빈 표 + note** 를 돌려준다 (화면이 note 를 안 그린다)
  ② 무언가 터져 500 이 나간다
"""

import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def main() -> None:
    # ⚠️ 컨테이너 안에서는 `/api` 접두사가 없다 — nginx 가 붙여 준다. 둘 다 시도한다.
    for path in (
        "/exchange/ranking",
        "/api/exchange/ranking",
        "/exchange/symbols",
        "/api/exchange/symbols",
    ):
        print(f"=== {path}")
        try:
            with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as resp:
                body = json.loads(resp.read())
        except Exception as exc:
            print("  못 읽음:", type(exc).__name__, str(exc)[:200])
            continue
        rows = body.get("rows", [])
        print(f"  줄 {len(rows)}개 · note={body.get('note')!r}")
        for row in rows[:12]:
            if not isinstance(row, dict):
                continue
            if "missing" in row:
                print(f"    {row.get('symbol'):14s} ⛔ {row['missing']}")
            else:
                keys = [k for k in ("price", "turnover", "volatility", "spread") if k in row]
                shown = " ".join(f"{k}={row[k]}" for k in keys)
                print(f"    {row.get('symbol'):14s} {shown}")

    print("\n=== 이 API 가 보는 시장들")
    try:
        from updown.marketdata.provider import MarketDataProvider

        print("  live_markets:", MarketDataProvider().live_markets())
    except Exception as exc:
        print("  못 읽음:", type(exc).__name__, str(exc)[:160])


main()
