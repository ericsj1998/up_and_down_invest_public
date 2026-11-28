"""공급 일정 사건 표 쓰기 — 실제 PostgreSQL 로 `put_pending` · `pending` · `waiting` · `settle`.

가짜 표로는 못 보는 것(넣거나 고치기 SQL 의 WHERE 절 · 짝 지은 줄을 안 고침 · 사라짐 → 다시
나타남 · 여러 줄 INSERT 의 넣은 수)을 실제 메서드로 돈다. `DATABASE_URL` 이 없으면
건너뛴다(`migrated_test_database` · `_test` DB).
값은 장치를 재는 중립 값이다.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from updown.common.db.session import create_engine, create_session_factory
from updown.orchestration.rebalancer.announce_job import AnnouncementStore, EventRow
from updown.orchestration.rebalancer.supply_job import (
    PENDING,
    REASON_DROPPED,
    REASON_LISTED,
    REASON_OUTSIDE,
    REASON_SMALL,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 28, 1, 23, tzinfo=UTC)
H = timedelta(hours=1)


@pytest.fixture
async def store(migrated_test_database: str) -> AsyncIterator[AnnouncementStore]:
    """실제 DB 위 표 — 쓴 줄은 반드시 지운다(갈래 이름 `test_` 앞붙이)."""
    engine = create_engine(migrated_test_database)
    yield AnnouncementStore(create_session_factory(engine))
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM announcement_events WHERE kind LIKE 'test\\_%'"))
        await conn.execute(sa.text("DELETE FROM announcement_feeds WHERE kind LIKE 'test\\_%'"))
    await engine.dispose()


def _kind() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


def _row(kind: str, token: str, at: datetime, reason: str, *, seen: datetime = NOW) -> EventRow:
    return EventRow(kind, token, at, seen, "W/1", f"t {reason}", "AAAUSDT", None, reason)


async def test_pending_rows_are_rewritten_but_decided_rows_are_not(
    store: AnnouncementStore,
) -> None:
    kind = _kind()
    at = NOW + 72 * H
    a, b, c = (_row(kind, f"{x}@d", at, REASON_LISTED) for x in ("aaa", "bbb", "ccc"))
    assert await store.put_pending([a, b, c], PENDING) == 3
    assert {r.token for r in await store.pending(kind, PENDING, NOW)} == {"aaa@d", "bbb@d", "ccc@d"}
    # 다음 날: aaa 는 작아짐 · bbb 는 사라짐 · seen_at 이 새로 적힌다
    later = NOW + 24 * H
    small = replace(a, reason=REASON_SMALL, seen_at=later)
    gone = replace(b, reason=REASON_DROPPED, seen_at=later)
    assert await store.put_pending([small, gone], PENDING) == 2
    got = {r.token: r for r in await store.pending(kind, PENDING, NOW)}
    assert got["aaa@d"].reason == REASON_SMALL and got["aaa@d"].seen_at == later
    assert got["bbb@d"].reason == REASON_DROPPED
    # 사라졌던 것이 다시 나타나면 listed
    assert await store.put_pending([replace(b, seen_at=later + H)], PENDING) == 1
    assert {r.token for r in await store.waiting(kind, REASON_LISTED, NOW, at)} == {
        "bbb@d",
        "ccc@d",
    }
    # 짝을 지은 줄(계약 있음) · 결과를 적은 줄(순위 밖)은 다시 고치지 않는다
    paired = replace(c, contract="CCC_USDT", reason="ok", seen_at=at - 4 * H)
    outside = replace(got["bbb@d"], reason=REASON_OUTSIDE, seen_at=at - 4 * H)
    assert await store.settle([paired, outside]) == 2
    retry = [replace(c, reason=REASON_LISTED), replace(b, reason=REASON_LISTED)]
    assert await store.put_pending(retry, PENDING) == 0
    assert await store.waiting(kind, REASON_LISTED, NOW, at) == []
    # 짝 지은 줄만 우주 점검 · 러너에 보인다
    active = await store.active([kind], NOW)
    assert [(r.token, r.contract) for r in active] == [("ccc@d", "CCC_USDT")]
    # 이미 지난 사건은 아직 정하지 않은 줄에서도 빠진다(`after`)
    assert {r.token for r in await store.pending(kind, PENDING, at)} == set()


async def test_seen_rows_are_appended_once_and_runner_reads_only_known_feeds(
    store: AnnouncementStore,
) -> None:
    kind = _kind()
    at = NOW + 72 * H
    seen = _row(kind + "_seen", "aaa@d@2026-11-28", at, "seen")
    assert await store.add([seen]) == 1
    assert await store.add([replace(seen, title="again")]) == 0  # 같은 날 기록은 한 번
    paired = replace(_row(kind, "aaa@d", at, REASON_LISTED), contract="AAA_USDT", reason="ok")
    await store.put_pending([_row(kind, "aaa@d", at, REASON_LISTED)], PENDING)
    await store.settle([replace(paired, seen_at=at - 4 * H)])
    # 받기 상태가 없으면 모름(None) · 처음 받기 성공 뒤엔 사건이 보인다 · 오래되면 다시 모름
    assert await store.for_symbol([kind], "AAA_USDT", at, 900, {kind: 48 * 3600}) is None
    await store.mark(kind, now=at - 5 * H, ok=True, backfilled=True, stats={"list_n": 3})
    got = await store.for_symbol([kind], "AAA_USDT", at, 900, {kind: 48 * 3600})
    assert got is not None and [e.token for e in got] == ["aaa@d"]
    assert await store.for_symbol([kind], "AAA_USDT", at + 44 * H, 900, {kind: 48 * 3600}) is None
    feed = await store.feed(kind)
    assert feed is not None and feed.stats == {"list_n": 3}
