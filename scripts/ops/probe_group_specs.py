"""묶음 표의 주식·지수 추종 계약 — **거래소 명세 실측** (공개 API · 키 없음).

대상 = `config/symbol_groups.yml` 에 선언된 Gate 계약 전부.

    uv run python scripts/ops/probe_group_specs.py

판을 띄우려면 호가 눈금이 `config/costs.yml` 의 `spec_ticks` 에 있어야 한다. 그 파일은
*"눈금을 실측하지 않고 옮겨 적지 않는다"* 고 적어 두었다 — 이 도구가 그 실측이다. 같이 본다:

    있나          거래소가 그 계약을 지금 주나 (없으면 묶음 표에서 빼야 한다)
    눈금          order_price_round — spec_ticks 에 적을 값
    계약 크기     quanto_multiplier · 최소 주문 수
    수수료        taker_fee_rate — 코인(0.05%)과 다른가
    배율 상한     leverage_max — 1.0.0 의 돌파 다리는 4x 다
    거래대금      24시간 (USDT) — 얇으면 나갈 수 없다
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from updown.common.symbol_groups import load_symbol_groups  # noqa: E402

BASE = "https://api.gateio.ws/api/v4/futures/usdt"


def get(path: str) -> object:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    table = load_symbol_groups()
    wanted = dict(table.markets.get("GATE", {}))
    tickers = {str(row["contract"]): row for row in get("/tickers")}  # type: ignore[union-attr]
    print(
        "| 계약 | 묶음 | 있나 | 눈금 | 계약 크기 | 최소 | 테이커 | 배율 상한 | 24h 거래대금(USDT) |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for symbol in sorted(wanted, key=lambda s: (wanted[s].group, s)):
        info = wanted[symbol]
        try:
            spec = get(f"/contracts/{symbol}")
        except Exception as exc:
            print(f"| {symbol} | {info.group} | ⛔ 없다 ({str(exc)[:40]}) | | | | | | |")
            continue
        assert isinstance(spec, dict)
        tick = tickers.get(symbol, {})
        turnover = float(tick.get("volume_24h_quote") or 0)
        state = "거래 중" if not spec.get("in_delisting") else "⚠️ 상폐 중"
        taker = float(spec.get("taker_fee_rate") or 0) * 100
        print(
            f"| {symbol} | {info.group} | {state} | {spec.get('order_price_round')} | "
            f"{spec.get('quanto_multiplier')} | {spec.get('order_size_min')} | {taker:.3f}% | "
            f"{spec.get('leverage_max')}x | {turnover:,.0f} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
