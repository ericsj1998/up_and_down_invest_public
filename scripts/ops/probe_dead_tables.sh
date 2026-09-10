#!/usr/bin/env bash
# 0129 드롭 대상 14표의 행 수 — 실계좌 DB (배포 전 · T272). 값은 표 이름과 행 수만.
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
SQL="select relname, n_live_tup from pg_stat_user_tables where relname in ('users','trade_proposals','approved_orders','orders','positions','transitions','risk_plan_revisions','risk_policies','broker_credentials','account_balances','allocation_ledger','backtest_runs','notifications','portfolio_snapshots') order by 1"
echo "=== live DB"; docker exec updown_live-postgres-1 psql -U updown -d updown -At -c "$SQL"
echo "=== demo DB"; docker exec updown_live-postgres-1 psql -U updown -d updown_demo -At -c "$SQL"
echo "=== alembic"; docker exec updown_live-postgres-1 psql -U updown -d updown -At -c "select version_num from alembic_version"
docker exec updown_live-postgres-1 psql -U updown -d updown_demo -At -c "select version_num from alembic_version"
echo "=== env names (값은 안 찍는다)"
grep -oE "^(EDGAR_USER_AGENT|DB_POOL_SIZE|DB_MAX_OVERFLOW|SESSION_SECRET|NVIDIA_API_KEY)=" .env.live | sed 's/=$/=<set>/'
