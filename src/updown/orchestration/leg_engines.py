"""외부 다리 엔진 — 탐지기 · 세션 꼴이 아닌 펀드 다리를 **이름 없이** 붙이는 접점.

펀드 다리는 보통 세션(`Session` + `LiveRunner`)이 탐지기 신호로 돈다. 그 꼴에 안 맞는 다리 —
하루 한 번 걷고, 한 포지션 안에 몫을 여럿 두고, 지정가를 며칠 걸어 두는 다리 — 는 자기 러너를
가진 **엔진**으로 붙는다. 플랫폼은 엔진의 이름을 모른다: 설치된 패키지가 entry point
`updown.leg_engines` 로 `register() -> ((엔진 이름, 팩토리), …)` 를 내고, 매매법 선언의
`engine: <이름>` 이 그것을 고른다(`analysis/plugins.py` 탐지기와 같은 길).

펀드가 엔진에 바라는 것은 조정자 포트와 같다(실현 누계 · 다리별 실현 · 예산 받기) — 그래서 엔진은
조정자에 종목 포트로 끼고, 실현은 펀드 총자본에 들고, 다리 선언이 `drawdown_isolated` 면 브레이크
계산에서 빠진다(`Coordinator.isolate`).

증거금 양보: 같은 계좌의 다른 다리 진입이 증거금이 모자라면 `request_margin` 이 도는 엔진들에게
비켜 달라고 묻는다(엔진이 몫을 닫아 증거금을 푼다). 세션 러너와 엔진은 서로를 import 하지 않고
이 모듈에서 만난다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol, cast

from updown.common.domain.instrument import Instrument
from updown.common.domain.session import MarketCalendar
from updown.common.logging.setup import get_logger

LEG_ENGINES_GROUP = "updown.leg_engines"

_logger = get_logger("orchestration.leg_engines")

TRADE_OPEN = "open"
TRADE_TAKE_PROFIT = "take_profit"
TRADE_STOP_LOSS = "stop_loss"
TRADE_SIGNAL_EXIT = "signal_exit"
TRADE_OUTCOMES = frozenset({TRADE_OPEN, TRADE_TAKE_PROFIT, TRADE_STOP_LOSS, TRADE_SIGNAL_EXIT})
"""엔진 매매 한 줄의 결과 낱말 — 원장 `Outcome` 으로 옮긴다(`leg_ledger.engine_record`)."""


@dataclass(frozen=True, slots=True)
class EngineTrade:
    """엔진 다리의 매매 한 건 — 판 원장(`wf_trades`)에 같은 꼴로 적힐 재료 (T830).

    엔진은 한 포지션 안에 몫을 여럿 둔다. **몫 하나 = 매매 한 건**이다.

    Attributes:
        trade_id: 몫 열쇠(엔진 안에서 유일 · 원장 `trade_id`).
        kind: 몫 종류 이름(표시 · 기록용).
        opened_at: 몫이 실제로 찬 시각(UTC).
        entry: 체결가.
        contracts: 진입 체결 계약 수.
        multiplier: 계약 승수.
        stop: 몫의 재난 손절가(없으면 None).
        target: 몫의 매도 지정가(규칙 청산 몫이면 None).
        closed_at: 다 닫힌 시각. 열려 있으면 None.
        exit_price: 닫힌 계약의 가중 평균가. 열려 있으면 None.
        fees: 진입 + 청산 수수료 USDT(메이커 환급은 음수).
        pnl: 실현 손익 USDT(진입 · 청산 수수료 포함 · 펀딩 제외). 열려 있으면 None.
        outcome: `TRADE_OUTCOMES` 의 낱말.
        note: 어떻게 끝났나(엔진의 사유 낱말 그대로).
        short: 숏 몫인가(지금 엔진은 롱뿐).
        adopted: 원장 사본을 적기 전에 이미 든 몫인가 — 진입 수수료가 추정값이고 성과 표본에 안
            섞는다(원장 `Actor.ADOPTED`).
    """

    trade_id: str
    kind: str
    opened_at: datetime
    entry: Decimal
    contracts: int
    multiplier: Decimal
    stop: Decimal | None = None
    target: Decimal | None = None
    closed_at: datetime | None = None
    exit_price: Decimal | None = None
    fees: Decimal = Decimal(0)
    pnl: Decimal | None = None
    outcome: str = TRADE_OPEN
    note: str = ""
    short: bool = False
    adopted: bool = False

    def __post_init__(self) -> None:
        """닫힘 칸이 함께 있는지 · 낱말이 맞는지 확인한다(반쯤 닫힌 줄은 원장을 거짓말하게 한다)."""
        if self.outcome not in TRADE_OUTCOMES:
            raise ValueError(f"엔진 매매 결과 낱말 {self.outcome!r} — {sorted(TRADE_OUTCOMES)}")
        closed = (self.closed_at, self.exit_price, self.pnl)
        if any(v is None for v in closed) != all(v is None for v in closed):
            raise ValueError(
                f"{self.trade_id}: 닫힘 칸(시각 · 청산가 · 손익)은 함께 있거나 함께 없다"
            )
        if (self.closed_at is None) != (self.outcome == TRADE_OPEN):
            raise ValueError(f"{self.trade_id}: 열림 낱말과 닫힘 시각이 어긋난다")
        if self.contracts <= 0 or self.entry <= 0 or self.multiplier <= 0:
            raise ValueError(f"{self.trade_id}: 계약 · 체결가 · 승수는 양수다")


class EngineTradeSink(Protocol):
    """엔진이 매매 한 줄을 넘기는 곳 — 원장 사본을 쓴다.

    🔴 `put` 은 **막히지 않고 실패하지 않는다**(절대 규칙 8-1). 엔진 장부 파일이 진짜 장부이고
    원장은 사본이라, 쓰기가 늦거나 실패해도 엔진의 매매(특히 닫기)는 그대로 간다. 실패는 구현이
    폴백 파일 + 오류 로그로 남긴다. 같은 줄을 여러 번 넘겨도 된다(같은 `trade_id` 는 덮어쓴다).
    """

    def put(self, trade: EngineTrade) -> None:
        """한 줄을 넘긴다(열림 · 고침 · 닫힘 모두 같은 길)."""
        ...


class LegEngine(Protocol):
    """펀드가 엔진 다리에 대고 하는 일."""

    findings: list[str]

    @property
    def symbol(self) -> str:
        """굴리는 종목."""
        ...

    def realized(self) -> Decimal:
        """실현 손익 누계(수수료 포함) — 조정자가 지난 정산과의 차이만 총자본에 더한다."""
        ...

    def realized_of(self, attributions: frozenset[str]) -> Decimal:
        """이 엔진의 다리 귀속 키가 `attributions` 에 있으면 실현 누계, 아니면 0."""
        ...

    def set_budget(self, budget: Decimal) -> None:
        """조정자가 주는 예산 — 엔진은 몫 크기를 펀드 총자본에서 정하므로 기록만 한다."""
        ...

    def held_contracts(self) -> int:
        """장부가 아는 거래소 포지션 계약 수(고아 점검이 읽는다)."""
        ...

    def waiting_entry(self) -> bool:
        """걸어 둔 진입 지정가가 있나(거래소에만 보이는 주문이 고아가 아니라 대기임을 안다)."""
        ...

    def guarded_fill(self) -> int:
        """다음 마감에 몫이 될 찬 계약 중 재난 손절을 이미 받은 수 — 대조가 보유로 센다."""
        ...

    def status(self) -> dict[str, Any]:
        """화면 · 점검용 요약."""
        ...

    async def refresh(self) -> None:
        """화면용 거래소 값(증거금 · 미실현)을 다시 읽는다(짧은 캐시는 엔진이 든다)."""
        ...

    async def run(self) -> None:
        """멈출 때까지 돈다."""
        ...

    def stop(self) -> None:
        """다음 깨어남에서 멈춘다(포지션 · 주문은 그대로)."""
        ...

    async def wind_down(self) -> None:
        """포지션을 시장가로 다 닫고 주문 · 손절을 거둔 뒤 멈춘다(다리를 펀드에서 뺄 때)."""
        ...

    async def yield_margin(self, need: Decimal) -> Decimal:
        """계좌 가용이 `need` 이상이 될 때까지 늦게 든 몫부터 닫는다 — 푼 증거금 추정치."""
        ...


@dataclass(frozen=True, slots=True)
class LegEngineContext:
    """엔진을 만드는 재료 — 펀드가 채운다.

    Attributes:
        fund_id: 펀드 id(엔진 상태 파일 · 주문 이름의 재료).
        attribution: 다리 귀속 키(`매매법 id@버전`).
        instrument: 종목.
        orders: 주문 어댑터(`execution.gateway` 가 준 것 · 절대 규칙 #0).
        quotes: 조회 어댑터(`marketdata.provider` 가 준 것).
        calendar: 거래소 달력.
        capital: 펀드 총자본(실현 기준) — 몫 크기의 바탕.
        state_dir: 엔진 상태 파일을 둘 곳(마운트된 볼륨).
        leverage: 다리 선언 배율.
        settings: 다리 선언의 엔진 값(없으면 빈 사전).
        trades: 매매 한 줄을 넘길 곳(T830 · 판 원장 사본). None 이면 원장에 안 적는다
            (시험 · 저장소 없음).
    """

    fund_id: str
    attribution: str
    instrument: Instrument
    orders: object
    quotes: object
    calendar: MarketCalendar
    capital: Callable[[], Decimal]
    state_dir: Path
    leverage: Decimal
    settings: Mapping[str, object] = field(default_factory=dict[str, object])
    trades: EngineTradeSink | None = None


LegEngineFactory = Callable[[LegEngineContext], LegEngine]


def discovered_leg_engines() -> dict[str, LegEngineFactory]:
    """Entry point `updown.leg_engines` 로 등록된 엔진 전부.

    Returns:
        엔진 이름 → 팩토리.

    Raises:
        ValueError: 두 패키지가 같은 이름을 등록했을 때(조용히 하나를 고르지 않는다 · 규칙 #8).
    """
    out: dict[str, LegEngineFactory] = {}
    for point in sorted(entry_points(group=LEG_ENGINES_GROUP), key=lambda item: item.name):
        register = point.load()
        for name, factory in cast("Iterable[tuple[str, LegEngineFactory]]", register()):
            if name in out:
                raise ValueError(f"다리 엔진 이름 {name!r} 이 둘이다 — entry point 를 확인하라")
            out[name] = factory
    return out


RUNNING: dict[tuple[str, str], LegEngine] = {}
"""도는 엔진 — `(거래소, 종목)` → 엔진. 고아 점검 · 양보 요청 · 화면이 같은 표를 본다."""


def register_running(market: str, engine: LegEngine) -> None:
    """도는 엔진으로 적는다(한 거래소 한 종목에 하나 — 같은 계약의 포지션은 하나다).

    Raises:
        ValueError: 그 종목에 이미 엔진이 돈다.
    """
    key = (market, engine.symbol)
    if key in RUNNING and RUNNING[key] is not engine:
        raise ValueError(f"{market}:{engine.symbol} 에 이미 엔진이 돈다")
    RUNNING[key] = engine


def unregister_running(market: str, symbol: str) -> None:
    """도는 엔진 표에서 뺀다."""
    RUNNING.pop((market, symbol), None)


def running_on(market: str) -> dict[str, LegEngine]:
    """그 거래소에서 도는 엔진 — 종목 → 엔진."""
    return {sym: eng for (mkt, sym), eng in RUNNING.items() if mkt == market}


async def request_margin(market: str, need: Decimal) -> Decimal:
    """같은 계좌의 엔진들에게 계좌 가용이 `need` 이상이 되게 비켜 달라고 묻는다.

    Args:
        market: 거래소(계좌).
        need: 이 진입이 원하는 **가용 증거금**(USDT · 부족분이 아니라 목표).

    Returns:
        엔진들이 푼 증거금 추정 합. 도는 엔진이 없으면 0.

    Note:
        엔진마다 계좌 가용을 다시 읽고 이미 넉넉하면 아무것도 안 닫는다. 엔진 하나가 실패해도
        진입을 막지 않는다 — 실패를 남기고 다음 엔진에 묻는다. 진입은 그대로 가용 안에서 줄어
        나간다(지금까지의 선착순 규칙).
    """
    freed = Decimal(0)
    for symbol, engine in sorted(running_on(market).items()):
        try:
            freed += await engine.yield_margin(need)
        except Exception as exc:
            _logger.error(
                "leg_engine_yield_failed",
                payload={"market": market, "symbol": symbol, "error": repr(exc)[:200]},
            )
    return freed
