"""보유 중인 매매가 손절선에 얼마나 다가갔나 — 진입 뒤 실제 저가로 잰다 (공개 봉 · 키 없음).

    uv run python scripts/ops/probe_stop_distance.py

"손절된 것 같다" 는 느낌은 보통 **손절 날 뻔한 순간**에서 온다. 원장·거래소가 둘 다
"닫힌 것 없음" 이라고 할 때, 남은 질문은 *"그럼 얼마나 가까웠나"* 다. 그것만 답한다.

🔴 값은 로컬에서 Gate 공개 API 로 받는다 — 실계좌 키를 쓰지 않는다.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime

# 실계좌 원장에서 읽은 보유 4건 (2026-09-21 · probe_recent_closes.sh).
HELD = [
    # 종목, 진입가, 계획 손절, 의도 배율, 증거금 USDT, 진입 시각(UTC)
    ("BTC_USDT", 83757.30, 81597.789296, 4.46, 62.247, "2026-09-21T08:50:00"),
    ("SOL_USDT", 115.58, 111.827304, 6.00, 62.247, "2026-09-21T08:50:00"),
    ("XRP_USDT", 1.4789, 1.434549, 4.00, 62.247, "2026-09-21T08:50:00"),
    ("DOGE_USDT", 0.09186, 0.089433, 4.00, 62.247, "2026-09-21T08:55:00"),
]


def bars(sym: str, frm: int) -> list[dict]:
    url = (
        "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        f"?contract={sym}&interval=5m&from={frm}"
    )
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read())


def main() -> None:
    now = datetime.now(UTC)
    print(f"지금 {now:%Y-%m-%d %H:%M} UTC · 진입 뒤 5분봉 저가로 잰다\n")
    print("| 종목 | 진입 | 손절선 | 진입 뒤 최저 | 최저-손절 여유 | 지금가 | 지금 손익 | 금액 |")
    print("|---|---|---|---|---|---|---|---|")
    total = 0.0
    worst: tuple[float, str] = (1e9, "")
    for sym, entry, stop, lev, margin, opened in HELD:
        frm = int(datetime.fromisoformat(opened).replace(tzinfo=UTC).timestamp())
        rows = bars(sym, frm)
        lows = [float(r["l"]) for r in rows]
        last = float(rows[-1]["c"])
        low = min(lows) if lows else entry
        room = (low - stop) / stop * 100  # 손절선 대비 여유 (%)
        pct = (last - entry) / entry * 100 * lev  # 배율 얹은 손익률
        usdt = margin * pct / 100
        total += usdt
        if room < worst[0]:
            worst = (room, sym)
        print(
            f"| {sym[:-5]} | {entry:,.5g} | {stop:,.5g} | {low:,.5g} | "
            f"**{room:+.2f}%** | {last:,.5g} | {pct:+.2f}% | {usdt:+.2f} USDT |"
        )
    print(f"\n합계 평가손익 **{total:+.2f} USDT**")
    print(f"손절선에 가장 가까웠던 것: **{worst[1][:-5]}** (최저가가 손절선보다 {worst[0]:+.2f}%)")
    print("\n⚠️ 5분봉 저가 기준이다 — 봉 안에서 더 내려갔다 올라왔다면 이 표가 살짝 낙관이다.")


main()
