#!/usr/bin/env bash
# 배포로 교체되기 전 컨테이너(멈춘 api 슬롯 · 1.7.2 구간)의 로그로 저평가 화면이 어땠나 · Caddy 접근 로그 (값 없음).
cd ~/updown 2>/dev/null || exit 1
OLD=$(docker ps -a --format '{{.Names}} {{.Status}}' | grep -E "^updown_live-api(_b)?-1 Exited" | cut -d' ' -f1 | head -1)
echo "=== 멈춘 슬롯: $OLD ($(docker inspect -f '{{.State.StartedAt}} ~ {{.State.FinishedAt}}' $OLD | cut -c1-45))"
echo "--- /fundamentals 접근 줄 (경로 · 상태)"
docker logs "$OLD" 2>&1 | grep -E '"(GET|POST) /fundamentals/' | grep -oE '/fundamentals/[a-z_]+|" [0-9]{3} ' | paste - - | sort | uniq -c | sort -rn | head -8
echo "--- screen/ranking 이벤트"
docker logs "$OLD" 2>&1 | grep -oE '"ts": "[^"]+"|"event_type": "(screen_[a-z_]+|ranking_[a-z_]+)"' | paste - - 2>/dev/null | grep event_type | tail -8
docker logs "$OLD" 2>&1 | grep -oE '"event_type": "(screen_[a-z_]+|ranking_[a-z_]+)"' | sort | uniq -c
echo "--- screen_price_missing / ranking_price_missing 상세"
docker logs "$OLD" 2>&1 | grep -E 'price_missing' | grep -oE '"detail": "[^"]{0,140}"' | sort | uniq -c | sort -rn | head -5
echo "--- http_5xx (1.7.2 부터 기록)"
docker logs "$OLD" 2>&1 | grep http_5xx | grep -oE '"path": "[^"]+"|"status": [0-9]+|"detail": "[^"]{0,160}"' | paste - - - | sort | uniq -c | sort -rn | head -8
echo "--- error 이벤트 종류"
docker logs "$OLD" 2>&1 | grep -E '"level": "error"' | grep -oE '"event_type": "[^"]{0,80}"' | sort | uniq -c | sort -rn | head -8
echo "=== Caddy 6h: fundamentals / chart-order 상태"
docker logs --since 6h updown_live-proxy-1 2>&1 | grep -E "fundamentals|chart-order" | grep -oE '"uri":"/api/[a-z_/-]+|"status":[0-9]+' | paste - - | sort | uniq -c | sort -rn | head -10
