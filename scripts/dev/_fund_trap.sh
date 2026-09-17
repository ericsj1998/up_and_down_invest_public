#!/usr/bin/env bash
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
docker cp scripts/dev/_fund_create.py updown-api_demo-1:/tmp/_fund_create.py
docker cp scripts/dev/_fund_trap.json updown-api_demo-1:/tmp/_fund_trap.json
if [ "${1:-}" = "create" ]; then
  LOGF=logs/t279/_fund_create.log; echo "start $(date +%T)" > "$LOGF"
  setsid nohup bash -c "docker exec updown-api_demo-1 python /tmp/_fund_create.py create; echo CREATE_DONE \$(date +%T)" >> "$LOGF" 2>&1 < /dev/null &
  sleep 3; cat "$LOGF"
else
  docker exec updown-api_demo-1 python /tmp/_fund_create.py
fi
