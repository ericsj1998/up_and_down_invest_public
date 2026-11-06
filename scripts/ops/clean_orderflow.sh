#!/usr/bin/env bash
# 서버 호가 기록을 **로컬에 같은 파일이 있는 것만** 지운다 (2026-11-06 · 사용자 요청).
#
#   bash scripts/ops/clean_orderflow.sh            # 지우지 않고 세기만(기본)
#   bash scripts/ops/clean_orderflow.sh --delete   # 체크섬이 같은 파일만 서버에서 지운다
#   KEEP_DAYS=7 MARKETS="GATE UPBIT" bash ...      # 남길 최근 날 수 · 대상(기본 2 일 · 셋 다)
#
# 먼저 받는다: bash scripts/ops/pull_orderflow.sh · 층별은 MARKETS=GATE_L2 bash scripts/ops/pull_orderflow.sh
# 로컬에 없거나 체크섬이 다른 파일은 절대 안 지운다. 쓰는 중인 최근 파일은 대상에서 뺀다.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/host.env" ] && . "$HERE/host.env"
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/logs/orderflow_server"
BASE="/var/lib/docker/volumes/updown_live_flowlogs/_data"
MARKETS="${MARKETS:-GATE UPBIT GATE_L2}"
KEEP_DAYS="${KEEP_DAYS:-2}"
DELETE=0
[ "${1:-}" = "--delete" ] && DELETE=1
CUT="$(date -u -d "$KEEP_DAYS days ago" +%Y-%m-%d)"
ROOTDIR="$("${SSH[@]}" "sudo -n test -d $BASE/orderflow && echo $BASE/orderflow || echo $BASE")"
same=0 missing=0 differ=0 bytes=0
gone="$(mktemp)"
trap 'rm -f "$gone"' EXIT
for m in $MARKETS; do
  listing="$("${SSH[@]}" "sudo -n find $ROOTDIR/$m -type f -name '20*-*-*.jsonl*' -printf '%P %s\n' 2>/dev/null \
    | sort | while read -r f s; do
        d=\$(basename \"\$f\" | cut -c1-10); [ \"\$d\" \\< \"$CUT\" ] || continue
        echo \"\$f \$s \$(sudo -n nice -n 19 sha256sum $ROOTDIR/$m/\"\$f\" | cut -d' ' -f1)\"; done")"
  while read -r f size sum; do
    [ -n "${f:-}" ] || continue
    local_file="$OUT/$m/$f"
    if [ ! -f "$local_file" ]; then
      missing=$((missing + 1))
    elif [ "$(sha256sum "$local_file" | cut -d' ' -f1)" != "$sum" ]; then
      differ=$((differ + 1))
    else
      same=$((same + 1))
      bytes=$((bytes + size))
      echo "$m/$f" >> "$gone"
    fi
  done <<< "$listing"
done
echo "기준 $CUT 앞 · 같음 $same($((bytes / 1048576)) MB) · 로컬에 없음 $missing · 다름 $differ"
if [ "$DELETE" = 1 ] && [ "$same" -gt 0 ]; then
  # 이름 모양을 서버에서 한 번 더 본다
  "${SSH[@]}" "while read -r f; do case \"\$f\" in */20*-*-*.jsonl|*/20*-*-*.jsonl.gz) sudo -n rm -f -- '$ROOTDIR/'\"\$f\";; esac; done" < "$gone"
  echo "서버에서 $same 개 지움"
fi
[ "$DELETE" = 1 ] || echo "지우지 않았다 — 지우려면 --delete"
"${SSH[@]}" "df -h / | tail -1"
