#!/usr/bin/env bash
# 224차 격자 진행 상황 — 실측 속도로 **남은 시간을 환산**한다 (읽기 전용).
#
# 🔴 **결과 파일을 센다 — DONE 줄을 세지 않는다** (2026-09-22). 런처 둘이 같은 잡을 두 번씩
#    돌린 적이 있고, 그때 DONE 은 64 인데 파일은 32 였다 — 진행률이 두 배로 부풀어 보였다.
#    끝난 일의 증거는 로그 줄이 아니라 산출물이다.
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
LOGF=logs/t279/_exit_down_run.log
JOBS=logs/t279/_exit_down_jobs.txt
[ -f "$LOGF" ] || { echo "로그 없음 — 안 떴다"; exit 1; }

TOTAL=$(wc -l < "$JOBS" 2>/dev/null | tr -d ' ')
FILES=$(ls -1 logs/t279/ 2>/dev/null | grep -cE 'pen075_w14_x1[0257]\.json')
LAUNCHERS=$(pgrep -fc "xargs -P 8" 2>/dev/null | head -1)
# ⚠️ xargs 런처 줄도 스크립트 이름을 담고 있다 — 빼지 않으면 런처가 '잡' 으로 세어진다.
RUNNING=$(pgrep -af "_exit_down_one.sh" 2>/dev/null | grep -v pgrep | grep -vc xargs)
START=$(head -1 "$LOGF" | sed -n 's/^start \([0-9-]* [0-9:]*\).*/\1/p')

echo "시작 $START · 지금 $(date '+%F %T')"
echo "런처 ${LAUNCHERS:-0}개 · 도는 잡 ${RUNNING:-0}개 · 결과 파일 **$FILES / $TOTAL**"
# 같은 잡이 둘이면 중복이다 — 바로 보이게.
DUPS=$(pgrep -af "_exit_down_one.sh" 2>/dev/null | grep -v pgrep | grep -v xargs \
  | awk '{print $4, $7, $9}' | sort | uniq -d | wc -l)
[ "${DUPS:-0}" -gt 0 ] && echo "🔴 중복으로 도는 잡 $DUPS개 — 런처가 겹쳤다"

if [ "${FILES:-0}" -gt 0 ] && [ -n "$START" ]; then
  EL=$(( $(date +%s) - $(date -d "$START" +%s) ))
  # ⚠️ 경과 전체로 나누면 중복으로 돌던 구간의 느린 속도가 섞인다 — 낙관하지 않는 쪽이다.
  LEFT=$(( (TOTAL - FILES) * EL / FILES ))
  echo "경과 $(( EL / 60 ))분 · 파일당 평균 $(( EL / FILES / 60 ))분(벽시계)"
  echo "→ 남은 약 **$(( LEFT / 60 ))분 이하** · 끝 예상 **$(date -d "+$LEFT seconds" '+%m-%d %H:%M')** (중복 구간 속도 기준이라 보수적)"
fi

echo "--- 팔별 결과 파일 (창 3 x 종목 6 = 18 이 만점)"
for A in x10 x12 x15 x17; do
  n=$(ls -1 logs/t279/ 2>/dev/null | grep -c "pen075_w14_$A\.json")
  echo "  $A: $n / 18"
done
echo "--- 최근 로그"
tail -4 "$LOGF"
