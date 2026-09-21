#!/usr/bin/env bash
# 열린 판이 **언제부터** 걸었나 (anchor) — 화면의 매매 목록에 예열 걸음의 과거 매매가 섞이는지 가른다.
#
# 🔴 `wf_trades` 에는 **라이브 매매만** 남는다. 세션 원장(화면의 대시보드 `outcomes`)은
#    예열 걸음의 매매까지 센다 — anchor 가 과거면 화면에 '손절' 이 줄줄이 보이는데
#    DB 로는 "닫힌 매매 없음" 이 나온다. 둘이 어긋나는 자리가 여기다.
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"

# ⚠️ `anchor` 는 시각이 아니라 **판을 식별하는 열쇠 문자열**이다
#    (`GATE:BNB_USDT:private_strategy@0.1.0:live`). 시각으로 캐스팅하면 에러가 난다 —
#    한 번 겪었다(2026-09-21). 걷기 시작점은 세션 봉인(`/state` 의 `seal.start`)에 있다.
echo "=== 열린 판 · 열쇠와 생성 시각"
$P -F'|' -tAc "
select r.symbol, r.playbook, r.anchor, r.opened_at::timestamp(0) as 판생성,
       r.live, round(coalesce(r.margin_budget,0),3) as 자리예산, round(r.leverage,2) as 배율
from wf_runs r where r.closed_at is null order by r.symbol" 2>&1 | head -20

echo
echo "=== 그 판들의 라이브 매매 건수 (DB 에 남은 것만)"
$P -F'|' -tAc "
select r.symbol, count(t.id) as 매매, count(t.id) filter (where t.closed_at is not null) as 닫힘
from wf_runs r left join wf_trades t on t.run_id = r.id
where r.closed_at is null group by 1 order by 1" 2>&1 | head -20
