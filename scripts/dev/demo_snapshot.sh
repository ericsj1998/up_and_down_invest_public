#!/usr/bin/env bash
# 정적 데모 저장본 다시 만들기 — 지금 도는 데모(게스트)를 둘러보며 화면이 부르는 응답을 web/public/__demo/ 에 담는다.
#   bash scripts/dev/demo_snapshot.sh            (BASE 를 주면 그 주소 · 없으면 scripts/ops/host.env 의 PUBLIC_DOMAIN)
# 🔴 저장본은 비공개 경로다(strategy_split.yml private_paths) — 매매법 이름 · 라벨이 들어 있다. 주소는 찍지 않는다.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$(pwd)
if [ -z "${BASE:-}" ]; then
  # shellcheck disable=SC1091
  . scripts/ops/host.env
  BASE="https://${PUBLIC_DOMAIN:?scripts/ops/host.env 에 PUBLIC_DOMAIN 이 없다}"
fi
OUT="$ROOT/web/public"
NEW="$OUT/__demo.new"
rm -rf "$NEW"
mkdir -p "$NEW"
export LD_LIBRARY_PATH="$HOME/tools/pwlibs/root/usr/lib/x86_64-linux-gnu:$HOME/tools/pwlibs/root/usr/lib/x86_64-linux-gnu/nss:${LD_LIBRARY_PATH:-}"
cp scripts/dev/demo_snapshot.mjs "$HOME/tools/pw/demo_snapshot.mjs"
( cd "$HOME/tools/pw" && BASE="$BASE" OUT="$NEW" node demo_snapshot.mjs )
if [ ! -f "$NEW/__demo/auth/me.json" ]; then
  echo "🔴 게스트 auth/me 가 없다 — 게스트 입장이 안 됐다. 옛 저장본을 그대로 둔다"
  rm -rf "$NEW"
  exit 1
fi
# 정적 데모는 아무것도 못 만든다 — 화면이 거래 단추를 잠근 모양으로 그리게 게스트 권한에서 거래를 뺀다.
python3 - "$NEW/__demo/auth/me.json" <<'PY'
import json, sys
p = sys.argv[1]
me = json.load(open(p, encoding="utf-8"))
me["may_trade"] = False
me["caps"] = [c for c in me.get("caps", []) if c not in ("demo_trade", "demo_edit", "live_trade")]
for m in (me.get("markets") or {}).values():
    if isinstance(m, dict):
        m["trade"] = False
me["static_demo"] = True
me.pop("fresh_until", None)  # 저장본 시각이 굳어 있으면 화면 타이머가 곧 "인증 만료" 로 바뀐다 — 게스트는 재인증이 없다
json.dump(me, open(p, "w", encoding="utf-8"), ensure_ascii=False)
print("게스트 권한에서 거래를 뺐다:", me["caps"])
PY
rm -rf "$OUT/__demo"
mv "$NEW/__demo" "$OUT/__demo"
rm -rf "$NEW"
echo "== 저장본"; find "$OUT/__demo" -name '*.json' | wc -l; du -sh "$OUT/__demo"
