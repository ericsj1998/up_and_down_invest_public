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

`/admin/resources/memory` (T310 R4 · 2026-09-26) — 이 프로세스가 **어디에** 메모리를 쓰나: 커널이 본
RSS · 스왑 · 판마다 급전이 쥔 봉 수 · 차트 캐시 · (원할 때만) 객체 종류 표. 같은 요약을 30분마다
`api_memory_beat` 로 로그에 남겨, 가동 시간이 지날수록 자라는지를 서버 프로브가 본다.
"""

from __future__ import annotations

import asyncio
import gc
import json
import sys
import time
from collections import Counter
from collections.abc import Sized
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Request

from updown.apps.api.walkforward import MAX_RUNNING, SESSIONS
from updown.apps.engine.resource_beat import ENGINE_KEY, EVERY_S
from updown.common import paths
from updown.common.logging.setup import get_logger
from updown.common.resources import snapshot, warnings_for
from updown.marketdata import ratelimit

router = APIRouter(prefix="/admin/resources", tags=["admin-resources"])
_logger = get_logger("apps.api.resources_admin")

MEMORY_BEAT_S = 30 * 60
"""메모리 요약 로그 주기 — 급전 봉이 자라는 속도(시간 단위)를 보기에 충분하고 로그는 하루 48줄."""
FIRST_BEAT_S = 120
"""첫 요약은 기동 2분 뒤(판 되살리기 뒤) — 가동 시간 0 의 기준선."""
_STATUS_KEYS = ("VmRSS", "VmSwap", "VmHWM", "Threads")
_MB = 1024 * 1024

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
    """Redis 메모리 사용량. 못 읽으면 `error` 를 담는다 — 0 으로 꾸미지 않는다 (규칙 #8)."""
    try:
        info = await redis.info("memory")
        return {"used_memory": int(info.get("used_memory", 0)), "error": None}
    except Exception as exc:
        return {"used_memory": None, "error": repr(exc)}


async def _engine_snapshot(redis: Any) -> tuple[dict[str, Any] | None, str | None]:
    """엔진 자원 비트 — 다른 프로세스라 Redis 로만 본다. 없으면 이유를 문장으로 준다.

    Args:
        redis: 앱 상태의 Redis.

    Returns:
        `(스냅샷 + age_s, None)` 또는 `(None, 사유)`. 비트가 없는 것은 죽음·락 대기·dev 에서
        안 띄움 중 무엇이든 "모른다" 이지 정상이 아니다 — 화면이 사유를 그대로 보여 준다.
    """
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


def parse_proc_status(text: str) -> dict[str, int]:
    """`/proc/<pid>/status` 의 메모리 줄을 바이트로 · 스레드는 개수로.

    Args:
        text: status 파일 내용.

    Returns:
        `{VmRSS, VmSwap, VmHWM, Threads}` 중 있는 것. 없는 줄은 빠진다 —
        0 으로 꾸미지 않는다 (규칙 #8).
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if key in _STATUS_KEYS and parts and parts[0].isdigit():
            out[key] = int(parts[0]) * (1 if key == "Threads" else 1024)
    return out


def feed_bars(feed: object) -> dict[str, int]:
    """급전이 쥐고 있는 마감 봉 수(시간축별) — 감싼 급전(창 · 봉인)은 안쪽을 따라간다.

    Args:
        feed: 세션의 급전.

    Returns:
        `{시간축: 봉 수}`. 봉 저장을 못 찾으면 빈 사전.

    Note:
        🔴 실계좌 `LiveFeed._rows` 는 받은 마감 봉을 **덧붙이기만** 하고 안 지운다
        (2026-09-26 확인) — 가동 시간에 비례해 자라는지 이 숫자로 본다.
    """
    for _ in range(4):
        # 실계좌 `LiveFeed._rows` · 봉인 `SealedFeed._source`
        rows: object = getattr(feed, "_rows", None) or getattr(feed, "_source", None)
        if isinstance(rows, dict):
            out: dict[str, int] = {}
            for frame, bucket in cast("dict[object, object]", rows).items():
                if isinstance(bucket, dict | list):
                    out[str(getattr(frame, "value", frame))] = len(cast("Sized", bucket))
            return out
        feed = next(
            (
                inner
                for name in ("_inner", "inner", "_feed")
                if (inner := getattr(feed, name, None)) is not None
            ),
            None,
        )
        if feed is None:
            break
    return {}


def board_memory() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """판마다 쥔 것 — 급전 봉 · 원장 기록 · 차트 캐시.

    Returns:
        `(판 목록 — 봉이 많은 순, 시간축별 봉 합)`.
    """
    boards: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for key, live in list(SESSIONS.items()):
        session = live.session
        bars = feed_bars(session.feed)
        totals.update(bars)
        full = getattr(live, "_full", {})
        boards.append(
            {
                "key": key,
                "symbol": session.instrument.symbol,
                "feed_bars": bars,
                "records": len(session.ledger.records),
                "chart_frames": len(live.chart),
                "chart_full_bars": sum(len(rows) for rows in full.values()),
            }
        )
    boards.sort(key=lambda row: -sum(row["feed_bars"].values()))
    return boards, dict(totals)


def type_table(top: int) -> list[dict[str, Any]]:
    """가비지 컬렉터가 아는 객체 종류별 개수 · 얕은 크기 — 상위 `top` 개(크기순).

    Args:
        top: 몇 줄.

    Returns:
        `[{type, count, bytes}]`.

    Note:
        ⚠️ 모든 객체를 한 번 훑는다(수십만 개 · 1초 안팎). 요청할 때만 · 스레드에서 부른다.
    """
    count: Counter[str] = Counter()
    size: Counter[str] = Counter()
    for obj in gc.get_objects():
        name = type(obj).__name__
        count[name] += 1
        try:
            size[name] += sys.getsizeof(obj)
        except TypeError:
            continue
    return [{"type": n, "count": count[n], "bytes": size[n]} for n, _ in size.most_common(top)]


def process_memory() -> dict[str, Any]:
    """이 프로세스의 RSS · 스왑 · 최고 RSS · 스레드 — 못 읽으면 `error`."""
    try:
        return dict(parse_proc_status(Path("/proc/self/status").read_text(encoding="utf-8")))
    except OSError as exc:
        return {"error": repr(exc)}


@router.get("/memory")
async def memory(types: bool = False) -> dict[str, Any]:
    """메모리가 어디에 쓰이나 — 프로세스 · 판별 급전 봉 · 차트 캐시 · (선택) 객체 종류 표 (T310 R4).

    Args:
        types: 참이면 객체 종류 표(상위 25)를 스레드에서 센다 — 1초 안팎이 걸린다.

    Returns:
        `{ts, process, sessions, feed_bars_total, boards, gc_counts, types?}`.
    """
    boards, totals = board_memory()
    out: dict[str, Any] = {
        "ts": time.time(),
        "process": process_memory(),
        "sessions": len(boards),
        "feed_bars_total": totals,
        "boards": boards,
        "gc_counts": list(gc.get_count()),
    }
    if types:
        out["types"] = await asyncio.to_thread(type_table, 25)
    return out


async def memory_beat_loop() -> None:
    """30분마다 메모리 요약 한 줄(`api_memory_beat`) — 가동 시간에 따라 자라는지 서버 프로브가 본다.

    Note:
        읽기만 한다(급전 봉 수 · /proc) — 객체 종류 표는 안 센다. 실패해도 다음 주기에 다시 —
        관찰 로그는 매매를 막지 않는다 (§1.2.1).
    """
    delay = FIRST_BEAT_S  # 기동 직후 한 번 — 가동 시간 0 의 기준선
    while True:
        await asyncio.sleep(delay)
        delay = MEMORY_BEAT_S
        try:
            boards, totals = board_memory()
            proc = process_memory()
            _logger.info(
                "api_memory_beat",
                payload={
                    "rss_mb": round(proc.get("VmRSS", 0) / _MB, 1),
                    "swap_mb": round(proc.get("VmSwap", 0) / _MB, 1),
                    "sessions": len(boards),
                    "feed_bars_total": totals,
                    "top": [
                        {"symbol": row["symbol"], "bars": sum(row["feed_bars"].values())}
                        for row in boards[:3]
                    ],
                },
            )
        except Exception as exc:
            _logger.warning("api_memory_beat_failed", payload={"error": str(exc)[:200]})
