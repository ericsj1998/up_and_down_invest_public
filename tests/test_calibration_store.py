"""교정 원장 — **관측이 주문을 막지 않는다** (T185).

## 무엇을 지키나

    ① 추가 전용이다 — 같은 (판·매매·역할) 을 두 번 적으면 **두 행**이 남는다
    ② naive 시각을 거부한다 (절대 규칙 #7)
    ③ DB 가 죽어도 **던지지 않는다** (절대 규칙 #8-1)

③ 이 이 파일의 존재 이유다. 교정 원장은 관측이고, 여기서 예외가 나면 주문 경로가
통째로 멈춘다 — 관측을 붙이다가 매매를 세우는 것이 가장 나쁜 결과다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from updown.orchestration.walkforward.store import RunStore


class _Broken:
    """무엇을 하든 터지는 세션 팩토리 — DB 장애를 흉내 낸다."""

    def __call__(self) -> _Broken:
        return self

    async def __aenter__(self) -> _Broken:
        raise RuntimeError("DB 가 죽었다")

    async def __aexit__(self, *_: object) -> bool:
        return False


class _Session:
    """더한 것을 그대로 들고 있는 가짜 세션."""

    def __init__(self, sink: list[Any]) -> None:
        self._sink = sink
        self.committed = 0

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    def add(self, row: Any) -> None:
        self._sink.append(row)

    async def commit(self) -> None:
        self.committed += 1


def _store(sink: list[Any]) -> RunStore:
    return RunStore(lambda: _Session(sink))  # pyright: ignore[reportArgumentType]


@pytest.mark.asyncio
async def test_records_one_row() -> None:
    sink: list[Any] = []
    run = uuid.uuid4()
    await _store(sink).record_calibration(
        run,
        kind="entry",
        trade_id="t1",
        role="진입",
        intended_price=Decimal("100.5"),
        judge_ts=datetime(2026, 8, 31, tzinfo=UTC),
        wanted_contracts=Decimal("12"),
        sent_contracts=Decimal("11"),
        extra={"equity": "1000"},
    )
    assert len(sink) == 1
    row = sink[0]
    assert row.kind == "entry"
    assert row.intended_price == Decimal("100.5")
    # 🔴 원하던 수량과 나간 수량이 **둘 다** 남아야 증거금 한도 발동을 셀 수 있다.
    assert row.wanted_contracts == Decimal("12")
    assert row.sent_contracts == Decimal("11")


@pytest.mark.asyncio
async def test_append_only_not_upsert() -> None:
    """같은 (판·매매·역할) 을 두 번 적으면 **두 행**이다.

    `wf_orders` 는 덮어써서 *지금 상태*를 드는데, 교정은 *모든 시도*를 세는 일이라
    정반대다. 덮어쓰면 거절 3회 뒤 체결 1회가 **체결 1회**로만 보인다.
    """
    sink: list[Any] = []
    store = _store(sink)
    run = uuid.uuid4()
    for price in ("100", "101"):
        await store.record_calibration(
            run, kind="entry", trade_id="t1", role="진입", intended_price=Decimal(price)
        )
    assert len(sink) == 2
    assert [str(one.intended_price) for one in sink] == ["100", "101"]


@pytest.mark.asyncio
async def test_naive_timestamp_is_refused() -> None:
    """naive 시각은 거부한다 (절대 규칙 #7).

    조용히 받으면 UTC 인지 KST 인지 모르는 값이 원장에 쌓이고, 지연을 재는 순간
    9시간이 섞인다.
    """
    with pytest.raises(ValueError, match="tz-aware"):
        await _store([]).record_calibration(
            uuid.uuid4(), kind="entry", judge_ts=datetime(2026, 8, 31)
        )


@pytest.mark.asyncio
async def test_db_failure_does_not_raise() -> None:
    """🔴 DB 가 죽어도 **던지지 않는다** (절대 규칙 #8-1).

    이 표는 관측이다. 여기서 예외가 나면 주문 경로가 멈추고, 그러면 관측을 붙이려다
    매매를 세운 것이 된다 — 가장 나쁜 결과다.
    """
    store = RunStore(_Broken())  # pyright: ignore[reportArgumentType]
    await store.record_calibration(uuid.uuid4(), kind="entry", intended_price=Decimal("1"))


@pytest.mark.asyncio
async def test_funding_needs_no_trade() -> None:
    """펀딩은 매매에 안 매달린다 — `trade_id` 가 비어도 남는다."""
    sink: list[Any] = []
    await _store(sink).record_calibration(uuid.uuid4(), kind="funding", amount=Decimal("-1.25"))
    assert sink[0].trade_id == ""
    assert sink[0].amount == Decimal("-1.25")
