#!/usr/bin/env bash
# wallet_drift 가 언제부터 · 어떤 값으로 났나 — api 로그(값만 · 키 없음) + 거래소 계정(총액 · 가용 · 증거금 · 미실현 · 포지션)
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API · wallet_drift 처음 · 마지막 · 수(48h)"
docker logs --since 48h "$API" 2>&1 | grep "wallet_drift" | grep -oE '"timestamp": "[^"]+"|원장 합 [0-9.]+ > 계정 총액 [0-9.]+ \([0-9.]+% 초과\)' | paste - - | sed -n '1p;$p'
docker logs --since 48h "$API" 2>&1 | grep -c "wallet_drift"
echo "=== 직전 24h 자가 점검에서 wallet_drift 가 없던 마지막 시각"
docker logs --since 48h "$API" 2>&1 | grep -E "self_check|자가 점검" | grep -v wallet_drift | grep -oE '"timestamp": "[^"]+"' | tail -1
echo "=== 거래소 계정(값만)"
docker exec "$API" python - <<'EOF' 2>/dev/null | grep -v registry
import asyncio
from updown.common.config import load_settings
from updown.marketdata.provider import MarketDataProvider
from updown.common.domain.instrument import Market
async def main():
    p = MarketDataProvider()
    a = p.adapter_for(Market.GATE)
    m = await a.margins()
    print("margins:", {k: (round(float(v), 2) if isinstance(v, (int, float)) or hasattr(v, 'quantize') else v) for k, v in (m.items() if isinstance(m, dict) else vars(m).items())})
    try:
        ps = await a.positions()
        for x in ps:
            d = vars(x) if hasattr(x, "__dict__") else x
            print("pos:", {k: str(v)[:18] for k, v in d.items() if k in ("symbol","side","size","entry","mark","unrealised","unrealized_pnl","margin","leverage","value")})
    except Exception as e:
        print("positions err", type(e).__name__)
asyncio.run(main())
EOF
