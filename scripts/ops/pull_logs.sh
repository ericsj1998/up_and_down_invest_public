#!/usr/bin/env bash
# 서버 앱 로그를 내 로컬로 받고, **체크섬이 맞은 것만** 서버에서 지운다 (2026-09-26 · 사용자 요청).
#
#   bash scripts/ops/pull_logs.sh                 # 받기만 — 서버는 그대로 (기본)
#   bash scripts/ops/pull_logs.sh --delete        # 받고 · 체크섬 확인 · 확인된 파일만 서버에서 지운다
#   DEST=~/어딘가 bash scripts/ops/pull_logs.sh   # 받을 곳 (기본 ~/projects/updown_serverlogs)
#
# 대상   실계좌 · 데모 앱 로그 회전 파일 `runlogs/app` · `demologs/app` 의 `<proc>-YYYY-MM-DD.jsonl` 중
#        **오늘(UTC) 것이 아닌 것** — 오늘 파일은 지금 쓰는 중이라 받지도 지우지도 않는다.
# 안 건드림
#   - DB `event_logs` — append-only(절대 규칙 #8-2 · 앱 역할은 INSERT 만).
#   - 펀드 원장 `funds/` · RUN 저널 `walkforward/` · 대조 `reconcile/` — 로그가 아니라 상태다.
#   - Docker 컨테이너 로그 — json-file 상한(50 MB x 5)이 맡는다.
#
# 로컬에는 `<받을 곳>/<실계좌|데모>/<파일>.jsonl.gz` 로 압축해 두고, 받은 목록(이름 · 크기 · sha256 · 지움 여부)을
# `<받을 곳>/manifest-<UTC 시각>.tsv` 에 남긴다. 이미 받아 둔 파일(같은 sha256)은 다시 받지 않는다.
# 서버 주소 · 키는 저장소에 두지 않는다 — `scripts/ops/host.env` 에서 읽는다(remote.sh 와 같다).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/host.env" ] && . "$HERE/host.env"
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
DEST="${DEST:-$HOME/projects/updown_serverlogs}"
DELETE=0
[ "${1:-}" = "--delete" ] && DELETE=1

VOLS=(runlogs demologs)
declare -A LABEL=([runlogs]=실계좌 [demologs]=데모)
TODAY="$(date -u +%Y-%m-%d)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"
MANIFEST="$DEST/manifest-$STAMP.tsv"
printf 'vol\tfile\tbytes\tsha256\tdeleted\n' > "$MANIFEST"

freed=0
for vol in "${VOLS[@]}"; do
  dir="/var/lib/docker/volumes/updown_live_${vol}/_data/app"
  out="$DEST/${LABEL[$vol]}"
  mkdir -p "$out"
  # 서버 목록 — 이름 규칙에 맞고 오늘이 아닌 것만 · "이름 크기 sha256"
  listing="$("${SSH[@]}" "sudo -n find '$dir' -maxdepth 1 -type f -regextype posix-extended \
      -regex '.*/[a-z_]+-[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl' ! -name '*-$TODAY.jsonl' -printf '%f %s\n' 2>/dev/null \
      | sort | while read -r f s; do echo \"\$f \$s \$(sudo -n sha256sum '$dir/'\"\$f\" | cut -d' ' -f1)\"; done")"
  if [ -z "$listing" ]; then
    echo "${LABEL[$vol]}: 받을 파일 없음"
    continue
  fi
  verified=()
  while read -r f size sum; do
    [[ "$f" =~ ^[a-z_]+-[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl$ ]] || { echo "이름 규칙 밖 — 건너뜀: $f"; continue; }
    local_gz="$out/$f.gz"
    if [ -f "$local_gz" ] && [ "$(gzip -dc "$local_gz" | sha256sum | cut -d' ' -f1)" = "$sum" ]; then
      echo "${LABEL[$vol]}: $f — 이미 받아 둠(같은 sha256)"
    else
      # ⚠️ `< /dev/null` — 없으면 ssh 가 이 while 의 입력(목록)을 먹어 첫 파일 뒤에 끝난다
      "${SSH[@]}" "sudo -n cat '$dir/$f'" < /dev/null | gzip -6 > "$local_gz.part"
      got="$(gzip -dc "$local_gz.part" | sha256sum | cut -d' ' -f1)"
      if [ "$got" != "$sum" ]; then
        rm -f "$local_gz.part"
        echo "🔴 ${LABEL[$vol]}: $f — sha256 불일치(서버 $sum · 로컬 $got) · 이 파일은 지우지 않는다"
        printf '%s\t%s\t%s\t%s\t%s\n' "$vol" "$f" "$size" "$sum" "no(mismatch)" >> "$MANIFEST"
        continue
      fi
      mv "$local_gz.part" "$local_gz"
      echo "${LABEL[$vol]}: $f — 받음 $((size / 1048576)) MB · sha256 일치"
    fi
    verified+=("$f")
    printf '%s\t%s\t%s\t%s\t%s\n' "$vol" "$f" "$size" "$sum" "$([ $DELETE = 1 ] && echo yes || echo no)" >> "$MANIFEST"
    freed=$((freed + size))
  done <<< "$listing"
  if [ $DELETE = 1 ] && [ ${#verified[@]} -gt 0 ]; then
    # 지우는 목록은 로컬에서 체크섬이 맞은 이름뿐 — 서버에서 한 번 더 이름 규칙 · 오늘 아님을 확인한다
    names="${verified[*]}"
    # ⚠️ 볼륨 폴더는 root 전용이라 cd 가 안 된다 — 전체 경로로 sudo rm
    "${SSH[@]}" "for f in $names; do case \"\$f\" in *-$TODAY.jsonl) continue;; esac; \
      echo \"\$f\" | grep -Eq '^[a-z_]+-[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl$' && sudo -n rm -f -- '$dir/'\"\$f\"; done" < /dev/null
    echo "${LABEL[$vol]}: 서버에서 ${#verified[@]} 개 지움"
  fi
done
echo "받은 곳 $DEST · 목록 $MANIFEST · 대상 합계 $((freed / 1048576)) MB$([ $DELETE = 1 ] && echo ' · 서버에서 지움' || echo ' · 서버는 그대로(--delete 로 지운다)')"
"${SSH[@]}" "df -h / | tail -1"
