#!/usr/bin/env bash
# 실계좌 DB app_settings.live_entries_halted = 1 — 1.28.1 부터 새로 뜨는 라이브 판이 이 값을 읽어 **새 진입만** 멈춘다.
# 1.28.0(지금 도는 것)은 이 키를 안 읽으므로 지금은 무해하고, 다음 배포가 뜨는 순간부터 정지 상태로 시작한다.
# 사용자 결정(2026-10-01 저녁): "지금 매매법은 정지 유지". 되돌리기: 화면 RUN "전부 재개" 또는 값 0.
#   bash scripts/ops/remote.sh scripts/ops/set_live_entries_halted.sh
cd ~/updown 2>/dev/null || exit 1
PG=$(docker ps --format '{{.Names}}' | grep -E 'postgres' | head -1)
[ -z "$PG" ] && { echo "postgres 컨테이너 없음"; exit 1; }
docker exec "$PG" psql -U updown -d updown -tAc "INSERT INTO app_settings(key, value) VALUES ('live_entries_halted', '1') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();"
docker exec "$PG" psql -U updown -d updown -tAc "SELECT key, value, updated_at FROM app_settings WHERE key = 'live_entries_halted';"
