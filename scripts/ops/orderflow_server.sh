#!/usr/bin/env bash
# T283 수집기 — 서버에서 (2026-09-17). 프로필 `orderflow` 서비스라 기본 기동에서 빠져 있다.
#
#   상태:  bash scripts/ops/remote.sh scripts/ops/orderflow_server.sh          (파일 복사 + 실행 · 기본 status)
#   켜기:  bash scripts/ops/remote.sh -- 'bash /tmp/orderflow_server.sh start'  (위 status 를 한 번 돌린 뒤 · /tmp 에 복사돼 있다)
#   끄기:  bash scripts/ops/remote.sh -- 'bash /tmp/orderflow_server.sh stop'
#
# 공개 엔드포인트만 읽는다(키 없음 · 주문 없음). 실계좌·데모와 다른 볼륨(flowlogs). 값(시크릿)은 찍지 않는다.
set -u
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
C="docker compose --env-file .env.live -f docker/compose.base.yml -f docker/compose.live.yml --profile orderflow"
NAME=updown_live-orderflow-1
case "${1:-status}" in
  start)
    $C up -d --no-deps orderflow && sleep 5
    docker ps --format "{{.Names}}\t{{.Status}}\t{{.Image}}" | grep orderflow || echo "⛔ 컨테이너가 없다 — docker logs $NAME"
    ;;
  stop)
    $C stop orderflow && echo "중지" ;;
  status)
    echo "=== container"; docker ps -a --format "{{.Names}}\t{{.Status}}\t{{.Image}}" | grep orderflow || echo "안 돔(프로필 서비스 · start 로 켠다)"
    echo "=== env 키 유무 (값은 안 찍는다)"; grep -cE "^ORDERFLOW_(GATE|UPBIT)=" .env.live 2>/dev/null || echo "0 (비면 핵심 6종)"
    echo "=== heartbeat (last 가 1~2분 안이면 살아 있다)"
    docker exec "$NAME" cat logs/orderflow/heartbeat.json 2>/dev/null || echo "heartbeat 없음"
    echo "=== 파일 수 · 크기"
    docker exec "$NAME" sh -c 'find logs/orderflow -name "*.jsonl" | wc -l; du -sh logs/orderflow 2>/dev/null | cut -f1' 2>/dev/null
    echo "=== 최근 오류(30m)"; docker logs --since 30m "$NAME" 2>&1 | grep -c '실패' 2>/dev/null
    echo "=== 메모리 — 스왑이 늘면 수집기부터 끈다"; free -m | grep -E "Mem|Swap"; docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}" "$NAME" 2>/dev/null
    ;;
  *) echo "사용법: $0 start|stop|status"; exit 2 ;;
esac
