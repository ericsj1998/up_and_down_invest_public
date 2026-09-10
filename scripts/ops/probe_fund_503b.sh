#!/usr/bin/env bash
# 펀드 생성 503 — 그 시각 api 의 warning/error 이벤트 전부 (ts 로 · 값 없음) + 리더 표시.
cd ~/updown 2>/dev/null || exit 1
A=updown_live-api_b-1
echo "== 17:34:20~17:34:40 warning/error"
docker logs --since 3h $A 2>&1 | grep -E '"ts": "2026-09-10T17:34:(2|3)' | grep -E '"level": "(warning|error)"' | grep -oE '"event_type": "[^"]{0,120}"|"detail": "[^"]{0,200}"' | head -10
echo "== 17:34 분의 event_type 전체 (Gate·토스 요청 제외)"
docker logs --since 3h $A 2>&1 | grep -E '"ts": "2026-09-10T17:34' | grep -vE 'HTTP Request' | grep -oE '"event_type": "[^"]{0,90}"' | sort | uniq -c | sort -rn | head -14
echo "== 리더"
docker exec $A python -c "
import json,urllib.request
b=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))
print({k:v for k,v in b.items() if k!='dependencies'})
" 2>&1 | tail -2
