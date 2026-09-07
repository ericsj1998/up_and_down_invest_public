#!/usr/bin/env bash
# 서버에서 스크립트 하나를 돌린다 — 로컬 파일을 올려 실행하고 출력을 받는다.
#
#   bash scripts/ops/remote.sh scripts/ops/status.sh
#   bash scripts/ops/remote.sh scripts/ops/probe_gate.py        # .py 는 api 컨테이너 안 python 으로
#   bash scripts/ops/remote.sh -- 'docker ps'                   # 한 줄 명령
#
# 왜 이 모양인가 (docs/platform/ops_runbook.md §2): `wsl.exe bash -lc "..."` 페이로드는 $(...) · 백틱 ·
# 따옴표 · {{.Names}} 가 깨진다. **명령은 파일에 적고 파일을 올려 실행한다** — 그게 유일하게 안 깨지는 길이다.
set -euo pipefail
# 서버 주소는 저장소에 두지 않는다 (퍼블릭 저장소 · 2026-09-06) — 같은 폴더의 host.env 에서 읽는다.
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/host.env" ] && . "$HERE/host.env"
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다 — host.env.example 을 복사해 채운다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")

if [ "${1:-}" = "--" ]; then
  shift; "${SSH[@]}" "$*"; exit $?
fi
src="${1:?사용법: remote.sh <로컬 스크립트 경로> | -- '<명령>'}"
name="$(basename "$src")"
scp -q -i "$KEY" "$src" "$HOST:/tmp/$name"
case "$name" in
  *.py)
    # api 컨테이너(리더든 팔로워든 하나) 안에서 — 앱 코드·.env.live 가 거기 있다
    # ⚠️ api_demo 는 빼야 한다 — 그 컨테이너는 테스트넷 키·데모 DB 라 프로브가 빈 값을 낸다 (2026-09-06 실측:
    #    배포 뒤 `docker ps` 가 api_demo 를 먼저 내놓아 probe_gate 가 조용히 아무것도 안 찍었다).
    "${SSH[@]}" "API=\$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1); \
      docker cp /tmp/$name \"\$API\":/tmp/$name && docker exec \"\$API\" python /tmp/$name 2>&1 | grep -v '^{'" ;;
  *)
    "${SSH[@]}" "bash /tmp/$name" ;;
esac
