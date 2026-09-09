"""라이브 러너 — 웹소켓 봉을 세션에 먹이고 주문을 낸다 (T13 조립).

## 이 파일이 하는 일은 **조립뿐**이다

판단은 세션이, 주문은 어댑터가, 봉 관리는 급전이 한다. 여기에 계산이 생기면
`orchestration/` 입주 조건을 어긴 것이고 해당 도메인으로 내려보내야 한다.

    웹소켓 (GateCandleStream)
       ↓  LiveCandle(candle, closed)
    LiveFeed.push          미마감은 pending 에만
       ↓  True (마감 봉 도착)
    Session.step()         판단 — 과거와 **같은 코드**
       ↓  Snapshot
    주문 어댑터             OrderGateway 가 준 것만

## 🔴 주문 어댑터를 직접 만들지 않는다

`OrderGateway` 가 준 것을 받는다 (절대 규칙 #0). 러너가 만들면 게이트를 우회하는
경로가 생기고, 그게 열리면 이중 게이트가 뜻을 잃는다.

## 🔴 봉 구멍을 메운다

웹소켓이 끊기면 그 사이 봉이 안 온다. `LiveFeed.gaps` 가 0 이 아니면 REST 로 받아
`backfill` 한다 — 빈 봉은 "거래가 없었다" 와 구별되지 않고, 그 상태로 판정하면 지표가
조용히 틀린다.
"""

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Protocol, cast, runtime_checkable

import structlog

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.order import OrderKind, OrderStatus
from updown.common.logging.setup import get_logger
from updown.decision.risk.policy import funding_shortfall
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.marketdata.adapter import BrokerAdapter, QuoteAdapter, RequestCounting
from updown.marketdata.ingest.timeframes import interval_seconds
from updown.marketdata.stream import CandleStream
from updown.orchestration.leftovers import held_size, sweep
from updown.orchestration.liquidity import probe_book
from updown.orchestration.walkforward import pending as pending_mod
from updown.orchestration.walkforward.funding import attribute_funding
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Funding,
    HalfBy,
    Ledger,
    Outcome,
    TradeRecord,
    new_trade_id,
    plan_fault,
)
from updown.orchestration.walkforward.live_feed import LiveFeed
from updown.orchestration.walkforward.live_filler import LiveFiller, Want
from updown.orchestration.walkforward.order_mapping import (
    RUN_CHARS,
    SENTINEL_RR,
    close_limit_order,
    close_order,
    contracts_for,
    entry_order,
    limit_entry_order,
    order_key,
    resize_order,
    rounding_drift_pct,
    run_tag,
    take_profit_orders,
)
from updown.orchestration.walkforward.session import STEP_FRAME, Session
from updown.orchestration.walkforward.store import RunStore

_logger = get_logger("walkforward.live_runner")

WARM_BARS = 5
"""이미 데워진 축을 새로고침할 때 받는 봉 수.

🔴 **한 번에 하나씩만 닫힌다.** TTL 이 봉 간격의 1/10 이므로 새로 닫힌 봉은 많아야
하나인데 400 개를 받고 있었다 — 5 는 재연결·시계 오차에 여유를 둔 값이다.

⚠️ 처음이거나 오래 끊겼으면 **400 으로 되돌아간다** (`wide`). 좁은 창만 고집하면
그 사이 구멍이 영영 안 메워진다.
"""

MARGIN_HEADROOM = Decimal("0.99")
"""라이브 주문 크기에 두는 여유 (1%).

🔴 **거래소가 요구하는 증거금은 우리 계산보다 크다** (2026-08-18 실측): 234계약에 우리는
499.70 을 셌는데 Gate 는 502.19 를 요구해 `400 INSUFFICIENT_AVAILABLE` 이 났다. 차이는
표시가(mark)와 체결가의 차 + 수수료다.

⚠️ 증거금을 잔액에 딱 맞춰 넣으면 **항상 거부되고**, 그 실패는 화면에서 "주문 0건" 으로만
보여 신호가 없는 것과 구별되지 않는다 (절대 규칙 #8).

⛔ 백테스트에는 적용하지 않는다 — 같은 입력에 같은 출력이어야 한다 (절대 규칙 #5).
   여기(라이브 경로)에만 둔다.
"""

STOP_GUARD_LIMIT = 3
"""손절을 **연속 몇 번** 못 걸면 포지션을 던지는가 (사용자 요구 2026-08-18).

> *"익절 ㅡ 손절 주문에 실패하면 (...) 최악의 경우 시장가로 던질 수 있게라도"*

🔴 **손절 없는 레버리지 포지션이 계좌를 지우는 유일한 것이다.** 익절이 없으면 이익을
놓칠 뿐이지만, 손절이 없으면 **손실에 바닥이 없다.** 그래서 둘을 다르게 다룬다:

    익절 실패  →  다음 걸음에 다시 건다 (포지션은 손절이 지킨다)
    손절 실패  →  다시 건다. 연속 3번 못 걸면 **시장가로 청산한다**

⚠️ 3 인 이유: 1 이면 일시적인 네트워크 오류에 멀쩡한 포지션을 던진다. 판정이 15분마다
   도므로 3번은 **45분**이고, 그 사이 한 번이라도 성공하면 계수가 0 으로 돌아간다.

⛔ 던지는 것은 **되돌릴 수 없다.** 그래도 손절 없이 45분을 더 버티는 것보다 낫다 —
   이 판단은 리스크를 줄이는 방향이므로 절대 규칙 #3 과 #8-1 에 부합한다.
"""

FRESH_TICK = 1.0
"""급전 갱신 루프가 도는 간격(초).

⚠️ 이것이 **조회 주기가 아니다.** 축마다 자기 TTL(`refresh`)이 있어서, 1초마다 훑어도
실제 조회는 그 축의 간격에 맞춰 나간다 — 1d 봉을 초 단위로 묻지 않는다.
"""

WATCH_TTL = 90.0
"""보기용 축을 **마지막으로 본 뒤** 이만큼은 계속 데운다 (초).

⚠️ 너무 짧으면 화면을 보는 중에 축이 식어 봉이 늙는다. 너무 길면 탭을 닫아도
한참 동안 계속 당긴다. 화면 폴링이 1.5초이므로 90초면 **60번 놓쳐야** 식는다.
"""

FORMING_TTL = 0.7
"""진행 중 봉을 거래소에 다시 묻기까지의 최소 간격(초).

🔴 **화면이 얼마나 자주 부르든 조회는 이 간격을 넘지 않는다.** 꼬리가 흔들리는 것을
보려면 초 단위로 봐야 하는데, 그 요청이 그대로 거래소로 나가면 레이트리밋에 걸리고
**판정용 조회까지 같이 막힌다** (`refresh()` 주석과 같은 사고다).

⚠️ 0.7 인 이유는 화면 폴링(1초)보다 **조금 짧아야** 매 폴링이 새 값을 받기 때문이다.
같거나 길면 두 번에 한 번씩 같은 값이 와서 화면이 멈춘 것처럼 보인다.
"""

PNL_DRIFT = Decimal("0.30")
"""원장과 거래소의 실현 손익 차이 허용치 (T20 ①).

⚠️ **넉넉하다.** 슬리피지·펀딩비·부분 체결이 정당하게 갈리게 만들고, 그것을 매번
경보로 올리면 사람이 경보를 안 믿게 된다.

🔴 **엄한 것은 크기가 아니라 부호다** (`pnl_sign_split`). *"둘 다 이겼는데 조금
다르다"* 와 *"한쪽은 이기고 한쪽은 졌다"* 는 전혀 다른 사건이고, 2026-08-19 결함은
후자였다 — 문턱으로는 초반 몇 건을 놓쳤을 것이다.
"""

WALLET_DRIFT = Decimal("0.05")
"""원장과 거래소 잔액이 이만큼 벌어지면 경보 (T14-2 · 5%).

⚠️ 0 으로 두면 늘 울린다 — 미실현 손익·펀딩비·반올림 때문에 완전히 같을 수 없다.
⛔ 성과를 보고 조정하지 않는다. 이것은 *"원장이 사실과 갈렸나"* 를 묻는 값이다.
"""

LIQ_LOOKBACK = 20
"""청산 흔적을 찾을 때 훑는 자금 원장 줄 수 (T14-2).

⚠️ 넓게 잡으면 **옛 청산**을 이번 것으로 오인한다. 한 판에서 청산은 드물고, 나면 그
직후에 조회하므로 최근 몇 줄이면 충분하다.
"""

LIQ_MARKS = ("liq", "adl", "강제")
"""청산으로 읽는 문구 조각들.

⛔ Gate 는 청산 전용 `type` 을 주지 않는다 — 강제청산도 `pnl` 로 들어오고 구별은
`text` 에 남는다. 문구가 바뀌면 못 잡지만, 그때는 **손절로 세어진다** — 그 방향이
덜 거짓말한다 (`_was_liquidated` 참고).
"""

STALL_BARS = 2
"""진입 축 봉이 이만큼 마감됐는데 판정이 안 늘면 **정지**로 본다 (T15-4).

⚠️ 1 이면 오경보가 난다 — 방금 마감된 봉을 아직 판정하기 전인 순간이 늘 있다.
⛔ 성과를 보고 조정하지 않는다. 이것은 판정 자체가 도는지 묻는 값이다.
"""

TRIGGER_TICK = 1.0
"""방아쇠 축을 다시 묻는 주기(초) — T17.

⭐ `refresh` 의 TTL 이 간격의 1/10 이라 10초봉은 1초에 한 번까지 실제로 조회된다.
그보다 자주 물어도 캐시에 막혀 새 봉이 없다.

⛔ 진입 축과 방아쇠 축이 같으면(0.1) 이 루프는 아예 돌지 않는다.
"""

PROBE_TICK = 60.0
"""점검(reconcile·감사) 배경 주기(초) — 30→60 (§1 레이트리밋 · 2026-09-01).

🔴 러너 6종이 30초마다 reconcile+감사로 `positionRisk`·`openAlgoOrders` 등을 때려
BINANCE 한도를 넘었다(실측 141%, `pnl_audit` 스로틀 뒤에도 피크 119%). 레이트리미터는
**진단용**이라 요청을 안 막으므로(러너가 못 붙는 사고를 막으려 그렇게 뒀다), 초과는
곧 IP 밴이다 — **덜 부르는 것이 유일한 방어**다.

⚠️ 대가: 거래소-우선 청산·무방비 탐지 지연이 30→60초. 그러나 **손절 재장착은 매 걸음
별도로 돌고**(진입축), 브로커측 조건부 손절이 그 사이 포지션을 지킨다 — 이 배경 점검은
백스톱이지 1차 방어가 아니다. 60초 백스톱은 안전 여유 안이다.
"""
FUNDING_SYNC_INTERVAL = 300.0
"""거래소 자금 원장에서 펀딩 정산을 읽어 열린 매매에 붙이는 주기 (T226). 정산은 8시간마다다."""

PNL_AUDIT_INTERVAL = 300.0
"""회계 대조(`_pnl_audit`)의 최소 간격(초) — **레이트리밋 방어** (2026-09-01).

🔴 `_pnl_audit` 은 `position_closes`(BINANCE `allOrders`+`income`, 가중치 큼)를 부른다.
걸음·30초 점검마다 부르면 6종 러너가 BINANCE 한도(2,400/분)를 **141% 로 초과**했다
(실측). 라이브면 IP 밴이고, 밴 중에는 손절·청산이 못 나간다.

⇒ 회계 갈림은 **느리게 움직이므로** 5분마다면 충분하다. 손절·포지션 안전 점검
(positionRisk·openAlgoOrders)은 그대로 30초에 둔다 — 그건 무겁지 않고 안전에 직결된다.
"""
"""자동 점검 간격(초).

⚠️ 거래소에 REST 를 따로 때리는 일이다. 봉이 흐르는지 보려고 **봉보다 자주** 물을
이유가 없다.
"""

BOOK_TICK = 300.0
"""호가창을 다시 보는 주기(초) — 사용자 제안 2026-08-20.

🔴 유동성은 **들어갈 때 한 번만** 봤다. 사용자 지적: *"들고 있는 동안 호가가 마르는
경우"*. SPCX 가 그 상태로 끝났다 — 증거금 420 이 15시간 묶였다.

⚠️ **걸음마다 재지 않는다.** 걸음은 1초에 한 번 도는데 그때마다 호가창을 부르면 판
하나당 하루 86,400번이고 판이 셋이면 26만이다. Gate 한도에 걸린다.

⭐ 5분이면 판당 하루 288번이다. 호가가 마르는 것은 초 단위 사건이 아니라 **분 단위**로
진행되므로, 이 주기로 놓치는 것은 없다.
"""

RECONNECT_WAIT = 3.0
"""스트림이 끊긴 뒤 다시 붙기까지 기다리는 초.

⚠️ 곧바로 다시 붙지 않는 이유 — 거래소가 막고 있는 경우 초당 재연결은 레이트리밋을
부르고, 그러면 **조회까지 같이 막힌다.**
"""

SEED_BARS = 800
"""시드로 받아 올 봉 수.

🔴 지표가 데워지려면 과거가 필요하다. 웹소켓만 쓰면 15m 에서 며칠을 기다려야 하고,
그동안 판정이 **불완전한 지표로** 돈다 — 그게 라이브 첫날 성적을 못 믿게 만든다.

⚠️ 세션이 보는 창과 같아야 한다. 적게 주면 화면·셋업이 다른 레벨을 보고, 이 프로젝트가
   그 사고를 세 번 겪었다 (화면 200봉 vs 셋업 800봉).
"""


@runtime_checkable
class StopAware(Protocol):
    """조건부 주문을 말할 수 있는 어댑터."""

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        """걸려 있는 조건부 주문들.

        Args:
            instrument: 종목.

        Returns:
            조건부 행들.
        """
        ...


@runtime_checkable
class LeverageAware(Protocol):
    """배율을 바꿀 수 있는 어댑터 (2026-08-19).

    Note:
        🔴 **거래소와 원장이 같은 배율을 봐야 한다.** 원장만 바꾸면 우리가 계산한
        계약수와 거래소가 잡는 증거금이 어긋난다.
    """

    async def set_leverage(self, instrument: Instrument, leverage: Decimal) -> None:
        """그 계약의 격리 마진 배율.

        Args:
            instrument: 종목.
            leverage: 배율.
        """
        ...


@runtime_checkable
class MarginAware(Protocol):
    """계정 전체가 잡고 있는 증거금을 읽을 수 있는 어댑터 (2026-08-19).

    Note:
        🔴 **`available` 에서 빠진 돈은 잃은 돈이 아니다.** 포지션이 열리면 거래소가
        증거금을 떼어 붙일 뿐이고, 그것을 손실로 세면 감사가 거짓 경보를 낸다.
    """

    async def account_margin(self) -> Decimal:
        """모든 포지션이 잡고 있는 증거금의 합.

        Returns:
            증거금 합. 없으면 0.
        """
        ...


@runtime_checkable
class BookAware(Protocol):
    """자금 변동 원장을 읽을 수 있는 어댑터 (T14-2).

    Note:
        🔴 **청산과 우리 손절 체결을 가르는 유일한 근거다.** "포지션이 사라졌다" 만
        보면 둘이 똑같이 보이고, 구별하지 못하면 정상 손절을 `강제청산`(-100%)으로
        세어 성적이 통째로 망가진다.
    """

    async def account_book(self, limit: int = 30) -> list[dict[str, str]]:
        """최근 자금 변동.

        Args:
            limit: 최근 몇 건.

        Returns:
            변동 행들.
        """
        ...


@runtime_checkable
class OrdersAware(Protocol):
    """미결 지정가 주문을 말할 수 있는 어댑터.

    Note:
        ⭐ 포지션을 이어받을 때 **계획을 여기서 되읽는다** — 우리가 걸어 둔 익절 두
        다리가 1차·목표를 그대로 들고 있다.
    """

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """걸려 있는 미결 지정가 주문들.

        Args:
            instrument: 종목.

        Returns:
            미결 주문 행들.
        """
        ...


# 🔴 **원장이 자기 불변식을 안다** (2026-08-19 사고 ⑦). 여기 있던 시절에는 세션이
#    부를 수 없었고(러너가 세션을 import 한다), 그래서 검사가 **기록을 만든 뒤**에
#    돌았다 — 주문만 막고 **유령 보유 기록**이 남았다.
geometry_fault = plan_fault


class StopBlownError(RuntimeError):
    """손절선을 **이미 지났다** — 못 건 것이 아니다 (2026-08-20).

    Note:
        🔴 Gate 가 `TRIGGER_PRICE_{LESS,GREATE}_LAST` 를 두 번(원값·1틱 완화) 냈다는 것은
        발동가가 현재가의 반대쪽이라는 뜻이고, 그 말은 **손절이 발동했다**는 것이다.

        ⛔ 실패로 세면 3회를 기다리게 되고, 그 동안 포지션은 조건부 없이 남는다.
    """


def _on_tick(price: Decimal, tick: Decimal, *, long: bool) -> Decimal:
    """발동가를 계약 **호가 눈금**에 맞춘다 — 느슨한 쪽으로.

    Args:
        price: 원장이 정한 손절가.
        tick: 계약의 최소 호가 단위.
        long: 보유가 롱인가.

    Returns:
        눈금의 배수인 가격.

    Note:
        🔴 **어긋나면 그 손절은 영영 안 걸린다** (실측 18건). 원장의 손절가는 봉 가격에서
        나오므로 계약 눈금의 배수라는 보장이 없다.

        ⚠️ **느슨한 쪽으로 간다** (롱은 내림·숏은 올림). 촘촘한 쪽으로 반올림하면 계획보다
        이른 손절이 되고, 그것은 원장이 정한 값을 집행이 바꾸는 일이다 (절대 규칙 #4).
    """
    if tick <= 0:
        return price
    steps = (price / tick).to_integral_value(rounding=ROUND_FLOOR if long else ROUND_CEILING)
    return steps * tick


TRIGGER_REJECTED = ("TRIGGER_PRICE",)
"""조건부 발동가가 **현재가의 잘못된 쪽**이라 거절당했을 때 Gate 가 쓰는 표딱지.

🔴 **본절에서 반드시 만난다** (2026-08-20). 반익이 발동하면 손절이 진입가로 올라가고
(`손절 == 진입`), 가격이 그 자리로 돌아오는 순간 발동가와 현재가가 **같아진다**:

```
숏 손절은 rule 1 (>=) — 발동가가 현재가보다 커야 받는다
진입 68508.1 · 본절 손절 68508.1 · 현재가 68508.1  →  400 거절
```

⇒ 세 번 연속 거절되면 `panic_close` 가 시장가로 던진다. 실제로 40초마다 그 고리가
  돌았다 — 손절이 **없어서**가 아니라 **정확히 본절이라서** 못 걸린 것이다.

⚠️ 표딱지 전체가 아니라 조각으로 본다 — Gate 가 `LESS_LAST`·`GREATE_LAST`(오타 그대로)·
`INVALID_PARAM_TRIGGER_PRICE` 를 섞어 쓰고, 그 목록은 우리가 못 고정한다.
"""


@runtime_checkable
class PositionAware(Protocol):
    """거래소 포지션을 말할 수 있는 어댑터.

    Note:
        🔴 `BrokerAdapter` 본체에 넣지 않는다 — 조회 전용 어댑터(업비트)까지 포지션을
        알아야 하게 되고, 그것은 없는 개념이다. **말할 수 있는 것만** 이 좁은 약속을
        따르고, 러너는 `isinstance` 로 확인한 뒤 묻는다.
    """

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        """포지션 스냅샷.

        Args:
            instrument: 종목.

        Returns:
            거래소가 말하는 포지션. 없으면 빈 사전.
        """
        ...


@runtime_checkable
class PositionLister(Protocol):
    """계정에 **무엇이 열려 있는지** 통째로 말할 수 있는 어댑터.

    Note:
        🔴 `PositionAware` 와 **따로 둔다.** 하나로 합치면 `position_snapshot` 만 가진
        어댑터가 `isinstance` 에서 탈락해 이어받기가 조용히 죽는다 — 프로토콜을 넓히면
        기존 구현이 말없이 빠지는 것이 `runtime_checkable` 의 성질이다.

        묻는 방식이 반대라 쓰임도 다르다: 저쪽은 *"이 종목의 포지션"*(러너가 자기
        종목을 볼 때), 이쪽은 *"무엇이 열려 있나"*(콘솔이 **고아 포지션**을 찾을 때).
    """

    async def open_positions(self) -> list[dict[str, str]]:
        """열려 있는 포지션 전부 (`size` 0 은 뺀다).

        Returns:
            각 행에 `symbol` 이 있는 포지션 행들.
        """
        ...


def adopted_id(orders: Sequence[dict[str, str]]) -> str:
    """이어받을 매매의 id — 우리가 걸어 둔 주문 이름에서 되뽑는다.

    Args:
        orders: 거래소 미결 주문들.

    Returns:
        주문 이름에 박힌 매매 id. 못 찾으면 새 id.

    Note:
        ⭐ 되뽑으면 **저널·화면의 매매 id 가 재시작 전과 같아진다.** 새로 뽑으면 같은
        포지션이 두 이름으로 남아 나중에 이력을 못 잇는다.

        🔴 **두 형식을 다 받는다** (T18 ⑤ 전환기). 이름 규격을 바꾸기 전에 나간 주문이
        아직 거래소에 떠 있을 수 있고, 그것을 못 읽으면 **이어받기가 통째로 실패해서
        포지션이 고아가 된다** — 규격 변경이 그 사고를 만들면 안 된다.

        ```
        옛   t-<trade 12>-take_profit-1
        새   t-<run 6>-<trade 8>-tp1
        ```

        ⚠️ 가르는 법은 **토막 수**다. 새 형식은 `-` 로 넷, 옛 형식은 넷이지만 두 번째
        토막이 12자다. 그래서 두 번째가 `RUN_CHARS` 자면 판 표식으로 본다.
    """
    for row in orders:
        found = trade_of_text(str(row.get("text", "")))
        if found:
            return found
    return new_trade_id()


def trade_of_text(text: str) -> str:
    """주문 이름에서 **매매 id** 를 되뽑는다 (T18 ⑤).

    Args:
        text: 거래소 주문의 `text`.

    Returns:
        매매 id — **앞자리만일 수 있다.** 우리 이름이 아니면 빈 문자열.

    Note:
        🔴 **주문 이름은 30자 제한이 있다** (`TEXT_LIMIT`). 판 표식이 붙는 새 형식에서는
        매매 id 가 **8자로 잘려** 들어간다:

        ```
        t-72bb3b4690f3-cl-0      옛 형식 — 판 표식 없음 · 매매 id 12자
        t-82e456-72bb3b46-cl-1   새 형식 — 판 표식 6자 + 매매 id 8자
        ```

        ⇒ 원장과 맞출 때는 **앞자리로** 맞춰야 한다. 완전 일치로 찾으면 새 형식이
        전부 안 맞는다 (`RunStore.trace`).

        ⛔ **`ao-` 는 못 읽는다.** 조건부가 발동해 Gate 가 만든 주문이라 이름이 우리
        것이 아니다 — 어느 방법으로도 이름으로는 못 잇는다. 그런 주문은 **"판 미상"**
        으로 남기고 아무 판에나 붙이지 않는다.
    """
    if not text.startswith("t-"):
        return ""
    parts = text.split("-")
    if len(parts) < 3 or not parts[1]:
        return ""
    if len(parts) >= 4 and len(parts[1]) == RUN_CHARS and parts[2]:
        # 새 형식: t-<run 6>-<trade 8>-tp1
        return parts[2]
    return parts[1]


def fee_from_close(row: dict[str, object], multiplier: Decimal) -> tuple[Decimal, Decimal] | None:
    """거래소 청산 행(`position_close`)에서 **실제 수수료와 그 비율**을 읽는다 (T236).

    Args:
        row: Gate `position_close` 한 줄 — `pnl_fee`(음수 = 냈다) · `max_size` ·
            `long_price`/`short_price` · `side`.
        multiplier: 계약 승수(`quanto_multiplier`).

    Returns:
        `(수수료 USDT · 양수, 진입 명목 대비 비율)`. 필드가 없거나 명목이 0 이면 None —
        지어내지 않는다.

    Note:
        비율의 분모는 **최대 보유 계약 x 평균 진입가 x 승수** — 원장 `cost_pct` 가 "명목 대비
        왕복 비용" 이라 같은 잣대다. 반익 뒤 남은 절반은 원장이 `filled_ratio` 로 이미 줄여
        세므로 여기서 다시 나누지 않는다.
    """
    try:
        fee = abs(Decimal(str(row.get("pnl_fee"))))
        size = abs(Decimal(str(row.get("max_size"))))
        side = str(row.get("side") or "")
        price = Decimal(str(row.get("short_price" if side == "short" else "long_price")))
    except Exception:
        return None
    notional = size * price * multiplier
    if notional <= 0:
        return None
    return fee, fee / notional


def attribute_closes(
    closes: Sequence[dict[str, str]],
    mine_tag: str,
    intervals: Sequence[tuple[float, float]],
    since: float = 0.0,
) -> tuple[Decimal, int, int]:
    """거래소 청산 손익을 **내 것만** 골라 합한다 — 태그 + 소유권 창 (벽돌 3·4 · 2026-09-01).

    Args:
        closes: 거래소 청산 이력 (`{time, pnl, text, ...}`).
        mine_tag: 내 판 표식(`run_tag`). 비면(옛 형식) 태그 대신 소유권 창만으로 가른다.
        intervals: 내가 **그 계약을 들고 있던 시간창** 목록 `(시작 epoch, 끝 epoch)`.
            열린 포지션은 끝이 `+inf`. 선물은 계약당 포지션이 하나라(시스템이 강제),
            이 창 안의 청산은 태그가 없어도(강제청산 `ao-`) **내 것**이다.
        since: 재정렬(resync) 워터마크 epoch. 이 시각 이전 청산은 태그·창과 무관하게
            **전부 무시**한다 (복구 불가한 과거를 버린다). 0 이면 워터마크 없음.

    Returns:
        `(내 실현손익 합, 내 청산 건수, 그중 무태그(강제청산·수동청산) 건수)`.

    Note:
        🔴 **한 계정에서 여러 전략을 돌려도 내 것만 센다 — 서브계정 불필요** (2026-09-01
        사용자 지적). 재사용된 계정엔 여러 런 태그 + `ao-` 강제청산 + 수동정리가 며칠치
        쌓인다. 예전 시각 컷은 이 전부를 합쳐 가짜 `pnl_sign_split` 을 냈다.

        ⭐ **귀속 규칙 두 가지**:
        - 주문 이름에 **내 태그**가 박혔으면 → 언제든 내 것 (태그가 증명한다).
        - 태그가 없으면(`ao-` 강제청산·수동청산) → **내 소유권 창 안이면** 내 것.
          창 밖(내가 그 계약을 들기 전)은 이전 소유자의 잔재라 뺀다.

        ⚠️ 내가 강제청산됐는데 원장이 그걸 놓쳤으면, 이제 `theirs` 에 그 손실이 잡혀
        원장(`mine`)과 갈린다 — **그게 맞다**. 감사가 "너 청산됐는데 원장은 모른다" 를
        정확히 짚는다 (예전엔 태그 없다고 빼서 놓쳤다).
    """

    def owned(row: dict[str, str]) -> tuple[bool, bool]:
        """이 주문·조건부가 내 판의 것인가.

        Args:
            row: 거래소 행 (`text` 에 판 표식이 있을 수 있다).

        Returns:
            `(내 것인가, 무태그인가)`. 태그가 있으면 태그가 정하고, 없으면 소유권 창이 정한다.
        """
        tag = run_of_text(str(row.get("text", "")))
        if mine_tag and tag:
            return tag == mine_tag, False  # 태그가 정한다
        # 무태그(또는 내 태그를 모름) — 소유권 창이 정한다
        try:
            ts = float(row.get("time") or "nan")
        except (TypeError, ValueError):
            return False, True
        return any(lo <= ts < hi for lo, hi in intervals), True

    theirs = Decimal(0)
    counted = liq = 0
    for row in closes:
        if since > 0:
            try:
                if float(row.get("time") or "0") < since:
                    continue  # 재정렬 이전 — 버린 과거
            except (TypeError, ValueError):
                continue
        mine, untagged = owned(row)
        if not mine:
            continue
        if untagged:
            liq += 1  # 무태그인데 내 창 안 = 내 강제청산/수동청산
        with contextlib.suppress(Exception):
            theirs += Decimal(str(row.get("pnl", "0") or "0"))
            counted += 1
    return theirs, counted, liq


def run_of_text(text: str) -> str:
    """주문 이름에서 **판 표식** 을 되뽑는다 (T18 ⑤).

    Args:
        text: 거래소 주문의 `text`.

    Returns:
        판 표식 6자. 옛 형식이거나 우리 이름이 아니면 빈 문자열.

    Note:
        🔴 **거래소 콘솔이 이것으로 판을 가른다** (사용자 요구 2026-08-19). 빈 값은
        *"판 미상"* 이고, 그것을 아무 판에나 붙이지 않는다 — 붙이면 남의 판 성적에
        남의 주문이 섞인다.
    """
    if not text.startswith("t-"):
        return ""
    parts = text.split("-")
    if len(parts) >= 4 and len(parts[1]) == RUN_CHARS and parts[2]:
        return parts[1]
    return ""


class LiveRunner:
    """웹소켓 봉으로 세션을 걸어간다.

    Note:
        ⚠️ **한 종목·한 진입 시간축**이다. 여러 종목은 러너를 여러 개 띄운다 — 하나에
        묶으면 한 종목의 봉 지연이 다른 종목 판정을 늦춘다.
    """

    _last_pnl_at: float = 0.0
    """마지막 회계 대조 시각 (monotonic) — `PNL_AUDIT_INTERVAL` 스로틀용."""
    _pnl_findings: list[dict[str, str]] | None = None
    """마지막 회계 대조 결과 — 스로틀 중에는 이 캐시를 돌려준다 (accounting_ok 유지)."""

    _adopt_refused: str | None = None
    """입양을 거부한 이유 — 감사가 이것을 **끈적한 발견**으로 올린다 (2026-08-25).

    GT ADA 고아(원장-무소유 116계약 · 손절 없음)가 화면 어디에도 안 보였다:
    `ledger_mismatch` 는 2연속 감사를 요구하는데 재시작이 streak 를 리셋했고, 원장
    기준으로 그리는 화면(펀드 표·RUN 목록)은 원장이 모르는 포지션을 정확히 못 그린다.
    거부 순간이 곧 확진이므로 streak 없이 즉시, 상태가 풀릴 때까지 계속 올린다.
    """

    _revived_open: frozenset[str] = frozenset()
    """클래스 기본값 —  로 조립하는 테스트 더블이 옛 경로를 그대로 타게 한다.
    실제 러너는 생성자가 원장을 보고 인스턴스 값으로 덮는다."""

    _log: structlog.stdlib.BoundLogger = _logger
    """클래스 기본값 — **생성자를 우회해 조립하는 테스트 더블**도 로그를 남긴다.

    실제 러너는 생성자가 판·종목·거래소를 묶은 로거로 덮고, `identify()` 가 판
    식별자까지 붙인다 (T76). 여기 기본값이 없으면 더블이 로그 한 줄에 터진다 —
    로깅이 실패해서 판정이 죽는 것은 본말전도다.
    """

    def __init__(
        self,
        session: Session,
        feed: LiveFeed,
        stream: CandleStream,
        quotes: QuoteAdapter,
        orders: BrokerAdapter,
    ) -> None:
        """러너를 만든다.

        Args:
            session: 판단·원장. **과거와 같은 세션 클래스**다.
            feed: 라이브 급전. `session.feed` 와 **같은 객체**여야 한다.
            stream: 웹소켓 캔들 스트림.
            quotes: 공개 조회 어댑터 — 봉 구멍을 메울 때 쓴다.
            orders: 주문 어댑터. **`OrderGateway` 가 준 것**이어야 한다 (절대 규칙 #0).

        Raises:
            ValueError: `session.feed` 가 `feed` 와 다른 객체인 경우.

        Note:
            🔴 **같은 객체인지 확인한다.** 다르면 러너가 밀어 넣은 봉을 세션이 못 보고,
            세션은 영원히 시드만 보며 "새 봉이 없다"고 판단한다 — 예외 없이 아무 일도
            일어나지 않는 종류의 사고다.
        """
        if session.feed is not feed:
            raise ValueError(
                "session.feed 와 러너의 feed 가 다른 객체다 — 러너가 밀어 넣은 봉을 "
                "세션이 못 본다. 예외가 안 나고 아무 일도 안 일어난다"
            )
        self._session = session
        self._feed = feed
        self._stream = stream
        self._quotes = quotes
        self._orders = orders
        self.run_key = ""
        """판 식별자 (`live7c37a84d`) — API 가 러너를 만든 뒤 `identify()` 로 넣어 준다.

        🔴 **로그에 어느 판인지가 없으면 11개 중 누구의 오류인지 모른다**
        (사용자 지적 2026-08-28). `trace_id` 는 전 세션이 같아 구분에 못 쓴다.
        """
        self._log: structlog.stdlib.BoundLogger = _logger.bind(  # pyright: ignore[reportAttributeAccessIssue]
            market=session.instrument.market.value,
            symbol=session.instrument.symbol,
            book=session.playbook.attribution,
        )
        """이 러너의 로그 — 판을 가리키는 값이 **모든 줄에** 묶여 나간다.

        ⚠️ `run` 은 `identify()` 뒤에야 붙는다 (생성 시점에는 API 가 아직 안 정했다).
        """
        self.steps = 0
        """판정한 봉 수."""
        self.backfilled = 0
        """REST 로 메운 봉 수 — **0 이 아니면 웹소켓이 끊겼던 것**이다."""
        self.orders = 0
        """보낸 주문 수."""
        self.last_probe: dict[str, object] | None = None
        """마지막 자동 점검 결과. 화면이 **누르기 전에도** 답을 볼 수 있게 한다."""
        self.findings: list[dict[str, str]] = []
        # 🔴 **보기용 축은 누가 볼 때만 데운다** (2026-08-29 요율 한도 사고).
        #    축 → 마지막으로 화면이 물어본 시각(monotonic).
        self._watched: dict[Timeframe, float] = {}
        """마지막 감사에서 나온 이상들. 화면이 그대로 보여 준다.

        🔴 비어 있는 것이 정상이다 — 하나라도 있으면 **판정을 믿으면 안 된다.**
        """
        self._ladder_pending: dict[str, int] = {}
        self.short_by: str | None = None
        """예산 합이 계좌를 **얼마나 넘었나** (사용자 신고 2026-08-21). 넉넉하면 None.

        ⭐ 막기만 하면 사람이 얼마를 줄여야 할지 모른다 — 예산 문(`sizing_of`)이 필요
        금액을 말하는 것과 같은 이유다.
        """
        self.dry: dict[str, str] | None = None
        """호가가 **말랐을 때** 그 사실 (사용자 제안 2026-08-20). 멀쩡하면 None.

        ⭐ 화면이 이 값으로 배너를 그리고, 나갈 값을 사람에게 계산해 보여 준다 —
        기계가 정할 값이 아니다 (실측: 던졌으면 -420, 기다렸더니 -74).
        """
        self._stepping = asyncio.Lock()
        """걸음이 **겹쳐 돌지 못하게** (사용자 신고 2026-08-21).

        🔴 `_one_step` 은 두 곳에서 불린다 — 방아쇠(1초마다)와 스트림(봉마다). 안에서
        네트워크를 여러 번 기다리므로 한쪽이 도는 중에 다른 쪽이 들어왔고, 우편함의
        대기 목록을 **둘이 같이 읽어** 사다리가 두 번 나갔다.

        ⚠️ 실측: 진입 다리 114개 중 **10개**가 겹쳤고, 그중 넷은 간격이 **0~1ms** 였다.
        """
        self._arming = asyncio.Lock()
        """손절 무장이 **겹쳐 돌지 못하게** (2026-08-31).

        🔴 `_guard_stop` 도 이제 두 곳에서 불린다 — 걸음과 점검 루프(30초). `stops_for`
        는 *목록 → 취소 → 등록* 이라 둘이 겹치면 **둘 다 옛 손절을 보고 둘 다 등록**해
        조건부가 두 개 남거나, 취소와 등록 사이의 빈틈이 겹쳐 늘어난다.

        ⚠️ **기다리지 않고 건너뛴다** (`_stepping` 과 같은 규칙). 무장은 30초 뒤에
        어차피 다시 확인하고, 줄을 세우면 옛 값으로 뒤늦게 거는 일이 생긴다.
        """
        self.swept: list[str] = []
        """판을 띄우며 **치운 잔재** — 조용히 지우지 않기 위한 기록 (2026-08-21).

        🔴 주인 없는 조건부는 새 포지션을 통째로 닫으므로 시작할 때 묻지 않고 거둔다.
        그러면 사람이 나중에 *"내 손절 어디 갔지"* 라고 물을 수 있으므로, **무엇을
        치웠는지**가 판 상세에 남아야 한다 (절대 규칙 #8).
        """

        self.overlaps = 0
        """겹쳐 들어와 **건너뛴** 걸음 수 (§1-0s).

        ⭐ 0 이 아닌 것 자체는 정상이다 — 두 루프가 같은 함수를 쓰는 설계의 결과다.
        급증하면 **걸음이 너무 오래 걸린다**는 뜻이므로 그때 봐야 한다.
        """
        self._stop_id: str = ""
        """지금 걸려 있는 **조건부 주문 id** (사용자 신고 2026-08-20).

        🔴 조건부가 발동하면 Gate 가 `ao-{id}` 라는 이름으로 주문을 만든다. 그 이름에는
        **우리 매매 id 가 없어서**, 이 값을 안 적어 두면 콘솔이 손절 체결을 매매에
        영영 못 잇는다 — 실측: 화면이 *"RUN 미상 · 배율 — · 수익률 —"* 로 떴다.

        ⭐ **추정이 아니라 정확한 열쇠다.** 시각·종목으로 맞추는 방법도 있지만 그것은
        틀릴 여지가 있고, id 는 하나뿐이다.
        """
        self._armed_for = ""
        """**손절을 걸어 본 매매** — 아직 안 걸어 본 매매를 무방비로 외치지 않는다.

        🔴 사용자 신고 2026-08-20: *"실제 손절 주문이 발행되기 전에 바로 뜨네. 그래서
        얼럿 알림이 먼저 와."* 맞다. 감사는 걸음과 **따로 도는 타이머**(`_keep_probing`)
        에서도 돌아서, 체결과 `_arm` 사이에 끼어들면 *"포지션이 있는데 조건부가 0건"*
        을 본다 — **사실이지만 정상적인 무장 중**이다.

        ⇒ *"걸 기회가 있었는데도 없다"* 만 무방비로 센다. `_guard_stop` 이 이 매매에
          대해 한 번이라도 돌면 여기에 id 가 박히고, 그 뒤의 0건은 진짜다.

        ⚠️ **시계로 재지 않는다.** *"몇 초 지났으면"* 은 근거 없는 상수이고, 느린 날에는
        거짓 경보가 그대로 난다. 기준은 시간이 아니라 **기회**다.
        """
        self.observe_only = False
        """**주문을 한 건도 안 낸다** — 판정만 돌린다 (2026-08-19 · 토큰화 주식).

        🔴 **조회 거래소와 주문 거래소가 다른 데서 생긴 상태다.** SNDK·SKHY 는 라이브
        API 에 있고 testnet 에는 없다 — 봉도 오고 박스도 서고 방아쇠도 켜지는데
        주문만 못 낸다.

        ⇒ 막는 대신 **판정을 돌려 보고 주문만 뺀다.** 그 자리가 어떻게 생겼는지는
        볼 수 있고, 못 보는 것은 체결·슬리피지·손절 발동이다.

        ⛔ **이 판의 성적을 실주문 판과 한 표에 올리지 않는다.** 여기 손익은 **원장의
        모형**이고, 체결 실패도 슬리피지도 없다 — 2026-08-19 에 거짓말한 것이 정확히
        그 모형이다. 나란히 놓으면 *"안 낸 주문이 잘 됐다"* 가 성적이 된다.
        """

        self._stopped_at: dict[str, Decimal] = {}
        """손절을 건 시점의 **채워진 비중** — 늘면 다시 건다 (T19).

        🔴 다리1 만 덮는 손절은 **조용히** 절반을 무방비로 둔다. 포지션도 있고 손절도
        있어서 화면상 정상으로 보인다.
        """

        self._halved: set[str] = set()
        """신호 반익을 **거래소에서도** 처리한 매매들 (2026-08-19 사고 ③-b).

        🔴 **원장만 반익하고 있었다.** 전환 신호가 뜨면 원장은 절반을 덜었다고 적는데,
        거래소에는 아무 주문도 안 나가고 1차 익절 지정가가 계획가에 그대로 걸려 있었다 —
        가격은 거기 가지 않으므로 **포지션은 전량 그대로**였다.

        ⚠️ 한 번만 한다. 걸음마다 물으면 같은 반익으로 계속 던다.
        """
        """익절을 **못 건** 매매들 → 체결 수량. 매 걸음 다시 시도한다.

        🔴 안 남기면 영영 안 걸린다 — `_sent` 가 이미 보냈다고 기억하기 때문이다.
        """
        self.stop_misses = 0
        """손절을 **연속으로** 못 건 횟수. `STOP_GUARD_LIMIT` 에 닿으면 던진다.

        ⚠️ 한 번이라도 성공하면 0 으로 돌아간다 — 누적이 아니라 **연속**이다.
        """
        self._restarts = 0
        """스트림이 끝나 **러너가 새로 붙은** 횟수 (스트림 내부 재연결과 별개다)."""
        self.failures = 0
        """판정이 터진 횟수. 🔴 0 이 아니면 그만큼의 봉이 **판정 없이 지나갔다**."""
        self.last_error = ""
        """마지막 실패 문구 — 화면이 그대로 보여 준다.

        🔴 사용자 요구 2026-08-18: *"주문이 안들어가는 오류 발생, 주문 실패 시 이걸
        사용자가 알 수 있어야 해."* 로그에만 있으면 아무도 안 본다.
        """
        self._refreshed: dict[Timeframe, float] = {}
        # ⭐ 진행 중 봉 캐시 — `{축: (조회 시각, 봉)}`. **급전과 별개로 둔다**:
        #    여기 값이 급전으로 새어 들어가면 판정이 미마감 봉을 보게 된다.
        self._forming: dict[Timeframe, tuple[float, Candle | None]] = {}
        """축마다 마지막으로 거래소에 물어본 시각 (`monotonic`).

        ⚠️ 벽시계가 아니라 단조 시계다 — 시스템 시각이 뒤로 가면 TTL 이 영원히 안 풀린다.
        """
        # 🔴 교정 원장(T185)의 잡음 거르개 — 조건부 손절은 걸음마다 재장착되므로
        #    같은 상태를 다시 적지 않는다. 프로세스가 살아 있는 동안만 유효하고,
        #    재기동하면 첫 걸음에 한 번 더 적힌다 (그 정도 중복은 분석에서 지운다).
        self._calibrated: dict[tuple[str, str], str] = {}
        self._funding_seen: str = ""
        self.placed: dict[str, dict[str, str]] = {}
        """매매 id → 거래소 주문의 흔적 `{order_id, status, contracts}`.

        🔴 **원장에 보유중인데 여기 없으면 유령 포지션이다** (사용자 발견 2026-08-18):

        > *"포지션 증거금에 돈이 안들어가 있는데, 어떻게 주문이 체결된거야???"*

        맞는 의심이었다. 거래소에 물어보니 포지션 0 · 증거금 0 · 미결 0 인데 화면은
        `보유중 롱 64,182` 였다. **원장은 판정만으로 보유를 적고**(`Session.step`),
        주문 전송은 러너가 **따로** 한다 — 전송이 실패해도 원장의 보유는 남는다.

        ⇒ 그래서 전송 결과를 매매별로 남긴다. 화면이 이 값을 나란히 보여 주면
          *"내쪽에서만 체결됐다"* 를 **눈으로** 잡을 수 있다 (§1-0s 관측 규약).

        ⛔ 이것으로 원장을 고치지 않는다 — 고치는 것은 T14 다. 여기서 원장을 되돌리면
          결정론 코어가 거래소 응답에 따라 달라진다 (절대 규칙 #5).
        """
        self._sent: set[str] = set()
        """이미 주문을 보낸 계획 id 들 — **중복 주문을 막는 마지막 방어선**이다.

        ⚠️ 멱등키가 서버 쪽 방어이고 이것이 우리 쪽이다. 둘 다 있어야 하는 이유는
        Gate 가 같은 `text` 를 거부하는지 확인되지 않았기 때문이다.
        """
        self._revived_open: frozenset[str] = frozenset(
            item.trade_id for item in session.ledger.records if item.outcome is Outcome.OPEN
        )
        """러너가 태어날 때 **이미 열려 있던** 매매 — 진입을 다시 보내면 안 된다 (2026-08-25).

        🔴 실측 사고: `_sent` 는 프로세스마다 빈 집합으로 시작하는데, 시장가로 들어간
        기록은 `entry_fills` 가 비어 있어 되살아난 판의 **첫 걸음마다 진입이 또 나갔다** —
        재시작이 잦았던 날 Gate BTC 가 원장 67계약 vs 거래소 333계약이 됐다.
        멱등키도 못 막는다: 재전송은 revision 이 같아도 **다른 프로세스**라 서버가
        같은 text 를 거부하지 않았다.

        ⇒ 여기 있는 매매는 `_place` 가 진입 대신 **보호만** 건다 (`_protect`).
          손절·익절을 다시 거는 길은 그대로 산다 — 막는 것은 진입 다리 하나다.
        """
        # 🔴 **회계 대조 위상 스태거** (§1 · 2026-09-01) — 6종 러너가 동시에 부팅하면
        #    첫 `pnl_audit` 가 한꺼번에 터져 `income`+`allOrders` 버스트가 난다. 첫
        #    대조를 종목 해시(0~90초)만큼 미뤄 부하를 퍼뜨린다. `_pnl_findings=[]` 로
        #    두면 그 사이 accounting_ok 는 참(=아직 안 갈림)으로 안전하게 유지된다.
        self._last_pnl_at = time.monotonic() - PNL_AUDIT_INTERVAL
        self._pnl_findings = []
        self._last_funding_at = 0.0
        self._funding_rows_seen: set[str] = set()
        self._resized_at: dict[str, int] = {}
        self._maker_exit: dict[str, tuple[int, int]] = {}
        """메이커 청산 대기표 — 매매 id → (건 봉 순번, 계약 수) (T126 · 1.1.0).

        🔴 **여기 든 것은 반드시 나가야 한다.** `_expire_maker_exit` 가 매 걸음 돌며
        `maker_exit_bars` 봉이 지나면 시장가로 마무리한다. 이 표가 비지 않으면
        청산 신호가 뜬 포지션이 조용히 남아 있다는 뜻이다.
        """
        self._ref_regime_at: datetime | None = None
        """기준(BTC) 레짐을 마지막으로 계산한 4h 마감봉 ts (T66-e F1 · 봉당 1회)."""
        """매매별 마지막 재레버 봉 순번 (0.8.0) — 같은 봉에 두 번 조정하지 않는다."""
        self._spec: dict[str, str] | None = None
        self._bars_at_step = 0
        """마지막 판정 때 진입 축 마감 봉이 몇 개였나 (T15-4).

        🔴 **`steps` 만으로는 정지를 못 잡는다.** 0 인 것이 *"아직 봉이 안 왔다"* 인지
        *"봉은 왔는데 판정이 안 돈다"* 인지 구별되지 않는데, 후자가 실제로 있었다
        (전진 축 불일치 — 예외도 로그도 안 났고 증상은 이 숫자뿐이었다).
        """

        self._run_key = ""
        """주문 이름에 넣는 판 표식 (T18 ⑤). 비면 옛 형식으로 나간다."""

        self.guards: dict[str, dict[str, str]] = {}
        """안전장치가 **실제로 발동한 기록** — `{이름: {횟수, 마지막, 무엇}}`.

        🔴 **안 도는 안전장치는 없는 것과 같다.** 이 프로젝트의 방어선은 전부 코드와
        테스트로만 증명돼 있고, 실전에서 한 번도 발동한 적이 없다:

        ```
        panic_close    손절을 3번 연속 못 걸면 시장가로 던진다
        geometry       이익이 날 수 없는 계획의 주문을 막는다
        adopt          판이 죽어도 거래소 포지션을 원장으로 되읽는다
        reconcile      거래소가 먼저 닫으면 30초 안에 원장도 닫는다
        margin_share   다른 판이 지갑을 쓰면 예산을 줄인다
        liquidation    강제청산을 손절과 갈라 센다
        ```

        ⛔ **"테스트가 통과했으니 된다" 로 넘어가지 않는다.** 오늘 나온 결함 넷 중
        단위 테스트가 잡은 것은 0개였다 — 전부 조립에서만 드러났다 (T15-5).

        ⚠️ **0 인 것이 나쁘다는 뜻은 아니다.** 발동할 일이 없었다는 뜻일 수도 있다.
        구별하는 것은 사람이며, 이 표는 그 판단의 재료다 (§1-0s 관측 규약).
        """

        self.leverage_ok = False
        """거래소 배율이 **원장과 같다고 확인됐나** (2026-08-20 사고 ⓔ).

        🔴 **이것이 거짓이면 계약 수 계산이 통째로 틀린다.** 원장이 20배로 명목을 잡고
        거래소가 3배로 증거금을 떼면 필요 증거금이 **6.7배**가 된다 — 실측:

        ```
        예산 138.14 x 다리 0.5 = 69.07  →  20배 명목 1381.4
        Gate 실제 배율 3       →  증거금 460.5
        거절: INSUFFICIENT_AVAILABLE margin 461.36 while available 24.39
        ```

        ⚠️ 그리고 **주문이 통과했다면 더 나빴다** — 원장은 20배 손익을 적는데 실제는
        3배라, 자가 점검의 `원장 +94.26 vs 거래소 -10.19` 가 그 모습이다.
        """

        self.peers: Callable[[], int] = lambda: 0
        """같은 계좌에서 **함께 도는 다른 판의 수** — API 가 꽂는다 (2026-08-20 ⓓ).

        🔴 **판마다 원장이 계좌 전체를 자기 것으로 여긴다** (`seed_cash` = 계좌 총액).
        그래서 판이 둘이면 둘 다 *"원장 981 vs 계정 895"* 라고 외치는데, 둘 다
        맞는 말이 아니다 — 나눌 근거가 없을 뿐이다.

        ⚠️ 값이 아니라 **함수**다. 판은 러너가 도는 동안에도 뜨고 죽으므로, 시작할 때
        센 숫자를 들고 있으면 곧 낡는다.
        """

        self.account_modelled: Callable[[], Decimal | None] = lambda: None
        """같은 계좌 판들의 **원장 평가액 합** — API 가 꽂는다 (2026-08-30).

        🔴 이것이 `wallet_unattributable` 을 **살아 있는 검사로 되돌린다.** 판이 여럿일
        때 감사는 *"가를 수 없다"* 며 손을 놓았는데, 그 사이 원장-사실 대조가 **몇 달째
        한 번도 안 돌았다** — 경고가 매 걸음 떠서 아무도 안 보는 상태였다.

        가를 수 없는 것은 **판 하나의 몫**이지 **합계**가 아니다. 판마다 예산
        (`margin_budget`)이 따로 있으므로 합은 계정 총액과 맞아야 한다:

            계정 총액(쓸 수 있는 돈 + 잡힌 증거금)  ==  Σ 판별 원장 평가액

        ⚠️ 합을 못 낼 때만(`None`) 예전처럼 "가를 수 없다" 로 물러선다.
        """

        self.squeezed: dict[str, str] | None = None
        """다른 판이 지갑을 쓰고 있어 **예산보다 적게** 주문한 흔적 (ㄷ).

        🔴 **화면이 이 사실을 말해야 한다.** 선착순은 규칙이지만, 말하지 않으면 사람은
        *"왜 주문이 작아졌지"* 를 전략 문제로 오해한다 (§1-0s 관측 규약).
        """

        self._store: RunStore | None = None
        """판 저장소 (T16 ②). 없으면 원장이 이 프로세스와 함께 죽는다."""

        self._run_id: uuid.UUID | None = None
        self._pending_json: str | None = None
        """마지막으로 저장한 대기 계획(JSON 문자열) — 같으면 다시 안 쓴다 (T218)."""

    def use_store(self, store: RunStore | None, run_id: uuid.UUID, key: str = "") -> None:
        """판 저장소를 붙인다 (T16 ②).

        Args:
            store: 저장소. None 이면 안 쓴다.
            run_id: 이 러너가 속한 판.
            key: 판의 짧은 id — **주문 이름에 들어가는 표식**이다 (T18 ⑤).

        Note:
            🔴 **생성자가 아니라 여기서 받는다.** 러너는 `orchestration/` 이고 저장소도
            같은 층이지만, 러너를 세우는 시점에는 아직 판이 안 열려 있다 — 판을 여는
            것은 API 의 일이고 그 결과가 `run_id` 다.
        """
        self._store = store
        self._run_id = run_id
        # ⭐ **주문 이름에 들어가는 표식** (T18 ⑤). DB id 가 아니라 사람이 쓰는 짧은
        #    id 다 — 거래소 콘솔에서 눈으로 판을 가르는 것이 목적이므로.
        self._run_key = key

    async def _persist_pending(self) -> None:
        """대기 중 진입 계획을 `wf_runs.meta_json` 에 남긴다 (T218).

        Note:
            🔴 세션의 `_waiting` 은 메모리에만 있었다 — 그래서 재시작하면 거래소에 걸린 진입
            지정가가 주인을 잃고 좀비로 거둬졌다. 걸음마다 스냅샷을 비교해 **바뀐 때만** 쓴다.

            ⚠️ 실패해도 걸음을 막지 않는다 (규칙 #8-1) — 경고만 남기고 다음 걸음에 다시 쓴다.
        """
        if self._store is None or self._run_id is None or self.observe_only:
            return
        filler = self._session.filler
        if not isinstance(filler, LiveFiller):
            return
        try:
            snap = pending_mod.snapshot(self._session, filler)
            payload = None if snap is None else pending_mod.to_json(snap)
            encoded: str | None = None if payload is None else json.dumps(payload, sort_keys=True)
            if encoded == self._pending_json:
                return
            await self._store.put_meta(self._run_id, pending_mod.META_KEY, payload)
            self._pending_json = encoded
        except Exception as exc:
            self._log.warning("live_pending_persist_failed", payload={"error": str(exc)[:160]})

    async def restore_pending(self, saved: pending_mod.PendingEntry) -> set[str]:
        """저장돼 있던 대기 계획을 **거래소 사실과 대조해** 이어받는다 (T218).

        Args:
            saved: `wf_runs.meta_json["pending_entry"]` 에서 꺼낸 계획.

        Returns:
            이어받은 거래소 주문 id 들 — 좀비 정리가 이것들을 건너뛴다.

        Note:
            표마다: 거래소에 아직 있으면 우편함에 심는다 · 보낸 적이 없으면 다시 부탁한다 ·
            그 사이 채워졌으면 체결로 넣는다(세션이 다음 걸음에 원장에 적는다) · 거래소에 없고
            체결도 아니면 버린다. 되살릴 것이 하나도 없으면 계획을 접고 저장도 지운다.
        """
        filler = self._session.filler
        if not isinstance(filler, LiveFiller):
            return set()
        rows: list[dict[str, str]] = []
        if isinstance(self._orders, OrdersAware):
            with contextlib.suppress(Exception):
                rows = await self._orders.open_orders(self.instrument)
        prices: dict[str, Decimal | None] = {}
        open_ids = {str(row.get("id", "")) for row in rows}
        for leg in saved.tickets:
            if leg.order_id is not None and leg.order_id not in open_ids:
                with contextlib.suppress(Exception):
                    prices[leg.order_id] = await self._fill_price(leg.order_id)
        plan = pending_mod.plan_restore(saved, rows, lambda oid: prices.get(oid))
        if not plan.alive:
            self._log.info(
                "live_pending_dropped",
                payload={
                    "trade_id": saved.record.trade_id,
                    "dropped": len(plan.dropped),
                    "note": "저장된 대기 계획이 거래소에 남아 있지 않다 — 접는다",
                },
            )
            if self._store is not None and self._run_id is not None:
                with contextlib.suppress(Exception):
                    await self._store.put_meta(self._run_id, pending_mod.META_KEY, None)
            return set()
        kept: list[str] = []
        for leg in plan.inherit:
            assert leg.order_id is not None
            filler.inherit(leg.ticket, leg.order_id, leg.ratio)
            kept.append(leg.ticket)
        for leg in plan.place:
            filler.place(leg.ticket, price=leg.price, ratio=leg.ratio, long=leg.long)
            kept.append(leg.ticket)
        for leg, price in plan.filled:
            filler.inherit(leg.ticket, leg.order_id or "", leg.ratio)
            filler.note(leg.ticket, price=price, ratio=leg.ratio)
            kept.append(leg.ticket)
        ok = self._session.restore_pending(saved.record, tuple(kept), saved.waiting_until)
        # 🔴 **저장돼 있던 값을 기억해 둔다** (1.0.3 · 2026-09-06 실측). `_persist_pending` 은
        #    마지막으로 쓴 값과 같으면 안 쓴다 — 복원 뒤 그 기억이 None 이면 대기가 끝나
        #    (체결 흡수) 지워야 할 때도 "None == None" 으로 건너뛰어 **메타가 영원히 남는다.**
        #    다음 재시작이 이미 열린 매매의 대기 계획을 또 되살리려 든다 (세션이 거절하지만
        #    우편함에 죽은 체결이 남는다).
        self._pending_json = json.dumps(pending_mod.to_json(saved), sort_keys=True)
        self._log.info(
            "live_pending_restored" if ok else "live_pending_restore_refused",
            payload={
                "trade_id": saved.record.trade_id,
                "inherited": len(plan.inherit),
                "placed": len(plan.place),
                "filled": len(plan.filled),
                "dropped": len(plan.dropped),
            },
        )
        if not ok:
            # 세션이 거절했다(이미 보유 중이거나 대기 중) — 저장된 계획은 더 이상 사실이 아니다.
            await self._persist_pending()
            return set()
        return {leg.order_id for leg in plan.inherit if leg.order_id is not None}

    async def _persist(self) -> None:
        """원장을 DB 에 통째로 다시 쓴다.

        Note:
            ⚠️ **매 걸음 통째로 쓴다** — 파일 저널이 하던 그대로다. 한 판의 매매는
            많아야 수십 건이라 비용이 없고, 이어쓰기로 두면 갱신(대기 → 체결 → 청산)을
            표현할 수 없다.

            ⛔ **실패해도 던지지 않는다** (절대 규칙 #8-1). 저장 실패가 손절·청산 같은
            리스크 감소 행동을 막으면 안 된다 — 로그로 크게 남기고 매매는 계속 돈다.
        """
        if self._store is None or self._run_id is None:
            return
        try:
            await self._store.save(self._run_id, self._session.ledger.records)
        except Exception as exc:
            self.last_error = f"원장 저장 실패: {exc}"[:200]
            self._log.error(
                "live_ledger_unsaved",
                payload={
                    "run_id": str(self._run_id),
                    "error": str(exc)[:200],
                    "note": "매매는 계속 돈다 — 다음 걸음에 다시 쓴다 (§1.2.1)",
                },
            )

    def note_guard(self, name: str, detail: str) -> None:
        """**밖에서** 안전장치 발동을 적는다 (T20 ④-b).

        Args:
            name: 안전장치 이름.
            detail: 무엇 때문이었나.

        Note:
            🔴 **감시자는 러너 밖에 있다.** 그런데 *"감시자가 되살렸다"* 는 사실은
            **판 화면**에 남아야 한다 — 로그에만 두면 아무도 안 본다.

            ⚠️ *"판이 잘 돌고 있다"* 와 *"두 번 죽었다가 되살아났다"* 는 전혀 다른
            상태이고, 후자는 **원인이 아직 남아 있다**는 뜻이다.
        """
        self._fired(name, detail)

    def _fired(self, name: str, detail: str) -> None:
        """안전장치 하나가 발동했다고 적는다.

        Args:
            name: 안전장치 이름.
            detail: 무엇 때문이었나.

        Note:
            ⚠️ **횟수만 세지 않는다.** 숫자 하나는 *"세 번 돌았다"* 까지만 말하고,
            사람이 알아야 하는 것은 *"무엇 때문에"* 다.
        """
        was = self.guards.get(name, {})
        self.guards[name] = {
            "count": str(int(was.get("count", "0")) + 1),
            "at": datetime.now(UTC).isoformat(),
            "why": detail[:160],
        }
        self._log.info("live_guard_fired", payload={"guard": name, "why": detail[:160]})

    async def set_leverage(self, leverage: Decimal) -> None:
        """거래소 쪽 배율을 바꾼다 (사용자 요구 2026-08-19).

        Args:
            leverage: 새 배율.

        Raises:
            RuntimeError: 어댑터가 배율 변경을 모르는 경우.

        Note:
            ⛔ **원장은 여기서 안 바꾼다.** 원장의 배율은 손익 계산의 근거이고, 그것을
            바꾸는 것은 API 층의 결정이다 — 여기서 같이 바꾸면 거래소만 성공하고 원장이
            실패하는 순간을 표현할 자리가 없어진다.
        """
        if not isinstance(self._orders, LeverageAware):
            raise RuntimeError("이 어댑터는 배율 변경을 모른다")
        await self._orders.set_leverage(self.instrument, leverage)
        self._fired("leverage", f"배율을 {leverage} 로 바꿨다 — 청산가가 진입에 가까워진다")

    async def _sync_leverage(self) -> None:
        """거래소 배율을 **원장과 같게** 만든다 — 판이 뜰 때 한 번 (2026-08-20 ⓔ).

        Note:
            🔴 **이것이 없어서 사고가 났다.** `set_leverage` 는 사람이 화면에서 *"배율
            바꾸기"* 를 누를 때만 불렸다. 그래서 20배로 만든 판이 거래소에 남아 있던
            **3배**로 돌았고, 계약 수는 20배 기준으로 계산됐다.

            ```
            우리 계산   예산 69.07 x 20배 = 명목 1381.4  →  증거금 69.07 이라 믿는다
            Gate 계산   명목 1381.4 / 3배             →  증거금 460.5 를 요구한다
            ```

            거절되면 그나마 낫다 — **통과하면** 원장은 20배 손익을 적고 실제는 3배라
            수치가 조용히 6.7배 갈린다. 이 클래스의 `LeverageAware` docstring 이 이미
            *"거래소와 원장이 같은 배율을 봐야 한다"* 고 적어 뒀는데, 아무도 안 불렀다.

            🔴 **못 맞추면 주문을 아예 안 낸다** (`observe_only`). 배율을 모르는 채로
            내는 주문은 크기를 모르는 주문이고, 그 실패는 화면에서 조용하다 (규칙 #8).
            판은 계속 뜬 채로 판정만 하므로 사람이 보고 고칠 수 있다.

            ⚠️ **매 걸음 확인하지 않는다.** Gate 의 배율은 계약별 계정 설정이라 우리가
            바꾸지 않는 한 유지되고, 한 종목에 한 판이라 다른 판이 바꿀 수도 없다.
        """
        if self.observe_only:
            # ⛔ 관찰 전용은 주문을 안 내므로 배율이 뜻이 없다.
            return
        wanted = self._session.ledger.leverage
        if not isinstance(self._orders, LeverageAware):
            self._log.warning(
                "live_leverage_unsupported",
                payload={
                    "wanted": str(wanted),
                    "note": "이 어댑터는 배율을 못 바꾼다 — 거래소 설정 그대로 나간다",
                },
            )
            return
        try:
            await self._orders.set_leverage(self.instrument, wanted)
        except Exception as exc:
            self.failures += 1
            self.observe_only = True
            self.last_error = f"배율 {wanted} 을 못 걸어 주문을 멈췄다: {exc}"[:200]
            self._fired(
                "leverage_desync",
                f"거래소 배율을 {wanted} 로 못 바꿨다 — 주문을 내지 않는다",
            )
            self._log.error(
                "live_leverage_sync_failed",
                payload={
                    "wanted": str(wanted),
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": "계약 수 계산의 전제가 깨졌다 — 주문을 내면 크기가 틀린다",
                },
            )
            return
        self.leverage_ok = True
        self._log.info(
            "live_leverage_synced",
            payload={"symbol": self.instrument.symbol, "leverage": str(wanted)},
        )

    async def _spare_margin(self) -> Decimal | None:
        """계정이 **지금 쓸 수 있는 돈** — 다른 판이 잡은 증거금을 뺀 값 (ㄷ).

        Returns:
            가용 금액. 못 읽으면 None (그러면 예산 그대로 간다).

        Note:
            🔴 **Gate 의 `available` 이 이미 그 값이다.** 격리 마진에서 포지션이 열리면
            거래소가 필요한 증거금을 `available` 에서 떼어 포지션에 붙인다 — 즉 다른
            판이 굴리는 돈은 여기서 이미 빠져 있다. 우리가 따로 셀 필요가 없다.

            ⚠️ **못 읽으면 자르지 않는다.** 조회 실패로 주문을 0계약으로 만들면, 그
            실패가 화면에서 "신호가 없었다" 와 구별되지 않는다 (절대 규칙 #8). 대신
            거래소가 `INSUFFICIENT_AVAILABLE` 로 거절하고 그것은 `placed` 에 남는다.
        """
        try:
            account = await self._orders.get_balance()
        except Exception as exc:
            self._log.warning("live_balance_unreadable", payload={"error": str(exc)[:140]})
            return None
        return Decimal(str(account.cash))

    async def _note_order(
        self,
        trade_id: str,
        *,
        role: str,
        status: str,
        order_id: str = "",
        contracts: str = "",
        price: Decimal | None = None,
        error: str = "",
        raw: dict[str, object] | None = None,
    ) -> None:
        """주문 하나의 지금 상태를 판 밑에 적는다 (T16 ②).

        Args:
            trade_id: 매매의 짧은 id.
            role: 진입 · 익절1 · 익절2 · 손절 · 청산.
            status: 거래소가 말한 상태.
            order_id: 거래소 주문 id.
            contracts: 계약 수.
            price: 지정가·발동가.
            error: 실패 문구.
            raw: 되짚기용 원문. **로그가 아니라 표에 남는다** — 컨테이너 로그는 재기동에
                날아가고, 그때 남은 것이 없으면 원인을 좁힐 수 없다 (2026-08-29 실측:
                post-only 거절 18건의 의도 가격을 사후에 구하지 못했다).

        Note:
            🔴 **원장이 보유중인데 여기 행이 없으면 유령 포지션이다.** 원장은 판정만으로
            보유를 적고 전송은 러너가 따로 한다 — 전송이 실패해도 보유는 남는다.
        """
        if self._store is None or self._run_id is None:
            return
        await self._store.record_order(
            self._run_id,
            trade_id,
            role=role,
            status=status,
            exchange_order_id=order_id,
            contracts=contracts,
            price=price,
            error=error,
            raw=raw,
        )
        # 🔴 교정 원장 (T185) — **상태가 바뀔 때만** 남긴다.
        #
        #    조건부 손절은 24시간에 만료돼 **매 걸음 다시 걸린다**. 그대로 적으면
        #    하루에 수천 행이 쌓이고, 정작 세려는 *"몇 번 시도해서 몇 번 어긋났나"*
        #    가 재장착 잡음에 묻힌다.
        #
        #    ⚠️ 그렇다고 `wf_orders` 처럼 덮어쓰면 안 된다 — 거절 3회 뒤 체결 1회가
        #      "체결 1회" 로만 보인다. 그래서 **변화만 추가**한다.
        seen = self._calibrated.get((trade_id, role))
        if seen != status:
            self._calibrated[(trade_id, role)] = status
            await self._note_calibration(
                kind="entry" if role == "진입" else "exit",
                trade_id=trade_id,
                role=role,
                intended_price=price,
                sent_contracts=Decimal(contracts) if contracts else None,
                extra={
                    "status": status,
                    "order_id": order_id,
                    "error": error,
                    "symbol": self.instrument.symbol,
                    "raw": dict(raw or {}),
                },
            )

    async def _note_calibration(
        self,
        *,
        kind: str,
        trade_id: str = "",
        role: str = "",
        intended_price: Decimal | None = None,
        judge_close: Decimal | None = None,
        judge_ts: datetime | None = None,
        rvol: Decimal | None = None,
        wanted_contracts: Decimal | None = None,
        sent_contracts: Decimal | None = None,
        amount: Decimal | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        """교정 원장에 한 줄 더한다 (T185) — **관측 전용**.

        Note:
            🔴 **못 되찾는 값만 남기면 된다.** `judge_close` 와 `rvol` 은 `judge_ts`
            만 있으면 봉에서 **다시 계산**할 수 있다 (둘 다 데이터의 결정론적 함수다).
            반면 *러너가 걸려고 한 가격* 과 *증거금 한도 없었으면 걸었을 수량* 은
            사후에 만들 수 없다 — 실제로 2026-08-29 post-only 거절 18건의 의도
            가격을 못 구했다.

            그래도 `judge_close` 는 손에 있으면 같이 적는다. 데이터가 개정되면
            재계산이 그때의 값과 달라지기 때문이다.

            ⚠️ 실패해도 던지지 않는다 (절대 규칙 #8-1).
        """
        if self._store is None or self._run_id is None:
            return
        await self._store.record_calibration(
            self._run_id,
            kind=kind,
            trade_id=trade_id,
            role=role,
            intended_price=intended_price,
            judge_close=judge_close,
            judge_ts=judge_ts,
            rvol=rvol,
            wanted_contracts=wanted_contracts,
            sent_contracts=sent_contracts,
            amount=amount,
            extra=dict(extra or {}),
        )

    async def _note_funding(self) -> None:
        """펀딩 요율을 본 순간을 교정 원장에 남긴다 (T185 ⑤).

        Note:
            같은 요율을 같은 8시간 구간에서 여러 번 보므로 **값이 바뀔 때만** 적는다.
            시점 오차를 재는 데 필요한 것은 *"언제 얼마로 바뀌었나"* 이지 폴링 기록이
            아니다.
        """
        rate = self._session.recent_funding
        if rate is None:
            return
        mark = f"{rate}"
        if self._funding_seen == mark:
            return
        self._funding_seen = mark
        held = self._session.position
        await self._note_calibration(
            kind="funding",
            amount=Decimal(str(rate)),
            extra={
                "symbol": self.instrument.symbol,
                "observed_at": datetime.now(UTC).isoformat(),
                "trade_id": held.trade_id if held else "",
                "entry": str(held.entry) if held else "",
                "leverage": str(held.leverage) if held else "",
                "note": "요율이다 — 정산액은 계좌원장 API 가 따로 필요하다",
            },
        )

    @property
    def instrument(self) -> Instrument:
        """대상 종목."""
        return self._session.instrument

    @property
    def ledger(self) -> Ledger:
        """이 판의 원장 — **읽기용**이다.

        Note:
            API 가 같은 계좌 판들의 평가액을 합할 때 쓴다 (`account_modelled`).
            ⛔ 밖에서 고치지 않는다 — 원장을 바꾸는 것은 러너와 세션의 일이다.
        """
        return self._session.ledger

    @property
    def beat(self) -> Timeframe:
        """**걸음이 도는 박자** — 감시자가 정지를 재는 기준 (사용자 신고 2026-08-21).

        Returns:
            방아쇠 축이 따로 돌면 그것, 아니면 판정 축.

        Note:
            🔴 **방아쇠가 없는 룰이 있다.** 봉 마감 판정형 플레이북은
            `trigger_timeframe` 을 선언하지 않아 `_chase_trigger` 가 즉시 반환한다 —
            그 판은 **판정 축 봉이 마감돼야** 한 걸음 간다.

            감시자가 상수 300초로 재는 바람에 멀쩡한 15분봉 판 둘이 *"걸음이 541초째
            그대로다"* 로 붉게 떴다. 그 문구는 *"아무도 관리하지 않는다"* 라고 겁을
            주는데, 늘 떠 있으면 **진짜일 때 아무도 안 본다.**

            ⚠️ `price_frame` 과 다르다 — 저것은 *진입가를 적는 축*이고 방아쇠가 없으면
            `STEP_FRAME`(5m) 으로 떨어진다. 걸음의 박자와는 다른 값이다.
        """
        trigger = self._session.price_frame
        return self.entry if trigger is None or trigger is self.entry else trigger

    @property
    def price_frame(self) -> Timeframe:
        """**진입가를 적는 축** — 세션이 정하고 없으면 `STEP_FRAME` (T17 ③).

        Note:
            🔴 **판정 축과 다르다.** 계획은 판정 축(15m)으로 서고 진입가는 이 축의 마지막
            마감 봉 종가로 적힌다. 둘이 벌어지면 한 계획 안에 두 시점이 섞여 익절이
            진입 아래로 갈 수 있다 — 2026-08-18 에 4건이 그렇게 나갔다.
        """
        return self._session.price_frame or STEP_FRAME

    @property
    def entry(self) -> Timeframe:
        """진입 시간축."""
        return self._feed.entry

    @property
    def gaps(self) -> int:
        """급전에 남은 봉 구멍 — 0 이 아니면 시리즈에 빈 곳이 있다."""
        return self._feed.gaps

    @property
    def reconnects(self) -> int:
        """웹소켓 재연결 횟수 — 0 이 아니면 그 사이 봉을 놓쳤을 수 있다.

        Note:
            🔴 **두 곳에서 끊긴다.** 스트림 내부가 스스로 다시 붙는 경우와, 스트림
            **자체가 끝나** 러너가 새로 붙는 경우다. 둘을 합쳐 세지 않으면 화면이
            *"재연결 0"* 이라고 적는 동안 러너는 다섯 번 다시 붙어 있을 수 있다.
        """
        return self._stream.reconnects + self._restarts

    def pending_bar(self) -> Candle | None:
        """진행 중인 봉 — 화면이 "살아 있나" 를 보는 근거다.

        Returns:
            미마감 봉. 없으면 None.

        Note:
            ⚠️ **None 이 오래 이어지면 스트림이 죽은 것이다.** Gate 는 진행 중인 봉을
            몇 초마다 보내므로, 정상이라면 거의 항상 있다.
        """
        return self._feed.pending(self.entry)

    def identify(self, run_key: str) -> None:
        """판 식별자를 붙인다 — 이후 이 러너의 모든 로그에 `run` 이 박힌다.

        Args:
            run_key: API 가 정한 판 handle (`live7c37a84d`).

        Note:
            생성자에서 못 받는 이유는 handle 이 **러너를 만든 뒤** 정해지기 때문이다.
            안 부르면 로그에 `run` 만 빠지고 나머지는 그대로 나간다 (조용한 실패 아님).
        """
        self.run_key = run_key
        self._log = self._log.bind(run=run_key)  # pyright: ignore[reportAttributeAccessIssue]

    async def run(self) -> None:
        """봉이 올 때마다 세션을 한 걸음 걷는다 (끝나지 않는다).

        Note:
            ⛔ **`finished` 로 끝나지 않는다.** 라이브는 끝이 없다. 멈추는 것은 취소
            (`asyncio.CancelledError`)뿐이며, 그때 스트림이 정리된다.

            ⚠️ 미마감 봉도 `push` 한다 — `pending` 에 담겨 화면이 쓴다. `advance` 가
            False 를 주므로 판정은 안 돈다.
        """
        # ⭐ **이 태스크에서 나가는 모든 로그에 판을 박는다** (T76). 러너 자신의
        #    로그는 `self._log` 가 이미 달고 나가지만, 어댑터(`gate_stop_placed` ·
        #    `binance_order_submit` 등)는 판을 모른다 — contextvars 로 흘려 준다.
        structlog.contextvars.bind_contextvars(
            run=self.run_key,
            market=self.instrument.market.value,
            symbol=self.instrument.symbol,
        )
        self._log.info(
            "live_runner_started",
            payload={
                "symbol": self.instrument.symbol,
                "entry": self.entry.value,
                "testnet_orders": getattr(self._orders, "is_testnet", None),
                "live_data": not self._stream.is_testnet,
            },
        )
        # 🔴 **스트림이 끝나면 러너가 죽었다.** `async for` 가 정상 종료로 빠져나오면
        #    `run()` 이 반환하고 판은 조용히 멈춘다 — 화면에는 `running=false` 만 뜨고
        #    이유가 없다. 웹소켓은 끊긴다. 그것이 예외인지 정상 종료인지와 무관하게
        #    **다시 붙어야 한다** (사용자 요구 2026-08-18: *"끊겼을 때 자동으로 연결을
        #    시도해야 해"*).
        #
        # ⛔ 취소(`CancelledError`)만 끝이다 — 사람이 멈춘 것이므로 되살리지 않는다.
        # 🔴 **급전 채우기와 점검을 배경으로 돌린다.** 화면 요청에 묶여 있으면 안 보는
        #    축은 영원히 낡고, 보는 축도 화면이 멈추면 같이 멈춘다.
        #
        # ⚠️ 스트림 루프와 **같은 태스크에 두지 않는다.** 스트림은 봉을 기다리며 오래
        #    막히므로, 거기 섞으면 갱신도 같이 막힌다.
        # 🔴 **거래소에 이미 열려 있는 포지션을 이어받는다** (사용자 지적 2026-08-18:
        #    *"연결이 끊겨버리면 1차 익절이나 그런 대응 자체가 불가능하잖아"*).
        # 🔴 **포지션을 잡기 전에 배율부터 맞춘다** (2026-08-20 사고 ⓔ). 이어받기가
        #    먼저 오면 남의 배율로 잡힌 포지션을 우리 배율이라 믿고 손익을 적는다.
        await self._recall_rejections()
        await self._sync_leverage()
        await self.adopt()
        # 🔴 **재시작으로 사라진 메이커 청산을 마무리한다** (§C · F3 · 2026-09-01).
        #    `adopt` 는 "원장이 모르는 포지션" 을 되받지만, 원장이 이미 `SIGNAL_EXIT` 로
        #    닫아 둔 포지션은 되받으면 안 된다 — 전략의 청산 결정을 존중해 **마무리**한다.
        with contextlib.suppress(Exception):
            await self._recover_pending_exits()
        # 🔴 **판정보다 손절이 먼저다** (2026-08-20 · B3). 되살아나는 순간 포지션은 이미
        #    있는데, 예전에는 `_guard_stop` 이 첫 판정 뒤에야 돌았다 — 그 사이가 무방비다.
        #
        #    WSL 업데이트로 06:47 에 죽고 06:48 에 돌아왔을 때, 포지션 넷 중 셋에
        #    조건부가 없었다. 돌아온 뒤 제일 먼저 할 일은 **지키는 것**이다.
        #
        # ⛔ 실패해도 여기서 안 멈춘다 — 걸음이 돌면 매번 다시 시도한다.
        with contextlib.suppress(Exception):
            await self._guard_stop()
        helpers = [
            asyncio.create_task(self._keep_fresh(), name="live-fresh"),
            asyncio.create_task(self._keep_probing(), name="live-probe"),
            # ⭐ 들고 있는 동안에도 나갈 수 있는지 본다 (5분마다).
            asyncio.create_task(self._keep_watching_book(), name="live-book"),
            # ⭐ 방아쇠 축이 진입 축과 다를 때만 실제로 돈다 (0.1 은 즉시 반환).
            asyncio.create_task(self._chase_trigger(), name="live-trigger"),
        ]
        try:
            await self._loop()
        finally:
            # ⛔ 러너가 끝나면 배경도 끝난다 — 안 거두면 죽은 판의 조회가 계속 나간다.
            for task in helpers:
                task.cancel()

    async def _recall_rejections(self) -> None:
        """재시작 전에 **거절당한 주문**을 되살린다 (사용자 신고 2026-08-20).

        Note:
            🔴 `failures`·`last_error` 는 메모리라 프로세스가 다시 뜨면 0 이 된다. 그래서
            **한 건도 못 내는 판**과 **자리가 아직 안 난 판**이 화면에서 똑같이
            *"첫 판정 대기 · 0/0"* 으로 보였다 — 사용자가 7시간 뒤에야 물었다.

            실측: SOL 판이 15:30 에 두 번 거절됐는데(예산이 1계약을 못 샀다) 19:27
            재시작 뒤 화면은 `failures 0 · last_error ""` 였다. **사실은 DB 에 있었다.**

            ⚠️ **한 번만 읽는다.** 폴링 경로에 두면 판마다 몇 초 간격으로 DB 를 때린다.

            ⛔ 실패해도 안 던진다 — 표시용 값이 판을 못 뜨게 하면 안 된다 (§1.2.1).
        """
        if self._store is None or self._run_id is None:
            return
        with contextlib.suppress(Exception):
            count, why = await self._store.rejections(self._run_id)
            if count <= 0:
                return
            self.failures = count
            self.last_error = f"재시작 전 거절 {count}건 — {why}"[:200]

    async def adopt(self) -> bool:
        """거래소에 **이미 열려 있는 포지션**을 원장으로 이어받는다.

        Returns:
            이어받았으면 True.

        Note:
            🔴 사용자 지적 2026-08-18: *"연결이 끊겨버리면 1차 익절이나 그런 대응 자체가
            불가능하잖아."* 맞다. `src/` 를 고치면 API 가 리로드되고 판이 새로 뜨는데,
            **원장은 비어 있고 거래소 포지션은 남는다.** 그 순간부터 아무도 그 포지션을
            관리하지 않는다 — 반익도, 본절 상향도, 손절 재장착도 멈춘다.

            ⭐ **계획을 지어내지 않는다. 거래소에서 되읽는다.** 우리가 걸어 둔 주문이
            계획을 그대로 들고 있기 때문이다:

            ```
            진입가   포지션 스냅샷의 entry_price
            손절     조건부 주문의 trigger price
            1차·목표  reduce_only 지정가 두 개 (방향에 따라 가까운 쪽이 1차)
            매매 id   주문 text `t-<id>-take_profit-1` 에서 되뽑는다
            ```

            ⛔ **손절을 못 찾으면 이어받지 않는다.** 손절 없이 원장에 넣으면 세션이
            그것을 관리 대상으로 여기면서 `planned_stop` 자리에 아무 값이나 들어간다 —
            그 값이 곧 주문이 된다 (절대 규칙 #4·#8). 그때는 감사(`ledger_mismatch`)가
            사람에게 넘긴다.

            ⚠️ **actor 는 SYSTEM 이 아니다.** 이 기록은 판정으로 만들어진 것이 아니라
            **주워 온 것**이므로 성과 표본에 섞이면 안 된다.
        """
        if not isinstance(self._orders, PositionAware):
            return False
        if any(item.outcome is Outcome.OPEN for item in self._session.ledger.records):
            return False
        try:
            held = await self._orders.position_snapshot(self.instrument)
        except Exception as exc:
            self._log.warning("live_adopt_unreadable", payload={"error": str(exc)[:140]})
            return False
        size = Decimal(str(held.get("size", "0") or "0"))
        if size == 0:
            return False

        stops: list[dict[str, str]] = []
        orders: list[dict[str, str]] = []
        if isinstance(self._orders, StopAware):
            with contextlib.suppress(Exception):
                stops = await self._orders.open_stops(self.instrument)
        if isinstance(self._orders, OrdersAware):
            with contextlib.suppress(Exception):
                orders = await self._orders.open_orders(self.instrument)

        trigger = next(
            (Decimal(str(row["trigger_price"])) for row in stops if row.get("trigger_price")),
            None,
        )
        if trigger is None:
            # ⛔ 계획의 뿌리가 없다 — 지어내지 않고 사람에게 넘긴다.
            self._adopt_refused = (
                f"거래소 보유 {size}계약 · 조건부 손절 없음 — 입양 거부. "
                "원장이 모르는 포지션이라 화면 표에는 안 나온다. 콘솔 청산으로 정리한다"
            )
            self._log.error(
                "live_adopt_refused",
                payload={
                    "size": str(size),
                    "reason": "조건부 손절이 없어 계획을 복구할 수 없다",
                    "note": "감사가 adopt_refused 발견으로 올린다 — 콘솔에서 정리한다",
                },
            )
            return False

        # 🔴 **남의 판 포지션을 주워 오지 않는다** (다중 RUN ③). Gate 무기한은 종목당
        #    포지션이 하나라, 두 판이 같은 종목을 돌면 거래소는 그것이 누구 것인지
        #    모른다 — 이름에 박힌 판 표식이 유일한 근거다 (T18 ⑤).
        #
        # ⚠️ **표식이 없으면 주워 온다.** 규격을 바꾸기 전에 나간 주문이 아직 떠 있을
        #    수 있고, 그때 거부하면 포지션이 고아가 된다 — 그쪽이 더 나쁘다.
        if self._run_key:
            tags = {run_of_text(str(row.get("text", ""))) for row in orders}
            tags.discard("")
            mine = run_tag(self._run_key)
            if tags and mine not in tags:
                self._log.error(
                    "live_adopt_not_mine",
                    payload={
                        "mine": mine,
                        "found": sorted(tags),
                        "note": "다른 판의 포지션이다 — 줍지 않는다 (다중 RUN ③)",
                    },
                )
                return False

        short = size < 0
        entry = Decimal(str(held.get("entry_price", "0") or "0"))
        # ⭐ **가까운 쪽이 1차다.** 숏은 목표가 아래에 있으므로 내림차순으로 세우고,
        #    롱은 위에 있으므로 오름차순으로 센다.
        legs = sorted(
            (Decimal(str(row["price"])) for row in orders if row.get("price")),
            reverse=short,
        )
        if legs:
            first, target = legs[0], legs[-1]
        else:
            # 🔴 익절 주문이 없다 = 추세 판(센티널 익절 생략)일 가능성이 가장 높다.
            #    trigger(손절가)를 익절로 적으면 롱의 익절이 진입 **아래**가 되어
            #    다음 걸음에 지정가가 즉시 크로스 → 포지션이 시장가로 청산된다
            #    (2026-08-26 발견). 센티널을 재구성해 "익절 없음"을 그대로 보존한다.
            risk = abs(entry - trigger)
            far = risk * SENTINEL_RR * 2
            first = target = entry - far if short else entry + far
        trade_id = adopted_id(orders)
        cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(self.instrument.market)
        record = TradeRecord(
            trade_id=trade_id,
            playbook=self._session.playbook.attribution,
            actor=Actor.ADOPTED,
            direction=Direction.SHORT if short else Direction.LONG,
            placed_at=self._session.cursor,
            opened_at=self._session.cursor,
            entry=entry,
            planned_stop=trigger,
            planned_first=first,
            planned_target=target,
            outcome=Outcome.OPEN,
            cost_pct=cost.round_trip_pct,
            leverage=self._session.ledger.leverage,
        )
        self._fired("adopt", f"거래소 포지션 {size} 계약을 원장으로 되읽었다")
        self._session.ledger.add(record)
        self._session.adopt(record)
        self.placed[trade_id] = {
            "order_id": "adopted",
            "status": "adopted",
            "contracts": str(abs(size)),
        }
        self._log.info(
            "live_adopted",
            payload={
                "trade_id": trade_id,
                "direction": "숏" if short else "롱",
                "contracts": str(abs(size)),
                "entry": str(entry),
                "stop": str(trigger),
                "first": str(first),
                "target": str(target),
                "note": "거래소에서 되읽었다 — 판정으로 만든 기록이 아니다",
            },
        )
        return True

    async def manual_adopt(self) -> dict[str, str]:
        """콘솔에서 **고아 포지션을 다시 이어받는다** (사용자 요구 2026-09-01).

        자동 이어받기(`adopt`)는 판이 뜰 때 **한 번만** 돈다. 매매법을 바꾸려고 판을
        지우고 새로 만들면 거래소 포지션이 남는데, 새 판의 원장이 그것을 모르면
        아무도 관리하지 않는 고아가 된다. 매번 판을 지웠다 다시 만들지 않고, 이
        창구가 `adopt` 를 **다시** 불러 그 포지션을 원장으로 되받는다.

        Returns:
            `{adopted, reason}`. `adopted` 가 `"yes"` 면 되받았다 — 다음 대조에서
            고아 경고가 풀린다. `"no"` 면 `reason` 이 왜인지 말한다 (손절이 없으면
            계획을 복구할 근거가 없으므로 콘솔 청산으로 정리해야 한다).

        Note:
            🔴 **스텝 락을 잡고 돈다.** 걸음 루프가 같은 원장을 건드리는 중에 이어받으면
            레코드가 꼬인다 (절대 규칙 #5·#8). `adopt` 는 원래 기동 시점(락 밖)에만
            불렸으므로, 도중에 부르는 이 경로는 직접 직렬화한다.

            ⛔ **손절을 못 찾으면 이어받지 않는다** — `adopt` 의 안전 규약을 그대로
            쓴다. 계획의 뿌리(조건부 손절)가 없으면 원장에 아무 값이나 넣지 않고
            사람에게 넘긴다 (절대 규칙 #4).

            ⚠️ **이미 원장이 보유 중이면 아무 일도 안 한다** — 그 경우는 고아가 아니라
            수량 불일치(naked)라 다른 손잡이(청산·탈출)의 몫이다.
        """
        async with self._stepping:
            self._adopt_refused = None
            ok = await self.adopt()
        if ok:
            return {"adopted": "yes", "reason": "거래소 포지션을 원장으로 되받았다 — 이제 관리한다"}
        if self._adopt_refused:
            return {"adopted": "no", "reason": self._adopt_refused}
        return {
            "adopted": "no",
            "reason": (
                "이어받을 것이 없다 — 거래소 포지션이 0 이거나 원장이 이미 관리 중이다. "
                "수량이 어긋난 것이면 청산·탈출으로 정리한다"
            ),
        }

    async def resync_ledger(self) -> dict[str, str]:
        """**원장을 지금 거래소 상태로 재정렬한다** — 복구 불가한 과거를 앵커한다 (2026-09-01).

        Returns:
            무엇을 옮겼는지 사람이 읽을 요약.

        Note:
            🔴 **왜 필요한가**: 재사용된 계정에 며칠치가 쌓이고 원장이 재개(resume)로
            낡으면, 원장 실현도 거래소 귀속도 어느 것도 진짜가 아닌 **복구 불가** 상태가
            된다. 외과적으로 과거를 고칠 수 없으니, 사람이 이 버튼을 눌러 *"지금 거래소
            상태"* 에 앵커한다 — 과거 fiction 은 버리고 **지금부터 정확히** 추적한다.

            ⭐ **비파괴다.** 원장 기록은 안 지운다(감사 추적용). 대신:
            - `audit_since = now` — 감사가 지금 이전 청산을 무시한다.
            - `realized_anchor = 지금 실현` — 표시·대조가 *지금 이후 실현*만 센다.
            - 거래소 포지션을 다시 이어받고(adopt), 갈림 플래그를 푼다.

            ⛔ **리셋이 아니다.** 거래소 계정·돈·포지션은 안 건드린다 — 우리 회계의
            기준점만 지금으로 옮긴다.
        """
        now = datetime.now(UTC)
        self._session.audit_since = now
        self._session.realized_anchor = self._session.ledger.realized_cash
        self._session.verified_realized = None
        # 거래소 포지션을 원장이 다시 알게 한다 (있으면). 손절 없으면 adopt 가 거부하지만
        # 워터마크·앵커는 이미 잡혔으므로 재정렬 자체는 성립한다.
        adopted = False
        with contextlib.suppress(Exception):
            adopted = await self.adopt()
        # 갈림 플래그를 푼다 — 앵커로 회계 대조가 지금부터 다시 시작한다.
        self._session.accounting_ok = True
        self._session.reconciled = True
        self._log.warning(
            "live_ledger_resynced",
            payload={
                "at": now.isoformat(),
                "realized_anchor": str(self._session.realized_anchor),
                "readopted": str(adopted),
                "note": "복구 불가한 과거를 버리고 지금 거래소 상태에 앵커했다 — 계정은 안 건드림",
            },
        )
        return {
            "resynced": "yes",
            "anchor": str(self._session.realized_anchor),
            "readopted": "yes" if adopted else "no",
        }

    async def _recover_pending_exits(self) -> None:
        """재시작 뒤 **원장은 닫았는데 거래소는 아직 보유중**인 청산을 마무리한다 (§C · F3).

        Note:
            🔴 **왜 필요한가**: 세션이 신호로 전량 청산을 결정하면 원장은 그 자리에서
            `SIGNAL_EXIT` 로 닫고(손익 확정), `_apply_exit` 가 메이커 지정가로 거래소
            포지션을 닫는다. 그 대기 상태(`_maker_exit`)는 **메모리에만** 있어서 `src/`
            리로드·재시작이면 소멸한다 — 그러면 `_expire_maker_exit` 가 안 돌아 지정가가
            영영 안 채워진 채 남고, **원장은 이익을 찍었는데 거래소는 포지션을 든** 고아가
            된다. 사용자가 매매법을 바꾸느라 리로드할 때마다 이 창이 열렸다.

            ⭐ **되받기(`adopt`)가 아니라 마무리다.** 전략은 이미 나가기로 결정했으므로
            그 결정을 존중해 **닫는다** — 다시 여는 것은 청산한 매매를 되살리는 일이다.
            그래서 `adopt`(재개)와 배타적으로, 원장 마지막이 `SIGNAL_EXIT` 이고 거래소
            방향이 그와 같을 때만 여기서 처리한다.

            ⭐ **기존 만료 경로를 그대로 탄다.** `_maker_exit` 에 **이미 만료된** 순번으로
            등록해, 다음 걸음의 `_expire_maker_exit` 가 스냅샷을 다시 확인하고 시장가로
            마무리한다(또는 그새 채워졌으면 정리만). 새 집행 로직을 만들지 않는다.

            ⛔ 실패해도 던지지 않는다 — 청산 마무리는 리스크 **감소** 행동이라 다음
            걸음이 다시 시도한다 (절대 규칙 #8-1).
        """
        if self.observe_only or not isinstance(self._orders, PositionAware):
            return
        if any(item.outcome is Outcome.OPEN for item in self._session.ledger.records):
            return  # 열린 기록이 있으면 재개(adopt)·대조(reconcile)의 몫이다
        try:
            snapshot = await self._orders.position_snapshot(self.instrument)
        except Exception as exc:
            self._log.warning("recover_exit_unreadable", payload={"error": str(exc)[:140]})
            return
        size = held_size(snapshot)
        if size == 0:
            return  # 거래소도 비었다 — 청산이 끝났다
        exited = next(
            (
                item
                for item in reversed(self._session.ledger.records)
                if item.outcome is Outcome.SIGNAL_EXIT
            ),
            None,
        )
        if exited is None:
            return  # 마지막이 신호청산이 아니면 다른 상황 — 대조에 맡긴다
        long_held = size > 0
        if (exited.direction is Direction.LONG) != long_held:
            return  # 방향이 다르다 — 그 청산의 잔량이 아니다
        if exited.trade_id in self._maker_exit:
            return  # 이미 대기줄에 있다
        # 🔴 **이미 만료된** 순번으로 넣는다 — 다음 `_expire_maker_exit` 가 곧장
        #    시장가로 마무리한다 (bars 가 0 이어도 성립: now - placed = bars+1 >= bars).
        bars = self._session.playbook.maker_exit_bars
        self._maker_exit[exited.trade_id] = (self._step_seq() - bars - 1, abs(size))
        self._fired(
            "recover_exit",
            f"재시작 복구 — 원장은 닫았는데 거래소가 {size:+d} 계약 보유중, 청산을 마무리한다",
        )
        self._log.warning(
            "live_recover_pending_exit",
            payload={
                "trade_id": exited.trade_id,
                "size": str(size),
                "note": "메이커 청산 대기가 재시작으로 사라졌다 — 시장가로 마무리한다 (§C)",
            },
        )

    async def reconcile(self) -> bool:
        """거래소가 **이미 닫은** 포지션을 원장에도 닫는다.

        Returns:
            닫았으면 True.

        Note:
            🔴 사용자 지적 2026-08-18: *"실제 주문은 이미 손절 난 상태야. (…) 현재
            화면에서는 해당 포지션을 보유 중인걸로 보여."*

            청산 판정(`Session._settle`)은 **진입 축 봉이 마감돼야** 도는데, 브로커측
            조건부 손절은 **가격이 닿는 순간** 발동한다. 그 사이 최대 한 봉(15분) 동안
            원장은 "보유중", 거래소는 "없음" 이다 — 화면이 그 시간 내내 거짓말한다.

            ⭐ **30초마다 돈다** (`_keep_probing`). 판정 주기와 무관하게 사실을 따라가는
            일이므로 봉을 기다릴 이유가 없다.

            ⛔ **청산가를 지어내지 않는다.** 거래소 체결 이력에서 실제 체결가를 읽고,
            못 찾으면 닫지 않고 감사에 맡긴다 (절대 규칙 #4·#8).

            ⚠️ 이것은 **판정이 아니다.** 진입 판단은 여전히 봉 마감에서만 돈다 —
            그 둘을 섞으면 라이브와 백테스트가 다른 규칙으로 돌게 된다 (절대 규칙 #5).
        """
        held = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if held is None or not isinstance(self._orders, PositionAware):
            return False
        try:
            snapshot = await self._orders.position_snapshot(self.instrument)
        except Exception as exc:
            self._log.warning("live_reconcile_unreadable", payload={"error": str(exc)[:140]})
            return False
        if Decimal(str(snapshot.get("size", "0") or "0")) != 0:
            return False

        fill = await self._closing_fill(held.trade_id)
        if fill is None:
            self._log.error(
                "live_reconcile_no_fill",
                payload={
                    "trade_id": held.trade_id,
                    "note": "거래소는 비었는데 체결가를 못 찾았다 — 감사가 사람에게 넘긴다",
                },
            )
            return False

        long_side = held.direction is Direction.LONG
        gained = (fill > held.entry) if long_side else (fill < held.entry)
        # 🔴 **청산인지 먼저 묻는다** (T14-2). 포지션이 사라진 것만으로는 우리 손절이
        #    체결된 것과 구별되지 않는데, 둘은 손익이 전혀 다른 사건이다.
        outcome = Outcome.TAKE_PROFIT if gained else Outcome.STOP_LOSS
        if not gained and await self._was_liquidated():
            outcome = Outcome.LIQUIDATED
            self._fired("liquidation", f"강제청산 — 진입 {held.entry} · 청산 {fill}")
        done = held.closed(at=datetime.now(UTC), price=fill, outcome=outcome)
        self._fired("reconcile", f"거래소가 먼저 닫았다 — {outcome.value} @ {fill}")
        self._session.ledger.replace(done)
        self._session.release()
        await self._align_fee(done.trade_id)
        self._log.info(
            "live_reconciled",
            payload={
                "trade_id": held.trade_id,
                "entry": str(held.entry),
                "exit": str(fill),
                "outcome": done.outcome.value,
                "note": "거래소가 먼저 닫았다 — 봉 마감을 기다리지 않고 따라잡았다",
            },
        )
        return True

    async def _was_liquidated(self) -> bool:
        """방금 사라진 포지션이 **강제청산**이었나 (T14-2).

        Returns:
            청산 흔적을 찾았으면 True. 못 읽거나 흔적이 없으면 False.

        Note:
            🔴 **구별하지 못하면 성적이 망가진다.** 청산은 `gain_pct` 가 -100% 로
            기록되는 사건이고(지갑이 빈다), 정상 손절은 계획한 만큼만 잃는다. 둘을
            뭉개면 손절 한 건이 계좌를 날린 것으로 세어진다.

            ⚠️ **문자열로 가른다.** Gate 는 청산 전용 `type` 을 주지 않고 강제청산도
            `pnl` 로 들어온다 — 구별은 `text` 에 남는다. 그래서 거래소 문구에 의존하며,
            문구가 바뀌면 **False 로 떨어진다**(청산을 손절로 본다).

            ⛔ 그 방향이 맞다. 손절을 청산으로 잘못 세면 성적이 -100% 로 튀지만, 청산을
            손절로 세면 실제 손실만큼만 적힌다 — 틀리더라도 **덜 거짓말하는 쪽**이다.
            대신 감사가 지갑 대조(`wallet_drift`)로 그 차이를 잡는다.
        """
        if not isinstance(self._orders, BookAware):
            return False
        try:
            rows = await self._orders.account_book(limit=LIQ_LOOKBACK)
        except Exception as exc:
            self._log.warning("live_account_book_unreadable", payload={"error": str(exc)[:140]})
            return False
        for row in rows:
            text = f"{row.get('text', '')} {row.get('type', '')}".lower()
            if any(mark in text for mark in LIQ_MARKS):
                self._log.error(
                    "live_liquidation_seen",
                    payload={
                        "row": str(row)[:200],
                        "note": "강제청산이다 — 손절 체결과 다르게 센다 (-100%)",
                    },
                )
                return True
        return False

    async def _closing_fill(self, trade_id: str) -> Decimal | None:
        """그 매매를 **닫은** 체결가 — 거래소 이력에서 읽는다.

        Args:
            trade_id: 원장의 매매 id.

        Returns:
            체결가. 못 찾으면 None.

        Note:
            ⭐ 조건부가 발동해 생긴 주문은 Gate 가 `ao-` 이름을 붙인다 — 우리 id 가 안
            달려 있으므로 **가장 최근 체결**을 쓴다. 우리 이름이 붙은 청산 주문
            (`take_profit`·`cclose`)이 있으면 그쪽이 우선이다.
        """
        if not hasattr(self._orders, "recent_orders"):
            return None
        try:
            rows = await self._orders.recent_orders(self.instrument)  # type: ignore[attr-defined]
        except Exception:
            return None
        ours = [
            row
            for row in cast("list[dict[str, str]]", rows)
            if trade_id in str(row.get("text", "")) and row.get("fill_price")
        ]
        pick = ours[0] if ours else None
        if pick is None:
            # ⚠️ `ao-` 는 이름에 우리 id 가 없다. 체결된 것 중 가장 최근을 쓴다.
            fills = [
                row
                for row in cast("list[dict[str, str]]", rows)
                if row.get("fill_price") and str(row.get("finish_as")) == "filled"
            ]
            pick = fills[0] if fills else None
        if pick is None:
            return None
        try:
            return Decimal(str(pick["fill_price"]))
        except Exception:
            return None

    async def _align_fee(self, trade_id: str) -> None:
        """닫힌 매매의 비용을 **거래소가 실제로 뗀 수수료**로 맞춘다 (T236 · 2026-09-09).

        Args:
            trade_id: 원장의 매매 id.

        Note:
            원장은 모형 왕복 비용(`cost_pct` · costs.yml 테이커 0.15%)을 빼고 거래소는 계정
            요율(메이커 0.02% · 테이커 0.05% 등)로 뗀다. 작은 매매에서 그 차이가 부호를
            뒤집었다(데모 DOGE 원장 -0.40 vs 거래소 +0.25 · `pnl_sign_split`). 청산 이력에서 이
            매매 창 안의 `position_close` 행을 찾아 `pnl_fee` 를 붙이고 `cost_pct` 를 실제
            비율로 바꾼다 — 원장 산식은 그대로다.

            ⛔ 못 찾으면 안 바꾼다(모형값 유지 · 규칙 #4·#8). 한 번 맞추면 `fee_actual` 이 남아
            다시 안 한다.
        """
        record = next(
            (item for item in self._session.ledger.records if item.trade_id == trade_id), None
        )
        if record is None or record.closed_at is None or record.opened_at is None:
            self._log.info(
                "live_fee_align_skipped",
                payload={"trade_id": trade_id, "why": "기록 없음 또는 열린/미체결 기록"},
            )
            return
        if record.fee_actual is not None:
            return
        if not hasattr(self._orders, "position_closes"):
            self._log.info(
                "live_fee_align_skipped",
                payload={
                    "trade_id": trade_id,
                    "why": f"어댑터에 청산 이력이 없다 ({type(self._orders).__name__})",
                },
            )
            return
        try:
            rows = cast(
                "list[dict[str, object]]",
                await self._orders.position_closes(self.instrument),  # type: ignore[attr-defined]
            )
            spec = await self._contract_spec()
            multiplier = Decimal(str(spec["quanto_multiplier"]))
        except Exception as exc:
            self._log.warning(
                "live_fee_align_unreadable", payload={"trade_id": trade_id, "error": str(exc)[:140]}
            )
            return
        lo = record.opened_at.timestamp() - 60
        hi = record.closed_at.timestamp() + 900
        picked: dict[str, object] | None = None
        for row in rows:
            try:
                ts = float(str(row.get("time") or "nan"))
            except ValueError:
                continue
            if lo <= ts <= hi:
                picked = row  # 최신순이라 첫 번째가 가장 늦은 청산
                break
        if picked is None:
            self._log.info(
                "live_fee_align_skipped",
                payload={"trade_id": trade_id, "why": f"창 안 청산 행 없음 (행 {len(rows)}개)"},
            )
            return
        found = fee_from_close(picked, multiplier)
        if found is None:
            self._log.info(
                "live_fee_align_skipped",
                payload={"trade_id": trade_id, "why": "청산 행에 pnl_fee/max_size/가격이 없다"},
            )
            return
        fee, ratio = found
        before = record.cost_pct
        aligned = dc_replace(record, cost_pct=ratio, fee_actual=fee)
        self._session.ledger.replace(aligned)
        self._log.info(
            "live_fee_aligned",
            payload={
                "trade_id": trade_id,
                "fee": str(fee),
                "cost_pct_model": str(before),
                "cost_pct_actual": str(ratio),
                "note": "모형 비용 → 거래소 실제 수수료 비율 (원장 실현 = 거래소 실현 · T236)",
            },
        )
        await self._persist()

    async def _correct_exit_to_fill(self, trade_id: str) -> None:
        """청산된 매매의 원장 청산가를 **거래소 실측 체결가로 교정한다** (자동 재구성 · 벽돌 1).

        Note:
            🔴 **원장은 판정 시점 신호가로 청산을 확정한다**(결정론 코어 · 규칙 #5).
            실제 체결가는 메이커 오프셋·슬리피지만큼 다르므로, 그대로 두면 실현손익이
            **신호가 기준 허구**로 남아 펀드 손익에 섞인다. `reconcile()` 이 거래소-우선
            청산에서 이미 하는 교정을 **메이커 청산 체결**에도 적용한다 — 거래소가 진실.

            ⛔ **못 읽으면 안 바꾼다.** 체결가를 못 찾으면 지어내지 않고 신호가를 둔다
            (규칙 #4·#8) — 감사가 `pnl_drift` 로 잡는다.

            ⚠️ 라이브가 백테스트와 갈리는 지점이다 — 그것이 맞다. 백테스트는 모형가로
            일관되게, 라이브는 **실제 체결가**로 일관되게 잰다 (reconcile 과 같은 철학).
        """
        record = next(
            (item for item in self._session.ledger.records if item.trade_id == trade_id), None
        )
        if record is None or record.exit_price is None:
            return
        fill = await self._closing_fill(trade_id)
        if fill is None or fill == record.exit_price:
            return
        corrected = record.closed(
            at=record.closed_at or datetime.now(UTC), price=fill, outcome=record.outcome
        )
        self._session.ledger.replace(corrected)
        self._log.info(
            "live_exit_price_corrected",
            payload={
                "trade_id": trade_id,
                "from": str(record.exit_price),
                "to": str(fill),
                "note": "신호가 → 실제 체결가 (거래소가 진실 · 자동 재구성)",
            },
        )
        await self._align_fee(trade_id)

    async def _loop(self) -> None:
        """스트림을 먹으며 끝나지 않는다 — 끊기면 다시 붙는다."""
        while True:
            try:
                await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._restarts += 1
                self.last_error = f"스트림: {exc}"[:200]
                self._log.error(
                    "live_runner_stream_died",
                    payload={
                        "error": str(exc)[:200],
                        "restarts": self._restarts,
                        "note": "다시 붙는다 — 그 사이 봉은 REST 로 메운다",
                    },
                )
            else:
                self._restarts += 1
                self._log.warning(
                    "live_runner_stream_ended",
                    payload={"restarts": self._restarts, "note": "예외 없이 끝났다 — 다시 붙는다"},
                )
            # ⚠️ 곧바로 다시 붙지 않는다. 거래소가 막고 있는 경우 초당 재연결은
            #    레이트리밋을 부르고, 그러면 조회까지 같이 막힌다.
            await asyncio.sleep(RECONNECT_WAIT)
            # 🔴 끊긴 동안의 봉을 메운다 — 안 메우면 그 구간이 판정에서 통째로 빠지고,
            #    빈 봉의 ATR·MA 는 **조용히 틀린다**.
            try:
                await self._heal()
            except Exception as exc:
                self._log.error("live_runner_heal_failed", payload={"error": str(exc)[:200]})

    async def _keep_fresh(self) -> None:
        """**모든 축을 스스로 채운다** — 화면이 보든 말든.

        Note:
            🔴 사용자 지적 2026-08-18: *"지금 봉 갱신이 실시간으로는 아예 안되는거야?
            거의 다 딜레이가 2배씩인데"*. 맞다. 실측 (간격 대비 배수):

                10s 3.67x · 30s 2.22x · 1m 3.61x · 5m 2.12x · 15m 1.71x · 1d 1.46x

            갱신이 **화면 요청에 묶여 있었다** — `/state?frame=X` 가 올 때만 그 축을
            채웠다. 그래서 안 보는 축은 영원히 낡고, 보는 축도 화면이 멈추면 같이 멈춘다.

            ⇒ 급전을 채우는 것은 **화면의 일이 아니다.** 러너가 자기 주기로 돈다.

            ⚠️ 축마다 자기 간격으로만 조회한다 (`refresh` 의 TTL). 9개 축을 같은 주기로
            때리면 1d 봉을 초 단위로 묻게 되고, 그것은 레이트리밋을 부른다.

            ⛔ 예외가 나도 멈추지 않는다. 급전 채우기가 실패한다고 판정이 멈추면
            **점검이 위험 자체가 된다** — 그 실패는 감사가 잡는다.
        """
        while True:
            await asyncio.sleep(FRESH_TICK)
            for frame in self._feed.timeframes:
                # 🔴 **아무도 안 보는 축은 안 당긴다** (2026-08-29 실측으로 잡았다).
                #
                #    계측이 붙고 나서야 숫자가 나왔다: 한도의 **156%** 를 쓰고 있었다
                #    (`used 3747 / limit 2400`). 판 6개가 각자 9개 축을 계속 데우는데,
                #    사람은 그중 하나를 본다 — 나머지 여덟은 밴을 부르는 값이다.
                #
                #    ⇒ 판정에 필요한 축은 언제나 데우고, **보기용은 누가 볼 때만** 데운다.
                if not self._needs(frame):
                    continue
                try:
                    await self.refresh(frame)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log.warning(
                        "live_refresh_failed",
                        payload={"frame": frame.value, "error": str(exc)[:140]},
                    )

    async def _chase_trigger(self) -> None:
        """**방아쇠 축을 쫓는다** — 그 축 봉이 마감되면 한 걸음 돈다 (T17).

        Note:
            🔴 **웹소켓은 진입 축만 준다.** 축마다 구독하면 10초봉이 초당 수십 프레임을
            밀어 넣고 그 대부분은 아무도 안 보는 축이다 — 그래서 방아쇠 축만 REST 로
            촘촘히 채운다.

            ⭐ `refresh` 의 TTL 이 간격의 1/10 이라 10초봉은 1초에 한 번까지 물을 수
            있다. 그보다 자주 물어도 새 봉이 없다.

            ⛔ **진입 축과 같으면 아예 안 돈다** — 0.1 은 이 루프가 없는 것과 같다.

            ⚠️ 여기서 `_one_step` 을 직접 부른다. 스트림 루프와 **같은 함수**를 쓰므로
            판정 경로는 여전히 하나다 (원칙 P3).
        """
        trigger = self._session.price_frame
        if trigger is None or trigger is self.entry:
            return
        while True:
            await asyncio.sleep(TRIGGER_TICK)
            try:
                if not await self.refresh(trigger):
                    continue
                # 🔴 **REST 로 채운 봉은 스스로 판정을 깨우지 않는다.** `refresh` 는
                #    `backfill` 을 쓰고, 그것은 `_arrived` 를 안 켠다 — 켜면 0.1 이
                #    구멍을 메울 때마다 한 걸음 더 돈다. 그래서 여기서 명시적으로 깨운다.
                self._feed.wake()
                await self._one_step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures += 1
                self.last_error = f"방아쇠 판정: {exc}"[:200]
                self._log.error(
                    "live_trigger_step_failed",
                    payload={"error": str(exc)[:200], "frame": trigger.value},
                )

    async def _keep_watching_book(self) -> None:
        """**들고 있는 동안에도 나갈 수 있는지** 본다 (사용자 제안 2026-08-20).

        Note:
            🔴 사용자 지적: *"들고 있는 동안 호가가 마르는 경우"*. 유동성 문은 판을
            **띄울 때 한 번만** 돌았고, 그 뒤로는 아무도 다시 안 봤다 — 나갈 때가 되어서야
            알았고, SPCX 는 그 상태로 증거금 420 을 15시간 묶었다.

            ⛔ **말랐다고 던지지 않는다.** 실측이 반대를 가리킨다:

            ```
            그때 시장가로 던졌다면   -420   (호가가 표시가에서 22% 아래였다)
            표시가 아래에서 기다렸더니 -74
            ```

            호가는 잠깐 얇아졌다 돌아오는 일이 흔하고, 그때마다 던지면 멀쩡한 포지션을
            매번 손해 보고 닫는다.

            ⇒ 자동으로 하는 것은 **더 묶이지 않게 하는 것뿐**이다 (`liquid = False`).
              나가는 값은 사람이 정한다 — 화면이 계산해 버튼으로 내놓는다.

            ⛔ **못 읽으면 막지 않는다** (§1.2.1). 조회 실패로 새 진입이 멎으면 그것도
            조용한 고장이다.
        """
        if self.observe_only:
            # ⛔ 주문을 안 내는 판이다 — 호가를 볼 이유가 없다.
            return
        while True:
            await asyncio.sleep(BOOK_TICK)
            with contextlib.suppress(Exception):
                await self.check_book()
            # ⭐ **같은 박자로 본다** — 예산 합도 계좌도 분 단위로 움직인다. 걸음마다
            #   재면 판 여섯에 DB·거래소 왕복이 그만큼 곱해진다.
            with contextlib.suppress(Exception):
                await self.check_funding()

    async def check_funding(self) -> None:
        """**판들의 예산 합이 계좌 안에 드는가** (사용자 신고 2026-08-21).

        Note:
            🔴 이 검사는 판을 **띄울 때만** 돌았다 (`_budget_room`). 실측:

            ```
            띄울 때    판 6개 x 예산 50 = 300  <  계좌 348   ✅ 통과
            두 시간 뒤  예산 합 300           >  계좌 278   🔴 넘었다
            ```

            예산은 그대로인데 **계좌가 줄었다.** 그때부터 새 주문마다 Gate 가
            `LIQUIDATE_IMMEDIATELY` 로 거절했다 — 실측 실패 3회.

            ⚠️ **`available` 이 아니라 총액으로 잰다.** 포지션에 들어간 돈은 사라진 돈이
            아니다 — 이 프로젝트가 반복해서 틀린 지점이다.

            ⛔ **예산을 자동으로 줄이지 않는다.** 어느 판을 깎을지는 사람이 정한다.
            ⛔ **못 읽으면 막지 않는다** (§1.2.1) — 조회 실패가 곧 정지가 되면 안 된다.
        """
        if self.observe_only or self._store is None:
            return
        if not isinstance(self._orders, MarginAware):
            return
        try:
            cash = Decimal(str((await self._orders.get_balance()).cash))
            total = cash + await self._orders.account_margin()
            taken = sum(
                (
                    Decimal(str(row["margin"]))
                    for row in await self._store.open_runs(live=True)
                    if row.get("margin")
                ),
                Decimal(0),
            )
        except Exception as exc:
            self._log.warning("live_funding_unreadable", payload={"error": str(exc)[:140]})
            return
        if total <= 0 or taken <= 0:
            return
        # ⭐ 허용치는 `config/risk.yml` 의 `funding_shortfall_tolerance_pct` (2026-09-06). 예산 합은
        #    띄울 때의 자본이고 계좌는 수수료·미실현 손익으로 매 봉 움직인다 — 자본을 전부 예산으로
        #    쓴 펀드가 첫 수수료 한 푼(0.0049)에 "0.00 많다" 로 전 판의 새 진입이 막혔다(실계좌
        #    실측). 판정은 decision 층의 몫이다.
        short = funding_shortfall(load_risk_settings(), budgets=taken, account=total)
        was = self._session.funded
        self._session.funded = short is None
        self.short_by = None if short is None else f"{short:.2f}"
        if was and not self._session.funded:
            # 🔴 **바뀌는 순간에만 크게 남긴다** — 걸음마다 쌓으면 진짜 사건이 묻힌다.
            self._fired("underfunded", f"예산 합 {taken:.2f} > 계좌 {total:.2f}")
            self._log.error(
                "live_underfunded",
                payload={
                    "budgets": f"{taken:.2f}",
                    "account": f"{total:.2f}",
                    "short_by": self.short_by,
                    "note": "새 진입을 막았다 — 어느 판의 예산을 줄일지는 사람이 정한다",
                },
            )
        elif not was and self._session.funded:
            self._log.info("live_funded_again", payload={"account": f"{total:.2f}"})

    async def check_book(self) -> None:
        """호가창을 한 번 재고 결과를 `dry` 에 담는다.

        Note:
            ⭐ **명목은 이 판이 실제로 굴리는 크기**다 — 예산 x 배율. 판마다 크기가
            다르므로 같은 호가창이라도 답이 다를 수 있다.

            ⚠️ **못 읽었으면 아무것도 안 바꾼다.** `read=False` 를 "말랐다" 로 읽으면
            네트워크가 흔들릴 때마다 새 진입이 멎는다.
        """
        book = self._session.ledger
        notional = book.sizing_base * book.leverage
        got = await probe_book(self._orders, self.instrument, notional)
        if not got.read:
            return
        was = self._session.liquid
        self._session.liquid = got.ok
        self.dry = (
            None
            if got.ok
            else {
                "why": got.why,
                "mark": str(got.mark),
                "bid": str(got.bid or ""),
                "bid_gap_pct": f"{got.bid_gap_pct:.2f}",
                "bid_depth": f"{got.bid_depth:.0f}",
                "note": "던지지 않는다 — 새 진입만 막았다. 나갈 값은 사람이 정한다",
            }
        )
        if was and not got.ok:
            # 🔴 **바뀌는 순간에만 크게 남긴다.** 5분마다 같은 줄을 쌓으면 진짜 사건이
            #    그 안에 묻힌다 (밤사이 `live_account_unreadable` 3979줄을 겪었다).
            self._fired("book_dry", got.why)
            self._log.error(
                "live_book_dry",
                payload={**(self.dry or {}), "symbol": self.instrument.symbol},
            )
        elif not was and got.ok:
            self._log.info(
                "live_book_wet",
                payload={"symbol": self.instrument.symbol, "note": "호가가 돌아왔다"},
            )

    async def _keep_probing(self) -> None:
        """**점검을 스스로 돈다** — 사람이 누를 때까지 기다리지 않는다.

        Note:
            🔴 사용자 지적 2026-08-18: *"난 봉 점검을 사람이 굳이 굳이 눌러서 점검해야
            하는 이유를 전혀 모르겠어."* 맞다. 점검은 *"봉이 흐르는가"* 를 묻는 일이고,
            그 답은 **사람이 궁금해하기 전에** 알고 있어야 한다.

            ⚠️ 버튼은 남긴다 — *"지금 당장 확인"* 은 여전히 필요하다. 다만 **누르기
            전에는 모른다** 는 상태가 없어진다.

            ⚠️ 주기를 길게 잡는다. 거래소에 REST 를 따로 때리는 일이라, 봉이 흐르는지
            보려고 봉보다 자주 물을 이유가 없다.
        """
        while True:
            await asyncio.sleep(PROBE_TICK)
            try:
                self.last_probe = await self.probe()
                # 🔴 **거래소가 먼저 채웠는지도 여기서 따라잡는다** (2026-09-06 사고 —
                #    4h 축에서 지정가가 봉 중간에 채워졌는데 원장이 4시간 동안 몰랐다).
                await self._absorb_between_bars()
                # ⭐ 펀딩 정산을 열린 매매에 (T226) — 5분마다 · 실패는 경고만.
                await self._sync_funding()
                # 🔴 **거래소가 먼저 닫았는지 여기서 따라잡는다** (사용자 지적
                #    2026-08-18: *"실제 주문은 이미 손절 난 상태야. (…) 화면에서는
                #    해당 포지션을 보유 중인걸로 보여"*).
                #
                #    청산 판정은 봉이 마감돼야 도는데 조건부 손절은 **가격이 닿는
                #    순간** 발동한다. 봉을 기다리면 최대 15분 동안 화면이 거짓말한다.
                #
                # ⚠️ 이것은 **판정이 아니라 사실 확인**이다 — 진입 판단은 여전히 봉
                #    마감에서만 돈다 (절대 규칙 #5).
                if await self.reconcile():
                    # 🔴 원장이 바뀌었다 — 봉 마감을 기다리면 최대 15분 동안 DB 가
                    #    보유중이라고 말한다.
                    await self._persist()
                # 🔴 **감사도 여기서 돈다** (T15-4). 걸음에서만 돌면 판정이 멈춘 순간
                #    감사도 같이 멈춘다 — 정지를 재는 항목이 정지에 같이 죽는다.
                #
                # ⚠️ 거짓 경보 수명도 짧아진다. 예전에는 잘못된 경보가 **15분 동안**
                #    화면에 붙어 있었다 (진입 축 간격이 곧 갱신 주기였다).
                await self._run_audit()
                # 🔴 **무방비를 봤으면 다음 걸음까지 기다리지 않는다** (2026-08-31).
                #
                #    감사는 30초 만에 알았는데 고치는 자리(`_guard_stop`)가 걸음 안에만
                #    있었고, 라이브 급전은 **진입축**(4h)으로 걸음을 준다 — 알고도
                #    2시간 12분을 무방비로 뒀다 (`_guard_stop` docstring 의 실측).
                #
                # ⭐ **감사 결과를 조건으로 쓴다.** 매 틱 거래소에 조건부를 물으면
                #    판 수만큼 왕복이 늘고, 그 목록은 감사가 방금 읽었다 (항목 ⑤) —
                #    같은 사실을 두 번 묻지 않는다.
                #
                # ⛔ **고치는 것은 감사가 아니라 이 루프다.** 감사는 보는 일만 한다
                #    (`audit` docstring) — 섞으면 점검이 상태를 바꿔서 다음에 무엇이
                #    원인이었는지 알 수 없게 된다.
                if any(item["code"] == "stop_missing" for item in self.findings):
                    await self._guard_stop(escalate=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.warning("live_probe_failed", payload={"error": str(exc)[:140]})

    async def _consume(self) -> None:
        """스트림 한 번 — 끊기면 반환한다 (`run` 이 다시 부른다)."""
        async for item in self._stream.stream():
            moved = self._feed.push(self.entry, item.candle, closed=item.closed)
            if not moved:
                continue
            if self._feed.gaps > 0:
                await self._heal()
            # ⛔ **한 걸음이 터져도 러너는 산다.** 판정 하나가 예외를 내면 예전에는
            #    루프가 통째로 끝났다 — 봉 하나 때문에 판 전체가 조용히 멈추는 것은
            #    가장 나쁜 실패다 (절대 규칙 #8).
            try:
                await self._one_step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures += 1
                self.last_error = f"판정: {exc}"[:200]
                self._log.error(
                    "live_runner_step_failed",
                    payload={
                        "error": str(exc)[:200],
                        "failures": self.failures,
                        "note": "이 봉은 건너뛴다 — 다음 봉에서 계속한다",
                    },
                )

    def price_drift(self) -> str:
        """**진입가 축이 판정 축보다 뒤처졌는가** (T15-3).

        Returns:
            뒤처졌으면 사람이 읽는 이유, 아니면 빈 문자열.

        Note:
            🔴 **한 계획 안에 두 시점이 섞이는 것을 막는다.** 계획(손절·사다리)은 진입
            축(15m) 가격으로 서고, 원장에 적히는 진입가는 `STEP_FRAME`(5m) 종가다. 두
            축이 벌어지면 **익절이 진입 아래**로 갈 수 있다. 실측 (2026-08-18):

            ```
            기록 진입   64441.7    ← 9시간 전 5분봉 종가 (그 축이 동결됐다)
            1차 익절    64425.2    ← 15분봉 현재가 기준으로 계산
            reach       -16.5      ← 음수. 이 사다리는 나오면 안 됐다
            ```

            4건이 그 계획으로 나가 원장에 `-4.48%` 로 적혔다. 라벨은 `반익반본` 인데
            1차 익절이 손실 가격이었으므로 **익절 라벨이 붙은 손실**이다.

            ⛔ **판정을 미룬다, 버리지 않는다.** 봉은 급전에 남아 있고 `_arrived` 도 켜져
            있으므로, 축이 따라잡으면 다음 걸음이 그 봉을 판정한다.

            ⚠️ **백테스트에서는 늘 빈 문자열이다.** 모든 축이 미리 적재돼 있어 두 축의
            마감 시각이 같다 — 그래서 이 가드는 골든 테스트를 흔들지 않는다.
        """
        price_rows = self._feed.observed(self.price_frame)
        judge_rows = self._feed.observed(self.entry)
        if not price_rows:
            return f"{self.price_frame.value} 봉이 없다 — 진입가를 정할 축이 비었다"
        if not judge_rows:
            return ""
        price_closed = price_rows[-1].ts + timedelta(seconds=interval_seconds(self.price_frame))
        judge_closed = judge_rows[-1].ts + timedelta(seconds=interval_seconds(self.entry))
        behind = (judge_closed - price_closed).total_seconds()
        # ⚠️ 한 진입 축 간격까지는 정상이다 — 두 축의 마감이 정확히 겹치지 않는다.
        if behind <= interval_seconds(self.entry):
            return ""
        return (
            f"{self.price_frame.value}(진입가 축)가 {self.entry.value}(판정 축)보다 "
            f"{int(behind)}초 뒤처졌다 — 이 상태로 판정하면 계획과 진입가가 "
            f"다른 시점에서 나온다 (2026-08-18 에 4건이 그렇게 나갔다)"
        )

    async def _one_step(self) -> None:
        """봉 하나를 판정하고 주문까지 낸다 — **한 번에 하나만 돈다**.

        Note:
            🔴 **겹쳐 들면 같은 사다리가 두 번 나간다** (사용자 신고 2026-08-21).

            이 함수는 두 곳에서 불린다 — `_chase_trigger`(1초마다)와 `_loop`(봉마다).
            그것은 의도된 설계다 (원칙 P3: 판정 경로는 하나). 그런데 안에서 네트워크를
            여러 번 기다리므로, 한쪽이 도는 중에 다른 쪽이 **그대로 들어왔다.**

            우편함이 그 겹침을 못 막는다 — `to_place()` 는 대기 목록을 **읽기만** 하고
            지우는 것은 `sent()` 다. 둘이 같은 목록을 읽고 둘 다 보낸다:

            ```
            02:15:05.763  숏 -599  en-1
            02:15:05.764  숏 -599  en-1   ← 1ms 뒤 같은 다리
            02:15:06.210  숏 -599  en-0
            02:15:06.210  숏 -599  en-0   ← 0.1ms 뒤 같은 다리
                          합계 -2396      (계획은 -1198)
            ```

            **예산의 두 배가 나갔고, 청산은 원장이 아는 절반만 닫아 나머지가 유령이
            됐다.** 실측: 진입 다리 114개 중 **10개**가 그렇게 겹쳤다.

            ⚠️ 어젯밤 `REDUCE_ONLY_FAIL`(`pending order == 포지션 전량`)도 같은 뿌리다 —
            익절도 두 번 걸렸다.

            ⭐ **기다리지 않고 건너뛴다.** 줄을 세우면 밀린 걸음이 뒤늦게 옛 값으로
            판정한다 — 방아쇠는 1초 뒤에 어차피 다시 온다.
        """
        # ⛔ **`locked()` 와 `acquire()` 사이에 await 가 없다** — 그래서 이 검사는
        #    asyncio 에서 안전하다. 사이에 다른 코드를 넣으면 그 순간 다시 깨진다.
        if self._stepping.locked():
            self.overlaps += 1
            return
        async with self._stepping:
            await self._walk_once()

    async def _absorb_between_bars(self) -> None:
        """봉 사이에 **대기 지정가의 체결을 원장에 옮긴다** — 30초 점검에서 부른다.

        Note:
            🔴 2026-09-06 실계좌 사고(`docs/incidents/2026-09-06_limit_fill_between_bars.md`):
            NEAR 지정가가 12:04 에 채워졌는데 판정 축이 4h 라 체결 확인(`_pump_fills`)·
            원장 기록(`Session._collect`)이 16:00 까지 안 돌았다. 그 4시간 동안 **조건부
            손절이 없었고** 대조·감사는 고아 경보를 냈다.

            ⚠️ **판정이 아니라 사실 확인이다** (절대 규칙 #5). 우편함을 채우고
            (`_pump_fills`), 전량 체결이면 세션이 원장에 적고(`absorb_fills`), 그 즉시
            손절을 건다(`_guard_stop`). 계획을 접을지는 여전히 봉 마감이 정한다.

            ⛔ 걸음과 **같은 자물쇠**(`_stepping`) 안에서 돈다 — 걸음 도중에 우편함을
            건드리면 같은 체결을 두 번 세거나 `_collect` 와 경합한다. 걸음이 돌고 있으면
            건너뛴다 (30초 뒤에 다시 온다).
        """
        box = self._mailbox
        if box is None or self._session.waiting_trade is None:
            return
        # ⛔ **`resting()` 으로 문을 잠그지 않는다** (1.0.2 · 2026-09-06 실측). 블루그린 복원의
        #    "체결로" 경로(`restore_pending`)는 표를 우편함에 **이미 넣어 둔다** — 그 순간
        #    resting 은 비어 있고 체결은 우편함 안에 있다. resting 을 조건으로 걸면 바로 그
        #    체결을 영원히 못 옮긴다 (배포 뒤 3분 동안 NEAR 손절이 안 걸렸다).
        #    `_pump_fills` 는 스스로 resting 이 없으면 돌아가므로 여기서 거를 이유가 없다.
        # ⛔ `locked()` 와 `acquire()` 사이에 await 가 없다 (`_one_step` 과 같은 이유).
        if self._stepping.locked():
            return
        async with self._stepping:
            await self._pump_fills()
            # ⭐ 진입 시각은 **흡수한 지금**이다 — 마지막 마감 봉은 재시작 직후 몇 시간 전 것일
            #    수 있다 (1.0.2 실측: 12:04 체결이 11:55 로 적혔다). 결정론 코어는 시계를
            #    못 보므로 러너가 준다.
            if not self._session.absorb_fills(datetime.now(UTC)):
                await self._persist_pending()
                return
            opened = self._session.position
            self._log.info(
                "live_fill_absorbed",
                payload={
                    "symbol": self.instrument.symbol,
                    "trade_id": opened.trade_id if opened else None,
                    "entry": str(opened.entry) if opened else None,
                    "note": "봉 사이에 지정가가 전량 채워졌다 — 원장에 적고 손절을 건다",
                },
            )
            await self._persist_pending()
            await self._guard_stop()
            await self._persist()

    async def _sync_funding(self) -> None:
        """거래소가 뗀 펀딩 정산을 열린 매매에 붙인다 (T226 · 사용자 2026-09-07).

        Note:
            자금 원장(`account_book`)의 `fund`/`FUNDING_FEE` 행 중 이 종목 · 열린 매매 구간 ·
            아직 안 붙인 것만. 명목(`position_snapshot.value`)으로 나눠 비율도 적는다 —
            `gain_pct` 가 `cost_pct` 처럼 뺀다.
            ⛔ 세션의 모형 정산(`model_funding`)은 라이브에서 꺼져 있다 — 켜 두면 두 번 낸다.
            조회 실패는 경고만 — 펀딩 표기는 리스크 감소 행동이 아니다 (§1.2.1).
        """
        held = self._session.position
        book = getattr(self._orders, "account_book", None)
        if held is None or book is None:
            return
        now = time.monotonic()
        if now - self._last_funding_at < FUNDING_SYNC_INTERVAL:
            return
        self._last_funding_at = now
        try:
            rows = cast("list[dict[str, str]]", await book(limit=100))
        except Exception as exc:
            self._log.warning("live_funding_unreadable", payload={"error": str(exc)[:140]})
            return
        # 열쇠는 매매와 함께 저장된다 (0114) — 재시작 뒤 첫 동기화는 저장된 열쇠로 시작한다.
        #   메모리 집합만 믿던 때는 블루그린 승격마다 열린 구간의 정산이 전부 다시 붙었다
        #   (2026-09-08 실측: 원장이 거래소 합의 2.7배).
        legacy = False
        if not self._funding_rows_seen:
            if held.funding_keys:
                self._funding_rows_seen = set(held.funding_keys)
            elif held.funding_paid != 0:
                # 열쇠 없이 누적만 있는 옛 매매 — 부푼 값을 버리고 거래소 합으로 다시 맞춘다.
                legacy = True
        fresh, self._funding_rows_seen = attribute_funding(
            rows,
            symbol=self.instrument.symbol,
            opened_at=held.opened_at or held.placed_at,
            closed_at=None,
            seen=self._funding_rows_seen,
        )
        if not fresh:
            return
        paid = sum((-row.change for row in fresh), Decimal(0))
        notional = Decimal(0)
        if isinstance(self._orders, PositionAware):
            try:
                snap = await self._orders.position_snapshot(self.instrument)
                notional = abs(Decimal(str(snap.get("value") or "0")))
                if notional == 0:
                    size = abs(Decimal(str(snap.get("size") or "0")))
                    price = Decimal(str(snap.get("mark_price") or snap.get("entry_price") or "0"))
                    notional = size * price
            except Exception:
                notional = Decimal(0)
        pct = paid / notional if notional > 0 else Decimal(0)
        before = held.funding_paid
        self._session.apply_funding(
            paid=paid, pct=pct, keys=tuple(row.key for row in fresh), reset=legacy
        )
        self._log.info(
            "live_funding_synced",
            payload={
                "symbol": self.instrument.symbol,
                "trade_id": held.trade_id,
                "settlements": len(fresh),
                "paid": str(paid),
                "pct": str(pct),
                "reset_from": str(before) if legacy else None,
                "note": "거래소 펀딩 정산을 열린 매매에 붙였다 — 원장 손익이 이만큼 준다",
            },
        )
        await self._persist()

    async def _walk_once(self) -> None:
        """걸음 한 번의 본체 — **`_one_step` 만 부른다** (겹침 방어가 거기 있다)."""
        # 🔴 **체결 판정용 봉을 먼저 채운다.** `Session._tick()` 은 `STEP_FRAME`(5m) 의
        #    마지막 봉으로 **진입가와 체결·청산**을 정하는데, 라이브에서 그 축은 웹소켓이
        #    구독하지 않아 **시드 이후 한 번도 갱신되지 않았다.**
        #
        #    죽은 봉 하나로 4시간 동안 판정한 결과 (2026-08-18 실측 · 4건 전부):
        #
        #      진입가  64441.7   네 건 **전부 동일** (죽은 봉의 종가)
        #      시각    23:40     네 건 **전부 동일** · 진입과 청산이 같은 순간
        #      1차익절 64425.2   진입보다 **아래** — 롱인데 손실 방향
        #      planned_rr = null · 네 건 모두 손실 · 합계 -4.48%
        #
        #    계획(15m)은 살아 있는 값으로 서고 진입가는 죽은 5m 종가로 적히니, 한 계획
        #    안에서 **두 시점이 섞였다.** 익절이 진입 아래로 간 것이 그 결과다.
        #
        # ⛔ 화면이 보는 축과 **다르다.** 이 축은 판정에 필요하므로 사람이 보든 말든 매
        #   걸음 채운다 — `refresh()` 의 TTL(간격의 절반)이 과한 조회를 막는다.
        # 🔴 **돈이 없으면 새 진입을 안 한다** (T14-2 · 사용자 요구: *"포지션 금액이
        #    다 떨어지면 매매가 멈춘다"*). 밖에서 넣었다고 가정하지 않는다.
        #
        # ⚠️ **판을 죽이지는 않는다.** 보유 중인 포지션은 손절·반익이 계속 관리해야
        #    하고, 그것이 끝나면 증거금이 돌아와 다시 돌 수 있다 (§1.2.1 — 리스크를
        #    줄이는 행동은 막지 않는다).
        if self._session.ledger.halted_at and self._session.auto:
            self._session.auto = False
            self._log.error(
                "live_margin_exhausted",
                payload={
                    "halted_at": self._session.ledger.halted_at,
                    "note": "새 진입을 멈춘다 — 보유 포지션 관리는 계속한다",
                },
            )
        # 🔴 **T22 브레이커** — 고점 대비 낙폭이 문턱에 닿으면 **새 진입만** 멈춘다.
        #    손절·반익·스탑 상향은 계속한다 (§1.2.1 — 리스크를 줄이는 행동은 막지
        #    않는다). ⛔ 자동 재개 없음 — 사람이 보고 `auto` 를 다시 켠다.
        book = self._session.ledger
        if book.tripped_at and self._session.auto:
            self._session.auto = False
            self._log.error(
                "live_breaker_tripped",
                payload={
                    "tripped_at": book.tripped_at,
                    "drawdown_pct": str(book.drawdown_pct),
                    "stop_pct": str(book.drawdown_stop_pct),
                    "note": "낙폭 브레이커 — 새 진입을 멈춘다. 보유 포지션 관리는 계속한다",
                },
            )
        await self.refresh(self.price_frame)
        # 🔴 **채웠는데도 뒤처져 있으면 판정하지 않는다** (T15-3). 위 `refresh` 가 조용히
        #    실패했을 수 있고, 그 상태로 판정하면 계획과 진입가가 다른 시점에서 나온다.
        #
        # ⛔ 봉은 버리지 않는다 — 급전에 남아 있으므로 축이 따라잡으면 다음 걸음이 본다.
        drift = self.price_drift()
        if drift:
            self.failures += 1
            self.last_error = f"진입가 축 지연으로 판정을 미뤘다: {drift}"[:200]
            self._log.error(
                "live_price_frame_behind",
                payload={
                    "detail": drift,
                    "note": "판정을 미뤘다 — 봉은 남아 있고 축이 따라잡으면 다시 본다",
                },
            )
            return
        # 🔴 **판정 전에 우편함을 채운다** (T19 ⑤). 세션은 동기라 네트워크를 못
        #    기다린다 — 여기서 거래소에 묻고 넣어 둬야 그 걸음이 채워진 것을 본다.
        await self._pump_fills()
        # 🔴 펀딩 요율 주입 (0.8.1) — 세션은 네트워크를 모르므로 여기서 넣는다.
        #    실패는 None = 게이트 잠듦 (보수 방향이며 로그는 남긴다).
        try:
            self._session.recent_funding = await self._quotes.funding_rate(self.instrument)
            # 🔴 교정 원장 (T185 ⑤) — **정산 시점 오차**를 재려면 우리가 언제 무엇을
            #    봤는지가 남아야 한다. 백테스트는 8시간 경계에 정확히 문다고 가정하는데
            #    거래소가 실제로 언제 무는지는 안 재봤다.
            #
            #    ⚠️ 여기 남는 것은 **요율**이지 정산액이 아니다. 정산액은 거래소
            #      계좌원장 API(Gate `/futures/{settle}/account_book?type=fund`)를
            #      따로 불러야 하고, 그것은 어댑터 추가라 별도 건이다. 요율 + 명목 +
            #      관측 시각이면 **시점**은 답이 나온다.
            await self._note_funding()
        except Exception as exc:
            self._session.recent_funding = None
            self._log.warning("live_funding_unreadable", payload={"error": str(exc)[:120]})
        await self._inject_ref_regime()
        before = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        shot = self._session.step()
        if shot is None:
            # 세션이 멈춰 있다(일시정지). 봉은 이미 급전에 들어갔으므로 잃지 않는다.
            return
        self.steps += 1
        # ⭐ **판정 정지 기준선** (T15-4). 이 값이 뒤처지기 시작하면 봉은 흐르는데
        #    판정이 안 도는 것이다.
        self._bars_at_step = len(self._feed.observed(self.entry))
        await self._place()
        # 🔴 **부탁받은 지정가를 보낸다** (T19 ⑤). 판정 뒤라야 이 걸음에서 새로 생긴
        #    부탁까지 나간다.
        await self._pump_orders()
        # ⭐ 대기 계획을 DB 에 남긴다 (T218) — 다음에 판이 다시 떠도 걸린 표를 이어받게.
        await self._persist_pending()
        # 🔴 **채워진 만큼 손절을 다시 건다** (T19 · 사다리의 가장 위험한 자리).
        await self._resize_stop()
        # 🔴 **원장이 반익했으면 거래소에서도 던다** (사고 ③-b).
        await self._apply_half()
        # 🔴 **원장이 신호로 전량 닫았으면 거래소에서도 닫는다** (0.7.0 · ADX 약화 청산).
        await self._apply_exit(before)
        # 🔴 **못 건 익절을 다시 건다.** 안 하면 포지션은 열려 있는데 목표에 닿아도
        #    아무 일이 안 일어난다.
        await self._retry_ladders()
        # 🔴 **손절을 먼저 걸고, 그 다음에 점검한다** (순서가 뒤집혀 있었다).
        #
        #    진입한 그 걸음에서 감사가 손절보다 **먼저** 돌아서, 아직 걸리지도 않은
        #    손절을 "0건 — 무방비다" 로 보고했다. 실측 (2026-08-18 22:45:01):
        #
        #      22:45:01.???  live_runner_sizing        진입 주문
        #      22:45:01.???  live_audit_found          stop_missing ← 거짓
        #      22:45:01.698  gate_stop_placed          64265.0 · 숏 · rule 1
        #
        #    거래소에 물어보면 손절은 멀쩡히 걸려 있었다. 그런데 감사는 **다음 15분봉**
        #    까지 다시 안 돌므로, 그 거짓 경보가 15분 동안 화면에 붙어 있었다.
        #
        # ⛔ **거짓 경보는 무해하지 않다.** 이 화면의 경보는 *"판정을 믿지 말라"* 는
        #    뜻이고, 그것이 자주 틀리면 진짜일 때도 안 믿게 된다.
        #
        # 🔴 조건부 주문은 24시간에 만료되고 그 소멸이 **조용하다** — 걸었다는 기억은
        #    만료를 모르므로 매 걸음 거래소에 다시 묻는다.
        # 🔴 **거래소가 먼저 닫았을 수 있다** — 손절을 다시 걸기 전에 확인한다.
        await self.reconcile()
        # 🔴 **메이커 청산이 안 채워졌으면 시장가로 마무리한다** (T126 · 1.1.0).
        #    reconcile 뒤라야 거래소의 진짜 잔량을 보고 판단한다.
        await self._expire_maker_exit()
        # 🔴 **보유 계약을 지금 자본의 목표 노출로 되맞춘다** (0.8.0 재레버).
        await self._resize_position()
        await self._guard_stop()
        # 🔴 **걸음마다 스스로 점검한다** (사용자 요구 2026-08-18). 오늘 나온 버그가
        #    전부 같은 모양이었다 — 무언가 조용히 얼어 있고 아무도 모른다.
        await self._run_audit()
        # 🔴 **원장을 DB 에 남긴다** (T16 ②). 여기서 안 남기면 다음 리로드에 이 걸음의
        #    판정이 통째로 사라지고, 거래소 포지션만 관리자 없이 남는다.
        await self._persist()

    async def exchange(self) -> dict[str, object]:
        """거래소 잔고·포지션을 읽는다 — **사실**이다.

        Returns:
            `{available, position_margin, position: {...}}`. 못 읽으면 `{}`.

        Note:
            🔴 **원장(`Ledger`)과 다른 값이다.** 원장은 손익률을 곱해 나가는 모형이고
            이것은 계좌다. 둘을 나란히 두는 이유는 갈리는 순간이 반드시 오기 때문이다 —
            반올림 드리프트·수수료·펀딩·부분체결이 전부 원장에 없다.

            ⚠️ **판정에 쓰지 않는다.** 표시용이다. 여기 값을 수량 산정에 끌어들이면
            결정론이 깨진다 (절대 규칙 #5) — 같은 봉에 계좌 상태에 따라 다른 주문이 난다.
        """
        balance = await self._orders.get_balance()
        held: dict[str, str] = {}
        if isinstance(self._orders, PositionAware):
            held = await self._orders.position_snapshot(self.instrument)
        # 🔴 **계좌의 `position_margin` 은 0 으로 온다** (2026-08-18 실측). 포지션이
        #    `margin=500.85` 로 열려 있는데도 계좌 요약은 0 이었다 — 격리 마진 포지션을
        #    계좌 수준에서 세지 않는 것으로 보인다.
        #
        #    ⇒ **포지션이 말하는 값을 진짜로 쓴다.** 계좌 요약을 믿으면 화면이
        #      *"증거금 0"* 이라고 적고, 사용자는 돈이 안 들어간 줄 안다 (실제로 그랬다).
        #
        # ⚠️ 계좌 값도 함께 낸다 — 둘이 다른 것 자체가 정보이고, 감추면 다음에 또 헷갈린다.
        locked = held.get("margin") or str(balance.positions_value)
        return {
            "available": str(balance.cash),
            "position_margin": locked,
            "account_position_margin": str(balance.positions_value),
            "broker": balance.broker,
            "position": held,
        }

    async def close_all(self) -> dict[str, object]:
        """열린 포지션을 **닫고** 조건부·줄이는 주문을 거둔다 (RUN 삭제 시).

        Returns:
            `{closed, contracts, swept}`. 포지션이 없으면 `closed=False` 지만
            **거두기는 그래도 돈다**.

        Note:
            🔴 **RUN 을 지우면 포지션이 남는다 — 그것을 막는다** (사용자 질문 2026-08-18:
            *"내가 RUN 을 지웠을 때, 해당 포지션 증거금이 어떤식으로 동작하는 지"*).

            예전에는 러너만 취소하고 끝났다. 그러면 거래소에 **아무도 관리하지 않는
            포지션**이 남는다 — 조건부 손절은 24시간에 만료되므로 그 뒤로는 손절도 없다.
            증거금은 계속 잡혀 있고, 다음 RUN 이 같은 종목이면 그 포지션에 **얹힌다**.

            🔴 **그런데 주문은 계속 남고 있었다** (사용자 신고 2026-08-21: *"내가 xrp
            삭제한건데, 왜 포지션이 남아있지?"*). 이 함수는 문서에 *"조건부 주문을
            거둔다"* 라고 적고 반환값에 `stops_cancelled` 까지 두면서 **본문에 거두는
            코드가 없었다.** 문서가 처리한다고 말하니 아무도 다시 안 봤다 — 가장 나쁜
            종류의 결함이다.

            그리고 포지션이 없으면 그 앞에서 빠져나갔다. XRP 는 이미 손절로 닫힌
            뒤였고, 그래서 조건부만 고아로 남았다 — **"닫을 포지션이 없다" 와 "치울 것이
            없다" 는 다르다.**

            ⭐ **닫기가 먼저, 거두기가 나중이다** (§1.2.1). 조건부를 먼저 거두면 청산이
            실패했을 때 **무방비 포지션**이 된다. 이 순서면 최악이 *"이미 닫힌 것에
            트리거가 남았다"*(무해)이고, 뒤집으면 *"손절 없는 포지션"*(치명)이 된다.

            ⚠️ 실패해도 예외를 밖으로 내지 않는다 — 삭제는 끝나야 한다. 대신 결과를
            돌려주고 로그에 남긴다. 못 닫았으면 사람이 거래소에서 닫아야 한다 (규칙 #8).
        """
        result: dict[str, object] = {"closed": False, "contracts": 0, "swept": 0}
        if not isinstance(self._orders, PositionAware):
            return result
        held = await self._orders.position_snapshot(self.instrument)
        size = held_size(held)
        record = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if size != 0 and record is None:
            # 원장에 보유 기록이 없는데 거래소에는 있다 — 유령의 반대 경우다.
            # ⛔ 닫지 못하므로 **거두지도 않는다** — 그 조건부가 유일한 보호막이다.
            self._log.warning(
                "live_close_without_record",
                payload={"contracts": size, "note": "거래소에만 있는 포지션 — 사람이 확인한다"},
            )
            return result
        if size != 0 and record is not None:
            done = await self._orders.submit_order(
                close_order(record, self.instrument, abs(size), run=self._run_key, revision=0)
            )
            result["closed"] = True
            result["contracts"] = abs(size)
            result["status"] = done.status.value
            self._log.info(
                "live_position_closed",
                payload={"contracts": abs(size), "status": done.status.value, "reason": "RUN 삭제"},
            )
        # ⭐ **포지션이 없어도 여기까지 온다** — 이 한 줄이 XRP 조건부를 남겼던 자리다.
        #   `sweep` 이 스스로 포지션을 다시 확인하므로 위에서 못 닫았으면 아무것도 안 거둔다.
        result["swept"] = len(await sweep(self._orders, self.instrument, why="RUN 삭제"))
        return result

    def watch(self, frame: Timeframe) -> None:
        """이 축을 **누가 보고 있다** — 화면이 부를 때마다 알린다.

        Args:
            frame: 화면이 지금 그리는 축.

        Note:
            🔴 알림이 없으면 그 축은 곧 식는다 (`WATCH_TTL`). 그것이 요점이다 —
            아무도 안 보는 축을 데우는 데 요율 한도를 쓰고 있었다.

            ⚠️ **판정 축은 이 목록과 무관하게 언제나 데운다** (`_needs`). 화면을 닫으면
            판정이 멈추는 일이 있어서는 안 된다.
        """
        self._watched[frame] = time.monotonic()

    def _needs(self, frame: Timeframe) -> bool:
        """이 축을 지금 데워야 하나.

        Args:
            frame: 시간축.

        Returns:
            판정에 쓰이거나, 최근에 누가 봤으면 참.

        Note:
            🔴 **판정에 쓰이는 축은 무조건 참이다.** 걸음(STEP_FRAME)·진입·방아쇠·
            가격 축이 그것이고, 이 넷이 식으면 매매가 멈춘다 — 화면을 닫았다고 판정이
            멈추면 그것이 사고다 (§1.2.1 의 정신).

            ⚠️ 나머지(보기용)는 **마지막으로 본 지 `WATCH_TTL` 안**일 때만 데운다.
            사람이 탭을 닫거나 다른 축으로 옮기면 조용히 식는다.
        """
        if frame in {STEP_FRAME, self.entry, self.price_frame}:
            return True
        seen = self._watched.get(frame)
        return seen is not None and time.monotonic() - seen < WATCH_TTL

    async def refresh(self, frame: Timeframe) -> int:
        """**보고 있는 축**을 거래소에서 새로 받아 채운다.

        Args:
            frame: 갱신할 시간축.

        Returns:
            새로 들어간 봉 수. 이미 최신이면 0.

        Note:
            🔴 **웹소켓은 진입 축 하나만 구독한다.** 그래서 나머지 축은 시드 때 한 번
            받고 **영원히 얼어 있었다** (사용자 신고 2026-08-18: *"타임라인 10초봉이 뭐
            그냥 안움직이는데?"*).

            15m 은 진입 축이라 살아 있어서 눈에 안 띄었다. 10초봉은 10초마다 봉이
            바뀌어야 하므로 **얼어 있는 것이 즉시 보였다.**

            ⚠️ 축마다 웹소켓을 더 구독하지 않는다. 9개 축을 다 구독하면 10초봉이 초당
            수십 개 프레임을 밀어 넣고, 그 대부분은 **아무도 안 보는 축**이다.
            ⇒ **보고 있는 축만, 요청이 올 때** 채운다.

            ⛔ **진행 중 봉은 넣지 않는다.** 마감 안 된 봉이 급전에 들어가면 판정이 그것을
            보고, 같은 입력에 다른 출력이 난다 (절대 규칙 #5). 그래서 화면은 *"마지막
            마감 봉"* 까지만 본다 — 꼬리가 실시간으로 흔들리지 않는 이유가 이것이다.

            ⚠️ 진입 축은 여기서 만지지 않는다. 웹소켓이 이미 채우고 있고, 두 경로가 같은
            축을 밀면 커서가 어느 쪽 것인지 모르게 된다.
        """
        if frame is self.entry:
            return 0
        # 🔴 **그 축의 간격보다 자주 조회하지 않는다.** 화면은 `/state` 를 700ms 마다
        #    치는데, 그때마다 거래소에 물으면 10초봉 하나를 보는 동안 초당 한 번씩
        #    REST 를 때린다 — 새 봉은 10초에 하나뿐인데 14번은 헛걸음이고, 그러다
        #    레이트리밋에 걸리면 **판정용 조회까지 같이 막힌다.**
        #
        # 🔴 **간격의 1/10 이다** (사용자 지적 2026-08-18: *"10초봉을 보러 갔는데 최신봉이
        #    15초 전이면 어떻게 해"*).
        #
        #    절반(5초)으로 뒀더니 최악의 경우 나이가 **봉 간격 + TTL = 15초**까지 갔다 —
        #    10초봉인데 15초 전 봉을 보는 것은 그 축을 보는 뜻이 없다.
        #
        #    ⇒ 나이 상한이 **간격의 1.1배**가 되게 잡는다:
        #
        #        10s  → 1초    (최신 봉이 최대 11초 전)
        #        1m   → 6초
        #        15m  → 90초
        #
        # ⚠️ 하한 1초는 남긴다. 화면이 1.5초마다 당기므로 그보다 짧게 잡을 이유가 없고,
        #    렌더 한 번에 여러 번 부르는 경우를 막는다.
        seconds = interval_seconds(frame)
        ttl = max(1.0, seconds / 10)
        now_mono = time.monotonic()
        last = self._refreshed.get(frame)
        if last is not None and now_mono - last < ttl:
            return 0
        # 🔴 **이미 받은 봉을 다시 받지 않는다** (2026-08-29 요율 제한 사고).
        #
        #    예전에는 새로고침마다 **400봉**을 통째로 받았다. 그런데 지난번 조회 뒤로
        #    새로 닫힌 봉은 보통 **한두 개**다 — 나머지 398 개는 이미 급전에 있고,
        #    `backfill` 이 어차피 겹치는 것을 버린다.
        #
        #    실측(6분 · BINANCE): `klines` 647회 중 1m 이 354회. 판 6개가 각자 6초마다
        #    400봉을 받고 있었다. 그 무게가 한도를 갉아 IP 밴으로 이어졌다.
        #
        # ⚠️ **끊겼다 돌아오면 넓게 받는다.** 좁은 창만 고집하면 그 사이 구멍이 영영
        #    안 메워지고, 구멍 난 시리즈로 낸 판정은 조용히 틀린다 (규칙 #8).
        #    판단은 **벽시계 경과**로 한다 — 급전 내부를 안 들여다봐도 자가 교정된다.
        gone = 0.0 if last is None else now_mono - last
        wide = last is None or gone > seconds * WARM_BARS
        self._refreshed[frame] = now_mono
        span = timedelta(seconds=seconds)
        now = datetime.now(UTC)
        bars = 400 if wide else WARM_BARS
        rows = await self._quotes.get_candles(self.instrument, frame, now - span * bars, now)
        if not rows:
            return 0
        # ⛔ 마지막은 **진행 중**이다 — 버린다.
        closed = rows[:-1]
        if not closed:
            return 0
        return self._feed.backfill(frame, closed)

    async def forming(self, frame: Timeframe) -> Candle | None:
        """**지금 만들어지고 있는 봉** — 화면 전용 (사용자 요구 2026-08-18).

        Args:
            frame: 보고 있는 시간축.

        Returns:
            미마감 봉. 아직 못 받았으면 None.

        Note:
            🔴 사용자 요구: *"주가창에서 꼬리 위아래로 왔다갔다 하는 거, 10초 축으로
            실제 호가를 보고 싶다."* 지금 화면은 **마감된 봉까지만** 그린다 —
            `refresh()` 가 `rows[:-1]` 로 진행 중 봉을 버리기 때문이고, 그것이
            꼬리가 안 흔들리는 이유다.

            ⛔ **급전에 넣지 않는다.** 미마감 봉이 급전에 들어가면 판정이 그것을 보고,
            같은 상황에서 매 틱 다른 답이 난다 (절대 규칙 #5). 이 함수는 값을
            **돌려주기만** 하고 `_feed` 를 건드리지 않는다 — 그래서 판정 경로와
            물리적으로 갈라져 있다.

            🔴 **TTL 을 반드시 둔다.** 화면이 초당 여러 번 부를 수 있는데 그때마다
            거래소에 물으면 레이트리밋에 걸리고, 그러면 **판정용 조회까지 같이
            막힌다** (`refresh()` 주석과 같은 이유다).

            ⚠️ 진입 축은 웹소켓이 이미 미마감 봉을 주므로 그것을 쓴다 — 같은 축을
            REST 로 또 당길 이유가 없다.
        """
        held = self._feed.pending(frame)
        if held is not None:
            return held
        now_mono = time.monotonic()
        cached = self._forming.get(frame)
        if cached is not None and now_mono - cached[0] < FORMING_TTL:
            return cached[1]
        span = timedelta(seconds=interval_seconds(frame))
        now = datetime.now(UTC)
        try:
            rows = await self._quotes.get_candles(self.instrument, frame, now - span * 3, now)
        except Exception as exc:  # 화면용이라 어떤 실패도 판정을 막지 않는다
            self._log.warning(
                "live_forming_failed",
                payload={"timeframe": frame.value, "error": str(exc)[:140]},
            )
            self._forming[frame] = (now_mono, None)
            return None
        # ⭐ **마지막이 진행 중인 봉이다** — `refresh()` 가 버리는 바로 그것을 여기서 쓴다.
        latest = rows[-1] if rows else None
        # ⚠️ 이미 마감된 봉이면 진행 중이 아니다. 거래소가 새 봉을 아직 안 열었을 때
        #    그 값을 "지금"으로 그리면 화면이 한 봉 과거를 현재로 말한다.
        if latest is not None and latest.ts + span <= now:
            latest = None
        self._forming[frame] = (now_mono, latest)
        return latest

    async def frame_ages(self) -> dict[str, float]:
        """축마다 **마지막 봉이 몇 초 됐는지** — 화면이 신선도를 보여 줄 수 있게.

        Returns:
            `{시간축: 초}`. 봉이 없으면 그 축은 빠진다.

        Note:
            🔴 사용자 요구 2026-08-18: *"사용자가 갱신이 되고 있는지 알 수 있으면 참
            좋을텐데."* 얼어 있는 축과 **원래 느린 축**은 화면상 구별되지 않는다 —
            1d 봉이 6시간째 그대로인 것은 정상이고, 10s 봉이 6분째 그대로면 고장이다.

            ⇒ 나이를 **그 축의 간격으로 나눈 배수**로 읽으면 둘이 갈린다. 화면이 그
            판단을 한다.
        """
        now = datetime.now(UTC)
        out: dict[str, float] = {}
        for frame in self._feed.timeframes:
            # 🔴 **화면용 보기로 잰다.** `view()` 는 커서까지만 주는데 커서는 진입 축
            #    봉이 마감될 때만 움직인다 — 그것으로 재면 10초봉이 15분째 그대로라고
            #    보고하게 되고, 실제로 그런 오진을 냈다 (2026-08-18).
            rows = self._feed.observed(frame)
            if rows:
                # 🔴 **봉이 닫힌 뒤로 잰다** (2026-08-18 정정). `ts` 는 봉이 **여는**
                #    시각이고 그 봉은 한 간격 뒤에 닫힌다 — ts 로 재면 마지막 마감 봉이
                #    늘 `1~2 x 간격` 으로 나온다.
                #
                #    그래서 10초봉이 22초, 1분봉이 122초로 보였고, 나는 그것을
                #    *"두 배씩 밀렸다"* 로 읽었다. **밀린 것이 아니라 재는 법이 틀렸다.**
                #
                # ⇒ 닫힌 시각(`ts + 간격`) 기준으로 재면 0 에 가까운 것이 정상이고,
                #   한 간격을 넘으면 **진짜로 한 봉을 놓친 것**이다.
                closed_at = rows[-1].ts + timedelta(seconds=interval_seconds(frame))
                out[frame.value] = max(0.0, (now - closed_at).total_seconds())
        return out

    async def _run_audit(self) -> None:
        """감사를 돌리고 결과를 보관한다 — 화면이 읽는다.

        Note:
            ⛔ **감사가 판을 멈추지 않는다.** 여기서 예외가 나 러너가 죽으면 감사가
            위험 자체가 된다 (절대 규칙 #8 은 조용한 실패를 막는 것이지, 점검이 본업을
            막게 하라는 뜻이 아니다).

            ⚠️ 이상이 **사라진 것**도 남긴다. 고쳐졌는지 사람이 알아야 하고, 목록만
            비우면 *"봤는데 없어졌다"* 와 *"아직 안 봤다"* 가 같아진다.
        """
        # ⭐ T236 — 아직 실제 수수료를 못 붙인 닫힌 매매를 감사 주기마다 셋씩 따라잡는다
        #    (닫힐 때 거래소 이력이 늦게 도착한 경우 · 옛 행). 실패해도 감사는 돈다.
        pending = [
            item.trade_id
            for item in self._session.ledger.records
            if item.closed_at is not None and item.fee_actual is None
        ][-3:]
        for trade_id in pending:
            try:
                await self._align_fee(trade_id)
            except Exception as exc:
                self._log.warning(
                    "live_fee_align_failed",
                    payload={"trade_id": trade_id, "error": f"{type(exc).__name__}: {exc}"[:200]},
                )
        try:
            found = await self.audit()
        except Exception as exc:
            self._log.error("live_audit_failed", payload={"error": str(exc)[:200]})
            return
        was = {item["code"] for item in self.findings}
        now = {item["code"] for item in found}
        for code in sorted(now - was):
            item = next(one for one in found if one["code"] == code)
            self._log.error(
                "live_audit_found",
                payload={"code": code, "level": item["level"], "detail": item["detail"]},
            )
        for code in sorted(was - now):
            self._log.info("live_audit_cleared", payload={"code": code})
        self.findings = found
        # 🔴 **회계가 증명 가능하게 틀리면 펀드 격리 신호를 세션에 꽂는다** (벽돌 2).
        #    `pnl_sign_split` = 원장·거래소 실현손익 부호 반대. 펀드가 이 세션의 허구
        #    손익을 총자본·TWR 에 안 넣게 한다 (`SessionBridge` 가 읽는다).
        self._session.accounting_ok = not any(item["code"] == "pnl_sign_split" for item in found)

    async def audit(self) -> list[dict[str, str]]:
        """**스스로 점검한다** — 걸음마다. 이상이 있으면 목록으로 낸다.

        Returns:
            발견 목록. 각 항목은 `{code, level, detail}`. 이상이 없으면 빈 목록.

        Note:
            🔴 사용자 요구 2026-08-18: *"이런 걸 좀 알아서 안터지게 보완해둘수 없나?
            일정 시간마다 검증한다던가."*

            맞다. 오늘 나온 버그가 **전부 같은 모양**이었다 — 무언가 조용히 얼어 있고
            아무도 모른다. 하나씩 잡는 대신 **불변식을 걸음마다 확인한다.**

            여기 있는 항목은 전부 **실제로 터진 것**이다. 가상의 위험이 아니다:

                축 동결      10s·5m 이 시드 뒤로 안 움직였다 (판정이 죽은 봉을 봤다)
                판정 정지    봉은 느는데 걸음이 안 늘었다
                가격 축 지연  계획은 15m, 진입가는 9시간 전 5m 종가였다 (4건 -4.48%)
                기하 역전    롱인데 1차 익절이 진입 아래였다
                손절 소멸    조건부가 24시간에 사라졌다 (포지션 무방비)
                원장 불일치  원장은 보유중인데 거래소는 비어 있었다

            ⚠️ **여기서 고치지 않는다.** 감사는 보는 일이고 고치는 것은 각자의 자리가
            있다 — 섞으면 감사가 상태를 바꿔서, 다음에 무엇이 원인인지 알 수 없게 된다.

            ⛔ 실패해도 판정을 막지 않는다. 감사가 판을 멈추면 감사가 위험이 된다.
        """
        found: list[dict[str, str]] = []
        now = datetime.now(UTC)

        # ① 축이 흐르는가 — 판정 축과 진입 축은 **반드시**.
        #
        # 🔴 **`observed` 로 잰다** (T15-4 · 2026-08-18 정정). 예전에는 `judged` 로
        #    쟀는데 그 보기는 커서까지만 주고, 커서는 진입 축 봉이 마감될 때만 움직인다 —
        #    즉 **감사가 막힌 눈으로** 보고 있었다. 축이 동결됐는데 정상이라고 답했고,
        #    같은 계약을 쓰는 한 감사도 같이 눈이 먼다.
        # 🔴 **진입가를 적는 축도 본다** (T20 ②). 2026-08-19 ① 은 *"10초봉이 15분마다만
        #    갱신된다"* 였는데 **아무 예외도 안 났다** — 값이 있고 갱신만 안 됐기 때문이다.
        #    그 축이 얼면 진입가가 조용히 낡고, 낡은 가격으로 계획이 선다.
        for frame in {STEP_FRAME, self.entry, self.price_frame}:
            rows = self._feed.observed(frame)
            if not rows:
                found.append(
                    {"code": "frame_empty", "level": "error", "detail": f"{frame.value} 봉이 없다"}
                )
                continue
            age = (now - rows[-1].ts).total_seconds()
            span = interval_seconds(frame)
            # 세 배를 넘으면 봉 두 개를 통째로 건너뛴 것이다.
            if age > span * 3:
                found.append(
                    {
                        "code": "frame_frozen",
                        "level": "error",
                        "detail": (
                            f"{frame.value} 가 {int(age)}초째 그대로다 "
                            f"(간격 {span}초) — 판정이 죽은 봉을 보고 있다"
                        ),
                    }
                )

        # ② **봉은 느는데 판정이 안 도는가** (T15-4 새 항목).
        #
        # 🔴 이것이 없어서 놓쳤다. `steps` 가 0 인 것은 *"아직 봉이 안 왔다"* 와
        #    *"봉은 왔는데 판정이 안 돈다"* 를 똑같이 보이게 하는데, 후자가 실제로
        #    있었다 (전진 축 불일치) — 예외도 로그도 없었고 증상은 그 숫자 하나였다.
        #
        # ⚠️ 한 봉 차이는 정상이다 — 방금 마감된 봉을 아직 판정하기 전일 수 있다.
        behind = len(self._feed.observed(self.entry)) - self._bars_at_step
        if self.steps and behind >= STALL_BARS:
            found.append(
                {
                    "code": "judgement_stalled",
                    "level": "error",
                    "detail": (
                        f"{self.entry.value} 봉이 {behind}개 마감됐는데 판정이 안 늘었다 "
                        f"(걸음 {self.steps}) — 봉은 흐르는데 로직이 안 돈다"
                    ),
                }
            )

        # ③ **계획과 진입가가 같은 시점에서 나오는가** (T15-3).
        #
        # 🔴 `_one_step` 이 이미 막지만 화면에는 안 보인다 — 사람에게는 "판정 0 회" 로만
        #    나타나고, 그것은 정상 대기와 구별되지 않는다 (절대 규칙 #8).
        behind_price = self.price_drift()
        if behind_price:
            found.append({"code": "price_frame_behind", "level": "error", "detail": behind_price})

        # ④ 보유 중이면 계획 기하가 성립하는가.
        held = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if held is not None:
            broken = geometry_fault(held)
            if broken:
                # 🔴 **무슨 일인지 이름을 붙인다** (사용자 확정 2026-08-21).
                #
                #    체결이 손절선을 넘어 들어오면 기하가 뒤집힌다. 사실이지만
                #    *"숏인데 손절이 진입 아래다"* 만으로는 **행동으로 이어지지 않고**,
                #    매 걸음 붉게 떠서 진짜 이상을 그 밑에 묻는다 (무방비 경보에서 겪었다).
                #
                #    실측 (ETH · 2026-08-21):
                #
                #      진입 2374.95  손절 2370.92   ← "진입 아래" 는 맞다
                #      표시가 2351.95 · 조건부 2370.95 걸림 · 미실현 +4.60
                #
                #    손절은 **현재가 위**에 정상으로 걸려 있었다. 뒤집힌 것은 계획이지
                #    보호가 아니다.
                #
                # ⛔ **입을 막는 것이 아니다.** 손절이 정말로 없는 경우는 바로 아래
                #    `stop_missing` 이 **error** 로 잡는다 — 위험한 절반은 거기 있다.
                #
                # ⭐ RR 통계에는 이미 안 섞인다 — 손절이 진입 반대쪽이면 `risk <= 0` 이라
                #   `realized_rr` 가 None 을 낸다.
                dead = "손절선 너머" in held.note
                found.append(
                    {
                        "code": "born_dead" if dead else "plan_geometry",
                        "level": "warn" if dead else "error",
                        "detail": (
                            f"{broken} — 체결가가 손절선을 넘어 들어왔다. 손절이 걸려 "
                            "있는지는 아래 항목이 따로 본다. 이 매매는 RR 통계에 "
                            "안 들어간다"
                        )
                        if dead
                        else broken,
                    }
                )

        # ⑤ 보유 중이면 브로커측 손절이 실제로 걸려 있는가.
        #
        # 🔴 **아직 걸어 보지도 않은 것을 무방비로 외치지 않는다** (사용자 신고 2026-08-20:
        #    *"실제 손절 주문이 발행되기 전에 바로 뜨네. 그래서 얼럿 알림이 먼저 와."*).
        #
        #    감사는 걸음과 **따로 도는 타이머**에서도 돈다. 체결로 원장에 보유가 생긴
        #    순간과 `_arm` 이 조건부를 거는 순간 사이에 끼어들면 *"포지션이 있는데 조건부
        #    0건"* 이 보인다 — **사실이지만 정상적인 무장 중**이고, 경보음까지 울렸다.
        #
        # ⚠️ **시계로 재지 않는다.** *"몇 초 지났으면"* 은 근거 없는 상수이고 느린 날에는
        #    거짓 경보가 그대로 난다. 기준은 시간이 아니라 **기회**다 — `_guard_stop` 이
        #    이 매매에 대해 한 번이라도 돌았나. 그 루프는 걸음마다 도므로 억누르는 창은
        #    한 걸음뿐이고, 진짜 무방비는 다음 걸음에 그대로 뜬다.
        if held is not None and isinstance(self._orders, StopAware):
            try:
                stops = await self._orders.open_stops(self.instrument)
                if not stops and self._armed_for == held.trade_id:
                    found.append(
                        {
                            "code": "stop_missing",
                            "level": "error",
                            "detail": "포지션이 있는데 조건부 손절이 0건이다 — 무방비다",
                        }
                    )
            except Exception as exc:
                found.append({"code": "stop_unreadable", "level": "warn", "detail": str(exc)[:120]})

        # ⑤-0 **판들의 예산 합이 계좌 안에 드는가** (사용자 신고 2026-08-21).
        #      넘으면 새 주문마다 Gate 가 `LIQUIDATE_IMMEDIATELY` 로 거절한다.
        if self.short_by is not None:
            found.append(
                {
                    "code": "underfunded",
                    "level": "error",
                    "detail": (
                        f"열린 판들의 예산 합이 계좌보다 {self.short_by} USDT 많다 — "
                        "새 진입을 막았다. 판을 줄이거나 어느 판의 예산을 낮춘다 "
                        "(어느 쪽인지는 코드가 정하지 않는다)"
                    ),
                }
            )

        # ⑤-1 **나갈 호가가 있는가** (사용자 제안 2026-08-20). `_keep_watching_book` 이
        #      5분마다 채운다 — 여기서 다시 조회하지 않는다 (감사는 30초마다 돈다).
        if self.dry is not None:
            found.append(
                {
                    "code": "book_dry",
                    # ⚠️ 보유 중이면 **error**(못 나갈 수 있다), 아니면 **warn**(못 들어갈
                    #    뿐이다). 같은 사실이라도 포지션이 있고 없고가 위험을 가른다.
                    "level": "error" if held is not None else "warn",
                    "detail": (
                        f"{self.dry['why']} — 새 진입은 막았다. "
                        + (
                            "들고 있는 것은 **던지지 않는다** (실측: 던졌으면 -420, "
                            "표시가 아래에서 기다렸더니 -74). 나갈 값은 사람이 정한다"
                            if held is not None
                            else "포지션이 없으니 지금은 손해가 없다"
                        )
                    ),
                }
            )

        # ⑥ **증거금이 바닥나 멈췄는가** (T14-2).
        #
        # 🔴 조용히 멈추면 "판정 0회" 와 구별되지 않는다 (절대 규칙 #8). 사용자가 요구한
        #    *"포지션 금액이 다 떨어지면 매매가 멈춘다"* 는 **멈춘 것을 말해야** 완성이다.
        book = self._session.ledger
        stopped = book.halted_at
        if stopped:
            found.append(
                {
                    "code": "margin_exhausted",
                    "level": "error",
                    "detail": (
                        f"매매 {stopped[:6]} 뒤로 펀드 몫을 다 잃었다(증거금 0) — 그 뒤는 안 센다"
                        if not book.refill
                        else f"매매 {stopped[:6]} 뒤로 증거금을 목표치까지 못 채웠다 — "
                        "지갑이 모자란다. 그 뒤 매매는 성적에 안 센다"
                    ),
                }
            )
        # ⑥-b **브레이커가 닿았는가** (T22). 멈춘 이유를 화면이 말한다.
        if book.tripped_at:
            found.append(
                {
                    "code": "breaker_tripped",
                    "level": "error",
                    "detail": (
                        f"매매 {book.tripped_at[:6]} 에서 고점 대비 낙폭 "
                        f"{book.max_drawdown_pct:.1f}% 가 브레이커 {book.drawdown_stop_pct}% 에 "
                        "닿아 새 진입을 멈췄다 — 사람이 보고 다시 켠다"
                    ),
                }
            )

        # ⑦ **원장이 말하는 돈과 거래소가 말하는 돈이 같은가** (T14-2).
        #
        # 🔴 원장은 **모형**이고 거래소는 **사실**이다. 둘이 갈리는 것을 예전에는 아무도
        #    몰랐다 — 실측에서 원장 1000 / 거래소 800 이었고, 그 차이만큼의 주문이
        #    거부됐다. 원장을 여기서 고치지는 않는다 (절대 규칙 #5).
        # ⭐ 능력 확인이 필요 없다 — `get_balance` 는 `BrokerAdapter` 계약에 있다.
        if book.funding is Funding.WALLET:
            try:
                account = await self._orders.get_balance()
            except Exception as exc:
                found.append(
                    {"code": "balance_unreadable", "level": "warn", "detail": str(exc)[:120]}
                )
            else:
                # 🔴 **포지션에 들어간 돈은 잃은 돈이 아니다** (2026-08-19 사고).
                #    `available` 만 보면 진입하는 순간 그만큼이 사라진 것으로 보이고,
                #    감사가 *"원장이 사실과 갈렸다"* 고 외친다 — 실측에서 29.8% 차이가
                #    났는데 잡힌 증거금 297 을 더하니 계정은 멀쩡했다.
                #
                # ⛔ 이 프로젝트가 반복하는 실수다: **옮긴 것을 잃은 것으로 센다.**
                locked = Decimal(0)
                if isinstance(self._orders, MarginAware):
                    with contextlib.suppress(Exception):
                        locked = await self._orders.account_margin()
                real = Decimal(str(account.cash)) + locked
                modelled = book.equity
                # 🔴 **판이 여럿이면 이 대조가 성립하지 않는다** (2026-08-20 ⓓ).
                #    판마다 원장이 `seed_cash = 계좌 총액` 으로 시작한다 — 즉 판이 둘이면
                #    둘 다 *"계좌 전체가 내 것"* 이라 여기고, 둘 다 갈렸다고 외친다.
                #    실제로 나온 8.8% 는 **틀린 계산이 낸 숫자**이지 사실의 차이가 아니다.
                #
                # ⇒ 나눌 근거가 없으면 **가를 수 없다고 말한다.** 조용히 넘기면 진짜
                #   갈림을 놓치고, 그대로 외치면 늘 붉어서 아무도 안 본다 — 둘 다 나쁘다.
                others = self.peers()
                # 🔴 **합계로 대조하되, 위험한 방향만 외친다** (2026-08-30).
                #
                #    ㄱ) 판이 여럿이면 예전엔 손을 놓고 warn 만 뱉었다. 판을 여럿 띄우는
                #        것이 상시라 **대조가 한 번도 안 돌았다** — 가를 수 없는 것은
                #        *판 하나의 몫*이지 *합계*가 아니다.
                #
                #    ㄴ) 그런데 합계도 계정 총액과 **같을 이유가 없다.** 사람은 계좌의
                #        일부만 굴린다 (실측 2026-08-30: 판 6개 원장 합 1000 ·
                #        계정 총액 4956 — 이건 갈린 것이 아니라 **안 쓴 돈**이다).
                #
                # ⇒ **초과분만** 사고다. 원장이 계좌에 없는 돈을 자기 것으로 세면 그만큼의
                #   주문이 거절된다. 모자란 쪽(안 쓴 돈)은 정상이므로 입을 다문다.
                #
                # ⚠️ 이것이 옛 검사를 좁히는 것은 맞다. 대신 **더 센 검사가 따로 있다** —
                #   ⑧-0 `_pnl_audit` 이 **끝난 매매 단위로** 원장 손익과 거래소 손익을
                #   맞춰 본다. 잔액은 다 잃은 뒤에야 갈리지만 그쪽은 첫 건에서 갈린다.
                pooled = self.account_modelled() if others > 0 else modelled
                if pooled is None:
                    found.append(
                        {
                            "code": "wallet_unattributable",
                            "level": "warn",
                            "detail": (
                                f"판이 {others + 1}개인데 합계를 못 냈다 — 원장"
                                f"({modelled:.2f})과 계정 총액({real:.2f})을 가를 수 없다"
                            ),
                        }
                    )
                elif pooled > 0 and real >= 0 and pooled - real > pooled * WALLET_DRIFT:
                    over = (pooled - real) / pooled
                    whose = (
                        f"판 {others + 1}개 원장 합 {pooled:.2f}"
                        if others > 0
                        else f"원장 {pooled:.2f}"
                    )
                    found.append(
                        {
                            "code": "wallet_drift",
                            "level": "error",
                            "detail": (
                                f"{whose} > 계정 총액 {real:.2f} ({over * 100:.1f}% 초과) — "
                                "원장이 **계좌에 없는 돈**을 자기 것으로 센다. "
                                "그만큼의 주문이 거절된다. "
                                "계정 총액 = 쓸 수 있는 돈 + 포지션에 잡힌 증거금"
                            ),
                        }
                    )

        # ⑧-0 🔴 **원장이 말하는 손익과 거래소가 말하는 손익** (T20 ①).
        #
        #    지금까지 감사는 **잔액**만 봤다. 그런데 잔액은 **다 잃은 뒤에야** 갈린다 —
        #    2026-08-19 에 이 경보가 처음 뜬 것은 28건 중 20건 넘게 끝난 뒤였고,
        #    그 3시간 동안 화면은 승률 100% 를 띄우고 있었다.
        #
        #    ⇒ **끝난 매매 단위로** 잰다. 그러면 첫 건에서 갈린다:
        #
        #      1건 끝난 시점   원장 +2.3%   거래소 -3.7%   →  즉시 경보
        found += await self._pnl_audit()

        # ⑧ 원장과 거래소가 같은 것을 말하는가.
        if isinstance(self._orders, PositionAware):
            try:
                position = await self._orders.position_snapshot(self.instrument)
            except Exception:
                position = {}
            else:
                on_book = held is not None
                on_exchange = bool(position)
                # ⭐ 입양 거부는 streak 를 기다리지 않는다 — 거부 순간이 곧 확진이고,
                #   재시작마다 streak 가 리셋돼 화면에 못 오르던 구멍을 막는다.
                if self._adopt_refused is not None:
                    if on_exchange and not on_book:
                        found.append(
                            {
                                "code": "adopt_refused",
                                "level": "error",
                                "detail": self._adopt_refused,
                            }
                        )
                    else:
                        self._adopt_refused = None  # 정리됐다 — 발견도 걷는다
                # ⭐ 체결 직후 한 걸음은 원장이 아직 모른다 (거래소 → 우편함 → 세션 걸음). 한 번은
                #    한 번은 넘어가고 두 번 연속이면 진짜다 — 사용자 2026-08-23: 체결마다 경고.
                # ⭐ 그 판이 걸어 둔 지정가가 **봉 사이에** 채워진 것은 갈림이 아니라 반영
                #    대기다 — 30초 안에 `_absorb_between_bars` 가 옮긴다 (2026-09-06 사고).
                pending_fill = (
                    on_exchange and not on_book and self._session.waiting_trade is not None
                )
                streak = getattr(self, "_mismatch_streak", 0)
                self._mismatch_streak = (
                    streak + 1 if on_book != on_exchange and not pending_fill else 0
                )
                if on_book != on_exchange and self._mismatch_streak >= 2:
                    found.append(
                        {
                            "code": "ledger_mismatch",
                            "level": "error",
                            "detail": (
                                f"원장 {'보유중' if on_book else '없음'} / "
                                f"거래소 {'보유중' if on_exchange else '없음'}"
                            ),
                        }
                    )

        # ⑨ 🔴 **거래소가 말하는 청산가 vs 우리 손절** (2026-08-30 · β 배선의 짝).
        #
        #    사용자 지적: *"테스트넷에서 청산이 나오는 경우의 수를 테스트할 수가 없다 —
        #    주가가 그렇게 빠질 수가 없는데 어떻게 청산 테스트를 하나?"* 맞다.
        #    6x 에서 청산이 나려면 16% 역행이 필요하고 그건 기다려서 볼 일이 아니다.
        #
        #    ⇒ **기다릴 필요가 없다.** 거래소가 포지션마다 청산가를 계산해 준다.
        #      첫 포지션이 열리는 순간 우리 모형이 맞는지 알 수 있다:
        #
        #        · 유지증거금이 계단식이면 거래소 청산가가 우리 계산보다 **가깝다**
        #        · β 의 전제는 "손절이 청산보다 안쪽" 인데, 거래소 청산가가 손절보다
        #          안쪽이면 그 전제가 깨진 것이고 **손절은 장식**이다 (T120 이 센 바로 그 판)
        #
        #    이것이 청산을 실제로 일으키지 않고 청산 모형을 검사하는 유일한 방법이다
        #    (`gate/trade_client.position_of` 가 같은 말을 적어 뒀다).
        held = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if held is not None and isinstance(self._orders, PositionAware):
            try:
                snapshot = await self._orders.position_snapshot(self.instrument)
                raw_liq = str(snapshot.get("liq_price", "") or "")
                exchange_liq = Decimal(raw_liq) if raw_liq else None
            except Exception:
                exchange_liq = None
            if exchange_liq is not None and exchange_liq > 0 and held.entry > 0:
                long = held.direction is Direction.LONG
                stop_room = abs(held.entry - held.planned_stop) / held.entry
                liq_room = abs(held.entry - exchange_liq) / held.entry
                if (
                    (exchange_liq >= held.planned_stop)
                    if long
                    else (exchange_liq <= held.planned_stop)
                ):
                    found.append(
                        {
                            "code": "liq_inside_stop",
                            "level": "error",
                            "detail": (
                                f"거래소 청산가 {exchange_liq} 가 손절 {held.planned_stop} 보다 "
                                f"**안쪽**이다 (청산 {liq_room * 100:.2f}% / 손절 "
                                f"{stop_room * 100:.2f}%) — 손절이 장식이고 청산이 먼저 온다. "
                                f"배율을 낮추거나 β 를 조여야 한다"
                            ),
                        }
                    )
                elif liq_room > 0:
                    margin = stop_room / liq_room
                    if margin > Decimal("0.8"):
                        found.append(
                            {
                                "code": "stop_near_liquidation",
                                "level": "warn",
                                "detail": (
                                    f"손절이 청산거리의 {margin * 100:.0f}% 지점이다 "
                                    f"(청산 {liq_room * 100:.2f}% / 손절 {stop_room * 100:.2f}%) "
                                    f"— 여유가 얇다. 갭 한 번이면 청산이 먼저다"
                                ),
                            }
                        )
        return found

    def _commit_pnl(self, found: list[dict[str, str]]) -> list[dict[str, str]]:
        """회계 대조 결과를 캐시하고 스로틀 시계를 지금으로 (성공 완료 시에만)."""
        self._last_pnl_at = time.monotonic()
        self._pnl_findings = found
        return found

    async def _pnl_audit(self) -> list[dict[str, str]]:
        """원장 실현 손익 vs **거래소 실현 손익** (T20 ①).

        Returns:
            어긋난 것들. 없으면 빈 목록.

        Note:
            🔴 **잔액은 다 잃은 뒤에야 갈린다.** `wallet_drift` 가 처음 외친 것은
            2026-08-19 에 28건 중 20건 넘게 끝난 뒤였고, 그 3시간 동안 화면은 승률
            100% 를 띄웠다. 끝난 매매 단위로 재면 **첫 건에서** 갈린다.

            🔴 **부호가 다르면 크기와 무관하게 경보다.** 그때 원장은 +2.255%,
            사실은 -3.725% 였다 — 퍼센트 문턱만 봤으면 초반 몇 건은 통과했을 것이다.
            *"둘 다 이겼는데 조금 다르다"* 와 *"한쪽은 이기고 한쪽은 졌다"* 는 전혀
            다른 사건이다.

            ⚠️ **갈릴 정당한 이유가 있다** — 슬리피지(원장은 봉 종가, 거래소는 실제
            체결가) · 펀딩비(무기한은 8시간마다 정산되고 원장에 없다) · 부분 체결.
            그래서 크기 차이는 넉넉히 보고 **부호**를 엄하게 본다.

            ⚠️ **귀속**: Gate 는 계약당 포지션이 하나이고 판마다 종목이 하나라 종목으로
            갈린다. 두 판이 같은 종목을 돌면 못 가르며, 그때는 **가를 수 없다고 말한다**
            — 조용히 합치면 남의 손익을 내 성적으로 읽는다.

            ⛔ **이 감사가 원장을 고치지 않는다** (절대 규칙 #5). 말하는 것까지가 일이다.

            🔴 **스로틀** (2026-09-01): `position_closes`(BINANCE `allOrders`+`income`)는
            가중치가 커서, 걸음·30초마다 부르면 한도를 초과해 IP 밴을 부른다(실측 141%).
            회계 갈림은 느리므로 `PNL_AUDIT_INTERVAL`(5분)마다만 새로 재고, 그 사이엔
            마지막 결과를 돌려준다 — 그래야 `accounting_ok` 가 유지된다. 손절·포지션
            점검은 이 스로틀 밖이라 여전히 30초다 (안전 직결 · 가중치 작음).
        """
        found: list[dict[str, str]] = []
        if not hasattr(self._orders, "position_closes"):
            return self._commit_pnl(found)  # 조회 못 하는 어댑터 — 회계 대조 없음
        # 🔴 **종목별 위상 오프셋** (§1 · 2026-09-01) — 6종 러너가 동시에 시작하면
        #    스로틀 만료가 겹쳐 `income`+`allOrders` 를 한꺼번에 발사한다(실측 버스트).
        #    간격에 종목 해시 기반 0~90초를 더해 서로 어긋나게 한다 — 부하가 5분 창에
        #    퍼진다. 결정론 코어가 아니라 타이밍이라 규칙 #5 와 무관하다.
        phase = abs(hash(self.instrument.symbol)) % 90
        if (
            time.monotonic() - self._last_pnl_at < PNL_AUDIT_INTERVAL + phase
            and self._pnl_findings is not None
        ):
            return self._pnl_findings  # 스로틀 — 무거운 거래소 조회(allOrders·income)를 아낀다
        book = self._session.ledger
        ours = [item for item in book.closed if item.gain_pct is not None]
        if not ours:
            return self._commit_pnl(found)
        try:
            closes = cast(
                "list[dict[str, str]]",
                await self._orders.position_closes(self.instrument),  # type: ignore[attr-defined]
            )
        except Exception as exc:
            # ⚠️ 조회 실패는 **스로틀·캐시하지 않는다** — 일시적일 수 있으니 다음 걸음에
            #    다시 시도한다. 캐시하면 5분간 옛 회계를 붙들거나 갈림을 지운다.
            return [{"code": "closes_unreadable", "level": "warn", "detail": str(exc)[:120]}]
        # ⚠️ 판이 뜨기 전의 청산은 남의 것이다 — **첫 매매 시각**으로 자른다.
        #    원장에 판 시작 시각이 없어서 이것이 가장 가까운 경계다.
        # 🔴 **내 판 표식 + 소유권 창으로 귀속한다** (벽돌 3·4 · 2026-09-01) — 재사용된
        #    한 계정에 쌓인 남의 런·잔재 청산을 뺀다. 무태그 강제청산은 **내가 그 계약을
        #    들고 있던 창 안**이면 내 것으로 센다 (선물은 계약당 포지션 하나).
        intervals: list[tuple[float, float]] = []
        for item in book.records:
            if item.opened_at is None:
                continue
            end = item.closed_at.timestamp() if item.closed_at else float("inf")
            intervals.append((item.opened_at.timestamp(), end))
        # 🔴 재정렬 워터마크 — 이 시각 이전 청산은 감사에서 버린다 (resync).
        since = self._session.audit_since.timestamp() if self._session.audit_since else 0.0
        theirs, counted, liq = attribute_closes(
            closes, run_tag(self._run_key), intervals, since=since
        )
        if not counted:
            return self._commit_pnl(found)
        # 🔴 **귀속된 실측 실현손익을 세션에 남긴다** (벽돌 3 write · 2026-09-01). 원장이
        #    거래소와 갈리면 펀드 격리가 원장 허구 대신 이 값을 쓴다 — 재시작하며 이미
        #    갈린 채로 떠도 허구가 아니라 실측을 반영한다.
        self._session.verified_realized = theirs
        # 🔴 **지금 증거금으로 과거를 곱하면 안 된다** (2026-08-20 ⓓ). 예전에는
        #    `sizing_base x 손익률 합` 으로 셌는데, 증거금은 매매마다 달라진다 — 벌면
        #    커지고 청산나면 금고에서 채워진다. 그래서 오래 돈 판일수록 원장 쪽이
        #    부풀었고, `원장 +94.26 vs 거래소 -10.19` 라는 **부호까지 틀린** 대조를 냈다.
        #
        # ⇒ 원장이 굴러가는 그 자리에서 **그때의 증거금으로** 센 값을 쓴다.
        # 🔴 **재정렬 이후 실현**만 원장 쪽으로 센다 — 앵커 이전 fiction 은 뺀다.
        mine = book.realized_cash - self._session.realized_anchor
        # ⚠️ 내 창 안의 무태그 청산(=내 강제청산/수동청산)이 있으면 사실을 남긴다 —
        #    원장이 그걸 놓쳤으면 아래 부호 대조가 그것을 짚어 준다.
        tail = f" · 내 강제/수동청산 {liq}건 포함" if liq else ""
        if (mine > 0) != (theirs > 0) and (abs(mine) > 0 or abs(theirs) > 0):
            found.append(
                {
                    "code": "pnl_sign_split",
                    "level": "error",
                    "detail": (
                        f"🔴 원장 {mine:+.2f} vs 거래소(내 판) {theirs:+.2f} — **부호가 "
                        f"다르다**. 끝난 매매 {len(ours)}건 / 내 청산 {counted}건{tail}. "
                        "회계가 틀렸다는 뜻이므로 판정을 믿으면 안 된다"
                    ),
                }
            )
        elif abs(theirs) > 0 and abs(mine - theirs) / max(abs(theirs), Decimal(1)) > PNL_DRIFT:
            found.append(
                {
                    "code": "pnl_drift",
                    "level": "warn",
                    "detail": (
                        f"원장 {mine:+.2f} vs 거래소 {theirs:+.2f} — 슬리피지·펀딩비로 "
                        f"설명되는 범위를 넘었다 (끝난 매매 {len(ours)}건)"
                    ),
                }
            )
        return self._commit_pnl(found)

    async def probe(self) -> dict[str, object]:
        """**거래소에 직접 물어** 봉이 실시간으로 흐르는지 본다.

        Returns:
            `{exchange_latest, feed_cursor, lag_seconds, frame_seconds, verdict, streaming}`.

        Note:
            🔴 **`running` 만으로는 살아 있는지 알 수 없다.** 그것은 *"루프가 안 죽었다"*
            일 뿐이고, 웹소켓이 조용히 끊겨도 태스크는 멀쩡히 대기한다. 실제로 겪은
            사고가 그 모습이었다 — 예외도 로그도 없이 40분간 아무 일이 없었다.

            ⇒ 그래서 **REST 로 따로 물어** 거래소의 최신 봉과 급전의 커서를 비교한다.
              둘이 한 봉 이상 벌어지면 스트림이 뒤처진 것이다.

            ⚠️ 판정에 쓰지 않는다 — 점검이다. 여기서 받은 봉을 급전에 넣으면 사람이
              버튼을 누른 시각이 원장에 섞여 결정론이 깨진다 (절대 규칙 #5).
        """
        span = timedelta(seconds=interval_seconds(self.entry))
        now = datetime.now(UTC)
        rows = await self._quotes.get_candles(self.instrument, self.entry, now - span * 5, now)
        latest = rows[-1].ts if rows else None
        cursor = self._feed.cursor
        frame_seconds = interval_seconds(self.entry)
        lag = None if latest is None else (latest - cursor).total_seconds()
        if latest is None:
            verdict = "거래소가 봉을 안 준다 — 조회 자체가 실패다"
        elif lag is not None and lag <= 0:
            verdict = "최신이다 — 급전이 거래소를 따라잡았다"
        elif lag is not None and lag < frame_seconds:
            verdict = "진행 중 봉 하나 차이 — 정상이다 (마감을 기다린다)"
        else:
            verdict = "🔴 한 봉 이상 뒤처졌다 — 스트림이 끊겼을 수 있다"
        # 🔴 **화면이 실제로 가진 마지막 봉을 따로 낸다** (사용자 신고 2026-08-18:
        #    깜빡임이 안 나온다).
        #
        #    원인: `exchange_latest` 는 거래소의 **진행 중 봉**이고, 급전은 진행 중 봉을
        #    버린다(마감된 것만 넣는다). 그래서 그 ts 는 차트에 **없는 봉**을 가리켰고,
        #    화면은 맞는 봉을 못 찾아 아무것도 안 그렸다 — 정상 상태였는데도 그랬다.
        #
        # ⇒ 깜빡일 대상은 **차트가 가진 마지막 봉**이다. 거래소 최신 봉은 지연 판정용으로
        #   그대로 남긴다 (둘은 다른 질문에 답한다).
        drawn = self._feed.judged(self.entry)
        last_drawn = drawn[-1].ts if drawn else None
        return {
            "exchange_latest": None if latest is None else latest.isoformat(),
            "chart_latest": None if last_drawn is None else last_drawn.isoformat(),
            "feed_cursor": cursor.isoformat(),
            "lag_seconds": lag,
            "frame_seconds": frame_seconds,
            "frame": self.entry.value,
            "verdict": verdict,
            "streaming": bool(rows) and lag is not None and lag < frame_seconds,
            "probed_at": now.isoformat(),
        }

    async def _heal(self) -> None:
        """봉 구멍을 REST 로 메운다.

        Note:
            🔴 **구멍을 두고 판정하지 않는다.** 빈 봉은 "거래가 없었다" 와 구별되지 않고,
            그 상태의 ATR·MA 는 조용히 틀린다.

            ⚠️ 커서는 안 움직인다 (`LiveFeed.backfill`). 과거를 채우는 일이다.
        """
        span = timedelta(seconds=interval_seconds(self.entry))
        end = self._feed.cursor
        start = end - span * SEED_BARS
        rows = await self._quotes.get_candles(self.instrument, self.entry, start, end)
        added = self._feed.backfill(self.entry, rows)
        self.backfilled += added
        self._log.warning(
            "live_runner_healed",
            payload={
                "added": added,
                "gaps_left": self._feed.gaps,
                "backfilled_total": self.backfilled,
            },
        )

    async def _place(self) -> None:
        """새로 생긴 계획을 주문으로 보낸다.

        Note:
            ⭐ **스냅샷이 아니라 원장을 본다.** 무엇을 주문할지의 근거는 원장이고,
            스냅샷은 그 순간의 화면이다 — 스냅샷을 보면 원장에 안 남은 것을 주문하거나
            원장에 남은 것을 놓칠 수 있다.

            🔴 **여기서 값을 바꾸지 않는다.** 손절·익절·수량의 SSoT 는 원장·RiskManager
            이고 집행은 값을 바꿀 권한이 없다 (절대 규칙 #4). 러너는 **옮기기만** 한다 —
            수량 계산조차 `order_mapping` 이 하고 이 함수는 그 결과를 보낸다.

            🔴 **진입 → 익절 순서다.** 익절을 먼저 보내면 없는 포지션을 줄이려 해서
            `reduce_only` 가 거부한다. 그리고 진입이 실패하면 익절을 **아예 안 보낸다** —
            보내면 반대 포지션이 열린다.

            🔴 **손절은 안 보낸다.** `CONDITIONAL_ORDERS` 를 능력표에 안 넣었으므로
            브로커측 스탑이 없고, 발동은 세션이 감시해 `_settle` 에서 처리한다.
            ⚠️ 그래서 **서버가 죽으면 손절이 안 걸린다** — 지금 구조의 가장 큰 구멍이고
            페이크머니 구간에 메워야 한다 (spec §7 · §12.6).

            ⚠️ **재시도하지 않는다.** 실패는 로그에 남기고 넘어간다. 재시도는
            `find_order` 로 체결 여부를 먼저 확인해야 하고(절대 규칙 #6), 그 경로를
            여기에 두면 "확인 없이 다시 보내는" 코드가 언젠가 생긴다.
        """
        if self.observe_only:
            # ⛔ 관찰 전용이다 — 주문 경로를 통째로 건너뛴다.
            return
        # 🔴 **끝난 기록은 주문이 아니다** (사용자 신고 2026-08-20). `_sent` 는 러너가
        #    뜰 때 **빈 집합**이고 원장은 저장소에서 통째로 되살아난다 — 그래서 재시작
        #    할 때마다 **이미 닫힌 매매 전부**가 "아직 안 보낸 것" 으로 보였다. 실측:
        #
        #      live14ee7408  failures 10 · placed 10건 전부 contracts 0
        #      그 10건은 14:30~17:09 에 손절·익절로 **닫힌 매매**였다
        #
        #    이번엔 `entry_fills` 가 차 있어 `_protect`(읽기만) 로 빠져 무해했지만,
        #    시장가 판(0.1·0.4)이었으면 **몇 시간 전에 닫힌 매매 10건을 다시 열었다.**
        #
        # ⭐ 보유 중인 기록은 그대로 통과한다 — 재시작 뒤 손절·익절을 다시 거는 길이
        #   그것이고, 그 경로가 없으면 되찾은 포지션이 무방비가 된다.
        fresh = [
            item
            for item in self._session.ledger.records
            if item.trade_id not in self._sent
            and item.opened_at is not None
            and item.outcome is Outcome.OPEN
        ]
        if not fresh:
            return
        spec = await self._contract_spec()
        for record in fresh:
            self._sent.add(record.trade_id)
            try:
                # 🔴 **지정가로 이미 샀으면 다시 사지 않는다** (2026-08-20 사고 ⓐ).
                #    `entry_fills` 는 거래소가 채웠다고 알려 온 조각들이다 — 그것이
                #    있다는 것은 **포지션이 이미 열려 있다**는 뜻이고, 여기서 시장가를
                #    또 내면 계획의 두 배가 열린다.
                #
                #    실제로 그렇게 돌았다: 0.51(지정가 판)의 로그에 시장가 경로에만
                #    있는 `live_runner_sizing`·`geometry_blocked` 가 찍혔고, 40초마다
                #    진입 → 기하 차단 → 손절 실패 → panic_close 가 반복됐다.
                if record.entry_fills or record.trade_id in self._revived_open:
                    # 🔴 지정가로 이미 샀거나(위) **되살아난 보유 기록**(2026-08-25 재전송
                    #    사고)이면 진입을 다시 내지 않는다 — 포지션은 이미 거래소에 있고,
                    #    여기서 또 사면 원장 몰래 계약이 쌓인다 (실측 BTC 67→333).
                    await self._protect(record)
                else:
                    await self._send(record, spec)
            except Exception as exc:
                # ⛔ 삼키지 않고 남긴다. 주문이 안 나간 채로 원장만 진입한 상태이므로
                #    사람이 봐야 한다 (절대 규칙 #8).
                self.failures += 1
                self.last_error = f"주문: {exc}"[:200]
                # 🔴 **화면이 볼 수 있게 남긴다.** 로그에만 두면 아무도 안 본다 —
                #    밤새 익절이 세 번 거절됐는데 아침에야 알았다 (2026-08-18).
                self.placed[record.trade_id] = {
                    "status": "rejected",
                    "order_id": "",
                    "contracts": "0",
                    "error": str(exc)[:180],
                }
                self._log.error(
                    "live_runner_order_failed",
                    payload={
                        "trade_id": record.trade_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "note": "주문이 거절됐다 — **어느 다리인지 `text` 로 갈린다** "
                        "(entry=진입 · take_profit=익절 · stop=손절). 진입이 성공한 뒤 "
                        "익절만 거절되면 포지션은 열려 있고 익절이 없는 상태다",
                    },
                )

    async def _protect(self, record: TradeRecord) -> None:
        """**지정가 사다리로 이미 산** 매매에 손절·익절만 건다 (2026-08-20 ⓐ).

        Args:
            record: 원장 기록. `entry_fills` 가 차 있다.

        Note:
            🔴 **계약 수를 거래소에 묻는다.** 원장은 *비중*(계획의 몇 %)만 알고 계약
            수는 모른다 — 다리마다 값이 다르고 반올림이 끼기 때문이다. 여기서 지어내면
            익절이 포지션보다 크거나 작아지고, 큰 쪽은 `reduce_only` 로 조용히 실패한다.

            ⚠️ **포지션이 없으면 갈린 것이다.** 우편함은 채워졌다는데 거래소에 포지션이
            없다면 둘 중 하나가 거짓말이다 — 손절을 걸 대상이 없으므로 남기고 멈춘다
            (절대 규칙 #8).
        """
        if not isinstance(self._orders, PositionAware):
            return
        snapshot = await self._orders.position_snapshot(self.instrument)
        filled = abs(int(Decimal(str(snapshot.get("size", "0"))))) if snapshot else 0
        self.placed[record.trade_id] = {
            "status": "limit_filled",
            "order_id": "",
            "contracts": str(filled),
        }
        if filled <= 0:
            self.failures += 1
            self.last_error = "지정가는 채워졌다는데 거래소에 포지션이 없다"[:200]
            self._log.error(
                "live_limit_position_missing",
                payload={
                    "trade_id": record.trade_id,
                    "fills": len(record.entry_fills),
                    "note": "원장과 거래소가 갈렸다 — 손절을 걸 대상이 없다",
                },
            )
            return
        self._log.info(
            "live_limit_protecting",
            payload={
                "trade_id": record.trade_id,
                "contracts": filled,
                "legs": len(record.entry_fills),
                "entry": str(record.entry),
                "note": "지정가로 샀다 — 시장가 진입을 내지 않고 보호만 건다",
            },
        )
        await self._warn_if_stacked(record, filled)
        await self._arm(record, filled)

    async def _warn_if_stacked(self, record: TradeRecord, filled: int) -> None:
        """거래소 포지션이 **이 매매가 연 것보다 큰지** 본다 (2026-08-20).

        Args:
            record: 원장 기록.
            filled: 거래소가 말한 계약 수.

        Note:
            🔴 **겹쳐 쌓인 것을 아무도 세지 않았다.** 사고 당시 `placed` 에는 기록
            다섯이 **전부 같은 2544 계약**을 들고 있었다 — Gate 는 계약당 포지션이
            하나라, 판이 여러 번 진입하면 원장은 다섯 건인데 거래소는 하나로 합친다.

            ```
            의도    예산 185 x 20배 / 68577 → 약 540 계약
            사실    2544 계약 · 증거금 886 (계좌의 99.9%) · 청산까지 4.36%
            ```

            ⚠️ **그 상태는 조용하다.** 포지션도 있고 손절도 있으니 화면상 정상이고,
            자가 점검의 어느 항목도 *"계획보다 다섯 배 크다"* 를 묻지 않았다.

            ⛔ **막지는 않는다.** 포지션은 이미 열려 있고, 여기서 보호를 건너뛰면
            무방비가 된다 — 더 나쁘다. 말하는 것까지가 이 함수의 일이다 (규칙 #8).

            ⚠️ 넉넉히 본다(1.5배). 반올림·부분 체결·다리 비중으로 정확히는 안 맞는다.
        """
        spec = await self._contract_spec()
        multiplier = Decimal(str(spec["quanto_multiplier"]))
        try:
            want = contracts_for(
                self._session.ledger.sizing_base * record.filled_ratio,
                record.leverage,
                record.entry,
                multiplier,
                size_min=int(spec.get("order_size_min", 1)),
                size_max=int(spec.get("order_size_max", 0)) or None,
            )
        except Exception:
            # ⛔ 크기를 못 세면 조용히 넘긴다 — 이것은 경보이지 관문이 아니다.
            return
        if filled <= want * 3 // 2:
            return
        self.last_error = f"포지션이 계획의 {filled / want:.1f}배다 — 판이 겹쳐 쌓였다"[:200]
        self._fired("stacked", f"거래소 {filled} 계약 vs 계획 {want} 계약")
        self._log.error(
            "live_position_stacked",
            payload={
                "trade_id": record.trade_id,
                "on_exchange": filled,
                "planned": want,
                "note": "Gate 는 계약당 포지션이 하나다 — 앞선 매매가 안 닫혔다",
            },
        )

    @property
    def _mailbox(self) -> LiveFiller | None:
        """이 판이 쓰는 우편함 — 시장가 판이면 없다."""
        found = self._session.filler
        return found if isinstance(found, LiveFiller) else None

    async def _inject_ref_regime(self) -> None:
        """기준 종목(BTC) 4h 레짐을 세션에 주입한다 (T66-e F1 · funding 주입과 같은 선례).

        Note:
            게이트를 선언한 플레이북이 없으면 아무것도 안 한다 (0.8.1 판 비용 0).
            실패는 None = 게이트 잠듦(0.2.0 동작 · 검증된 폴백)이며 로그는 남긴다 (#8).
            4h 봉당 1회만 계산한다 — 마지막 마감봉 ts 가 같으면 재사용.
        """
        gate_n = next(
            (
                item.entry_ref_ma_gate
                for item in self._session.playbooks
                if item.entry_ref_ma_gate is not None
            ),
            None,
        )
        if gate_n is None:
            return
        from dataclasses import replace as _replace

        from updown.analysis.indicators.ma import sma as _sma
        from updown.common.domain.instrument import Timeframe

        ref = _replace(self.instrument, symbol="BTC_USDT", name="BTC 무기한 (기준)")
        try:
            end = datetime.now(UTC)
            rows = await self._quotes.get_candles(
                ref, Timeframe.H4, end - timedelta(hours=4 * (gate_n + 20)), end
            )
            # 마감봉만 (마지막 봉이 진행 중이면 제외 — 판정은 닫힌 봉으로)
            closed = [c for c in rows if c.ts + timedelta(hours=4) <= end]
            if len(closed) <= gate_n:
                raise ValueError(f"기준 캔들 부족: {len(closed)} <= {gate_n}")
            last_ts = closed[-1].ts
            if self._ref_regime_at == last_ts:
                return  # 같은 4h 봉 — 재계산 불필요 (주입값 유지)
            level = _sma([c.close for c in closed], gate_n)[-1]
            if level is None:
                raise ValueError("기준 SMA 워밍업 미달")
            self._session.ref_above = closed[-1].close > level
            self._ref_regime_at = last_ts
        except Exception as exc:
            self._session.ref_above = None  # 모름 = 게이트 잠듦 (0.2.0 동작 폴백)
            self._ref_regime_at = None
            self._log.warning("live_ref_regime_unreadable", payload={"error": str(exc)[:140]})

    async def _pump_fills(self) -> None:
        """거래소에 **채워졌나** 물어 우편함에 넣는다 (T19 ⑤).

        Note:
            🔴 **세션이 판정하기 전에 돈다.** 세션은 동기라 네트워크를 못 기다리므로,
            여기서 미리 넣어 둬야 그 걸음이 채워진 것을 보고 판정한다.

            ⚠️ **비중은 거래소가 모른다.** *"채워졌다"* 까지만 말하고 그것이 계획의
            절반인지 전부인지는 우리가 부를 때 정한 값이다 (`ratio_of`).

            ⛔ **실패해도 던지지 않는다.** 조회 실패로 걸음이 멎으면 손절 감시도 같이
            멎는다 (절대 규칙 #8-1).
        """
        box = self._mailbox
        if box is None or not box.resting():
            return
        if not isinstance(self._orders, OrdersAware):
            return
        try:
            rows = await self._orders.open_orders(self.instrument)
        except Exception as exc:
            self._log.warning("live_fill_poll_failed", payload={"error": str(exc)[:140]})
            return
        alive = {str(row.get("id", "")) for row in rows}
        for ticket in box.resting():
            order_id = box.mine(ticket)
            ratio = box.ratio_of(ticket)
            if order_id is None or ratio is None or order_id in alive:
                continue
            # ⭐ 미결에서 사라졌다 = 채워졌거나 취소됐다. **어느 쪽인지 물어본다** —
            #   지어내면 안 산 것을 샀다고 적는다 (사고 ③ 과 같은 병).
            price = await self._fill_price(order_id)
            if price is None:
                box.dropped(ticket)
                continue
            box.note(ticket, price=price, ratio=ratio)
            await self._note_order(
                self._waiting_id(), role=f"진입{ticket[-1]}", status="filled", price=price
            )

    async def _fill_price(self, order_id: str) -> Decimal | None:
        """그 주문이 **얼마에** 채워졌나.

        Args:
            order_id: 거래소 주문 id.

        Returns:
            체결가. 안 채워졌으면 None.

        Note:
            🔴 **거래소가 말한 값을 그대로 쓴다.** 우리가 부른 지정가로 대신하면
            슬리피지가 사라져 원장이 사실보다 나은 말을 한다.
        """
        if not hasattr(self._orders, "recent_orders"):
            return None
        try:
            rows = cast(
                "list[dict[str, str]]",
                await self._orders.recent_orders(self.instrument),  # type: ignore[attr-defined]
            )
        except Exception:
            return None
        for row in rows:
            if str(row.get("id", "")) != order_id:
                continue
            if str(row.get("finish_as", "")) != "filled":
                return None
            with contextlib.suppress(Exception):
                return Decimal(str(row.get("fill_price") or row.get("price") or "0")) or None
        return None

    def _waiting_id(self) -> str:
        """대기 중인 매매의 id — 없으면 빈 문자열."""
        waiting = self._session.waiting_trade
        return "" if waiting is None else waiting.trade_id

    async def _pump_orders(self) -> None:
        """부탁받은 지정가를 **보내고**, 거두라는 것을 **거둔다** (T19 ⑤).

        Note:
            ⛔ **거절은 다시 안 보낸다.** 대개 규격 문제라 반복되고, 2026-08-19 에
            손절이 그 모양으로 27번 거절됐다 — 그 자리는 버리고 세는 쪽이 정직하다.
        """
        if self.observe_only:
            # ⛔ 관찰 전용이다 — 주문 경로를 통째로 건너뛴다.
            return
        box = self._mailbox
        if box is None:
            return
        for ticket, order_id in box.to_cancel():
            try:
                await self._orders.cancel_order(order_id)
            except Exception as exc:
                self._log.warning(
                    "live_limit_cancel_failed",
                    payload={"ticket": ticket, "error": str(exc)[:140]},
                )
                # 🔴 취소 실패의 가장 흔한 이유는 이미 채워졌다는 것 (2026-08-23 · 원장 없음 /
                #    거래소 보유중). 체결을 버리면 관리되지 않는 포지션이 남는다 — 되살린다.
                price = await self._fill_price(order_id)
                ratio = box.ratio_of(ticket)
                if price is not None and ratio is not None:
                    adopted = self._session.adopt_late_fill(ticket.rsplit(":", 1)[0], price, ratio)
                    self._log.error(
                        "live_cancel_raced_fill",
                        payload={
                            "ticket": ticket,
                            "price": str(price),
                            "adopted": adopted is not None,
                            "note": (
                                "취소 전에 체결됐다 — 원장으로 되살렸다"
                                if adopted is not None
                                else "취소 전에 체결됐는데 되살릴 대기 기록이 없다 — 감사가 올린다"
                            ),
                        },
                    )
            # ⚠️ 실패해도 뗀다 — 이미 채워졌거나 만료된 것이 대부분이고, 안 떼면
            #    걸음마다 같은 취소를 다시 보낸다.
            box.dropped(ticket)
        wants = box.to_place()
        if not wants:
            return
        waiting = self._session.waiting_trade
        if waiting is None:
            for want in wants:
                box.failed(want.ticket)
            return
        spec = await self._contract_spec()
        multiplier = Decimal(str(spec["quanto_multiplier"]))
        # 🔴 **시장가와 같은 계산을 쓴다** (2026-08-20 사고 ⓕ). 여기만 `sizing_base` 를
        #    날것으로 써서, 다른 판이 지갑을 먹은 상태에도 예산 전액으로 주문을 냈다.
        equity = await self._usable_equity(waiting.trade_id)
        for want in wants:
            try:
                contracts = contracts_for(
                    equity * want.ratio,
                    # 🔴 방향별 사이징 (T59): 세션이 계산한 방향 스케일 노출(롱3/숏1.02)을 쓴다 —
                    #    ledger.leverage(플랫)를 쓰면 숏도 3x 로 나가 검증된 롱3/숏1 이 깨진다.
                    waiting.leverage,
                    want.price,
                    multiplier,
                    size_min=int(spec.get("order_size_min", 1)),
                    size_max=int(spec.get("order_size_max", 0)) or None,
                )
                sent = await self._orders.submit_order(
                    limit_entry_order(
                        waiting,
                        self.instrument,
                        contracts,
                        want.price,
                        leg=int(want.ticket.rsplit(":", 1)[-1]),
                        run=self._run_key,
                        # ⭐ 룰이 선언하면 poc — 크로스 거부로 메이커 요율 보장 (T60 축④).
                        #   거부는 아래 except 로 떨어져 다음 판정에서 재시도된다.
                        post_only=self._session.post_only_entry,
                    )
                )
            except Exception as exc:
                box.failed(want.ticket)
                self.failures += 1
                self.last_error = f"지정가 진입 거절: {exc}"[:200]
                # 🔴 **거절은 값을 남겨야 진단이 된다** (2026-08-29).
                #
                #    post-only 거절 18건이 났는데, 남은 것이 "거절됐다" 뿐이라 원인을
                #    사후에 못 좁혔다 — `raw_json` 은 `{}` 였고 컨테이너 로그는 재기동에
                #    날아갔다. 그래서 **다음 한 건이면 갈리도록** 세 값을 같이 남긴다:
                #
                #      물러선 폭(plan)  원장이 계획한 평단이 종가에서 얼마나 물러났나
                #      나간 폭(leg)     실제로 거래소에 나간 지정가가 얼마나 물러났나
                #
                #    둘이 다르면 손실 지점은 **사다리**이고, 둘 다 0 이면 **탐지기 위쪽**이다.
                #    실측 대조군: 탐지기는 80,216.23 을 내는데 나간 값은 80,457.60(종가)였다.
                #
                # ⚠️ 진단이 주문 경로를 멈추면 안 된다 (절대 규칙 #8-1) — 값을 못 구하면
                #    그 칸만 비우고 간다.
                await self._note_order(
                    waiting.trade_id,
                    role=f"진입{want.ticket[-1]}",
                    status="rejected",
                    price=want.price,
                    error=str(exc)[:180],
                    raw=self._entry_forensics(want, waiting),
                )
                self._log.error(
                    "live_limit_entry_failed",
                    payload={
                        "ticket": want.ticket,
                        "error": str(exc)[:200],
                        **self._entry_forensics(want, waiting),
                    },
                )
                continue
            self.orders += 1
            box.sent(want.ticket, str(sent.broker_order_id or ""))
            await self._note_order(
                waiting.trade_id,
                role=f"진입{want.ticket[-1]}",
                status=sent.status.value,
                order_id=str(sent.broker_order_id or ""),
                contracts=str(contracts),
                price=want.price,
            )

    def _entry_forensics(self, want: Want, waiting: TradeRecord) -> dict[str, object]:
        """지정가 진입이 거절됐을 때 **원인을 가르는 값들**.

        Args:
            want: 거래소로 나간 주문 희망 (`price` · `long`).
            waiting: 원장의 대기 매매 (`entry` = 계획 평단).

        Returns:
            `{close, plan, sent, plan_pct, leg_pct, long}`. 못 구한 칸은 뺀다.

        Note:
            🔴 **`plan_pct` 와 `leg_pct` 가 이 함수의 존재 이유다.** 진입 지정가는 종가에서
            `entry_offset_pct`(0.30%) 만큼 물러나 있어야 한다 — 롱은 아래, 숏은 위. 그
            값이 사라지면 지정가가 **종가 그대로** 나가고, post-only 는 호가를 넘는 순간
            거부하므로 진입이 동전 던지기가 된다.

            ⚠️ 두 값을 **따로** 재는 이유: 원장까지는 물러나 있는데 나간 값만 종가면
            손실 지점이 사다리이고, 둘 다 0 이면 탐지기 위쪽이다. 하나만 재면 그 구분이
            안 되고, 구분이 안 되면 다음 거절에서도 똑같이 못 좁힌다.

            ⛔ 조회·계산이 실패해도 **던지지 않는다** — 진단 때문에 주문 경로가 멈추는
            것은 규칙 #8-1 위반이다.
        """
        out: dict[str, object] = {
            "sent": str(want.price),
            "plan": str(waiting.entry),
            "long": want.long,
        }
        try:
            rows = self._feed.observed(self.entry)
            close = rows[-1].close if rows else None
            if close is None or close <= 0:
                return out
            out["close"] = str(close)
            out["plan_pct"] = f"{(waiting.entry / close - 1) * 100:+.4f}"
            out["leg_pct"] = f"{(want.price / close - 1) * 100:+.4f}"
        except Exception as exc:  # 진단이 주문을 막지 않는다 (규칙 #8-1)
            out["forensics_error"] = str(exc)[:100]
        return out

    async def _resize_stop(self) -> None:
        """다리가 더 채워졌으면 손절 수량을 **늘린다** (T19).

        Note:
            🔴 **사다리에서 가장 위험한 자리다.** 다리1 만 채워진 상태로 손절을 걸었는데
            나중에 다리2 가 채워지면, 손절이 **절반만 덮어** 나머지 절반이 무방비로
            남는다. 그 상태는 조용하다 — 포지션도 있고 손절도 있으니 화면상 정상이다.

            ⭐ Gate 조건부는 포지션 전량을 닫는 형태로 걸리므로(`size=0` · `close=true`)
            수량이 자동으로 따라온다. 그래도 **다시 건다** — 어댑터가 바뀌거나 부분
            수량으로 걸리는 구현에서 조용히 뚫리는 것을 막는다.

            ⚠️ 값은 원장이 정한다 (절대 규칙 #4). 여기서는 **다시 걸기만** 한다.
        """
        box = self._mailbox
        if box is None:
            return
        held = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if held is None:
            return
        seen = self._stopped_at.get(held.trade_id)
        if seen == held.filled_ratio:
            return
        self._stopped_at[held.trade_id] = held.filled_ratio
        if seen is not None:
            self._log.info(
                "live_stop_resized",
                payload={
                    "trade_id": held.trade_id,
                    "was": str(seen),
                    "now": str(held.filled_ratio),
                    "note": "다리가 더 채워졌다 — 손절을 다시 건다",
                },
            )
        await self._guard_stop()

    async def _apply_half(self) -> None:
        """원장이 **신호 반익**을 적었으면 거래소에서도 절반을 던다 (사고 ③-b).

        Note:
            🔴 **원장과 거래소가 다른 말을 하고 있었다.** 원장은 *"절반 뺐다"* 고
            적는데 거래소에는 주문이 안 나갔다. 1차 익절 지정가는 **계획가**에 걸려
            있고 신호 반익은 그 가격에 닿지 않은 채로 나는 사건이라, 영영 안 채워진다.

            ⇒ 둘 중 하나는 거짓말이고, 고칠 쪽은 **집행**이다 — 매매법이 *"하락 추세전환
              신호가 있을 경우 절반 익절"* 이라고 말하기 때문이다 (사용자 흐름 ⑤).

            ⭐ **목표 도달 반익은 여기 오지 않는다.** 그쪽은 지정가가 실제로 채워져서
              난 사건이므로 거래소가 이미 처리했다 — `half_by` 가 그 둘을 가른다.

            ⚠️ **1차 익절 지정가를 먼저 취소한다.** 안 하면 나중에 가격이 계획가에
              닿았을 때 이미 없는 절반을 또 줄이려 한다.

            ⛔ 실패해도 던지지 않는다. 반익은 이익 쪽 행동이라 못 해도 포지션은 손절이
              지킨다 (익절 실패와 같은 등급 — 절대 규칙 #8-1).
        """
        if self.observe_only:
            return
        held = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if held is None or held.half_at is None or held.half_by is not HalfBy.SIGNAL:
            return
        if held.trade_id in self._halved:
            return
        self._halved.add(held.trade_id)
        if not isinstance(self._orders, PositionAware):
            return
        try:
            snapshot = await self._orders.position_snapshot(self.instrument)
            size = abs(int(Decimal(str(snapshot.get("size", "0")))) if snapshot else 0)
            half = size // 2
            if half <= 0:
                self._log.warning(
                    "live_half_too_small",
                    payload={"trade_id": held.trade_id, "size": size},
                )
                return
            # ⭐ 걸려 있던 1차 익절을 거둔다 — 그 절반은 지금 나간다.
            if isinstance(self._orders, OrdersAware):
                mine = order_key(held.trade_id, OrderKind.TAKE_PROFIT.value, 0, self._run_key)
                for row in await self._orders.open_orders(self.instrument):
                    if str(row.get("text", "")).lstrip("t-") == mine.lstrip("t-"):
                        with contextlib.suppress(Exception):
                            await self._orders.cancel_order(str(row["id"]))
            done = await self._orders.submit_order(
                close_order(held, self.instrument, half, run=self._run_key, revision=1)
            )
            self.orders += 1
            await self._note_order(
                held.trade_id,
                role="신호반익",
                status=done.status.value,
                order_id=str(done.broker_order_id or ""),
                contracts=str(half),
                price=held.half_price,
            )
            self._fired("signal_half", f"전환 신호로 {half} 계약을 덜었다")
        except Exception as exc:
            self.failures += 1
            self.last_error = f"신호 반익 실패: {exc}"[:200]
            self._log.error(
                "live_signal_half_failed",
                payload={
                    "trade_id": held.trade_id,
                    "error": str(exc)[:200],
                    "note": "원장은 절반을 뺐다고 적었는데 거래소는 전량이다 — 갈렸다",
                },
            )
            await self._note_order(
                held.trade_id, role="신호반익", status="rejected", error=str(exc)[:180]
            )

    def _step_seq(self) -> int:
        """지금 걸음의 판정 봉 순번 — 메이커 청산 만료를 세는 단위 (T126)."""
        rows = self._feed.observed(self.entry)
        if not rows:
            return 0
        judge = self._session.playbook.timeframe
        return int(rows[-1].ts.timestamp()) // max(interval_seconds(judge), 1)

    async def _maker_exit_price(self, record: TradeRecord) -> Decimal | None:
        """청산 지정가 — 현재가에서 **유리한 쪽**으로 offset 만큼 (T126).

        Args:
            record: 닫으려는 원장 기록.

        Returns:
            눈금에 맞춘 지정가. 가격을 못 구하면 None (호출부가 시장가로 간다).

        Note:
            롱 청산은 파는 것이므로 현재가 **위**, 숏 청산은 사는 것이므로 **아래**다.
            그쪽이라야 post-only 가 즉시 크로스하지 않는다.

            ⚠️ 눈금 반올림은 `_on_tick` 의 손절 규약과 **반대 방향**이다. 손절은
            느슨한 쪽(롱=내림)으로 가지만, 청산 지정가는 크로스를 피해야 하므로
            롱은 **올림**이다 — 그래서 `long=` 을 뒤집어 넘긴다.
        """
        rows = self._feed.observed(self.entry)
        if not rows:
            return None
        px = rows[-1].close
        if px <= 0:
            return None
        long = record.direction is Direction.LONG
        off = self._session.playbook.maker_exit_offset
        want = px * (Decimal(1) + off) if long else px * (Decimal(1) - off)
        spec = await self._contract_spec()
        tick = Decimal(str(spec.get("order_price_round", "0.1")))
        return _on_tick(want, tick, long=not long)

    async def _expire_maker_exit(self) -> None:
        """메이커 청산이 안 채워졌으면 **시장가로 마무리한다** (T126 · 대가 ①).

        Note:
            🔴 **이 함수가 이 기능의 안전장치 전부다.** 지정가는 안 채워질 수 있고,
            청산 신호가 뜬 뒤의 보유는 계획에 없는 위험이다. `maker_exit_bars` 봉이
            지나면 무조건 던진다 — 값이 좋아질 때까지 기다리지 않는다.

            ⭐ 기다리는 동안에도 거래소측 조건부 손절은 걸려 있다 (무방비 아님).
            ⛔ 청산은 리스크 **감소** 행동이라 로그가 실패해도 집행한다 (#8-1).
        """
        if self.observe_only or not self._maker_exit:
            return
        if not isinstance(self._orders, PositionAware):
            return
        bars = self._session.playbook.maker_exit_bars
        now_seq = self._step_seq()
        for trade_id, (placed_seq, _size) in list(self._maker_exit.items()):
            record = next(
                (item for item in self._session.ledger.records if item.trade_id == trade_id),
                None,
            )
            try:
                snapshot = await self._orders.position_snapshot(self.instrument)
                size = held_size(snapshot)
            except Exception as exc:
                self._log.warning("maker_exit_snapshot_failed", payload={"error": str(exc)[:140]})
                continue
            if size == 0:  # 채워졌다 — 고아 정리를 이제 한다
                # 🔴 **실현손익을 실제 체결가로 교정한다** (자동 재구성 벽돌 1).
                #    원장은 신호가로 청산을 찍었는데 실제 체결은 오프셋·슬리피지만큼
                #    다르다 — 거래소가 진실이므로 실측 체결가로 맞춘다.
                with contextlib.suppress(Exception):
                    await self._correct_exit_to_fill(trade_id)
                self._maker_exit.pop(trade_id, None)
                self._log.info(
                    "maker_exit_filled",
                    payload={"trade_id": trade_id, "waited_bars": now_seq - placed_seq},
                )
                await sweep(self._orders, self.instrument, why="메이커 청산 체결")
                continue
            if now_seq - placed_seq < bars or record is None:
                continue  # 아직 기다린다
            # 만료 — 시장가로 던진다
            self._maker_exit.pop(trade_id, None)
            try:
                await sweep(self._orders, self.instrument, why="메이커 청산 만료")
                done = await self._orders.submit_order(
                    close_order(record, self.instrument, abs(size), run=self._run_key, revision=1)
                )
                self.orders += 1
                self._log.warning(
                    "maker_exit_expired_to_market",
                    payload={
                        "trade_id": trade_id,
                        "waited_bars": now_seq - placed_seq,
                        "contracts": abs(size),
                        "note": "지정가가 안 채워졌다 — 계획대로 시장가로 마무리한다",
                    },
                )
                await self._note_order(
                    trade_id,
                    role="신호청산(만료시장가)",
                    status=done.status.value,
                    order_id=str(done.broker_order_id or ""),
                    contracts=str(abs(size)),
                )
            except Exception as exc:
                self.failures += 1
                self.last_error = f"메이커 청산 만료 시장가 실패 ({exc})"[:200]
                self._log.error(
                    "maker_exit_expiry_failed",
                    payload={"trade_id": trade_id, "error": str(exc)[:140]},
                )

    async def _resize_position(self) -> None:
        """보유 계약을 **지금 자본의 목표 노출**로 되맞춘다 (0.8.0 · 설계 B 개정).

        Note:
            🔴 격자 백테스트(+2,465%)는 봉마다 자본의 3x 로 노출을 되맞추는 셈인데
            라이브는 진입 때 계약 고정(+1,309% 상당)이었다 — 이익이 계좌에서 놀았다.
            사용자 승인(2026-08-25)으로 그 갭을 닫는다: 목표 자본 = 원장 예산 +
            **거래소 미실현**, 목표 계약 = 자본 x 방향배율 / 가격.

            ⚠️ **±5% 이내는 안 건드린다** — 잔조정은 수수료만 나간다.
            ⚠️ 미실현은 집행 계층의 사실 대조다 (`_usable_equity` 의 spare margin 과
              같은 선례) — 판정(진입·청산·손절값)에는 안 들어간다 (규칙 #5 경계 유지).
            ⚠️ 늘림은 리스크 증가라 호가가 말랐으면 보류한다 (#8-1 분류). 줄임은 항상.
            ⭐ 멱등키가 봉 시각 기반이라 재시작·중복 걸음에도 같은 봉엔 한 번만 나간다.
        """
        if self.observe_only or not self._session.playbook.relever:
            return
        held = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if held is None or not isinstance(self._orders, PositionAware):
            return
        rows = self._feed.observed(self.entry)
        if not rows:
            return
        bar = rows[-1]
        seq = int(bar.ts.timestamp()) // max(interval_seconds(self.entry), 1)
        if self._resized_at.get(held.trade_id) == seq:
            return
        try:
            snapshot = await self._orders.position_snapshot(self.instrument)
        except Exception as exc:
            self._log.warning("live_resize_unreadable", payload={"error": str(exc)[:140]})
            return
        size = int(Decimal(str(snapshot.get("size", "0") or "0")))
        if size == 0:
            return  # 포지션이 없다 — reconcile 소관
        unreal = Decimal(str(snapshot.get("unrealised_pnl", "0") or "0"))
        # 🔴 기준은 **펀드의 지금 배분**(margin_budget)이다 (2026-08-25 12:00 실측).
        #    sizing_base(WALLET rolling)는 진입 순간의 증거금에 동결돼 있어 — 예산이
        #    177.55 로 갱신됐는데 150 기준으로 조정했다 — 리밸런싱이 보유 포지션에
        #    영영 안 닿는다. 예산이 없는 단독 판은 sizing_base 로 폴백한다.
        base = self._session.ledger.margin_budget
        if base is None:
            base = self._session.ledger.sizing_base
        capital = base * held.filled_ratio + unreal
        if capital <= 0:
            return  # 자본이 잠식됐다 — 청산은 손절·ADX 규칙이 맡는다
        try:
            spec = await self._contract_spec()
            multiplier = Decimal(str(spec["quanto_multiplier"]))
            target = contracts_for(
                capital,
                held.leverage,
                bar.close,
                multiplier,
                size_min=int(spec.get("order_size_min", 1)),
                size_max=int(spec.get("order_size_max", 0)) or None,
            )
        except Exception as exc:
            self._log.warning("live_resize_spec_failed", payload={"error": str(exc)[:140]})
            return
        current = abs(size)
        delta = target - current
        # 무시 폭 = playbook.relever_band (기본 ±5% · T63 ① 승격 — 값은 설정이 SSoT).
        band = self._session.playbook.relever_band
        if delta == 0 or Decimal(abs(delta)) < Decimal(current) * band:
            self._resized_at[held.trade_id] = seq  # 이 봉은 볼 일 없다
            return
        grow = delta > 0
        if grow and not self._session.playbook.relever_grow:
            # 🔴 되맞춤의 **늘리는 쪽만** 껐다 (T136). 사는 자리가 고점이고 SMA 트레일
            #    손절은 한참 아래라, 되돌림에 커진 계약 + 올라간 평단으로 맞는다.
            #    줄임(리스크 감소)은 아래로 계속 간다.
            self._resized_at[held.trade_id] = seq
            return
        if grow and (self.dry is not None or self._session.ledger.tripped_at):
            # 호가 마름·낙폭 브레이커 — 리스크 **증가**는 보류한다 (#8-1 분류).
            # 줄임은 아래로 계속 간다 — 리스크 감소는 막지 않는다.
            return
        try:
            done = await self._orders.submit_order(
                resize_order(
                    held, self.instrument, abs(delta), grow=grow, seq=seq, run=self._run_key
                )
            )
            self.orders += 1
            self._resized_at[held.trade_id] = seq
            await self._note_order(
                held.trade_id,
                role="재레버",
                status=done.status.value,
                order_id=str(done.broker_order_id or ""),
                contracts=f"{'+' if grow else '-'}{abs(delta)}",
                price=bar.close,
            )
            self._fired(
                "resized",
                f"재레버 {current}→{target} 계약 (자본 {capital:.2f} · 미실현 {unreal:.2f})",
            )
            # ⭐ 익절 다리를 새 크기로 다시 건다 — 안 하면 늘린 뒤 다리가 포지션보다
            #   작아, 목표에 닿아도 일부만 나간다.
            await self._rearm_ladders(held, target)
            # 🔴 T229 — 줄이는 재레버는 그 계약의 손익을 **지금** 실현한다. 원장이 청산까지
            #    안 세면 마감 때 `pnl_drift` 가 난다 (NEAR 29→26 · BTC 3→1 실측). 체결 평단이
            #    응답에 있으면 (평단 - 진입) x 방향 x 계약 x 승수 를 매매에 누적한다.
            adjust = Decimal(0)
            avg_fill = getattr(done, "average_price", None)
            filled = Decimal(str(getattr(done, "filled_quantity", 0) or 0))
            if not grow and avg_fill is not None and filled > 0:
                adjust = (
                    (Decimal(str(avg_fill)) - held.entry)
                    * held.direction.sign
                    * filled
                    * multiplier
                )
                self._session.apply_realized_adjust(adjust)
            elif not grow:
                self._log.warning(
                    "live_resize_pnl_unknown",
                    payload={
                        "trade_id": held.trade_id,
                        "note": "감축 체결 평단이 응답에 없다 — 실현 조정을 못 적었다 (T229)",
                    },
                )
            self._log.info(
                "live_resized",
                payload={
                    "trade_id": held.trade_id,
                    "from": current,
                    "to": target,
                    "capital": str(capital),
                    "unrealised": str(unreal),
                    "status": done.status.value,
                    "realized_adjust": str(adjust),
                },
            )
        except Exception as exc:
            self.failures += 1
            self.last_error = f"재레버 실패: {exc}"[:200]
            self._log.error(
                "live_resize_failed",
                payload={
                    "trade_id": held.trade_id,
                    "delta": delta,
                    "error": str(exc)[:200],
                    "note": "포지션은 그대로다 — 손절이 전량을 덮고 있고 다음 봉에 다시 본다",
                },
            )

    async def _rearm_ladders(self, held: TradeRecord, total: int) -> None:
        """익절 다리를 **지금 크기**로 다시 건다 (0.8.0 재레버의 뒤처리).

        Note:
            🔴 재레버로 계약이 늘면 옛 익절 다리는 포지션보다 작다 — 목표에 닿아도
            일부만 나간다. 옛 다리를 거두고 같은 멱등키로 새 크기를 건다 (Gate 는
            취소 후 같은 text 재사용을 받는다 — 2026-08-25 00:00 reduce_slot 실측).

            ⛔ 실패해도 던지지 않는다 — 익절 부재는 손절 부재와 등급이 다르다 (#8-1).
        """
        if not isinstance(self._orders, OrdersAware):
            return
        try:
            legs = take_profit_orders(held, self.instrument, total, run=self._run_key)

            def clean(text: str) -> str:
                """멱등키 조각을 거래소가 받는 문자로 — 접두 `t-` 와 `:` 를 뗀다.

                Args:
                    text: 원 조각.

                Returns:
                    정리된 조각.
                """
                return text.lstrip("t-").replace(":", "-")

            keys = {clean(order.idempotency_key) for order in legs}
            for row in await self._orders.open_orders(self.instrument):
                if clean(str(row.get("text", ""))) in keys:
                    with contextlib.suppress(Exception):
                        await self._orders.cancel_order(str(row["id"]))
            for order in legs:
                await self._orders.submit_order(order)
                self.orders += 1
            self._log.info(
                "live_resize_ladder_rearmed",
                payload={"trade_id": held.trade_id, "contracts": total, "legs": len(legs)},
            )
        except Exception as exc:
            self._log.warning(
                "live_resize_ladder_failed",
                payload={"trade_id": held.trade_id, "error": str(exc)[:160]},
            )

    async def _apply_exit(self, before: TradeRecord | None) -> None:
        """원장이 **신호로 전량 닫았으면** 거래소 포지션도 닫는다 (0.7.0 · ADX 약화 청산).

        Args:
            before: 이 걸음의 판정 **전에** 열려 있던 기록. 없었으면 None.

        Note:
            🔴 **세션 전량 청산에는 미러가 없었다.** 반익은 `_apply_half` 가 옮기는데
            전량(SIGNAL_EXIT)은 아무도 안 옮겼다 — 지금까지 무해했던 이유는 SMA 트레일
            손절이 거래소측 조건부라 전량 청산이 항상 **거래소가 먼저** 였기 때문이다
            (`reconcile` 방향). ADX 약화 청산은 가격이 SMA 위에 멀리 있을 때 나는
            **세션발** 사건이라, 이 미러가 없으면 원장만 닫히고 포지션이 남는다.

            ⭐ **같은 걸음에 뒤집었으면(플립) 여기 안 온다** — 원장에 새 OPEN 기록이
              있으면 진입 주문 경로가 처리한다. 이 함수는 "닫고 현금" 만 옮긴다.

            ⛔ 실패해도 던지지 않는다 — 거래소측 조건부 손절이 아직 지키고 있고,
              감사(`ledger_mismatch`)가 갈린 상태를 사람에게 알린다 (절대 규칙 #8-1:
              청산은 리스크 감소 행동이므로 로그 실패가 집행을 막지 않는다).
        """
        if self.observe_only or before is None:
            return
        now = next(
            (item for item in self._session.ledger.records if item.trade_id == before.trade_id),
            None,
        )
        if now is None:
            return
        # ⭐ T233 ② — close 매매법은 손절도 세션이 마감 몸통으로 판정한다(거래소엔 보호 손절만
        #    있다). 그 STOP_LOSS·HALF_BREAKEVEN 은 아무도 안 옮기면 원장만 닫히고 포지션이
        #    남는다 → 여기서 시장가.
        #    touch 매매법의 손절은 거래소 조건부가 먼저 나가므로 예전처럼 reconcile 소관이다.
        mode_of = getattr(self._session, "stop_mode_of", None)
        mode = "touch" if mode_of is None else str(mode_of(before))
        stop_by_close = mode == "close" and now.outcome in (
            Outcome.STOP_LOSS,
            Outcome.HALF_BREAKEVEN,
        )
        if now.outcome is not Outcome.SIGNAL_EXIT and not stop_by_close:
            return
        if any(item.outcome is Outcome.OPEN for item in self._session.ledger.records):
            # 같은 걸음에 뒤집었다 — 반대 진입 주문이 그쪽 경로로 나간다.
            return
        if not isinstance(self._orders, PositionAware):
            return
        try:
            snapshot = await self._orders.position_snapshot(self.instrument)
            size = held_size(snapshot)
            if size == 0:
                # 거래소도 이미 없다 (손절이 먼저 나갔다) — reconcile 소관이다.
                return
            # ── 메이커 청산 (T126 · 1.1.0) — 지정가로 걸고 기다린다 ──
            #   기다리는 동안 거래소측 조건부 손절은 그대로다 (무방비 아님).
            #   만료는 `_expire_maker_exit` 가 시장가로 마무리한다 — **반드시** 돈다.
            bars = self._session.playbook.maker_exit_bars
            # 손절은 손절이다 — 마감 판정 손절은 지정가로 기다리지 않는다.
            limit = await self._maker_exit_price(now) if (bars > 0 and not stop_by_close) else None
            if limit is not None:
                done = await self._orders.submit_order(
                    close_limit_order(
                        now, self.instrument, abs(size), limit, run=self._run_key, revision=0
                    )
                )
                self._maker_exit[now.trade_id] = (self._step_seq(), abs(size))
                role, note = "신호청산(지정가)", f"{limit} 에 걸고 {bars}봉 기다린다"
            else:
                done = await self._orders.submit_order(
                    close_order(now, self.instrument, abs(size), run=self._run_key, revision=0)
                )
                role, note = (
                    ("손절(마감판정)", "시장가 전량 — close 매매법의 손절은 세션이 판정한다")
                    if stop_by_close
                    else ("신호청산", "시장가 전량")
                )
            self.orders += 1
            await self._note_order(
                now.trade_id,
                role=role,
                status=done.status.value,
                order_id=str(done.broker_order_id or ""),
                contracts=str(abs(size)),
            )
            kind = "마감 판정 손절" if stop_by_close else "신호 청산"
            self._fired(
                "stop_exit" if stop_by_close else "signal_exit",
                f"{kind} — {abs(size)} 계약 ({note})",
            )
            if limit is not None:
                return  # 아직 안 닫혔다 — 고아 정리(sweep)는 체결·만료 뒤에 한다
            # ⭐ 닫기가 먼저, 거두기가 나중이다 (§1.2.1) — 익절 지정가·조건부 손절이
            #   고아로 남으면 다음 진입 때 없는 포지션을 줄이려 든다.
            await sweep(self._orders, self.instrument, why="신호 청산")
        except Exception as exc:
            self.failures += 1
            self.last_error = f"신호 청산 실패: {exc}"[:200]
            self._log.error(
                "live_signal_exit_failed",
                payload={
                    "trade_id": now.trade_id,
                    "error": str(exc)[:200],
                    "note": "원장은 닫았는데 거래소는 보유다 — 조건부 손절이 아직 "
                    "지키고 있고, 감사가 갈린 상태를 알린다",
                },
            )
            await self._note_order(
                now.trade_id, role="신호청산", status="rejected", error=str(exc)[:180]
            )

    async def _guard_stop(self, *, escalate: bool = True) -> None:
        """포지션이 있으면 브로커측 손절이 걸려 있는지 확인하고, 없으면 건다.

        Args:
            escalate: 못 걸었을 때 **연속 실패로 세어 시장가 청산까지 갈 것인가**.
                걸음에서 부를 때만 참이다 (아래 Note).

        Note:
            🔴 **이것이 서버 다운 중 손절의 유일한 방어선이다** (spec §7 · §12.6).
            우리가 감시하는 손절은 프로세스가 살아 있어야 돈다 — 정전·재부팅·인터넷
            끊김에 포지션이 무방비로 남는다.

            🔴 **만료 때문에 매 걸음 확인한다.** Gate 조건부 주문은 기본 24시간에
            사라지고, 사라지는 것이 **조용하다** — 포지션은 있는데 손절이 없는 상태가
            되고 아무 신호도 안 난다. 그래서 "걸었다" 를 기억하지 않고 **거래소에 묻는다**
            (우리 기억은 만료를 모른다).

            🔴 **"매 걸음" 이 4시간이었다** (2026-08-31 실측). 이 함수는 `_walk_once`
            안에서 `session.step()` 의 조기 반환 **뒤에** 있고, 라이브 급전의
            `advance()` 는 `STEP_FRAME`(5m) 을 무시하고 **진입축** 기준으로 답한다
            (`live_feed.advance` 의 Note). 진입축이 4h 인 판에서는 이 함수가
            **4시간에 한 번** 돈다:

            ```
            08-31 00:00:00  조건부 등록          trigger 76504.4 · close_long
            08-31 13:47:53  Gate 가 죽였다        "price deviated too much from mark"
            08-31 13:48:22  감사 stop_missing     ← 29초 만에 알았다
            08-31 16:00:00  다음 4h 걸음          ← 여기까지 2시간 12분 무방비
            ```

            같은 날 ETH 도 05:31 에 취소되어 08:00 걸음까지 **2시간 29분** 무방비였다.
            저절로 나으니 화면에서는 배너가 깜빡이는 것으로 보인다.

            ⇒ 점검 루프(`_keep_probing` · 30초)도 이 함수를 부른다. **탐지 주기와
              복구 주기를 같게** 만드는 것이 요점이다 — 30초 만에 알고 4시간을
              기다리는 것은 아는 것이 아니다.

            ⚠️ **점검 루프에서는 `escalate=False`** 다. 연속 실패 문턱
            (`STOP_GUARD_LIMIT`)은 *걸음* 을 세도록 골라진 값이라, 30초 루프에 그대로
            두면 일시적인 레이트리밋 세 번에 **90초 만에 시장가로 던진다**. 문턱을
            시간 기준으로 바꾸는 것은 별개 축이다 (절대 규칙 #12: 한 번에 한 축).

            ⚠️ 손절 가격은 원장이 정한다 (절대 규칙 #4). 러너는 옮기기만 한다 —
            본절 상향도 원장이 `planned_stop` 을 올리면 여기서 새 값으로 다시 건다.

            ⛔ 실패를 삼키지 않는다. 손절을 못 걸었다는 것은 **포지션이 무방비**라는
            뜻이므로 사람이 봐야 한다 (절대 규칙 #8).
        """
        # ⛔ **겹쳐 돌면 조건부가 두 개 남는다** — `_arming` docstring 참고.
        #    ⚠️ `locked()` 와 `acquire()` 사이에 await 가 없어야 이 검사가 성립한다.
        if self._arming.locked():
            return
        async with self._arming:
            await self._guard_stop_once(escalate=escalate)

    async def _guard_stop_once(self, *, escalate: bool) -> None:
        """`_guard_stop` 의 본체 — **`_guard_stop` 만 부른다** (겹침 방어가 거기 있다)."""
        if self.observe_only:
            # ⛔ 관찰 전용이다 — 걸 포지션이 없으므로 손절도 없다.
            return
        held = next(
            (item for item in self._session.ledger.records if item.outcome is Outcome.OPEN),
            None,
        )
        if held is None:
            return
        if not hasattr(self._orders, "stops_for"):
            # 조건부를 못 거는 어댑터다 — 능력표에 없으면 여기 오지 않는다.
            return
        # ⭐ T233 ② — 어디에 걸지는 매매법의 `stop_mode` 가 정한다: touch 면 손절선,
        #    close 면 보호 손절 (`_guard_price`).
        guard = self._guard_price(held)
        failed = await self._arm_stop(held)
        # ⭐ **걸 기회를 가졌다** — 성공이든 실패든. 이 뒤의 "조건부 0건" 은 진짜 무방비다
        #    (사용자 신고 2026-08-20: 손절이 나가기 **전에** 경보가 먼저 울렸다).
        self._armed_for = held.trade_id
        if isinstance(failed, StopBlownError):
            # 🔴 **이건 "못 걸었다" 가 아니라 "이미 지났다" 다** (2026-08-20 실측).
            #
            #    Gate 의 `TRIGGER_PRICE_{LESS,GREATE}_LAST` 는 발동가가 현재가의 반대쪽
            #    이라는 뜻이고, 그 말은 **손절선을 이미 통과했다**는 것이다. 1틱 완화로도
            #    안 되면 남은 해석은 하나뿐이다.
            #
            #    ⛔ 그런데 지금까지는 실패로 세고 **3회를 기다렸다.** 밤사이 실측:
            #
            #      stop_guard_failed 33회 (전부 GREATE_LAST) → misses 1·2·3 → panic 10회
            #
            #    기다리는 동안 포지션은 **조건부 없이** 남는다. 백엔드가 그 사이에 죽으면
            #    (실제로 06:47 에 죽었다) 아무도 안 지킨다.
            #
            # ⇒ 기다리지 않는다. 손절이 발동한 것이므로 **그 자리에서 던진다.**
            self.stop_misses = 0
            self._fired("stop_hit", f"손절선({guard})을 이미 지났다 — 즉시 청산")
            self._log.error(
                "live_stop_already_through",
                payload={
                    "trade_id": held.trade_id,
                    "stop": str(guard),
                    "planned_stop": str(held.planned_stop),
                    "error": str(failed)[:180],
                    "note": "발동가가 현재가 반대쪽이다 = 손절이 이미 발동했다 — 시장가로 던진다",
                },
            )
            await self._note_order(held.trade_id, role="손절", status="through", price=guard)
            await self._panic_close(held, "손절선을 이미 지났다 — 조건부를 걸 수 없다")
            return
        if failed is not None:
            exc = failed
            self._log.error(
                "live_runner_stop_guard_failed",
                payload={
                    "trade_id": held.trade_id,
                    "stop": str(guard),
                    "planned_stop": str(held.planned_stop),
                    "error": f"{type(exc).__name__}: {exc}",
                    "misses": self.stop_misses + (1 if escalate else 0),
                    "limit": STOP_GUARD_LIMIT,
                    "escalate": escalate,
                    "note": "포지션이 무방비다 — 브로커측 손절이 없다",
                },
            )
            # ⚠️ **점검 루프(30초)에서는 안 센다.** 문턱이 *걸음* 을 세도록 골라진
            #    값이라 여기서 같이 세면 레이트리밋 세 번에 90초 만에 시장가 청산이
            #    난다 (`_guard_stop` docstring). 다음 걸음이 여전히 못 걸면 그때 센다.
            if escalate:
                self.stop_misses += 1
            # 🔴 **못 지키는 동안은 새로 사지 않는다** (2026-08-20 · A3). 무방비인데 또
            #    사면 무방비가 하나 더 는다 — 밤사이 XRP 17건이 그 고리였다.
            self._session.guarded = False
            self.last_error = f"손절 실패 {self.stop_misses}/{STOP_GUARD_LIMIT}: {exc}"[:200]
            # 🔴 **무방비를 기록에 남긴다.** 화면 문구는 다음 재시작에 사라지지만
            #    이 행은 남는다 — "그때 손절이 없었다" 를 나중에 증명할 유일한 근거다.
            await self._note_order(
                held.trade_id,
                role="손절",
                status="failed",
                price=guard,
                error=str(exc)[:180],
            )
            if escalate and self.stop_misses >= STOP_GUARD_LIMIT:
                # 🔴 **던진다.** 손절 없는 레버리지 포지션은 손실에 바닥이 없다 —
                #    연속 실패가 여기까지 왔으면 다음 걸음을 기다릴 이유가 없다.
                #
                # ⛔ **점검 루프는 여기 못 온다** (`escalate=False`). 시장가 청산은
                #    되돌릴 수 없고, 30초 루프가 그 방아쇠를 쥐면 `_panic_close` 가
                #    실패했을 때 **30초마다 다시 던진다**. 새 경로는 *거는 일*만 한다.
                self._fired("panic_close", f"손절 {self.stop_misses}회 연속 실패")
                await self._panic_close(held, f"손절을 {self.stop_misses}번 연속 못 걸었다")
        else:
            # ⭐ 한 번이라도 성공하면 **연속**이 끊긴다.
            if self.stop_misses:
                self._log.info(
                    "live_runner_stop_recovered",
                    payload={"trade_id": held.trade_id, "after_misses": self.stop_misses},
                )
            self.stop_misses = 0
            # ⭐ 다시 지켜지고 있다 — 새 진입을 받는다 (A3 의 반대 방향).
            self._session.guarded = True
            # ⚠️ 매 걸음 갈아 끼운다 — 역할당 행이 하나라 **지금 걸린 값**이 남는다.
            # 🔴 **조건부 주문 id 를 같이 적는다** (사용자 신고 2026-08-20). 발동하면
            #    Gate 가 `ao-{id}` 로 주문을 만드는데 그 이름에 우리 매매 id 가 없다 —
            #    이 값이 **손절 체결을 매매에 잇는 유일한 열쇠**다.
            await self._note_order(
                held.trade_id,
                role="손절",
                status="placed",
                price=guard,
                order_id=self._stop_id,
            )

    def _guard_price(self, held: TradeRecord) -> Decimal:
        """거래소에 걸 조건부 손절 자리 — `Session.guard_price` (T233 ②).

        Args:
            held: 보유 중인 기록.

        Returns:
            touch 매매법은 손절선, close 매매법은 보호 손절. 세션에 그 메서드가 없으면(시험의 가짜)
            손절선 그대로 — 1.5.0 까지의 동작.
        """
        guard_of = getattr(self._session, "guard_price", None)
        if guard_of is None:
            return held.planned_stop
        return cast("Decimal", guard_of(held))

    async def _arm_stop(self, held: TradeRecord) -> Exception | None:
        """손절을 건다 — **본절이 문턱에 걸리면 1틱 띄워** 한 번 더 시도한다.

        Args:
            held: 보유 중인 기록.

        Returns:
            끝내 못 걸었으면 그 예외. 걸었으면 None.

        Note:
            🔴 **본절이 거래소 문턱과 정확히 겹친다** (2026-08-20). 반익 뒤 손절은
            진입가로 올라가는데, 가격이 그 자리로 돌아오면 발동가 == 현재가가 되어
            Gate 가 거부한다. 그러면 3회 연속 실패 → `panic_close` → 재진입 → 반복이다.

            ⇒ 그 거절에 한해 **1틱만** 느슨한 쪽으로 옮겨 다시 건다. 롱이면 진입 아래로,
              숏이면 위로 — 본절보다 딱 한 눈금 나쁜 자리다.

            ⚠️ **현재가가 아니라 원장 값 기준으로 옮긴다.** 현재가를 따라가면 가격이
            멀어질 때마다 손절도 같이 멀어져 **끌려간다** — 그것은 손절이 아니다.
            한 번 옮겨도 안 되면 그 손절은 이미 뚫린 것이므로, 옛 경로(3회 → 시장가
            청산)가 그대로 받는다.

            ⛔ **원장을 안 고친다** (절대 규칙 #4·#5). 거래소에 건 값만 한 눈금 다르고,
            판정과 손익 계산의 근거는 여전히 `planned_stop` 이다.
        """
        long = held.direction is Direction.LONG
        spec = await self._contract_spec()
        tick = Decimal(str(spec.get("order_price_round", "0.1")))
        # 🔴 **눈금에 맞춰 보낸다** (2026-08-20 실측 18건: `trigger.price price is not an
        #    integer multiple of a price unit`). 원장의 손절가는 봉 가격에서 나오므로
        #    계약 눈금의 배수라는 보장이 없다 — 어긋나면 그 손절은 **영영 안 걸린다.**
        #
        # ⚠️ **느슨한 쪽으로 내린다.** 촘촘한 쪽으로 반올림하면 계획보다 이른 손절이 되고,
        #    그것은 원장이 정한 값을 집행이 바꾸는 일이다 (절대 규칙 #4).
        # ⭐ T233 ② — touch 는 손절선, close 는 보호 손절 (`_guard_price`).
        guard = self._guard_price(held)
        wanted = _on_tick(guard, tick, long=long)
        try:
            # 🔴 **조건부 주문 id 를 붙잡는다** (사용자 신고 2026-08-20). 이것이 발동하면
            #    Gate 가 `ao-{id}` 라는 이름으로 주문을 만드는데, 그 이름에는 우리 매매
            #    id 가 없다 — 이 값을 안 적어 두면 **콘솔이 손절 체결을 영영 못 잇는다**
            #    (실측: 화면이 "RUN 미상 · 배율 — · 수익률 —" 로 떴다).
            #
            # ⚠️ 이미 맞게 걸려 있으면 None 이 온다. 그때는 **덮어쓰지 않는다** — 옛 id 가
            #    여전히 유효한 열쇠다.
            made = cast(
                "str | None",
                await self._orders.stops_for(  # type: ignore[attr-defined]
                    self.instrument, wanted, long=long
                ),
            )
            self._stop_id = made or self._stop_id
        except Exception as first:
            if not any(label in str(first) for label in TRIGGER_REJECTED):
                return first
            loose = wanted - tick if long else wanted + tick
            try:
                again_id = cast(
                    "str | None",
                    await self._orders.stops_for(  # type: ignore[attr-defined]
                        self.instrument, loose, long=long
                    ),
                )
                self._stop_id = again_id or self._stop_id
            except Exception as again:
                if any(label in str(again) for label in TRIGGER_REJECTED):
                    # ⭐ 한 눈금 물러나도 같은 소리를 한다 = **가격이 이미 멀리 지났다.**
                    return StopBlownError(str(again))
                return again
            self._fired("stop_nudged", f"본절 손절을 1틱 띄워 {loose} 로 걸었다")
            self._log.warning(
                "live_stop_nudged",
                payload={
                    "trade_id": held.trade_id,
                    "planned": str(held.planned_stop),
                    "placed": str(loose),
                    "tick": str(tick),
                    "note": "발동가가 현재가와 같아 거절됐다 — 본절의 정상 모습이다",
                },
            )
        return None

    async def _panic_close(self, held: TradeRecord, why: str) -> None:
        """포지션을 **시장가로 던진다** — 손절을 못 거는 최후 수단.

        Args:
            held: 보유 중인 매매 기록.
            why: 왜 던지는가. 로그와 화면에 그대로 나간다.

        Note:
            🔴 사용자 요구 2026-08-18: *"최악의 경우 시장가로 던질 수 있게라도"*.

            ⛔ **되돌릴 수 없다.** 그래도 손절 없이 버티는 것보다 낫다 — 이 판단은
            리스크를 줄이는 방향이므로 절대 규칙 #3 과 #8-1 에 부합한다.

            ⚠️ **이것마저 실패하면 사람이 봐야 한다.** 그때는 앱이 할 수 있는 것이 없다 —
            거래소 콘솔에서 손으로 닫는 수밖에 없고, 그 사실을 에러로 남긴다.

            ⚠️ 원장은 여기서 안 고친다. 거래소 응답으로 원장을 되돌리면 결정론 코어가
            계좌 상태에 따라 달라진다 (절대 규칙 #5) — T14 가 그 경계를 정한다.
        """
        self._log.error(
            "live_runner_panic_close",
            payload={"trade_id": held.trade_id, "why": why, "note": "시장가 전량 청산을 보낸다"},
        )
        try:
            # 🔴 **거래소 실제 포지션 크기를 읽어 그만큼 닫는다** (§2 · 2026-09-01).
            #    예전에는 `sizing_base` 로 계약 수를 **재계산**했다 — 재레버·부분체결로
            #    실제 포지션이 더 크면 일부만 닫히고 잔량이 무방비로 남았다. `reduce_only`
            #    는 과대청산(뒤집힘)만 막지 과소청산은 못 막는다. 최후 안전장치가 전량을
            #    보장 못 하면 안전장치가 아니다.
            contracts = 0
            if isinstance(self._orders, PositionAware):
                with contextlib.suppress(Exception):
                    snapshot = await self._orders.position_snapshot(self.instrument)
                    contracts = abs(held_size(snapshot))
            if contracts == 0:
                # 스냅샷을 못 읽었다 — 계산값으로 폴백한다 (전량보장은 못 하지만 안 던지는
                # 것보다 낫다). reduce_only 라 넘쳐도 뒤집히지는 않는다.
                spec = await self._contract_spec()
                multiplier = Decimal(str(spec["quanto_multiplier"]))
                contracts = contracts_for(
                    self._session.ledger.sizing_base,
                    held.leverage,
                    held.entry,
                    multiplier,
                    size_min=int(spec.get("order_size_min", 1)),
                    size_max=int(spec.get("order_size_max", 0)) or None,
                )
            done = await self._orders.submit_order(
                close_order(held, self.instrument, contracts, revision=0)
            )
            self.orders += 1
            self.stop_misses = 0
            self.last_error = f"손절 불가로 시장가 청산했다 ({why})"[:200]
            self._log.error(
                "live_runner_panic_closed",
                payload={"trade_id": held.trade_id, "status": done.status.value, "why": why},
            )
        except Exception as exc:
            # 🔴 여기까지 실패하면 앱이 할 수 있는 것이 없다.
            self.last_error = f"🔴 손절도 청산도 실패 — 거래소 콘솔에서 손으로 닫는다: {exc}"[:200]
            self._log.error(
                "live_runner_panic_close_failed",
                payload={
                    "trade_id": held.trade_id,
                    "error": str(exc)[:200],
                    "note": "포지션이 무방비로 남았다 — 사람이 거래소에서 닫아야 한다",
                },
            )

    async def _contract_spec(self) -> dict[str, str]:
        """계약 명세 — 승수·최소·최대를 읽는다 (캐시).

        Returns:
            명세 원문.

        Note:
            ⛔ 승수를 상수로 박지 않는다. 계약마다 다르고 거래소가 바꿀 수 있으며,
            testnet 과 라이브도 다르다 (`maintenance_rate` 0.004 vs 0.003 실측).

            🔴 **주문 어댑터에게 먼저 묻는다** (2026-08-19 사고 ① 의 남은 절반).
            아침에 `GatePaperAdapter.contract_spec` 을 만들어 testnet 을 보게 했는데,
            **여기가 여전히 조회 어댑터(`_quotes` = 라이브 API)를 부르고 있었다** —
            고친 것이 배선되지 않아 그대로였다.

            그 차이가 만든 사고: ETH 손절 발동가 1919.02 를 보냈는데 400 이 왔다.
            **라이브** 명세의 호가 단위 0.01 로는 정당한 값이지만, 주문은 testnet 으로
            나가고 있었다. 20배 숏이 손절 없이 굴렀고 안전장치가 세 번 실패한 뒤
            시장가로 던졌다.

            ⚠️ **어느 거래소의 값인지가 곧 그 값의 뜻이다.** 계약 명세는 수량·눈금·
            레버리지 한도를 정한다 — 다른 곳 값으로 만든 가격은 전부 무효다.

            ⚠️ 조회 어댑터로 떨어지는 길을 남긴다. 주문 어댑터가 이 능력을 안 가진
            구현일 수 있고, 그때 명세 자체를 못 읽으면 판이 통째로 안 뜬다.
        """
        if self._spec is None:
            source = self._orders if hasattr(self._orders, "contract_spec") else self._quotes
            self._spec = cast(
                "dict[str, str]",
                await source.contract_spec(self.instrument),  # type: ignore[union-attr]
            )
        return self._spec

    async def _usable_equity(self, trade_id: str) -> Decimal:
        """이 주문에 **실제로 쓸 수 있는 돈** — 예산과 계좌 가용의 작은 쪽.

        Args:
            trade_id: 흔적에 남길 매매 id.

        Returns:
            여유를 뺀 금액.

        Note:
            🔴 **시장가 경로에만 있던 계산이다** (2026-08-20 사고 ⓕ). 지정가 사다리는
            `sizing_base` 를 그대로 썼고, 그래서 다른 판이 지갑을 먹은 상태에서도
            예산 전액으로 주문을 냈다 — `INSUFFICIENT_AVAILABLE` 이 반복된 이유의 절반이
            여기다 (나머지 절반은 배율 ⓔ).

            ⇒ 두 경로가 **같은 계산**을 쓰게 뽑아냈다. 한쪽에만 있는 안전장치는
              언젠가 다른 쪽에서 사고가 난다.
        """
        budget = self._session.ledger.sizing_base
        spare = await self._spare_margin()
        equity = (budget if spare is None else min(budget, spare)) * MARGIN_HEADROOM
        if spare is not None and spare < budget:
            self.squeezed = {
                "budget": str(budget),
                "usable": str(spare),
                "note": "다른 판이 지갑을 쓰고 있다 — 선착순이다",
            }
            self._fired("margin_share", f"예산 {budget} 중 {spare} 만 쓸 수 있다")
            self._log.warning(
                "live_margin_shared",
                payload={**self.squeezed, "trade_id": trade_id},
            )
        else:
            self.squeezed = None
        return equity

    async def _send(self, record: TradeRecord, spec: dict[str, str]) -> None:
        """계획 하나를 진입 + 익절 주문으로 보낸다.

        Args:
            record: 원장 기록 (체결된 진입).
            spec: 계약 명세.

        Raises:
            OrderMappingError: 수량을 만들 수 없는 경우.

        Note:
            🔴 **반올림 드리프트를 기록한다.** 계약이 정수라 원장이 가정한 "자본 전액"
            과 어긋나고, 그 차이를 안 남기면 나중에 "라이브가 백테스트보다 나쁘다" 의
            원인을 전략으로 오해한다 (§1-0s 관측 규약).
        """
        multiplier = Decimal(str(spec["quanto_multiplier"]))
        size_min = int(spec.get("order_size_min", 1))
        size_max = int(spec.get("order_size_max", 0)) or None
        # 🔴 **증거금으로 계산한다** (T14-3 · 2026-08-18). `equity` 를 쓰면 금고 돈이
        #    증거금이 되어 유보가 아무 일도 하지 않는다 (실측: 유보 100% 에 주문 2.4% 감소).
        #
        # ⚠️ **거래소가 요구하는 증거금은 우리 계산보다 크다** (2026-08-18 실측):
        #
        #      우리 계산   234계약 → 499.70 USDT
        #      Gate 요구   502.19 USDT  → 400 INSUFFICIENT_AVAILABLE
        #
        #    차이는 **표시가(mark) 와 체결가의 차 + 수수료**다. 증거금을 잔액에 딱 맞춰
        #    넣으면 **항상 거부된다** — 그리고 그 실패는 화면에서 "주문 0건" 으로만 보인다.
        #
        # ⇒ 라이브에서만 여유를 둔다. 백테스트 원장은 손대지 않는다 (같은 입력에 같은
        #   출력이어야 한다 — 절대 규칙 #5).
        # 🔴 **판들이 지갑을 나눠 쓴다** (ㄷ · 사용자 확정 2026-08-18). 원장의
        #    `sizing_base` 는 **이 판의 예산**이고, 거래소 `available` 은 **계정 전체가
        #    지금 쓸 수 있는 돈**이다 — 다른 판이 이미 잡은 증거금은 거기서 빠져 있다.
        #
        #    둘 중 작은 쪽이 실제로 쓸 수 있는 것이다. 안 자르면 두 판이 각각 300 을
        #    굴린다고 믿으면서 같은 지갑에서 600 을 쓰려 하고, 판마다 "내 예산은 300"
        #    이라 **아무도 초과를 모른다.**
        #
        # ⚠️ **선착순이 된다** — 먼저 진입한 판이 지갑을 먹고 뒤의 판은 못 들어간다.
        #    그것을 버그가 아니라 **규칙으로** 받아들이고, 화면이 그 사실을 말한다
        #    (`budget` 필드).
        #
        # ⛔ 백테스트 원장은 손대지 않는다 — 여기(라이브 경로)에만 있다 (규칙 #5).
        equity = await self._usable_equity(record.trade_id)
        # 🔴 방향별 사이징 (T59): 세션이 계산한 방향 스케일 노출(record.leverage — 롱3/숏1.02)을
        #    쓴다. ledger.leverage(플랫 3)를 쓰면 숏도 3x 로 나가 검증된 롱3/숏1 이 깨진다.
        leverage = record.leverage
        contracts = contracts_for(
            equity,
            leverage,
            record.entry,
            multiplier,
            size_min=size_min,
            size_max=size_max,
        )
        drift = rounding_drift_pct(contracts, equity, leverage, record.entry, multiplier)
        self._log.info(
            "live_runner_sizing",
            payload={
                "trade_id": record.trade_id,
                "contracts": contracts,
                "equity": str(equity),
                "leverage": str(leverage),
                "rounding_drift_pct": f"{drift * 100:.4f}",
                "note": "원장은 자본 전액을 가정한다 — 이 드리프트만큼 실제가 작다",
            },
        )

        # ⚠️ **보내기 전에 표시한다.** 예외가 나면 `sending` 이 남고, 그것이 곧
        #    *"보내려 했는데 응답을 못 받았다"* 는 뜻이다 — 흔적이 없으면 시도조차
        #    안 한 것과 구별되지 않는다 (절대 규칙 #8).
        self.placed[record.trade_id] = {
            "status": "sending",
            "order_id": "",
            "contracts": str(contracts),
        }
        # 🔴 **기하가 뒤집힌 계획은 주문을 안 낸다** (2026-08-18: 롱인데 1차 익절이
        #    진입 아래인 계획으로 4건이 나가 -4.48% 로 끝났다).
        #
        #    이익을 내려면 가격이 반대로 가야 하는 익절은 **매매가 아니다.** 돈을 걸기
        #    전에 막는 것이 유일하게 싼 지점이다.
        #
        # ⚠️ 원장은 그대로 둔다 — 거래소 응답으로 원장을 되돌리면 결정론이 깨진다
        #    (절대 규칙 #5). 대신 `placed` 에 남겨 화면이 **거래소에 없다**고 말한다.
        fault = geometry_fault(record)
        if fault:
            self.failures += 1
            self.last_error = f"계획 기하 오류로 주문을 막았다: {fault}"[:200]
            self._fired("geometry", fault)
            self.placed[record.trade_id] = {
                "status": "blocked",
                "order_id": "",
                "contracts": "0",
                "error": fault[:180],
            }
            self._log.error(
                "live_runner_geometry_blocked",
                payload={
                    "trade_id": record.trade_id,
                    "direction": record.direction.value,
                    "entry": str(record.entry),
                    "stop": str(record.planned_stop),
                    "first": str(record.planned_first),
                    "target": str(record.planned_target),
                    "fault": fault,
                    "note": "주문을 내지 않았다 — 이 계획으로는 이익이 날 수 없다",
                },
            )
            # 🔴 **막은 것도 남긴다.** 아무 행이 없으면 "주문을 안 냈다" 와 "냈는데
            #    거래소가 못 받았다" 가 구별되지 않는다 (절대 규칙 #8).
            await self._note_order(
                record.trade_id, role="진입", status="blocked", error=fault[:180]
            )
            return
        entry = await self._orders.submit_order(
            entry_order(record, self.instrument, contracts, run=self._run_key)
        )
        self.orders += 1
        self.placed[record.trade_id] = {
            "status": entry.status.value,
            "order_id": str(entry.broker_order_id or ""),
            "contracts": str(contracts),
        }
        await self._note_order(
            record.trade_id,
            role="진입",
            status=entry.status.value,
            order_id=str(entry.broker_order_id or ""),
            contracts=str(contracts),
            price=record.entry,
        )
        # 🔴 교정 원장 (T185) — 백테스트 가정과의 차이를 나중에 재려면 여기가 유일한
        #    자리다. `contracts_for` 가 호가·최소·최대로 깎기 **전** 값을 같이 남긴다.
        wanted = (
            (equity * leverage / (record.entry * multiplier))
            if record.entry > 0 and multiplier > 0
            else None
        )
        await self._note_calibration(
            kind="entry",
            trade_id=record.trade_id,
            role="진입",
            intended_price=record.entry,
            judge_ts=record.placed_at,
            wanted_contracts=wanted,
            sent_contracts=Decimal(str(contracts)),
            extra={
                "equity": str(equity),
                "leverage": str(leverage),
                "multiplier": str(multiplier),
                "size_min": str(size_min),
                "size_max": str(size_max),
                "status": entry.status.value,
                "direction": record.direction.value,
                "planned_stop": str(record.planned_stop),
                "symbol": self.instrument.symbol,
            },
        )
        if entry.status not in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}:
            # 🔴 진입이 안 채워졌으면 익절을 **안 보낸다** — 보내면 반대 포지션이 열린다.
            self._log.warning(
                "live_runner_entry_unfilled",
                payload={
                    "trade_id": record.trade_id,
                    "status": entry.status.value,
                    "note": "익절을 걸지 않는다 — 없는 포지션을 줄이려 하면 반대가 열린다",
                },
            )
            return

        # 🔴 **손절을 익절보다 먼저 건다** (T20 ⑤ · 2026-08-19 실측).
        #
        #    진입 체결부터 손절이 걸리기까지 중앙 **107.7ms** 동안 포지션이 무방비였다.
        #    그 사이에 익절 두 다리(각 35ms)가 끼어 있었기 때문이다.
        #
        # ⚠️ **익절 실패와 손절 실패는 위험도가 다르다.** 익절이 없으면 이익을 놓칠
        #    뿐이고 포지션은 손절이 지킨다 — 그러니 지키는 쪽이 먼저다.
        #
        # ⭐ 걸음 끝의 `_guard_stop` 은 그대로 둔다. 여기서 한 번 더 부르는 것은
        #   **멱등**이다 (거래소에 물어보고 이미 걸려 있으면 갈아 끼운다).
        await self._arm(record, int(entry.filled_quantity))

    async def _clear_reduce(self, record: TradeRecord) -> None:
        """익절을 걸기 전에 **남아 있는 줄이는 주문을 거둔다** (사용자 신고 2026-08-20).

        Args:
            record: 이 매매의 기록 — 로그에 남길 용도다.

        Note:
            🔴 익절이 `REDUCE_ONLY_FAIL` 로 거절됐다. 실측 5건이 전부 같은 모양이다:

            ```
            position size -256  pending order 256  while reduce order 128
            position size  -80  pending order  80  while reduce order  80
            position size   -8  pending order   8  while reduce order   4
            position size -274  pending order 274  while reduce order 137
            ```

            ⚠️ **`pending order` 가 언제나 포지션 전량과 같다.** 익절이 나가기도 전에
            누군가 그 자리를 통째로 잡고 있었다는 뜻이고, 그래서 그 매매는 **익절 없이**
            손절만 들고 돌았다.

            ⛔ **무엇이 잡고 있었는지 추정하지 않는다.** 이전 매매의 익절일 수도,
            조건부의 `auto_size` 일 수도 있다 — 답은 거래소에 있다. 그래서 **묻고 거둔다.**

            ⭐ **거둔 것을 남긴다** (§1-0s). 이 로그가 다음에 *"무엇이 자리를 잡고
            있었나"* 에 답한다 — 고침이 관측을 같이 만든다.

            ⛔ **실패해도 던지지 않는다.** 익절을 못 거는 것보다 나쁘지 않고, 포지션은
            손절이 지킨다 (§1.2.1).
        """
        if not isinstance(self._orders, OrdersAware):
            return
        try:
            rows = await self._orders.open_orders(self.instrument)
        except Exception as exc:
            self._log.warning("live_reduce_unreadable", payload={"error": str(exc)[:140]})
            return
        stale = [row for row in rows if str(row.get("is_reduce_only", "")) == "True"]
        if not stale:
            return
        self._log.warning(
            "live_reduce_slot_taken",
            payload={
                "trade_id": record.trade_id,
                "found": [
                    {"id": str(row.get("id", "")), "size": str(row.get("size", ""))}
                    for row in stale
                ],
                "note": "익절을 걸기 전에 남아 있던 줄이는 주문이다 — 거둔다",
            },
        )
        for row in stale:
            with contextlib.suppress(Exception):
                await self._orders.cancel_order(str(row.get("id", "")))

    async def _arm(self, record: TradeRecord, filled: int) -> None:
        """열린 포지션에 **손절과 익절을 건다** — 어떻게 샀는지와 무관하다.

        Args:
            record: 원장 기록.
            filled: 실제로 열린 계약 수.

        Note:
            🔴 **진입 경로에서 떼어냈다** (2026-08-20 사고 ⓐ). 예전에는 이 일이 시장가
            전송 함수 안에 붙어 있어서, 지정가로 산 매매는 보호를 받으려면 **시장가
            주문을 또 내는 수밖에** 없었다. 실제로 그렇게 돌았다.

            ⇒ 보호는 *"포지션이 있다"* 에만 딸린 일이다. 그 자리를 따로 만든다.
        """
        await self._guard_stop()

        if filled <= 1:
            self._log.warning(
                "live_runner_too_small_to_ladder",
                payload={"trade_id": record.trade_id, "filled": filled},
            )
            return
        # 🔴 **한 다리가 실패해도 나머지는 건다** (사용자 요구 2026-08-18).
        #    예전에는 첫 다리에서 예외가 나면 둘째 다리를 시도조차 안 했다 — 실제로
        #    밤새 그랬다(호가 자릿수 거절). 그리고 **다시 걸지도 않았다.**
        #
        # ⚠️ 익절 실패는 손절 실패와 **위험도가 다르다.** 익절이 없으면 이익을 놓칠
        #    뿐이고 포지션은 손절이 지킨다. 그래서 여기서는 던지지 않고 **다시 건다.**
        #
        # ⭐ **두 다리를 동시에 보낸다** (T20 ⑤). 서로 독립인데 순차라 왕복이 두 번
        #   쌓였다 — 거래소 왕복 중앙 35.2ms 이므로 그만큼 줄어든다.
        #
        # ⚠️ `return_exceptions=True` 라야 한 다리가 터져도 나머지 결과를 받는다.
        #   그것이 위 "한 다리가 실패해도 나머지는 건다" 를 지키는 방법이다.
        missing: list[int] = []
        await self._clear_reduce(record)
        legs = list(take_profit_orders(record, self.instrument, filled, run=self._run_key))
        done = await asyncio.gather(
            *(self._orders.submit_order(order) for order in legs), return_exceptions=True
        )
        for leg, (order, got) in enumerate(zip(legs, done, strict=True)):
            try:
                if isinstance(got, BaseException):
                    raise got
                sent = got
                self.orders += 1
            except Exception as exc:
                missing.append(leg)
                await self._note_order(
                    record.trade_id,
                    role=f"익절{leg + 1}",
                    status="rejected",
                    price=order.price,
                    error=str(exc)[:180],
                )
                self.failures += 1
                self.last_error = f"익절 {leg + 1}단 실패: {exc}"[:200]
                self._log.error(
                    "live_runner_take_profit_failed",
                    payload={
                        "trade_id": record.trade_id,
                        "leg": leg,
                        "error": str(exc)[:200],
                        "note": "다음 걸음에 다시 건다 — 포지션은 손절이 지킨다",
                    },
                )
            else:
                await self._note_order(
                    record.trade_id,
                    role=f"익절{leg + 1}",
                    status=sent.status.value,
                    order_id=str(sent.broker_order_id or ""),
                    price=order.price,
                )
        if missing:
            # 🔴 다음 걸음이 다시 시도하도록 표시한다. 안 남기면 **영영 안 걸린다** —
            #    `_sent` 가 이미 이 매매를 보냈다고 기억하기 때문이다.
            self._ladder_pending[record.trade_id] = filled
        else:
            self._ladder_pending.pop(record.trade_id, None)

    async def _retry_ladders(self) -> None:
        """못 건 익절을 **다시 건다** — 매 걸음.

        Note:
            🔴 익절이 거절되면 포지션은 열려 있는데 이익 실현 주문이 없다. 손절은 있으니
            무방비는 아니지만, **목표에 닿아도 아무 일이 안 일어난다.**

            ⚠️ 이미 걸린 다리를 또 걸지 않는다 — 거래소에 물어 확인하고, 없는 것만 건다.
            멱등키가 같으므로 중복은 거래소도 막지만, 그 실패는 로그를 더럽힌다.
        """
        if not self._ladder_pending:
            return
        for trade_id, filled in list(self._ladder_pending.items()):
            record = next(
                (item for item in self._session.ledger.records if item.trade_id == trade_id),
                None,
            )
            if record is None or record.outcome is not Outcome.OPEN:
                # 이미 끝난 매매다 — 걸 이유가 없다.
                self._ladder_pending.pop(trade_id, None)
                continue
            done = True
            for leg, order in enumerate(
                take_profit_orders(record, self.instrument, filled, run=self._run_key)
            ):
                try:
                    await self._orders.submit_order(order)
                    self.orders += 1
                    self._log.info(
                        "live_runner_take_profit_recovered",
                        payload={"trade_id": trade_id, "leg": leg},
                    )
                except Exception as exc:
                    done = False
                    self._log.warning(
                        "live_runner_take_profit_retry_failed",
                        payload={"trade_id": trade_id, "leg": leg, "error": str(exc)[:160]},
                    )
            if done:
                self._ladder_pending.pop(trade_id, None)


async def build_live_feed(
    quotes: QuoteAdapter,
    instrument: Instrument,
    frames: Sequence[Timeframe],
    entry: Timeframe,
    *,
    bars: int = SEED_BARS,
) -> LiveFeed:
    """REST 로 시드를 받아 라이브 급전을 만든다.

    Args:
        quotes: 공개 조회 어댑터.
        instrument: 대상 종목.
        frames: 필요한 시간축들 (멀티 TF 판정용).
        entry: 진입 시간축.
        bars: 시간축마다 받을 봉 수.

    Returns:
        시드가 채워진 급전.

    Note:
        🔴 **시간축마다 자기 길이로 받는다.** 같은 시각 범위로 받으면 1d 는 몇 봉밖에
        안 들어오고, 그 상태의 상위 TF 추세는 판정이 안 된다.

        ⚠️ 마지막 봉은 **진행 중일 수 있다.** REST 도 미마감 봉을 준다 — 시드에서 빼면
        웹소켓이 그 봉을 다시 줄 때 채워지고, 안 빼면 미마감 값이 확정으로 남는다.
        그래서 **마지막 봉을 버린다.**
    """
    now = datetime.now(UTC)
    seed: dict[Timeframe, Sequence[object]] = {}
    for frame in frames:
        span = timedelta(seconds=interval_seconds(frame))
        rows = await quotes.get_candles(instrument, frame, now - span * bars, now)
        # ⛔ 마지막 봉을 버린다 — 진행 중일 수 있고, 미마감 값이 확정으로 남으면 안 된다.
        seed[frame] = rows[:-1] if rows else rows
    return LiveFeed(cast("dict[Timeframe, Sequence[object]]", seed), entry)  # type: ignore[arg-type]


async def seed_within_budget(
    quotes: QuoteAdapter,
    instrument: Instrument,
    frames: Sequence[Timeframe],
    entry: Timeframe,
    *,
    cap: int,
    counter: object | None = None,
    bars: int = SEED_BARS,
) -> tuple[LiveFeed, int | None]:
    """시드를 받되 **브로커 요청 수를 세고 상한을 건다** (T253).

    Args:
        quotes: 조회 어댑터 (DB 캐시가 감싼 것이어도 된다).
        instrument: 대상 종목.
        frames: 필요한 시간축들.
        entry: 진입 시간축.
        cap: 판 시작 한 번의 요청 상한. 0 이면 무제한.
        counter: 요청을 세는 쪽 — 캐시가 감싸고 있으면 그 **안쪽** 어댑터. None 이면 `quotes`.
        bars: 시간축마다 받을 봉 수.

    Returns:
        (급전, 쓴 요청 수). 세지 못하는 어댑터(웹소켓 거래소)면 요청 수는 None.

    Raises:
        RequestBudgetExceededError: 상한을 넘었다 — 잡는 쪽이 사람에게 말한다(규칙 #8).

    Note:
        실측(2026-09-09): 선언 4h 주식 판 하나가 2분에 454요청 → 데모 API 재시작. 그 뒤로
        `custom` 은 차트 축(1h · 58요청)을 쓰지만 셋업 있는 매매법은 선언 축 그대로라,
        이 눈금과 상한이 없으면 같은 사고가 다시 난다. 요청 수는 로그 한 줄
        (`run_start_requests`)로 남는다 — T253 DoD.
    """
    meter = counter if counter is not None else quotes
    counting = meter if isinstance(meter, RequestCounting) else None
    before = counting.requests if counting is not None else 0
    guard = counting.budget(cap) if counting is not None and cap > 0 else contextlib.nullcontext()
    with guard:
        feed = await build_live_feed(quotes, instrument, frames, entry, bars=bars)
    used = counting.requests - before if counting is not None else None
    _logger.info(
        "run_start_requests",
        payload={
            "symbol": instrument.symbol,
            "frames": [f.value for f in frames],
            "requests": used,
            "cap": cap,
        },
    )
    return feed, used


def attach(session: Session, feed: LiveFeed) -> None:
    """세션에 라이브 급전을 붙인다.

    Args:
        session: 세션.
        feed: 라이브 급전.

    Note:
        ✅ **cast 가 없어졌다** (2026-08-17). `LiveFeed` 가 `seal` 을 갖게 되어
        `Session.feed` 주석을 `Feed` 프로토콜로 넓힐 수 있었다 (`feed_protocol.py`).

        ⚠️ 함수를 남겨 두는 이유는 **같은 객체를 쓰라는 것**을 한 곳에서 강제하기
        위해서다. 러너가 밀어 넣은 봉을 세션이 못 보면 예외 없이 아무 일도 안 일어난다 —
        `LiveRunner` 생성자가 그것을 확인한다.
    """
    session.feed = feed


async def wait_cancelled(task: asyncio.Task[None]) -> None:
    """러너를 정리한다.

    Args:
        task: `run()` 을 도는 태스크.

    Note:
        ⚠️ 라이브는 스스로 끝나지 않으므로 **취소가 정상 종료 경로**다. `CancelledError`
        를 오류로 적으면 매번 종료가 실패로 보인다.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        _logger.info("live_runner_stopped", payload={"reason": "cancelled"})
