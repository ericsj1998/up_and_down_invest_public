"""주식추종 토큰으로 **백테스트가 가능한가** — 봉이 언제부터 있나 (공개 API · 키 없음).

    uv run python scripts/ops/probe_stock_token_history.py

사용자: *"코인만 딱 잡는 것보다 … 저런것들로 해보는 건 어때? 코인을 좀 헷징하는거지."*

재보자고 답하기 전에 **잴 수 있는지**부터 본다. T283 호가 트랙에서 배운 것이 이것이다 —
표본이 모자란 자료로 시작하면 몇 주를 쓰고 "판정 불가" 로 끝난다.

재는 것:
  ① 1H 봉이 **언제부터** 있나 (= 백테스트 가능 구간)
  ② 24h 거래대금 — 얇으면 비용이 먹는다
  ③ 숏·역방향 상품이 있나 (사용자: *"숏 추종 기반 탭"*)
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime

# 사용자가 고른 (a) — 빅테크 + 크립토주. 지수·ETF 는 따로 본다.
BIGTECH = (
    "AAPL_USDT",
    "MSFT_USDT",
    "GOOGL_USDT",
    "AMZN_USDT",
    "META_USDT",
    "NVDA_USDT",
    "TSLA_USDT",
    "NFLX_USDT",
    "AMD_USDT",
    "INTC_USDT",
    "COIN_USDT",
    "MSTR_USDT",
    "HOOD_USDT",
    "PLTR_USDT",
)
INDEX = ("SPY_USDT", "QQQ_USDT", "SPX_USDT", "SOXX_USDT", "GDX_USDT", "TQQQX_USDT")
# 🔴 **역방향 ETF** — 사용자: *"숏 추종 기반 탭도 있으면 좋겠네. 결국 우리가 롱을 더 잘버니까."*
#    SQQQ = 나스닥100 3배 역방향 · TZA = 소형주 3배 역방향. 롱 전용 룰로 하락을 먹는 길이다.
INVERSE = ("SQQQ_USDT", "TZA_USDT")
# 비교용 — 지금 쓰는 핵심 6종 중 둘.
COINS = ("BTC_USDT", "DOGE_USDT")


def get(url: str) -> list:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def first_bar(sym: str) -> datetime | None:
    """가장 오래된 봉 = 상장일.

    ⚠️ Gate 는 `from` 과 `limit` 을 **같이 주면 400** 이다(실측 2026-09-22). `limit` 만
    주고 **일봉**으로 받는다 — 2,000 일이면 어떤 계약이든 상장일까지 닿는다.
    """
    rows = get(
        "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        f"?contract={sym}&interval=1d&limit=2000"
    )
    if not rows:
        return None
    return datetime.fromtimestamp(int(rows[0]["t"]), UTC)


def main() -> None:
    live = {
        str(r.get("name", "")): r
        for r in get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    }
    # 🔴 계약 표의 `trade_size` 는 **계약 수 누적**이라 종목 간 비교가 안 된다(승수가 다르다).
    #    호가표(tickers)의 24h **USDT 거래대금**을 쓴다 — 이것이 "얼마나 두꺼운가" 의 자다.
    ticks = {
        str(r.get("contract", "")): r
        for r in get("https://api.gateio.ws/api/v4/futures/usdt/tickers")
    }
    now = datetime.now(UTC)
    print("| 종목 | 첫 1H 봉 | 걸을 수 있는 기간 | 24h 거래대금 | 배율 상한 |")
    print("|---|---|---|---|---|")
    for group, names in (
        ("주식", BIGTECH),
        ("지수·ETF", INDEX),
        ("역방향(롱으로 하락 먹기)", INVERSE),
        ("코인(비교)", COINS),
    ):
        print(f"| **{group}** | | | | |")
        for sym in names:
            row = live.get(sym)
            if row is None:
                print(f"| {sym} | ⛔ 계약 없다 | — | — | — |")
                continue
            try:
                first = first_bar(sym)
            except Exception as exc:
                print(f"| {sym} | 못 읽음 {str(exc)[:40]} | — | — | — |")
                continue
            if first is None:
                print(f"| {sym} | 봉이 없다 | — | — | — |")
                continue
            days = (now - first).days
            flag = " 🔴" if days < 400 else (" ⚠️" if days < 900 else "")
            quote = ticks.get(sym, {}).get("volume_24h_quote")
            try:
                turnover = f"{float(quote):,.0f}" if quote is not None else "—"
            except (TypeError, ValueError):
                turnover = "—"
            print(
                f"| {sym} | {first:%Y-%m-%d} | **{days}일**{flag} | {turnover} | "
                f"{row.get('leverage_max', '—')}x |"
            )

    print('\n=== 숏·역방향 상품이 있나 (사용자: "숏 추종 기반 탭")')
    # Gate 의 레버리지 토큰은 보통 3L/3S 로 끝난다. 무기한 계약 목록에서 찾아본다.
    short_like = sorted(
        n
        for n in live
        if n.endswith(("3S_USDT", "5S_USDT", "S_USDT"))
        and any(c.isdigit() for c in n.removesuffix("_USDT"))
    )
    inverse = sorted(n for n in live if n.startswith(("SQQQ", "SPXS", "SDOW", "TZA")))
    print(f"  레버리지 숏 토큰 꼴: {', '.join(short_like) if short_like else '없다'}")
    print(f"  역방향 ETF 꼴: {', '.join(inverse) if inverse else '없다'}")
    print(
        "\n⚠️ 무기한 계약은 **그 자체로 숏이 된다** — 역방향 상품이 없어도\n"
        "   숏 다리로 같은 일을 한다. 다만 룰 0.3(A) 은 롱 전용이고,\n"
        "   숏은 T290 삼각수렴 다리가 맡고 있다."
    )


main()
