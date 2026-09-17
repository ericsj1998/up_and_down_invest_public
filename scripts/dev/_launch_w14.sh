#!/usr/bin/env bash
# 74차 E0w 세션 재현 — 룰 0.2 + stop_min_pct 1.4 · 창 4 x 종목 · 하한 켬 · 배율 1 · 관통 0.75 · 떼어 띄움 · 8 병렬
# 예상: 34차 실측(종목·창당 OOS 26분 · IS 25분 · FULL 47분 · Gate 11분) x 16 잡 ÷ 8 병렬 ≈ 1 시간
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
JOBS=logs/t279/_w14_jobs.txt
: > "$JOBS"
for S in KRW-BTC KRW-ETH; do echo "FULL 2022-01-01 2026-09-14 $S doc" >> "$JOBS"; done
for S in KRW-BTC KRW-ETH KRW-XRP KRW-SOL KRW-DOGE KRW-ADA; do echo "OOS 2024-07-01 2026-09-14 $S doc" >> "$JOBS"; done
for S in KRW-BTC KRW-ETH; do echo "IS 2022-01-01 2024-06-30 $S doc" >> "$JOBS"; done
for S in BTC_USDT ETH_USDT XRP_USDT SOL_USDT DOGE_USDT ADA_USDT; do echo "GATE 2025-07-07 2026-08-22 $S gate" >> "$JOBS"; done
cat > logs/t279/_w14_one.sh <<'ONE'
#!/usr/bin/env bash
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
W=$1; S0=$2; E0=$3; SYM=$4; COST=$5
uv run python scripts/research/scenarios/t279_parity.py --symbols "$SYM" --start "$S0" --end "$E0" --name "$W" --trigger 1h --step 1h --cost "$COST" --quiet --playbook private_strategy --research-patch '{"breakout_pen_atr_min": 0.75}' --rule-params '{"pen_min_atr": "0.75", "stop_min_pct": "1.4"}' --tag pen075_w14 2>&1 | grep -v "registry.built\|Warning\|warn" | tail -2
echo "DONE $W $SYM w14 $(date '+%T')"
ONE
chmod +x logs/t279/_w14_one.sh
LOGF=logs/t279/_w14_run.log
echo "start $(date '+%T') jobs $(wc -l < "$JOBS")" > "$LOGF"
setsid nohup bash -c "xargs -P 8 -L 1 bash logs/t279/_w14_one.sh < $JOBS; echo ALLDONE \$(date '+%T')" >> "$LOGF" 2>&1 < /dev/null &
sleep 2; echo launched; cat "$LOGF"
