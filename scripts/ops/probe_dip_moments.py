"""진입 뒤 **손실로 보였던 순간**이 있었나 — 1분봉 저가로 되짚는다 (공개 봉 · 키 없음).

    uv run python scripts/ops/probe_dip_moments.py

사용자: *"뭐가 잠깐 손실이었는데 다시 이득으로 돌아간 것 같긴 하네. 아마 아주 빠르게
장대 음봉 꼬리가 생긴 게 아닐까."*

화면은 **미실현 손익률(증거금 대비 · 배율 반영)** 을 보여 준다. 배율이 4~6배라 가격이
0.2% 만 밀려도 화면에는 -1% 가 찍힌다 — "손절한 것 같다" 로 보일 만한 크기다.
그래서 5분봉이 아니라 **1분봉**으로, 진입가 아래로 내려간 순간을 전부 찾는다.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime

# 실계좌 원장의 보유 4건 — 거래소 체결가(실제 진입)를 쓴다. 화면의 손익률이 이 값을 쓴다.
HELD = [
    ("BTC_USDT", 83718.5, 4.46, "2026-09-21T08:50:00"),
    ("SOL_USDT", 115.32, 6.00, "2026-09-21T08:50:00"),
    ("XRP_USDT", 1.4764, 4.00, "2026-09-21T08:50:00"),
    ("DOGE_USDT", 0.09186, 4.00, "2026-09-21T08:55:00"),
]


def bars(sym: str, frm: int) -> list[dict]:
    url = (
        "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        f"?contract={sym}&interval=1m&from={frm}"
    )
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read())


def main() -> None:
    print("진입 뒤 **1분봉 저가**로 되짚은 '화면에 손실로 보였을' 순간 (배율 반영)\n")
    print("| 종목 | 진입(체결) | 최저 저가 | 그때 화면 손익 | 시각(UTC) | 손실이던 분 |")
    print("|---|---|---|---|---|---|")
    worst_all: tuple[float, str, str] = (0.0, "", "")
    for sym, entry, lev, opened in HELD:
        frm = int(datetime.fromisoformat(opened).replace(tzinfo=UTC).timestamp())
        rows = bars(sym, frm)
        if not rows:
            print(f"| {sym[:-5]} | 봉을 못 받았다 | | | | |")
            continue
        worst = min(rows, key=lambda r: float(r["l"]))
        low = float(worst["l"])
        shown = (low - entry) / entry * 100 * lev
        when = datetime.fromtimestamp(int(worst["t"]), UTC)
        red = sum(1 for r in rows if float(r["l"]) < entry)
        print(
            f"| {sym[:-5]} | {entry:,.6g} | {low:,.6g} | **{shown:+.2f}%** | "
            f"{when:%m-%d %H:%M} | {red}분 / {len(rows)}분 |"
        )
        if shown < worst_all[0]:
            worst_all = (shown, sym, f"{when:%m-%d %H:%M}")
    if worst_all[1]:
        print(
            f"\n가장 붉게 보였을 순간: **{worst_all[1][:-5]} {worst_all[0]:+.2f}%** "
            f"({worst_all[2]} UTC · {int(worst_all[2][6:8]) + 9:02d}시대 KST)"
        )
    print("\n⚠️ 1분봉 저가라 **실제로 화면에 그 값이 찍혔는지**는 폴링 주기(4초)에 달렸다 —")
    print("   꼬리가 1분 안에 왔다 가면 화면이 그 값을 한 번도 안 그릴 수도 있다.")


main()
