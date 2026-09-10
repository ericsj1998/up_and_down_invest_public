#!/usr/bin/env bash
# 연구 PC DB 의 미국주식 **일봉**(NASDAQ·NYSE · 2020~ · 공개 자료)을 실계좌/데모 DB 로 복사한다 — 저평가 2단계의 종가와
# 5년 백분위가 토스 없이도 서게 (사용자 2026-09-11 "왜 여전히 출력이 안 될까" · 서버 토스 토큰 403).
#
#   bash scripts/ops/copy_stock_candles_to_live.sh          # 실계좌 DB(updown)
#   bash scripts/ops/copy_stock_candles_to_live.sh demo     # 데모 DB(updown_demo)
#
# 하는 일: ① 2020-01~2025-07 월 파티션 DDL(IF NOT EXISTS) ② 종목 행이 없으면 만든다(market·symbol 로 대조 · id 는 서버 것)
#          ③ 일봉을 (instrument_id, timeframe, ts) 충돌은 건너뛰며 넣는다. 원본은 지우지 않는다. 값·주소는 찍지 않는다.
# ⚠️ 실계좌 DB 에 쓰는 일이다 — 사람이 돌린다.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1
[ -f scripts/ops/host.env ] && . scripts/ops/host.env
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
TARGET_DB="updown"; [ "${1:-}" = "demo" ] && TARGET_DB="updown_demo"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
echo "== ① 파티션 DDL (2020-01 ~ 2025-07)"
uv run python - > "$work/partitions.sql" <<'PY'
from datetime import UTC, datetime
from updown.common.db.partitions import ensure_partitions_ddl
for ddl in ensure_partitions_ddl("candles", datetime(2025, 7, 1, tzinfo=UTC), months_back=66, months_forward=0):
    print(ddl.rstrip(";") + ";")
PY
echo "   $(grep -c 'CREATE' "$work/partitions.sql")개 문장"
echo "== ② 로컬 일봉 덤프 (NASDAQ·NYSE · 1d)"
docker exec updown-postgres-1 psql -U updown -d updown -At -c "\\copy (select i.market, i.symbol, i.name, i.asset_type, i.currency, c.timeframe, c.ts, c.open, c.high, c.low, c.close, c.volume from candles c join instruments i on i.id = c.instrument_id where i.market in ('NASDAQ','NYSE') and c.timeframe = '1d' order by i.market, i.symbol, c.ts) to stdout with (format csv)" > "$work/candles.csv"
echo "   $(wc -l < "$work/candles.csv") 행 · $(du -h "$work/candles.csv" | cut -f1)"
gzip -f "$work/candles.csv"
scp -q -i "$KEY" "$work/partitions.sql" "$HOST:/tmp/stock_partitions.sql"
scp -q -i "$KEY" "$work/candles.csv.gz" "$HOST:/tmp/stock_candles.csv.gz"
ssh -i "$KEY" -o BatchMode=yes "$HOST" "TARGET_DB='$TARGET_DB' bash -s" <<'REMOTE'
set -euo pipefail
cd ~/updown
PG="docker exec -i updown_live-postgres-1 psql -U updown -d $TARGET_DB -q -v ON_ERROR_STOP=1"
before=$(docker exec updown_live-postgres-1 psql -U updown -d "$TARGET_DB" -At -c "select count(*) from candles c join instruments i on i.id=c.instrument_id where i.market in ('NASDAQ','NYSE') and c.timeframe='1d'")
$PG < /tmp/stock_partitions.sql
gunzip -f /tmp/stock_candles.csv.gz
$PG <<'SQL'
create temp table stock_in (market text, symbol text, name text, asset_type text, currency text, timeframe text, ts timestamptz, open numeric, high numeric, low numeric, close numeric, volume numeric);
\copy stock_in from '/tmp/stock_candles.csv' with (format csv)
insert into instruments (market, symbol, name, asset_type, currency)
  select distinct market, symbol, name, asset_type, currency from stock_in
  on conflict (market, symbol) do nothing;
insert into candles (instrument_id, timeframe, ts, open, high, low, close, volume)
  select i.id, s.timeframe, s.ts, s.open, s.high, s.low, s.close, s.volume
  from stock_in s join instruments i on i.market = s.market and i.symbol = s.symbol
  on conflict (instrument_id, timeframe, ts) do nothing;
SQL
after=$(docker exec updown_live-postgres-1 psql -U updown -d "$TARGET_DB" -At -c "select count(*), count(distinct instrument_id), min(ts)::date, max(ts)::date from candles c join instruments i on i.id=c.instrument_id where i.market in ('NASDAQ','NYSE') and c.timeframe='1d'")
echo "   $TARGET_DB 미국주식 1d: $before → $after (행 · 종목 · 처음 · 끝)"
rm -f /tmp/stock_partitions.sql /tmp/stock_candles.csv
REMOTE
echo "== 끝. 저평가 카드는 캐시 TTL 뒤 종가·백분위·점수가 선다(토스가 열리기 전까지는 어제 종가 기준)."
