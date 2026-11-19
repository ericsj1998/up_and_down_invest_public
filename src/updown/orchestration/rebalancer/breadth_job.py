"""시장 전체 값 작업 — 전 종목 일봉 종가를 쌓고 그날의 종목 폭 · 뒤처진 묶음 수익을 한 줄로 남긴다.

판단은 하지 않는다 — 식은 `analysis.indicators.breadth.market_values` 가, 재료는
`marketdata.provider.gate_universe_facts` 가 낸다. 여기는 순서와 실패 처리만 조립한다
(orchestration 입주 조건).

하루의 흐름(UTC):
  ① 자정 직전 · 직후(`lead_s` · `capture_window_s` 안): 전 종목 시세를 **한 번** 받아 그날의
     종가로 쌓는다.
  ② 필요한 날(최근 `span` 일) 가운데 빈 날이 있으면: 종목마다 일봉을 받아 메운다(처음 한 번 ·
     자정을 놓친 날). 호출 사이에 `pace_s` 를 쉰다 — 리더 안에서 돌아도 이벤트 루프를 막지 않는다.
  ③ 쌓인 표로 값을 내어 `market_breadth_days` 에 한 줄 적는다. 러너는 그 줄을 읽기만 한다.

🔴 **부분 자료로 값을 내지 않는다.** 메우기에서 실패한 종목이 `max_fail_ratio` 를 넘으면 줄을 적지
않는다 — 러너는 "모름" 으로 읽고 그 문을 선언한 다리의 신규 진입을 보류한다(규칙 #8 · #8-1).
🔴 **같은 날 · 같은 일수의 줄은 한 번만.** 이미 있으면 거래소에 묻지 않는다(매시 점검의 비용 = DB
한 번 읽기).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.analysis.indicators.breadth import MarketValues, market_values
from updown.analysis.playbook.types import Playbook
from updown.common.db.models.market import MarketBreadthDay, MarketDailyClose
from updown.common.logging.setup import get_logger

_logger = get_logger("orchestration.rebalancer.breadth_job")

SOURCE_TICKER = "ticker"
SOURCE_CANDLE = "candle"


@dataclass(frozen=True, slots=True)
class BreadthParams:
    """값 한 줄을 정하는 일수 셋 — 매매법 선언(`entry_washed`)에서 온다.

    Attributes:
        ma_days: 종목 폭을 재는 이평 일수.
        lag_days: 뒤처진 묶음의 최근 수익 일수.
        rank_days: 뒤처진 묶음을 고르는 수익 일수.
    """

    ma_days: int
    lag_days: int
    rank_days: int

    @property
    def span(self) -> int:
        """값을 내는 데 필요한 날 수(마지막 닫힌 날 포함)."""
        return max(self.ma_days, self.lag_days + self.rank_days + 1)


@dataclass(frozen=True, slots=True)
class BreadthJobConfig:
    """작업 설정.

    Attributes:
        market: 종가를 쌓는 거래소 이름(표의 `market` 칸).
        lead_s: 자정 이 초 전부터는 "곧 닫힐 날" 을 겨눈다 — 자정 직전에 한 번 돌려 자정 걸음보다
            먼저 줄을 만든다.
        capture_window_s: 자정 뒤 이 초 안에 받은 시세만 "방금 닫힌 날의 종가" 로 친다.
        pace_s: 메우기에서 종목 사이에 쉬는 초.
        max_fail_ratio: 메우기에서 못 받은 종목 비율이 이보다 크면 값을 내지 않는다.
        min_symbols: 종가가 있는 종목이 이보다 적으면 값을 내지 않는다.
    """

    market: str = "GATE"
    lead_s: int = 20
    capture_window_s: int = 300
    pace_s: float = 0.2
    max_fail_ratio: float = 0.05
    min_symbols: int = 100


class CloseSource(Protocol):
    """전 종목 종가의 원천(`marketdata.gate.universe_facts.GateUniverseFacts` 꼴)."""

    async def contracts(self) -> dict[str, tuple[bool, bool]]:
        """`{계약 이름: (거래 중, 폐지 공지)}`."""
        ...

    async def last_closes(self) -> dict[str, Decimal]:
        """`{계약 이름: 최근 체결가}` — 한 번에."""
        ...

    async def daily_closes(self, contract: str, days: int, *, now: datetime) -> dict[date, Decimal]:
        """한 계약의 닫힌 일봉 종가."""
        ...


def params_of(playbooks: Iterable[Playbook]) -> tuple[BreadthParams, ...]:
    """매매법 선언에서 필요한 일수 셋을 모은다(겹치면 하나).

    Args:
        playbooks: 선언 전부.

    Returns:
        `entry_washed` 를 선언한 매매법들의 일수 셋 — 정렬된 고유 목록. 없으면 빈 튜플
        (= 작업이 할 일이 없다).
    """
    found = {
        BreadthParams(
            b.entry_washed.breadth_ma, b.entry_washed.lag_days, b.entry_washed.lag_rank_days
        )
        for b in playbooks
        if b.entry_washed is not None
    }
    return tuple(sorted(found, key=lambda p: (p.ma_days, p.lag_days, p.rank_days)))


class MarketCloseStore:
    """`market_daily_closes` · `market_breadth_days` 읽기 · 더하기 — 먼저 적힌 값이 남는다."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """세션 팩토리를 받는다.

        Args:
            factory: DB 세션 팩토리.
        """
        self._factory = factory

    async def days_with_closes(self, market: str, since: date, min_symbols: int) -> set[date]:
        """`since` 부터, 종가가 `min_symbols` 종목 이상 쌓인 날들."""
        async with self._factory() as session:
            rows = await session.execute(
                sa.select(MarketDailyClose.day)
                .where(MarketDailyClose.market == market, MarketDailyClose.day >= since)
                .group_by(MarketDailyClose.day)
                .having(sa.func.count() >= min_symbols)
            )
            return {row[0] for row in rows}

    async def add_closes(
        self, market: str, rows: Sequence[tuple[str, date, Decimal]], source: str
    ) -> None:
        """종가 칸을 더한다 — 이미 있는 칸은 그대로 둔다."""
        if not rows:
            return
        async with self._factory() as session:
            for k in range(0, len(rows), 2000):
                chunk = [
                    {"market": market, "symbol": s, "day": d, "close": c, "source": source}
                    for s, d, c in rows[k : k + 2000]
                ]
                await session.execute(
                    pg_insert(MarketDailyClose).values(chunk).on_conflict_do_nothing()
                )
            await session.commit()

    async def table(self, market: str, days: Sequence[date]) -> dict[str, list[Decimal | None]]:
        """`days`(오름차순)에 맞춘 종목별 종가 줄 — 없는 칸은 None."""
        index = {d: k for k, d in enumerate(days)}
        out: dict[str, list[Decimal | None]] = {}
        async with self._factory() as session:
            rows = await session.execute(
                sa.select(
                    MarketDailyClose.symbol, MarketDailyClose.day, MarketDailyClose.close
                ).where(
                    MarketDailyClose.market == market,
                    MarketDailyClose.day >= days[0],
                    MarketDailyClose.day <= days[-1],
                )
            )
            for symbol, day, close in rows:
                k = index.get(day)
                if k is not None:
                    out.setdefault(symbol, [None] * len(days))[k] = close
        return out

    async def breadth(self, market: str, day: date, params: BreadthParams) -> MarketValues | None:
        """그날 · 그 일수의 값 줄(없으면 None)."""
        async with self._factory() as session:
            row = (
                await session.execute(
                    sa.select(MarketBreadthDay).where(
                        MarketBreadthDay.market == market,
                        MarketBreadthDay.day == day,
                        MarketBreadthDay.ma_days == params.ma_days,
                        MarketBreadthDay.lag_days == params.lag_days,
                        MarketBreadthDay.rank_days == params.rank_days,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return MarketValues(
            breadth=row.breadth,
            lag_ret=row.lag_ret,
            breadth_count=row.breadth_count,
            lag_count=row.lag_count,
        )

    async def add_breadth(
        self, market: str, day: date, params: BreadthParams, values: MarketValues
    ) -> None:
        """값 줄을 더한다 — 이미 있으면 그대로 둔다."""
        async with self._factory() as session:
            await session.execute(
                pg_insert(MarketBreadthDay)
                .values(
                    market=market,
                    day=day,
                    ma_days=params.ma_days,
                    lag_days=params.lag_days,
                    rank_days=params.rank_days,
                    breadth=values.breadth,
                    lag_ret=values.lag_ret,
                    breadth_count=values.breadth_count,
                    lag_count=values.lag_count,
                )
                .on_conflict_do_nothing()
            )
            await session.commit()


class CloseStore(Protocol):
    """작업이 쓰는 저장소의 꼴(`MarketCloseStore`) — 시험은 메모리 대역을 넣는다."""

    async def days_with_closes(self, market: str, since: date, min_symbols: int) -> set[date]:
        """종가가 충분히 쌓인 날들."""
        ...

    async def add_closes(
        self, market: str, rows: Sequence[tuple[str, date, Decimal]], source: str
    ) -> None:
        """종가 칸 더하기."""
        ...

    async def table(self, market: str, days: Sequence[date]) -> dict[str, list[Decimal | None]]:
        """날 줄에 맞춘 종가 표."""
        ...

    async def breadth(self, market: str, day: date, params: BreadthParams) -> MarketValues | None:
        """값 줄 읽기."""
        ...

    async def add_breadth(
        self, market: str, day: date, params: BreadthParams, values: MarketValues
    ) -> None:
        """값 줄 더하기."""
        ...


@dataclass(frozen=True, slots=True)
class BreadthOutcome:
    """작업 한 번의 결과(로그 · 시험용).

    Attributes:
        day: 값의 날(마지막 닫힌 UTC 날).
        action: `exists`(이미 있음) · `computed`(새로 냄) · `skipped`(낼 수 없었음).
        captured: 자정 시세로 쌓은 종목 수.
        filled: 일봉으로 메운 종목 수.
        failed: 메우기에서 못 받은 종목 수.
        reason: `skipped` 의 까닭.
    """

    day: date
    action: str
    captured: int = 0
    filled: int = 0
    failed: int = 0
    reason: str = ""


async def run_day(
    store: CloseStore,
    source: CloseSource,
    params: Sequence[BreadthParams],
    *,
    now: datetime,
    cfg: BreadthJobConfig,
) -> BreadthOutcome:
    """방금 닫힌 날의 시장 전체 값을 낸다(이미 있으면 아무것도 안 한다).

    Args:
        store: 저장소.
        source: 전 종목 종가의 원천.
        params: 낼 일수 셋들(`params_of`). 비어 있으면 할 일이 없다.
        now: 지금 시각(UTC) — 결정론 시험을 위해 받는다.
        cfg: 설정.

    Returns:
        무엇을 했는지.

    Note:
        자정 시세는 `capture_window_s` 안일 때만 그날 종가로 친다 — 늦게 받은 "지금 가격" 을 지난
        날의 종가로 적지 않는다(그때는 일봉으로 메운다).
    """
    # 경계 = 방금 지났거나 `lead_s` 안에 올 UTC 자정. 자정 **직전**에 돌면 곧 닫힐 날을 겨눈다 —
    # 자정 정각의 걸음이 판정하기 전에 줄이 있어야 해서다(몇 초 이른 시세 = 그날 종가로 친다).
    here = now.astimezone(UTC)
    midnight = (here + timedelta(seconds=cfg.lead_s)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day = (midnight - timedelta(days=1)).date()
    if not params:
        return BreadthOutcome(day=day, action="skipped", reason="선언 없음")
    missing = [p for p in params if await store.breadth(cfg.market, day, p) is None]
    if not missing:
        return BreadthOutcome(day=day, action="exists")
    span = max(p.span for p in params)
    days = [day - timedelta(days=k) for k in range(span - 1, -1, -1)]
    have = await store.days_with_closes(cfg.market, days[0], cfg.min_symbols)
    captured = filled = failed = 0
    near = -cfg.lead_s <= (here - midnight).total_seconds() <= cfg.capture_window_s
    if day not in have and near:
        prices = await source.last_closes()
        await store.add_closes(
            cfg.market, [(name, day, price) for name, price in prices.items()], SOURCE_TICKER
        )
        captured = len(prices)
        have = await store.days_with_closes(cfg.market, days[0], cfg.min_symbols)
    if any(d not in have for d in days):
        names = sorted(
            name for name, (trading, _gone) in (await source.contracts()).items() if trading
        )
        rows: list[tuple[str, date, Decimal]] = []
        for name in names:
            try:
                got = await source.daily_closes(name, span + 1, now=now)
            except Exception as exc:
                failed += 1
                _logger.warning(
                    "breadth_fill_symbol_failed", payload={"symbol": name, "error": str(exc)[:120]}
                )
            else:
                filled += 1
                rows += [(name, d, c) for d, c in got.items() if days[0] <= d <= day]
            await asyncio.sleep(cfg.pace_s)
        if not names or failed / len(names) > cfg.max_fail_ratio:
            return BreadthOutcome(
                day=day,
                action="skipped",
                captured=captured,
                filled=filled,
                failed=failed,
                reason="메우기 실패가 많다",
            )
        await store.add_closes(cfg.market, rows, SOURCE_CANDLE)
        have = await store.days_with_closes(cfg.market, days[0], cfg.min_symbols)
    if day not in have:
        return BreadthOutcome(
            day=day,
            action="skipped",
            captured=captured,
            filled=filled,
            failed=failed,
            reason="그날 종가가 없다",
        )
    table = await store.table(cfg.market, days)
    for p in missing:
        values = market_values(
            _tail(table, p.span), ma_days=p.ma_days, lag_days=p.lag_days, rank_days=p.rank_days
        )
        await store.add_breadth(cfg.market, day, p, values)
    return BreadthOutcome(
        day=day, action="computed", captured=captured, filled=filled, failed=failed
    )


def _tail(
    table: Mapping[str, Sequence[Decimal | None]], span: int
) -> dict[str, Sequence[Decimal | None]]:
    """표의 마지막 `span` 칸만 — 일수 셋마다 필요한 길이가 다르다."""
    return {name: row[-span:] for name, row in table.items()}
