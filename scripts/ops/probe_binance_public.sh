#!/usr/bin/env bash
# T758 §11-1 — 서버에서 바이낸스 USDT-M 공개 API 가 닿는지 · 거래소 한도 헤더를 읽기만 한다.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_binance_public.sh
#
# 읽기만: 공개 GET 몇 번 · 파일 · 컨테이너 · env 안 건드림 · 키 안 씀 · 응답은 요약만 찍고 임시 파일은 지운다.
# 호출: exchangeInfo 1 회(가중치 1) · klines 1 회(limit 400 · 가중치 2) · Gate contracts 1 회 · Gate candlesticks 1 회.
set -u
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BN="https://fapi.binance.com"
GT="https://api.gateio.ws/api/v4"

echo "== 바이낸스 exchangeInfo"
code=$(curl -sS -m 30 -D "$TMP/h1" -o "$TMP/info.json" -w '%{http_code}' "$BN/fapi/v1/exchangeInfo")
echo "HTTP $code · 크기 $(wc -c <"$TMP/info.json") 바이트"
grep -i -E '^(x-mbx-used-weight|retry-after)' "$TMP/h1" || true

python3 - "$TMP/info.json" <<'PY'
import collections
import json
import sys

try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:  # 차단 응답이면 JSON 이 아니다
    print("JSON 아님:", str(e)[:100])
    raise SystemExit(0)
if "symbols" not in d:
    print("symbols 없음:", str(d)[:200])
    raise SystemExit(0)
rows = [s for s in d["symbols"] if s.get("quoteAsset") == "USDT"]
print("USDT 심볼", len(rows), "· 전체", len(d["symbols"]))
for key in ("status", "contractType", "underlyingType"):
    print(key, dict(collections.Counter(str(s.get(key)) for s in rows).most_common()))
print("비 ASCII", [s["symbol"] for s in rows if not s["symbol"].isascii()][:10])
print("한도", [(r.get("rateLimitType"), r.get("interval"), r.get("intervalNum"), r.get("limit")) for r in d.get("rateLimits", [])])
print("폐지 표본(SETTLING)", [s["symbol"] for s in rows if s.get("status") == "SETTLING"][:10])
PY

echo "== 바이낸스 klines(BTCUSDT · 1d · limit 400)"
code=$(curl -sS -m 30 -D "$TMP/h2" -o "$TMP/k.json" -w '%{http_code}' "$BN/fapi/v1/klines?symbol=BTCUSDT&interval=1d&limit=400")
echo "HTTP $code"
grep -i -E '^x-mbx-used-weight' "$TMP/h2" || true
python3 - "$TMP/k.json" <<'PY'
import json
import sys
from datetime import UTC, datetime

try:
    k = json.load(open(sys.argv[1], encoding="utf-8"))
    print("봉", len(k), "· 첫", datetime.fromtimestamp(k[0][0] / 1000, UTC).date(), "· 끝", datetime.fromtimestamp(k[-1][0] / 1000, UTC).date(), "· 열", len(k[0]))
except Exception as e:
    print("읽기 실패:", str(e)[:100])
PY

echo "== Gate contracts · 한도 헤더"
code=$(curl -sS -m 30 -D "$TMP/h3" -o "$TMP/c.json" -w '%{http_code}' "$GT/futures/usdt/contracts")
echo "HTTP $code"
grep -i -E '^x-gate-ratelimit' "$TMP/h3" || true
python3 - "$TMP/c.json" <<'PY'
import collections
import json
import sys

try:
    c = json.load(open(sys.argv[1], encoding="utf-8"))
    print("계약", len(c), "· status", dict(collections.Counter(str(x.get("status")) for x in c).most_common()), "· in_delisting", sum(1 for x in c if x.get("in_delisting")))
except Exception as e:
    print("읽기 실패:", str(e)[:100])
PY

echo "== Gate candlesticks 한도 헤더"
curl -sS -m 30 -D "$TMP/h4" -o /dev/null "$GT/futures/usdt/candlesticks?contract=BTC_USDT&interval=1d&limit=1" || true
grep -i -E '^x-gate-ratelimit' "$TMP/h4" || true

echo "== 끝"
