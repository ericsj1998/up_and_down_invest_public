#!/usr/bin/env bash
# 배포 뒤 외부 노출 검사 (T39 · 2026-09-04) — **쿠키 없이** 모든 라우트를 두드린다.
#
#   scripts/deploy/check_exposure.sh https://<도메인>/api        # 밖에서
#   scripts/deploy/check_exposure.sh http://localhost:5175/api   # 로컬 (nginx 경유)
#
# 기대: /health · /health/ready · /auth/login · /auth/callback · /auth/me 만 200/302,
#      나머지 GET 은 전부 401. 하나라도 200 이면 **문이 열린 것**이고 exit 1 이다.
# 라우트 목록은 추측하지 않고 **컨테이너 안의 앱**에서 뽑는다 — 새 라우터가 생겨도 자동으로 검사한다.
set -u
cd "$(dirname "$0")/../.." || exit 1
BASE="${1:?base url 이 필요하다 (예: http://localhost:5175/api)}"
ENV="${ENV:-dev}"; ENV_FILE="${ENV_FILE:-.env.$ENV}"
C="docker compose --env-file $ENV_FILE -f docker/compose.base.yml -f docker/compose.$ENV.yml"

# 🔴 `app.routes` 를 걷지 않는다 — FastAPI 가 include_router 를 `_IncludedRouter` 로 감싸 하위
#    라우트가 안 보인다 (실측 2026-09-04: 60여 개 중 7개만 나왔다). OpenAPI 스키마가 정답이다.
#    (`include_in_schema=False` 인 라우트는 빠진다 — 그런 라우트는 만들지 않는다.)
ROUTES=$($C run --rm --no-deps -T api python - <<'PY' 2>/dev/null | grep -E '^[A-Z]+ /'
import re
from updown.apps.api.main import app
seen = set()
for path, ops in app.openapi()["paths"].items():
    # 경로 인자는 그럴듯한 값으로 — 404 든 422 든 "문 뒤" 응답이면 된다
    filled = re.sub(r"\{[^}]+\}", "x", path)
    for method in ops:
        seen.add(f"{method.upper()} {filled}")
print("\n".join(sorted(seen)))
PY
)
PUBLIC_OK="^(GET /health|GET /health/ready|GET /auth/login|GET /auth/callback|GET /auth/me|GET /auth/logout|POST /auth/logout)$"

bad=0; n=0
while read -r method path; do
  [ -z "$method" ] && continue
  n=$((n+1))
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X "$method" "$BASE$path" -H 'content-type: application/json' -d '{}' 2>/dev/null)
  if echo "$method $path" | grep -Eq "$PUBLIC_OK"; then
    printf "  공개  %-6s %-45s %s\n" "$method" "$path" "$code"
    continue
  fi
  case "$code" in
    401|403) ;;                     # 막혔다 — 정상
    404|405|422|429|503) ;;         # 인증 뒤 검증 오류가 아니라, 문 앞에서 났으면 정상. 401 아님은 아래서 표시
    200|201|204|3*) printf "🔴 열림 %-6s %-45s %s\n" "$method" "$path" "$code"; bad=$((bad+1)) ;;
    000) printf "⚠️ 응답없음 %-6s %-45s\n" "$method" "$path" ;;
    *)   printf "?      %-6s %-45s %s\n" "$method" "$path" "$code" ;;
  esac
done <<< "$ROUTES"

echo "---"
echo "라우트 $n 개 검사 · 인증 없이 열린 것 $bad 개"
[ "$bad" -eq 0 ] && echo "✅ 닫혀 있다" || { echo "🔴 위 경로가 인증 없이 응답한다 — 배포 중단"; exit 1; }
