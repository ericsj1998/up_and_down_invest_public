#!/usr/bin/env bash
# 낙폭 브레이크 한 번 풀기 요청 — 실계좌 DB app_settings.fund_brake_release 에 요청을 남긴다.
# 거래 리더가 1 분 안에 집어 브레이크 원장의 고점을 지금 잔고로 다시 열고 요청을 지운다
# (apps/api/rebalancer.py consume_brake_release). 포지션 · 주문 · 실제 잔고는 안 건드린다.
# 🔴 리스크를 늘리는 행동이다 — 다음 새 진입부터 줄이지 않은 크기로 든다. 사람이 정했을 때만.
#   전부:      bash scripts/ops/remote.sh scripts/ops/request_brake_release.sh
#   한 펀드만: 이 파일의 TARGET 을 펀드 id 로 바꿔서.
# 화면에서도 된다 — 사이드바 '관리' → "낙폭 브레이크 풀기"(관리자 비밀번호).
TARGET="${BRAKE_RELEASE_TARGET:-all}"
case "$TARGET" in *[!A-Za-z0-9_.-]*) echo "대상 이름이 이상하다: $TARGET"; exit 1;; esac
cd ~/updown 2>/dev/null || exit 1
PG=$(docker ps --format '{{.Names}}' | grep -E 'postgres' | head -1)
[ -z "$PG" ] && { echo "postgres 컨테이너 없음"; exit 1; }
Q() { docker exec "$PG" psql -U updown -d updown -tAc "$1"; }
echo "== 요청 전 마지막 풀기 기록"
Q "SELECT coalesce(max(value), '(없음)') FROM app_settings WHERE key = 'fund_brake_release_last';"
Q "INSERT INTO app_settings(key, value) VALUES ('fund_brake_release', '$TARGET') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();" > /dev/null
echo "== 요청 남김($TARGET) — 리더가 집을 때까지 최대 90 초 기다린다"
for _ in $(seq 1 18); do
  sleep 5
  left=$(Q "SELECT count(*) FROM app_settings WHERE key = 'fund_brake_release';")
  [ "$left" = "0" ] && break
done
if [ "$left" = "0" ]; then echo "== 리더가 요청을 집었다"; else echo "== 🔴 아직 안 집었다 — 요청은 남아 있다(리더 · 펀드 붙음 상태를 본다)"; fi
echo "== 마지막 풀기 기록(시각|펀드|누가|풀기 전 낙폭 %)"
Q "SELECT coalesce(max(value), '(없음)') FROM app_settings WHERE key = 'fund_brake_release_last';"
