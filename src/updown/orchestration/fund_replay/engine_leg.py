"""펀드 재현의 **엔진 다리** — 세션 꼴이 아닌 다리를 코인 판과 한 시계 · 한 지갑으로 걷는다.

(T608 · 2026-10-28)

실계좌에서 엔진 다리(`orchestration.leg_engines` · 하루 한 번 걷는 다리)는 조정자 포트로 끼어
① 실현을 펀드 총자본에 넣고 ② 몫 크기를 펀드 총자본에서 정하고 ③ 같은 계좌의 증거금을 코인 다리와
나눠 쓴다(코인 진입이 모자라면 늦게 든 몫부터 양보 · T533). 코인 재현과 엔진 재현을 따로 돌려 사후에
더하면 이 셋이 전부 빠진다 — 그래서 하네스 안에서 같은 걸음으로 돈다(사용자 2026-10-28 *"그냥 합쳐서
돌려야 하는 거 아닌가"*).

엔진의 이름은 모른다(플랫폼 원칙 · T224 · T532): 여기가 아는 것은 `LegEngine` 면 + 되감기에 필요한
셋 — 마감 처리(`on_close(day)`) · 양보(`yield_margin`) · 몫 장부(`book.units` 의 체결가 · 계약 수 ·
유령).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.candle import Candle
    from updown.common.domain.instrument import Instrument, Market, Timeframe
    from updown.common.domain.session import MarketCalendar

HOUR = timedelta(hours=1)


class ReplayEngine(Protocol):
    """되감기가 엔진에 대고 하는 일 — `LegEngine` 면에 더해 셋."""

    book: Any
    """몫 장부 — `units: {열쇠: 몫}` · 몫은 `fill` · `contracts` · `ghost` 를 가진다."""

    async def on_close(self, day: date) -> None:
        """거래일 `day` 마감 처리."""
        ...

    async def yield_margin(self, need: Decimal) -> Decimal:
        """계좌 가용이 `need` 이상이 될 때까지 늦게 든 몫부터 닫는다."""
        ...


@dataclass(slots=True)
class ReplayEngineLeg:
    """엔진 다리 하나의 되감기 재료.

    Attributes:
        symbol: 종목(조정자 포트 열쇠).
        hours: 그 계약의 1H 봉(오름차순) — 모의 거래소에 흘리고 엔진의 일봉 재료가 된다.
        closes: `(처리 시각, 거래일)` 오름차순 — 마감 + 여유가 지나면 그날을 처리한다.
        quanto: 계약 하나의 명목 배수(증거금 계산).
        leverage: 다리 배율(격리 · 증거금 = 명목 ÷ 배율).
        sim: 모의 거래소(`execution.gateway.sim_adapter`) — `observe(high, low, last)` 로 값을
            흘린다.
        engine: 엔진(플러그인 팩토리가 만든 러너). 만든 뒤 `clock` 을 `now` 로 바꿔 끼운다.
        now_at: 되감기 시계(엔진 · 봉 조회가 "지금" 으로 읽는 값).
    """

    symbol: str
    hours: list[Any]
    closes: list[tuple[datetime, date]]
    quanto: Decimal
    leverage: Decimal
    sim: Any
    engine: Any = None
    now_at: datetime = field(default_factory=lambda: datetime(2000, 1, 1, tzinfo=UTC))
    _k_hour: int = 0
    _k_close: int = 0

    def now(self) -> datetime:
        """엔진 · 봉 조회에 꽂는 시계."""
        return self.now_at

    def hour_source(self) -> _HourSource:
        """엔진이 1H 봉을 읽는 출처 — 시계 앞(끝이 지금 뒤인 봉)은 안 준다."""
        return _HourSource(self)

    def margin_view(self, spare: Any) -> _MarginView:
        """엔진이 보는 주문 어댑터 — 가용만 펀드 지갑(코인 포지션까지 뺀 여유)으로 바꾼다.

        Args:
            spare: 펀드 가용을 주는 함수(`FundReplay.spare`).
        """
        return _MarginView(self.sim, spare)

    def held_margin(self) -> Decimal:
        """몫 장부가 든 격리 증거금 합 — 유령(포지션 없음)은 0."""
        if self.engine is None or self.leverage <= 0:
            return Decimal(0)
        total = Decimal(0)
        for unit in self.engine.book.units.values():
            if getattr(unit, "ghost", False) or int(getattr(unit, "contracts", 0) or 0) <= 0:
                continue
            total += Decimal(unit.fill) * Decimal(int(unit.contracts)) * self.quanto / self.leverage
        return total

    async def advance(self, t: datetime) -> list[tuple[str, str]]:
        """시계를 `t` 로 민다 — 끝난 1H 봉을 흘리고, 처리 시각이 지난 거래일을 마감한다.

        Args:
            t: 하네스 시계(정시 · UTC).

        Returns:
            사건들 `(종류, 설명)` — `close_day` · `day_missing`.
        """
        out: list[tuple[str, str]] = []
        while self._k_hour < len(self.hours):
            bar = self.hours[self._k_hour]
            end = bar.ts + HOUR
            if end > t:
                break
            # 마감 처리가 이 봉 안에 있으면 먼저 처리한다(실계좌: 마감 + 90초 뒤 · 그 뒤 봉은 아직
            # 없다)
            while self._k_close < len(self.closes) and self.closes[self._k_close][0] <= end:
                out.extend(await self._close(self.closes[self._k_close]))
                self._k_close += 1
            self.now_at = end
            self.sim.observe(bar.high, bar.low, bar.close)
            self._k_hour += 1
        while self._k_close < len(self.closes) and self.closes[self._k_close][0] <= t:
            out.extend(await self._close(self.closes[self._k_close]))
            self._k_close += 1
        self.now_at = max(self.now_at, t)
        return out

    async def _close(self, item: tuple[datetime, date]) -> list[tuple[str, str]]:
        at, day = item
        self.now_at = at
        if self.engine is None:
            return []
        try:
            await self.engine.on_close(day)
        except (
            Exception
        ) as exc:  # 그날 봉이 빠진 것(실계좌는 다음 깨어남에 다시) — 재현은 적고 넘어간다
            return [("day_missing", f"{day} {type(exc).__name__}: {str(exc)[:80]}")]
        return [("close_day", day.isoformat())]


class _HourSource:
    """엔진의 `HourSource` — 되감기 시계 뒤의 봉만 낸다."""

    def __init__(self, leg: ReplayEngineLeg) -> None:
        self._leg = leg

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """`[start, end)` 안이고 끝이 지금 이전인 1H 봉."""
        _ = (instrument, timeframe)
        now = self._leg.now_at
        return [c for c in self._leg.hours if start <= c.ts < end and c.ts + HOUR <= now]


class _MarginView:
    """모의 거래소 앞에 선 얇은 막 — `margins()` 의 가용만 펀드 지갑으로.

    모의 거래소는 자기 현금만 알아(코인 포지션을 모른다) 가용이 늘 넉넉하고, 그러면 엔진은 양보를
    한 번도 하지 않는다. 실계좌의 가용 = 지갑 - 모든 포지션 증거금이라 하네스의 `spare` 를 준다.
    """

    def __init__(self, sim: Any, spare: Any) -> None:
        self._sim = sim
        self._spare = spare

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sim, name)

    async def margins(self) -> dict[str, str]:
        """계좌 — 가용은 펀드 지갑 여유(코인 · 엔진 포지션 증거금을 뺀 값)."""
        base = dict(await self._sim.margins())
        base["available"] = str(self._spare())
        return base


def engine_closes(
    calendar: MarketCalendar,
    market: Market,
    first_day: date,
    end: datetime,
    settle_delay: timedelta,
) -> list[tuple[datetime, date]]:
    """거래일 처리 시각표 — `first_day` 부터 `end` 안에 마감 + 여유가 드는 날.

    Args:
        calendar: 거래소 달력.
        market: 일봉을 묶는 거래소(지수).
        first_day: 첫 거래일.
        end: 되감기 끝.
        settle_delay: 마감 뒤 처리까지 여유.
    """
    out: list[tuple[datetime, date]] = []
    d = first_day
    while d <= end.date():
        sess = calendar.regular_session(market, d)
        if sess is not None and sess[1] + settle_delay <= end:
            out.append((sess[1] + settle_delay, d))
        d += timedelta(days=1)
    return out


def engine_trades(engine: Any, symbol: str) -> list[dict[str, Any]]:
    """닫힌 몫 기록을 결과 JSON 꼴로."""
    rows: Sequence[dict[str, Any]] = getattr(engine.book, "closed", ())
    return [{"symbol": symbol, **{k: str(v) for k, v in row.items()}} for row in rows]
