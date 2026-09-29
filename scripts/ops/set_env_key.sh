#!/usr/bin/env bash
# 서버 .env.live 에 **비밀이 아닌** 운영 키 하나를 넣거나 바꾼다 (사용자 지시 · 2026-09-30 · LIVE_MAX_RUNNING=3).
#   KEY=LIVE_MAX_RUNNING VAL=3 bash scripts/ops/remote.sh scripts/ops/set_env_key.sh
# ⛔ LIVE_ORDERS · 거래소 키 · 세션 키는 여기로 안 바꾼다(사람이 서버에서). 값은 찍지 않고 키 이름 · 유무만 찍는다.
set -e
KEY="${KEY:-LIVE_MAX_RUNNING}"; VAL="${VAL:-3}"
case "$KEY" in LIVE_ORDERS|STOCK_LIVE_ORDERS|*KEY*|*SECRET*|*TOKEN*|*PASSWORD*) echo "🔴 $KEY 는 이 스크립트로 안 바꾼다"; exit 1;; esac
F=~/updown/.env.live
[ -f "$F" ] || F=$(ls -d ~/*/.env.live 2>/dev/null | head -1)
[ -f "$F" ] || { echo "🔴 .env.live 없음"; exit 1; }
cp "$F" "$F.bak.$(date -u +%Y%m%dT%H%M%SZ)"
if grep -qE "^${KEY}=" "$F"; then
  sed -i -E "s|^${KEY}=.*|${KEY}=${VAL}|" "$F"; echo "바꿈: $KEY (있던 줄)"
else
  printf '\n# CPU 예산(2026-09-30 · 사용자 지시)\n%s=%s\n' "$KEY" "$VAL" >> "$F"; echo "붙임: $KEY (새 줄)"
fi
echo "확인: $(grep -cE "^${KEY}=" "$F") 줄 · 파일 $(dirname "$F")/.env.live · 적용은 API 재시작(1.26.1 배포) 때"
