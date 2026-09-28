#!/usr/bin/env bash
# nginx(web) 만 다시 띄운다 — 스틸 중 DNS 실패로 옛 API IP 를 문 채 502 (2026-09-29). 매매 컨테이너는 안 건드린다.
#   bash scripts/ops/remote.sh scripts/ops/restart_web.sh
docker restart updown_live-web-1 && echo "web 재시작"
sleep 6
docker exec updown_live-web-1 sh -c 'wget -q -O- -T 8 http://127.0.0.1/api/health 2>&1 | head -c 120; echo'
docker logs --tail 3 updown_live-web-1 2>&1 | cut -c1-200
