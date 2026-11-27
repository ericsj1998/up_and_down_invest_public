#!/usr/bin/env bash
# 서버에서 거래소 공개 주소(키 없음)의 응답만 확인한다(읽기만 · 앱 · DB 안 건드림 · 임시 파일 지움).
#   bash scripts/ops/remote.sh scripts/ops/probe_binance_public_1128.sh
set -u
U="https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=3&catalogId="
for c in 161 49; do
  f="/tmp/probe_cms_$c.json"
  code=$(curl -s -o "$f" -w '%{http_code}' --max-time 15 "$U$c")
  echo "공지 목록 $c · http $code · 바이트 $(wc -c < "$f") · releaseDate $(grep -o '"releaseDate"' "$f" | wc -l) 건"
  grep -o '"title":"[^"]*"' "$f" | head -n 3 | cut -c1-110
  rm -f "$f"
done
f=/tmp/probe_ei.json
code=$(curl -s -o "$f" -w '%{http_code}' --max-time 20 "https://fapi.binance.com/fapi/v1/exchangeInfo")
echo "선물 종목 정보 · http $code · onboardDate $(grep -o '"onboardDate"' "$f" | wc -l) 건"
rm -f "$f"
TZ=Asia/Seoul date '+%m-%d %H:%M KST'
