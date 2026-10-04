#!/usr/bin/env bash
# 실계좌 api 로그 한 줄 모양(읽기 전용 · 비밀값 없음 — 사건 이름이 든 줄의 키 이름만 · 2026-10-04).
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== session_entry 가 든 줄 수 · 첫 줄의 키 이름"
docker logs "$API" 2>&1 | grep -c 'session_entry'
docker logs "$API" 2>&1 | grep 'session_entry' | head -1 | grep -oE '"[a-z_]+":' | sort -u | tr '\n' ' '; echo
echo "=== 그 줄들의 사건 이름(키 무관)"
docker logs "$API" 2>&1 | grep -oE 'session_entry_[a-z_]+' | sort | uniq -c | sort -rn | head -20
echo "=== 로그 줄 수 · 레벨 분포"
docker logs "$API" 2>&1 | wc -l
docker logs "$API" 2>&1 | grep -oE '"level": ?"[a-z]+"' | sort | uniq -c
