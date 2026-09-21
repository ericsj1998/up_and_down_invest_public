"""보유 4건이 **언제 팔리나** — 청산선(1H SMA20)까지 얼마나 남았나 (공개 봉 · 키 없음).

    uv run python scripts/ops/probe_exit_line.py

사용자: *"라이브 들어간 종목들은 도통 익절을 잘 안 하네."*

룰 0.3(`private_strategy`)은 `full_ride: true` — **고정 익절선이 없다.** 나가는 문은 둘뿐이다:

    ① 손절 (`stop_mode: touch`) — 진입 봉 저가 - 0.2 ATR
    ② 청산 (`ma_exit_below_long: 20`) — **1H 종가가 SMA20 아래로 마감**

화면의 '목표'(BTC 2,186,010 같은 값)는 100R 자리표시자지 익절선이 아니다. 그래서
"익절을 안 한다" 가 아니라 "SMA20 아래로 마감할 때까지 들고 간다" 가 맞다. 그 선이
지금 어디인지 찍는다 — 그래야 *언제* 팔릴지가 보인다.
"""

from __future__ import annotations

import json
import statistics as st
import urllib.request

HELD = [
    # 종목, 진입(체결), 손절선, 배율
    ("BTC_USDT", 83718.5, 81597.789296, 4.46),
    ("SOL_USDT", 115.32, 111.827304, 6.00),
    ("XRP_USDT", 1.4764, 1.434549, 4.00),
    ("DOGE_USDT", 0.09186, 0.089433, 4.00),
]


def bars(sym: str) -> list[dict]:
    url = (
        "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        f"?contract={sym}&interval=1h&limit=40"
    )
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read())


def main() -> None:
    print("룰 0.3 은 고정 익절선이 없다 — 나가는 문은 **손절** 과 **1H 종가 < SMA20** 둘뿐\n")
    print("| 종목 | 진입 | 지금가 | 지금 손익 | **청산선(SMA20)** | 청산까지 | 손절선 | 손절까지 |")
    print("|---|---|---|---|---|---|---|---|")
    for sym, entry, stop, lev in HELD:
        rows = bars(sym)
        closes = [float(r["c"]) for r in rows]
        last = closes[-1]
        # 🔴 마지막 봉은 아직 안 닫혔다 — 청산 판정은 **닫힌 봉**의 종가로 한다.
        sma = st.fmean(closes[-21:-1])
        pct = (last - entry) / entry * 100 * lev
        to_exit = (last - sma) / last * 100
        to_stop = (last - stop) / last * 100
        print(
            f"| {sym[:-5]} | {entry:,.6g} | {last:,.6g} | **{pct:+.2f}%** | {sma:,.6g} | "
            f"{to_exit:+.2f}% | {stop:,.6g} | {to_stop:+.2f}% |"
        )
    print("\n⚠️ '청산까지' 는 **지금 가격이 SMA20 위로 몇 % 인가**다.")
    print("   1H 봉이 그 아래로 *마감*해야 팔린다 — 봉 안에서 잠깐 내려가는 것은 청산이 아니다.")
    print("   (손절선은 다르다 — 닿기만 해도 나간다.)")


main()
