"""종목 순위를 **프로세스 안에서** 직접 부른다 (읽기 전용).

서버는 로그인을 요구해 HTTP 로 부르면 401 이다 — 그래서 함수를 직접 부른다.

    bash scripts/ops/remote.sh scripts/ops/probe_ranking_inproc.py

⚠️ 새 프로세스라 펀드·판 등록부(`FUNDS` · `LIVE_RUNNERS`)가 비어 있다 — 종목 우주는 **눈금 선언**
만으로 나온다(도는 API 에서는 펀드 18종이 더해진다). 여기서 보는 것은 거래소 경로다:
연결된 거래소를 스스로 고르나 · 전 종목 요약이 중립 자료형으로 오나 · 호가 판정이 되나.
"""

import asyncio

from updown.apps.api.exchange import ranking, symbols


async def main() -> None:
    board = await ranking()
    rows = board.get("rows", [])
    print(f"ranking: 거래소={board.get('market')} · 줄 {len(rows)} · note={board.get('note')!r}")
    for row in rows:
        if "missing" in row:
            print(f"   {row['symbol']:13s} ⛔ {row['missing']}")
            continue
        got = row.get("exit") or {}
        turnover = row.get("turnover")
        print(
            f"   {row['symbol']:13s} 가격 {row.get('price')} · "
            f"거래대금 {0 if turnover is None else turnover:,.0f} · 스프레드 {row.get('spread')} · "
            f"최근1h {row.get('recent_pct')} · 띄울수있나 {row.get('tradable')} · "
            f"나갈수있나 read={got.get('read')} ok={got.get('ok')}"
        )
    picks = await symbols()
    print(f"symbols: 거래소={picks.get('market')} · {[r['symbol'] for r in picks.get('rows', [])]}")


asyncio.run(main())
