#!/usr/bin/env bash
# 스왑 드나듦 · 프로세스별 스왑(읽기만)
vmstat 2 4 | tail -3 | awk '{print "드나듦 si",$7,"so",$8,"· 대기 wa",$16,"· 쉼 id",$15}'
free -m | awk '/Mem:/{print "가용 "$7} /Swap:/{print "스왑 "$3}'
for p in $(pgrep -f 'uvicorn|postgres|orderflow|caddy|redis' | head -14); do
  awk -v p="$p" '/^Name:/{n=$2} /^VmRSS:/{r=int($2/1024)} /^VmSwap:/{s=int($2/1024)} END{if(s>5) print n, "RSS", r, "스왑", s}' /proc/$p/status 2>/dev/null
done | sort -k5 -n -r | head -6
bash /tmp/orderflow_server.sh 2>/dev/null | tail -8 | cut -c1-150
