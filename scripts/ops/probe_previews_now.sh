#!/usr/bin/env bash
# 지금 예비 신호 · 시장 폭 (읽기 전용 · 2026-09-30) — 펀드 현황 API 는 401 이라 로그(preview_found · run→종목) + 게이트 공개 봉으로.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "=== 리더 $API · $(date -u +%H:%M:%SZ)"
docker logs "$API" > /tmp/lead.log 2>&1
echo "=== 마지막 예비 신호 목록(run → 종목)"
last=$(grep preview_found /tmp/lead.log | tail -n 1)
echo "$last" | grep -oE '"ts": "[^"]+"'
for run in $(echo "$last" | grep -oE 'live[0-9a-f]{8}:[a-z]+' ); do
  key=${run%%:*}; kind=${run##*:}
  sym=$(grep "\"session_id\": \"$key\"" /tmp/lead.log | grep -oE '"symbol": "[^"]+"' | head -n 1 | cut -d'"' -f4)
  book=$(grep "\"session_id\": \"$key\"" /tmp/lead.log | grep -oE '"playbook": "[^"]+"' | head -n 1 | cut -d'"' -f4)
  echo "  $key $kind $sym  다리들: $book"
done
echo "=== 보유(거래소 포지션)"
docker exec -i "$API" python - <<'PY'
import asyncio, os
from updown.marketdata.gate.trade_client import GateTradeClient
from updown.marketdata.gate.client import LIVE_BASE_URL
async def main():
    c = GateTradeClient(os.environ["GATE_API_KEY"], os.environ["GATE_API_SECRET"], base_url=LIVE_BASE_URL)
    pos = [(p.get("contract"), p.get("size"), p.get("entry_price"), p.get("unrealised_pnl")) for p in await c.get_positions() if str(p.get("size")) not in ("0","")]
    print("POSITIONS", pos)
    await c.aclose()
asyncio.run(main())
PY
echo "=== 시장 폭(게이트 공개 4H 봉 · 펀드 40종 · 마지막 마감 봉 · 24h)"
docker exec -i "$API" python - <<'PY'
import json, urllib.request, time, glob
syms = []
for f in glob.glob("logs/funds/*.json"):
    d = json.load(open(f, encoding="utf-8"))
    syms = [m["symbol"] for m in d["basket"]["members"]]
def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())
up4 = up24 = n = 0; rows = []
for s in syms:
    try:
        k = get(f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={s}&interval=4h&limit=8")
        c = [float(x["c"]) for x in k]
        r4 = c[-2] / c[-3] - 1; r24 = c[-2] / c[-8] - 1; now = c[-1] / c[-2] - 1
        n += 1; up4 += r4 > 0; up24 += r24 > 0
        rows.append((s, round(100*r24,1), round(100*r4,1), round(100*now,1)))
        time.sleep(0.05)
    except Exception as exc:
        rows.append((s, "err", str(exc)[:40], ""))
print(f"종목 {n} · 마지막 마감 4H 양봉 {up4} · 24h 상승 {up24}")
rows = [r for r in rows if r[1] != "err"]
rows.sort(key=lambda r: -r[1])
print("(종목, 24h%, 마지막 4H%, 형성 중 4H%)")
print("상위 12:", rows[:12])
print("하위 6:", rows[-6:])
PY
echo "=== 최근 24h MACD 롱 진입 · entry_limit 거절"
grep -E 'private_strategy' /tmp/lead.log | grep -E 'entered|live_entry|order|blocked|entry_limit' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,50}|"symbol": "[^"]+"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 6
echo "entry_limit 거절 수(리더 로그 전체): $(grep -c '"entry_limit"' /tmp/lead.log)"
rm -f /tmp/lead.log
