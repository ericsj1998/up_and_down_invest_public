#!/usr/bin/env bash
# T227 실측 — 보류(held) 가 실제로 발생했나 · 문의가 남았나. 이메일은 찍지 않는다 (id 앞 8자리 · 등급 · 시각만).
set -u
PG=updown_live-postgres-1
docker exec -i "$PG" psql -U updown -d updown -tA <<'SQL'
select 'accounts total ' || count(*) from accounts;
select 'contacts total ' || count(*) from account_contacts;
select 'hold_after_hours ' || coalesce((select value::text from app_settings where key='auth.hold_after_hours'), 'default');
select left(id::text, 8), role, role_collection, (approved_at is not null) as approved, blocked, to_char(created_at, 'MM-DD HH24:MI'), to_char(last_login_at, 'MM-DD HH24:MI'), hold_released_until is not null as extended from accounts order by created_at;
SQL
