"""리밸런싱 펀드 API (T61 M1) — 바스켓을 하나의 능동형 인덱스로 굴린다.

## 무엇을 하나
- **생성**: 바스켓 종목마다 `_live_start` 로 **이미 검증된 라이브 세션**을 띄우고, 각 세션을
  `SessionBridge` 로 감싸 `Coordinator` 에 묶는다. 펀드 = 세션 핸들 집합 하나.
- **틱**: 각 세션 평가금액을 모아 목표 예산을 다시 나눠 각 세션 `margin_budget` 을 갱신한다
  (다음 진입에 적용). **주문은 안 낸다** — 세션의 러너가 자기 신호로 매매한다 (설계 B).
- **입출금**: `CashFlow` 로 흡수하고 즉시 재분배. TWR 로 성과를 입출금과 분리해 잰다.

## ⛔ 별도 취급 (T61)
이건 RUN 이 아니다. 자기 저장소(`FUNDS`)에 있고, 개별 세션은 펀드가 **소유**한다.

## ⚠️ 검증 상태
순수 코어(allocation·TWR·engine·coordinator·adapter)는 테스트 완료. 이 API 층은 **testnet
페이퍼로 백테스트(B)↔실제 일치**를 확인하기 전에는 실사용 금지 — 자동 4h 루프는 그 확인 뒤에 켠다.
"""

# 이 조립 지점은 walkforward 의 세션 생성·정리 내부 함수를 의도적으로 재사용한다.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

import yaml
from fastapi import APIRouter, Body, HTTPException, Request

from updown.analysis.playbook.select import default_playbook, load_playbooks
from updown.analysis.playbook.types import BreadthCap, DrawdownBrake
from updown.apps.api.admin import instrument_of
from updown.apps.api.auth import require_market_trade, require_playbook_trade
from updown.apps.api.walkforward import (
    LIVE_RUNNERS,
    SESSIONS,
    _close_live_position,
    _drop_one,
    _live_start,
)
from updown.common.cache import TtlCache
from updown.common.domain.capabilities import capabilities_of
from updown.common.domain.instrument import Market, MarketGroup, Timeframe
from updown.common.logging.setup import get_logger
from updown.decision.allocation import Basket, BasketError, BasketMember, as_members, rank_members
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.rebalancer import (
    Coordinator,
    PositionPort,
    RebalanceEngine,
    SessionBridge,
    SlotGate,
)
from updown.orchestration.rebalancer.anchor import (
    MODE_ACCOUNT,
    Anchored,
    AnchorState,
    anchored,
    dnw_since,
    initial_state,
    manual_flow,
)
from updown.orchestration.rebalancer.coordinator import TickReport
from updown.orchestration.rebalancer.legs import (
    FundLeg,
    LegError,
    declared_legs,
    leg_gate,
    legs_on,
    member_leverage,
    member_playbook,
)
from updown.portfolio.performance import CashFlow, TwrLedger

router = APIRouter(prefix="/rebalancer", tags=["rebalancer"])


@dataclass(slots=True)
class Fund:
    """능동형 인덱스 하나 — 조정자 + 소유한 세션 핸들들.

    Attributes:
        fund_id: 펀드 식별자.
        label: 화면 이름.
        coordinator: 배분+성과 조정자.
        handles: `{종목: 세션 handle}` — 펀드가 소유한 라이브 세션들.
        leverage: 세션 레버리지 (롱 기준 — 숏은 short_size_mult 로 축소).
        playbook: 각 세션이 도는 전략 (기본 = `recommended: true` 플레이북).
    """

    fund_id: str
    label: str
    coordinator: Coordinator
    handles: dict[str, str]
    leverage: Decimal
    playbook: str
    market: str = "GATE"
    weight_mode: str = "static"
    """비중 방식 — "static"(기존 · 저장 비중 고정) | "rank60"(일봉 N일 수익 랭크 · 일 1회 갱신).

    기본은 static — 기존 펀드는 한 톨도 안 바뀐다 (T201 배선 원칙).
    """
    alt_leverage: Decimal | None = None
    """코어(BTC·ETH) 밖 종목의 배율 오버라이드 (2.0.0 A5 = 5). None = 전 종목 `leverage`."""
    slots: int = 0
    """P3 자리 수(T279 83차). 양수면 `weight_mode: slots` — 예산 = 총자본 ÷ slots ·
    진입 문이 상한을 건다."""
    halt_after_stops: int = 0
    """P3 같은 날 연속 손절 정지 문턱(§24 · 2). 0 = 없음."""
    notional_cap: Decimal | None = None
    """총 명목 상한(자본 배수 · 93차 V2 = 2). None = 없음."""
    notional_fit: bool = False
    """상한에 걸리면 **남은 여유만큼 줄여서** 진입한다 (T286 · A 구성). False = 통째로 건너뛴다."""
    drawdown_brake: DrawdownBrake | None = None
    """낙폭 브레이크 — 고점 대비 `at` 이상 빠져 있으면 신규 진입 크기를 `scale` 배로 (T286).

    낙폭의 출처는 **펀드 장부의 TWR 지수**(`engine.ledger.drawdown_pct`)다 — 이미 저장·복원되고
    입출금 중립이라(입금이 낙폭을 메우지 않는다) T286 의 "정할 것 ②" 가 여기서 해결된다.
    입출금이 없는 백테스트에서는 실현 잔고 낙폭과 **같은 값**이라 143~145차 측정과 같은 자다.
    """
    breadth_cap: BreadthCap | None = None
    """조건부 총 명목 상한 — 폭 ≥ `min` 일 때만 상한을 `cap` 으로 (T289). None = 늘 `notional_cap`.

    폭은 문이 멤버 세션들의 `band_breaks` 를 합해 센다 — 돌아볼 봉 수·축은 `_attach_gate` 가 심는다.
    """
    legs: tuple[FundLeg, ...] = ()
    """**다리** (T291 · 이종 합성 매매법) — 비어 있으면 지금까지의 펀드(문 하나 · 매매법 하나).

    있으면 다리마다 자기 문(자리·상한·브레이크·폭)·자기 종목·자기 배율로 돈다. 그때 위의
    `halt_after_stops`·`notional_cap`·`notional_fit`·`drawdown_brake`·`breadth_cap` 은 **안 쓰인다**
    (다리 것이 쓰인다) — `slots` 만 멤버 예산(`총자본 ÷ 자리`)에 쓰인다.
    """
    anchor: AnchorState | None = None
    """자동 앵커 상태 (T285) — 첫 틱에서 잡히고 펀드 파일에 저장된다. None = 아직 앵커 전."""
    anchor_skipped: str | None = None
    """마지막 틱에서 앵커를 못 한 이유(다른 펀드·단독 판·계좌 못 읽음).

    None = 앵커됨. 화면에 그대로 보여 준다.
    """


FUNDS: dict[str, Fund] = {}
"""펀드 저장소 — RUN(SESSIONS)과 **분리**된 별도 원장 (T61 별도 취급)."""


def _fund_or_404(fund_id: str, *, pop: bool = False) -> Fund:
    """펀드를 찾는다 — 없으면 404. 라우터 7곳이 같은 세 줄을 적던 것을 모았다 (2026-09-06).

    Args:
        fund_id: 펀드 id.
        pop: 참이면 저장소에서 **빼면서** 돌려준다 (접기).

    Returns:
        펀드.

    Raises:
        HTTPException: 404 — 펀드가 없다.
    """
    fund = FUNDS.pop(fund_id, None) if pop else FUNDS.get(fund_id)
    if fund is None:
        raise HTTPException(404, f"{fund_id} 펀드가 없다")
    return fund


_logger = logging.getLogger("rebalancer")
_events = get_logger("rebalancer.gate")
"""구조화 로그(`event_type` 으로 찾는다) — 운영 프로브가 서버 로그에서 읽는 줄은 이쪽으로 남긴다."""

CORE_SYMBOLS = ("BTC_USDT", "ETH_USDT", "BTCUSDT", "ETHUSDT")
"""코어 종목 — `alt_leverage` 오버라이드에서 제외 (GATE·BINANCE 두 표기)."""

RANK_WINDOW_DAYS = 60
"""rank60 모멘텀 창(일). 창 길이는 측정으로 정한다 — 값은 매매법 쪽 문서에."""


def _attach_gate(
    coordinator: Coordinator,
    slots: int,
    halt_after_stops: int,
    notional_cap: Decimal | None = None,
    *,
    notional_fit: bool = False,
    brake: DrawdownBrake | None = None,
    breadth: BreadthCap | None = None,
    leverage: Decimal | None = None,
    legs: Sequence[FundLeg] = (),
) -> None:
    """P3 진입 문을 펀드의 모든 세션에 끼운다 (T279 83차 · 2026-09-18 · T286 으로 크기까지).

    Args:
        coordinator: 펀드 조정자 — 문은 **조정자의 포트 매핑을 그대로** 본다. 종목을 넣고 빼면
            같은 매핑이 바뀌므로 문도 따라간다.
        slots: 동시 보유 상한. 0 = 없음.
        halt_after_stops: 같은 날 연속 손절 정지 문턱. 0 = 없음.
        notional_cap: 총 명목 상한(자본 배수). None = 없음.
        notional_fit: 상한에 걸릴 때 남은 여유만큼 줄여서 진입할지 (T286).
        brake: 낙폭 브레이크 선언. None = 없음.
        breadth: 조건부 총 명목 상한 선언 (T289). None = 늘 `notional_cap`.
        leverage: 펀드 선언 배율 — 줄여서 진입의 **허용 하한**(1/4)을 여기서 만든다.
        legs: 다리들 (T291). 있으면 위 규칙 인자 대신 **다리마다 자기 문**을 세운다.

    Note:
        아무 규칙도 없으면 안 끼운다 — 기존 펀드(비중 배분)는 한 톨도 안 바뀐다. 종목을 나중에
        더하면 이 함수를 다시 불러 새 세션에도 같은 문을 준다(멱등).

        🔴 낙폭은 **조정자 장부에서 매번 읽는다**(람다) — 값을 복사해 두면 틱마다 갱신되는 낙폭이
        문에 반영되지 않아 브레이크가 첫 값에 얼어붙는다.
    """
    ledger = coordinator.engine.ledger
    if legs:
        # ⭐ T291 — 다리마다 자기 문. 세션에는 그 종목에 실린 다리의 노출을 심고, 폭은 조건부
        #    상한을 선언한 다리의 **종목에서만** 센다(측정이 핵심 6종만 셌다 — 18종으로 세면
        #    폭 ≥ 4 가 흔해진다).
        split = leg_gate(coordinator.ports, legs, lambda: ledger.drawdown_pct / Decimal(100))
        for symbol, port in coordinator.ports.items():
            if not isinstance(port, SessionBridge):
                continue
            mine = legs_on(legs, symbol)
            port.session.entry_gate = split
            port.session.leg_leverage = {leg.attribution: leg.exposure for leg in mine}
            wide = next((leg for leg in mine if leg.breadth_cap is not None), None)
            port.breadth_bars = (
                0 if wide is None or wide.breadth_cap is None else wide.breadth_cap.bars
            )
            port.breadth_frame = None if wide is None else Timeframe(wide.timeframe)
        return
    if slots <= 0 and halt_after_stops <= 0 and brake is None:
        return
    ports = cast(dict[str, PositionPort], coordinator.ports)
    gate = SlotGate(
        ports=ports,
        slots=slots,
        halt_after_stops=halt_after_stops,
        notional_cap=notional_cap,
        notional_fit=notional_fit,
        min_grant=(Decimal(0) if leverage is None else leverage / Decimal(4)),
        drawdown=(None if brake is None else lambda: ledger.drawdown_pct / Decimal(100)),
        brake_at=(Decimal(0) if brake is None else brake.at),
        brake_scale=(Decimal(1) if brake is None else brake.scale),
        breadth_min=(0 if breadth is None else breadth.min),
        breadth_cap=(None if breadth is None else breadth.cap),
    )
    for port in coordinator.ports.values():
        if isinstance(port, SessionBridge):
            port.session.entry_gate = gate
            # T289 — 폭을 셀 봉 수·축을 세션 다리에 심는다. 선언이 없으면 0 으로 **되돌린다**
            #   (매매법 전환으로 선언이 사라졌는데 다리가 계속 세면 꺼진 규칙의 흔적이 남는다).
            books = port.session.playbooks
            port.breadth_bars = 0 if breadth is None else breadth.bars
            port.breadth_frame = books[0].timeframe if breadth is not None and books else None


def _reattach_gate(fund: Fund) -> None:
    """펀드의 **지금 규칙**으로 문을 다시 끼운다 — 종목 추가·전략 전환 뒤 (멱등).

    Args:
        fund: 대상 펀드.

    Note:
        호출부가 인자를 하나씩 적던 것을 모았다 — T286 이 인자를 셋 더 늘리면서 두 곳 중 하나만
        고치면 **그 경로의 세션만 규칙 없이 도는** 조용한 실패가 된다(전략 전환 뒤 브레이크가
        사라지는 식).
    """
    _attach_gate(
        fund.coordinator,
        fund.slots,
        fund.halt_after_stops,
        fund.notional_cap,
        notional_fit=fund.notional_fit,
        brake=fund.drawdown_brake,
        breadth=fund.breadth_cap,
        leverage=fund.leverage,
        legs=fund.legs,
    )
    _log_gate(fund)


def _log_gate(fund: Fund) -> None:
    """펀드의 세션들에 **실제로 끼워진 것**을 로그 한 줄로 남긴다 (2026-09-21 · 규칙 #8).

    Args:
        fund: 대상 펀드.

    Note:
        🔴 진입 문·다리 노출·사이징 예산·폭 설정은 **도는 프로세스의 메모리에만** 있다. 파일에도
        DB 에도 없어, 문이 안 끼워진 채 돌아도 밖에서는 아무 흔적이 없었다(실계좌 점검에서 이 셋을
        확인할 길이 없었다). 선언이 아니라 **세션에서 읽은 값**을 적는다 — 선언과 다르면
        그것이 결함이다.
        생성·복원·종목 편집·매매법 전환 때마다 남는다. 시크릿은 없다(이름·수·배율·예산).
    """
    try:
        _log_gate_unguarded(fund)
    except Exception as exc:
        # 관측 실패가 펀드 복원(= 열린 포지션 관리)을 막으면 안 된다 (#8-1). 흔적은 남긴다.
        _logger.warning("fund_gate_log_failed: %s %s", fund.fund_id, str(exc)[:160])


def _log_gate_unguarded(fund: Fund) -> None:
    """`_log_gate` 의 본문 — 예외는 호출자가 받는다."""
    members: dict[str, dict[str, object]] = {}
    for symbol, port in fund.coordinator.ports.items():
        if not isinstance(port, SessionBridge):
            continue
        session = port.session
        gate = session.entry_gate
        members[symbol] = {
            "books": [item.playbook_id for item in session.playbooks],
            "leverage": str(session.ledger.leverage),
            "budget": (
                None
                if session.ledger.margin_budget is None
                else str(session.ledger.margin_budget.quantize(Decimal("0.01")))
            ),
            "gate": None if gate is None else type(gate).__name__,
            "leg_exposure": {name: str(size) for name, size in session.leg_leverage.items()},
            "breadth": (
                None
                if port.breadth_frame is None or port.breadth_bars < 1
                else f"{port.breadth_frame.value}x{port.breadth_bars}"
            ),
        }
    _events.info(
        "fund_gate_attached",
        payload={
            "fund_id": fund.fund_id,
            "playbook": fund.playbook,
            "slots": fund.slots,
            "legs": [
                {
                    "playbook": leg.playbook,
                    "symbols": len(leg.symbols),
                    "slots": leg.slots,
                    "exposure": str(leg.exposure),
                    "cap": None if leg.notional_cap is None else str(leg.notional_cap),
                    "brake": leg.drawdown_brake is not None,
                    "breadth": leg.breadth_cap is not None,
                }
                for leg in fund.legs
            ],
            "members": members,
            "ungated": sorted(name for name, body in members.items() if body["gate"] is None),
        },
    )


def _member_terms(
    playbook: str,
    leverage: Decimal,
    alt_leverage: Decimal | None,
    legs: Sequence[FundLeg],
    symbol: str,
) -> tuple[str, Decimal]:
    """그 종목의 세션에 실을 `(매매법 이름, 배율)` — 생성·복원·종목 추가·전환이 공유한다 (T291).

    Args:
        playbook: 펀드의 매매법 id.
        leverage: 펀드 배율.
        alt_leverage: 알트 배율 오버라이드.
        legs: 펀드의 다리들. 비어 있으면 지금까지의 규칙(펀드 매매법 · `_member_leverage`).
        symbol: 종목.

    Returns:
        다리가 있으면 그 종목을 가진 다리들의 `a+b` 와 그 최댓값 배율.

    Raises:
        LegError: 다리가 있는데 그 종목을 가진 다리가 없다.
    """
    if not legs:
        return playbook, _member_leverage(leverage, alt_leverage, symbol)
    return member_playbook(legs, symbol), member_leverage(legs, symbol)


def _member_leverage(fund_leverage: Decimal, alt_leverage: Decimal | None, symbol: str) -> Decimal:
    """종목별 세션 배율 — 알트만 오버라이드한다 (2.0.0 A5: 코어 6x · 알트 5x).

    Note:
        `alt_leverage` 가 None 이면 기존 경로 그대로다 (기본 꺼짐 · T201 배선 원칙).
    """
    if alt_leverage is not None and symbol not in CORE_SYMBOLS:
        return alt_leverage
    return fund_leverage


async def _rank_weights(fund: Fund) -> tuple[BasketMember, ...] | None:
    """60일 수익 랭크 비중을 계산한다 — 실패하면 None (기존 비중 유지).

    Note:
        측정과 같은 정의: 일봉 종가 기준 60일 수익 내림차순 랭크 → 점수 N..1 (T201 D2).
        데이터가 모자라거나 조회가 실패하면 **비중을 안 바꾼다** — 비중 갱신 실패가
        매매를 막지 않는다 (규칙 8-1 정신 · 기존 비중은 유효한 상태다).
    """
    market = Market(fund.market)
    end = datetime.now(UTC)
    start = end - timedelta(days=RANK_WINDOW_DAYS + 5)
    momenta: dict[str, Decimal] = {}
    try:
        async with MarketDataProvider() as provider:
            adapter = provider.adapter_for(market)
            for member in fund.coordinator.engine.basket.members:
                rows = await adapter.get_candles(
                    instrument_of(member.symbol, market), Timeframe.D1, start, end
                )
                if len(rows) <= RANK_WINDOW_DAYS:
                    _logger.warning(
                        "rank_weights_short: %s %s %d봉", fund.fund_id, member.symbol, len(rows)
                    )
                    return None
                momenta[member.symbol] = rows[-1].close / rows[-1 - RANK_WINDOW_DAYS].close - 1
    except Exception as exc:
        _logger.warning("rank_weights_failed: %s %s", fund.fund_id, exc)
        return None
    return rank_members(momenta)


async def _refresh_rank(fund: Fund) -> None:
    """rank60 펀드의 바스켓 비중을 갱신한다 (일 1회 · 00 UTC 틱 · 생성 직후)."""
    members = await _rank_weights(fund)
    if members is None:
        return
    engine = fund.coordinator.engine
    engine.basket = Basket(members, version=engine.basket.version)
    _logger.info(
        "rank_weights_applied: %s %s",
        fund.fund_id,
        {m.symbol: str(m.weight) for m in members},
    )


def _signal_hours() -> int:
    """기본 플레이북 신호 TF 의 시간 수 — 리밸런싱 주기의 원천 (T63 ②).

    Returns:
        신호 TF 시간 수 (지금 4).

    Raises:
        ValueError: TF 가 1시간 미만이거나 24 의 약수가 아닐 때 — `_next_boundary` 의
            경계 산술이 성립하지 않으므로 조용히 도는 대신 기동을 멈춘다 (규칙 #8).
    """
    book = next(b for b in load_playbooks() if b.playbook_id == default_playbook())
    hours = int(interval(book.timeframe).total_seconds() // 3600)
    if hours < 1 or 24 % hours != 0:
        raise ValueError(f"신호 TF {book.timeframe.value} 로는 리밸런싱 경계를 못 만든다")
    return hours


REBALANCE_INTERVAL_H = _signal_hours()
"""리밸런싱 주기(시간) = 기본 플레이북의 신호 TF (T63 ② — 4 는 우연이 아니라 파생).

봉 마감에 리밸런싱하는 것이 설계라, TF 를 옮기면 이 값도 같이 옮겨져야 한다 —
두 상수로 두면 한쪽만 바뀐다.
"""

_next_tick_at: datetime | None = None
"""다음 자동 리밸런싱 시각 (UTC) — 화면 카운트다운이 읽는다. 루프가 갱신한다."""


def _next_boundary(now: datetime) -> datetime:
    """다음 4h 경계 (UTC 00·04·08·12·16·20) — 봉 마감에 맞춰 리밸런싱한다."""
    step = REBALANCE_INTERVAL_H
    hour = (now.hour // step + 1) * step
    base = now.replace(minute=0, second=0, microsecond=0)
    if hour >= 24:
        return (base + timedelta(days=1)).replace(hour=0)
    return base.replace(hour=hour)


async def rebalance_loop() -> None:
    """4h 경계마다 모든 펀드를 리밸런싱한다 — "앉아서 지켜보기"의 심장 (T61).

    Note:
        🔴 틱은 **예산만 갱신**한다(주문 아님) — 세션이 자기 신호로 매매한다(설계 B). 그래서
        이 루프는 저위험이다. 한 펀드가 실패해도 나머지는 돈다. 펀드가 0 개여도 카운트다운을
        위해 계속 돌며 다음 경계만 갱신한다.
    """
    global _next_tick_at
    while True:
        now = datetime.now(UTC)
        _next_tick_at = _next_boundary(now)
        await asyncio.sleep(max((_next_tick_at - now).total_seconds(), 1.0))
        for fund in list(FUNDS.values()):
            try:
                # rank60 — 하루 한 번(00 UTC 경계) 비중을 랭크로 갱신 (측정과 같은 일 리밸)
                if fund.weight_mode == "rank60" and _next_tick_at.hour == 0:
                    await _refresh_rank(fund)
                await _tick(fund)
            except Exception as exc:
                _logger.warning("fund_auto_tick_failed: %s %s", fund.fund_id, exc)
        if FUNDS:
            _logger.info("funds_rebalanced: %d", len(FUNDS))


FUNDS_ROOT = Path(os.environ.get("FUNDS_ROOT", "logs/funds"))
"""펀드 정의·성과를 디스크에 남기는 곳 (영속화, T61).

🔴 **메모리에만 두면 재시작에 증발한다** — 배포·재부팅·크래시는 몇 달 돌리면 반드시 온다.
그때 펀드도, 열린 포지션 관리도 다 잃는다. 그래서 상태 변화마다 여기 저장하고 시작 때 복구한다.

⚠️ `logs/` 밑에 둔다 — 세션 저널과 같은 **마운트된 볼륨**(runlogs)이라 컨테이너 재시작에도 남는다.
컨테이너 fs 에 두면 재시작에 같이 날아가 저장의 의미가 없다.
"""


def _fund_data(fund: Fund) -> dict[str, Any]:
    """펀드를 저장 가능한 딕셔너리로 — 바스켓·레버·전략·TWR 상태."""
    basket = fund.coordinator.engine.basket
    return {
        "fund_id": fund.fund_id,
        "label": fund.label,
        "playbook": fund.playbook,
        "leverage": str(fund.leverage),
        "market": fund.market,
        "weight_mode": fund.weight_mode,
        "alt_leverage": None if fund.alt_leverage is None else str(fund.alt_leverage),
        "slots": fund.slots,
        "halt_after_stops": fund.halt_after_stops,
        "notional_cap": None if fund.notional_cap is None else str(fund.notional_cap),
        # ⭐ T286 — 줄여서 진입 · 낙폭 브레이크. 돌던 펀드의 규칙은 선언이 바뀌어도 안 바뀐다.
        #    낙폭 **고점**은 여기 안 적는다 — `twr` 의 `twr_peak` 이 이미 그 값이고 입출금 중립이다.
        "notional_fit": fund.notional_fit,
        "drawdown_brake": (
            None
            if fund.drawdown_brake is None
            else {"at": str(fund.drawdown_brake.at), "scale": str(fund.drawdown_brake.scale)}
        ),
        "breadth_cap": (
            None
            if fund.breadth_cap is None
            else {
                "min": fund.breadth_cap.min,
                "cap": str(fund.breadth_cap.cap),
                "bars": fund.breadth_cap.bars,
            }
        ),
        # ⭐ T291 — 다리. 옛 저장본에는 없다(빈 목록 = 지금까지의 펀드). 저장본이 선언을 이긴다.
        "legs": [leg.to_dict() for leg in fund.legs],
        "basket": {
            "version": basket.version,
            "members": [{"symbol": m.symbol, "weight": str(m.weight)} for m in basket.members],
        },
        "twr": fund.coordinator.engine.ledger.to_dict(),
        # 판 → 펀드 매핑 (2026-09-04). 지금까지 메모리(`handles`)에만 있어 리포트(스케줄러
        # 프로세스)가 판을 펀드로 못 묶었다 — 파일에 남겨 어느 프로세스든 읽게 한다.
        "runs": dict(fund.handles),
        # ⭐ T285 — 멤버가 받은 몫(원장 걷기 시작점)과 마지막 정산의 누적 실현.
        #    재기동이 이 둘을 되살려야 과거 손익이 다시 세어지지 않는다
        #    (예전엔 seed 를 현재 몫으로 덮어 재기동마다 샜다).
        "seeds": {
            sym: str(SESSIONS[handle].session.ledger.seed_cash)
            for sym, handle in fund.handles.items()
            if handle in SESSIONS
        },
        "marks": {sym: str(mark) for sym, mark in fund.coordinator.marks.items()},
        "anchor": None if fund.anchor is None else fund.anchor.to_dict(),
    }


def _save_fund(fund: Fund) -> None:
    """펀드 상태를 디스크에 쓴다 — 생성·편집·입출금·틱 뒤에 부른다.

    Note:
        8-1: 저장 실패가 리스크 감소를 막지 않는다 — 여기 실패는 신규(저장)라 보류가 맞고,
        조용히 넘기지 않고 경고를 남긴다 (규칙 #8).
    """
    try:
        FUNDS_ROOT.mkdir(parents=True, exist_ok=True)
        path = FUNDS_ROOT / f"{fund.fund_id}.json"
        path.write_text(
            json.dumps(_fund_data(fund), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        _logger.warning("fund_save_failed: %s %s", fund.fund_id, exc)


def _archive_fund_file(fund_id: str, *, root: Path | None = None) -> Path | None:
    """접은 펀드의 정의를 **보관**한다 — 지우지 않는다 (T266 · 2026-09-10).

    Args:
        fund_id: 펀드 id.
        root: 펀드 저장 루트. None 이면 `FUNDS_ROOT`.

    Returns:
        보관 파일 경로. 원본이 없었으면 None.

    Note:
        판·체결(`wf_runs`·`wf_trades`)은 펀드 id 로 남는데 정의 파일(이름·바스켓·비중·전략)을
        지우면 리포트·매매일지가 "펀드 ?" 가 된다. `archive/` 로 옮기고 `dropped_at` 을 적는다 —
        기동 복원은 루트의 `*.json` 만 읽으므로 목록에서만 사라진다.
    """
    base = root or FUNDS_ROOT
    source = base / f"{fund_id}.json"
    if not source.exists():
        return None
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"fund_id": fund_id}
    if isinstance(data, dict):
        payload = cast("dict[str, Any]", data)
        payload["dropped_at"] = datetime.now(UTC).isoformat()
    else:
        payload = {"fund_id": fund_id, "dropped_at": datetime.now(UTC).isoformat()}
    archive = base / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"{fund_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    source.unlink(missing_ok=True)
    return target


RETRY_EVERY = 120.0
"""못 붙인 펀드를 다시 시도하는 간격(초).

⚠️ 짧게 두면 요율 제한으로 실패한 것을 빠르게 두드려 **밴을 연장시킨다.** 실측 밴이
2분이었으므로 그보다 짧게 두지 않는다.
"""

PENDING_FUNDS: set[str] = set()
"""복구에 **실패해서 아직 못 붙인** 펀드 파일 이름들 — `fund_retry_loop` 가 다시 시도한다.

🔴 **한 번 놓치면 다음 재기동까지 반쪽으로 돈다** (2026-08-29 실측). 기동 순간 거래소가
`418 -1003 (IP banned; way too many requests)` 을 주자 BINANCE 펀드 복구가 통째로
실패했고, `warning` 한 줄만 남긴 채 **그 판 6개가 예산 없이 계속 돌았다.** 원장은
저마다 시드(계좌 전액 4,956)를 자기 것으로 여겼고 — 합이 20,325 대 계정 4,956 —
감사가 `wallet_drift` 를 외쳤다. 사람 눈에는 *"BN 판이 전부 이상 1건"* 으로 보였다.

⚠️ **일시적 실패를 영구 상태로 만든 것이 결함이다.** 요율 제한·네트워크 끊김은 정상
범주이고, 그때 한 번 놓친 것을 **스스로 다시 붙이지 못하는 것**이 문제다.

⛔ 조용히 포기하지 않는다 (절대 규칙 #8) — 남아 있는 동안은 계속 시끄럽다.
"""


async def restore_funds() -> int:
    """저장된 펀드들을 복구한다 — api 시작 때 부른다.

    Returns:
        복구한 펀드 수.

    Note:
        🔴 각 펀드는 세션을 다시 띄운다 — 러너가 거래소의 기존 포지션을 원장으로 되읽어
        (`adopt`) 손절 관리를 이어간다. 한 펀드가 실패해도 나머지는 복구한다 (앱을 안 죽인다).

        ⭐ **실패한 것은 버리지 않고 `PENDING_FUNDS` 에 남긴다** — `fund_retry_loop` 가
        다시 붙인다. 예전에는 여기서 끝이라, 기동 순간의 요율 제한 한 번이 그 펀드를
        **다음 재기동까지** 없는 것으로 만들었다.
    """
    if not FUNDS_ROOT.exists():
        return 0
    restored = 0
    for path in sorted(FUNDS_ROOT.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            await _restore_one(data)
            restored += 1
            PENDING_FUNDS.discard(path.name)
            # ⭐ 복원 직후 한 번 틱 — 거래소 계좌에 총자본을 맞추고(자동 앵커 · T285) 예산을 준다.
            #    실패해도 복원은 된 것이다 — 다음 4h 경계가 다시 시도한다.
            try:
                await _tick(FUNDS[str(data["fund_id"])])
            except Exception as exc:
                _logger.warning("fund_restore_tick_failed: %s %s", data.get("fund_id"), exc)
        except Exception as exc:
            PENDING_FUNDS.add(path.name)
            _logger.warning("fund_restore_failed: %s %s", path.name, exc)
    if restored:
        _logger.info("funds_restored: %d", restored)
    if PENDING_FUNDS:
        # 🔴 error 다 — 이 상태로 돌면 그 펀드의 판들이 **예산 없이** 매매한다.
        #    원장이 저마다 계좌 전액을 자기 것으로 세므로 주문이 서로를 밀어낸다.
        # ⚠️ 이 로거는 표준 `logging` 이다 (structlog 아님) — printf 서식을 쓴다.
        _logger.error(
            "funds_pending: %s — 예산이 안 붙은 판이 있다 (원장이 계좌 전액을 자기 것으로 센다)",
            ", ".join(sorted(PENDING_FUNDS)),
        )
    return restored


async def fund_retry_loop(*, every: float = RETRY_EVERY) -> None:
    """못 붙인 펀드를 **다시 붙인다** — 붙을 때까지.

    Args:
        every: 시도 간격(초).

    Note:
        🔴 기동 순간은 거래소 호출이 가장 몰리는 때다 (판마다 캔들 워밍업 + 계좌 조회 +
        대조 1회). 그때 요율 제한에 걸린 것을 **조금 뒤에 다시 하면 대개 붙는다** —
        실측에서 밴은 2분 만에 풀렸다.

        ⚠️ 간격을 짧게 두지 않는다. 요율 제한 때문에 실패한 것을 빠르게 재시도하면
        밴을 연장시킨다 — 고치려는 것이 원인을 더한다.

        ⛔ **횟수 제한을 두지 않는다.** 몇 번 해보고 포기하면 결국 예전과 같은 반쪽
        상태이고, 그 상태는 조용하다. 안 붙는 동안은 `funds_pending` 이 계속 남는다.
    """
    while True:
        await asyncio.sleep(every)
        if not PENDING_FUNDS:
            continue
        _logger.info("funds_retrying: %s", ", ".join(sorted(PENDING_FUNDS)))
        await restore_funds()


def _running_handle(symbol: str, market: str = "GATE") -> str | None:
    """지금 이 (거래소, 종목)으로 도는 세션의 handle — autostart 가 되살린 것을 찾는다."""
    for handle, live in SESSIONS.items():
        if (
            live.session.instrument.symbol == symbol
            and live.session.instrument.market.value == market
            and live.running
        ):
            return handle
    return None


async def _restore_one(data: dict[str, Any]) -> None:
    """저장 딕셔너리 하나에서 펀드를 되살린다 — 세션 재기동 + TWR 이어받기.

    Note:
        🔴 `autostart_live` 가 이미 열린 RUN(펀드 멤버 포함)을 store 에서 되살렸으므로,
        같은 종목을 또 띄우면 409(종목당 포지션 하나)다. **도는 세션이 있으면 재사용**하고
        (펀드 격리 재적용), 없을 때만 새로 띄운다.
    """
    members = as_members(
        [(str(m["symbol"]), Decimal(str(m["weight"]))) for m in data["basket"]["members"]]
    )
    basket = Basket(members, version=str(data["basket"].get("version", "v1")))
    ledger = TwrLedger.from_dict(data["twr"])
    leverage = Decimal(str(data["leverage"]))
    playbook = str(data["playbook"])
    fund_market = str(data.get("market", "GATE"))  # 옛 저장본은 GATE 다
    weight_mode = str(data.get("weight_mode", "static"))  # 옛 저장본은 static — 불변
    raw_alt = data.get("alt_leverage")
    alt_leverage = None if raw_alt in (None, "") else Decimal(str(raw_alt))
    slots = int(data.get("slots", 0) or 0)  # 옛 저장본은 0 — 비중 배분 그대로
    halt_after_stops = int(data.get("halt_after_stops", 0) or 0)
    raw_cap = data.get("notional_cap")
    notional_cap = None if raw_cap in (None, "") else Decimal(str(raw_cap))
    # ⭐ T286 — 옛 저장본에는 없다(둘 다 꺼짐). 있으면 그대로 되살린다: 펀드를 만든 뒤 선언이
    #    바뀌어도 **돌던 펀드의 규칙은 안 바뀐다**(저장본이 이긴다 — slots·cap 과 같은 원칙).
    notional_fit = bool(data.get("notional_fit", False))
    raw_brake = data.get("drawdown_brake")
    drawdown_brake: DrawdownBrake | None = None
    if isinstance(raw_brake, Mapping):
        brake_body = cast("Mapping[str, Any]", raw_brake)
        drawdown_brake = DrawdownBrake(
            at=Decimal(str(brake_body["at"])), scale=Decimal(str(brake_body["scale"]))
        )
    # ⭐ T289 — 옛 저장본에는 없다(꺼짐). 있으면 그대로 되살린다(저장본이 이긴다 · T286 과 같다).
    raw_breadth = data.get("breadth_cap")
    breadth_cap: BreadthCap | None = None
    if isinstance(raw_breadth, Mapping):
        breadth_body = cast("Mapping[str, Any]", raw_breadth)
        breadth_cap = BreadthCap(
            min=int(breadth_body["min"]),
            cap=Decimal(str(breadth_body["cap"])),
            bars=int(breadth_body.get("bars", 3)),
        )
    # ⭐ T291 — 다리. 옛 저장본에는 없다(빈 튜플 = 문 하나 · 매매법 하나 · 지금까지의 경로 그대로).
    legs = tuple(
        FundLeg.from_dict(cast("Mapping[str, Any]", item))
        for item in cast("list[object]", data.get("legs") or [])
        if isinstance(item, Mapping)
    )
    total = ledger.balance
    wsum = basket.weight_sum
    # ⭐ T285 — 저장된 몫(seed)·정산 mark. 옛 저장본(없음)은 지금 몫을 seed 로, mark 는 첫 틱이 지금
    #    값으로 잡는다 — 과거 손익을 다시 세지 않는다(재기동마다 누적 손익률이 다시 곱히던 결함).
    seeds = cast("dict[str, Any]", data.get("seeds") or {})
    saved_marks = cast("dict[str, Any]", data.get("marks") or {})
    handles: dict[str, str] = {}
    ports: dict[str, SessionBridge] = {}
    kept: list[BasketMember] = []
    for member in basket.members:
        share = total / Decimal(slots) if slots > 0 else total * member.weight / wsum
        capital = total * member.weight / wsum
        seed = Decimal(str(seeds[member.symbol])) if member.symbol in seeds else capital
        handle = _running_handle(member.symbol, fund_market)
        if handle is not None:  # autostart 가 되살린 세션 재사용 — 펀드 격리 재적용
            session = SESSIONS[handle].session
            session.ledger.wallet_start = Decimal(0)
            session.ledger.refill = False  # T235 — 몫 안에서 굴린다 (채울 지갑이 없다)
            # 🔴 seed 는 **저장된 몫**으로 (T285). 현재 몫으로 덮으면 원장 걷기가 새 시작점에서
            #    과거 매매를 다시 세어 재기동마다 총자본이 샌다.
            session.ledger.seed_cash = seed
            # 🔴 예산도 몫으로 되돌린다 (2026-08-25). 안 되돌리면 판이 저장해 둔 **낡은
            #    예산**이 살아나고, WALLET 모형의 equity 가 그 값으로 수렴해 화면
            #    평가금액이 비중과 어긋난다 (실측: w2 와 w1 이 똑같이 150).
            session.ledger.margin_budget = share
            port = SessionBridge(session)
        else:  # 안 도는 종목만 새로 띄운다
            try:
                member_book, member_lev = _member_terms(
                    playbook, leverage, alt_leverage, legs, member.symbol
                )
                handle, port = await _spawn_session(
                    member.symbol,
                    share,
                    member_lev,
                    member_book,
                    market=fund_market,
                    capital=seed,
                    booked=min(share, capital) if legs else None,
                )
            except Exception as exc:
                # ⭐ 한 종목이 못 떠도 펀드는 산다 (2026-09-08 실측: 데모 서버의 펀드가 테스트넷에
                #    없는 계약 하나 때문에 통째로 복구 실패 → 30번 재시도 · 화면에 펀드 없음).
                #    빠진 종목은 경고로 남기고 나머지로 돈다 — 그 몫은 다음 리밸런싱이 다시 나눈다.
                _logger.warning(
                    "fund_member_skipped: %s %s — %s",
                    data.get("fund_id"),
                    member.symbol,
                    str(exc)[:160],
                )
                continue
        handles[member.symbol] = handle
        ports[member.symbol] = port
        kept.append(member)
    if not kept:
        raise RuntimeError("펀드 구성원이 하나도 뜨지 않았다")
    if len(kept) != len(basket.members):
        basket = Basket(tuple(kept), version=basket.version)
    engine = RebalanceEngine(basket=basket, ledger=ledger, slots=slots)
    marks = {
        sym: Decimal(str(saved_marks[sym])) for sym in ports if sym in saved_marks
    }  # 되살린 세션만 · 새로 띄운 세션은 첫 틱이 mark 를 잡는다
    coordinator = Coordinator(engine=engine, ports=dict(ports), marks=marks)  # type: ignore[arg-type]
    _attach_gate(
        coordinator,
        slots,
        halt_after_stops,
        notional_cap,
        notional_fit=notional_fit,
        brake=drawdown_brake,
        breadth=breadth_cap,
        leverage=leverage,
        legs=legs,
    )
    fund = Fund(
        legs=legs,
        fund_id=str(data["fund_id"]),
        label=str(data["label"]),
        coordinator=coordinator,
        handles=handles,
        leverage=leverage,
        playbook=playbook,
        market=fund_market,
        weight_mode=weight_mode,
        alt_leverage=alt_leverage,
        slots=slots,
        halt_after_stops=halt_after_stops,
        notional_cap=notional_cap,
        notional_fit=notional_fit,
        drawdown_brake=drawdown_brake,
        breadth_cap=breadth_cap,
    )
    raw_anchor = data.get("anchor")
    if isinstance(raw_anchor, dict):
        fund.anchor = AnchorState.from_dict(cast("dict[str, Any]", raw_anchor))
    FUNDS[fund.fund_id] = fund
    _log_gate(fund)


async def _spawn_session(
    symbol: str,
    share: Decimal,
    leverage: Decimal,
    playbook: str,
    *,
    market: str = "GATE",
    adopt_from: str | None = None,
    capital: Decimal | None = None,
    booked: Decimal | None = None,
) -> tuple[str, SessionBridge]:
    """종목 하나에 펀드 멤버 세션을 띄운다 — 생성·바스켓 추가·전략 전환이 공유한다.

    Args:
        symbol: 종목 (예: BTC_USDT).
        share: 이 세션의 **예산**(사이징 기준 · 자리 배분이면 총자본 ÷ 자리).
        leverage: 레버리지 (롱 기준).
        playbook: 세션 전략 id.
        market: 거래소 (GATE/BINANCE) — 펀드가 고른다 (T62 P3b).
        adopt_from: **무중단 전략 전환**용 (T61). 앞 세션의 handle 을 주면, 새 세션이 그
            표식(run_key)을 물려받아 거래소에 **열린 채 남은 포지션을 이어받는다**(`adopt`).
            그때는 증거금이 이미 포지션에 들어가 있으므로 `reviving=True` 로 잔액 검사를
            건너뛴다 — 안 그러면 "쓸 돈 없음" 으로 시작이 거부된다.
        capital: 이 세션이 **받은 몫**(원장 걷기 시작점 `seed_cash` · T285). 없으면 예산과 같다.
            자리 배분에서는 예산(총자본 ÷ 자리)과 몫(총자본 ÷ 종목 수)이 다르다.
        booked: 판 저장소에 **계좌 예산으로 적을 값** (T291). None 이면 `share`(지금까지의 동작).
            다리 펀드는 종목(18)이 자리(6)보다 많아 `share` 를 적으면 예산 합이 총자본의 3배가 되고,
            계좌 예산 검사(`_budget_room`)가 생성을 막고 러너의 `check_funding` 이 전 종목의 진입을
            막는다. 펀드가 계좌에서 실제로 가진 돈은 몫의 합(= 총자본)이므로 그것을 적는다 —
            사이징 예산은 아래에서 `share` 로 되돌린다.

    Note:
        🔴 예산 파라미터는 `margin` 이다 (없으면 계좌 전액). 그리고 펀드 멤버는 **자기 몫**만
        자본이라 `wallet_start=0`·`seed_cash=몫` 으로 격리한다 — 안 그러면 equity 가 계좌
        전액으로 잡혀 펀드 총자본이 종목수배 부풀고 배분이 통째로 틀린다.
    """
    payload: dict[str, Any] = {
        "playbook": playbook,
        "symbol": symbol,
        "market": market,  # T62 P3b — 펀드가 거래소를 고른다 (기본 GATE)
        "margin": str(share if booked is None else booked),
        "leverage": str(leverage),
    }
    if adopt_from:
        payload["run_key"] = adopt_from
    result = await _live_start(payload, reviving=bool(adopt_from))
    handle = str(result["session_id"])
    session = SESSIONS[handle].session
    session.ledger.wallet_start = Decimal(0)
    session.ledger.refill = False  # T235 — 몫 안에서 굴린다 (채울 지갑이 없다)
    session.ledger.seed_cash = share if capital is None else capital
    session.ledger.margin_budget = share  # 예산 — 다음 틱 전까지의 사이징 기준
    return handle, SessionBridge(session)


def _orders_go_live() -> bool:
    """지금 이 프로세스의 주문이 실계좌로 가나 — `execution.gateway.order_adapter` 와 같은 조건."""
    try:
        from updown.common.config import AppEnv, load_settings

        s = load_settings()
        return s.app_env is AppEnv.LIVE and bool(s.live_orders)
    except Exception:  # 설정을 못 읽으면 테스트넷으로 본다 — 종목을 더 빼는 쪽이 안전하다
        return False


def member_share(total_cash: Decimal, weight: Decimal, weight_sum: Decimal) -> Decimal:
    """종목 하나의 시작 예산 — **센트 아래는 버린다**.

    `total x w / Σw` 를 28자리 Decimal 로 나누면 1/7 같은 무한소수가 **올림**될 수 있어 다섯 몫의
    합이 `300.000…0857` 이 되고, 계좌 총액 검사(`_budget_room`)가 "300 을 넘는다" 로 펀드 생성을
    거절했다 (2026-09-05 실계좌 첫 펀드). 버리면 합은 항상 총액 이하이고 남는 것은 몇 센트다.

    Args:
        total_cash: 펀드 총 현금.
        weight: 이 종목의 비중.
        weight_sum: 비중 합.

    Returns:
        센트 단위로 내림한 예산.
    """
    return (total_cash * weight / weight_sum).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


async def _create_fund(
    *,
    basket: Basket,
    total_cash: Decimal,
    leverage: Decimal,
    playbook: str,
    label: str,
    market: str = "GATE",
    weight_mode: str = "static",
    alt_leverage: Decimal | None = None,
    slots: int = 0,
    halt_after_stops: int = 0,
    notional_cap: Decimal | None = None,
    notional_fit: bool = False,
    drawdown_brake: DrawdownBrake | None = None,
    breadth_cap: BreadthCap | None = None,
    legs: Sequence[FundLeg] = (),
) -> Fund:
    """바스켓 종목마다 세션을 띄우고 조정자로 묶는다.

    Args:
        basket: 구성 종목·비중.
        total_cash: 펀드 전체 시작 자본 (종목별로 비중대로 나뉜다).
        leverage: 세션 레버리지.
        playbook: 세션 전략 (기본 = `recommended: true` 플레이북).
        label: 화면 이름.
        market: 거래소 (GATE/BINANCE) — 모든 세션이 이 거래소로 나간다 (T62 P3b).
        weight_mode: 비중 방식 — "static"(입력 비중 고정) | "rank60"(일봉 수익 랭크) |
            "slots"(P3 자리 배분 · `slots` 가 양수여야 한다).
        alt_leverage: 알트 배율 오버라이드 (2.0.0 A5 = 5). None 이면 전 종목 `leverage`.
        slots: P3 동시 보유 상한(예산 = 총자본 ÷ slots · 진입 문). 0 = 없음.
        halt_after_stops: P3 같은 날 연속 손절 정지 문턱. 0 = 없음.
        notional_cap: 총 명목 상한(자본 배수 · V2 = 2). None = 없음.
        notional_fit: 상한에 걸릴 때 남은 여유만큼 줄여서 진입할지 (T286 · A 구성).
        drawdown_brake: 낙폭 브레이크 선언. None = 없음.
        breadth_cap: 조건부 총 명목 상한 선언 (T289). None = 없음.
        legs: 다리들 (T291 · 이종 합성 매매법). 있으면 종목마다 그 종목의 다리만 실은 세션을 띄우고
            문을 다리별로 세운다. 비어 있으면 지금까지의 경로 그대로다.

    Returns:
        만들어진 펀드. `FUNDS` 에 등록된다.

    Note:
        🔴 각 세션은 **`_live_start` 로 띄운 검증된 라이브 러너**다 — 여기서 새로 만들지 않는다.
        초기 예산 = `total x 비중/비중합`(자리 배분이면 `total ÷ slots`). 조정자는 이후 매 틱
        이 예산을 다시 나눈다.
    """
    wsum = basket.weight_sum
    handles: dict[str, str] = {}
    ports: dict[str, SessionBridge] = {}
    try:
        for member in basket.members:
            # 몫(capital · 원장 걷기 시작점)은 비중대로, 예산(share · 사이징)은
            # 자리 배분이면 총자본 ÷ 자리. 자리 배분에서 예산 합은 총자본을 넘는 것이
            # 의도다(동시 보유만 자리 수로 막는다) — 총자본은
            # 펀드가 들고 세션은 증분만 보고하므로 예산 합이 커도 장부가 부풀지 않는다 (T285).
            capital = member_share(total_cash, member.weight, wsum)
            share = total_cash / Decimal(slots) if slots > 0 else capital
            member_book, member_lev = _member_terms(
                playbook, leverage, alt_leverage, legs, member.symbol
            )
            handle, port = await _spawn_session(
                member.symbol,
                share,
                member_lev,
                member_book,
                market=market,
                capital=capital,
                booked=min(share, capital) if legs else None,
            )
            handles[member.symbol] = handle
            ports[member.symbol] = port
    except Exception:
        # ⛔ 부분 생성은 정리한다 — 한 종목이 실패하면 앞서 뜬 세션이 증거금을 잡은 채
        #    고아로 남는다. 만든 것을 되돌리고 예외를 그대로 올린다 (규칙 #8).
        for handle in handles.values():
            await _close_live_position(handle)
            await _drop_one(handle)
        raise

    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=total_cash), slots=slots)
    coordinator = Coordinator(engine=engine, ports=dict(ports))  # type: ignore[arg-type]
    _attach_gate(
        coordinator,
        slots,
        halt_after_stops,
        notional_cap,
        notional_fit=notional_fit,
        brake=drawdown_brake,
        breadth=breadth_cap,
        leverage=leverage,
        legs=legs,
    )
    fund = Fund(
        legs=tuple(legs),
        fund_id=f"fund{uuid4().hex[:8]}",
        label=label,
        coordinator=coordinator,
        handles=handles,
        leverage=leverage,
        playbook=playbook,
        market=market,
        weight_mode=weight_mode,
        alt_leverage=alt_leverage,
        slots=slots,
        halt_after_stops=halt_after_stops,
        notional_cap=notional_cap,
        notional_fit=notional_fit,
        drawdown_brake=drawdown_brake,
        breadth_cap=breadth_cap,
    )
    FUNDS[fund.fund_id] = fund
    _log_gate(fund)
    if weight_mode == "rank60":
        # 첫 비중부터 랭크로 — 실패하면 입력 비중으로 시작하고 다음 00 UTC 에 다시 시도한다.
        await _refresh_rank(fund)
    # 첫 틱 — 앵커 모드를 정하고(계좌가 곧 펀드인가 · 펀드 밖 유휴 현금이 있나) 예산을 준다 (T285).
    await _tick(fund)
    return fund


_UNREAL_TTL = 20.0
"""handle -> (미실현손익, 포지션 증거금) · 20초. 잦은 폴링이 거래소를 안 두드리게 짧게 캐시."""

_UNREAL_CACHE = TtlCache[tuple[str, str]]("fund.unreal", _UNREAL_TTL)
"""T217 (2026-09-04): 8s 였다. 화면이 4s 폴링이라 핸들당 7.5회/분 x positionRisk(weight 5) x
6핸들 = 분당 225 였다. 20s 면 90. 미실현손익은 표시값이라 20초 지연은 문제가 아니다."""


async def _exchange_facts(handle: str) -> tuple[str, str]:
    """세션의 (미실현 손익, 포지션 증거금) — 거래소 포지션에서 읽는다 (짧은 캐시).

    Returns:
        `(unrealised_pnl, margin)` 문자열. 포지션이 없으면 `("0", "0")`.

    Note:
        🔴 데이터는 판 단위에 이미 있다 (`position_snapshot` 의 `unrealised_pnl` · `margin`). 여기서
        그것을 펀드로 끌어올릴 뿐이다. 화면이 폴링하므로 TTL 로 거래소 호출을 아낀다.

        ⭐ 증거금을 따로 올리는 이유 (사용자 지적 2026-09-07): 종목 행의 "평가금액" 은 **배정 예산 +
        손익**이라 입금하면 주문 없이도 뛴다. 거래소가 실제로 잡고 있는 돈은 이 값이다 — 둘을 한
        열에 섞으면 입금이 곧 매수로 읽힌다.
    """
    kept = _UNREAL_CACHE.fresh(handle)
    if kept is not None:
        return kept[1]
    stale = _UNREAL_CACHE.peek(handle)
    runner = LIVE_RUNNERS.get(handle)
    unreal, margin = stale[1] if stale is not None else ("0", "0")
    if runner is not None:
        try:
            orders: Any = runner._orders  # position_snapshot 은 구체 어댑터에만 있다
            snap: dict[str, str] = await orders.position_snapshot(runner.instrument)
            unreal = str(snap.get("unrealised_pnl", "0") or "0")
            margin = str(snap.get("margin", "0") or "0")
        except Exception:  # 조회 실패는 표시값일 뿐 — 마지막 값 유지
            pass
    return _UNREAL_CACHE.put(handle, (unreal, margin))


def _member_share_now(fund: Fund, sym: str) -> Decimal:
    """종목 하나에 펀드가 지금 든 몫 — 배정 예산 + 마지막 정산 이후 실현 손익 (표시용 · T285).

    Args:
        fund: 펀드.
        sym: 종목.

    Returns:
        예산(없으면 시드) + 정산 뒤 증분. 아직 정산이 없으면 예산 그대로.
    """
    live = SESSIONS.get(fund.handles.get(sym, ""))
    port = fund.coordinator.ports.get(sym)
    mark = fund.coordinator.marks.get(sym)
    if live is None:
        return Decimal(0)
    budget = live.session.ledger.margin_budget
    base = budget if budget is not None else live.session.ledger.seed_cash
    if port is None or mark is None:
        return base
    return base + port.realized() - mark


async def _per_symbol(fund: Fund, sym: str) -> dict[str, Any]:
    """한 종목의 상세 — 평가금액·비중·실현손익·미실현손익·현재 포지션."""
    handle = fund.handles.get(sym, "")
    live = SESSIONS.get(handle)
    weight = next(
        (str(m.weight) for m in fund.coordinator.engine.basket.members if m.symbol == sym), ""
    )
    if live is None:
        return {"handle": handle, "weight": weight, "missing": True}
    session = live.session
    held = session.position
    unreal, margin = await _exchange_facts(handle)
    row: dict[str, Any] = {
        "handle": handle,
        # ⚠️ equity = 배정 예산 + 마지막 정산 이후 실현 (펀드가 이 종목에 든 몫 · T285).
        #    거래소가 잡은
        #    돈은 margin 이다. 원장 equity(시드 + 누적)를 그대로 쓰면 예산 재배분이 안 보인다.
        "equity": str(_member_share_now(fund, sym)),
        "margin": margin,
        "weight": weight,
        # 🔴 재정렬(resync) 앵커 이후 실현만 — 복구 불가한 과거는 뺀다 (2026-09-01).
        "realized": str(session.ledger.realized_cash - session.realized_anchor),
        "unrealized": unreal,
        "holding": held is not None,
        # 🔴 **원장과 거래소가 갈리면 이 종목 손익은 미확정이다** (2026-09-01 사용자
        #    신고). 두 신호를 나눠 싣는다: `reconciled` = 포지션 갈림(고아·유령·무방비),
        #    `accounting_ok` = 실현손익 회계가 거래소와 부호까지 맞나(`pnl_sign_split`).
        #    둘 중 하나라도 거짓이면 이 세션 손익은 미확정이고, 펀드 총자본·TWR 에서
        #    **동결 격리**된다 (`SessionBridge` · 벽돌 2). 화면도 회색 표식으로 그린다.
        "reconciled": session.reconciled,
        "accounting_ok": session.accounting_ok,
        # 🔴 갈렸을 때, 거래소 실측으로 귀속된 **진짜 실현손익**. 화면이 원장 허구(+21)
        #    대신 이 값(-4.22)을 그린다 — 원장 청산가 fiction 을 안 보여준다 (2026-09-01).
        "verified_realized": (
            None if session.verified_realized is None else str(session.verified_realized)
        ),
    }
    if held is not None:
        row["position"] = {
            "side": "롱" if held.direction.sign > 0 else "숏",
            "entry": str(held.entry),
            "target": str(held.planned_target),
            "stop": str(held.planned_stop),
            # 🔴 **상자를 그리려면 언제 들어갔는지가 있어야 한다** (사용자 요구 2026-09-21:
            #    *"여기에도 진입가, 손절가, 박스 그려줘"*). 가격 셋만으로는 가로선밖에 못 긋는다.
            "opened_at": None if held.opened_at is None else held.opened_at.isoformat(),
            # 🔴 **목표가 익절선이 아닐 수 있다** (사용자 지적 2026-09-21: *"우리 익절선이 따로
            #    없는 거 아냐? 이게 계산되고 있어?"* — 맞다).
            #
            #    추세추종(`full_ride`)은 고정 익절이 없고 `planned_target` 은 진입+100R 짜리
            #    **자리표시자**다(RIDE_R). 그 값을 '목표' 로 적으면 화면이 "218만 달러 대기" 처럼
            #    거짓말한다 — 2026-08-24 에 RUN 차트에서 같은 지적을 받아 고쳤는데, 이 카드에는
            #    그 판단이 안 실려 있었다. 플래그를 같이 실어 화면이 가린다.
            "full_ride": session.playbook.full_ride,
        }
    return row


async def _status(fund: Fund) -> dict[str, Any]:
    """펀드 현황 — 잔고·TWR·종목별 상세 (표시용)."""
    coord = fund.coordinator
    per_symbol = {sym: await _per_symbol(fund, sym) for sym in coord.ports}
    # 🔴 **거래소와 갈린 종목** — 포지션 갈림(reconciled) 또는 회계 갈림(accounting_ok)
    #    어느 쪽이든. 이 목록이 비어 있지 않으면 그 세션들은 총자본·TWR 에서 **동결
    #    격리**돼 있고(허구 손익 제외 · 벽돌 2), 화면은 헤드라인에 경고를 그린다.
    mismatch = [
        sym
        for sym, row in per_symbol.items()
        if row.get("reconciled") is False or row.get("accounting_ok") is False
    ]
    return {
        "fund_id": fund.fund_id,
        "label": fund.label,
        "playbook": fund.playbook,
        "market": fund.market,
        "basket": coord.engine.basket.version,
        "symbols": list(coord.engine.basket.symbols),
        "leverage": str(fund.leverage),
        "balance": str(coord.engine.balance),
        "twr_return": str(coord.engine.twr_return),
        "mismatch": mismatch,
        # 🔴 펀드 전체 낙폭 (2026-08-30 사용자 요구). RUN 별 낙폭은 판마다의 것이라
        #    백테스트 MDD(포트폴리오 곡선)와 비교가 안 된다 — 펀드 층에서 재야 같은 자다.
        "drawdown_pct": str(coord.engine.ledger.drawdown_pct),
        "max_drawdown_pct": str(coord.engine.ledger.max_drawdown_pct),
        "next_tick": _next_tick_at.isoformat() if _next_tick_at else None,
        # ⭐ 자동 앵커 (T285) — 총자본이 거래소 계좌에 맞춰졌나 · 못 맞췄으면 이유.
        "anchor": (
            None
            if fund.anchor is None and fund.anchor_skipped is None
            else {
                "mode": None if fund.anchor is None else fund.anchor.mode,
                "at": None
                if fund.anchor is None or fund.anchor.at is None
                else fund.anchor.at.isoformat(),
                "idle": None if fund.anchor is None else str(fund.anchor.idle),
                "skipped": fund.anchor_skipped,
            }
        ),
        "per_symbol": per_symbol,
    }


BASKETS_CONFIG = Path(os.environ.get("BASKETS_CONFIG", "config/baskets.yml"))


def basket_block_of(market: str) -> str:
    """시장 → `config/baskets.yml` 블록 이름.

    코인 `default` · 해외주식 `foreign_stock` · 국내주식 `domestic_stock`.

    Args:
        market: 시장 이름. 모르는 이름이면 `default`.

    Returns:
        블록 이름.
    """
    try:
        group = MarketGroup.of(Market(market))
    except ValueError:
        return "default"
    if group is MarketGroup.FOREIGN_STOCK:
        return "foreign_stock"
    if group is MarketGroup.DOMESTIC_STOCK:
        return "domestic_stock"
    return "default"


def _default_basket(market: str, playbook: str = "") -> tuple[list[dict[str, str]], list[str]]:
    """기본 바스켓 (config/baskets.yml) 을 대상 거래소에 맞춰 거른다.

    Args:
        market: 거래소·시장. 코인(GATE/BINANCE)은 `default` 블록에서 testnet 에 계약이 없는 종목을
            빼고, 해외주식은 `foreign_stock`, 국내주식은 `domestic_stock` 블록을 쓴다.
        playbook: 매매법 id. `by_playbook` 에 그 매매법의 바스켓이 있고 시장 묶음(`block`)이 맞으면
            그것이 우선이다(측정된 우주 = 펀드 종목 · 2026-09-17). 없으면 묶음 블록.

    Returns:
        (남는 멤버들, 이 거래소라서 뺀 종목들). 파일이 없거나 그 묶음의 블록이 비면 둘 다 빈 목록 —
        조용히 엉뚱한 기본값(주식 펀드에 코인)을 만들지 않는다 (규칙 #8 · 2026-09-10 실측).
    """
    try:
        raw: object = yaml.safe_load(BASKETS_CONFIG.read_text(encoding="utf-8"))
    except OSError:
        return [], []
    if not isinstance(raw, dict):
        return [], []
    top = cast("dict[str, Any]", raw)
    # ⭐ 매매법별 바스켓이 있으면 그것이 우선 (2026-09-17 · T279 데모). 매매법이 측정된 우주와 펀드
    #    종목이 같아야 리더보드 성적이 그 펀드의 기대치다. 다른 시장 묶음(코인 매매법에 주식
    #    거래소)이면 안 쓴다.
    body: object = None
    if playbook:
        by_book = cast("dict[str, Any]", top.get("by_playbook") or {})
        candidate = by_book.get(playbook)
        if isinstance(candidate, dict):
            book_spec = cast("dict[str, Any]", candidate)
            if str(book_spec.get("block", "default")) == basket_block_of(market):
                body = book_spec
    if body is None:
        body = top.get(basket_block_of(market))
    if not isinstance(body, dict):
        return [], []
    spec = cast("dict[str, Any]", body)
    by_market = cast("dict[str, Any]", spec.get("testnet_missing") or {})
    # 🔴 `testnet_missing` 은 **주문이 테스트넷으로 갈 때만** 뺀다 (2026-09-05 실계좌 첫 펀드에서
    #    NEAR 가 빠져 6종 계획이 5종으로 떴다). 실계좌(APP_ENV=live + LIVE_ORDERS=1)에는
    #    NEAR 가 있다.
    missing: set[str] = set()
    if not _orders_go_live():
        missing = {str(item) for item in cast("list[object]", by_market.get(market) or [])}
    members = [
        {"symbol": str(row["symbol"]), "weight": str(row["weight"])}
        for row in cast("list[dict[str, Any]]", spec.get("members") or [])
        if str(row["symbol"]) not in missing
    ]
    return members, sorted(missing)


def _leg_scopes() -> dict[str, list[str]]:
    """매매법별 종목 범위 `{매매법 id: 종목들}` — `config/baskets.yml by_playbook` (T291).

    Returns:
        파일이 없거나 모양이 다르면 빈 딕셔너리 — 그러면 `declared_legs` 가 "종목 범위가 없다" 로
        펀드 생성을 막는다(조용히 전 종목으로 풀지 않는다 · 규칙 #8).
    """
    try:
        raw: object = yaml.safe_load(BASKETS_CONFIG.read_text(encoding="utf-8"))
    except OSError:
        return {}
    if not isinstance(raw, dict):
        return {}
    by_book = cast("dict[str, Any]", cast("dict[str, Any]", raw).get("by_playbook") or {})
    out: dict[str, list[str]] = {}
    for name, body in by_book.items():
        if isinstance(body, dict):
            rows = cast("list[dict[str, Any]]", cast("dict[str, Any]", body).get("members") or [])
            out[str(name)] = [str(row["symbol"]) for row in rows]
    return out


def _legs_for(playbook_id: str, members: Sequence[str]) -> tuple[FundLeg, ...]:
    """그 매매법이 다리로 나뉘는 묶음이면 다리들을 만든다 — 아니면 빈 튜플 (T291).

    Args:
        playbook_id: 펀드가 고른 매매법.
        members: 펀드의 종목.

    Returns:
        다리들. `split_legs` 가 아닌 매매법(지금까지의 전부)은 빈 튜플.

    Raises:
        HTTPException: 400 — 다리 선언이 서로 안 맞는다(자리 수·배율·종목 범위).
    """
    books = load_playbooks()
    wrapper = next((item for item in books if item.playbook_id == playbook_id), None)
    if wrapper is None or not wrapper.split_legs:
        return ()
    try:
        return declared_legs(wrapper, books, _leg_scopes(), members)
    except LegError as exc:
        raise HTTPException(400, f"다리 선언이 맞지 않는다: {exc}") from exc


@router.get("/defaults")
async def defaults(market: str = "GATE", playbook: str = "") -> dict[str, Any]:
    """펀드 생성 폼의 기본값 — 화면 하드코딩의 대체 (T63 ②).

    Args:
        market: 대상 거래소. 그 testnet 에 없는 종목은 걸러서 준다.
        playbook: 고른 매매법 — 매매법별 바스켓(`baskets.yml by_playbook`)이 있으면 그것을
            준다(2026-09-17). 비면 기본 매매법.

    Returns:
        `{playbook, leverage, members, missing}`. `missing` 은 걸러진 종목 — 화면이
        "실계좌에선 포함" 안내를 그릴 수 있다.

    Note:
        바스켓 SSoT 는 config/baskets.yml 이다. 화면 상수("코어4")가 낡아 실제 라이브
        6종과 어긋났던 것이 이 엔드포인트가 생긴 이유다 (T63 §0).

        🔴 **배율도 같은 이유로 여기서 준다** (2026-08-30). 화면에 리터럴 `3` 이
        박혀 있어서 6x 로 측정한 1.3.0 을 골라도 3 이 떴다 — 문서와 화면이 다른 값을
        말하면 사람이 손으로 고치다 틀리고, 성적이 어느 배율의 것인지 모르게 된다.
    """
    members, missing = _default_basket(market, playbook)
    book = playbook or default_playbook()
    found = next((item for item in load_playbooks() if item.playbook_id == book), None)
    return {
        "playbook": book,
        "leverage": None if found is None or found.leverage is None else str(found.leverage),
        "members": members,
        "missing": missing,
    }


def fallback_leverage(market: Market) -> str:
    """선언도 페이로드도 없을 때의 펀드 배율.

    옛 판들의 값 3 을 유지하되, 능력표가 배율을 막는 시장(주식 현물)은 1 이다 — 3 이면
    원장이 거절해 펀드 생성이 "일부 롤백됨" 으로 끝난다 (T271 실측 2026-09-10 · 채팅
    위저드로 NASDAQ 펀드를 만들다 발견).

    Args:
        market: 펀드가 나갈 시장.

    Returns:
        배율 문자열 — `Decimal` 로 바로 읽는다.
    """
    return "3" if capabilities_of(market).leverage_allowed else "1"


@router.post("")
async def create(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """펀드를 만든다 — 바스켓·시작자본·레버리지.

    Args:
        request: 요청 — 이 매매법으로 펀드를 열 권한을 본다 (T230).
        payload: `{label, total_cash, leverage, playbook, market, members: [{symbol, weight}]}`.
            `market` 은 GATE(기본)/BINANCE — 펀드의 모든 세션이 그 거래소로 나간다 (T62 P3b).

    Returns:
        펀드 현황.

    Raises:
        HTTPException: 바스켓/값이 잘못됐으면 400.
    """
    try:
        members = as_members(
            [(str(m["symbol"]), Decimal(str(m["weight"]))) for m in payload.get("members", [])]
        )
        basket = Basket(members, version=str(payload.get("basket_version", "v1")))
        total = Decimal(str(payload.get("total_cash", "0")))
    except (KeyError, ArithmeticError, ValueError) as exc:
        raise HTTPException(400, f"펀드 정의가 잘못됐다: {exc}") from exc
    if total <= 0:
        raise HTTPException(400, f"시작 자본이 0 이하다: {total}")
    book = str(payload.get("playbook") or default_playbook())
    require_playbook_trade(request, (book,))  # T230 — 이 매매법으로 펀드를 열 권한
    require_market_trade(request, Market(str(payload.get("market") or Market.GATE.value)))  # T242
    # 🔴 **배율을 안 주면 매매법이 선언한 값을 쓴다** (2026-08-30). 리터럴 `3` 이
    #    여기 박혀 있어서, 6x 에서 측정한 1.3.0 을 배율 없이 만들면 조용히 3x 로 떴다.
    #    ⛔ 선언이 없는 매매법에서만 3 으로 떨어진다 (옛 판들의 값 — 동작 불변).
    declared_book = next((item for item in load_playbooks() if item.playbook_id == book), None)
    declared = None if declared_book is None else declared_book.leverage
    # ⭐ 배분 방식·알트 배율도 배율과 같은 규칙이다 (T202 중 발견) — 페이로드가 없으면
    #    **매매법 선언을 쓴다**. 화면에 입력칸이 없어서 2.0.0 을 골라도 static 으로
    #    뜨던 함정을 선언 폴백으로 막는다. 페이로드가 명시하면 그쪽이 이긴다.
    declared_mode = None if declared_book is None else declared_book.weight_mode
    declared_alt = None if declared_book is None else declared_book.alt_leverage
    # ⭐ P3(T279 83차) — 자리 수·연속 손절 정지도 매매법 선언이 기본이고 페이로드가 명시하면
    #    그쪽이다.
    #    `weight_mode` 가 slots 가 아니면 자리 배분은 꺼진다(선언 실수로 두 모형이 섞이지 않게).
    mode = str(payload.get("weight_mode") or declared_mode or "static")
    slots = int(payload.get("slots") or (0 if declared_book is None else declared_book.slots) or 0)
    halt = int(
        payload.get("halt_after_stops")
        or (0 if declared_book is None else declared_book.halt_after_stops)
        or 0
    )
    raw_cap = payload.get("notional_cap")
    notional_cap = (
        (None if declared_book is None else declared_book.notional_cap)
        if raw_cap in (None, "")
        else Decimal(str(raw_cap))
    )
    # ⭐ T286 — 줄여서 진입 · 낙폭 브레이크. 화면에 입력칸이 없으므로 **선언이 유일한 출처**다.
    notional_fit = bool(
        payload.get("notional_fit")
        if payload.get("notional_fit") is not None
        else (False if declared_book is None else declared_book.notional_fit)
    )
    drawdown_brake = None if declared_book is None else declared_book.drawdown_brake
    breadth_cap = (
        None if declared_book is None else declared_book.breadth_cap
    )  # T289 · 선언이 유일한 출처
    if mode == "slots" and slots <= 0:
        raise HTTPException(400, "weight_mode 가 slots 인데 slots 가 없다 — 매매법 선언을 본다")
    raw_alt = payload.get("alt_leverage")
    fallback = fallback_leverage(Market(str(payload.get("market") or Market.GATE.value)))
    # ⭐ T291 — 다리로 나뉘는 묶음이면 다리를 만든다(선언이 안 맞으면 여기서 400 · 세션 띄우기 전).
    legs = _legs_for(book, list(basket.symbols))
    if legs and mode != "slots":
        # 조용히 문 하나짜리 묶음으로 뜨면 두 다리가 자리·상한을 나눠 써 측정과 다른 매매법이 된다.
        raise HTTPException(400, "다리로 나뉘는 매매법은 자리 배분(weight_mode: slots)이어야 한다")
    try:
        # 🔴 **브라우저가 끊어도 생성은 끝까지 간다** (2026-09-05 실측). 느린 서버에서 종목당
        #    15초라 6종목이 20초 시한을 넘겼고, 클라이언트가 연결을 닫자(nginx 499) 요청 태스크가
        #    취소돼 만든 판 4개를 롤백했다 — 사람 눈엔 "펀드 생성이 안 된다". shield 로 생성
        #    태스크를 요청 수명에서 떼어 낸다. 결과는 어차피 화면 폴링(`GET /rebalancer`)이
        #    보여 준다.
        fund = await asyncio.shield(
            _create_fund(
                basket=basket,
                total_cash=total,
                leverage=Decimal(str(payload.get("leverage") or declared or fallback)),
                playbook=book,
                label=str(payload.get("label", "리밸런싱 펀드")),
                market=str(payload.get("market", "GATE")),
                weight_mode=mode,
                alt_leverage=(declared_alt if raw_alt in (None, "") else Decimal(str(raw_alt))),
                slots=slots if mode == "slots" else 0,
                halt_after_stops=halt,
                notional_cap=notional_cap if mode == "slots" else None,
                notional_fit=notional_fit if mode == "slots" else False,
                drawdown_brake=drawdown_brake,
                breadth_cap=breadth_cap if mode == "slots" else None,
                legs=legs,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        # 🔴 거래소에 없는 종목(testnet 에 ZEC/NEAR 없음) 등은 500 이 아니라 이유를 준다.
        #    부분 생성은 _create_fund 가 이미 정리했다 (규칙 #8).
        raise HTTPException(400, f"펀드 생성 실패 (일부 롤백됨): {exc}") from exc
    return await _status(fund)


@router.get("")
async def listing() -> dict[str, Any]:
    """도는 펀드 전부 — 콘솔이 RUN 위에 이 목록을 그린다 (T61 별도 취급).

    Returns:
        `{funds: [펀드 현황...]}`.
    """
    return {"funds": [await _status(fund) for fund in FUNDS.values()]}


MEMBER_BARS = 90
"""상세보기가 그리는 일봉 수 (기본)."""
MEMBERS_TTL_S = 300.0
"""상세 응답 기억 시간 — 종목마다 브로커 일봉이라 폴링마다 부르지 않는다."""
_MEMBERS_CACHE = TtlCache[dict[str, Any]]("fund.members", MEMBERS_TTL_S)


def bar_changes(closes: Sequence[Decimal]) -> dict[str, str | None]:
    """마지막 종가 기준 1일·5일 등락(%) — 상세 카드의 상태 한 줄 (순수 · T261).

    Args:
        closes: 일봉 종가 오름차순.

    Returns:
        `{last, change_1d_pct, change_5d_pct}` — 봉이 모자라면 그 칸은 None.
    """

    def _pct(back: int) -> str | None:
        if len(closes) <= back or closes[-1 - back] == 0:
            return None
        return str(((closes[-1] / closes[-1 - back]) - 1) * 100)

    return {
        "last": str(closes[-1]) if closes else None,
        "change_1d_pct": _pct(1),
        "change_5d_pct": _pct(5),
    }


@router.get("/{fund_id}/members")
async def members(fund_id: str, bars: int = MEMBER_BARS) -> dict[str, Any]:
    """펀드 종목 상세 — 종목마다 마감 일봉과 간단한 상태 (사용자 요구 2026-09-10 "상세보기").

    Args:
        fund_id: 펀드 id.
        bars: 일봉 수 (10~250).

    Returns:
        `{fund_id, market, at, members: [{symbol, weight, equity, holding, position, unrealized,
        last, change_1d_pct, change_5d_pct, bars: [{time, open, high, low, close, volume}]}]}`.
        봉을 못 받은 종목은 `bars` 가 비고 `bars_error` 에 이유가 있다.

    Raises:
        HTTPException: 404 — 펀드가 없다.
    """
    fund = _fund_or_404(fund_id)
    wanted = max(10, min(int(bars), 250))
    key = f"{fund_id}:{wanted}"
    cached = _MEMBERS_CACHE.get(key)
    if cached is not None:
        return cached
    market = Market(fund.market)
    end = datetime.now(UTC)
    start = end - timedelta(days=int(wanted * 1.6) + 7)
    span = interval(Timeframe.D1)
    rows: list[dict[str, Any]] = []
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        for sym in fund.coordinator.engine.basket.symbols:
            leg = await _per_symbol(fund, sym)
            candles: list[Any] = []
            try:
                candles = list(
                    await adapter.get_candles(instrument_of(sym, market), Timeframe.D1, start, end)
                )
            except Exception as exc:
                leg["bars_error"] = str(exc)[:120]
            # 마지막 봉이 아직 진행 중이면 뺀다 — 형성 중인 봉을 마감처럼 그리지 않는다.
            if candles and candles[-1].ts + span > end:
                candles = candles[:-1]
            closed = candles[-wanted:]
            rows.append(
                {
                    **leg,
                    "symbol": sym,
                    **bar_changes([c.close for c in closed]),
                    "bars": [
                        {
                            "time": int(c.ts.timestamp()),
                            "open": str(c.open),
                            "high": str(c.high),
                            "low": str(c.low),
                            "close": str(c.close),
                            "volume": str(c.volume),
                        }
                        for c in closed
                    ],
                }
            )
    body: dict[str, Any] = {
        "fund_id": fund.fund_id,
        "market": market.value,
        "at": end.isoformat(),
        "members": rows,
    }
    _MEMBERS_CACHE.put(key, body)
    return body


@router.get("/{fund_id}")
async def status(fund_id: str) -> dict[str, Any]:
    """펀드 현황.

    Args:
        fund_id: 펀드 id.

    Returns:
        예산·잔고·TWR·세션 목록.

    Raises:
        HTTPException: 404 — 펀드가 없다.
    """
    fund = _fund_or_404(fund_id)
    return await _status(fund)


@router.post("/{fund_id}/tick")
async def tick(fund_id: str) -> dict[str, Any]:
    """한 주기 리밸런싱을 **수동으로** 돈다 — 각 세션 예산 갱신 (주문 아님).

    Args:
        fund_id: 펀드 id.

    Returns:
        새 예산·잔고 등 이번 틱의 보고.

    Raises:
        HTTPException: 404 — 펀드가 없다.

    Note:
        자동 4h 루프는 testnet 검증 뒤에 켠다. 그전엔 이 수동 틱으로 동작을 확인한다.
    """
    fund = _fund_or_404(fund_id)
    report = await _tick(fund)
    return {
        "budgets": {s: str(b) for s, b in report.budgets.items()},
        "balance": str(report.balance),
        "twr_return": str(report.twr_return),
        "missing": list(report.missing),
        "winding_down": list(report.winding_down),
    }


@router.delete("/{fund_id}")
async def drop(fund_id: str) -> dict[str, Any]:
    """펀드를 접는다 — 세션을 전부 청산·정리하고 정의 파일은 `archive/` 로 **보관** (T266).

    Args:
        fund_id: 펀드 id.

    Returns:
        `{dropped, sessions}` — 지운 펀드와 거둔 세션 핸들.

    Raises:
        HTTPException: 404 — 펀드가 없다.

    Note:
        🔴 세션의 열린 포지션까지 닫는다 (`_close_live_position`) — 러너만 죽이면 거래소에
        관리자 없는 포지션이 남는다.
    """
    fund = _fund_or_404(fund_id, pop=True)
    for handle in fund.handles.values():
        await _close_live_position(handle)
        await _drop_one(handle)
    _archive_fund_file(fund_id)
    return {"dropped": fund_id, "sessions": list(fund.handles.values())}


@router.put("/{fund_id}/basket")
async def edit_basket(fund_id: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """바스켓을 바꾼다 — 종목 추가/제거 + 비중 조절 (동적, 무중단).

    Args:
        fund_id: 펀드.
        payload: `{members: [{symbol, weight}], basket_version}`. 새 구성 전체.

    Returns:
        갱신된 펀드 현황.

    Raises:
        HTTPException: 404 펀드 없음 · 400 바스켓 정의 오류 또는 종목 추가 실패(되돌림).

    Note:
        🔴 새 종목은 지금 잔고에서 비중대로 세션을 띄우고, 빠진 종목은 청산·정리한다. 남은
        종목은 다음 진입부터 새 비중을 쓴다 (보유 중 강제 리사이즈 안 함 — 설계 B). basket 버전을
        올려 바꾸기 전/후 성과를 안 섞는다. 추가가 실패하면 방금 띄운 세션만 되돌리고 펀드는 그대로.
    """
    fund = _fund_or_404(fund_id)
    try:
        members = as_members(
            [(str(m["symbol"]), Decimal(str(m["weight"]))) for m in payload.get("members", [])]
        )
        new_basket = Basket(members, version=str(payload.get("basket_version", "v2")))
    except (KeyError, ArithmeticError, ValueError, BasketError) as exc:
        raise HTTPException(400, f"바스켓 정의가 잘못됐다: {exc}") from exc

    engine = fund.coordinator.engine
    old_basket = engine.basket
    old = set(old_basket.symbols)
    new = set(new_basket.symbols)
    # ⭐ T291 — 다리 펀드는 새 종목 구성으로 다리의 종목 범위를 다시 낸다(어느 다리에도 안 속하는
    #    종목이 있으면 여기서 400 · 아무것도 건드리기 전). 계좌 층 값은 **돌던 다리의 것**을 지킨다.
    old_legs = fund.legs
    if old_legs:
        fresh = {leg.playbook: leg for leg in _legs_for(fund.playbook, list(new_basket.symbols))}
        if set(fresh) != {leg.playbook for leg in old_legs}:
            raise HTTPException(400, "이 종목 구성으로는 다리 하나가 비게 된다")
        fund.legs = tuple(replace(leg, symbols=fresh[leg.playbook].symbols) for leg in old_legs)

    # 🔴 순서: **빠진 종목 청산 → 틱 → 새 종목 스폰 → 빠진 세션 정리** (T285 · 2026-09-17).
    #    빠진 종목의 강제 청산 손익이 원장에 적힌 뒤에 틱이 돌아야 그 증분이 총자본에 든다(정리는
    #    `release` 라 그 뒤 실현도 다음 틱이 흡수한다). 총자본은 펀드가 들고 세션은
    #    증분만 보고하므로 스폰 순서가 돈을 만들지 않는다(2026-08-25 "허공에서 +12.5%" 는
    #    평가금액 합산 구조의 결함이었다).
    #    새 멤버 예산은 틱이 낸 것 · 기존 멤버는 보유 중이면 다음 진입부터(설계 B).
    for sym in old - new:
        handle = fund.handles.get(sym, "")
        if handle:
            await _close_live_position(handle)
    engine.basket = new_basket
    report = await _tick(fund)

    added: list[str] = []
    try:
        for member in new_basket.members:
            if member.symbol in old:
                continue
            share = report.budgets.get(member.symbol, Decimal(0))
            member_book, member_lev = _member_terms(
                fund.playbook, fund.leverage, fund.alt_leverage, fund.legs, member.symbol
            )
            handle, port = await _spawn_session(
                member.symbol,
                share,
                member_lev,
                member_book,
                market=fund.market,
                booked=(
                    min(share, engine.balance * member.weight / new_basket.weight_sum)
                    if fund.legs
                    else None
                ),
            )
            fund.handles[member.symbol] = handle
            fund.coordinator.ports[member.symbol] = port  # type: ignore[index]
            added.append(member.symbol)
        _reattach_gate(fund)  # 새 세션에도
    except Exception as exc:
        for sym in added:  # 방금 띄운 것만 되돌린다 — 펀드는 그대로
            handle = fund.handles.pop(sym, "")
            fund.coordinator.ports.pop(sym, None)
            if handle:
                await _close_live_position(handle)
                await _drop_one(handle)
        engine.basket = old_basket  # 바스켓·예산도 원상 복구
        fund.legs = old_legs
        _reattach_gate(fund)
        fund.coordinator.tick()
        raise HTTPException(400, f"종목 추가 실패 (되돌림): {exc}") from exc

    for sym in old - new:  # 빠진 종목 정리 (청산은 위에서 · 미정산 증분은 release 가 다음 틱으로)
        handle = fund.handles.pop(sym, "")
        fund.coordinator.release(sym)
        if handle:
            await _drop_one(handle)

    _save_fund(fund)
    return await _status(fund)


@router.put("/{fund_id}/playbook")
async def change_playbook(
    request: Request, fund_id: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """전략(매매법)을 바꾼다 — 모든 세션을 새 전략으로 다시 띄운다 (동적 전환).

    Args:
        request: 요청 — 새 매매법을 쓸 권한을 본다 (T230).
        fund_id: 펀드.
        payload: `{playbook}` — 새 전략 id (예: `sample_ma_cross`).

    Returns:
        갱신된 펀드 현황.

    Raises:
        HTTPException: 404 펀드 없음 · 400 모르는 전략 또는 전환 실패.

    Note:
        🔴 **세션의 매매법은 생성 때 박힌다** — 바꾸려면 각 세션을 새 전략으로 재기동해야 한다.
        ✅ **무중단 전환** (T61): 열린 포지션을 **닫지 않는다**. 앞 세션의 러너만 거두고
        (`_drop_one`, 거래소 포지션·주문은 그대로), 새 전략 세션이 그 포지션을 이어받는다
        (`adopt`) — 표식(run_key)을 물려줘 "내 포지션" 으로 인식시키고, 증거금이 이미
        포지션에 있으므로 잔액 검사를 건너뛴다(`reviving`). 현금 대기 중이면 이어받을 것이
        없어 그냥 새로 시작한다. 예산·비중·TWR 은 그대로 이어간다.

        ⚠️ **손절이 거래소에 남아 있어야 이어받는다** (live_runner.adopt). 앞 세션이 손절을
        걸어 뒀으면 문제없다 — 못 찾으면 안 줍고 감사가 사람에게 넘긴다(규칙 #4·#8).
    """
    fund = _fund_or_404(fund_id)
    new_pb = str(payload.get("playbook", ""))
    if new_pb not in {p.playbook_id for p in load_playbooks()}:
        raise HTTPException(400, f"모르는 전략: {new_pb!r}")
    require_playbook_trade(request, (new_pb,))  # T230
    require_market_trade(request, Market(fund.market))  # T242
    if new_pb == fund.playbook:
        return await _status(fund)

    # 🔴 **계좌 층도 새 매매법의 것으로 바꾼다** (T286 · 2026-09-19). 전에는 진입·청산만 갈아
    #    끼우고 자리·상한·줄여서 진입·낙폭 브레이크는 **옛 매매법의 값이 그대로 남았다**.
    #    그러면 a6 로 바꾼 펀드가 a6 의 진입을 하면서 계좌 층이 없는 A0 (MDD 45%)로 돌고,
    #    화면 라벨과 성적은 A (MDD 33%)를 말한다 — 측정과 현실이 조용히 갈라진다.
    #    ⚠️ 배율은 안 건드린다 — 사람이 펀드를 만들 때 정한 값이고, 바꾸면 이미 열린 포지션의
    #       증거금 전제가 달라진다. 선언 배율과 다르면 화면이 그 사실을 보여 준다.
    declared_new = next((p for p in load_playbooks() if p.playbook_id == new_pb), None)
    # ⭐ T291 — 새 매매법이 다리로 나뉘는 묶음이면 **지금 종목으로** 다리를 낸다(안 맞으면 여기서
    #    400 · 세션을 건드리기 전). 다리 없는 매매법으로 가면 빈 튜플 — 문 하나로 돌아간다.
    #    ⚠️ 종목은 안 바꾼다. 핵심 6종 펀드를 18종 매매법으로 바꾸면 숏 다리도 그 6종에서만 돈다 —
    #       나머지 종목은 바스켓 편집으로 더한다(측정된 우주는 `baskets.yml by_playbook` 에 있다).
    new_legs = _legs_for(new_pb, list(fund.coordinator.engine.basket.symbols))
    if new_legs and (declared_new is None or declared_new.weight_mode != "slots"):
        raise HTTPException(400, "다리로 나뉘는 매매법은 자리 배분(weight_mode: slots)이어야 한다")
    if declared_new is not None:
        fund.weight_mode = declared_new.weight_mode or fund.weight_mode
        fund.slots = declared_new.slots
        fund.halt_after_stops = declared_new.halt_after_stops
        fund.notional_cap = declared_new.notional_cap if declared_new.slots > 0 else None
        fund.notional_fit = declared_new.notional_fit and declared_new.slots > 0
        fund.drawdown_brake = declared_new.drawdown_brake
        fund.breadth_cap = declared_new.breadth_cap if declared_new.slots > 0 else None
        # 🔴 엔진의 자리 수도 같이 — 예산을 `총자본 ÷ 자리` 로 낼지 비중으로 낼지가 여기서 갈린다.
        #    안 바꾸면 자리 6 을 선언해 놓고 예산은 비중대로 나가 두 모형이 섞인다.
        fund.coordinator.engine.slots = declared_new.slots
        _logger.info(
            "fund_rules_switched",
            extra={
                "fund_id": fund.fund_id,
                "playbook": new_pb,
                "slots": fund.slots,
                "notional_cap": None if fund.notional_cap is None else str(fund.notional_cap),
                "notional_fit": fund.notional_fit,
                "brake": None if fund.drawdown_brake is None else str(fund.drawdown_brake.at),
                "breadth_cap": None if fund.breadth_cap is None else str(fund.breadth_cap.cap),
                "note": "매매법을 바꾸면 계좌 층도 그 선언의 것으로 간다 (T286)",
            },
        )

    await _tick(fund)  # 총자본 갱신 (청산 전 값으로 재배분 · 앵커 포함)
    total = fund.coordinator.engine.balance
    basket = fund.coordinator.engine.basket
    wsum = basket.weight_sum
    new_handles: dict[str, str] = {}
    new_ports: dict[str, SessionBridge] = {}
    try:
        for member in basket.members:
            old = fund.handles.get(member.symbol)
            if old:  # 러너만 거둔다 — 거래소 포지션·주문은 그대로 (무중단)
                await _drop_one(old)
            # 자리 배분이면 `총자본 ÷ 자리`(합이 총자본을 넘는 것이 의도) · 아니면 비중대로.
            #    생성·복원 경로와 같은 식이어야 한다 (T286 — 전에는 여기만 비중이었다).
            share = total / Decimal(fund.slots) if fund.slots > 0 else total * member.weight / wsum
            # old 를 물려주면 새 세션이 그 포지션을 이어받는다 (없으면 새로 시작)
            member_book, member_lev = _member_terms(
                new_pb, fund.leverage, fund.alt_leverage, new_legs, member.symbol
            )
            handle, port = await _spawn_session(
                member.symbol,
                share,
                member_lev,
                member_book,
                market=fund.market,
                adopt_from=old,
                booked=min(share, total * member.weight / wsum) if new_legs else None,
            )
            new_handles[member.symbol] = handle
            new_ports[member.symbol] = port
    except Exception as exc:
        raise HTTPException(400, f"전략 전환 실패: {exc}") from exc

    fund.handles = new_handles
    fund.coordinator.ports = new_ports  # type: ignore[assignment]
    # 새 세션의 원장은 0 부터 — 옛 정산 mark 를 물려주면 증분이 틀린다 (T285)
    fund.coordinator.marks = {}
    fund.playbook = new_pb
    fund.legs = new_legs
    _reattach_gate(fund)  # 갈아 끼운 세션에도
    await _tick(fund)
    return await _status(fund)


@router.post("/{fund_id}/resync")
async def resync(fund_id: str) -> dict[str, Any]:
    """**갈린 세션의 원장을 지금 거래소 상태로 재정렬한다** (사용자 요구 2026-09-01).

    Args:
        fund_id: 펀드.

    Returns:
        갱신된 펀드 현황 + 재정렬한 종목들.

    Raises:
        HTTPException: 404 — 펀드가 없다.

    Note:
        🔴 **복구 불가한 과거를 버리고 앵커한다.** 재사용된 계정에 며칠치가 쌓이고
        원장이 재개로 낡으면 어느 손익도 진짜가 아닌 복구 불가 상태가 된다. 외과적
        교정 대신, 갈린 세션(포지션 갈림·회계 갈림)만 골라 *지금 거래소 상태* 에
        앵커한다 — 워터마크·실현 앵커를 지금으로, 포지션 재이어받기, 갈림 플래그 해제.

        ⛔ **리셋이 아니다.** 거래소 계정·돈·포지션은 안 건드린다. **깨끗한 세션은
        안 건드린다** — 갈린 것만 재정렬해 정상 세션의 실적을 안 지운다.

        ⚠️ 공유 계정에선 세션별 거래소 현금을 못 가르므로 equity 는 원장 기준을
        유지한다(라이브 전용 계정이면 진짜 잔고로 앵커 가능). 지금은 표시 실현·감사
        기준점만 옮긴다 — 그래도 허구 손익이 화면·대조에서 사라진다.
    """
    fund = _fund_or_404(fund_id)
    resynced: list[str] = []
    for sym, handle in fund.handles.items():
        live = SESSIONS.get(handle)
        runner = LIVE_RUNNERS.get(handle)
        if live is None or runner is None:
            continue
        # 🔴 **갈린 세션만** — 깨끗한 세션의 정상 실적을 안 지운다.
        if live.session.reconciled and live.session.accounting_ok:
            continue
        with contextlib.suppress(Exception):
            await runner.resync_ledger()
            resynced.append(sym)
    await _tick(fund)  # 재정렬된 실현으로 총자본·TWR 갱신 (앵커 포함)
    status = await _status(fund)
    status["resynced"] = resynced
    return status


async def _account_headroom(market: str) -> tuple[Decimal, Decimal] | None:
    """(계좌 총액, 이미 원장에 배정된 합) — 입금이 실제 돈에 덮이는지 보는 두 값.

    Args:
        market: 거래소.

    Returns:
        `(total, pooled)`. 계좌를 못 읽으면 None — 그때는 입금을 **확인할 수 없다**고 말해야
        한다 (조용히 통과시키지 않는다 · 절대 규칙 #8).

    Note:
        총액 = 쓸 수 있는 돈 + 포지션에 잡힌 증거금 (리포트 `_account_summary` 와 같은 정의).
        배정 합 = 이 거래소에서 도는 모든 세션 원장의 equity — 펀드 안팎을 가리지 않는다.
        같은 계좌를 여러 판이 나눠 쓰므로, 남은 자리(headroom) = 총액 - 배정 합이다.
    """
    from updown.apps.api.exchange import (
        _all_live,  # pyright: ignore[reportPrivateUsage]
        _orders_adapter,  # pyright: ignore[reportPrivateUsage]
        _tracked,  # pyright: ignore[reportPrivateUsage]
    )

    try:
        orders = _orders_adapter(market)
        balance = await orders.get_balance()
        tracked = await _tracked(orders, market)
        positions = (await _all_live(orders, market, tracked)).get("positions", [])
        locked = sum((Decimal(str(p.get("margin", "0") or "0")) for p in positions), Decimal(0))
        total = Decimal(str(balance.cash)) + locked
    except Exception as exc:
        _logger.warning("fund_headroom_unavailable: %s %s", market, str(exc)[:160])
        return None
    pooled = Decimal(0)
    for handle, live in SESSIONS.items():
        runner = LIVE_RUNNERS.get(handle)
        if runner is not None and runner.instrument.market.value == market:
            pooled += live.session.ledger.equity
    return total, pooled


async def _wallet_total(market: str) -> Decimal | None:
    """거래소가 **직접 말하는** 지갑 총액 — 앵커의 기준 (T285 · 2026-09-18).

    Args:
        market: 거래소.

    Returns:
        지갑 총액(가용 + 포지션 증거금 + **대기 주문 증거금** · 미실현 제외). 못 읽으면 None.

    Note:
        🔴 총액을 `가용 + 포지션 증거금` 으로 **재구성하면 안 된다** (2026-09-17~18 실계좌 실측).
        16:00 에 낸 지정가 진입 주문이 04:00 까지 걸려 있는 동안 그 주문 증거금 33.5 USDT 가
        빠져 계좌가 298.09 → 264.58 로 읽혔고, 앵커가 그것을 손실로 적어 세 틱 동안 예산이
        11% 줄고 없던 낙폭 11.8% 가 장부에 남았다. 돈은 한 푼도 안 움직였다. 어댑터의
        `margins()` 가 주는 거래소의 `total` 을 그대로 쓴다. `margins()` 가 없는 어댑터만
        예전 재구성 값으로 떨어진다.
    """
    try:
        from updown.apps.api.exchange import _orders_adapter  # pyright: ignore[reportPrivateUsage]

        orders: Any = _orders_adapter(market)
        if hasattr(orders, "margins"):
            raw = cast("dict[str, str]", await orders.margins())
            return Decimal(str(raw["total"]))
    except Exception as exc:
        _logger.warning("fund_wallet_total_unavailable: %s %s", market, str(exc)[:160])
        return None
    facts = await _account_headroom(market)
    return None if facts is None else facts[0]


async def _anchor_for(fund: Fund) -> Anchored | None:
    """이번 틱의 자동 앵커 — 유일한 소유자일 때 계좌 총액과 입출금을 읽는다 (T285).

    Args:
        fund: 펀드.

    Returns:
        앵커 결과. 못 하면 None — 이유는 `fund.anchor_skipped` 에 남기고 화면이 그대로 보여 준다.

    Note:
        🔴 거래소는 "이 펀드의 돈" 을 모른다. 같은 거래소에 다른 펀드나 펀드 밖 단독 판이
        있으면 계좌 총액을 나눌 근거가 없어 앵커하지 않는다(증분 모형 그대로 · 조용히 넘기지
        않고 이유를 적는다).
        첫 앵커는 모드만 정하고 과거 입출금은 세지 않는다 — 그 전 돈은 이미 장부(투입 원금)에 있다.
    """
    market = fund.market
    others = [f.fund_id for f in FUNDS.values() if f.fund_id != fund.fund_id and f.market == market]
    if others:
        fund.anchor_skipped = f"같은 거래소에 다른 펀드 {len(others)}개"
        return None
    mine = set(fund.handles.values())
    strangers = 0
    for handle in SESSIONS:
        runner = LIVE_RUNNERS.get(handle)
        if handle not in mine and runner is not None and runner.instrument.market.value == market:
            strangers += 1
    if strangers:
        fund.anchor_skipped = f"펀드 밖 단독 판 {strangers}개"
        return None
    total = await _wallet_total(market)
    if total is None:
        fund.anchor_skipped = "거래소 계좌를 읽을 수 없음"
        return None
    try:
        from updown.apps.api.exchange import _orders_adapter  # pyright: ignore[reportPrivateUsage]

        rows: list[dict[str, Any]] = await _orders_adapter(market).account_book(limit=100)
    except Exception as exc:
        fund.anchor_skipped = f"자금 원장을 읽을 수 없음: {str(exc)[:60]}"
        return None
    now = datetime.now(UTC)
    ledger = fund.coordinator.engine.ledger
    state = fund.anchor
    if state is None:
        state = initial_state(total, ledger.contributed, ledger.balance)
        _, seen = dnw_since(rows, None, ())  # 지금까지의 입출금은 열쇠만 기억 — 다시 안 센다
        state = replace(state, seen=seen)
        dnw = Decimal(0)
    else:
        dnw, seen = dnw_since(rows, state.at, state.seen)
        state = replace(state, seen=seen)
    result = anchored(state, total, dnw, now)
    fund.anchor_skipped = None
    _logger.info(
        "fund_anchored: %s mode=%s total=%s before=%s dnw=%s idle=%s",
        fund.fund_id,
        result.state.mode,
        f"{total:.2f}",
        f"{result.equity_before_flow:.2f}",
        f"{dnw:.2f}",
        f"{result.state.idle:.2f}",
    )
    return result


async def _tick(fund: Fund, flow: CashFlow | None = None) -> TickReport:
    """펀드 틱 — 거래소 계좌에 총자본을 맞춘 뒤(자동 앵커) 예산을 다시 나누고 저장한다 (T285).

    Args:
        fund: 펀드.
        flow: 화면에서 기록한 입출금 (없으면 None). `drift` 모드에선 유휴 현금에서 펀드로
            옮겨 온 것이다.

    Returns:
        이번 틱 보고.

    Note:
        모든 틱(4h 루프 · 수동 · 입출금 · 편집 · 전략 전환 · 재정렬 · 복원)이 여기를 지난다 — 앵커가
        빠지는 경로가 없어야 장부가 다시 새지 않는다. 앵커를 못 하면 증분 모형으로 돈다.
    """
    found = await _anchor_for(fund)
    anchor_value: Decimal | None = None
    auto_flow: CashFlow | None = None
    if found is not None:
        anchor_value = found.equity_before_flow
        state = found.state
        if found.flow != 0:
            auto_flow = CashFlow(
                at=state.at or datetime.now(UTC), amount=found.flow, note="거래소 입출금 자동 반영"
            )
        if flow is not None:
            state = manual_flow(state, flow.amount)
        fund.anchor = state
    report = fund.coordinator.tick(flow if flow is not None else auto_flow, anchor=anchor_value)
    _save_fund(fund)
    return report


@router.post("/{fund_id}/deposit")
async def deposit(fund_id: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """입출금 — 양수 입금, 음수 출금. 흡수하고 즉시 재분배한다.

    Args:
        fund_id: 펀드.
        payload: `{amount, note}`. `amount` 양수=입금, 음수=출금.

    Returns:
        갱신된 펀드 현황.

    Raises:
        HTTPException: 404 펀드 없음 · 400 금액을 못 읽음 · 0 · 잔고를 넘는 출금.

    Note:
        🔴 입금은 **성과가 아니다** — TWR 은 안 오르고 잔고만 는다 (§4.18). 배치는 이 틱에서.

        ⚠️ 출금이 잔고를 넘으면 거절한다 (2026-09-07). 전에는 `TwrLedger.step` 이 0 으로
        **조용히** 잘라 펀드가 빈 채로 돌았다 — 조용한 실패는 금지다 (절대 규칙 #8).
        ⛔ 거래소 이체는 여기서 하지 않는다 — 이것은 원장 사건이고 돈은 사람이 옮긴다.
    """
    fund = _fund_or_404(fund_id)
    try:
        amount = Decimal(str(payload["amount"]))
    except (KeyError, ArithmeticError, ValueError) as exc:
        raise HTTPException(400, f"금액을 못 읽었다: {payload.get('amount')!r}") from exc
    if amount == 0:
        raise HTTPException(400, "금액이 0 이다 — 입금은 양수, 출금은 음수")
    balance = fund.coordinator.engine.balance
    if amount < 0 and -amount > balance:
        raise HTTPException(400, f"출금 {-amount} 이 펀드 잔고 {balance} 를 넘는다")
    if amount > 0:
        # 🔴 거래소에 없는 돈은 원장에 못 넣는다 (사용자 2026-09-07) — 넣으면 다음 진입이 없는
        #    돈으로 사이징돼 거절되고 감사에 wallet_drift 가 뜬다. 여기서 막고 얼마 부족한지 말한다.
        facts = await _account_headroom(fund.market)
        if facts is None:
            raise HTTPException(
                503, "거래소 계좌를 읽을 수 없어 입금을 확인할 수 없다 — 잠시 뒤 다시"
            )
        total, pooled = facts
        headroom = total - pooled
        if amount > headroom:
            short = amount - headroom
            room = max(headroom, Decimal(0))
            raise HTTPException(
                400,
                f"잔고가 {short:.2f} USDT 부족합니다 — 계좌 총액 {total:.2f} 중 이미 배정된 "
                f"원장 합이 {pooled:.2f} 라 넣을 수 있는 최대는 {room:.2f} 입니다",
            )
    if fund.anchor is not None and fund.anchor.mode == MODE_ACCOUNT:
        # ⭐ 계좌 모드(계좌 = 펀드)는 거래소 입출금을 자금 원장에서 자동으로 읽는다 (T285).
        #    여기서 또
        #    적으면 두 번 센다 — 거래소에 넣고 "지금 리밸런싱" 을 누르면 반영된다.
        raise HTTPException(
            400,
            "이 펀드는 계좌 전체를 굴려서 거래소 입출금을 자동으로 읽습니다 — 거래소에 넣고 "
            "'지금 리밸런싱' 을 누르면 반영됩니다",
        )
    flow = CashFlow(at=datetime.now(UTC), amount=amount, note=str(payload.get("note", "")))
    report = await _tick(fund, flow)
    return {
        "balance": str(report.balance),
        "twr_return": str(report.twr_return),
        "flow": str(amount),
    }
