#!/usr/bin/env bash
# 화면이 무엇을 보여 주고 있나 — 브라우저가 실제로 부른 것 + API 가 실제로 돌려준 것 (읽기 전용).
#
# 🔴 서버 프로브가 "정상" 이라고 화면 문제를 두 번 잘못 짚었다. 그래서 **브라우저 요청부터** 본다.
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"

echo "=== 보유중 4건의 부분 청산·감축 흔적 (반익 · 재레버 감축이면 '뭔가 정리됐다' 로 보인다)"
$P -F'|' -tAc "
select r.symbol, t.outcome, t.half_at::timestamp(0) as 반익시각, t.half_by as 반익사유,
       round(coalesce(t.half_price,0),6) as 반익가,
       round(coalesce(t.realized_adjust,0),4) as 감축실현USDT,
       round(coalesce(t.funding_paid,0),4) as 펀딩,
       round(coalesce(t.filled_leverage,0),3) as 체결배율, round(t.leverage,3) as 의도배율,
       t.updated_at::timestamp(0) as 갱신
from wf_trades t join wf_runs r on r.id=t.run_id
where t.closed_at is null and t.opened_at is not null order by r.symbol"

echo
echo "=== 열린 판 · 멈춤 사유 (halt) 가 있나"
$P -F'|' -tAc "
select r.symbol, r.playbook, r.live, r.opened_at::timestamp(0), r.updated_at::timestamp(0),
       coalesce(r.closed_reason,'-') as 닫힌사유,
       left(coalesce(r.meta_json::text,''), 160) as meta
from wf_runs r where r.closed_at is null order by r.symbol"

echo
echo "=== 최근 닫힌 판 (판이 닫히면 화면에서 매매가 사라져 '정리됐다' 로 보인다)"
$P -F'|' -tAc "
select r.symbol, r.playbook, r.closed_at::timestamp(0), coalesce(r.closed_reason,'-')
from wf_runs r where r.closed_at is not null and r.closed_at > now() - interval '3 days'
order by r.closed_at desc limit 15"
echo "(비면 없음)"

echo
echo "=== 브라우저가 실제로 부른 것 (nginx 접근 로그 최근 40줄 · 상태코드)"
NG=$(docker ps --format '{{.Names}}' | grep -E "nginx|web|caddy" | head -1)
if [ -n "$NG" ]; then
  docker logs --tail 200 "$NG" 2>&1 | grep -vE '\.(js|css|png|svg|ico|woff2?)' | tail -n 40 | cut -c1-200
else
  echo "  프록시 컨테이너를 못 찾음: $(docker ps --format '{{.Names}}' | tr '\n' ' ')"
fi

echo
echo "=== 화면이 읽는 API 가 지금 돌려주는 값 (컨테이너 안에서 호출)"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker exec "$API" sh -c '
  for path in /api/health /api/rebalancer/funds; do
    echo "--- $path"
    wget -qO- --timeout=8 "http://127.0.0.1:8000$path" 2>/dev/null | head -c 1200
    echo
  done' 2>&1 | head -40
