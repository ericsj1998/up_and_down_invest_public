#!/usr/bin/env bash
# 최근 감사 발견(live_audit_found) 내용 (읽기 전용) — 코드 · 종목 · note.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker logs --since 30m "$API" 2>&1 | grep 'live_audit_found' | grep -oE '"ts": "[^"]+"|"symbol": "[^"]+"|"code": "[^"]+"|"note": "[^"]{0,200}|"run": "[^"]+"' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 6
