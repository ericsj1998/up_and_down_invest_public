#!/usr/bin/env bash
# T279 224차 — 청산 사다리 **아래쪽** (SMA 10·12·15·17) 세션 재현. 기준 SMA20 은 이미 있다.
#
#   bash scripts/dev/_launch_exit_down.sh            → 세 창 (BIS · BOOS · GATE) · 72 잡
#   WINDOWS=BIS bash scripts/dev/_launch_exit_down.sh → BIS 만 (24 잡 · 선별용)
#
# 🔴 청산 한 줄(`ma_exit_below_long`)만 다른 플레이북을 **같은 러너**로 돌린다. 다른 값이 한 글자라도
#    다르면 차이가 청산 때문인지 알 수 없다 (175차·188차와 같은 방식).
# ⚠️ 실측 속도(34차): 종목·창당 BIS 25분 · BOOS 26분 · GATE 11분. 8 병렬.
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a

WINDOWS="${WINDOWS:-BIS BOOS GATE}"
ARMS="${ARMS:-x10 x12 x15 x17}"
SYMS="BTC_USDT ETH_USDT XRP_USDT SOL_USDT DOGE_USDT ADA_USDT"
JOBS=logs/t279/_exit_down_jobs.txt
: > "$JOBS"

for W in $WINDOWS; do
  case "$W" in
    # 바이낸스 선물 공개 봉 — 129차 창 경계 그대로 (앞 두 달은 워밍업)
    BIS)  S0=2022-03-01; E0=2024-06-30; MKT=BINANCE; COST=gate ;;
    BOOS) S0=2024-07-01; E0=2026-08-22; MKT=BINANCE; COST=gate ;;
    GATE) S0=2025-07-07; E0=2026-08-22; MKT=;        COST=gate ;;
    *) echo "모르는 창: $W"; exit 1 ;;
  esac
  for A in $ARMS; do
    for S in $SYMS; do
      echo "$W $S0 $E0 $S $COST $A $MKT" >> "$JOBS"
    done
  done
done

cat > logs/t279/_exit_down_one.sh <<'ONE'
#!/usr/bin/env bash
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
W=$1; S0=$2; E0=$3; SYM=$4; COST=$5; ARM=$6; MKT=${7:-}
MARG=()
[ -n "$MKT" ] && MARG=(--market "$MKT")
uv run python scripts/research/scenarios/t279_parity.py \
  --symbols "$SYM" --start "$S0" --end "$E0" --name "$W" \
  --trigger 1h --step 1h --cost "$COST" --quiet \
  --playbook "private_strategy_$ARM" \
  --research-patch '{"breakout_pen_atr_min": 0.75}' \
  --rule-params '{"pen_min_atr": "0.75", "stop_min_pct": "1.4"}' \
  --tag "pen075_w14_$ARM" "${MARG[@]}" 2>&1 \
  | grep -v "registry.built\|Warning\|warn" | tail -2
echo "DONE $W $SYM $ARM $(date '+%T')"
ONE
chmod +x logs/t279/_exit_down_one.sh

LOGF=logs/t279/_exit_down_run.log
{
  echo "start $(date '+%F %T') · 창 [$WINDOWS] · 팔 [$ARMS] · 잡 $(wc -l < "$JOBS")"
  echo "예상: BIS 25분 · BOOS 26분 · GATE 11분 (종목·창당 · 34차 실측) ÷ 8 병렬"
} > "$LOGF"
setsid nohup bash -c "xargs -P 8 -L 1 bash logs/t279/_exit_down_one.sh < $JOBS; echo ALLDONE \$(date '+%F %T')" \
  >> "$LOGF" 2>&1 < /dev/null &
sleep 2
echo "띄웠다 — 진행은 tail -f $LOGF · 남은 잡은 grep -c DONE $LOGF"
cat "$LOGF"
