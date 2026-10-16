#!/usr/bin/env bash
# 펀드 멤버 종목 목록 · 펀드 매매법 · 자리 · 배율 · 앵커 (읽기 전용)
#   사용: bash scripts/ops/remote.sh scripts/ops/probe_fund_members.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
for f in $(docker exec "$API" sh -c 'ls ${FUNDS_ROOT:-logs/funds}/*.json 2>/dev/null'); do
  docker exec "$API" python3 -c "
import json
d = json.load(open('$f'))
print('FUND', '$f', d.get('playbook'), 'slots', d.get('slots'), 'leverage', d.get('leverage'), 'alt_leverage', d.get('alt_leverage'), 'weight_mode', d.get('weight_mode'))
b = d.get('basket')
print('BASKET', type(b).__name__, (sorted(b) if isinstance(b, (list, dict)) else b))
r = d.get('runs')
print('RUNS', type(r).__name__, (sorted(r)[:45] if isinstance(r, (list, dict)) else r))
s = d.get('seeds')
print('SEEDS', (sorted(s.items())[:45] if isinstance(s, dict) else s))
a = d.get('anchor')
print('ANCHOR', {k: a.get(k) for k in list(a)[:8]} if isinstance(a, dict) else a)
"
done
