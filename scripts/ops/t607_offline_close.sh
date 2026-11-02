#!/usr/bin/env bash
# T607 — 옛 펀드 보관 + GATE 열린 판 접기를 **api 가 내려간 사이에 파일 · DB 로** 한다 (대안 · 2026-11-02 설계).
#
# 🔴 기본 길은 **화면**이다(콘솔 → 펀드 카드 "삭제" → "정말 삭제" = DELETE /rebalancer · RUN 표 "삭제 → 정말 삭제" =
#    DELETE /walkforward/sessions). 이 스크립트는 **화면 길이 막힐 때만**(로그인 · 리더 · 시한 문제) 쓴다 —
#    docs/planning/tasks/T607_account_split_fresh_fund.md "로그인 없는 길" 절.
#
# 하는 일(DELETE /rebalancer + DELETE /sessions 가 하는 것 중 api 가 내려가 있으면 빠지는 것만):
#   1 펀드 정의 파일  runlogs/funds/<id>.json → runlogs/funds/archive/<id>.json   (복원기는 archive/ 를 안 읽는다)
#   2 wf_runs         live · GATE · closed_at is null 인 행 전부 → closed_at = now() · closed_reason = 사유   (행은 지우지 않는다)
#   3 저널            runlogs/walkforward/<닫은 키>.json → 백업 폴더로 **옮긴다**(지우지 않는다 · 되돌릴 수 있게)
# 안 하는 일: 거래소 포지션 닫기(포지션 0 이 전제 · 아니면 멈춤) · wf_trades · event_logs(append-only) · 엔진 상태 파일.
#
# 전제(스크립트가 확인하고 아니면 멈춤): ① api 컨테이너(updown_live-api · api_b) 가 **둘 다 내려가 있다**
#   (살아 있으면 다음 틱이 펀드 파일을 다시 쓰고 러너는 그대로 돈다) ② 펀드 파일이 하나다(아니면 FUND=<id> 로 고른다)
#   ③ 거래소 포지션 · 대기 · 조건부 0 은 **사람이 내리기 전에** probe_gate.py 로 확인했다(내려간 뒤엔 확인 못 한다).
#
#   bash scripts/ops/remote.sh scripts/ops/t607_offline_close.sh            # 계획만(기본) — 전 표 · 되돌리기 SQL 미리보기
#   APPLY=1 bash scripts/ops/remote.sh scripts/ops/t607_offline_close.sh    # 실제로 (remote.sh 는 환경변수를 안 넘긴다 →
#                                                                            #   서버에서 직접: APPLY=1 bash /tmp/t607_offline_close.sh)
#   FUND=fund77e26368 … KEYS='k1,k2' …   펀드 id 고정 · 닫을 판 키를 일부로 제한(기본 = GATE 열린 행 전부)
#
# 되돌리기(백업 폴더 ~/updown/t607_<UTC>/ 에 전부 남긴다):
#   · 파일: archive/<id>.json 을 funds/ 로 되돌려 놓는다(백업 사본도 있다) → 다음 기동에 restore_funds 가 되살린다
#   · DB:   rollback.sql (closed_at · closed_reason 을 NULL 로) → 다음 기동에 autostart_live 가 되살린다
#   · 저널: journals/*.json 을 runlogs/walkforward/ 로 되돌린다(화면 "죽은 RUN" 목록용 · DB 가 진짜라 없어도 돈은 안 바뀐다)
set -uo pipefail
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }

VOL=${VOL:-/var/lib/docker/volumes/updown_live_runlogs/_data}
PG=${PG:-updown_live-postgres-1}
P="docker exec $PG psql -U updown -d updown"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=~/updown/t607_$STAMP
REASON="T607 계좌 분리 · 옛 펀드 보관 · 사람이 api 내려간 사이에 접음($STAMP)"
APPLY=${APPLY:-0}
KEYS=${KEYS:-}

echo "=== T607 오프라인 접기 · $STAMP · $([ "$APPLY" = "1" ] && echo '🔴 실행' || echo '계산만')"

echo "--- 전제 ① api 컨테이너가 내려가 있나"
UP=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' || true)
if [ -n "$UP" ]; then
  echo "⛔ api 가 살아 있다: $UP — 먼저 내린다(swap_stop_first.sh 앞부분 · 포지션 0 일 때만). 멈춘다"; exit 1
fi
docker ps --format '{{.Names}}' | grep -q "^$PG$" || { echo "⛔ postgres 컨테이너($PG) 가 없다"; exit 1; }
sudo test -d "$VOL/funds" || { echo "⛔ 볼륨 경로가 없다: $VOL/funds (sudo 필요)"; exit 1; }

echo "--- 전제 ② 펀드 파일"
FILES=$(sudo ls "$VOL/funds"/*.json 2>/dev/null || true)
echo "$FILES" | sed 's#^#   #'
if [ -n "${FUND:-}" ]; then
  FILE="$VOL/funds/$FUND.json"
  sudo test -f "$FILE" || { echo "⛔ $FILE 이 없다"; exit 1; }
else
  N=$(echo "$FILES" | grep -c . || true)
  [ "$N" = "1" ] || { echo "⛔ 펀드 파일이 ${N}개 — FUND=<id> 로 고른다. 멈춘다"; exit 1; }
  FILE=$FILES
  FUND=$(basename "$FILE" .json)
fi
echo "   접을 펀드: $FUND"
sudo grep -oE '"(label|playbook|anchor_skipped)": *("[^"]*"|null)' "$FILE" | sed 's#^#   #'

echo "--- 대상 wf_runs (live · GATE · 열린 행) — 이 표가 되돌릴 근거다"
WHERE="live and market = 'GATE' and closed_at is null"
if [ -n "$KEYS" ]; then
  INLIST=$(echo "$KEYS" | sed "s/[^,]*/'&'/g")
  WHERE="$WHERE and key in ($INLIST)"
fi
$P -F'|' -tAc "select key, symbol, playbook_id, opened_at::timestamp(0), coalesce(margin_budget,0)::numeric(12,2) from wf_runs where $WHERE order by symbol"
N_RUNS=$($P -tAc "select count(*) from wf_runs where $WHERE")
echo "   열린 행 ${N_RUNS}건"
echo "--- 열린 wf_trades (있으면 포지션이 있다는 뜻 — 0 이어야 한다)"
N_TR=$($P -tAc "select count(*) from wf_trades t join wf_runs r on r.id = t.run_id where r.live and r.market = 'GATE' and t.closed_at is null")
echo "   열린 매매 ${N_TR}건"
[ "$N_TR" = "0" ] || { echo "⛔ 열린 매매가 있다 — 거래소 포지션이 남았을 수 있다. 사람이 먼저 정리한다. 멈춘다"; exit 1; }

echo "--- 되돌리기 SQL (미리보기)"
ROLLBACK="update wf_runs set closed_at = null, closed_reason = null where closed_reason = '$REASON';"
echo "   $ROLLBACK"

if [ "$APPLY" != "1" ]; then
  echo "=== 계획만 돌았다. 실제로 하려면 서버에서 APPLY=1 bash /tmp/$(basename "$0")"
  exit 0
fi

echo "=== 🔴 실행 · 백업 $BACKUP"
mkdir -p "$BACKUP/journals"
echo "$ROLLBACK" > "$BACKUP/rollback.sql"
$P -F'|' -tAc "select key, symbol, playbook_id, opened_at, closed_at, closed_reason from wf_runs where $WHERE" > "$BACKUP/before_runs.tsv"
KEYLIST=$($P -tAc "select key from wf_runs where $WHERE")

echo "--- 1 펀드 파일 → archive/"
sudo mkdir -p "$VOL/funds/archive"
sudo cp -p "$FILE" "$BACKUP/$FUND.json"
if sudo test -f "$VOL/funds/archive/$FUND.json"; then
  echo "   archive/$FUND.json 이 이미 있다 — 덮어쓰지 않고 원본만 백업 폴더로 옮긴다"
  sudo mv "$FILE" "$BACKUP/$FUND.moved.json"
else
  sudo mv "$FILE" "$VOL/funds/archive/$FUND.json"
  if command -v python3 >/dev/null 2>&1; then
    sudo python3 - "$VOL/funds/archive/$FUND.json" "$STAMP" <<'PY'
import json, sys
p, stamp = sys.argv[1], sys.argv[2]
d = json.load(open(p, encoding="utf-8"))
d["dropped_at"] = stamp.replace("Z", "+00:00").replace("T", "T")
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
  else
    echo "   (python3 없음 — dropped_at 은 안 적었다 · 복원기는 archive/ 를 안 읽으므로 동작엔 차이 없다)"
  fi
fi
echo "   funds/:"; sudo ls "$VOL/funds"/*.json 2>/dev/null | sed 's#^#     #' || echo "     (없음 — 기대한 상태)"

echo "--- 2 wf_runs 닫기"
$P -tAc "update wf_runs set closed_at = now(), closed_reason = '$REASON', updated_at = now() where $WHERE"
echo "   남은 열린 행: $($P -tAc "select count(*) from wf_runs where live and market = 'GATE' and closed_at is null")건 (기대 0 · KEYS 로 제한했으면 그만큼)"

echo "--- 3 저널 → 백업 폴더"
MOVED=0
for k in $KEYLIST; do
  if sudo test -f "$VOL/walkforward/$k.json"; then
    sudo mv "$VOL/walkforward/$k.json" "$BACKUP/journals/$k.json" && MOVED=$((MOVED + 1))
  fi
done
echo "   옮긴 저널 ${MOVED}개 · 남은 저널 $(sudo ls "$VOL/walkforward"/*.json 2>/dev/null | wc -l)개"

echo "=== 끝 · 되돌리기: $BACKUP/rollback.sql · $BACKUP/$FUND.json → funds/ · journals/ → walkforward/"
echo "    다음: 새 이미지 슬롯 up → make live-check(열린 판 0 · 펀드 0 · live_run_resumed 0 · fund_restore_failed 0)"
