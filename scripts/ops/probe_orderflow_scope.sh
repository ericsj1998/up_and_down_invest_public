#!/usr/bin/env bash
# T283 수집기가 **무엇을** 모으고 있나 — 시장·종목·줄 수·최신 (읽기 전용).
#
# 사전 등록 판정 기준이 "Gate·업비트 두 출처가 같은 방향" 이라, 한쪽만 모이고 있으면
# 표본이 아무리 쌓여도 판정이 안 된다. 그걸 먼저 확인한다.
cd ~/updown 2>/dev/null || exit 1
OF=$(docker ps --format '{{.Names}}' | grep -i orderflow | head -1)
[ -z "$OF" ] && { echo "orderflow 컨테이너 없음"; exit 1; }
echo "=== 컨테이너: $OF"
docker exec "$OF" sh -c '
  date -u "+지금 %Y-%m-%d %H:%M:%S UTC"
  echo "--- 시장 디렉토리"
  ls -1 /app/logs/orderflow 2>/dev/null
  for m in /app/logs/orderflow/*/; do
    [ -d "$m" ] || continue
    case "$m" in *heartbeat*) continue;; esac
    echo "--- $m"
    for s in "$m"*/; do
      [ -d "$s" ] || continue
      n=$(basename "$s")
      first=$(ls -1 "$s" 2>/dev/null | head -1)
      last=$(ls -1 "$s" 2>/dev/null | tail -1)
      lines=$(cat "$s"*.jsonl 2>/dev/null | wc -l)
      kinds=$(tail -n 400 "$s$last" 2>/dev/null | sed -n "s/.*\"kind\": *\"\([a-z_]*\)\".*/\1/p" | sort | uniq -c | tr "\n" " ")
      echo "   $n : $first ~ $last · $lines 줄 · 최근 종류[ $kinds]"
    done
  done
  echo "--- heartbeat"
  cat /app/logs/orderflow/heartbeat.json 2>/dev/null | head -c 500; echo'
echo
echo "=== 수집기가 선언한 대상 (환경)"
docker exec "$OF" sh -c 'echo "ORDERFLOW_MARKETS=$ORDERFLOW_MARKETS"; echo "ORDERFLOW_SYMBOLS=$ORDERFLOW_SYMBOLS"' 2>/dev/null
echo
echo "=== 최근 로그 (오류만)"
docker logs --since 24h "$OF" 2>&1 | grep -iE 'error|exception|fail|refus|denied' | tail -n 8 | cut -c1-240
