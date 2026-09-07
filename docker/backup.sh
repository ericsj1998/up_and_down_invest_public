#!/bin/bash
# 일일 백업 — postgres 이미지 안에서 돈다 (T213 · 2026-09-04).
#
#   pg_dump  → /backups/updown-YYYYMMDDTHHMMZ.dump   (custom 포맷 · 압축 · pg_restore 로 복구)
#   funds    → /backups/funds-YYYYMMDDTHHMMZ.tgz      (logs/funds/*.json — 펀드 원장)
#   보존     BACKUP_KEEP_DAYS (기본 7) 를 넘긴 것은 지운다
#
# 기동 시 한 번, 그 뒤 24시간마다. `once` 인자면 한 번만 돌고 끝난다 (복구 연습·검증용).
#
# 🔴 실패는 조용히 넘기지 않는다 — pg_dump 가 실패하면 exit 1 로 컨테이너가 죽고
#    `restart: unless-stopped` 가 다시 띄운다. 로그에 이유가 남는다 (규칙 #8).
set -eu

KEEP="${BACKUP_KEEP_DAYS:-7}"
OUT=/backups
mkdir -p "$OUT"

run_once() {
  stamp="$(date -u +%Y%m%dT%H%MZ)"
  dump="$OUT/updown-$stamp.dump"
  echo "[backup] $stamp pg_dump -> $dump"
  # -Fc: custom 포맷 (압축 · 선택 복구 가능). 실패하면 반쪽 파일을 남기지 않는다.
  if ! pg_dump -Fc --no-owner --no-privileges -f "$dump.part"; then
    rm -f "$dump.part"
    echo "[backup] pg_dump FAILED" >&2
    return 1
  fi
  mv "$dump.part" "$dump"
  if [ -d /app/logs/funds ]; then
    tar czf "$OUT/funds-$stamp.tgz" -C /app/logs funds
  fi
  # 보존 — mtime 기준. 하루 한 번이면 KEEP 일치가 남는다.
  find "$OUT" -maxdepth 1 -type f \( -name 'updown-*.dump' -o -name 'funds-*.tgz' \) -mtime +"$KEEP" -print -delete \
    | sed 's/^/[backup] pruned /'
  echo "[backup] done · $(du -sh "$OUT" | cut -f1) in $OUT · $(ls "$OUT" | wc -l) files"
}

case "${1:-}" in
  once) run_once; exit 0 ;;
  "") ;;  # 인자 없음 = 상주 루프
  *)
    # 🔴 모르는 인자로 루프에 들어가지 않는다 — `docker compose run backup sh -c ...` 처럼
    #    엔트리포인트를 잊고 부르면 여기서 바로 멈춘다 (2026-09-04 검증에서 겪음).
    echo "[backup] usage: backup.sh [once]   (got: $*)" >&2
    exit 2 ;;
esac

while true; do
  run_once || exit 1
  sleep 86400
done
