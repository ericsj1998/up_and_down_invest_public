"""자원 창 — 관리자 전용 (T215 · 2026-09-04).

> 사용자: *"배포 시 배포 공간 스펙을 지정하기 위한 RAM·CPU·저장용량 등을 확인할 수 있는 기능, 창."*

한 응답에 다 담는다 — 화면이 여섯 군데를 따로 물으면 어느 하나가 늦을 때 표가 반쪽이 된다.

```
api      지금 이 프로세스 (psutil · cgroup)
engine   Redis 의 30초 비트 (`apps/engine/resource_beat`) — 없으면 null + 이유
db       pg_database_size · 표 상위 10 (pg_total_relation_size)
redis    used_memory
runs     실행 중 RUN / MAX_RUNNING
warnings RAM 85% · 디스크 80% (api·engine 각각)
```

문은 미들웨어가 건다 — `/admin/resources` 는 `roles.ADMIN_PREFIXES`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from updown.apps.api.walkforward import MAX_RUNNING, SESSIONS
from updown.apps.engine.resource_beat import ENGINE_KEY, EVERY_S
from updown.common import paths
from updown.common.resources import snapshot, warnings_for
from updown.marketdata import ratelimit

router = APIRouter(prefix="/admin/resources", tags=["admin-resources"])

_TABLES_SQL = sa.text(
    """
    select relname as name,
           pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(relname)) as bytes
    from pg_stat_user_tables
    order by bytes desc
    limit 10
    """
)
_DB_SIZE_SQL = sa.text("select pg_database_size(current_database())")


async def _db_stats(factory: Any) -> dict[str, Any]:
    """DB 크기와 큰 표 10개. 못 읽으면 `error` 를 담는다 — 0 으로 꾸미지 않는다 (규칙 #8)."""
    try:
        async with factory() as session:
            size = (await session.execute(_DB_SIZE_SQL)).scalar_one()
            rows = (await session.execute(_TABLES_SQL)).all()
        return {
            "size": int(size),
            "tables": [{"name": str(r.name), "bytes": int(r.bytes)} for r in rows],
        }
    except Exception as exc:
        return {"size": None, "tables": [], "error": repr(exc)}


async def _redis_stats(redis: Any) -> dict[str, Any]:
    try:
        info = await redis.info("memory")
        return {"used_memory": int(info.get("used_memory", 0)), "error": None}
    except Exception as exc:
        return {"used_memory": None, "error": repr(exc)}


async def _engine_snapshot(redis: Any) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = await redis.get(ENGINE_KEY)
    except Exception as exc:
        return None, f"redis 읽기 실패: {exc!r}"
    if not raw:
        return None, (
            f"engine 비트 없음 — {EVERY_S * 3}s 안에 스냅샷이 안 왔다 "
            "(engine 이 죽었거나 락 대기 · dev 에서 engine 컨테이너를 안 띄웠으면 정상)"
        )
    try:
        snap = json.loads(raw)
    except ValueError as exc:
        return None, f"engine 비트 파싱 실패: {exc!r}"
    age = time.time() - float(snap.get("ts", 0))
    snap["age_s"] = age
    return snap, None


@router.get("")
async def resources(request: Request) -> dict[str, Any]:
    """자원 한 장 — 배포 스펙 산정(T64)의 입력이자 운영 중 경고선.

    Args:
        request: 요청. 앱 상태의 Redis 로 엔진 스냅샷을 읽는다.

    Returns:
        api 스냅샷 · engine 스냅샷(비트가 없으면 None 과 사유) · 경고 문장들.
    """
    state = request.app.state.updown
    api = snapshot("api", disks={"logs": paths.logs_root(), "root": Path("/")})
    engine, engine_note = await _engine_snapshot(state.redis)
    warns = warnings_for(api)
    if engine is not None:
        warns += warnings_for(engine)
    elif engine_note:
        warns.append(engine_note)
    return {
        "ts": time.time(),
        "api": api,
        "engine": engine,
        "engine_note": engine_note,
        # T217 — 거래소 한도 소모 + 최근 60초 경로별 weight (어느 호출이 먹는지)
        "ratelimit": {
            "meters": ratelimit.snapshot(),
            "paths": {v: ratelimit.paths_1m(v) for v in ("BINANCE", "GATE")},
        },
        "db": await _db_stats(state.session_factory),
        "redis": await _redis_stats(state.redis),
        "runs": {"running": len(SESSIONS), "max": MAX_RUNNING},
        "warnings": warns,
    }
