#!/usr/bin/env bash
# 유니버스 봉 예열을 지금 한 바퀴 — 배포로 리더 슬롯이 바뀌면 작업이 죽으므로 다시 띄운다.
#
#   bash scripts/ops/warm_now.sh
#
# ⚠️ 이것은 **로컬에서** 돈다 (`remote.sh` 로 넘기지 않는다). 주소는 `scripts/ops/host.env`(비추적),
#    관리자 토큰은 `.env.dev` 의 `TOSS_PROXY_TOKEN` 에서 읽는다 — 둘 다 화면에 찍지 않는다.
#    `/admin/toss/warm` 은 개인 토큰이 통과하는 유일한 쓰기 경로다(`auth.WARM_JOB_PATH`).
set -u
cd "$(dirname "$0")/../.." || exit 1
# shellcheck disable=SC1091
. scripts/ops/host.env 2>/dev/null || { echo "host.env 가 없다"; exit 1; }
TOKEN=$(grep -m1 '^TOSS_PROXY_TOKEN=' .env.dev 2>/dev/null | cut -d= -f2-)
[ -n "${TOKEN:-}" ] || { echo "관리자 토큰이 없다 — .env.dev 의 TOSS_PROXY_TOKEN"; exit 1; }
OUT=$(mktemp)
# 🔴 `updown_mode=live` 쪽지가 없으면 nginx 가 **데모 API** 로 보낸다 — 데모는 토스를 직접 안 부르니
#    503 "예열은 실계좌 서버에서" 가 돌아온다 (2026-09-11 실측).
code=$(curl -s -o "$OUT" -w '%{http_code}' -m 30 -X POST \
  "https://${PUBLIC_DOMAIN}/api/admin/toss/warm" \
  -H "Authorization: Bearer ${TOKEN}" -H 'content-type: application/json' \
  -H 'Cookie: updown_mode=live' -d '{}')
echo "HTTP $code"
grep -oE '"(job_id|status|detail)": *"[^"]*"' "$OUT" | head -4
rm -f "$OUT"
