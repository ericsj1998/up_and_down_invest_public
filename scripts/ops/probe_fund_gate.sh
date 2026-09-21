#!/usr/bin/env bash
# 펀드 문이 세션에 **실제로** 끼워졌나 · 진입 때 센 폭 — 로그의 `fund_gate_attached` · `fund_gate_breadth` 를 읽는다.
# 메모리에만 있던 값(문 · 다리 노출 · 사이징 예산)을 인증 없이 본다. 시크릿 없음.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "=== $API · $(date -u +%H:%M:%SZ)"
docker logs --since 72h "$API" 2>&1 | grep '"event_type": "fund_gate_attached"' | tail -n 3 > /tmp/_gate_lines.txt
docker logs --since 72h "$API" 2>&1 | grep '"event_type": "fund_gate_breadth"' | tail -n 40 > /tmp/_breadth_lines.txt
python3 - <<'PY'
import json
for line in open("/tmp/_gate_lines.txt", encoding="utf-8"):
    try:
        row = json.loads(line)
    except ValueError:
        continue
    p = row["payload"]
    print(f"--- {row.get('ts')} · {p['fund_id']} · {p['playbook']} · 자리 {p['slots']} · 문 없는 세션: {p['ungated'] or '없음'}")
    for leg in p["legs"]:
        print("   다리", leg)
    for sym, m in sorted(p["members"].items()):
        print(f"   {sym:12s} {'+'.join(m['books']):58s} 배율 {m['leverage']:>2s} · 예산 {m['budget']} · 문 {m['gate']} · 노출 {m['leg_exposure']} · 폭 {m['breadth']}")
print("=== 진입 때 센 폭 (최근 40)")
late = total = 0
for line in open("/tmp/_breadth_lines.txt", encoding="utf-8"):
    try:
        p = json.loads(line)["payload"]
    except ValueError:
        continue
    total += 1
    bars = [v for v in p["last_bar"].values() if v]
    behind = sorted(k for k, v in p["last_bar"].items() if v and v != max(bars))
    late += bool(behind)
    print(f"   {p['at']} · 폭 {p['breadth']} (문턱 {p['min']}) → 상한 {p['cap']} · 돌파 {[k for k, v in p['breaks'].items() if v]} · 봉이 늦은 형제 {behind or '없음'}")
print(f"=== 폭을 센 {total}번 중 형제 봉이 늦었던 것 {late}번")
PY
