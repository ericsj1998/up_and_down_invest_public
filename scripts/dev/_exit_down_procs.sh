#!/usr/bin/env bash
# 224차 격자 — 런처가 몇 개 살아 있나 (중복 실행 점검 · 읽기 전용).
#
# 🔴 첫 런처(setsid nohup)를 **Windows 쪽 ps** 로 보고 죽었다고 판단했었다 — 거기서는 WSL
#    프로세스가 안 보인다. 실제로 살아 있었다면 같은 잡이 두 번씩 돌고 있다.
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
echo "=== xargs 런처"
pgrep -af "xargs -P 8" | grep -v pgrep
echo "=== 지금 도는 잡 (창 · 종목 · 팔) — 같은 줄이 둘이면 중복이다"
# ⚠️ xargs 런처 줄도 스크립트 이름을 담고 있다 — 빼지 않으면 런처 둘이 '중복 잡' 으로 세어진다.
pgrep -af "_exit_down_one.sh" | grep -v pgrep | grep -v xargs \
  | awk '{print $4, $7, $9}' | sort | uniq -c
echo "=== DONE 줄 vs 결과 파일"
echo "DONE $(grep -c '^DONE ' logs/t279/_exit_down_run.log) · 파일 $(ls -1 logs/t279/ | grep -cE 'pen075_w14_x1[0257]\.json')"
