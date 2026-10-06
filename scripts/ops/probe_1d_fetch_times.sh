API=$(docker ps --filter status=running --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
for S in ADA_USDT DYDX_USDT LTC_USDT; do
  echo "== $S 1d 조회 시각(22h)"
  docker logs --timestamps --since 22h "$API" 2>&1 | grep "contract=$S&interval=1d" | cut -c1-19 | tr '\n' ' '
  echo
done
