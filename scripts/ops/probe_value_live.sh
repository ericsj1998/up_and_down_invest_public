#!/usr/bin/env bash
# 저평가 화면이 왜 비었나 — API 안에서 screen 을 직접 부르고(요약만) · 토스/EDGAR 오류 · 토큰 (값 없음).
cd ~/updown 2>/dev/null || exit 1
A=$(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $A: /fundamentals/screen 직접 (NASDAQ · SP500)"
docker exec "$A" python -c '
import json, urllib.request, urllib.error
for q in ("market=NASDAQ&size=5", "market=SP500&size=5", "market=NASDAQ&has_facts=true&size=5"):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/fundamentals/screen?" + q, timeout=120) as r:
            b = json.load(r)
        rows = b.get("rows") or []
        print(q, "->", r.status, "rows", len(rows), "total", b.get("total"), "pending", b.get("pending"), "keys", sorted(b)[:8])
        for row in rows[:2]:
            print("   ", row.get("symbol"), "facts", row.get("has_facts"), "price", row.get("price"), "score", row.get("score"))
    except urllib.error.HTTPError as e:
        print(q, "-> HTTP", e.code, e.read()[:200].decode(errors="replace"))
    except Exception as e:
        print(q, "-> ERR", str(e)[:200])
' 2>&1 | grep -v "^20[0-9][0-9]-"
echo "=== $A 30m: 재무·토스·EDGAR 오류/경고"
docker logs --since 30m "$A" 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]{0,90}"' | sort | uniq -c | sort -rn | head -12
echo "=== $A 30m: EDGAR/토스 상세 (앞 160자)"
docker logs --since 30m "$A" 2>&1 | grep -iE 'edgar|sec.gov|toss_auth|TossAuth|403|401' | grep -oE '"event_type": "[^"]{0,80}"|"detail": "[^"]{0,160}"|"status": [0-9]+|"url": "[^"]{0,80}"' | sort | uniq -c | sort -rn | head -10
echo "=== 토큰 (30m)"; echo "issued=$(docker logs --since 30m $A 2>&1 | grep -c toss_token_issued) refresh=$(docker logs --since 30m $A 2>&1 | grep -c toss_token_refresh) fail403=$(docker logs --since 30m $A 2>&1 | grep -c '토큰 발급 실패: 403')"
echo "=== 토스 요청 수 (30m)"; docker logs --since 30m $A 2>&1 | grep -oE 'openapi.tossinvest.com/[a-z0-9/_-]+' | sort | uniq -c | sort -rn | head -5
echo "=== nginx 30m: /api/fundamentals"; docker logs --since 30m updown_live-web-1 2>&1 | grep "/api/fundamentals" | grep -oE '"(GET|POST) /api/fundamentals/[a-z_]+[^"]*" [0-9]+ .*ups=[^ ]+' | sed -E 's/\?[^"]*"/"/; s/ [0-9]+ "[^"]*" "[^"]*"//' | sort | uniq -c | sort -rn | head -6
