#!/usr/bin/env bash
# T283 수집기 제어 — start | stop | status (로컬 WSL · setsid nohup · pid 파일)
set -u
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
PID=logs/orderflow/capture.pid
LOG=logs/orderflow/capture.log
mkdir -p logs/orderflow
case "${1:-status}" in
  start)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "이미 돌고 있음 pid $(cat "$PID")"; exit 0; fi
    setsid nohup uv run python scripts/runtime/orderflow_capture.py >> "$LOG" 2>&1 < /dev/null &
    echo $! > "$PID"; sleep 2
    echo "시작 pid $(cat "$PID") · 로그 $LOG" ;;
  stop)
    if [ -f "$PID" ]; then kill "$(cat "$PID")" 2>/dev/null && echo "중지 pid $(cat "$PID")"; rm -f "$PID"; else echo "pid 파일 없음"; fi ;;
  status)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "돌고 있음 pid $(cat "$PID")"; else echo "안 돔"; fi
    [ -f logs/orderflow/heartbeat.json ] && echo "heartbeat: $(cat logs/orderflow/heartbeat.json)"
    echo "파일: $(find logs/orderflow -name '*.jsonl' | wc -l) · 크기: $(du -sh logs/orderflow 2>/dev/null | cut -f1)"
    [ -f "$LOG" ] && echo "최근 오류: $(grep -c '실패' "$LOG") 건" ;;
  *) echo "사용법: $0 start|stop|status"; exit 2 ;;
esac
