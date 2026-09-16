#!/usr/bin/env bash
# 52차 BB 길이 사다리 — 세션 엔진 재현 (bb_period 25 · 30 · 창 4 x 종목 · 하한 켬 · 배율 1 · 관통 0.75 그대로) — 떼어 띄움 · 8 병렬
# 예상: 34차 실측(종목·창당 OOS 26분 · IS 25분 · FULL 47분 · Gate 11분) x 32 잡 ÷ 8 병렬 ≈ 1.5~2 시간
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
JOBS=logs/t279/_bb_jobs.txt
: > "$JOBS"
for P in 25 30; do
  for S in KRW-BTC KRW-ETH; do
    echo "FULL 2022-01-01 2026-09-14 $S doc $P" >> "$JOBS"
  done
done
for P in 25 30; do
  for S in KRW-BTC KRW-ETH KRW-XRP KRW-SOL KRW-DOGE KRW-ADA; do
    echo "OOS 2024-07-01 2026-09-14 $S doc $P" >> "$JOBS"
  done
  for S in KRW-BTC KRW-ETH; do
    echo "IS 2022-01-01 2024-06-30 $S doc $P" >> "$JOBS"
  done
  for S in BTC_USDT ETH_USDT XRP_USDT SOL_USDT DOGE_USDT ADA_USDT; do
    echo "GATE 2025-07-07 2026-08-22 $S gate $P" >> "$JOBS"
  done
done
cat > logs/t279/_bb_one.sh <<'ONE'
#!/usr/bin/env bash
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
W=$1; S0=$2; E0=$3; SYM=$4; COST=$5; P=$6
uv run python scripts/research/scenarios/t279_parity.py --symbols "$SYM" --start "$S0" --end "$E0" --name "$W" --trigger 1h --step 1h --cost "$COST" --quiet \
  --playbook private_strategy --research-patch '{"breakout_pen_atr_min": 0.75}' --rule-params "{\"pen_min_atr\": \"0.75\", \"bb_period\": $P}" --tag "pen075_bb$P" \
  2>&1 | grep -v "registry.built\|Warning\|warn" | tail -2
echo "DONE $W $SYM bb$P $(date '+%T')"
ONE
chmod +x logs/t279/_bb_one.sh
LOGF=logs/t279/_bb_ladder_run.log
echo "start $(date '+%T') jobs $(wc -l < "$JOBS")" > "$LOGF"
setsid nohup bash -c "xargs -P 8 -L 1 bash logs/t279/_bb_one.sh < $JOBS; echo ALLDONE \$(date '+%T')" >> "$LOGF" 2>&1 < /dev/null &
echo $! > logs/t279/_bb_ladder.pid
sleep 3
echo "launched pid $(cat logs/t279/_bb_ladder.pid) alive=$(kill -0 "$(cat logs/t279/_bb_ladder.pid)" 2>/dev/null && echo yes || echo no)"
