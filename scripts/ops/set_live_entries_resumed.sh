#!/usr/bin/env bash
# 실계좌 DB app_settings.live_entries_halted = 0 — 진입 정지 해제(사용자 2026-10-02 "정지 풀어줘").
# 판은 뜰 때 이 값을 읽는다 — 값만 바꾸면 지금 도는 판은 그대로 정지이고, 다음 배포 · 재시작에서 진입이 켜진 채로 뜬다.
# 지금 도는 판을 바로 켜려면 화면 RUN "전부 재개". 다시 정지: set_live_entries_halted.sh
#   bash scripts/ops/remote.sh scripts/ops/set_live_entries_resumed.sh
cd ~/updown 2>/dev/null || exit 1
PG=$(docker ps --format '{{.Names}}' | grep -E 'postgres' | head -1)
[ -z "$PG" ] && { echo "postgres 컨테이너 없음"; exit 1; }
docker exec "$PG" psql -U updown -d updown -tAc "INSERT INTO app_settings(key, value) VALUES ('live_entries_halted', '0') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();"
docker exec "$PG" psql -U updown -d updown -tAc "SELECT key, value, updated_at FROM app_settings WHERE key = 'live_entries_halted';"
