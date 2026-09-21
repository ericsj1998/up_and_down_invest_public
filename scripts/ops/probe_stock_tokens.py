"""토큰화 주식 계약이 Gate 에 **지금도 있나** (공개 API · 키 없음).

    uv run python scripts/ops/probe_stock_tokens.py

사용자: *"애초에 TSLAX 데이터를 못받아오는데, 매매가 가능하긴 한거야? 이게 이해가 전혀 안돼."*

순위 창의 목록(`exchange.py TRACKED`)에 토큰화 주식 넷이 있고, 그중 일부가 "확인 불가 · 없다"
로 뜬다. 그 행이 **우리 문제인지 거래소 문제인지**를 가른다 — 원인이 다르면 처방도 다르다:

    거래소에 계약이 없다  → 목록에서 빼는 것이 맞다 (우리가 고칠 게 없다)
    계약은 있는데 우리가 못 읽는다 → 지우면 증상만 감추는 것이다
"""

from __future__ import annotations

import json
import urllib.request

# 순위 창이 보는 목록 (exchange.py TRACKED) — 코인 다섯 + 토큰화 주식 넷.
TRACKED = (
    "BTC_USDT",
    "ETH_USDT",
    "SOL_USDT",
    "XRP_USDT",
    "DOGE_USDT",
    "TSLAX_USDT",
    "SPCX_USDT",
    "SNDK_USDT",
    "SKHY_USDT",
)


def contracts() -> dict[str, dict]:
    """Gate USDT 무기한 계약 전부 — 이름 → 계약."""
    url = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
    with urllib.request.urlopen(url, timeout=30) as resp:
        rows = json.loads(resp.read())
    return {str(r.get("name", "")): r for r in rows}


def main() -> None:
    live = contracts()
    print(f"Gate USDT 무기한 계약 **{len(live)}개** 중에서 찾는다\n")
    print("| 종목 | 거래소에 있나 | 상태 | 24h 거래대금 | 비고 |")
    print("|---|---|---|---|---|")
    missing: list[str] = []
    for name in TRACKED:
        row = live.get(name)
        if row is None:
            missing.append(name)
            print(f"| {name} | ⛔ **없다** | — | — | 이 계약이 사라졌다 |")
            continue
        vol = row.get("trade_size") or row.get("position_size") or "—"
        status = "거래정지" if row.get("in_delisting") else "정상"
        print(f"| {name} | ✅ 있다 | {status} | {vol} | {row.get('type', '')} |")

    if missing:
        print(f"\n🔴 **없는 계약 {len(missing)}개**: {', '.join(missing)}")
        print("   → 우리가 못 읽는 것이 아니라 **거래소에 그 계약이 없다**. 매매도 불가능하다.")

    # ── 주식추종 토큰 찾기 (사용자 요구: "META 구글 애플 뭐 이런 거 추종하는 USDT" 탭) ──
    #
    # 🔴 이름으로 추측하면 틀린다 — `AVAX_USDT`·`DYDX_USDT` 도 X 로 끝나지만 코인이다.
    #    그래서 **아는 주식 티커 목록**과 맞춰 본다. 맞는 것만 주식으로 친다.
    print("\n=== 주식추종 토큰 후보 (아는 티커와 맞춘 것만)")
    tickers = {
        "AAPL": "애플",
        "MSFT": "마이크로소프트",
        "GOOGL": "알파벳",
        "GOOG": "알파벳",
        "AMZN": "아마존",
        "META": "메타",
        "TSLA": "테슬라",
        "NVDA": "엔비디아",
        "NFLX": "넷플릭스",
        "AMD": "AMD",
        "INTC": "인텔",
        "COIN": "코인베이스",
        "MSTR": "마이크로스트래티지",
        "HOOD": "로빈후드",
        "PLTR": "팔란티어",
        "SPY": "S&P500 ETF",
        "QQQ": "나스닥100 ETF",
        "SPX": "S&P500",
        "SOXX": "반도체 ETF",
        "GDX": "금광 ETF",
        "TQQQ": "나스닥100 3배",
        "RTX": "RTX",
        "TJX": "TJX",
        "VRTX": "버텍스",
        "BSX": "보스턴사이언티픽",
        "FCX": "프리포트",
        "CGNX": "코그넥스",
        "LRCX": "램리서치",
        "SPCE": "버진갤럭틱",
        "SPCX": "스페이스X(비상장)",
        "SNDK": "샌디스크",
        "SKHY": "SK하이닉스",
        "SKHYNIX": "SK하이닉스",
        "TSLAX": "테슬라",
    }
    found_stocks: list[tuple[str, str]] = []
    for name in sorted(live):
        base = name.removesuffix("_USDT")
        for suffix in ("X", ""):
            core = base.removesuffix(suffix) if suffix else base
            label = tickers.get(core)
            if label is not None:
                found_stocks.append((name, f"{label} ({core})"))
                break
    for name, label in found_stocks:
        print(f"  {name:16s} {label}")
    print(f"  → {len(found_stocks)}개")
    print("\n⚠️ 이 목록은 **후보**다. 같은 글자가 코인 이름일 수 있으므로(SPX·GDX 등),")
    print("   탭에 넣기 전에 사람이 한 번 본다.")


main()
