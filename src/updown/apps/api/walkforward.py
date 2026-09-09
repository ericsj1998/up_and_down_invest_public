"""모의 라이브 API (T13) — 세션을 만들고 한 봉씩 민다.

## 🔴 화면 페이로드는 점검기와 **같은 모양**이다

`inspection.Snapshot.to_dict()` 를 그대로 쓴다. 프론트가 `InspectorChart` 를 재사용해야
하고(T13 ⑥), 두 벌로 만들면 한쪽만 고쳐져 *"점검기에서는 맞는데 모의 라이브에서는
틀리다"* 를 쫓게 된다.

⚠️ 실제로 그 사고가 있었다 — 캔들 키를 `o/h/l/c` 로 냈는데 화면은 `open/high/low/close`
를 읽어서 전부 `NaN` 이 되고 **가운데 일직선**이 그려졌다. 타입스크립트는 통과했다.

## 세션은 서버에 산다

브라우저에 두면 새로고침에 날아가고, 봉인 커서를 클라이언트가 들고 있게 되어 **미래
차단이 클라이언트 신뢰에 의존**한다. 그것은 봉인이 아니다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import time
from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.analysis.detectors.registry import SetupRegistry
from updown.analysis.detectors.rules import RuleConfig, load_rules
from updown.analysis.indicators.adx import adx
from updown.analysis.indicators.ma import sma
from updown.analysis.indicators.volatility import realized_vol
from updown.analysis.playbook.select import (
    active_playbooks,
    default_playbook,
    load_playbooks,
)
from updown.analysis.playbook.types import Playbook
from updown.analysis.structures.box_range import SPAN_COVER
from updown.apps.api.admin import instrument_of, rules_config
from updown.apps.api.analysis import as_json
from updown.apps.api.auth import require_market_trade, require_playbook_trade
from updown.apps.api.stock_order import StockOrderRejectedError, hours_of, stock_order_terms
from updown.common.costs import (
    DEFAULT_CONFIG_PATH,
    TICK_RATIO,
    TickUnknownError,
    load_cost_table,
    resolve_tick,
)
from updown.common.domain.candle import Candle
from updown.common.domain.capabilities import capabilities_of
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.domain.session import load_calendar
from updown.common.logging.setup import get_logger
from updown.common.wire import candle_json
from updown.decision.risk.manual import confirm
from updown.decision.risk.policy import RiskConfigError, require_stop_cap
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.execution.gateway import (
    OrderGatewayError,
    order_adapter,
)
from updown.marketdata.adapter import Capability, QuoteAdapter
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.timeframes import interval, interval_seconds
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.inspection import setups as setup_layers
from updown.orchestration.inspection.catalog import available_flags, expand
from updown.orchestration.inspection.overlays import (
    adx_overlay,
    ma_layer,
)
from updown.orchestration.inspection.snapshot import FrameView, Layer, build_frame, project
from updown.orchestration.inspection.snapshot import Snapshot as ChartSnapshot
from updown.orchestration.leftovers import sweep as sweep_leftovers
from updown.orchestration.leftovers import sweep_zombie_entries
from updown.orchestration.playbook_run import major_trend
from updown.orchestration.reconcile import (
    NAKED,
    Finding,
    Snapshot,
    blocked_keys,
    compare,
    covered,
    partial_fills,
)
from updown.orchestration.walkforward import (
    Funding,
    Ledger,
    Outcome,
    Seal,
    SealedFeed,
    Session,
    TradeRecord,
    evidence_rows,
)
from updown.orchestration.walkforward import pending as pending_mod
from updown.orchestration.walkforward.ledger import Direction
from updown.orchestration.walkforward.live_filler import LiveFiller
from updown.orchestration.walkforward.live_runner import (
    SEED_BARS,
    LiveRunner,
    MarginAware,
    attach,
    build_live_feed,
    run_of_text,
)
from updown.orchestration.walkforward.order_mapping import run_tag
from updown.orchestration.walkforward.sealed import SealBreachError
from updown.orchestration.walkforward.sealed_filler import SealedFiller
from updown.orchestration.walkforward.session import STEP_FRAME
from updown.orchestration.walkforward.store import (
    REFILL_CAP_KEY,
    RunStore,
    RunStoreError,
    SettingsStore,
    anchor_of,
)
from updown.orchestration.walkforward.stored_candles import StoredCandles, needed_frames

_logger = get_logger("api.walkforward")

router = APIRouter(prefix="/walkforward", tags=["walkforward"])

LEVERAGE_MIN = Decimal(1)
LEVERAGE_MAX = Decimal(125)
"""바꿀 수 있는 배율 범위.

⚠️ 상한은 **testnet 기준**이다 (라이브는 200). 하드코딩하지 않고 계약 명세에서 읽는
것이 옳지만, 그 값은 종목마다 달라서 여기서는 **거래소가 거부하게** 두고 범위 검사는
말도 안 되는 값(0·음수·1000)만 막는다.

⛔ 배율을 올리면 청산가가 진입에 가까워진다 — 리스크를 늘리는 방향이라 로그에 남긴다.
"""

FRAMES: tuple[Timeframe, ...] = (
    # ⭐ **하위 축은 보기 전용이다** (사용자 요구 2026-08-18: *"15분봉에서 잡힌 전략 기준으로
    #    동작하는 걸 10초봉에서 보던가 할 수는 있을 거 아냐"*). 진입 축은 플레이북이 정하고
    #    (플레이북마다 다르다) 이 목록은 **볼 수 있는 축**이다.
    #
    # ⚠️ 축이 늘면 매 걸음 계산량도 는다 — 판정은 진입 축에서만 나지만 화면용 프레임은
    #    전부 만든다. 느려지면 여기서 줄인다.
    Timeframe.S10,
    Timeframe.S30,
    Timeframe.M1,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.M30,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
)
"""T13 ⑨ — 다루는 봉은 5m·15m·1h·4h·1d 다."""

_registry_cache: SetupRegistry | None = None


def _shared_registry() -> SetupRegistry:
    """탐지기 레지스트리 — 프로세스에 하나. 룰 YAML 은 기동 뒤 바뀌지 않는다.

    Returns:
        entry point 로 발견한 탐지기 + `config/rules` 설정을 묶은 레지스트리.

    Note:
        Session 에 넘기지 않으면 `propose()` 가 **걸음마다** 새로 만든다 (2026-09-08 실측: 4h 봉
        9,300개 봉인 백테스트가 그것만으로 10분을 넘겼다). 라이브(4h 에 한 걸음)도 같은 것을 쓴다.
    """
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = SetupRegistry.from_plugins(load_rules())
    return _registry_cache


WARMUP_BARS = 600
"""봉인 시작 **이전**에 확보할 봉 수.

🔴 추세·거래량 기준선은 창을 넘어선다. 워밍업 없이 시작하면 판정이 `None` 이라
플레이북이 영원히 0건이고, 그것을 "자리가 없었다"로 읽게 된다 — 점검기에서 실제로
겪었고 두 번 겪었다.
"""

ADX_GATE_FLAG = "overlay.adx_gates"
VOL_CLAMP = (Decimal("0.4"), Decimal("2.0"))
"""변동성 승수의 (하한, 상한) — 탐지기 기본값과 **같아야 한다** (탐지기의 `_size_mult`).

⚠️ 여기만 바꾸면 화면이 판정과 다른 배수를 적는다. 룰 설정으로 올릴 값이지만
(§4.3.1) 지금 탐지기도 상수라, 두 곳이 같다는 사실을 이 주석이 짊어진다.
"""
STANCE_FLAG = "overlay.stance"
"""**지금 어느 전략이 서 있는가** — 문 하나하나가 아니라 결론 (사용자 지적 2026-08-30).

🔴 문턱 칩만으로는 사람이 결론을 잘못 읽는다. 실측: XRP 에서 *"숏 진입 ≥20 · 충족"* 을
보고 숏 자리라고 읽었는데, 숏은 **셋이 다 맞아야** 열린다:

    ADX ≥ 20          22.4   ✅
    가격 < SMA200     1.39 vs 1.1493  ❌ 위에 있다
    SMA 기울기 < 0    +0.73%          ❌ 올라가는 중

⇒ 문 하나가 열린 것과 **전략이 선 것**은 다르다. 그 구별을 화면이 대신 해 준다.

⛔ 여기서 판정을 새로 만들지 않는다. 탐지기가 낸 셋업이 있으면 그것이 결론이고,
없으면 **왜 없는지**만 적는다 — 화면이 자기 규칙을 가지면 판정과 갈린다.
"""

SLOPE_FLAG = "overlay.ma_slope"
VOL_FLAG = "overlay.vol_target"
"""진입 순간에 정해지는 **한 숫자**짜리 근거 둘 (사용자 확정 2026-08-30).

⚠️ **판(pane)을 만들지 않는다.** ADX 는 문턱을 넘나드는 것을 시간축으로 봐야 뜻이
있지만, 이 둘은 *"들어가는 그 순간의 값"* 이라 선으로 그려도 볼 일이 없다. 그리고
차트 상자는 460px 로 고정이라 판이 늘면 **캔들이 쓸 높이가 그만큼 줄어든다** —
판 넷이면 가격이 100px 남고, 원래 보려던 것이 제일 안 보이게 된다.

⇒ 차트 아래 **칩 한 줄**로 낸다. 높이를 안 먹는다.
"""
"""추세강도(ADX)와 그 문턱들 — **판단의 절반이 여기 있는데 화면에 없었다**.

사용자 지적 2026-08-30: *"사용자가 차트를 통해 현재 매매법이 어떤 전략으로 대기 중이고,
어떤 전략으로 어떻게 진입했는지 알 수 있어야 한다."*

근거 원문은 ADX 를 인용한다 — *"ADX 34.9 <= 35 (본대 부재)"* — 그런데 프론트엔드
전체에 `adx` 라는 글자가 한 번도 없었다. 즉 **왜 캐리이고 왜 본대가 아닌지**를 화면에서
확인할 방법이 없었다.

⚠️ 두 레이어로 나눈 이유: 값(봉별 점)과 문턱(가로선)은 **갱신 주기가 다르다.** 값은
봉마다 바뀌고 문턱은 플레이북이 바뀔 때만 바뀐다. 한 레이어에 섞으면 화면이 매번
문턱을 다시 그린다.
"""
"""트레일 청산선(SMA) 오버레이 레이어 id — full_ride 전략의 실제 청산 경로.

`shapes` 는 봉별 `{ts, price}` 점이다. 화면이 이걸 이어 선으로 그린다 — 세션이 손절을
상향 트레일하는 그 SMA 라, 선의 미래 경로가 곧 청산가의 경로다 (사용자 요구 2026-08-24).
"""

ORDER_FLAG = "walk.order"
"""지금 걸려 있는 주문을 그리는 레이어 id.

⭐ 플래그 목록에 없는 **화면 전용** 레이어다. 사람이 켜고 끄는 것이 아니라 세션 상태를
그대로 비춘다.
"""

JOURNAL_ROOT = Path("logs/walkforward")

_KEY_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _safe_key(raw: str) -> str:
    """세션 열쇠를 파일 이름으로 써도 안전한 모양으로 강제한다 (보안 리뷰 2026-09-03).

    Raises:
        HTTPException: 영숫자·`_`·`-` 64자를 벗어나면 422 — `../funds/x` 나 절대경로가
            `journal_path` 에 들어가면 저널 기록기가 **다른 도메인의 .json**(펀드 상태 등)
            을 덮어쓴다 (pathlib 는 절대경로 조각에 앵커를 통째로 내준다).
    """
    if not _KEY_SHAPE.fullmatch(raw):
        raise HTTPException(422, f"세션 id 는 영숫자·_·- 64자다 — 받은 값: {raw!r}")
    return raw


"""걸어간 기록이 남는 곳 (T13 산출물).

⚠️ 세션당 파일 하나이고 매 변경마다 통째로 다시 쓴다 — 대기에서 체결·청산으로 **갱신**
되므로 이어쓰기로는 표현이 안 된다.
"""

VIEW_BARS = 200
"""화면에 그릴 봉 수."""

CHART_TTL = 1.0
"""작도를 다시 하기까지의 최소 간격(초).

🔴 **봉 수만으로는 재생 중에 캐시가 아무 일도 안 했다** (사용자 신고 2026-08-17:
*"진행하다 점점 느려지더니 결국 거의 동작을 안할 정도가 돼"*).

재생 중에는 봉이 **매 걸음** 늘어나 열쇠가 매번 달라진다. 그래서 폴링 하나하나가
3~5초짜리 전체 작도를 했고, 러너가 CPU 를 계속 쓰는 와중이라 응답이 더 늘어졌다.

⚠️ **대가: 차트가 최대 이만큼 낡는다.** 커서·대시보드·매매 로그는 매번 최신이므로
어긋나는 것은 캔들과 도형뿐이다. 눈으로 따라가는 도구에서 1초 지연은 멈춤보다 낫다.

⛔ **근본 원인은 이것이 아니다** — 레벨 원장을 매 걸음 **전체 이력으로** 다시 쌓는
   것이 원인이고(진행할수록 비용이 커지는 이유), 증분 원장이 진짜 해결이다.
   이 값은 그때까지의 완충이다.
"""

SEALED_RANGE = (datetime(2026, 6, 16, tzinfo=UTC), datetime(2026, 8, 16, tzinfo=UTC))
"""⛔ **T01 판정용 out-of-sample 구간.** 여기서 걸어가면 T12 판정이 무의미해진다.

사람이 실수로 고를 수 있으므로 코드가 거부한다 (T13 ④).

## 🔴 6.5개월 → **2개월**로 줄였다 (사용자 확정 2026-08-17)

> *"봉인 기간을 2달로 축소하고 싶어. 이제 슬슬 이번 년도 데이터로도 돌려봐야 할 것
> 같네. 요 근래가 하락장이었거든. 숏 포지션을 테스트할 수 있는 좋은 기회야."*

⭐ **왜 이 봉인이 필요한가** — 세션 봉인(한 판 안에서 미래를 못 봄)과 다른 것을 막는다.
정직하게 걸어가도 *돌려보고 → 고치고 → 다시 돌리면* 그 구간에 맞춰진다. 실측이 그것을
그대로 보여 줬다 (같은 코드, 같은 날):

```
맞춰 온 3구간    -1.19 ~ -1.31%
손대지 않은 3구간 -3.79 ~ -7.08%     3~6배 나쁘다
```

⚠️ **줄인 대가**: 최종 판정 표본이 6.5개월에서 2개월로 준다. §12.9 표본 30건을
채우는지는 그때 확인해야 하고, 못 채우면 판정을 미루는 것이지 문턱을 낮추는 것이
아니다 (§5.6.7).

⛔ 다시 늘리지 않는다. 한 번 열어 본 구간은 봉인으로 되돌릴 수 없다 — 이미 봤기
때문이다. 늘려도 그 구간은 out-of-sample 이 아니다.
"""

RANDOM_RANGE = (datetime(2022, 3, 1, tzinfo=UTC), datetime(2025, 12, 1, tzinfo=UTC))
"""랜덤 시작 시점을 뽑을 구간 — 적재 범위 안쪽으로 여유를 둔다."""

DEFAULT_SPEED = 16
"""새 판의 기본 배속 (사용자 확정 2026-08-17: *"앞으로 모든 테스트는 디폴트 진행
배율은 16배야"*).

🔴 **배속은 봉을 건너뛰지 않는다** — `step()` 을 얼마나 자주 부르나일 뿐이다
(절대 규칙 #5). 그래서 1배와 16배의 원장은 **같아야 한다**. 다르면 그 자체가 결함 신호다.

⚠️ **서버가 기본값을 든다.** 화면에만 두면 스크립트로 띄운 판(`/start` 직접 호출)이
1배로 돌아, 같은 측정이 도구에 따라 몇 배씩 느려진다.
"""


@dataclass(slots=True)
class Live:
    """서버가 들고 있는 세션 하나.

    Attributes:
        session: 엔진.
        flags: 화면에 그릴 플래그들.
        applied: **매매에 적용**하는 근거들. 나머지는 조회 전용이다 (T13 ②).
        speed: 배속. 엔진은 안 보고 화면만 본다.
        seed: 시작 시점을 뽑은 시드. 재현에 필요하다.
    """

    session: Session
    flags: tuple[str, ...]
    applied: tuple[str, ...]
    speed: int = DEFAULT_SPEED
    seed: int | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    runner: asyncio.Task[None] | None = None
    waiting: bool = False
    """동시 실행 티켓을 기다리는 중인가 — 멈춘 것과 다르다."""
    chart: dict[Timeframe, tuple[int, float, list[dict[str, Any]]]] = field(
        default_factory=dict[Timeframe, "tuple[int, float, list[dict[str, Any]]]"]
    )
    """작도 캐시 — `시간축 -> (봉 수, 그린 시각, 그림)`.

    🔴 봉은 **덧붙기만** 하므로 봉 수가 완전한 열쇠다. 같은 봉 수에서 다시 그리면
    **반드시 같은 그림**이 나오므로 재사용이 결과를 바꾸지 않는다 (절대 규칙 #5).

    ⚠️ 되감기(`at`)는 안 담는다 — 같은 봉 수라도 다른 시점이면 그림이 다르다.
    """
    _full: dict[Timeframe, list[Candle]] = field(default_factory=dict[Timeframe, list[Candle]])

    @property
    def running(self) -> bool:
        """백그라운드로 걸어가는 중인가."""
        return self.runner is not None and not self.runner.done()


SESSIONS: dict[str, Live] = {}
"""메모리 세션 등록부.

⚠️ 프로세스가 죽으면 날아간다. T13 은 **한 판을 앉은 자리에서 끝까지** 걸어가는
도구이므로 지금은 이것으로 충분하고, 영속화가 필요해지면 그때 DB 로 내린다.
"""


RUNNER_TICK = 0.05
"""백그라운드 러너가 한 묶음 민 뒤 쉬는 시간(초).

⭐ **배속은 한 번에 미는 봉 수로 낸다** — 쉬는 시간을 줄이는 방식으로 내면 배속이
높을 때 이벤트 루프를 굶겨 다른 세션이 멈춘다. 여러 판을 동시에 돌리는 것이 목적이므로
한 판이 루프를 독점하면 안 된다.
"""

MAX_RUNNING = 6
"""동시에 **봉을 미는** 세션 수 상한 (사용자 요구 2026-08-17).

🔴 **"6코어"를 이 구조로 옮기면 이 값이다.** 러너는 전부 **한 이벤트 루프**에서 돌므로
실제로 쓰는 코어는 하나다 — 세션을 늘려도 CPU 가 여섯 배로 늘지 않고, 각자 느려질
뿐이다. 그래서 막아야 하는 것은 코어가 아니라 **동시에 계산이 도는 판 수**다.

⚠️ 상한을 넘은 판은 **자기 차례를 기다린다**(멈춘 것이 아니다). 목록에 `대기` 로 뜬다.

⛔ 진짜로 여러 코어를 쓰려면 프로세스를 나눠야 하고, 그러면 세션 등록부·저널이
프로세스 사이에서 갈린다. 지금 구조에서 할 일이 아니다.
"""

RUNNING = asyncio.Semaphore(MAX_RUNNING)
"""동시 실행 티켓. ⚠️ 이벤트 루프가 만들어진 뒤에 쓰인다 (모듈 임포트 시점 아님)."""


LIVE_RUNNERS: dict[str, LiveRunner] = {}

_store: RunStore | None = None

_settings: SettingsStore | None = None
_candles: CandleRepository | None = None
"""봉 캐시의 저장소 (T240) — 폴링 브로커(토스)의 급전 시드가 여기서 먼저 읽힌다."""
"""앱 전체가 공유하는 설정 (T21 ⑦) — 재충전 상한이 여기 산다."""
"""판 저장소 (T16 ②). `main.py` 기동 훅이 넣는다.

🔴 **라이브는 이것 없이 뜨지 않는다.** 저장이 안 되는데 판이 돌면 지금 고치는 그 버그가
그대로 재현되고, 화면에는 아무 표시가 없다 (절대 규칙 #8):

```
거래소     포지션·손절·익절이 그대로 남는다
원장       통째로 사라진다 → 반익·본절 상향·손절 재장착이 전부 멈춘다
```

⚠️ **백테스트는 요구하지 않는다.** 결정론 복원이 있어서(같은 봉인·같은 설정 → 같은
원장) 파일 저널만으로 되살아난다 — 라이브는 봉인 구간이 없어 그 경로가 없다.
"""


def ledger_store() -> RunStore | None:
    """저장소를 **읽기 전용으로** 빌려준다 (2026-08-20).

    Returns:
        붙어 있으면 저장소, 아니면 None.

    Note:
        ⭐ 거래소 콘솔이 체결 이력에 **계획·배율·종목**을 붙이려면 원장을 봐야 한다.
        모듈 전역을 직접 들여오면 붙기 전 시점의 `None` 을 잡으므로, 부를 때마다 지금
        값을 준다.

        ⚠️ **콘솔은 이것으로 쓰지 않는다.** 화면이 원장을 고치기 시작하면 *"사실"* 과
        *"모형"* 의 경계가 무너진다 — 그 경계가 이 파일의 존재 이유다.
    """
    return _store


def attach_store(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """저장소를 붙인다 — API 기동 훅이 부른다.

    Args:
        factory: 세션 팩토리. None 이면 뗀다 (테스트가 쓴다).

    Note:
        ⚠️ **엔진을 여기서 만들지 않는다.** 만들면 API 가 이미 든 커넥션 풀과 별도 풀이
        생기고, 종료할 때 아무도 안 닫는다.
    """
    global _store, _settings, _candles
    _store = None if factory is None else RunStore(factory)
    _candles = None if factory is None else CandleRepository(factory)
    # ⭐ 금고 한도는 판 저장소와 **같은 팩토리**를 쓴다 — 풀을 하나로 유지한다.
    _settings = None if factory is None else SettingsStore(factory)


"""라이브 세션의 러너들 — 건강 상태를 보여주려고 따로 든다.

⚠️ `SESSIONS` 에 있는 것은 세션(판단·원장)이고 러너는 **봉을 먹이는 쪽**이다. 러너만
아는 값(걸음·구멍·재연결)이 있어서 함께 들어야 화면이 "왜 아무 일이 없나" 에 답할 수 있다.
"""


async def _walk(key: str) -> None:
    """세션 하나를 봉인 끝까지 걸어간다 (백그라운드).

    Args:
        key: 세션 id.

    Note:
        🔴 **`step()` 자체는 그대로다** — 배속은 *얼마나 자주 부르나*일 뿐 봉을
        건너뛰지 않는다 (절대 규칙 #5). 여기서도 `speed` 만큼 **연속 호출**한다.

        ⚠️ `await` 를 반드시 한 번은 지난다. 안 그러면 이 코루틴이 이벤트 루프를 잡고
        API 응답과 다른 세션이 통째로 멈춘다.

        ⛔ 예외를 삼키지 않는다 (절대 규칙 #8). 터지면 러너가 끝나고 `running` 이
        False 가 되어 목록에 드러난다.
    """
    live = SESSIONS.get(key)
    if live is None:
        return
    # ⭐ **티켓을 하나 잡고 돈다.** 없으면 기다린다 — 판이 많아도 한 번에 여섯 개만
    #    계산이 돌아 컴퓨터가 버틴다 (`MAX_RUNNING`).
    live.waiting = True
    async with RUNNING:
        live.waiting = False
        while live is not None and not live.session.finished:
            if not live.session.paused:
                for _ in range(max(1, live.speed)):
                    if live.session.step() is None:
                        break
                    # 🔴 **걸음마다 루프를 놓아 준다** (사용자 지적 2026-08-17:
                    #    *"80퍼 언저리부터 아예 진행이 멈춰버려"*).
                    #
                    #    걸음 비용이 진행에 따라 커진다 — 실측 0% 100ms → 80% 282ms
                    #    (최대 1.4초). 여기에 배속을 곱하면 32배에서 **한 번에 9초**
                    #    동안 이벤트 루프를 붙잡고, 그동안 API 응답이 전부 막혀
                    #    화면이 죽은 것처럼 보인다. 판이 여럿이면 더하다.
                    #
                    #    ⚠️ `sleep(0)` 은 **쉬는 것이 아니라 양보**다. 걸음 수는 그대로라
                    #      결과가 안 바뀐다 (절대 규칙 #5).
                    await asyncio.sleep(0)
            await asyncio.sleep(RUNNER_TICK)
            live = SESSIONS.get(key)


def _playbook(name: str) -> Playbook:
    """선언에서 플레이북을 찾는다.

    Args:
        name: 플레이북 id.

    Returns:
        플레이북.

    Raises:
        HTTPException: 없는 이름이면 404 — 조용히 첫 번째를 고르지 않는다.
    """
    found = next((item for item in load_playbooks() if item.playbook_id == name), None)
    if found is None:
        known = ", ".join(item.playbook_id for item in load_playbooks())
        raise HTTPException(404, f"{name} 플레이북이 없다 — 있는 것: {known}")
    return found


def _load_synth(
    root: Path, symbol: str, market: Market, start: datetime, end: datetime
) -> dict[Timeframe, list[Candle]]:
    """합성 캔들 파일을 읽어 요청 구간으로 자른다 (T202 · `_load` 의 테스트 문).

    파일: `{root}/{symbol}.json` = `{"5m": [[ts,o,h,l,c,v], ...], "4h": ..., ...}`.
    파일에 있는 시간축만 담는다 — `SealedFeed` 는 담긴 축만 검증하므로 하위 보기
    전용 축(10s 등)이 없어도 판(5m 스텝 · 4h 판정)은 정직하게 돈다.

    Raises:
        HTTPException: 파일이 없거나 형식이 깨졌을 때 (조용한 실패 금지 · 규칙 #8).
    """
    path = root / f"{symbol}.json"
    if not path.exists():
        raise HTTPException(400, f"합성 캔들 파일이 없다: {path}")
    inst = Instrument(
        market=market,
        symbol=symbol,
        name=f"합성 {symbol}",
        asset_type=AssetType.COIN,
        currency=Currency.USD,  # Currency 에 USDT 가 없다 — 표기 전용이라 USD 로 적는다
    )
    # 🔴 **파일 전체를 싣는다 — 봉인 시작에서 자르지 않는다** (T202 1차 시도의 발견).
    #    처음엔 `lo <= ts` 로 워밍업을 통째로 잘랐고, MA200·추세 게이트가 영영 안 서서
    #    60일 워크가 탐지 4,272건에 **진입 0건**이 됐다 — 전략의 판단이 아니라
    #    데이터 결핍이다 (T13 ④ 와 같은 함정). 대조 상대(walk_synth_compare)도 파일
    #    전체를 소스로 주므로, 여기서 600봉으로 재단하면 지표 수렴이 미세하게 갈려
    #    "같아야 할 원장" 대조에 가짜 차이가 낀다. 워밍업 길이는 파일을 만드는 쪽
    #    (make_walk_synth · 620일)이 정하고, 미래 봉은 `SealedFeed` 커서가 막는다.
    hi = end.timestamp()
    out: dict[Timeframe, list[Candle]] = {}
    raw: dict[str, list[list[float]]] = json.loads(path.read_text(encoding="utf-8"))
    for tf_value, rows in raw.items():
        frame = Timeframe(tf_value)
        out[frame] = [
            Candle(
                instrument=inst,
                timeframe=frame,
                ts=datetime.fromtimestamp(r[0], UTC),
                open=Decimal(str(r[1])),
                high=Decimal(str(r[2])),
                low=Decimal(str(r[3])),
                close=Decimal(str(r[4])),
                volume=Decimal(str(r[5])),
            )
            for r in rows
            if r[0] <= hi
        ]
        # ⛔ 워밍업이 없으면 지금 터진다 (규칙 #8) — 조용히 돌면 위의 "매매 0" 사고를
        #    다시 겪는다. 첫 봉이 봉인 시작보다 뒤면 파일이 워밍업을 안 담은 것이다.
        if out[frame] and out[frame][0].ts >= start:
            raise HTTPException(
                400,
                f"합성 파일에 {frame.value} 워밍업이 없다 — 첫 봉 "
                f"{out[frame][0].ts.isoformat()} 이 봉인 시작 {start.isoformat()} 이후다. "
                f"make_walk_synth 로 워밍업 포함 파일을 다시 만든다",
            )
    return out


def _check_seal(start: datetime, end: datetime) -> None:
    """T01 봉인 구간을 침범하는지 본다.

    Args:
        start: 시작.
        end: 끝.

    Raises:
        HTTPException: 겹치면 400.

    Note:
        🔴 **반복해서 걸어가면 out-of-sample 이 아니게 된다.** T12 판정은 그 구간을
        **한 번만** 여는 것이 전제다.
    """
    low, high = SEALED_RANGE
    if start < high and low < end:
        raise HTTPException(
            400,
            f"T01 판정용 봉인 구간({low:%Y-%m-%d}~{high:%Y-%m-%d})과 겹친다 — "
            f"여기서 걸어가면 T12 판정이 무의미해진다. 다른 시점을 고른다",
        )


class _SynthCapped:
    """합성 워크 전용 **판정 창 상한** — 라이브 시드(`SEED_BARS` 800)와 같은 폭만 보인다.

    Note:
        🔴 T202 2차 시도의 발견: 워밍업을 실었더니 세션이 **전체 이력**으로 판정해
        걸음이 2.4초(대조 스크립트의 58배)가 됐고, 무엇보다 **라이브(800봉 시드)·
        백테스트(`gate_backtest.CappedFeed` 800봉)와 판정 입력 자체가 달랐다** —
        입력이 다르면 원장이 같을 이유가 없어 대조가 성립하지 않는다.

        ⛔ `judged` 만 자른다 — `observed`(화면·감사)는 위임으로 그대로 나간다.
        `gate_backtest.CappedFeed` 와 같은 규칙이며, 합성(SYN) 경로에만 씌운다.
        실심볼 워크가 무창(전체 누적)으로 걷는 문제는 기존 결과 재현성이 걸려 있어
        여기서 건드리지 않고 문서 이슈로 남긴다 (docs/status/t202).
    """

    def __init__(self, inner: SealedFeed, bars: int) -> None:
        self._inner = inner
        self._bars = bars

    def __getattr__(self, name: str) -> object:
        """나머지는 전부 봉인 급전에 위임한다."""
        return getattr(self._inner, name)

    def judged(self, frame: Timeframe, *, at: datetime | None = None) -> Sequence[Candle]:
        """판정용 봉 — 봉인 급전의 답을 뒤에서 `bars` 개로 자른다.

        Args:
            frame: 시간축.
            at: 기준 시각. None 이면 지금.

        Returns:
            최근 `bars` 개 이하의 봉.
        """
        rows = self._inner.judged(frame, at=at)
        return rows[-self._bars :] if len(rows) > self._bars else rows


async def _load(
    symbol: str, market: Market, start: datetime, end: datetime
) -> dict[Timeframe, list[Candle]]:
    """봉인 구간 + 워밍업을 시간축별로 받는다.

    Note:
        🔴 **합성 워크 훅** (T202 · 사용자 승인 2026-09-02 · 기본 꺼짐): `WALK_SYNTH_DIR` 가
        설정되고 심볼이 `SYN` 으로 시작할 때만 거래소 대신 그 디렉토리의 합성 캔들을
        읽는다 — 라이브 배선(Session)을 합성 미래로 검증하는 문이다. 실심볼·기본
        경로는 한 톨도 안 바뀐다. ⛔ 프로덕션 env 에는 이 변수를 두지 않는다.
    """
    synth_dir = os.environ.get("WALK_SYNTH_DIR")
    if synth_dir and symbol.startswith("SYN"):
        return _load_synth(Path(synth_dir), symbol, market, start, end)
    instrument = instrument_of(symbol, market)
    out: dict[Timeframe, list[Candle]] = {}
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        for frame in FRAMES:
            rows = await adapter.get_candles(
                instrument, frame, start - interval(frame) * WARMUP_BARS, end
            )
            out[frame] = list(rows)
    return out


@router.post("/live")
async def live_start(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """새 RUN 을 만든다 — 사람이 콘솔에서 누르는 길.

    Args:
        request: 요청 — 이 매매법을 쓸 권한을 본다 (T230).
        payload: 아래 `_live_start` 와 같다.

    Returns:
        상태.

    Note:
        🔴 **되살리기와 가른다** (2026-08-19 사고 ⑤). 이 길에는 *"증거금만큼 현금이
        있나"* 검사가 붙는다 — 새로 만드는 것이니 옳다. 이어받기는 이미 자기 돈이
        포지션에 들어가 있는 것이 정상이라 그 검사를 받으면 안 된다.
    """
    return await _live_start(payload, request=request)


async def _live_start(
    payload: dict[str, Any], *, reviving: bool = False, request: Request | None = None
) -> dict[str, Any]:
    """**라이브 페이퍼 세션**을 띄운다 — Gate testnet 페이크머니 (T13).

    Args:
        payload: `{playbook, symbol, market, cash, leverage, skim}`.
            `symbol` 기본값은 `BTC_USDT`, `market` 은 `GATE` 다.
        reviving: **이미 있던 판을 되살리는 중**인가 (2026-08-19 사고 ⑤). 참이면
            증거금 검사를 건너뛴다 — 그 판의 돈은 이미 자기 포지션에 들어가 있어서
            `available` 이 비어 있는 것이 정상이기 때문이다. 새로 만들 때는 거짓이며,
            그때는 잔액을 넘는 예산을 막아야 한다.
        request: 사람이 누른 요청 — 이 매매법을 쓸 권한을 본다 (T230). 되살리기는 None.

    Returns:
        상태. `session_id` 로 목록·차트·매매 로그를 그대로 본다.

    Raises:
        HTTPException: 자격증명이 없으면 503, 플레이북이 없으면 404.

    Note:
        🔴 **기존 RUN 목록에 등록한다.** 그래서 금액·레버리지·매매·손익·차트를 보는
        화면이 **그대로 재사용**된다 — 라이브용 화면을 따로 만들면 한쪽만 고쳐지고,
        이 프로젝트가 그 사고를 세 번 겪었다.

        🔴 **주문 어댑터는 `OrderGateway` 에서 받는다** (절대 규칙 #0). 여기서 만들면
        게이트를 우회하는 경로가 생긴다.

        ⚠️ **진행률이 0 에 붙는다.** 라이브는 끝이 없어 비율이 성립하지 않는다
        (`LiveFeed.progress()`). 화면 막대가 안 차는 것이 정상이며, 임의 값을 넣으면
        그 값이 뜻을 가진 것처럼 보인다 (절대 규칙 #8).

        ⛔ **실주문이 아니다.** `GatePaperAdapter` 가 testnet 아닌 클라이언트를 거부하고,
        게이트의 LIVE 경로는 여전히 막혀 있다.
    """
    # ⭐ **세트** (T32 ⑥ · 사용자 설계 2026-08-22): `playbook: "a+b"` 또는 `playbooks: [..]`
    #    로 한 판에 플레이북 여럿을 싣는다. 국면 게이트가 걸음마다 맞는 것을 고른다 —
    #    횡보는 박스, 추세는 추세 플레이북. Gate 무기한은 종목당 포지션이 하나라 판
    #    둘로는 못 나눈다(아래 409) — 그래서 한 세션이어야 한다.
    #
    # ⚠️ 집행 플래그(방아쇠·지정가·사다리)는 세션 전역이라 **첫 플레이북** 것을 쓴다.
    #    세트의 첫 자리에는 박스를 둔다 — 돌파·눌림목은 즉시 진입이라 덜 민감하다.
    listed = payload.get("playbooks")
    names = (
        [str(name) for name in cast("list[object]", listed)]
        if isinstance(listed, list) and listed
        else [
            name.strip() for name in str(payload.get("playbook") or default_playbook()).split("+")
        ]
    )
    books = tuple(_playbook(name) for name in names if name)
    # T68 — 번들은 구성원으로 펼친다 (묶음 항목 자체는 세션에 안 들어간다).
    books = tuple(
        member
        for item in books
        for member in (tuple(_playbook(m) for m in item.bundle) if item.bundle else (item,))
    )
    # 🔴 이 매매법을 쓸 권한 (T230) — 서버 축(demo/live_trade)은 미들웨어가 봤다.
    #    되살리기(request 없음)는 통과.
    if request is not None:
        require_playbook_trade(request, (item.playbook_id for item in books))
    book = books[0]
    # ⭐ T250 — 셋업 없는 판(`custom`)은 **차트가 보던 축**을 판정 축으로 쓴다 (선언의 4h 는
    #    코인 기본값일 뿐). 토스는 1m/1d 원봉뿐이라 4h 를 1분봉 합성으로 만드는데, 600봉
    #    워밍업이 100일치 1분봉(수백 요청)이 됐다 (2026-09-09 실측 2분 454요청 → 재시작).
    #    셋업이 있는 판에는 이 통로가 없다 — 룰이 정한 축을 밖에서 바꾸면 화면과 매매가 갈린다.
    #    남은 비용(합성 축 워밍업 자체)은 T253.
    if not book.setups and payload.get("timeframe"):
        try:
            chart_frame = Timeframe(str(payload["timeframe"]))
        except ValueError as exc:
            raise HTTPException(400, f"모르는 판정 축이다: {payload['timeframe']}") from exc
        book = replace(book, timeframe=chart_frame)
        books = (book, *books[1:])
    set_id = "+".join(item.playbook_id for item in books)
    set_attribution = "+".join(item.attribution for item in books)
    # ⚠️ **문보다 먼저 읽는다** — 예산 검사가 사다리 모양(다리 비중)을 알아야 한다.
    catalog = load_rules()
    symbol = str(payload.get("symbol", "BTC_USDT"))
    market = Market(str(payload.get("market", Market.GATE.value)))
    if request is not None:
        require_market_trade(request, market)  # T242 — 이 시장에서 거래할 권한

    instrument = instrument_of(symbol, market)
    provider = MarketDataProvider()
    # 🔴 **이 API 가 연결한 거래소로만** (2026-09-06 · S7). `UPDOWN_MARKETS` 밖의 거래소로 판을
    #    띄우면 같은 계정을 다른 프로세스(서버 데모 · 로컬 데모)가 관리하고 있을 수 있다 —
    #    관리자 둘은 손절·진입·잔재 회수가 겹친다.
    if market.value not in provider.live_markets():
        raise HTTPException(
            400,
            f"{market.value} 는 이 API 가 연결하지 않은 거래소다 — 연결된 곳: "
            f"{', '.join(provider.live_markets()) or '없음'}. 데모 모드(Demo Trading)에서 띄운다",
        )
    quotes = provider.adapter_for(market)

    # 🔴 **어댑터는 게이트가 만든다** (절대 규칙 #0). 여기서 만들려 하자 AST 가드가
    #    잡았다 — 구체 어댑터를 아는 파일이 늘면 게이트가 관문이 아니게 된다.
    # 🔴 환경이 문을 정한다 (T157) — dev·paper 는 테스트넷, live+LIVE_ORDERS=1 은 실계좌.
    try:
        orders = order_adapter(quotes, user_id=str(payload.get("user", "live")))
    except OrderGatewayError as exc:
        raise HTTPException(503, str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(400, str(exc)) from exc

    # ⭐ 구체 클래스 나열 대신 계약 검사 (T63 §2b) — 새 거래소는 QuoteAdapter 를
    #   지키는 순간 자동으로 통과한다.
    if not isinstance(quotes, QuoteAdapter):  # pragma: no cover
        raise HTTPException(400, f"{market.value} 는 라이브 페이퍼를 지원하지 않는다")
    # ⭐ 어느 축을 주는지는 거래소가 선언한다 (T63 ② — supported_frames). 바이낸스
    #   10s·30s 부재(T62 P2b) 같은 사실을 조립부가 isinstance 로 알던 배선을 어댑터로 내렸다.
    # ⭐ T240 — 웹소켓이 없는 브로커(토스)는 봉을 REST 로 합성해 준다. 9개 축을 다 시드하면 축마다
    #    분봉 수만 개(첫 실측: 15분에 890 요청 · 판은 뜨지도 못함). 그래서 ① 축을 걸음·진입·일봉으로
    #    줄이고 ② DB 봉 캐시(`StoredCandles`)를 앞에 세운다 — 재시작·되살리기는 DB 에서 시드한다.
    #    시장 이름이 아니라 **능력**(WS 없음)으로 가른다.
    # 🔴 셋업 없는 판의 **방아쇠 축**은 급전을 조립하기 전에 안다 — 폴링 브로커의 축 목록에 넣어야
    #    한다(T250 실측: 1m 이 없어 매 걸음 실패 · 주문이 영영 안 나갔다). 문은 여기 하나다.
    trigger_asked: Timeframe | None = None
    if not book.setups:
        raw_trigger = str(payload.get("price_frame") or Timeframe.M1.value)
        try:
            trigger_asked = Timeframe(raw_trigger)
        except ValueError as exc:
            raise HTTPException(400, f"모르는 방아쇠 축이다: {raw_trigger}") from exc
    if Capability.WS not in quotes.capabilities:
        if _candles is None:
            raise HTTPException(503, "봉 저장소가 없다 — 폴링 브로커는 DB 캐시 없이 띄우지 않는다")
        quotes = StoredCandles(quotes, _candles, calendar=load_calendar())
        extra_frames = () if trigger_asked is None else (trigger_asked,)
        frames = list(
            quotes.supported_frames(needed_frames(book.timeframe, STEP_FRAME, *extra_frames))
        )
    else:
        frames = list(quotes.supported_frames(FRAMES))
    feed = await build_live_feed(quotes, instrument, frames, book.timeframe)
    account = await orders.get_balance()

    default = _drawn_flags(books)
    # 🔴 **셋업 없는 판은 그릴 것을 스스로 못 고른다** (차트 주문 · 2026-08-30).
    #    `_drawn_flags` 는 플레이북이 쓰는 **룰에서** 플래그를 뽑는다 — 룰이 없으면
    #    빈 목록이고, 그러면 차트 주문으로 띄운 판의 차트가 **아무것도 안 그린다.**
    #    사람이 무엇을 보고 잡았는지가 이 기능의 전부인데 그것이 사라진다.
    #
    # ⚠️ 셋업이 있는 판에는 이 통로가 없다 — 룰이 정한 그림을 밖에서 갈아 끼우면
    #    화면과 매매가 갈린다.
    if not book.setups and _flag_names(payload):
        default = tuple(_flag_names(payload))
    chosen = expand(default, available_flags(rules_config()))
    # ⛔ **적용 근거는 비어 있는 것이 맞다.** `custom` 은 판단을 안 한다 — 플래그는
    #    사람이 본 것이지 러너가 매매에 쓰는 것이 아니다 (T13 ②).
    applied = tuple(book.primary_flags)

    # 🔴 **증거금을 받는다** (사용자 확정 2026-08-18: *"포지션 증거금 입력으로 바뀌어야
    #    하고"*). 시드 금액이 아니다 — 시드는 지갑이고 증거금은 그중 굴리는 돈이다.
    #
    # ⚠️ **거래소 주문 API 에는 증거금 필드가 없다** (2026-08-18 실측). 주문은 계약수로
    #    내고 격리 마진에서 필요한 증거금은 거래소가 잡는다. 그래서 이 값은 우리가
    #    계약수를 계산할 때 쓰는 **예산**이다.
    # 🔴 **시드는 계정 총액이다** (2026-08-19 사고로 잡았다). `available` 만 쓰면 다른
    #    RUN 이 포지션을 들고 있을 때 그 증거금만큼 **작은 계좌**로 시작한다 — 실측:
    #
    #      BTC RUN 이 297 을 잡은 상태에서 다른 RUN 이 뜨자 시드가 998 → 700 이 됐고,
    #      감사가 "원장이 사실과 갈렸다" 고 외쳤다 (29.8% 차이).
    #
    #    ⛔ **포지션에 들어간 돈은 사라진 돈이 아니다.** 이 프로젝트가 반복하는 실수다.
    #
    # ⚠️ 다만 **쓸 수 있는 돈은 `available` 뿐이다** — 증거금 검사는 그쪽으로 한다.
    #    선착순이라 남이 잡은 돈은 지금 내가 못 쓴다.
    free = Decimal(str(account.cash))
    locked = Decimal(0)
    if isinstance(orders, MarginAware):
        with contextlib.suppress(Exception):
            locked = await orders.account_margin()
    wallet = free + locked
    raw_margin = payload.get("margin")
    # 기본값도 **쓸 수 있는 돈**이다 — 계정 총액으로 두면 남이 잡은 돈까지 예산이 된다.
    margin = free if raw_margin in (None, "") else Decimal(str(raw_margin))
    if margin <= 0:
        raise HTTPException(400, f"증거금이 0 이하다 ({margin}) — 주문을 낼 수 없다")
    # 🔴 **수익선·천장은 증거금 위의 선이다** (T21 ⑤⑥ · 2026-08-22 사용자 확정). 화면은
    #    이제 **증거금 초과분**을 받는다 — `"20%"` 면 증거금의 20% 위, `"200"` 이면 200 위.
    #    절대값으로 받던 때 기본값(200·300)이 증거금 1000 보다 낮아 첫 정산에서 증거금이
    #    통째로 금고로 빠지고 판이 멈췄다. 상대값이면 그 함정 자체가 없다.
    line = over_margin(payload.get("profit_line"), margin, "수익선")
    ceiling = over_margin(payload.get("budget_cap"), margin, "한계 가용자금")
    if line is not None and ceiling is not None and ceiling <= line:
        raise HTTPException(
            400,
            f"한계 가용자금({ceiling})이 수익선({line}) 이하다 — 수익선을 넘기 전에 천장에 "
            f"걸려 실현이 한 번도 안 된다. 천장은 수익선보다 높아야 한다",
        )
    # 🔴 T22 — 낙폭 브레이커. 비우면 안 건다.
    stop = drawdown_stop_of(payload.get("drawdown_stop"))
    if margin > free and not reviving:
        # ⛔ 시작을 거부한다. 시작해 두고 첫 주문에서 거부되면 원인이 화면에 안 보이고,
        #   "주문 0건" 으로만 나타난다 (절대 규칙 #8).
        #
        # 🔴 **되살릴 때는 안 본다** (2026-08-19 사고 ⑤). 이미 존재하던 판이고, 자기
        #    포지션에 증거금이 들어가 있으면 쓸 수 있는 돈이 비어 있는 것이 **정상**이다.
        #    실측: DOGE 가 계좌의 84% 를 물고 있어서 BTC·ETH 판이 리로드에 못 살아났고,
        #    거래소 포지션(ETH 숏 -246 · 20배)은 관리자 없이 남았다.
        raise HTTPException(
            400,
            f"증거금 {margin} 이 지금 쓸 수 있는 돈 {free} 보다 크다 "
            f"(계정 총액 {wallet} 중 {locked} 은 다른 포지션이 잡고 있다) — "
            "잔액을 넘는 증거금으로는 주문이 거부되고, 그 실패는 화면에서 '주문 0건' 으로만 보인다",
        )

    # 🔴 **눈금을 모르면 여기서 막는다** (T18 ①). 판정 중에 터지면 그 걸음만 실패하고
    #    화면에는 "판정 0회" 로만 보인다 — 문 앞에서 막아야 이유가 보인다 (규칙 #8).
    #
    # ⛔ 조용히 원화 기본값(500)으로 떨어지던 것이 2026-08-18 의 버그였다. Gate BTC 에서
    #    그것은 가격의 0.78% 이고, 그 눈금으로 숏 계획이 21건 중 1건만 섰다.
    try:
        resolve_tick(
            load_cost_table(DEFAULT_CONFIG_PATH).for_market(market),
            symbol,
            (await quotes.get_quote(instrument)).last_price,
            krw=instrument.currency is Currency.KRW,
        )
    except TickUnknownError as exc:
        raise HTTPException(400, str(exc)) from exc

    # 🔴 **들어가기 전에 나올 수 있는지 본다** (2026-08-20 사고).
    #
    #    SPCX_USDT 에 증거금 420 이 묶였다. 화면의 모든 숫자가 멀쩡했다 — 가격·손익·
    #    청산가가 전부 **표시가**(지수 기반) 기준이었기 때문이다. 정작 체결은 호가창에서
    #    나는데 그쪽은 이랬다:
    #
    #      표시가 139.06 · 최고 매수 108 (22% 구멍) · 최저 매도 138
    #
    #    한쪽만 두꺼운 시장이라 **들어가긴 쉽고 나오긴 불가능**했다. Gate 는 표시가에서
    #    20% 넘게 벗어난 주문을 아예 안 받으므로, 팔 수 있는 최저가와 유일한 매수자가
    #    3.25 차이로 영영 안 만난다.
    #
    # ⚠️ **눈금 검사(위)로는 못 잡는다.** 저것은 *"우리가 이 종목의 눈금을 아는가"* 라는
    #    설정 질문이고, 시장이 살아 있는지는 한 글자도 안 본다.
    #
    # ⛔ **못 읽으면 막지 않는다** — 조회 실패로 판을 못 띄우는 것도 사고다 (§1.2.1).
    # ⚠️ 함수 안에서 들여온다 — 모듈 꼭대기에서 서로를 부르면 순환이 된다
    #    (`exchange` 도 이 모듈의 것을 쓴다).
    from updown.apps.api.exchange import liquidity_of, sizing_of

    lever = Decimal(str(payload.get("leverage", 1)))
    # 🔴 **그 판의 거래소로** 본다 (2026-09-06) — 없으면 Gate 호가창으로 바이낸스 판을 판정한다.
    depth = await liquidity_of(symbol, margin * lever, market=market.value)
    if not depth.ok:
        raise HTTPException(
            409,
            f"{symbol} 는 지금 호가가 이 규모를 못 받는다 — {depth.why}. "
            "들어가면 나올 때 값이 없다 (2026-08-20 에 SPCX 로 증거금 420 이 묶였다). "
            "다른 종목을 고르거나 증거금을 줄인다",
        )

    # 🔴 **예산이 1계약을 살 수 있나** (사용자 신고 2026-08-20: *"이거는 왜 주문이 한번도
    #    없었어? 말이 안되는데."*). SOL·ETH 판이 7시간 돌면서 한 건도 못 냈다 — 자리는
    #    나왔는데 `contracts_for` 가 매번 0 을 냈고 그때마다 거절됐다:
    #
    #      SOL  자본 4.95 x 3배 = 14.85 USDT  vs  1계약 87.11   → 0계약
    #      ETH  자본 4.95 x 3배 = 14.85 USDT  vs  1계약 22.82   → 0계약
    #      BTC  자본 4.95 x 3배 = 14.85 USDT  vs  1계약  7.19   → 2계약 ✅
    #
    # ⚠️ **위 두 문으로는 못 잡는다.** 눈금 검사는 설정 질문이고 유동성 검사는 시장
    #    질문이다 — 이것은 **내 예산 질문**이라 셋이 서로 다른 사건이다.
    #
    # ⛔ 몇 시간 뒤 "0건" 으로 알게 되면 전략을 의심하게 된다. 띄우는 순간에 막는다.
    fit = await sizing_of(
        symbol, margin, lever, leg=thinnest_leg(catalog, book), market=market.value
    )
    if not fit.ok:
        raise HTTPException(409, f"{symbol} 는 이 예산으로 주문이 안 나간다 — {fit.why} USDT")

    # 🔴 **이 종목에 주인 없는 잔재가 있으면 치우고 시작한다** (사용자 요구 2026-08-21:
    #    *"run 을 띄울 때 선택할 수 있어야 할 것 같은데"*).
    #
    # ⭐ **묻지 않는다.** 그 종목으로 판을 띄운다는 것은 이제 이 판이 그 종목을 맡는다는
    #    뜻이고, 주인 없는 트리거는 어느 쪽을 골라도 새 판을 방해한다 — `size: 0` 조건부는
    #    **새 포지션을 통째로 닫는다.** 답이 하나뿐인 질문을 시작 순간에 띄우면 사람은
    #    읽지 않고 누르게 되고, 그러면 정작 물어야 할 때도 그냥 누른다.
    #
    # ⛔ **포지션이 있으면 `sweep` 이 스스로 아무것도 안 한다.** 그 경우는 이어받기
    #    (`LiveRunner.adopt`)의 몫이고, 이어받기는 **조건부 손절에서 계획을 되읽으므로**
    #    여기서 거두면 이어받을 근거를 우리가 지우는 셈이 된다.
    swept = await sweep_leftovers(orders, instrument, why="판 시작")
    # ⭐ 좀비 진입 지정가 정리는 **판을 되살린 뒤**로 옮겼다 (T218 · 아래). 저장된 대기 계획이
    #    되살리는 표는 좀비가 아니라서, 먼저 거두면 이어받을 것을 우리가 지우는 셈이 된다.

    # 🔴 **주문이 나가는 곳에 이 계약이 있나** (2026-08-19 · 토큰화 주식 추가로 드러났다).
    #
    #    조회는 라이브 API, 주문은 testnet 이라 **한쪽에만 있는 종목이 존재한다** —
    #    실측: SNDK_USDT · SKHY_USDT 는 라이브에 있고 testnet 에는 없다.
    #
    #    막지 않으면 판이 멀쩡히 떠서 판정을 돌고, 진입 주문에서만 거절당한다. 그 실패는
    #    화면에서 **"주문 0건"** 으로만 보여 원인을 못 찾는다 (절대 규칙 #8).
    #
    # ⚠️ 이것은 아침에 고친 ① 과 **같은 병의 다른 얼굴**이다 — 명세를 어느 거래소에서
    #    읽느냐가 곧 그 값의 뜻이다. 그래서 여기서도 **주문 어댑터**에게 묻는다.
    # 🔴 **판들의 예산 합이 계좌를 넘지 않게** (T21 ⑨). 되살릴 때는 안 본다 —
    #    이미 존재하던 판이고 그 예산은 이미 합에 들어 있다.
    if not reviving:
        await _budget_room(orders, margin, symbol, market)

    watching = False
    if hasattr(orders, "contract_spec"):
        try:
            await orders.contract_spec(instrument)  # type: ignore[attr-defined]
        except Exception as exc:
            # ⭐ **막지 않고 관찰 전용으로 돌린다** (사용자 확정 2026-08-19). 봉도 오고
            #    박스도 서고 방아쇠도 켜진다 — 못 하는 것은 주문뿐이다.
            watching = True
            _logger.warning(
                "live_observe_only",
                payload={
                    "symbol": symbol,
                    "why": str(exc)[:140],
                    "note": "주문 경로에 계약이 없다 — 판정만 돌린다",
                },
            )

    session = Session(
        instrument=instrument,
        playbooks=books,
        feed=feed,
        registry=_shared_registry(),
        # T226 — 라이브는 거래소 정산 기록을 붙인다(`_sync_funding`). 모형까지 켜면 두 번 낸다.
        model_funding=False,
        ledger=Ledger(
            # 🔴 **계좌가 진실이다** (T14-1). 라이브는 아무도 입금하지 않으므로,
            #    잔고가 시드 아래로 갈 때 자동으로 채우는 백테스트 모형을 쓰면 원장이
            #    돈을 만들어 낸다 — 실측에서 원장 1000 / 거래소 800 이 됐고 그 차이만큼
            #    주문이 거부됐다.
            funding=Funding.WALLET,
            # ⭐ **지갑은 증거금을 뺀 나머지다.** 격리 마진에는 '증거금 지갑' 이 따로
            #    없다 — RUN 생성은 검증만 하고 실제 증거금은 체결될 때 Gate 가 뗀다.
            #    그래서 `증거금 + 지갑 = 거래소 available` 이 성립한다.
            wallet_start=max(wallet - margin, Decimal(0)),
            # ⭐ **시드(지갑)는 거래소 잔고다.** 화면에서 받은 값을 쓰면 원장과 계좌가
            #   다른 돈을 세고, 그 차이가 손익률에 그대로 들어간다.
            seed_cash=wallet,
            # 🔴 굴리는 돈은 **따로** 든다 — 이것이 주문 크기를 정한다.
            margin_budget=margin,
            leverage=Decimal(str(payload.get("leverage", 1))),
            skim_pct=min(max(Decimal(str(payload.get("skim", 0))), Decimal(0)), Decimal(100))
            / Decimal(100),
            # 🔴 **금고 셋** (T21). 안 주면 None 이고, None 이면 지금까지와 한 줄도
            #    다르지 않게 돈다 (§5.6.2 동결).
            #
            # ⚠️ 재충전 상한만 **전역**이다 — 금고는 하나인데 판마다 다른 상한을 쓰면
            #    판 셋이 각자 태워 합이 세 배가 된다.
            profit_line=line,
            budget_cap=ceiling,
            # ⚠️ 비율(`30%`)이면 **판을 띄우는 순간의 계정 총액** 기준으로 푼다 —
            #    걸음마다 다시 풀면 원장이 과거 충전까지 새 기준으로 세어 결정론이
            #    깨진다 (규칙 #5).
            refill_cap=await _refill_cap(wallet),
            drawdown_stop_pct=stop,
        ),
    )
    session.flip_on_opposite = any(
        bool(catalog[name].params.get("symmetric")) for name in book.setups if name in catalog
    )
    # ⭐ T46 — 돌파 사건에만 반응하는 전환 스위치 (백테스트 `walk_session` 과 같은 배선).
    session.flip_on_event = any(item.flip_on_opposite for item in session.playbooks)
    # T66-e/T68 — 건당 리스크는 **제안을 낸 플레이북**이 정한다 (_exposure 인자).
    #    여기서는 상한만 판의 배율로 맞춘다 — 세션 전역 risk_pct 를 넣으면 번들에서
    #    추세(고정 배율)까지 r 사이징으로 오염된다.
    if any(item.risk_pct is not None for item in session.playbooks):
        session.leverage_cap = session.ledger.leverage
    # 🔴 β — 손절을 청산거리 안쪽으로 당기는 상한 (T120~T146).
    #    `require_stop_cap` 이 **배율과 짝을 강제한다**: 문턱(3x)을 넘는 배율인데 β 가
    #    없으면 여기서 터진다. 측정이 말하는 것은 "6x 가 좋다" 가 아니라 "β 를 켠 6x 가
    #    좋다" 이고(6x β0 은 청산 25건 · T144), 둘을 따로 켤 수 있게 두면 언젠가 반쪽만
    #    켜지는데 그 반쪽이 하필 위험한 쪽이다.
    try:
        _risk = load_risk_settings()
        session.stop_cap_ratio = require_stop_cap(_risk, session.ledger.leverage)
        # 🔴 손절 하한 (T147~T150) — β 의 짝. 손절거리를 [하한, β x 청산거리] 로 가둔다.
        session.stop_min_pct = _risk.stop_min_pct
    except RiskConfigError as exc:
        # ⛔ 시작을 거부한다 — 시작해 두고 청산이 나는 것보다 낫다 (절대 규칙 #8).
        raise HTTPException(400, str(exc)) from exc
    # ⭐ T42 ⑤ — 국면 RANGE 판정이 탐지기와 같은 최소 폭을 쓴다 (백테스트와 같은 배선).
    # ⭐ T233 ② — close 매매법의 라이브 보호 손절 자리. 백테스트는 안 쓰지만 같은 함수가 세팅해
    #    "화면 숫자 = 라이브 설정" 을 지킨다. close 매매법이 있는데 비율이 없으면 라이브가
    #    무방비라 막는다.
    session.stop_protect_ratio = _risk.stop_protect_ratio
    if _risk.stop_protect_ratio is None and any(
        item.stop_mode == "close" for item in session.playbooks
    ):
        raise RiskConfigError(
            "stop_mode: close 매매법인데 config/risk.yml 에 stop_protect_ratio 가 없다 — "
            "라이브가 거래소에 걸 보호 손절 자리가 없다"
        )
    # ⭐ T239 — 시장 능력표: 현물은 숏 없음 · 배율 없음. 시장 이름으로 분기하지 않고 표를 읽는다.
    caps = capabilities_of(session.instrument.market)
    session.short_allowed = caps.short_allowed
    if not caps.leverage_allowed and session.ledger.leverage > 1:
        raise RiskConfigError(
            f"{session.instrument.market} 는 배율을 쓸 수 없는 시장인데 원장 배율이 "
            f"{session.ledger.leverage} 다 — 매매법 선언의 leverage 를 지우거나 1 로 둔다"
        )
    # ⭐ T241 — 일중 청산은 마감이 있는 시장에서만 뜻이 있다. 달력을 세션에 준다.
    session.flat_at_close = any(item.flat_at_close for item in session.playbooks)
    if session.flat_at_close:
        if caps.always_open:
            raise RiskConfigError(
                f"{session.instrument.market} 는 24시간 장이라 마감 청산(flat_at_close)이 없다 — "
                "매매법 선언을 지운다"
            )
        session.calendar = load_calendar()
    session.span_cover = span_cover_of(catalog, book)
    # ✅ T42 ④ (사용자 확정 2026-08-22) — 라이브 원장도 체결 유형대로 센다. 일간 리포트의
    #    거래소 실제 수수료와 같은 자가 된다. 관문은 0.15% 그대로.
    session.fill_cost = True
    # 🔴 **진입가는 방아쇠 봉의 종가다** (T17 ③). 방아쇠만 내리고 진입가를 5m 종가로
    #    적으면 **한 계획 안에 두 시점이 섞이고**, 그것이 2026-08-18 사고의 모양이다.
    #
    # ⛔ 룰이 선언 안 하면 None 이라 0.1 은 한 줄도 안 달라진다 (§5.6.2).
    session.price_frame = trigger_frame(catalog, book)
    # 🔴 **셋업 없는 판은 스스로 방아쇠를 못 정한다** (차트 주문 · 2026-08-30).
    #
    #    `trigger_frame` 은 **룰 설정에서** 축을 읽는다 — 셋업이 없으면 읽을 곳이 없어
    #    `None` 이 되고, 그러면 판정이 **진입 축 마감에만** 돈다. `custom` 은 4h 라
    #    손절 확인이 **4시간에 한 번**이 된다는 뜻이다.
    #
    # ⛔ 그것은 손절이 없는 것과 거의 같다. 그래서 셋업이 없을 때만 띄우는 쪽이 축을
    #   정하게 하고, 안 주면 1분으로 둔다 — 조용히 4h 로 떨어지지 않는다 (규칙 #8).
    #
    # ⚠️ **셋업이 있는 판에는 이 통로가 없다.** 룰이 선언한 축을 밖에서 갈아 끼울 수
    #   있으면 그것은 조작 통로이고, 0.4 가 0.1 처럼 돌면서 0.4 로 기록된다 (T17 ③).
    #
    # ⚠️ **라이브에만 있다.** 백테스트 경로에는 안 넣었다 — 셋업 없는 판은 백테스트에서
    #   매매를 한 건도 안 만들고, 넣어 두면 "쓰이지 않는데 있는 통로"가 된다.
    if trigger_asked is not None:
        session.price_frame = trigger_asked
    # ⭐ 걸어 두고 받는 판이면 여기서 켜진다 (T19 ④). 선언이 없으면 시장가 그대로다.
    session.limit_entry = wants_limit(catalog, book)
    session.post_only_entry = wants_post_only(catalog, book)
    # 🔴 **걸어 두고 받는 판만 우편함을 든다** (T19 ⑤). 시장가 판에 꽂으면 아무도
    #     를 안 부르므로 무해하지만, 없는 편이 *"이 판이 무엇으로 도는가"* 가
    #    분명하다.
    session.shallow_entry = wants_shallow(catalog, book)
    # ⭐ 0.52 — 옮긴 진입가로 비용을 다시 잰다. 선언 없으면 0.5·0.51 그대로다.
    session.recheck_after_shift = wants_recheck(catalog, book)
    # 🔴 브레이커 (T22) — RUN 낙폭 문턱. 안 주면 None = 꺼짐 (§5.6.2 동결).
    #    값은 코드가 아니라 띄우는 쪽이 정한다 — 문턱의 근거는 낙폭 실측이다.
    session.loss_limit_pct = _money(payload.get("loss_limit"))
    session.filler = LiveFiller() if session.limit_entry else None
    # 🔴 **방아쇠 축 봉이 마감돼도 한 걸음 돈다** (T17). 이것이 없으면 판정이 진입 축
    #    마감에만 돌아, 방아쇠를 10초로 내려도 **빨라지지 않는다** — 실제로 그랬다.
    if session.price_frame is not None:
        feed.judge_on = {feed.entry, session.price_frame}
    attach(session, feed)

    handle = _safe_key(str(payload.get("session_id") or f"live{uuid4().hex[:8]}"))
    # 🔴 **판을 DB 에서 여닫는다** (T16 ②). 같은 닻(종목 + 매매법 + live)의 **열린**
    #    판이 있으면 그 판을 이어받는다 — 같은 key 로 다시 뜨고 매매 목록이 딸려 온다.
    #
    # ⛔ 저장소가 없으면 **띄우지 않는다.** 저장 없이 도는 라이브가 곧 이 태스크가
    #    고치는 버그이고, 조용히 그 상태로 돌아가면 아무도 못 알아챈다.
    if _store is None:
        raise HTTPException(
            503,
            "판 저장소가 없다 — DB 없이 라이브를 띄우면 재시작에 원장이 사라지고 "
            "거래소 포지션만 남는다 (반익·본절 상향·손절 재장착이 전부 멈춘다)",
        )
    # 🔴 **한 종목에 한 판** (다중 RUN 경로 가). Gate 무기한은 종목당 포지션이 하나라,
    #    두 판이 같은 종목을 돌리면 포지션이 합쳐지고 두 원장이 같은 포지션의 손익을
    #    각각 센다 — 양쪽 성적이 다 틀린다.
    #
    # ⚠️ **닻이 같으면 막지 않는다** — 그것은 남의 판이 아니라 우리가 이어받을 판이다.
    #    막으면 재시작이 통째로 실패하고 포지션이 고아가 된다.
    # 🔴 **닻이 같아도 "이미 도는 판" 이면 막는다** (사용자 신고 2026-08-20).
    #    예외의 뜻은 *"재시작이 옛 판을 되찾는다"* 인데, 도는 판이 있는데도 통과시키면
    #    같은 종목·같은 매매법으로 판을 **몇 개든** 띄울 수 있었다. 실측:
    #
    #      live07b06d20 · live125d9d75 · live24453961  →  전부 BTC_USDT · 같은 플레이북
    #
    #    셋이 한 포지션을 공유하고 세 원장이 같은 손익을 각각 센다 — 성적이 셋 다 틀린다.
    #
    # ⚠️ **이어받기는 그대로 열어 둔다.** 러너가 없으면(프로세스가 죽었다) 그 판은
    #    되찾아야 한다 — 막으면 재시작이 통째로 실패하고 포지션이 고아가 된다.
    for other, live in SESSIONS.items():
        # T62 — **시장까지 같아야 같은 포지션이다.** Gate BTC 와 Binance BTC 는 다른
        #   상품이라 서로 안 막는다 (DB 닻 검사도 이미 market+symbol 이다).
        if (
            other in LIVE_RUNNERS
            and live.session.instrument.symbol == symbol
            and live.session.instrument.market is market
        ):
            raise HTTPException(
                409,
                f"{symbol} 는 판 {other} 가 **지금 돌리고 있다** — Gate 무기한은 종목당 "
                "포지션이 하나라, 두 판이 같은 종목을 돌리면 포지션이 합쳐지고 두 원장이 "
                "같은 포지션의 손익을 각각 센다 (양쪽 성적이 다 틀린다). "
                "다른 종목을 고르거나 그 판을 먼저 지운다",
            )
    try:
        holder = await _store.holder_of(market.value, symbol, live=True)
    except RunStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    # ⭐ **자기 행은 안 막는다** (2026-08-27 실사고): 플레이북 버전 승격(0.4.0→0.4.1)
    #    으로 귀속 문자열이 바뀌면 부활이 자기 닻과 어긋나 409 를 자신에게 던졌다 —
    #    재시작마다 판 전부가 고아가 된다. 같은 키의 부활/재개는 닻을 새 귀속으로
    #    덮어쓰는 것이 맞다 (전략 전환 T61 과 같은 철학).
    if (
        holder is not None
        and holder[0] != handle
        and holder[1] != anchor_of(market.value, symbol, set_attribution, live=True)
    ):
        raise HTTPException(
            409,
            f"{symbol} 는 이미 판 {holder[0]} 가 돌리고 있다 — Gate 무기한은 종목당 "
            "포지션이 하나라, 두 판이 같은 종목을 돌리면 포지션이 합쳐지고 두 원장이 "
            "같은 포지션의 손익을 각각 센다 (양쪽 성적이 다 틀린다). "
            "다른 종목을 고르거나 그 판을 먼저 지운다",
        )
    try:
        opened = await _store.open(
            key=handle,
            market=market.value,
            symbol=symbol,
            # ⭐ 세트는 `a@1+b@2` 로 저장한다 — 재개 때 같은 문자열로 되살린다.
            playbook=set_attribution,
            playbook_id=set_id,
            live=True,
            seed_cash=session.ledger.seed_cash,
            margin_budget=margin,
            leverage=session.ledger.leverage,
            skim_pct=session.ledger.skim_pct,
            # ⭐ 풀린 값도 열에 남긴다 — 지금까지 안 넘겨서 wf_runs 의 수익선·천장이 늘 비어 있었다.
            profit_line=line,
            budget_cap=ceiling,
            # ⭐ 띄울 때 받은 설정 **원문**을 같이 남긴다 — 되살릴 때 그대로 넘긴다
            #    (`revive_settings_of`). 없으면 판이 설정 없이 되살아난다.
            meta={
                "flags": list(chosen),
                "applied": list(applied),
                "skim": str(payload.get("skim", 0)),
                "profit_line": str(payload.get("profit_line") or ""),
                "budget_cap": str(payload.get("budget_cap") or ""),
                "drawdown_stop": "" if stop is None else str(stop),
                # 🔴 **주문 표식을 영속화한다** (2026-08-25). 감시자 부활(run_revive)은 새
                #    핸들로 뜨는데 거래소 주문 태그는 이 표식이라, 안 넘기면 되살아난 판이
                #    자기 포지션을 "남의 판 것"으로 거부한다 (adopt 가드). 부활 때
                #    `revive_settings_of` 가 이 값을 그대로 넘긴다.
                "run_key": str(payload.get("run_key") or handle),
                # 🔴 **차트 주문이 쓴 플래그** (사용자 확정 2026-08-30: (다)안 — 이름은
                #    `custom` 하나, 조합은 메타로). 이것이 없으면 나중에 이 매매를 보고
                #    *"무엇을 보고 잡았나"* 를 물을 데가 없고, 그 질문이 기능의 전부다.
                #
                # ⚠️ 위의 `flags`(그릴 것) 와 다른 열이다. 그릴 것은 화면 설정이라
                #    나중에 바뀔 수 있고, 이것은 **매매 당시의 사실**이라 안 바뀐다.
                "custom_flags": _flag_names(payload),
                # ⭐ T250 — 셋업 없는 판의 판정 축(차트가 보던 축). 되살리기가 이 값을
                #    `timeframe` 으로 돌려주지 않으면 선언 축(4h)으로 되살아나 급전이
                #    얼어붙는다(`frame_frozen` 실측).
                **({"judge_frame": book.timeframe.value} if not book.setups else {}),
            },
        )
    except RunStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    handle = opened.key
    if opened.resumed:
        # ⭐ **매매 목록만 이어붙인다.** `steps` 와 시드 구간은 안 붙인다 — 그 둘은
        #    *이 프로세스가* 본 것이라 이어 붙이면 화면이 거짓말한다 (T16 문서).
        session.ledger.records.extend(opened.records)
        # 🔴 **보유 중이라는 것도 같이 되찾는다** (사용자 질문 2026-08-20: *"예산 제한을
        #    걸어뒀을 것 같은데 SPCX 가 어떻게 저렇게 많이 걸었지?"*).
        #
        #    원장만 되찾고 세션의 `_open` 은 빈 채로 뒀다. 그러면 `step()` 의
        #    `idle = self._open is None` 이 **참**이 되어, 이미 포지션을 든 채로 새
        #    진입이 또 나간다. 리로드마다 한 번씩:
        #
        #      01:46  1408 계약   ← 첫 진입
        #      01:55  2818        ← +1410 (리로드)
        #      01:56  4228        ← +1410 (리로드)
        #      01:56  5638        ← +1410 (리로드) · 새 매매 id
        #
        #    예산 100 을 걸어 뒀는데 증거금 435.99 가 잡힌 것이 이것이다 — **예산은
        #    주문 하나의 한도이지 포지션의 한도가 아니다.**
        #
        # ⚠️ `runner.adopt()` 는 이 자리를 못 메운다. 원장에 열린 기록이 있으면 *"이미
        #    안다"* 며 물러나는데(그게 맞다), 정작 `_open` 은 아무도 안 채웠다.
        held = [item for item in opened.records if item.outcome is Outcome.OPEN]
        if held:
            session.adopt(held[0])
            if len(held) > 1:
                # ⛔ 조용히 하나만 고르지 않는다 — 열린 기록이 둘이라는 것 자체가 사고의
                #    잔해이고(위 겹침), 그 사실은 사람이 봐야 한다 (절대 규칙 #8).
                _logger.error(
                    "live_resume_multiple_open",
                    payload={
                        "session_id": handle,
                        "open": [item.trade_id for item in held],
                        "note": "열린 기록이 여럿이다 — 첫 건만 보유로 잡는다",
                    },
                )
        _logger.info(
            "live_run_resumed",
            payload={
                "session_id": handle,
                "trades": len(opened.records),
                "held": held[0].trade_id if held else "",
                "opened_at": opened.opened_at.isoformat(),
                "note": "옛 판을 이어받았다 — 판정 횟수와 시드 구간은 새로 센다",
            },
        )
    session.journal_path = JOURNAL_ROOT / f"{handle}.json"
    session.journal_meta = {
        "session_id": handle,
        "symbol": symbol,
        "market": market.value,
        "playbooks": [item.attribution for item in books],
        "primary": book.attribution,
        "seed": None,
        # ⚠️ 라이브 구간은 **자란다** — 저널에 적히는 것은 그 순간의 값이다.
        "seal": {"start": feed.seal.start.isoformat(), "end": feed.seal.end.isoformat()},
        "seed_cash": str(session.ledger.seed_cash),
        "skim_pct": str(session.ledger.skim_pct),
        "drawdown_stop": "" if stop is None else str(stop),
        "leverage": str(session.ledger.leverage),
        "margin_budget": str(margin),
        "days": 0,
        "playbook_id": set_id,
        "flags": list(chosen),
        "applied": list(applied),
        # 🔴 되살릴 때 **백테스트로 되살아나지 않게** 표시한다. 라이브는 봉인 구간이
        #    없으므로 `/resume` 으로는 못 되살린다 — 다시 `POST /live` 해야 한다.
        "live": True,
        # ⭐ DB 의 판 id (T16 ②) — 저널만 남아도 어느 판의 것인지 잇는다.
        "run_id": str(opened.run_id),
        "resumed": opened.resumed,
    }

    spec = await quotes.contract_spec(instrument)
    # 어느 스트림인지는 어댑터가 안다 (T63 §2b — candle_stream 팩토리). 계약은 같다
    # (LiveCandle · reconnects · is_testnet).
    stream = quotes.candle_stream(
        [instrument], book.timeframe, Decimal(str(spec["quanto_multiplier"]))
    )
    runner = LiveRunner(session, feed, stream, quotes, orders)
    # 🔴 **로그가 어느 판인지 말하게 한다** (T76). 이 줄이 없으면 11개 판의
    #    오류가 구분 없이 섞인다 — 실제로 그래서 고아 셋을 손으로 세야 했다.
    runner.identify(handle)
    # ⛔ 주문 경로에 계약이 없으면 **한 건도 안 낸다.** 그 사실은 화면이 말한다.
    runner.observe_only = watching
    # 🔴 **조용히 지우는 것과 조용히 넘어가는 것은 다르다** (규칙 #8). 묻지 않기로 한
    #    대신 무엇을 치웠는지는 판 상세에 남긴다 — 사람이 나중에 *"내 손절 어디 갔지"*
    #    라고 물을 때 답이 화면에 있어야 한다.
    runner.swept = [f"{item.kind} {item.at}" for item in swept]
    # 🔴 **판이 여럿이면 잔액 대조가 성립하지 않는다** (2026-08-20 ⓓ).
    #    판마다 원장이 `seed_cash = 계좌 총액` 으로 시작하므로, 둘이면 둘 다
    #    *"계좌 전체가 내 것"* 이라 여기고 둘 다 갈렸다고 외친다.
    #
    # ⚠️ 값이 아니라 **함수**를 꽂는다 — 판은 도는 중에도 뜨고 죽는다.
    # T62 P3b — **같은 거래소의 판만** 센다. Gate 와 Binance 는 계좌가 달라, 섞어 세면
    #   바이낸스 판 하나가 Gate 판 넷 때문에 "잔액 대조 불가" 로 침묵한다.
    runner.peers = lambda: max(
        sum(1 for item in LIVE_RUNNERS.values() if item.instrument.market is market) - 1, 0
    )
    # 🔴 **합계도 꽂는다** (2026-08-30). 위의 `peers` 만 있을 때 감사는 판이 여럿이면
    #    *"가를 수 없다"* 며 물러섰고, 판을 여럿 띄우는 것이 상시라 **원장-사실 대조가
    #    한 번도 안 돌았다.** 가를 수 없는 것은 판 하나의 몫이지 합계가 아니다.
    #
    # ⚠️ 이것도 함수다 — 판은 도는 중에도 뜨고 죽는다.
    runner.account_modelled = lambda: sum(
        (item.ledger.equity for item in LIVE_RUNNERS.values() if item.instrument.market is market),
        Decimal(0),
    )
    # 🔴 걸음마다 원장을 DB 에 다시 쓴다 — 파일 저널이 하던 그대로다.
    # 🔴 **주문 표식(run_key)은 보통 handle 이지만, 무중단 전략 전환 때는 넘겨받는다**
    #    (T61). 새 전략 세션이 앞 세션의 거래소 포지션을 `adopt` 하려면, 그 포지션에
    #    걸린 주문의 표식과 자기 표식이 같아야 한다 — 다르면 "남의 판" 이라 안 줍는다.
    runner.use_store(_store, opened.run_id, str(payload.get("run_key") or handle))

    # 🔴 T218 — 저장돼 있던 **대기 진입 계획**을 먼저 되살리고, 그 표는 좀비 정리에서 뺀다.
    #    전에는 대기 계획이 메모리에만 있어 재시작마다 거래소의 진입 지정가를 전부 거뒀다
    #    (실계좌 첫날 배포 한 번에 BTC·ETH 표가 그렇게 취소됐다).
    kept_ids: set[str] = set()
    raw_pending = opened.meta.get(pending_mod.META_KEY) if opened.resumed else None
    if raw_pending:
        try:
            kept_ids = await runner.restore_pending(pending_mod.from_json(raw_pending))
        except Exception as exc:
            _logger.error(
                "live_pending_restore_failed",
                payload={
                    "session_id": handle,
                    "error": str(exc)[:200],
                    "note": "표는 좀비로 거둔다",
                },
            )
    # 🔴 좀비 진입 지정가 (2026-08-25 ADA 고아 사건): 주인 없는 진입 지정가는 채워지기 전에
    #    거둔다. 포지션이 있어도 거둔다 (부활은 보호만 걸기 때문). 되살린 표는 `keep` 으로 뺀다.
    swept += await sweep_zombie_entries(orders, instrument, why="판 시작", keep=kept_ids)
    runner.swept = [f"{item.kind} {item.at}" for item in swept]

    entry = Live(session=session, flags=chosen, applied=applied, seed=None, _full={})
    entry.runner = asyncio.create_task(runner.run(), name=f"live-{handle}")
    SESSIONS[handle] = entry
    LIVE_RUNNERS[handle] = runner
    _logger.info(
        "live_session_started",
        payload={
            "session_id": handle,
            "symbol": symbol,
            "market": market.value,
            "playbook": set_attribution,
            "seed_cash": str(session.ledger.seed_cash),
            "leverage": str(session.ledger.leverage),
        },
    )
    body = _state(handle, None, book.timeframe)
    body["session_id"] = handle
    body["live"] = True
    return body


@router.get("/live/{key}")
async def live_health(key: str) -> dict[str, Any]:
    """라이브 세션의 **건강 상태** — 화면이 이상을 알아볼 수 있게.

    Args:
        key: 세션 id.

    Returns:
        `{steps, orders, gaps, backfilled, reconnects, pending}`.

    Raises:
        HTTPException: 라이브 세션이 아니면 404.

    Note:
        🔴 **`steps` 가 0 인데 봉이 늘고 있으면 판정이 안 도는 것이다.** 실제로 그 버그를
        겪었고(전진 축 불일치), 증상은 이 숫자뿐이었다 — 예외도 로그도 안 났다.

        ⚠️ `reconnects` 가 0 이 아니면 그 사이 봉에 구멍이 있을 수 있다. `gaps` 가 0 이면
        메워진 것이다.
    """
    runner = LIVE_RUNNERS.get(key)
    if runner is None:
        raise HTTPException(404, f"{key} 는 라이브 세션이 아니다 — 목록에 있어도 백테스트다")
    live = _live(key)
    held = runner.pending_bar()
    # 🔴 **계좌를 함께 낸다** (사용자 요구 2026-08-18: *"내 계좌 잔액도 확인할 수 있어야
    #    하고, 뭐 현재 손익이나 그런것도"*). 원장은 모형이고 계좌는 사실이라, 나란히
    #    두지 않으면 갈리는 것을 전략 성과로 오해한다 (§1-0s 관측 규약).
    #
    # ⚠️ 못 읽어도 화면을 죽이지 않는다 — 건강 상태(걸음·구멍)는 계좌와 무관하게 보여야
    #    하고, 그것이 "왜 아무 일이 없나" 에 답하는 값이다.
    if runner.observe_only:
        # ⛔ **관찰 전용은 계좌를 묻지 않는다** (2026-08-20 · D1). 계약이 없는 종목
        #    (SNDK·SKHY)은 조회마다 400 이 오고, 화면이 판마다 4초 간격으로 폴링하니
        #    밤사이 `live_account_unreadable` 이 **3979번** 쌓였다.
        #
        #    ⚠️ 로그가 시끄러운 것만 문제가 아니다 — 진짜 조회 실패가 그 안에 묻힌다.
        #
        # ⭐ 값을 지어내지 않는다. *"안 물어봤다"* 를 그대로 말한다 (절대 규칙 #8).
        account = {"note": "관찰 전용 — 거래소에 계약이 없어 계좌를 묻지 않는다"}
    else:
        try:
            account = await runner.exchange()
        except Exception as exc:  # 표시용이라 어떤 실패도 화면을 막지 않는다
            _logger.warning(
                "live_account_unreadable", payload={"session_id": key, "error": str(exc)}
            )
            account = {"error": str(exc)[:200]}
    session = live.session
    return {
        "exchange": account,
        # 🔴 **진입하려다 못 한 것들** (2026-08-20). 세션은 이 값들을 세고 있었는데
        #    화면에 안 실려서, *"자리가 없었다"* 와 *"자리는 있었는데 못 갔다"* 를
        #    구별할 방법이 없었다 — 사용자가 정확히 그것을 물었다.
        #
        # ⚠️ **셋이 서로 다른 사건이다.** 뭉치면 원인을 못 찾는다:
        #      stale_plans    계획 기하가 안 맞아 주문조차 안 냈다
        #      expired        지정가를 걸었는데 안 채워진 채 계획이 사라졌다
        #      half_withheld  전환 신호가 났지만 익이 안 나 안 덜었다 (후보 B)
        "misses": {
            "stale_plans": session.stale_plans,
            "expired": session.expired,
            "waiting": session.waiting,
            "half_withheld": session.half_withheld,
            # 🔴 **태어날 때 죽는 매매를 센다** (§1-0s · 사용자 확정 2026-08-20).
            #    이 값이 크면 성적표의 승률·RR 을 그대로 읽으면 안 된다 — 실측에서
            #    14초짜리 매매 하나가 RR 66 으로 잡혀 필요 승률을 1.5% 로 보이게 했다.
            "thin_legs": session.thin_legs,
            "born_dead": session.born_dead,
            # 🔴 **겹쳐 들어와 건너뛴 걸음** (사용자 신고 2026-08-21). 겹치면 같은
            #    사다리가 두 번 나가고 절반이 유령이 됐다 — 이제 막지만, 급증하면
            #    걸음이 너무 오래 걸린다는 뜻이라 사람이 봐야 한다.
            "overlaps": runner.overlaps,
        },
        # 🔴 **못 지키는 동안은 새로 안 산다** (A3). 화면이 이 상태를 말해야 사람이
        #    "왜 안 사지" 를 고장으로 안 읽는다 (절대 규칙 #8).
        "guarded": session.guarded,
        # 🔴 **나갈 호가가 있는 동안만 새로 산다** (사용자 제안 2026-08-20). `guarded` 와
        #    따로 낸다 — 새 진입이 멎은 이유가 손절이냐 호가냐가 화면에서 갈려야 한다.
        "liquid": session.liquid,
        "dry": runner.dry,
        # 🔴 **예산 합이 계좌 안에 드나** (사용자 신고 2026-08-21). 셋을 따로 낸다 —
        #    새 진입이 멎은 이유가 손절이냐 호가냐 돈이냐가 화면에서 갈려야 한다.
        "funded": session.funded,
        "short_by": runner.short_by,
        # 🔴 **시작할 때 치운 잔재** (사용자 요구 2026-08-21). 묻지 않고 거두기로 했으므로
        #    무엇을 거뒀는지는 반드시 보여야 한다 — 안 그러면 조용한 실패다 (규칙 #8).
        "swept": runner.swept,
        # 🔴 **매매별 거래소 흔적.** 원장이 보유중인데 여기 없으면 유령 포지션이다 —
        #    실제로 그 상태를 겪었다 (거래소 포지션 0 인데 화면은 보유중).
        "placed": runner.placed,
        # 🔴 **축마다 마지막 봉이 몇 초 됐는지** (사용자 요구 2026-08-18: *"사용자가
        #    갱신이 되고 있는지 알 수 있으면"*). 얼어 있는 축과 **원래 느린 축**은
        #    화면상 구별되지 않는다 — 1d 가 6시간째 그대로면 정상, 10s 가 6분째
        #    그대로면 고장이다. 화면이 간격 대비 배수로 읽어 가른다.
        "frame_ages": await runner.frame_ages(),
        # 🔴 **실패를 화면이 볼 수 있게** (사용자 요구 2026-08-18: *"주문 실패 시 이걸
        #    사용자가 알 수 있어야 해"*). 로그에만 두면 아무도 안 본다 — 밤새 익절이
        #    세 번 거절됐는데 아침에야 알았다.
        "failures": runner.failures,
        "last_error": runner.last_error,
        # 🔴 **스스로 점검한 결과** — 비어 있는 것이 정상이다. 하나라도 있으면 판정을
        #    믿으면 안 된다 (사용자 요구 2026-08-18: *"일정 시간마다 검증한다던가"*).
        "findings": runner.findings,
        # 🔴 **자동 점검 결과** (사용자 요구 2026-08-18: *"알아서 점검되고 했으면"*).
        #    누르기 전에는 모르는 상태가 없어진다 — 버튼은 *"지금 당장"* 용으로 남는다.
        "probe": runner.last_probe,
        "session_id": key,
        # 🔴 **판정 축을 화면에 말해 준다** (사용자 지적 2026-08-18: *"15분 기준으로
        #    매매로직이 도는 걸로 알고 있거든"* — 맞다).
        #
        #    화면은 **차트에서 고른 축**으로 "언제 판정하나"를 적고 있었다. 10초봉을
        #    보는 중이면 *"첫 10초 봉이 마감되면 돈다"* 라고 썼는데, 판정은 플레이북의
        #    진입 축(15m)에서만 돈다 — 90배 틀린 말이고, 그래서 12분 된 판이
        #    **고장난 것처럼** 보였다.
        #
        # ⚠️ **차트 축과 판정 축은 다른 것이다.** 차트 축은 보는 눈금이고 판정 축은
        #    플레이북이 정한다 (`playbook.timeframe`). 화면이 이 둘을 같은 값으로
        #    쓰는 순간 거짓말이 된다.
        "entry": runner.entry.value,
        # ⭐ **다음 판정 시각.** 커서는 *지금 자라는 봉의 시작*이므로 그 봉이 마감되는
        #    시각이 다음 판정이다. 화면이 이것을 알아야 "판정 0" 이 **고장인지 대기인지**
        #    가른다 — 숫자 하나로는 그 둘이 똑같이 보인다 (절대 규칙 #8).
        "next_judge_at": (live.session.cursor + interval(runner.entry)).isoformat(),
        # 🔴 **이 판이 새 판인가 이어받은 판인가** (T16 ②). 화면이 이것을 모르면
        #    `steps` 가 0 인데 매매가 12건인 상태를 **고장으로 읽는다** — 실제로는
        #    "옛 판을 이어받았고 아직 새 봉을 안 봤다" 이며 완전히 정상이다.
        "run": _run_info(key, live, runner),
        # 🔴 **다른 판이 지갑을 쓰고 있다** (다중 RUN ㄷ). 선착순은 규칙이지만,
        #    말하지 않으면 사람은 "왜 주문이 작아졌지" 를 전략 문제로 오해한다.
        "squeezed": runner.squeezed,
        # 🔴 **안전장치가 실제로 돈 적이 있는가** (11번). 안 도는 방어선은 없는 것과
        #    같고, 코드와 테스트만으로는 그것을 알 수 없다.
        #
        # ⚠️ 0 인 것이 나쁘다는 뜻은 아니다 — 발동할 일이 없었다는 뜻일 수도 있다.
        #    구별하는 것은 사람이며 이 표는 그 판단의 재료다 (§1-0s).
        "guards": runner.guards,
        "steps": runner.steps,
        "orders": runner.orders,
        "gaps": runner.gaps,
        "backfilled": runner.backfilled,
        "reconnects": runner.reconnects,
        "pending_bar": None if held is None else held.ts.isoformat(),
        "running": live.running,
        "cursor": live.session.cursor.isoformat(),
    }


@router.post("/live/{key}/auto")
async def set_auto(key: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """도는 RUN 의 **새 진입을 멈추거나 다시 켠다** (사용자 요구 2026-08-20).

    Args:
        key: RUN id.
        payload: `{on}`. 거짓이면 새 진입을 안 받는다.

    Returns:
        `{session_id, auto}`.

    Raises:
        HTTPException: 그 판이 없으면 404.

    Note:
        🔴 **`paused` 를 쓰지 않는다.** 저쪽은 걸음(`Session.step`) 자체를 멈추는데,
        걸음이 멈추면 그 뒤에 딸린 것이 전부 멈춘다 — 손절 재장착(`_guard_stop`) ·
        거래소 대조(`reconcile`) · 반익 집행(`_apply_half`) · 못 건 익절 재시도.

        ⇒ 포지션을 든 채로 `paused` 를 걸면 **아무도 지키지 않는 포지션**이 된다.
          Gate 조건부는 24시간에 조용히 만료되므로 그 뒤로는 손절도 없다.

        ⭐ `auto` 는 **새 진입만** 막는다 (`Session.step` 의 `if self.auto and idle`).
          이미 든 것은 계속 관리된다 — 증거금이 바닥났을 때 러너가 스스로 하는 것과
          **같은 조치**다 (`live_margin_exhausted`: *"새 진입을 멈춘다 — 보유 포지션
          관리는 계속한다"*). 리스크를 줄이는 행동은 막지 않는다 (spec §1.2.1).

        ⚠️ **포지션을 닫지 않는다.** 닫는 것은 삭제(`DELETE /sessions/{key}`)의 일이고,
        둘을 한 단추에 묶으면 *"잠깐 멈춤"* 이 되돌릴 수 없는 행동이 된다.
    """
    live = SESSIONS.get(key)
    if live is None:
        raise HTTPException(404, f"{key} 는 없는 판이다")
    wanted = bool(payload.get("on", True))
    live.session.auto = wanted
    _logger.info(
        "live_auto_set",
        payload={
            "session_id": key,
            "auto": wanted,
            "note": "새 진입만 여닫는다 — 보유 포지션 관리는 계속한다",
        },
    )
    return {"session_id": key, "auto": wanted}


@router.post("/live/{key}/leverage")
async def set_leverage(key: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """도는 RUN 의 **레버리지를 바꾼다** (사용자 요구 2026-08-19).

    Args:
        key: RUN id.
        payload: `{leverage}`.

    Returns:
        `{leverage, note}`.

    Raises:
        HTTPException: RUN 이 없으면 404 · 값이 범위 밖이면 400 · 보유 중이면 409.

    Note:
        🔴 **보유 중에는 못 바꾼다.** 원장은 `leverage` 를 손익률에 그대로 곱하는데,
        열린 매매의 배율을 도중에 바꾸면 **이미 지나간 구간의 손익까지 새 배율로**
        계산된다 — 그 매매의 성적이 통째로 거짓이 된다.

        ⇒ 포지션이 닫힌 뒤에 바꾼다. 그러면 다음 매매부터 새 배율로 간다.

        ⚠️ **거래소 쪽 배율도 같이 바꾼다.** 원장만 바꾸면 우리가 계산한 계약수와
        거래소가 잡는 증거금이 어긋나고, 그 어긋남은 `INSUFFICIENT_AVAILABLE` 로만
        나타난다 (절대 규칙 #8).

        ⛔ **청산 위험이 커지는 방향이다.** 배율을 올리면 청산가가 진입에 가까워진다 —
        이것은 리스크를 늘리는 행동이므로 조용히 하지 않고 로그에 남긴다.
    """
    live = SESSIONS.get(key)
    runner = LIVE_RUNNERS.get(key)
    if live is None or runner is None:
        raise HTTPException(404, f"{key} 는 도는 RUN 이 아니다 — 레버리지를 바꿀 대상이 없다")
    try:
        wanted = Decimal(str(payload.get("leverage", "")))
    except (ArithmeticError, ValueError) as exc:
        raise HTTPException(
            400, f"레버리지를 숫자로 못 읽었다: {payload.get('leverage')!r}"
        ) from exc
    if wanted < LEVERAGE_MIN or wanted > LEVERAGE_MAX:
        raise HTTPException(
            400, f"레버리지는 {LEVERAGE_MIN}~{LEVERAGE_MAX} 사이여야 한다 (받은 값 {wanted})"
        )
    held = live.session.position
    if held is not None:
        raise HTTPException(
            409,
            f"{key} 가 보유 중이다 ({held.trade_id[:6]}) — 열린 매매의 배율을 바꾸면 "
            "이미 지나간 구간의 손익까지 새 배율로 계산돼 그 매매의 성적이 거짓이 된다. "
            "포지션이 닫힌 뒤에 바꾼다",
        )
    before = live.session.ledger.leverage
    # ⚠️ 거래소 쪽을 **먼저** 바꾼다. 원장만 바뀐 채로 거래소가 옛 배율이면 우리가 낸
    #    계약수가 거부되고, 그 실패는 화면에서 "주문 0건" 으로만 보인다.
    try:
        await runner.set_leverage(wanted)
    except Exception as exc:
        raise HTTPException(502, f"거래소가 레버리지를 안 받았다: {exc}") from exc
    live.session.ledger.leverage = wanted
    _logger.warning(
        "live_leverage_changed",
        payload={
            "session_id": key,
            "from": str(before),
            "to": str(wanted),
            "note": "청산가가 진입에 가까워진다 — 리스크를 늘리는 방향이다",
        },
    )
    return {
        "leverage": str(wanted),
        "note": "다음 매매부터 새 배율이다 — 이미 끝난 매매의 손익은 그때 배율 그대로다",
    }


@router.post("/adopt")
async def adopt_orphan(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """고아 포지션을 **살아 있는 판이 다시 이어받게** 한다 (사용자 요구 2026-09-01).

    Args:
        payload: `{market, symbol}` — 대조 배너가 가리키는 거래소·종목.

    Returns:
        `{adopted, reason, findings}`. `adopted` 가 `"yes"` 면 되받았고, 뒤이어 다시
        돌린 대조 결과를 실어 화면이 바로 갱신된다.

    Raises:
        HTTPException: 그 종목을 맡은 **살아 있는 판이 없으면** 404 — 그때는 고아가
            아니라 임자 없는 잔재이므로 `/leftovers` 청소나 `/exchange/close` 가 답이다.

    Note:
        🔴 **왜 필요한가**: 매매법을 바꾸려고 판을 지우고 새로 만들면 거래소 포지션이
        남는다. 자동 이어받기(`adopt`)는 기동 때 한 번만 도므로, 그 창을 놓치면
        아무도 관리하지 않는 고아가 된다. 매번 판을 지웠다 만들지 않고 이 창구로
        되받는다 — 무중단 전환(`PUT /rebalancer/{id}/playbook`)이 쓰는 것과 같은 길이다.

        ⛔ **닫지 않는다.** 이어받기는 포지션을 원장에 다시 매다는 일이라 손익을
        확정하지 않는다 — 청산(`/exchange/close`)과 **다른 단추**다 (절대 규칙 #4).

        ⚠️ **손절이 거래소에 남아 있어야 이어받는다.** 못 찾으면 계획을 지어내지 않고
        `reason` 으로 사람에게 넘긴다 (규칙 #4·#8).
    """
    market = str(payload.get("market", "")).strip()
    symbol = str(payload.get("symbol", "")).strip()
    if not market or not symbol:
        raise HTTPException(400, "market 과 symbol 이 둘 다 필요하다")
    hit = next(
        (
            (handle, runner)
            for handle, runner in LIVE_RUNNERS.items()
            if runner.instrument.market.value == market and runner.instrument.symbol == symbol
        ),
        None,
    )
    if hit is None:
        raise HTTPException(
            404,
            f"{market}:{symbol} 를 맡은 살아 있는 판이 없다 — 고아가 아니라 임자 없는 "
            "잔재다. 잔재 청소나 전량 청산으로 정리한다",
        )
    handle, runner = hit
    outcome = await runner.manual_adopt()
    _logger.warning(
        "console_adopt_orphan",
        payload={"session_id": handle, "market": market, "symbol": symbol, **outcome},
    )
    # 🔴 되받은 직후 다시 대조해 화면이 바로 갱신되게 한다 — 30초 주기를 안 기다린다.
    found = await reconcile_once()
    return {
        "adopted": outcome["adopted"],
        "reason": outcome["reason"],
        "findings": [
            {"code": item.code, "market": item.market, "symbol": item.symbol} for item in found
        ],
    }


@router.get("/live/{key}/tick")
async def live_tick(key: str, frame: str | None = None) -> dict[str, Any]:
    """**지금 만들어지고 있는 봉** 하나 — 꼬리가 실시간으로 흔들리게.

    Args:
        key: 세션 id.
        frame: 보고 있는 시간축. 안 주면 진입 축.

    Returns:
        `{frame, ts, open, high, low, close, volume, at}`. 아직 없으면 `bar: null`.

    Raises:
        HTTPException: 라이브 세션이 아니면 404.

    Note:
        🔴 사용자 요구 2026-08-18: *"주가창에서 꼬리 위아래로 왔다갔다 하는 거, 10초
        축으로 실제 호가를 보고 싶다."* `/state` 는 **마감된 봉까지만** 준다.

        🔴 **응답이 작아야 한다.** `/state` 는 봉 800개 + 도형을 싣고 오므로 초 단위로
        칠 수 없다 — 이 응답은 봉 **하나**다. 그래서 화면이 이것만 빠르게 당긴다.

        ⛔ **판정에 쓰이지 않는다.** 러너가 급전에 넣지 않고 값만 돌려준다
        (`LiveRunner.forming`) — 미마감 봉이 판정에 들어가면 같은 상황에서 매 틱 다른
        답이 난다 (절대 규칙 #5).
    """
    runner = LIVE_RUNNERS.get(key)
    if runner is None:
        raise HTTPException(404, f"{key} 는 라이브 세션이 아니다")
    only = _only(key, frame)
    bar = await runner.forming(only)
    return {
        "frame": only.value,
        "at": datetime.now(UTC).isoformat(),
        # ⚠️ 없으면 `null` 이다 — 0 이나 마지막 마감 봉으로 채우지 않는다. 못 받은 것과
        #    "안 움직였다" 는 완전히 다른 사실이고, 화면이 그것을 갈라야 한다.
        "bar": None if bar is None else candle_json(bar),
    }


WATCH_EVERY = 60.0
"""감시 주기(초).

⚠️ **짧게 둘 이유가 없다.** 판이 죽은 것은 1분 안에 알면 충분하고, 짧으면 거래소를
그만큼 더 두드린다. 급한 것(손절)은 러너가 걸음마다 본다.
"""

WATCH_STALL = 300.0
"""걸음이 이만큼 멈춰 있으면 **얼어붙은 것으로 본다** (초).

⚠️ 15분봉 판정은 원래 드문드문 돈다. 그래도 방아쇠 축이 돌고 봉이 들어오면 걸음은
계속 늘어난다 — 5분째 그대로면 그것은 정상이 아니다.
"""

WATCHED: dict[str, dict[str, str]] = {}
"""감시자가 마지막으로 본 것 — `{판 id: {code, detail, at}}`.

🔴 **화면이 읽는다.** 감시자가 로그에만 적으면 2026-08-19 를 그대로 반복한다 —
판 2개가 죽었는데 화면에는 목록에서 사라진 것으로만 보였다.
"""

_STEPS_AT: dict[str, tuple[int, float]] = {}
"""판별 마지막 걸음 수와 그때의 벽시계 — 얼어붙음을 재는 기준."""

REVIVE_LIMIT = 5
"""자동 부활을 **포기하는** 횟수 (T20 ④-b).

🔴 **무한 재시도는 감시자가 아니라 공격이다.** 증거금이 모자라 못 뜨는 판을 계속
띄우려 들면 거래소를 두드리기만 하고, 로그가 같은 줄로 가득 차 진짜 신호가 묻힌다.

⛔ 포기한 뒤에는 **경보만 남긴다.** 그 상태는 사람이 봐야 한다 (절대 규칙 #8).
"""

REVIVE_BACKOFF = 60.0
"""부활 재시도 간격의 밑값(초). 실패할수록 두 배로 벌린다.

⚠️ 60 → 120 → 240 → 480 → 960. 다섯 번이면 약 30분이고, 그 안에 안 되면 원인이
일시적인 것이 아니다.
"""

_REVIVE: dict[str, tuple[int, float]] = {}
"""판별 `(실패 횟수, 다음 시도 시각)`."""


async def _clear_orphans() -> None:
    """거래소에서 사라진 고아 포지션의 경보를 **거둔다** (2026-08-20).

    Note:
        🔴 **안 거두면 경보가 영원히 붙는다.** 사람이 거래소에서 직접 닫거나 손절이
        발동해 없어져도 배너가 남고, 그러면 다음에 진짜가 떴을 때 안 믿는다 —
        *"늘 붉으면 아무도 안 본다"* 와 같은 병이다.

        ⛔ **못 읽으면 그대로 둔다.** 조회 실패를 "없어졌다" 로 읽으면 남아 있는
        포지션의 경보를 지우게 되고, 그것이 이 함수가 막으려던 바로 그 일이다.
    """
    if not ORPHANS:
        return
    # ⭐ **한 번만 묻는다.** 콘솔 상태가 이미 전 종목 포지션을 낸다 — 고아마다 따로
    #   물으면 종목 수만큼 왕복이 곱해진다.
    from updown.apps.api.exchange import state as console_state

    try:
        body = await console_state()
    except Exception:
        return
    alive = {str(row.get("symbol", "")) for row in body.get("positions", [])}
    for symbol in list(ORPHANS):
        if symbol in alive:
            continue
        ORPHANS.pop(symbol, None)
        _logger.info(
            "live_orphan_cleared",
            payload={"symbol": symbol, "note": "거래소에 포지션이 없다 — 경보를 거둔다"},
        )


async def watch_runs() -> list[dict[str, str]]:
    """열린 판이 **실제로 돌고 있는지** 밖에서 본다 (T20 ③).

    Returns:
        이상 목록. 없으면 빈 목록.

    Note:
        🔴 **감시자를 감시 대상 안에 두지 않는다.** 러너 안에도 자가 점검이 있지만
        그것은 **러너가 걸음을 돌 때만** 돈다 — 2026-08-19 에 죽은 두 판은 자기가
        죽었다는 것을 말할 수 없었고, 화면에는 목록에서 사라진 것으로만 보였다.

        🔴 **기준은 메모리가 아니라 DB 의 열린 판이다.** 메모리를 기준으로 하면
        *사라진 판*은 목록에도 없어서 영원히 안 보인다. 이번에 정확히 그랬다.

        네 가지 모양을 가른다 (뭉개면 원인을 못 찾는다):

        ```
        no_runner    열린 판인데 러너가 없다        ← 되살리기가 실패했다
        task_dead    태스크가 끝났는데 판은 열려 있다  ← 예외로 죽었다
        steps_stall  봉은 오는데 걸음이 안 는다       ← 얼어붙었다
        feed_stall   봉이 안 들어온다               ← 구독이 끊겼다
        ```

        ⛔ **이 단계는 되살리지 않는다** (사용자 확정 2026-08-19). 감지가 맞는지 며칠
        보고 나서 자동 행동을 붙인다 — **틀린 감지가 자동으로 판을 재시작하면 그게 더
        나쁘다.** 지금은 보고 알리는 것까지가 일이다.
    """
    # 🔴 **고아 경보를 스스로 거둔다** (2026-08-20). 사람이 거래소에서 닫았거나 손절이
    #    발동해 사라졌는데도 배너가 남아 있으면, 다음에 진짜가 떴을 때 안 믿는다 —
    #    "늘 붉으면 아무도 안 본다" 와 같은 병이다.
    await _clear_orphans()
    if _store is None:
        return []
    try:
        rows = await _store.open_runs(live=True)
    except Exception as exc:
        _logger.error("watch_unreadable", payload={"error": str(exc)[:200]})
        return []

    now = time.monotonic()
    found: list[dict[str, str]] = []
    for row in rows:
        key = str(row["key"])
        symbol = str(row.get("symbol", ""))
        runner = LIVE_RUNNERS.get(key)
        entry = SESSIONS.get(key)
        note: dict[str, str] | None = None
        if runner is None:
            note = {
                "code": "no_runner",
                "detail": f"{symbol} 판이 열려 있는데 러너가 없다 — 되살리기가 실패했다",
            }
        elif entry is not None and entry.runner is not None and entry.runner.done():
            # ⚠️ 예외를 그대로 싣는다. "죽었다" 만으로는 다음에 또 죽는다.
            why = ""
            with contextlib.suppress(Exception):
                exc = entry.runner.exception()
                why = f" — {type(exc).__name__}: {exc}"[:160] if exc else " — 조용히 끝났다"
            note = {"code": "task_dead", "detail": f"{symbol} 러너 태스크가 끝났다{why}"}
        else:
            was = _STEPS_AT.get(key)
            # 🔴 **문턱을 그 판의 축에서 만든다** (사용자 신고 2026-08-21).
            #
            #    상수 300초는 *"방아쇠 축이 돈다"* 를 전제한다. 그런데 방아쇠가 없는
            #    룰이 있다 — 봉 마감 판정형 플레이북은 **판정 봉이 마감돼야**
            #    한 걸음 간다. 그 판에서 300초는 정상 구간이고, 그래서 멀쩡한 판 둘이
            #    *"걸음이 541초째 11 에서 그대로다"* 로 붉게 떴다.
            #
            # ⛔ **문턱을 그냥 늘리지 않는다** — 그러면 빠른 축의 진짜 정지를 늦게 잡는다.
            #    축에서 유도해야 둘 다 맞는다.
            #
            # ⚠️ 거짓 경보 하나가 **다른 모든 경보를 죽인다.** 그 문구는 *"거래소에
            #    포지션이 남아 있으면 지금 아무도 관리하지 않는다"* 라고 겁을 주는데,
            #    늘 떠 있으면 진짜일 때 아무도 안 본다.
            # ⭐ 방아쇠 축이 있으면 그것이 걸음의 박자다. 없으면 판정 축이 박자다.
            beat = runner.beat
            # ⭐ 두 봉을 통째로 넘겨야 정지다 — 한 봉은 경계에서 늘 걸린다.
            limit = max(WATCH_STALL, interval_seconds(beat) * 2)
            if was is None or was[0] != runner.steps:
                _STEPS_AT[key] = (runner.steps, now)
            elif now - was[1] > limit:
                stuck = int(now - was[1])
                note = {
                    "code": "steps_stall",
                    "detail": (
                        f"{symbol} 걸음이 {stuck}초째 {runner.steps} 에서 그대로다 "
                        f"({beat.value} 축 기준 {int(limit)}초를 넘겼다)"
                    ),
                }
        if note is None:
            # ⭐ 다시 살아났으면 부활 기록도 지운다 — 안 지우면 다음 사고에서
            #   상한에 이미 닿아 있는 채로 시작한다.
            WATCHED.pop(key, None)
            _REVIVE.pop(key, None)
            continue
        note["run"] = key
        note["at"] = datetime.now(UTC).isoformat()
        # 🔴 **포지션이 있으면 등급이 다르다.** 관리자 없는 레버리지 포지션은
        #    "판이 하나 안 돈다" 와 전혀 다른 사건이다.
        note |= await _guard_orphan(key, str(row.get("market", "")), symbol)
        # ⭐ **손절을 확인한 다음에 되살린다** (순서가 중요하다 — §1).
        note |= await _revive(key, row, note["code"])
        WATCHED[key] = note
        found.append(note)
        _logger.error("run_watch_alarm", payload=dict(note))
    return found


def _plan_fits_position(plan: dict[str, str], ex_entry: Decimal, *, long: bool) -> str:
    """복구한 계획이 **이 거래소 포지션의 것인지** 대조한다 (빈 문자열 = 일치).

    Args:
        plan: `plan_by_symbol`/`plan_of` 가 낸 `{stop, entry, ...}`.
        ex_entry: 거래소 포지션의 진입가.
        long: 포지션이 롱인가 (`size > 0`).

    Returns:
        어긋난 이유. 없으면 빈 문자열.

    Note:
        🔴 **엉뚱한 손절을 붙이지 않는다.** 종목이 같아도 진입가·방향이 다르면 다른
        매매다 (재생성 전후로 두 매매가 겹칠 수 있다). 진입가가 0.5% 넘게 어긋나거나
        손절 방향이 포지션과 안 맞으면 걸지 않고 사람에게 넘긴다 (절대 규칙 #4·#8).
    """
    try:
        p_entry = Decimal(plan.get("entry", "0") or "0")
        p_stop = Decimal(plan["stop"])
    except (KeyError, ArithmeticError, TypeError):
        return "계획 값을 못 읽었다"
    if ex_entry <= 0 or p_entry <= 0:
        return "진입가를 못 읽었다"
    if abs(ex_entry - p_entry) / ex_entry > Decimal("0.005"):
        return f"진입가 불일치 (DB {p_entry} · 거래소 {ex_entry})"
    if (long and p_stop >= p_entry) or (not long and p_stop <= p_entry):
        side = "롱" if long else "숏"
        return f"손절 방향이 {side} 포지션과 안 맞는다 (손절 {p_stop} · 진입 {p_entry})"
    return ""


async def _guard_orphan(key: str, market: str, symbol: str) -> dict[str, str]:
    """관리자 없는 포지션에 **손절이 걸려 있는지** 보고, 없으면 건다 (T20 ④).

    Args:
        key: 판 id.
        market: 시장 코드.
        symbol: 종목.

    Returns:
        경보에 얹을 값들. 포지션이 없으면 비어 있다.

    Note:
        🔴 **부활보다 이것이 먼저다.** 판이 죽어도 거래소 조건부 손절이 남아 있으면
        치명적이지 않다 — 그것이 브로커측 스탑을 쓰는 이유 자체다 (spec §7 · §12.6).
        그래서 감시자가 가장 먼저 답할 질문은 *"살릴 수 있나"* 가 아니라
        **"지금 무방비인가"** 다.

        🔴 **Gate 조건부는 24시간에 조용히 만료된다.** 판이 하루 넘게 죽어 있으면
        손절만 사라지고 포지션은 남는다 — 그래서 죽은 판도 **계속** 확인한다.

        ⚠️ **손절 가격을 지어내지 않는다.** 원장(DB)이 든 `planned_stop` 을 쓰고,
        그것을 못 읽으면 **걸지 않고 그 사실을 말한다** — 손절의 SSoT 는 RiskManager
        이고 감시자는 옮기기만 한다 (절대 규칙 #4).

        ⛔ **되살리지 않는다.** 이 단계는 감지와 **리스크 감소 행동**까지다. 자동
        재시작은 감지가 맞다는 것을 며칠 본 뒤에 붙인다 (사용자 확정 2026-08-19).
    """
    if _store is None or not market:
        return {}
    try:
        instrument = instrument_of(symbol, Market(market))
        orders = order_adapter(MarketDataProvider().adapter_for(Market(market)), user_id="guard")
    except Exception as exc:
        return {"guard": f"어댑터를 얻지 못했다 — {str(exc)[:80]}"}
    try:
        position = cast(
            "dict[str, str]",
            await orders.position_snapshot(instrument),  # type: ignore[attr-defined]
        )
        size = int(Decimal(str(position.get("size", "0")))) if position else 0
    except Exception as exc:
        return {"guard": f"포지션을 읽지 못했다 — {str(exc)[:80]}"}
    if size == 0:
        # ⭐ 포지션이 없으면 판이 죽어도 잃을 것이 없다. 등급이 다르다.
        return {"position": "0", "guard": "포지션 없음 — 급하지 않다"}
    try:
        stops = cast(
            "list[dict[str, str]]",
            await orders.open_stops(instrument),  # type: ignore[attr-defined]
        )
    except Exception as exc:
        return {"position": str(size), "guard": f"조건부를 읽지 못했다 — {str(exc)[:80]}"}
    if stops:
        return {"position": str(size), "guard": f"손절 {len(stops)}건 걸려 있다 — 지켜진다"}

    plan = None
    with contextlib.suppress(Exception):
        plan = await _store.plan_of(key)
    if plan is None:
        # 🔴 **판 key 로 못 찾으면 종목으로 찾는다** (재시작·매매법/비중 변경·재생성으로
        #    key 가 바뀐 고아). RiskManager 가 DB 에 영속한 손절을 되읽어 다시 건다 —
        #    지어내는 것이 아니다. 이것이 없으면 스탑이 사라진 고아를 영영 못 인수한다.
        with contextlib.suppress(Exception):
            plan = await _store.plan_by_symbol(market, symbol)
    if plan is None:
        # ⛔ 어디에도 계획이 없다 — 지어내지 않는다. 닫거나 손으로 건다.
        return {
            "position": str(size),
            "guard": "🔴 손절도 계획도 없다 — 콘솔에서 닫거나 손으로 건다",
        }
    # 🔴 **대조 없이 걸지 않는다.** 진입가·방향이 어긋나면 다른 매매의 계획이다.
    ex_entry = Decimal(str(position.get("entry_price", "0") or "0"))
    bad = _plan_fits_position(plan, ex_entry, long=size > 0)
    if bad:
        return {
            "position": str(size),
            "guard": f"🔴 DB 계획이 이 포지션과 다르다 ({bad}) — 손으로 확인한다",
        }
    try:
        await orders.stops_for(  # type: ignore[attr-defined]
            instrument, Decimal(plan["stop"]), long=size > 0
        )
    except Exception as exc:
        return {
            "position": str(size),
            "guard": f"🔴 손절을 거는 데 실패했다 — {str(exc)[:100]}",
        }
    _logger.error(
        "orphan_stop_placed",
        payload={
            "run": key,
            "symbol": symbol,
            "size": str(size),
            "stop": plan["stop"],
            "note": "판은 죽었지만 포지션은 이제 손절이 지킨다",
        },
    )
    return {
        "position": str(size),
        "guard": f"손절이 없어서 {plan['stop']} 에 다시 걸었다 — 판은 여전히 죽어 있다",
    }


async def _revive(key: str, row: dict[str, Any], code: str) -> dict[str, str]:
    """죽은 판을 **다시 붙인다** (T20 ④-b).

    Args:
        key: 판 id.
        row: `open_runs` 가 낸 행 — 매매법·종목·배율·증거금이 여기 있다.
        code: 감시자가 매긴 죽음의 모양.

    Returns:
        경보에 얹을 값들.

    Note:
        🔴 **`adopt` 경로를 탄다.** 러너는 기동할 때 거래소 포지션을 읽어 원장으로
        되읽는다 — 새 원장으로 시작하면 이미 있는 포지션을 모르고 **또 산다.**

        🔴 **되살린 것을 조용히 넘어가지 않는다.** *"판이 잘 돌고 있다"* 와 *"두 번
        죽었다가 되살아났다"* 는 전혀 다른 상태이고, 후자는 원인이 남아 있다는 뜻이다.
        판 화면의 안전장치 목록에 남긴다.

        ⚠️ **얼어붙음(`steps_stall`)에는 손대지 않는다.** 러너는 살아 있는데 걸음이
        안 도는 것이라, 새로 띄우면 **같은 종목에 판이 둘**이 된다 (Gate 는 계약당
        포지션이 하나라 서로의 포지션을 자기 것으로 여긴다). 그 경우는 사람이 본다.

        ⛔ **무한 재시도 금지.** 실패할수록 간격을 두 배로 벌리고, `REVIVE_LIMIT` 번
        실패하면 멈추고 소리친다 — 못 뜨는 이유가 일시적이지 않다는 뜻이다.
    """
    if code not in ("no_runner", "task_dead"):
        return {}
    tries, ready = _REVIVE.get(key, (0, 0.0))
    if tries >= REVIVE_LIMIT:
        return {"revive": f"🔴 {tries}회 실패해 자동 부활을 멈췄다 — 콘솔에서 직접 띄운다"}
    now = time.monotonic()
    if now < ready:
        return {"revive": f"{int(ready - now)}초 뒤 다시 시도한다 ({tries}/{REVIVE_LIMIT})"}
    payload: dict[str, Any] = {
        # 🔴 **키를 물려준다** (2026-08-27): 안 주면 새 uuid 로 떠서 자기 DB 닻과
        #    409 로 충돌하고(같은 종목·다른 키), 판의 정체성(성적·차트)도 끊긴다.
        "session_id": key,
        "playbook": row["playbook_id"],
        "symbol": row["symbol"],
        "market": row["market"],
        "leverage": row["leverage"],
        # ⚠️ 증거금이 비어 있으면 그 판은 지갑 전액을 쓰던 것이다 — 기본값을 씌우면
        #    규모가 조용히 달라진다.
        **({"margin": row["margin"]} if row.get("margin") else {}),
        # 🔴 금고 셋·브레이커도 같이 — 없으면 설정 없는 판이 되살아난다 (2026-08-23).
        **revive_settings_of(row),
    }
    try:
        body = await _live_start(payload, reviving=True)
    except Exception as exc:
        tries += 1
        _REVIVE[key] = (tries, now + REVIVE_BACKOFF * (2 ** (tries - 1)))
        _logger.error(
            "run_revive_failed",
            payload={"run": key, "tries": str(tries), "error": str(exc)[:200]},
        )
        return {"revive": f"부활 실패 {tries}/{REVIVE_LIMIT} — {str(exc)[:120]}"}
    _REVIVE.pop(key, None)
    handle = str(body.get("session_id", ""))
    runner = LIVE_RUNNERS.get(handle)
    if runner is not None:
        # ⭐ 판 화면이 이 사실을 든다. 로그에만 두면 아무도 안 본다.
        runner.note_guard("revived", f"감시자가 되살렸다 ({code})")
    _logger.info("run_revived", payload={"run": key, "as": handle, "code": code})
    return {"revive": f"감시자가 되살렸다 → {handle}"}


async def watch_forever() -> None:
    """감시를 주기로 돌린다.

    Raises:
        asyncio.CancelledError: 종료 신호만 올린다. 다른 예외는 로그로 남기고 다음 주기를 돈다.

    Note:
        ⛔ **여기서 예외가 나가면 감시가 통째로 멎는다.** 감시자가 조용히 죽으면
        감시자가 없는 것보다 나쁘다 — 있다고 믿게 되기 때문이다.
    """
    while True:
        try:
            await watch_runs()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.error("watch_loop_failed", payload={"error": str(exc)[:200]})
        await asyncio.sleep(WATCH_EVERY)


async def autostart_live() -> str | None:
    """API 가 뜰 때 **라이브를 다시 붙인다**.

    Returns:
        띄운 세션 id. 안 띄웠으면 None.

    Note:
        🔴 사용자 지적 2026-08-18: *"이제 그냥 내내 돌아야 하는 거잖아."* 맞다.
        러너는 uvicorn 의 태스크라 **재시작마다 죽었다.** `make dev` 의 자동 기동은
        스택을 처음 띄울 때 한 번만 돌아서, `src/` 를 고쳐 리로드될 때마다 판이
        사라졌고 사용자는 죽은 목록을 보고 있었다.

        ⇒ **기동 훅으로 옮긴다.** 리로드든 재부팅이든 API 가 살아나면 판도 살아난다.

        ⛔ **끄는 스위치를 남긴다** (`AUTO_LIVE=0`). 자동으로 주문 경로가 열리는 것을
        선택으로 두지 않으면, 나중에 실주문을 붙일 때 이 줄이 그대로 남아 무심코
        라이브가 돈다.

        ⚠️ **이미 도는 판이 있으면 안 띄운다.** Gate 는 계약당 포지션이 하나라 두 판이
        같은 종목이면 서로의 포지션을 자기 것으로 여긴다 (T14-2).

        ⚠️ **원장은 처음부터 시작한다.** 거래소에 포지션이 남아 있으면 새 판은 그것을
        모른다 — 진짜 이어붙이기는 T14-2(기존 포지션 확인)가 들어와야 성립한다.
        그때까지 이것은 *"판이 늘 살아 있게"* 까지만 한다.

        ⛔ 실패해도 API 기동을 막지 않는다 — 화면·조회는 라이브 없이도 써야 한다.
    """
    if os.environ.get("AUTO_LIVE", "1") == "0":
        return None
    if any(key in LIVE_RUNNERS for key in SESSIONS):
        return None
    # ⚠️ 주문 경로 자격증명이 하나도 없으면 되살릴 수 없다. 🔴 2026-09-06 까지는 **Gate 키
    #    하나**만 봤다 — 로컬 api 에서 Gate 키를 빼자(S7) 바이낸스 판 여섯이 통째로 관리 밖에
    #    놓였다. 거래소별 실패는 아래 루프가 판마다 따로 적는다(#8-1).
    if not any(
        os.environ.get(name, "").strip()
        for name in ("GATE_TESTNET_API_KEY", "BINANCE_TESTNET_API_KEY")
    ):
        return None

    # 🔴 **열린 RUN 을 전부 되살린다** (2026-08-19 실측으로 잡았다). 예전에는 BTC 하나가
    #    박혀 있어서, 손으로 띄운 ETH RUN 이 `src/` 를 고칠 때마다 **조용히 사라졌다** —
    #    원장은 DB 에 살아남는데(T16) 러너가 안 붙으니 아무도 그 포지션을 관리하지
    #    않는다. T16 이 고치려던 바로 그 상태다.
    wanted: list[dict[str, object]] = []
    if _store is not None:
        try:
            for row in await _store.open_runs(live=True):
                wanted.append(
                    {
                        # 🔴 키를 물려준다 (2026-08-27) — 부활 페이로드와 같은 이유.
                        "session_id": row["key"],
                        "playbook": row["playbook_id"],
                        "symbol": row["symbol"],
                        "market": row["market"],
                        "leverage": row["leverage"],
                        # ⚠️ 증거금이 비어 있으면 그 RUN 은 지갑 전액을 쓰던 것이다 —
                        #    되살릴 때 기본값을 씌우면 규모가 조용히 달라진다.
                        **({"margin": row["margin"]} if row["margin"] else {}),
                    }
                )
        except RunStoreError as exc:
            _logger.error("live_autostart_unreadable", payload={"error": str(exc)[:200]})

    # 🔴 **없으면 아무것도 안 만든다** (2026-08-19 사고 ⑥).
    #
    #    예전에는 여기서 BTC 기본 RUN 을 띄웠다. *"열린 RUN 이 없다"* 를 **"처음 켰다"**
    #    로만 해석한 것인데, **"사람이 방금 다 지웠다"** 도 같은 모양이다 — 판을 싹 지운
    #    직후 `src/` 를 고쳐 리로드되면 **주문 경로가 저절로 열렸다.**
    #
    #    ⇒ 이 함수의 일은 **되살리기 하나**다. 만드는 것은 사람이 콘솔에서 한다
    #      (사용자 확정: *"판 생성은 내가 할테니까, 너가 따로 하지 말고"*).
    #
    # ⚠️ 처음 켜는 경우도 이제 빈손으로 끝난다. 그것이 맞다 — 어떤 종목을 얼마로
    #    돌릴지는 기본값이 정할 일이 아니다.
    if not wanted:
        return None

    first: str | None = None
    for payload in wanted:
        try:
            # ⭐ **되살리기다** — 신규 생성용 증거금 검사를 받지 않는다 (사고 ⑤).
            body = await _live_start(payload, reviving=True)
        except Exception as exc:
            # ⛔ 하나가 실패해도 나머지는 띄운다 — 한 종목의 문제로 다른 RUN 이 통째로
            #    관리 밖에 놓이면 안 된다 (절대 규칙 #8-1).
            _logger.error(
                "live_autostart_failed",
                payload={
                    "symbol": payload.get("symbol"),
                    "playbook": payload.get("playbook"),
                    "error": str(exc)[:200],
                    # 🔴 **포지션이 남은 판이 못 살아나면 그것은 사건이다** (사고 ⑤).
                    #    관리자 없는 레버리지 포지션이 거래소에 남는다는 뜻이고,
                    #    화면에는 판이 그냥 사라진 것으로만 보인다 (절대 규칙 #8).
                    "note": "🔴 RUN 을 못 되살렸다 — 거래소에 포지션이 남아 있으면 "
                    "지금 아무도 관리하지 않는다. 콘솔에서 확인한다",
                },
            )
            continue
        handle = str(body.get("session_id", ""))
        first = first or handle
        _logger.info(
            "live_autostarted",
            payload={"session_id": handle, "symbol": payload.get("symbol")},
        )
    return first


@router.get("/fx")
async def fx() -> dict[str, Any]:
    """USDT → KRW 환율 (사용자 요구 2026-08-18 — 달러·원화 토글).

    Returns:
        `{usdt_krw, source, fetched_at}`. 못 읽으면 `usdt_krw` 가 None 이다.

    Note:
        🔴 **Gate 에는 KRW 가 없다** (2026-08-18 실측: `INVALID_CURRENCY: Invalid
        currency KRW`). 그래서 업비트 `KRW-USDT` 를 쓴다 — 이미 조회 전용 어댑터가 있고
        국내 실효 환율이라 체감과 맞는다 (은행 고시환율이 아니다).

        ⚠️ **표시 전용이다.** 원장·판정·비용은 전부 USDT 로 돈다. 환율을 계산에 끼우면
        투자 손익과 환손익이 섞이고, 그 분리는 Unified Portfolio 의 책임이다 (spec §4.18).

        ⛔ 실패하면 None 을 낸다 — 낡은 값을 캐시해서 내면 화면이 조용히 틀린 돈을 띄운다.
    """
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            res = await client.get(
                "https://api.upbit.com/v1/ticker", params={"markets": "KRW-USDT"}
            )
            res.raise_for_status()
            rows = res.json()
        rate = str(rows[0]["trade_price"])
    except Exception as exc:  # 표시용이라 화면을 막지 않는다
        _logger.warning("fx_unreadable", payload={"error": str(exc)[:200]})
        return {"usdt_krw": None, "source": "upbit", "error": str(exc)[:120]}
    return {
        "usdt_krw": rate,
        "source": "upbit KRW-USDT",
        "fetched_at": datetime.now(UTC).isoformat(),
    }


@router.post("/live/{key}/probe")
async def live_probe(key: str) -> dict[str, Any]:
    """봉이 **실시간으로 갱신되는지** 거래소에 직접 물어 본다.

    Args:
        key: 세션 id.

    Returns:
        거래소 최신 봉 · 급전 커서 · 지연(초) · 판정.

    Raises:
        HTTPException: 라이브 세션이 아니면 404.

    Note:
        🔴 **`running=true` 는 살아 있다는 증거가 아니다.** 웹소켓이 조용히 끊겨도
        태스크는 멀쩡히 대기한다 — 예외도 로그도 없이 40분간 아무 일이 없던 사고가
        정확히 그 모습이었다. 그래서 REST 로 **따로** 물어 비교한다.

        ⚠️ GET 이 아니라 POST 다. 거래소를 때리는 일이라 폴링에 섞이면 안 되고,
        **사람이 누를 때만** 나가야 한다.
    """
    runner = LIVE_RUNNERS.get(key)
    if runner is None:
        raise HTTPException(404, f"{key} 는 라이브 세션이 아니다")
    body = await runner.probe()
    _logger.info("live_probe", payload={"session_id": key, **body})
    return body


@router.get("/leftovers")
async def leftovers() -> dict[str, Any]:
    """**주인 없는 잔재** — 어느 판도 맡지 않는 포지션·주문 (사용자 요구 2026-08-21).

    Returns:
        `{rows: [{symbol, kind, position, orders}], owned: [...]}`.

    Note:
        🔴 **화면에 없으면 없는 것이 된다.** XRP 조건부가 하루를 남아 있었는데 화면 어디에도
        안 나왔다 — 거래소 상태를 직접 찔러서야 찾았다. 판을 안 띄운 종목의 잔재는
        판 목록에도, 판 상세에도, 콘솔에도 나올 자리가 없었다.

        ⚠️ **판이 맡고 있는 종목은 잔재가 아니다.** 돌고 있는 ETH 판의 손절을 잔재로
        보여 주고 지우게 하면 우리가 무방비 포지션을 만드는 것이다. 주인 판정은
        **종목 단위**로 한다 — Gate 무기한은 종목당 포지션이 하나뿐이라 이것으로 충분하고,
        주문 하나하나에 주인을 붙이는 것보다 틀릴 여지가 적다.

        ⭐ **포지션이 남은 경우와 주문만 남은 경우를 가른다.** 주문만 남았으면 답이 하나라
        (거둔다) 화면이 단추 하나면 되지만, 포지션이 남았으면 **닫는 순간 손익이 확정**되므로
        사람이 정해야 한다 — `kind` 가 그 갈림을 나른다.
    """
    # 🔴 **거래소마다 따로 본다** (2026-08-30 수정). 전에는 `console_state()` 를 인자
    #    없이 불러 **GATE 만** 훑었다 — 바이낸스에 고아가 생기면 화면 어디에도 안 나왔고,
    #    `owned` 도 두 거래소를 섞어 세서 한쪽의 판이 다른 쪽의 잔재를 가렸다.
    #    판 11개 중 6개가 바이낸스인 상태였으므로 절반이 사각지대였다.
    rows: list[dict[str, Any]] = []
    owned_all: set[str] = set()
    for market in _live_markets():
        body, owned, positions, resting = await _venue_snapshot(market)
        owned_all |= {f"{market}:{s}" for s in owned}
        _ = body
        for symbol in sorted({*positions, *resting} - owned - {""}):
            held = positions.get(symbol)
            rows.append(
                {
                    "market": market,
                    "symbol": symbol,
                    # 🔴 포지션이 있으면 그 주문들은 **잔재가 아니라 보호막**이다 —
                    #    목록엔 같이 실어 보내되 `kind` 로 갈라, 화면이 거두는 단추를
                    #    안 그리게 한다.
                    "kind": "포지션" if held else "주문",
                    "position": held,
                    "orders": [
                        {
                            "id": str(row.get("id", "")),
                            "kind": "조건부" if row.get("trigger_price") else "지정가",
                            "at": str(row.get("trigger_price") or row.get("price") or ""),
                            "size": str(row.get("size", "")),
                            "reduce_only": str(row.get("is_reduce_only", "")),
                        }
                        for row in resting.get(symbol, [])
                    ],
                }
            )
    return {"rows": rows, "owned": sorted(owned_all)}


async def _venue_snapshot(
    market: str,
) -> tuple[dict[str, Any], set[str], dict[str, dict[str, str]], dict[str, list[dict[str, str]]]]:
    """거래소 하나의 사실을 한 번에 긁는다 — 잔재 목록과 대조 루프가 같이 쓴다.

    Args:
        market: 거래소.

    Returns:
        `(원본 응답, 이 거래소에서 판이 맡은 종목들, 종목별 포지션, 종목별 걸린 주문)`.

    Raises:
        HTTPException: 조회 실패. ⛔ 조용히 빈 값을 내면 **"잔재 없음"** 이 되고,
            그것은 있는 위험을 없다고 말하는 것이다 (규칙 #8).
    """
    from updown.apps.api.exchange import state as console_state

    try:
        body = await console_state(market=market)
    except Exception as exc:
        raise HTTPException(503, f"{market} 거래소 상태를 못 읽었다 — {exc}") from exc
    owned = {
        runner.instrument.symbol
        for runner in LIVE_RUNNERS.values()
        if runner.instrument.market.value == market
    }
    positions = {
        str(row.get("symbol", "")): row
        for row in cast("list[dict[str, str]]", body.get("positions", []))
    }
    resting: dict[str, list[dict[str, str]]] = {}
    for row in [
        *cast("list[dict[str, str]]", body.get("stops", [])),
        *cast("list[dict[str, str]]", body.get("orders", [])),
    ]:
        resting.setdefault(str(row.get("symbol", "")), []).append(row)
    return body, owned, positions, resting


RECONCILE_S = 120
"""대조 주기(초). 4h 걸음보다 훨씬 촘촘해야 한다 — 사고는 걸음 사이에 난다.

⚠️ 더 짧게 하면 거래소 조회가 늘고 레이트리밋에 닿는다. `console_state` 가 TTL 캐시를
쓰므로 실제 호출은 이보다 드물다.
"""

FOUND: list[Finding] = []
"""마지막 대조에서 갈린 것들 — 배너·차단·화면이 같은 목록을 본다."""


@dataclass
class _Recon:
    """대조 상태 — **한 덩어리로 둔다**.

    Note:
        ⚠️ 모듈 전역 두 개(`BLOCKED`·`RECONCILE_AT`)로 두면 한쪽만 갱신되는 순간이
        생긴다 — "막을 것은 있는데 시각이 옛것" 은 화면에 `stale` 이 아닌 것으로 보인다.
        한 객체를 통째로 갈아 끼우면 그 틈이 없다.
    """

    blocked: frozenset[str] = frozenset()
    """신규 진입을 보류할 `거래소:종목` 열쇠들 (`reconcile.blocked_keys`)."""

    at: datetime | None = None
    """마지막으로 대조가 **성공한** 시각. None 이면 아직 한 번도 못 맞춰 봤다."""

    partial: dict[str, int] = field(default_factory=lambda: {})
    """**다 못 채워진 채 끝난 주문** 누적 (`손절:price_rate_proteced` 등).

    ⚠️ 경보가 아니라 계수기다 — 재장착은 러너가 1초마다 이미 한다. 이 값은
    *"실계좌에서도 이만큼 나는가"* 를 판단할 재료다.
    """

    watermark: dict[str, float] = field(default_factory=lambda: {})
    """거래소별로 **어디까지 세었나** (epoch). 두 번 세지 않기 위한 표식이다."""


RECON = _Recon()

RECONCILE_ROOT = Path(os.environ.get("RECONCILE_ROOT", "logs/reconcile"))
"""대조 결과를 남기는 곳.

🔴 **메모리에만 두면 재시작에 증발한다.** 그런데 사용자가 걱정한 시나리오가 바로
*"오류로 죽었다 살아났는데 거래소에만 남아 있는 경우"* 다 — 정확히 그때 잊어버리면
안 된다 (`ORPHANS` 가 그 모양이었다).
"""


def _save_reconcile(at: datetime) -> None:
    """대조 결과를 디스크에 남긴다 — 재시작해도 안 잊게."""
    try:
        RECONCILE_ROOT.mkdir(parents=True, exist_ok=True)
        (RECONCILE_ROOT / "latest.json").write_text(
            json.dumps(
                {
                    "at": at.isoformat(),
                    "blocked": sorted(RECON.blocked),
                    # 🔴 계수기는 **반드시 남긴다** — 재시작마다 0 이 되면 빈도를 못 센다.
                    "partial": RECON.partial,
                    "watermark": RECON.watermark,
                    "findings": [
                        {
                            "code": item.code,
                            "market": item.market,
                            "symbol": item.symbol,
                            "level": item.level,
                            "detail": item.detail,
                            "orders": list(item.orders),
                        }
                        for item in FOUND
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        _logger.warning("reconcile_save_failed", payload={"error": str(exc)[:140]})


_NAKED_STREAK: dict[str, int] = {}
"""축 D 를 **연속 몇 번** 봤나 — 진입 직후의 빈틈을 경보로 세지 않기 위해서다."""

NAKED_STRIKES = 2
"""축 D 는 **두 번 연속**일 때만 낸다 (= 4분).

🔴 체결로 포지션이 생긴 뒤 러너가 조건부를 걸기까지 몇 초가 걸린다. 그 순간을 잡으면
진입할 때마다 붉은 배너가 뜨고, **그러면 사람이 배너를 안 보게 된다** — 콘솔이 무방비
경보에서 이미 겪은 일이라 같은 처방(유예)을 쓴다.

⚠️ A·B·C 에는 유예가 없다. 그것들은 **한 번 봐도 사실**이다.
"""


def _settle_naked(found: list[Finding]) -> list[Finding]:
    """축 D 만 연속 관측을 요구한다 — 나머지는 그대로 통과시킨다."""
    now = {item.key for item in found if item.code == NAKED}
    for key in list(_NAKED_STREAK):
        if key not in now:
            _NAKED_STREAK.pop(key, None)
    for key in now:
        _NAKED_STREAK[key] = _NAKED_STREAK.get(key, 0) + 1
    return [
        item
        for item in found
        if item.code != NAKED or _NAKED_STREAK.get(item.key, 0) >= NAKED_STRIKES
    ]


def load_reconcile() -> None:
    """저장된 계수기를 되살린다 — **기동 때 한 번**.

    Note:
        🔴 계수기가 재시작마다 0 이 되면 *"실계좌에서도 이만큼 나는가"* 를 영영 못 센다.
        빈도를 세려고 만든 값이므로 누적이 유지되어야 뜻이 있다.

        ⚠️ **판정(`blocked`·`findings`)은 안 되살린다.** 그것들은 *지금* 의 사실이고,
        옛 값을 들고 시작하면 이미 해결된 것으로 진입을 막는다. 기동 직후 대조가
        한 번 돌아 새로 채운다.
    """
    path = RECONCILE_ROOT / "latest.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(saved, dict):
        return
    body = cast("dict[str, Any]", saved)
    part = body.get("partial")
    if isinstance(part, dict):
        RECON.partial.update({str(k): int(v) for k, v in cast("dict[str, Any]", part).items()})
    mark = body.get("watermark")
    if isinstance(mark, dict):
        RECON.watermark.update({str(k): float(v) for k, v in cast("dict[str, Any]", mark).items()})


async def reconcile_once() -> list[Finding]:
    """전 거래소 x 전 종목을 한 번 대조한다.

    Returns:
        갈린 것들.

    Raises:
        HTTPException: 어느 거래소든 조회에 실패하면. ⛔ **반쪽 대조를 성공으로 세지
            않는다** — 못 본 거래소의 고아를 "없다" 로 읽는 것이 이 기능의 최악 실패다.
    """
    shots: list[Snapshot] = []
    for market in _live_markets():
        body, owned, positions, resting = await _venue_snapshot(market)
        # ⭐ 계수기 — 이 조회에 이력이 이미 실려 있으므로 공짜다 (요청을 안 늘린다).
        got, mark = partial_fills(
            cast("list[dict[str, str]]", body.get("history", [])),
            RECON.watermark.get(market, 0.0),
        )
        for key, n in got.items():
            RECON.partial[key] = RECON.partial.get(key, 0) + n
        RECON.watermark[market] = mark
        # 원장이 보유 중이라고 말하는 종목들 (이 거래소의 판만)
        on_book = {
            runner.instrument.symbol
            for handle, runner in LIVE_RUNNERS.items()
            if runner.instrument.market.value == market
            and (live := SESSIONS.get(handle)) is not None
            and live.session.position is not None
        }
        # 진입 지정가를 걸고 기다리는 종목들 — 거래소에만 보이면 고아가 아니라 반영 대기다
        pending = {
            runner.instrument.symbol
            for handle, runner in LIVE_RUNNERS.items()
            if runner.instrument.market.value == market
            and (live := SESSIONS.get(handle)) is not None
            and live.session.waiting_trade is not None
        }
        for symbol in sorted({*positions, *resting, *owned} - {""}):
            row = positions.get(symbol)
            held = int(Decimal(str(row.get("size", "0") or "0"))) if row else 0
            shots.append(
                Snapshot(
                    market=market,
                    symbol=symbol,
                    held=held,
                    orders=tuple(str(o.get("id", "")) for o in resting.get(symbol, [])),
                    owned=symbol if symbol in owned else "",
                    on_book=symbol in on_book,
                    protected=covered(resting.get(symbol, []), held),
                    pending=symbol in pending,
                )
            )
    found = _settle_naked(compare(shots))
    at = datetime.now(UTC)
    FOUND[:] = found
    RECON.blocked = blocked_keys(found)
    RECON.at = at
    _save_reconcile(at)
    for item in found:
        _logger.error(
            "reconcile_finding",
            payload={"code": item.code, "market": item.market, "symbol": item.symbol},
        )
    # 🔴 판마다 **자기 칸이 막혔는지** 꽂는다 — 세션은 API 를 모른다 (계층 유지).
    for handle, runner in LIVE_RUNNERS.items():
        live = SESSIONS.get(handle)
        if live is None:
            continue
        key = f"{runner.instrument.market.value}:{runner.instrument.symbol}"
        live.session.reconciled = key not in RECON.blocked
    return found


async def reconcile_loop() -> None:
    """주기적으로 대조한다 — **경보만이 아니라 진입까지 막는다**.

    Note:
        🔴 배포 준비의 핵심은 여기다. 경보만 띄우면 사람이 자는 동안 갈린 상태 위에
        새 포지션이 쌓인다 — 관리 안 되는 포지션 옆에 관리되는 포지션을 하나 더 놓는
        셈이다.

        ⛔ **나가는 길은 안 막는다** (§1.2.1). 차단은 `session.step()` 의 진입 판단에만
        걸리고, 손절·청산·스탑 상향은 그 위에서 이미 끝나 있다.

        ⚠️ 조회 실패는 **차단을 풀지 않는다.** 못 읽은 것과 깨끗한 것은 다르다.
    """
    while True:
        await asyncio.sleep(RECONCILE_S)
        try:
            await reconcile_once()
        except Exception as exc:
            _logger.warning("reconcile_failed", payload={"error": str(exc)[:160]})


@router.get("/reconcile")
async def reconcile_state(refresh: bool = False) -> dict[str, Any]:
    """대조 결과 — 화면과 사람이 읽는 창구.

    Args:
        refresh: 참이면 지금 다시 대조한다 (기본은 마지막 결과를 낸다).

    Returns:
        `{at, findings, blocked, stale}`. `stale` 은 **마지막 대조가 너무 오래됐다** 는
        뜻이며, 그 자체가 경보다 — 루프가 죽었는데 화면이 옛 결과를 보여 주면 안 된다.
    """
    if refresh:
        await reconcile_once()
    at = RECON.at
    stale = at is None or (datetime.now(UTC) - at).total_seconds() > RECONCILE_S * 3
    return {
        "at": None if at is None else at.isoformat(),
        "period_s": RECONCILE_S,
        "stale": stale,
        "blocked": sorted(RECON.blocked),
        # ⚠️ 경보가 아니라 **계수기**다 — 재장착은 러너가 1초마다 이미 한다.
        #    실계좌로 갈 때 "이 빈도면 배율을 유지해도 되나" 를 판단할 재료다.
        "partial": dict(sorted(RECON.partial.items())),
        "findings": [
            {
                "code": item.code,
                "market": item.market,
                "symbol": item.symbol,
                "level": item.level,
                "detail": item.detail,
                "orders": list(item.orders),
            }
            for item in FOUND
        ],
    }


@router.post("/leftovers/{symbol}")
async def sweep_symbol(symbol: str) -> dict[str, Any]:
    """그 종목의 잔재를 **거둔다** — 포지션이 있으면 아무것도 안 한다.

    Args:
        symbol: 계약 이름.

    Returns:
        `{swept: [...]}`. 거둘 것이 없었으면 빈 목록.

    Raises:
        HTTPException: 살아 있는 판이 맡고 있는 종목이면 409.

    Note:
        ⛔ **판이 맡은 종목은 거부한다.** `sweep` 도 포지션이 있으면 안 거두지만, 그 판이
        아직 포지션을 안 잡았을 수 있다 — 그때 거두면 **다음 진입의 손절**을 미리 지운다.
        문을 두 겹으로 둔다.

        ⚠️ 포지션 청산은 여기서 안 한다 (`POST /exchange/close` 가 그 일이다). 닫는 것은
        손익이 확정되는 행동이라 잔재 청소와 **같은 단추에 있으면 안 된다**.
    """
    if any(runner.instrument.symbol == symbol for runner in LIVE_RUNNERS.values()):
        raise HTTPException(409, f"{symbol} 는 살아 있는 판이 맡고 있다 — 잔재가 아니다")
    from updown.apps.api.exchange import (
        _instrument,  # pyright: ignore[reportPrivateUsage]
        _orders_adapter,  # pyright: ignore[reportPrivateUsage]
    )

    swept = await sweep_leftovers(_orders_adapter(), _instrument(symbol), why="콘솔 정리")
    return {"swept": [f"{item.kind} {item.at}" for item in swept]}


# 저금통 은퇴 (사용자 확정 2026-08-26) — 재레버(0.8.0) 채택과 정면 충돌해 화면에서
#   내렸고, 라이브 펀드 경로는 금고 없이 뜬다. API·원장 필드는 동결(기본 off)로 남긴다.
#   되살릴 때는 §5.6.2 — 수익 떼기는 실현 경로 변경이라 새 플레이북 버전이다.
@router.get("/vault")
async def vault_settings() -> dict[str, Any]:
    """금고 전역 설정 — 지금 걸려 있는 값 (T21 ⑦).

    Returns:
        `{refill_cap, unit}`. 안 걸려 있으면 빈 문자열이다.

    Note:
        🔴 **안 걸린 것을 화면이 말해야 한다.** `None` 은 *"무한 충전"* 이고, 그것이
        가장 위험한 상태다 — 청산 → 채움 → 청산 이 무한히 돌면 금고는 잃는 속도를
        배로 늘리는 장치가 된다. 화면에 안 보이면 사람은 걸어 뒀다고 믿는다.

        ⭐ **다듬기 전 값을 돌려주지 않는다.** 화면이 보낸 그대로를 되비추면 사람은
        `30 %` 나 `300원` 같은 것을 저장했다고 믿는데, 실제로 걸린 것은 다르다.

        ⚠️ 비율은 여기서 **안 푼다** — 푸는 시점은 판이 뜨는 순간이고, 지금 잔액으로
        미리 풀어 보여 주면 나중에 실제로 걸리는 값과 다를 수 있다.
    """
    if _settings is None:
        return {"refill_cap": "", "unit": "amount"}
    text = cap_text(await _settings.get(REFILL_CAP_KEY))
    return {"refill_cap": text, "unit": "percent" if text.endswith("%") else "amount"}


@router.put("/vault")
async def set_vault(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """금고 전역 설정을 바꾼다 (T21 ⑦).

    Args:
        payload: `{refill_cap}`. `"300"` 은 금액, `"30%"` 는 **잔액 비율**,
            빈 값이면 **제한을 푼다**.

    Returns:
        바뀐 값.

    Raises:
        HTTPException: 저장소가 없거나 저장에 실패하면 503.

    Note:
        🔴 **모든 판에 같이 걸린다.** 금고는 하나인데 판마다 다른 상한을 쓰면 판 셋이
        각자 태워 합이 세 배가 된다.

        ⚠️ **이미 도는 판에는 다음 정산부터 적용된다.** 원장은 걸음마다 처음부터 다시
        걸어가므로(`_walk_wallet`), 값을 바꾸면 그 판의 과거 충전도 새 상한으로
        다시 세어진다 — 이미 상한을 넘겼으면 **즉시 멈춘다.** 그것이 의도다.

        ⛔ 저장 실패를 삼키지 않는다. 사람이 한도를 걸었는데 조용히 실패하면, 걸었다고
        믿는 상태로 돈다 (절대 규칙 #8).
    """
    if _settings is None:
        raise HTTPException(503, "저장소가 없다 — 한도를 걸 수 없다")
    text = cap_text(payload.get("refill_cap", ""))
    try:
        await _settings.put(REFILL_CAP_KEY, text)
    except RunStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    _logger.info(
        "vault_refill_cap_set",
        payload={
            "value": text,
            "unit": "percent" if text.endswith("%") else "amount",
            "note": "빈 값은 제한 없음 — 금고가 무한 탄창이 된다",
        },
    )
    return {"refill_cap": text, "unit": "percent" if text.endswith("%") else "amount"}


@cache
def _live_markets() -> tuple[str, ...]:
    """라이브 배선 가능한 거래소들 — SSoT 는 `provider.live_markets()` (T63 §2c)."""
    return MarketDataProvider().live_markets()


@router.get("/playbooks")
async def playbooks(request: Request) -> dict[str, Any]:
    """고를 수 있는 플레이북들 — 호출자가 **볼 수 있는**(`view`) 것만 (T13 ① · T230).

    Args:
        request: 요청 (`state.caller`). 호출자가 없으면(시험 우회) 전부.

    Returns:
        `{playbooks: [...]}`.
    """
    who = getattr(request.state, "caller", None)
    listing = _playbooks_all()
    if who is not None:
        listing["playbooks"] = [
            item for item in listing["playbooks"] if who.playbook(str(item["id"])).view
        ]
    return listing


def _playbooks_all() -> dict[str, Any]:
    """선언된 플레이북 전부 (T13 ①).

    Returns:
        `{playbooks: [...]}`. 각 항목에 국면·시간축·셋업이 실린다.
    """
    catalog = load_rules()
    return {
        "playbooks": [
            {
                "id": item.playbook_id,
                "attribution": item.attribution,
                "label": item.label or item.attribution,
                # ⭐ 권장 세트 그룹을 설정에서 정한다 (2026-08-24) — 하드코딩 제거.
                "recommended": item.recommended,
                # ⭐ **버전을 따로 낸다** — 화면이 `id` 로 고르고 `attribution` 으로
                #    보여 주는데, 둘을 이어 붙이면 어느 쪽이 성과 귀속 키인지 흐려진다.
                "version": item.version,
                "timeframe": item.timeframe.value,
                # 🔴 **방아쇠 축이 0.1 과 0.4 의 유일한 차이다** (T17). 목록에서 안
                #    보이면 둘이 같아 보이고, 사람이 무엇을 띄우는지 모른 채 고른다.
                #    None 이면 진입 축에서 방아쇠가 당겨진다 (= 0.1).
                "trigger": (
                    None if (found := trigger_frame(catalog, item)) is None else found.value
                ),
                "regimes": [regime.value for regime in item.regimes],
                # ⭐ T245 — 어느 시장 묶음(코인/주식)에서 도나. 매매법 선택창이 묶음으로 거른다.
                "groups": sorted(
                    {"coin" if g is MarketGroup.COIN else "stock" for g in item.market_groups}
                ),
                "setups": list(item.setups),
                "primary_flags": list(item.primary_flags),
                # 🔴 **측정된 배율** (2026-08-30). 화면의 기본값이 여기서 온다 —
                #    리터럴 3 이 박혀 있어서 6x 로 측정한 1.3.0 을 띄워도 3 이 떴다.
                "leverage": None if item.leverage is None else float(item.leverage),
                # ⭐ 기준 백테스트 한 줄 (2026-09-03) — 레버리지 입력칸을 없앤 대신,
                #    고르는 근거(측정 기간·손익·MDD)를 선택창이 보여 준다.
                "backtest_note": item.backtest_note,
            }
            # ⭐ 선택창에는 listed 만 (사용자 확정 2026-08-23). 숨긴 것도 id 로는 띄울 수 있다.
            for item in load_playbooks()
            if item.listed
        ],
        "sealed_range": [moment.isoformat() for moment in SEALED_RANGE],
        "timeframes": [frame.value for frame in FRAMES],
        # ⭐ 거래소 목록도 파생 (T63 §2c) — 화면 하드코딩 소멸.
        "live_markets": list(_live_markets()),
    }


def flag_names(given: object) -> list[str]:
    """플래그 선택을 id 목록으로 읽는다 — **리스트든 쉼표 문자열이든**.

    Args:
        given: 화면이 보낸 `"a,b"` 또는 저널에 저장된 `["a", "b"]`. `None`·빈 값이면
            빈 목록이다 (부르는 쪽이 기본값을 쓴다).

    Returns:
        공백을 털어낸 id 들. 빈 항목은 버린다.

    Note:
        🔴 **한쪽만 받으면 `/resume` 이 통째로 죽는다.** 실제로 죽어 있었다
        (2026-08-17 발견). 예전 코드는 `str(payload.get("flags", ""))` 였는데,
        리스트를 넘기면 `"['a', 'b']"` 가 되어 쉼표로 자른 조각이 `"['a'"` ·
        `" 'b']"` 다. 둘 다 없는 id 라 선택이 비고 400 이 났다.

        저널은 리스트로 저장하므로 **저널에 남은 판은 하나도 되살릴 수 없었다.**
        사용자에게는 *"멈춘 테스트가 다시 안 돌아간다"* 로 보였다 — 예외 메시지가
        `"고른 플래그가 없다"` 라, 원인이 형식 불일치라는 것을 가리키지 않았다.

        ⚠️ 그래서 실패 메시지에 **받은 값을 싣는다**. 무엇을 받았는지 안 보여 주면
        같은 사고를 또 겪는다 (절대 규칙 #8).
    """
    if isinstance(given, list):
        names = [str(item).strip() for item in cast("list[object]", given)]
    else:
        names = [item.strip() for item in str(given or "").split(",")]
    return [item for item in names if item]


@router.post("/start")
async def start(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """세션을 연다 (T13 ①③④).

    Args:
        request: 요청 — 이 매매법을 쓸 권한을 본다 (T230).
        payload: `{playbook, symbol, market, cash, start, days, seed, flags, applied}`.
            `start` 를 안 주면 **랜덤**이다 (T13 ③).

    Returns:
        `{session_id, ...상태}`.

    Raises:
        HTTPException: T01 구간 침범, 데이터 부족, 없는 플레이북.

    Note:
        🔴 **랜덤이 기본이다.** 사람이 "잘 나올 것 같은 구간"을 고르면 그 결과는 성과가
        아니라 선택의 결과다. 시드를 실어 재현만 가능하게 한다.
    """
    return await _start(payload, request=request)


def apply_playbook_knobs(session: Session, book: Playbook, catalog: dict[str, RuleConfig]) -> None:
    """매매법 선언·룰 설정이 정하는 세션 스위치들을 한 곳에서 켠다 (T231).

    백테스트 창구(`_start`)와 저장소 생성기(`playbook_evidence.py`)가 같은 배선을 쓴다.

    Args:
        session: 방금 만든 봉인 세션 (원장에 배율이 들어 있어야 한다).
        book: 대표 매매법 (묶음이면 첫 구성원).
        catalog: 룰 설정 (`load_rules()`).

    Raises:
        RiskConfigError: 배율에 짝인 β·손절 하한이 없으면 — 부르는 쪽이 시작을 거부한다 (규칙 #8).

    Note:
        2026-09-09 까지 `_start` 안에 있던 블록 그대로다 — 동작 변화 없음. 여기 두는 이유는
        `scripts/build/playbook_evidence.py` 가 **같은 스위치**로 저장소를 만들어야 화면 숫자와
        라이브 설정이 한 출처가 되기 때문이다.
    """
    session.flip_on_opposite = any(
        bool(catalog[name].params.get("symmetric")) for name in book.setups if name in catalog
    )
    # ⭐ T46 — 돌파 사건에만 반응하는 전환 스위치 (백테스트 `walk_session` 과 같은 배선).
    session.flip_on_event = any(item.flip_on_opposite for item in session.playbooks)
    # T66-e/T68 — 건당 리스크는 **제안을 낸 플레이북**이 정한다 (_exposure 인자).
    #    여기서는 상한만 판의 배율로 맞춘다 — 세션 전역 risk_pct 를 넣으면 번들에서
    #    추세(고정 배율)까지 r 사이징으로 오염된다.
    if any(item.risk_pct is not None for item in session.playbooks):
        session.leverage_cap = session.ledger.leverage
    # 🔴 β — 손절을 청산거리 안쪽으로 당기는 상한 (T120~T146).
    #    `require_stop_cap` 이 **배율과 짝을 강제한다**: 문턱(3x)을 넘는 배율인데 β 가
    #    없으면 여기서 터진다. 측정이 말하는 것은 "6x 가 좋다" 가 아니라 "β 를 켠 6x 가
    #    좋다" 이고(6x β0 은 청산 25건 · T144), 둘을 따로 켤 수 있게 두면 언젠가 반쪽만
    #    켜지는데 그 반쪽이 하필 위험한 쪽이다.
    _risk = load_risk_settings()
    session.stop_cap_ratio = require_stop_cap(_risk, session.ledger.leverage)
    # 🔴 손절 하한 (T147~T150) — β 의 짝. 손절거리를 [하한, β x 청산거리] 로 가둔다.
    session.stop_min_pct = _risk.stop_min_pct
    # ⭐ T42 ⑤ — 국면 RANGE 판정이 탐지기와 같은 최소 폭을 쓴다 (백테스트와 같은 배선).
    # ⭐ T233 ② — close 매매법의 라이브 보호 손절 자리. 백테스트는 안 쓰지만 같은 함수가 세팅해
    #    "화면 숫자 = 라이브 설정" 을 지킨다. close 매매법이 있는데 비율이 없으면 라이브가
    #    무방비라 막는다.
    session.stop_protect_ratio = _risk.stop_protect_ratio
    if _risk.stop_protect_ratio is None and any(
        item.stop_mode == "close" for item in session.playbooks
    ):
        raise RiskConfigError(
            "stop_mode: close 매매법인데 config/risk.yml 에 stop_protect_ratio 가 없다 — "
            "라이브가 거래소에 걸 보호 손절 자리가 없다"
        )
    # ⭐ T239 — 시장 능력표: 현물은 숏 없음 · 배율 없음. 시장 이름으로 분기하지 않고 표를 읽는다.
    caps = capabilities_of(session.instrument.market)
    session.short_allowed = caps.short_allowed
    if not caps.leverage_allowed and session.ledger.leverage > 1:
        raise RiskConfigError(
            f"{session.instrument.market} 는 배율을 쓸 수 없는 시장인데 원장 배율이 "
            f"{session.ledger.leverage} 다 — 매매법 선언의 leverage 를 지우거나 1 로 둔다"
        )
    # ⭐ T241 — 일중 청산은 마감이 있는 시장에서만 뜻이 있다. 달력을 세션에 준다.
    session.flat_at_close = any(item.flat_at_close for item in session.playbooks)
    if session.flat_at_close:
        if caps.always_open:
            raise RiskConfigError(
                f"{session.instrument.market} 는 24시간 장이라 마감 청산(flat_at_close)이 없다 — "
                "매매법 선언을 지운다"
            )
        session.calendar = load_calendar()
    session.span_cover = span_cover_of(catalog, book)
    # ✅ T42 ④ (사용자 확정 2026-08-22) — 라이브 원장도 체결 유형대로 센다. 일간 리포트의
    #    거래소 실제 수수료와 같은 자가 된다. 관문은 0.15% 그대로.
    session.fill_cost = True
    # 🔴 백테스트도 같은 규칙이다 — 진입가는 방아쇠 봉의 종가다 (T17 ③). 라이브만
    #    고치면 재는 것과 도는 것이 달라지고, 그러면 측정이 의미를 잃는다.
    session.price_frame = trigger_frame(catalog, book)
    # ⭐ 걸어 두고 받는 판이면 여기서 켜진다 (T19 ④). 선언이 없으면 시장가 그대로다.
    session.limit_entry = wants_limit(catalog, book)
    session.post_only_entry = wants_post_only(catalog, book)
    # 🔴 **걸어 두고 받는 판만 우편함을 든다** (T19 ⑤). 시장가 판에 꽂으면 아무도
    #     를 안 부르므로 무해하지만, 없는 편이 *"이 판이 무엇으로 도는가"* 가
    #    분명하다.
    session.shallow_entry = wants_shallow(catalog, book)
    # ⭐ 0.52 — 옮긴 진입가로 비용을 다시 잰다. 선언 없으면 0.5·0.51 그대로다.
    session.recheck_after_shift = wants_recheck(catalog, book)
    # 🔴 워크는 **봉 판정 필러**를 단다 (T202 3차 시도의 발견). `LiveFiller` 는 거래소
    #    답을 옮기는 우편함이라 봉인 워크에선 아무도 체결을 안 넣는다 — limit_entry
    #    플레이북(지정가 진입형)을 태우면 지정가가 영원히 미체결 = 매매 0 이 된다.
    #    실측: 탐지 5,808 · 제안 5,808 · 차단 0 · 진입 0. 기존 워크는 시장가 룰
    #    (시장가 진입형)만 태워 이 결함이 드러난 적이 없다 — 동작 변화 없음.
    #    백테스트(gate_backtest)와 같은 배선: SealedFiller + 익절도 뚫어야 체결.
    session.filler = SealedFiller() if session.limit_entry else None
    session.strict_fills = session.limit_entry


async def _start(payload: dict[str, Any], *, request: Request | None) -> dict[str, Any]:
    """`start` 의 본체 — 되살리기(`request=None`)도 이 길을 탄다. T230 문은 request 가 있을 때만."""
    # ⭐ 기본값은 recommended 파생 (T63 ②) — 이전의 플레이북 이름 리터럴은 대청소로
    #    아카이브된 뒤 400 을 던지는 낡은 배선이었다. 리터럴 기본값은 반드시 낡는다.
    book = _playbook(str(payload.get("playbook") or default_playbook()))
    # T68 — 번들은 구성원으로 펼친다 (라이브 시작 경로와 같은 규칙).
    sealed_books = tuple(_playbook(m) for m in book.bundle) if book.bundle else (book,)
    if request is not None:
        require_playbook_trade(request, (item.playbook_id for item in sealed_books))
    book = sealed_books[0]
    symbol = str(payload.get("symbol", "KRW-BTC"))
    market = Market(str(payload.get("market", "UPBIT")))
    if request is not None:
        require_market_trade(request, market)  # T242
    days = int(payload.get("days", 7))
    seed = payload.get("seed")

    raw_start = payload.get("start")
    if raw_start:
        moment = datetime.fromisoformat(str(raw_start)).astimezone(UTC)
        used_seed = None
    else:
        used_seed = int(seed) if seed is not None else random.randrange(2**31)
        span = (RANDOM_RANGE[1] - RANDOM_RANGE[0]).days - days
        moment = RANDOM_RANGE[0] + timedelta(days=random.Random(used_seed).randrange(max(1, span)))

    end = moment + timedelta(days=days)
    _check_seal(moment, end)

    source = await _load(symbol, market, moment, end)
    try:
        feed = SealedFeed(source, Seal(start=moment, end=end))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if os.environ.get("WALK_SYNTH_DIR") and symbol.startswith("SYN"):
        # 🔴 합성 검증은 라이브와 같은 800봉 창으로 판정한다 (`_SynthCapped` 참고).
        #    pyright 는 `__getattr__` 위임을 못 보므로 cast 로 넘긴다 — gate_backtest 와
        #    같은 수법이다. 실심볼 경로는 한 톨도 안 바뀐다.
        feed = cast("SealedFeed", _SynthCapped(feed, SEED_BARS))

    # ⭐ 점검기와 **같은 확장기**를 쓴다. 그룹 이름이 소속 전부로 펼쳐지는 규칙이
    #    두 화면에서 달라지면 같은 선택이 다른 그림을 낸다.
    default = [*book.primary_flags, *(f"setup.{name}" for name in book.setups)]
    # 🔴 **리스트와 쉼표 문자열을 둘 다 받는다.** 화면은 `"a,b"` 로 보내고 저널에는
    #    `["a", "b"]` 로 저장된다 — 한쪽만 받으면 `/resume` 이 통째로 죽는다.
    #
    #    실제로 죽어 있었다 (2026-08-17 발견): `str(["a","b"])` 는
    #    `"['a', 'b']"` 라 쉼표로 자르면 `"['a'"` · `" 'b']"` 가 되고, 둘 다 없는
    #    id 라 `chosen` 이 비어 400 이 났다. **저널에 남은 판은 되살릴 수 없었다.**
    #    사용자에게는 "멈춘 테스트가 다시 안 돌아간다" 로 보였다.
    picked = flag_names(payload.get("flags")) or default
    chosen = expand(picked, available_flags(rules_config()))
    if not chosen:
        raise HTTPException(
            400,
            f"고른 플래그가 없다 — 없는 id 만 넘겼거나 선택이 비었다: {picked}",
        )
    applied = tuple(payload.get("applied") or book.primary_flags)
    # 🔴 **고른 매매법 하나만 돌린다** (2026-08-17 정정).
    #
    #    예전에는 선언된 것을 전부 넘겼다 — 근거는 *"상단이 뚫린 뒤에도 그 아래에서
    #    타점을 기다렸다"* 였고, 국면이 바뀌면 도는 매매법도 바뀌어야 한다는 것이었다.
    #
    #    ⭐ **그 문제는 이미 해결됐다.** 돌파 셋업이 같은 플레이북 안으로 들어와서
    #      (`BOX_BREAKOUT`), 박스가 깨지면 같은 매매법이 그 자리를 맡는다.
    #
    #    🔴 그런데 남겨 뒀더니 **버전 비교가 통째로 무의미해졌다.** 0.1·0.2·0.3 은
    #      같은 매매법의 다른 버전인데, 0.2 를 골라도 셋이 다 돌아 후보가 섞였다 —
    #      실측에서 0.1 과 0.2 가 **글자 하나까지 같은 결과**를 냈다
    #      (주문 88 · 숏 5 · 승률 90% · 손익 +18.94%).
    #
    #    ⚠️ 국면별로 다른 매매법이 필요해지면 그때 **선언에서** 조합을 정한다.
    #      "전부 넘기고 국면이 고르게 한다" 는 비교를 못 하게 만든다.
    declared = list(sealed_books)
    session = Session(
        instrument=instrument_of(symbol, market),
        playbooks=tuple(declared),
        feed=feed,
        # ⭐ 레지스트리는 한 번만 — 없으면 걸음마다 룰 YAML·entry point 를 다시 읽는다 (2026-09-08).
        registry=_shared_registry(),
        ledger=Ledger(
            seed_cash=Decimal(str(payload.get("cash", 10_000_000))),
            # 🔴 레버리지는 손익률에 그대로 곱해진다 (T13 · 사용자 요구).
            leverage=Decimal(str(payload.get("leverage", 1))),
            # ⭐ 수익 유보 — 화면은 **퍼센트**로 받고 원장은 비율로 든다.
            #    ⚠️ 0~100 으로 자른다. 100 을 넘으면 이익보다 많이 빼게 되고,
            #      그러면 굴리는 돈이 매매마다 줄어드는 이상한 계좌가 된다.
            skim_pct=min(max(Decimal(str(payload.get("skim", 0))), Decimal(0)), Decimal(100))
            / Decimal(100),
        ),
    )
    # ⭐ **되살릴 때는 같은 열쇠를 쓴다** — 다르면 저널이 둘로 갈려 같은 판이
    #   목록에 두 번 뜬다 (사용자 요구: 재시작해도 항목이 안 사라져야 한다).
    # ⭐ **반대 신호 청산은 룰이 정하고 여기서 켠다** — 세션이 플레이북 id 를 알면
    #    층 위반이다 (`orchestration/` 입주 조건). 룰 설정의 `symmetric` 을 본다.
    catalog = load_rules()
    try:
        apply_playbook_knobs(session, book, catalog)
    except RiskConfigError as exc:
        # ⛔ 시작을 거부한다 — 시작해 두고 청산이 나는 것보다 낫다 (절대 규칙 #8).
        raise HTTPException(400, str(exc)) from exc
    key = _safe_key(str(payload.get("session_id") or uuid4().hex[:12]))
    # 🔴 원장을 파일로 남긴다 — 메모리에만 두면 서버 재시작에 통째로 날아간다.
    session.journal_path = JOURNAL_ROOT / f"{key}.json"
    session.journal_meta = {
        "session_id": key,
        "symbol": symbol,
        "market": market.value,
        "playbooks": [item.attribution for item in declared],
        "primary": book.attribution,
        "seed": used_seed,
        "seal": {"start": moment.isoformat(), "end": end.isoformat()},
        "seed_cash": str(session.ledger.seed_cash),
        "skim_pct": str(session.ledger.skim_pct),
        # ⭐ **복원에 필요한 값은 전부 저널에 있어야 한다** (사용자 요구 2026-08-17:
        #   *"재시작하면 항목이 없어지지 않았으면 좋겠네"*). 배율·유보를 빼먹으면
        #   되살린 판이 **다른 조건**으로 돌아 같은 이름의 다른 기록이 된다.
        "leverage": str(session.ledger.leverage),
        "days": days,
        "playbook_id": book.playbook_id,
        "flags": list(chosen),
        "applied": list(applied),
    }
    live = Live(
        session=session,
        flags=chosen,
        applied=applied,
        seed=used_seed,
        _full=source,
        # 배속은 화면 편의값이라 하한만 본다 — 0 이나 음수면 러너가 한 걸음도 안 민다.
        speed=max(1, int(payload.get("speed", DEFAULT_SPEED))),
    )
    SESSIONS[key] = live
    # 🔴 **시작하면 바로 걸어간다** (사용자 요구 2026-08-17) — 여러 판을 동시에 돌리려면
    #    화면이 `step` 을 밀어 주는 방식으로는 안 된다. 탭을 떠나면 그 판이 멈춘다.
    live.runner = asyncio.create_task(_walk(key), name=f"walk-{key}")
    body = _state(key, None, book.timeframe)
    body["session_id"] = key
    return body


def _run_info(key: str, live: Live, runner: LiveRunner) -> dict[str, Any]:
    """**이 RUN 이 무엇인가** — 화면 머리말이 쓰는 값들 (사용자 요구 2026-08-19).

    Args:
        key: RUN id.
        live: 세션 묶음.
        runner: 러너.

    Returns:
        id·축·돈·눈금.

    Note:
        🔴 **눈금을 실어야 한다.** 2026-08-18 사고의 원인이 *"원화 상수 500 이 Gate 에서
        가격의 0.78% 였다"* 인데, 그 값이 화면 어디에도 없어서 **숏 계획이 21건 중 1건만
        서는 것**을 봉 단위로 파고들 때까지 몰랐다 (T18 ①).

        ⇒ 지금 쓰는 눈금과 그 근거(명세 틱 · 비율)를 나란히 낸다. 가격 대비 몇 %인지가
        판단의 근거이므로 그것도 함께 낸다.

        ⚠️ **못 읽으면 None 이다.** 0 으로 채우면 "눈금이 0" 으로 읽히고, 그것은 가격을
        통째로 지우는 값이다 (절대 규칙 #8).
    """
    session = live.session
    book = session.playbook
    market = session.instrument.market
    symbol = session.instrument.symbol
    costs = load_cost_table(DEFAULT_CONFIG_PATH).for_market(market)
    rows = session.feed.observed(runner.price_frame)
    price = rows[-1].close if rows else None
    tick: Decimal | None = None
    if price is not None:
        with contextlib.suppress(TickUnknownError):
            tick = resolve_tick(
                costs, symbol, price, krw=session.instrument.currency is Currency.KRW
            )
    spec = costs.spec_ticks.get(symbol)
    return {
        "id": str(session.journal_meta.get("run_id", "")),
        "key": key,
        "resumed": bool(session.journal_meta.get("resumed")),
        # 🔴 **주문을 내는 판인가.** 관찰 전용의 손익은 원장의 **모형**이고 체결 실패도
        #    슬리피지도 없다 — 실주문 판과 한 표에 올리면 "안 낸 주문이 잘 됐다" 가
        #    성적이 된다. 화면이 그 차이를 말해야 한다 (§1-0s).
        "observe_only": runner.observe_only,
        "market": market.value,
        "symbol": symbol,
        "playbook": book.attribution,
        # ── 축 셋 — 서로 다른 것이고, 같은 값으로 쓰면 화면이 거짓말한다 ──────
        #
        # 🔴 **`runner.entry` 는 방아쇠 축이 아니다** (2026-08-19 사용자 질문에 답하다
        #    잡았다). 그 값은 급전의 진입 축 = 플레이북 시간축이라, 0.4 를 띄워도
        #    `15m` 으로 나왔다 — 실제 방아쇠는 `10s` 인데 화면이 15m 이라고 말했다.
        #
        #    ⇒ **룰이 말하는 값을 쓴다.** 탐지기는 `trigger_timeframe` 이 있으면 그
        #      축의 봉으로 방아쇠를 재고, 없으면 구조물 축(= 플레이북 시간축)으로 잰다.
        #      `session.price_frame` 이 그 선언을 그대로 들고 있다 (T17 ③).
        #
        # ⚠️ 이 프로젝트가 같은 실수를 반복한다 — 화면이 로직이 안 쓰는 값을 보여 주면
        #    사람은 그 값으로 판단한다 (차트 축으로 "언제 판정하나" 를 적어 90배 틀린 적).
        "judge_frame": book.timeframe.value,
        "trigger_frame": (session.price_frame or book.timeframe).value,
        # 🔴 이 판이 쓰는 β (손절 상한 = 청산거리의 이 비율 · T120~T146).
        #    노출 안 하면 **켰는지 안 켰는지 화면에서 확인할 방법이 없다** — 6x 의
        #    전제가 β 이므로, 안 보이면 전제가 성립하는지 모르는 채로 도는 것이다.
        "stop_min_pct": (None if session.stop_min_pct is None else str(session.stop_min_pct)),
        "stop_cap_ratio": (None if session.stop_cap_ratio is None else str(session.stop_cap_ratio)),
        "price_frame": runner.price_frame.value,
        # ── 돈 ────────────────────────────────────────────────
        "leverage": str(session.ledger.leverage),
        "margin_budget": (
            None if session.ledger.margin_budget is None else str(session.ledger.margin_budget)
        ),
        "seed_cash": str(session.ledger.seed_cash),
        "funding": session.ledger.funding.value,
        # ── 호가 눈금 (T18 ①) ─────────────────────────────────
        "tick": None if tick is None else str(tick),
        "spec_tick": None if spec is None else str(spec),
        "tick_ratio": str(TICK_RATIO),
        # ⭐ **가격 대비 비율이 판단의 근거다.** 0.5 라는 숫자만으로는 굵은지 얇은지 알 수
        #    없다 — BTC 에서는 0.0008% 지만 XRP 2.1 에서는 24% 다.
        "tick_pct": (
            None if tick is None or price is None or price <= 0 else f"{tick / price * 100:.5f}"
        ),
        "price": None if price is None else str(price),
        "started_at": live.started_at.isoformat(),
    }


def leverage_of(text: str) -> str:
    """그 주문을 낸 RUN 의 **배율** (사용자 요구 2026-08-19).

    Args:
        text: 거래소 주문의 `text`.

    Returns:
        배율. RUN 을 못 가르면 빈 문자열.

    Note:
        🔴 **거래소에는 배율이 없다.** 주문 줄에도 청산 줄에도 없고, 지금 포지션의
        배율을 붙이면 *그때 값이 아니라 지금 값*이 된다.

        ⇒ 주문 이름에 박힌 RUN 표식으로 우리 쪽 기록을 찾는다 (T18 ⑤).
        `ao-`(거래소가 만든 것)와 규격 이전 주문은 표식이 없어 **빈칸**이다 —
        지어내지 않는다.

        ⚠️ 지금 도는 RUN 의 배율이다. 그 RUN 이 도중에 배율을 바꿨다면 옛 체결에는
        새 값이 붙는다 — 정확한 이력은 원장(`wf_trades.leverage`)에 있다.
    """
    tag = run_of_text(text)
    if not tag:
        return ""
    for key, live in SESSIONS.items():
        if run_tag(key) == tag:
            return str(live.session.ledger.leverage)
    return ""


def span_cover_of(catalog: Mapping[str, RuleConfig], book: Playbook) -> Decimal:
    """플레이북의 룰이 선언한 **최소 박스 폭 배수** (T42 ⑤).

    Args:
        catalog: 룰 설정 표.
        book: 플레이북.

    Returns:
        룰이 `span_cover` 를 선언했으면 그 값(여럿이면 최댓값), 아니면 상수 `SPAN_COVER`.

    Note:
        🔴 세션의 국면 RANGE 판정이 탐지기와 **같은 값**을 써야 한다. 여기서 안 넘기면 탐지는
        넓은 박스만 보는데 국면은 좁은 박스로 RANGE 를 선언해 둘이 어긋난다.
        ⛔ 선언이 없으면 상수 그대로 — 동결 버전은 한 비트도 안 달라진다.
    """
    found = [
        Decimal(str(catalog[name].params["span_cover"]))
        for name in book.setups
        if name in catalog and catalog[name].params.get("span_cover") is not None
    ]
    return max(found) if found else SPAN_COVER


def wants_limit(catalog: Mapping[str, RuleConfig], book: Playbook) -> bool:
    """이 플레이북이 **걸어 두고 받는가** (T19 ④).

    Args:
        catalog: 룰 설정 표.
        book: 플레이북.

    Returns:
        룰 하나라도 `limit_entry` 를 선언했으면 True.

    Note:
        🔴 **룰이 말하는 값을 쓴다.** 여기서 정하면 설정과 동작이 갈리고, 그러면 어떤
        판이 어느 규칙으로 돌았는지 기록에서 알 수 없다.

        ⛔ 선언이 없으면 False — 0.1 과 0.4 는 시장가 경로 그대로다 (§5.6.2 동결).
    """
    return any(
        bool(catalog[name].params.get("limit_entry"))
        for item in book.setups
        for name in (item,)
        if name in catalog
    )


def wants_post_only(catalog: Mapping[str, RuleConfig], book: Playbook) -> bool:
    """지정가 진입을 **post-only(poc)** 로 낼지 (T60 축④).

    Args:
        catalog: 룰 설정 표.
        book: 플레이북.

    Returns:
        룰 하나라도 `post_only` 를 선언했으면 True.

    Note:
        `wants_limit` 과 같은 원칙 — 룰이 말하는 값을 쓴다. 선언 없으면 gtc 그대로
        (동결 경로 무변화). poc 는 크로스면 거부라 메이커 요율이 보장된다.
    """
    return any(
        bool(catalog[name].params.get("post_only"))
        for item in book.setups
        for name in (item,)
        if name in catalog
    )


def drawdown_stop_of(raw: object) -> Decimal | None:
    """브레이커 문턱 — 고점 대비 낙폭 % (T22).

    Args:
        raw: 화면 입력. `"31%"` · `"31"` 둘 다 31% 다. 비우면 None (안 건다).

    Returns:
        0 초과 100 이하의 %. None 이면 브레이커를 안 건다.

    Raises:
        HTTPException: 읽을 수 없거나 범위 밖이면 400 — 조용히 0 으로 바꾸면 판이 첫
            정산에서 멈춘다 (절대 규칙 #8).
    """
    text = str(raw or "").strip().rstrip("%").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise HTTPException(400, f"브레이커 낙폭을 읽을 수 없다: {raw!r}") from exc
    if value <= 0 or value > 100:
        raise HTTPException(400, f"브레이커 낙폭은 0 초과 100 이하 % 여야 한다: {raw!r}")
    return value


def revive_settings_of(row: dict[str, Any]) -> dict[str, Any]:
    """되살릴 판의 **설정**을 띄울 때의 payload 로 되돌린다 (T16 · T22).

    Args:
        row: `open_runs` 가 낸 행. `meta` 에 띄울 때 받은 금고 셋·브레이커 원문이 있다.

    Returns:
        `skim · profit_line · budget_cap · drawdown_stop` 중 있는 것만 (+ `run_key` · `timeframe`).

    Note:
        🔴 2026-08-23 발견 — `_revive` 가 매매법·종목·배율·증거금만 넘겨서 되살아난
        판은 **금고 설정이 전부 빠진 채** 돌았다. 설정은 판의 일부다. 원문(`"20%"`)을
        그대로 넘기므로 같은 증거금에서 같은 값으로 풀린다.
    """
    meta = cast(dict[str, Any], row.get("meta") or {})
    out: dict[str, Any] = {}
    for key in ("skim", "profit_line", "budget_cap", "drawdown_stop"):
        value = cast(object, meta.get(key))
        if value not in (None, ""):
            out[key] = value
    # 🔴 **주문 표식(run_key)을 이어받는다** (2026-08-25). 감시자 부활은 새 핸들로 뜨므로
    #    이걸 안 넘기면 거래소 주문 태그(옛 표식)와 어긋나 자기 포지션을 거부한다.
    #    옛 판(meta 에 run_key 없음)은 row 의 key 가 곧 표식이다 — 정상 founding 이
    #    run_key=handle=key 였기 때문이다.
    marker = str(meta.get("run_key") or row.get("key") or "")
    if marker:
        out["run_key"] = marker
    # ⭐ T250 — 셋업 없는 판의 판정 축(차트 축)을 되살린다.
    judge = meta.get("judge_frame")
    if isinstance(judge, str) and judge:
        out["timeframe"] = judge
    return out


def over_margin(raw: object, margin: Decimal, label: str) -> Decimal | None:
    """**증거금 초과분**으로 적힌 금고 선을 절대 금액으로 푼다 (T21 ⑤⑥ · 2026-08-22).

    Args:
        raw: 화면 값. `"20%"`(증거금 대비 비율) · `"200"`(금액) · `""`(안 걸었다).
        margin: 이 판의 증거금.
        label: 오류 문구에 넣을 칸 이름.

    Returns:
        `margin + 초과분`. 비었으면 None.

    Raises:
        HTTPException: 읽을 수 없거나 음수인 경우 — 조용히 None 으로 떨어뜨리면 "안 걸었다"와
            구별이 안 된다 (절대 규칙 #8).

    Note:
        🔴 **왜 상대값인가.** 절대값으로 받던 때 폼 기본값(수익선 200 · 천장 300)이 증거금
        1000 보다 낮아, 첫 정산에서 `증거금 - 수익선` 이 통째로 실현 대상이 되고 천장 초과분이
        금고로 빠져 **첫 매매에서 판이 멈췄다.** 초과분으로 받으면 그 함정이 구조적으로 없다.

        ⚠️ `0`·`0%` 는 "증거금 바로 위" 다 — 안 건 것이 아니다. 안 걸려면 칸을 비운다.
    """
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        if text.endswith("%"):
            ratio = Decimal(text[:-1].strip()) / Decimal(100)
            extra = margin * ratio
        else:
            extra = Decimal(text)
    except (ArithmeticError, ValueError) as exc:
        raise HTTPException(400, f"{label} 을 읽을 수 없다: {text!r} — 예) 20% 또는 200") from exc
    if extra < 0:
        raise HTTPException(400, f"{label} 은 증거금 초과분이라 음수일 수 없다: {text!r}")
    return margin + extra


def _money(raw: object) -> Decimal | None:
    """화면이 보낸 금액 — 비었으면 None (= 제한 없음).

    Args:
        raw: 화면 값.

    Returns:
        금액. 비었거나 0 이하면 None.

    Note:
        ⚠️ **0 을 "0원 한도" 로 읽지 않는다.** 화면에서 칸을 비우면 빈 문자열이 오고,
        그것은 *"안 걸었다"* 는 뜻이다 — 0 으로 읽으면 판이 즉시 멈춘다.
    """
    if raw in (None, ""):
        return None
    with contextlib.suppress(Exception):
        found = Decimal(str(raw))
        return found if found > 0 else None
    return None


def cap_text(raw: object) -> str:
    """사람이 적은 한도를 **저장할 모양**으로 다듬는다 (2026-08-20).

    Args:
        raw: 화면이 보낸 값. `"300"` · `"30%"` · `""` 중 하나.

    Returns:
        `""`(제한 없음) · `"300"`(금액) · `"30%"`(잔액 비율).

    Note:
        🔴 **한 칸에 두 뜻을 담는다** (사용자 요구): *"금액으로도 설정할 수 있고,
        퍼센트로도 설정할 수 있게"*. 칸을 둘로 나누면 둘 다 채워진 상태가 생기고,
        그때 어느 쪽이 이기는지를 화면이 설명할 수 없다.

        ⚠️ **비율은 100 을 넘기지 않는다.** 잔액보다 많이 꺼낼 수는 없다 — 넘겨 적으면
        그것은 *"제한 없음"* 과 같은 뜻이고, 사람은 걸어 뒀다고 믿는다.

        ⚠️ **0 은 "안 걸었다" 로 읽는다** (`_money` 와 같은 규칙). 칸을 비우면 빈
        문자열이 오고, 0 으로 읽으면 판이 즉시 멈춘다.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.endswith("%"):
        with contextlib.suppress(Exception):
            found = Decimal(text[:-1].strip())
            if found <= 0:
                return ""
            # ⚠️ `normalize()` 만 쓰면 30 이 `3E+1` 이 된다 — 사람이 읽는 값이므로
            #    지수 표기를 막는다 (`"f"`).
            return f"{format(min(found, Decimal(100)).normalize(), 'f')}%"
        return ""
    found = _money(text)
    return "" if found is None else str(found)


def resolve_cap(text: str, wallet: Decimal | None) -> Decimal | None:
    """저장된 한도를 **금액으로** 푼다.

    Args:
        text: `cap_text` 가 다듬어 둔 값.
        wallet: 판이 뜰 때의 계정 총액. 비율일 때만 쓴다.

    Returns:
        금액. 제한이 없으면 None.

    Note:
        🔴 **판이 뜰 때 한 번 푼다.** 매 걸음 지금 잔액으로 다시 풀면 한도가 계속
        움직이고, 원장은 걸음마다 처음부터 다시 걸어가므로(`_walk_wallet`) **과거
        충전까지 새 기준으로 다시 세어진다** — 같은 입력에 다른 출력이다 (규칙 #5).

        ⇒ 비율은 *"판을 띄우는 순간의 잔액 기준"* 이고, 화면이 그 사실을 말한다.

        ⚠️ 비율인데 잔액을 못 읽으면 **제한 없음이 아니라 None** 을 돌려주게 되는데,
        그것이 곧 무한 충전이다. 그래서 잔액이 없으면 비율을 **못 푼 것으로 보고**
        호출하는 쪽이 막는다 — 여기서 조용히 0 을 만들면 판이 즉시 멈춘다.
    """
    if not text:
        return None
    if not text.endswith("%"):
        return _money(text)
    if wallet is None or wallet <= 0:
        return None
    with contextlib.suppress(Exception):
        found = wallet * Decimal(text[:-1]) / Decimal(100)
        return found if found > 0 else None
    return None


async def _refill_cap(wallet: Decimal | None = None) -> Decimal | None:
    """금고에서 꺼내 쓸 수 있는 **누적 총액** (T21 ⑦ · 전역).

    Args:
        wallet: 계정 총액. 한도가 비율일 때만 쓴다.

    Returns:
        상한. 안 걸려 있으면 None.

    Note:
        🔴 **None 은 "무한 충전" 이다.** 청산 → 채움 → 청산 이 무한히 돌면 금고는
        잃는 속도를 배로 늘리는 장치가 된다 — 그래서 화면이 지금 값을 늘 보여 줘야
        한다. 안 보이면 사람은 걸어 뒀다고 믿는다.
    """
    if _settings is None:
        return None
    return resolve_cap(cap_text(await _settings.get(REFILL_CAP_KEY)), wallet)


async def _budget_room(paper: object, wanted: Decimal, symbol: str, market: Market) -> None:
    """이 판의 예산이 **같은 거래소 계좌의 총액 안에 들어가나** (T21 ⑨).

    Args:
        paper: 주문 어댑터.
        wanted: 이 판이 쓰겠다는 증거금 예산.
        symbol: 종목 (문구용).
        market: 이 판의 거래소 — **합산은 같은 거래소 판들만** (2026-08-27 사용자
            신고: GT+BN 예산을 합쳐 BN 계좌 총액과 비교해 5번째 판이 400 으로 막혔다.
            계좌는 거래소마다 따로다).

    Raises:
        HTTPException: 열린 판들의 예산 합이 계좌 총액을 넘으면 400.

    Note:
        🔴 **금고는 장부상 개념이다.** Gate 에는 판별 증거금 계좌가 없고 계좌 하나를
        모두가 나눠 쓴다 — 막지 않으면 *"금고에 있어야 할 돈"* 을 다른 판이 증거금으로
        써 버린다. 2026-08-19 에 DOGE 가 계좌의 84% 를 물어 두 판이 못 뜬 것이 그것이다.

        ⚠️ **`available` 이 아니라 총액으로 잰다.** 포지션에 들어간 돈은 사라진 돈이
        아니다 — 이 프로젝트가 반복해서 틀린 지점이다.

        ⛔ 못 읽으면 막지 않는다. 조회 실패로 판을 못 띄우면 안전 장치가 길을 막는다.
    """
    if _store is None or not isinstance(paper, MarginAware):
        return
    total = Decimal(0)
    with contextlib.suppress(Exception):
        cash = cast("Decimal", (await paper.get_balance()).cash)  # type: ignore[attr-defined]
        total = Decimal(str(cash)) + await paper.account_margin()
    if total <= 0:
        return
    taken = Decimal(0)
    with contextlib.suppress(Exception):
        for row in await _store.open_runs(live=True):
            if str(row.get("market", "")) != market.value:
                continue  # 다른 거래소 계좌의 판 — 이 계좌의 방을 안 먹는다
            found = row.get("margin")
            if found:
                taken += Decimal(str(found))
    # ⚠️ 센트 아래 잔재는 넘김이 아니다 — 1/7 같은 비중의 Decimal 반올림 꼬리(10^-19)가 "300 을
    #    넘는다" 로 펀드 생성을 막았다 (2026-09-05). 비교는 센트 단위로 한다.
    if (taken + wanted).quantize(Decimal("0.01")) > total.quantize(Decimal("0.01")):
        raise HTTPException(
            400,
            f"{symbol} 예산 {wanted} 를 더하면 {market.value} 판들의 예산 합이 "
            f"{taken + wanted} 로 그 계좌 총액 {total} 을 넘는다 (이미 잡힌 예산 "
            f"{taken}). 계좌 하나를 그 거래소 판들이 나눠 쓰므로, 넘기면 나중에 뜬 "
            "판이 증거금을 못 받아 조용히 주문 0건이 된다",
        )


def thinnest_leg(catalog: Mapping[str, RuleConfig], book: Playbook) -> Decimal:
    """가장 **얇은 진입 다리**의 비중 (2026-08-20).

    Args:
        catalog: 룰 설정 표.
        book: 플레이북.

    Returns:
        사다리로 걸어 두는 판이면 절반, 그 자리에서 사는 판이면 전부.

    Note:
        🔴 **예산은 다리 단위로 쪼개져 나간다.** 예산 검사를 예산 전액으로 하면 ETH 가
        통과한다 — 전액 9.9 x 3배 = 29.7 은 1계약(22.82)을 사고도 남지만, 실제로 한
        다리에 가는 것은 4.95 x 3배 = 14.85 라 **0계약**이다. 실측으로 그렇게 거절됐다.

        ⚠️ **룰이 선언한 다리 수를 쓴다** (`limit_legs` · 없으면 2). 여기서 0.5 를 따로 적으면
        사다리 모양이 바뀌는 날 문만 옛 값을 들고 남는다. 탐지기 상수를 직접 읽지 않는다 (T224).

        ⛔ 선언이 없으면 1 — 시장가 판은 계획 전액이 한 번에 나간다.
    """
    if not wants_limit(catalog, book):
        return Decimal(1)
    legs = max(
        (
            int(catalog[name].params.get("limit_legs", 2))
            for name in book.setups
            if name in catalog and catalog[name].params.get("limit_entry")
        ),
        default=2,
    )
    return Decimal(1) / Decimal(legs)


def wants_shallow(catalog: Mapping[str, RuleConfig], book: Playbook) -> bool:
    """사다리를 **한 칸 얕게** 놓는가 (T19 · 0.51).

    Args:
        catalog: 룰 설정 표.
        book: 플레이북.

    Returns:
        룰 하나라도 `shallow_entry` 를 선언했으면 True.

    Note:
        ⛔ 선언이 없으면 False — 0.5 는 계획이 적어 둔 자리를 그대로 쓴다.
    """
    return any(
        bool(catalog[name].params.get("shallow_entry")) for name in book.setups if name in catalog
    )


def wants_recheck(catalog: Mapping[str, RuleConfig], book: Playbook) -> bool:
    """진입가를 옮긴 뒤 **비용을 다시 재는 룰**이 있나 (0.52 · 2026-08-20).

    Args:
        catalog: 룰 설정 표.
        book: 플레이북.

    Returns:
        룰 하나라도 `recheck_after_shift` 를 선언했으면 True.

    Note:
        🔴 0.51 은 진입가만 얕게 당기고 익절가는 그대로 뒀다. 탐지기의 비용 검사는 옛
        진입가로 통과했고 실제로 쓰는 값은 달랐다 — 익절이 진입에서 **한 눈금 거리**에
        서는 계획이 나왔다 (실측 `70,718.7 → 70,718.6`, "목표 익절" -0.45%).

        ⛔ 선언이 없으면 False — 0.5·0.51 은 한 줄도 안 달라진다 (§5.6.2 동결).
    """
    return any(
        bool(catalog[name].params.get("recheck_after_shift"))
        for name in book.setups
        if name in catalog
    )


def trigger_frame(catalog: Mapping[str, RuleConfig], book: Playbook) -> Timeframe | None:
    """플레이북이 쓰는 룰의 **방아쇠 축** (T17 ③).

    Args:
        catalog: 룰 설정 표.
        book: 플레이북.

    Returns:
        방아쇠 축. 어느 룰도 선언 안 했으면 None.

    Raises:
        HTTPException: 룰이 모르는 축을 선언한 경우 — 조용히 무시하면 0.4 가 0.1 처럼
            돌면서 0.4 로 기록된다.

    Note:
        🔴 **진입가가 여기서 나온다.** 방아쇠를 10s 로 내리면서 진입가를 5m 종가로
        적으면 한 계획 안에 두 시점이 섞이고, 그것이 2026-08-18 사고의 모양이다.

        ⚠️ **여러 룰이 서로 다른 축을 선언하면 터진다.** 하나를 골라 쓰면 나머지 룰의
        진입가가 남의 축에서 나오고, 그 어긋남은 아무 신호 없이 손익에만 나타난다.
    """
    found = {
        str(catalog[name].params["trigger_timeframe"])
        for name in book.setups
        if name in catalog and catalog[name].params.get("trigger_timeframe")
    }
    if not found:
        return None
    if len(found) > 1:
        raise HTTPException(
            500,
            f"{book.attribution} 의 룰들이 서로 다른 방아쇠 축을 선언했다: {sorted(found)} — "
            "하나를 골라 쓰면 나머지 룰의 진입가가 남의 축에서 나온다",
        )
    raw = found.pop()
    try:
        return Timeframe(raw)
    except ValueError as exc:
        raise HTTPException(500, f"모르는 방아쇠 축이다: {raw!r}") from exc


def _summary(key: str, live: Live) -> dict[str, Any]:
    """목록 카드 하나 — **클릭하기 전에 알아야 하는 것만** 담는다.

    Args:
        key: 세션 id.
        live: 세션.

    Returns:
        요약 dict.

    Note:
        🔴 **차트를 담지 않는다.** 목록은 세션 수만큼 반복되므로 작도를 넣으면 판이
        늘수록 응답이 제곱으로 커진다. 자세한 것은 클릭해서 `/state` 로 본다.
    """
    session = live.session
    book = session.ledger
    rate = book.win_rate
    return {
        "session_id": key,
        # 🔴 **두 화면을 가르는 유일한 값이다.** 라이브(실계좌 페이퍼)와 백테스트가 한
        #    목록에 섞이면 성적이 뒤섞이고, 사용자가 그것을 지적했다 —
        #    *"모의 라이브에 냅다 붙이면 어떡하냐"*.
        #
        #    ⚠️ 화면이 아니라 **서버가 표시한다.** 화면이 심볼이나 시장으로 추측하면
        #    GATE 종목으로 돌린 백테스트가 라이브 목록에 뜬다.
        "live": key in LIVE_RUNNERS,
        # ⭐ 세트면 전부 적는다 (`a@1+b@2`) — 주 플레이북만 적으면 목록이 반쪽을 숨긴다.
        "playbook": "+".join(item.attribution for item in session.playbooks),
        "symbol": session.instrument.symbol,
        # T62 P3 — 레인 축: 어느 거래소의 판인가. 화면이 Gate/Binance 를 갈라 보여준다.
        "market": session.instrument.market.value,
        "started_at": live.started_at.isoformat(),
        "seal": {
            "start": session.feed.seal.start.isoformat(),
            "end": session.feed.seal.end.isoformat(),
        },
        "cursor": session.cursor.isoformat(),
        "progress": session.feed.progress(),
        "finished": session.finished,
        "paused": session.paused,
        # 🔴 **새 진입을 받는가** (사용자 요구 2026-08-20 — 콘솔의 "중지").
        #    `paused` 와 다르다: 저쪽은 걸음 자체를 멈춰 **손절 관리까지 멈춘다.**
        "auto": session.auto,
        "running": live.running,
        "waiting": live.waiting,
        "speed": live.speed,
        "seed": live.seed,
        "trades": len(book.records),
        "closed": len(book.closed),
        "wins": book.wins,
        "half_breakevens": book.half_breakevens,
        "liquidations": book.liquidations,
        # 🔴 **어느 돈 모형으로 낸 값인가** (T14-1). 재투입 규칙이 다르면 누적
        #    손익률의 정의가 다르고, 라벨이 없으면 **모형 차이가 전략 차이로 읽힌다**
        #    (§1-0s 관측 규약).
        "funding": book.funding.value,
        # ⭐ 지갑 — `WALLET` 모형에서 **거래소 잔액에 대응한다**.
        "wallet": float(book.wallet),
        # ⭐ 지갑에서 증거금으로 채워 넣은 총액. `refilled`(밖에서 넣었다고 **가정한**
        #    돈)와 다르다 — 이쪽은 내 돈을 옮긴 것이라 손익률에서 빼지 않는다.
        "topped_up": float(book.topped_up),
        # 🔴 **멈췄으면 그 사실을 말한다.** 조용히 멈추면 "판정 0회" 와 구별되지 않는다.
        "halted_at": book.halted_at,
        # ⭐ T22 — 낙폭과 브레이커. 멈췄으면 `tripped_at` 이 이유를 말한다.
        "drawdown_pct": float(book.drawdown_pct),
        "max_drawdown_pct": float(book.max_drawdown_pct),
        "tripped_at": book.tripped_at,
        "drawdown_stop_pct": (
            None if book.drawdown_stop_pct is None else float(book.drawdown_stop_pct)
        ),
        "refilled": float(book.refilled),
        "skim_pct": float(book.skim_pct * 100),
        "reserved": float(book.reserved),
        "withdrawn": float(book.withdrawn),
        "equity": float(book.equity),
        "win_rate": None if rate is None else float(rate),
        "cash": float(book.cash),
        "seed_cash": float(book.seed_cash),
        # 🔴 **굴리는 돈을 따로 낸다** (사용자 질문 2026-08-18: *"왜 1000-3X로 표기되지?
        #    난 500으로 포지션 증거금 넣은 것 같은데?"*). 목록이 `seed_cash`(지갑)만
        #    보여 줘서 입력한 증거금이 어디에도 안 보였다.
        #
        # ⚠️ 둘은 다른 돈이다 — 지갑은 거래소 잔액이고 증거금은 그중 주문에 쓰는 예산이다.
        "margin_budget": None if book.margin_budget is None else float(book.margin_budget),
        "leverage": float(book.leverage),
        "return_pct": float(book.return_pct),
        # 🔴 **이 판이 1.2.0 인지 1.3.0 인지 가르는 유일한 값이다** (2026-08-30).
        #    두 버전의 번들 구성원이 **같아서**
        #    위의 `playbook` 문자열로는 구별이 안 된다. β·하한은 판을 만들 때 주입되고
        #    이후 안 바뀌므로, 목록에 이 둘이 없으면 사람은 옛 판과 새 판을 섞어 본다.
        "stop_cap_ratio": (
            None if session.stop_cap_ratio is None else float(session.stop_cap_ratio)
        ),
        "stop_min_pct": (None if session.stop_min_pct is None else float(session.stop_min_pct)),
        # 🔴 **체결 모형 보정용 계측** (T165 · 2026-08-30). 백테스트가 쓰는 가정은
        #    "봉이 지정가를 관통하면 체결" 인데, 그 가정이 0.05%p 만 틀려도 4.6년
        #    수익이 -24~-44% 다. 체결률만으로는 못 고치고 **얼마나 더 관통해야
        #    채워지나**를 알아야 해서, 표마다 최근접 거리를 같이 남긴다.
        "fill_rate": (
            None
            if not (session.expired + len(session.ledger.records))
            else round(
                len(session.ledger.records) / (session.expired + len(session.ledger.records)) * 100,
                1,
            )
        ),
        "fill_probe": session.fill_probe[-40:],
        # 🔴 **이 판이 왜 안 들어가는지**를 목록이 말한다 (2026-08-30). 거짓이면 대조가
        #    갈려서 신규 진입을 보류 중이라는 뜻이다 — 이유는 위의 `watch` 배너에 있다.
        "reconciled": session.reconciled,
    }


def _read_journal(path: Path) -> dict[str, Any] | None:
    """저널 파일 하나를 읽는다.

    Args:
        path: 파일 경로.

    Returns:
        문서. 읽거나 파싱할 수 없으면 None.

    Note:
        ⚠️ **조용히 넘기지 않는다** — 호출부가 `None` 을 받아 *"읽을 수 없다"* 를
        목록에 싣는다. 파일이 깨졌는데 항목만 사라지면 사람이 못 알아챈다 (절대 규칙 #8).
    """
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, Any]", parsed)


def _journaled() -> list[dict[str, Any]]:
    """저널 파일에 남아 있는 판들 — **메모리에 없는 것만**.

    Returns:
        요약 목록. 읽을 수 없는 파일은 조용히 건너뛰지 않고 이유를 담아 낸다.

    Note:
        🔴 **재시작해도 항목이 안 사라져야 한다** (사용자 요구 2026-08-17). 세션
        등록부는 메모리라 프로세스가 죽으면 통째로 날아가는데, 저널은 남아 있다.

        ⚠️ 저널은 **결과의 사본**이지 살아 있는 세션이 아니다. 이어서 걸어가려면
        `/resume` 이 봉을 다시 받아 커서까지 되짚어야 한다 — 그래서 목록에서는
        `stored` 로 구분한다.
    """
    if not JOURNAL_ROOT.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in JOURNAL_ROOT.glob("*.json"):
        key = path.stem
        if key in SESSIONS:
            continue
        body = _read_journal(path)
        if body is None:
            rows.append({"session_id": key, "stored": True, "broken": "읽을 수 없다"})
            continue
        summary: dict[str, Any] = body.get("summary") or {}
        seed_cash = Decimal(str(body.get("seed_cash", 0)))
        rows.append(
            {
                "session_id": key,
                # 라이브였던 판은 **끝나도 라이브 목록에** 남는다. 백테스트 목록으로
                # 옮겨 가면 실계좌 성적이 페이크 성적 사이에 섞인다.
                "live": bool(body.get("live")),
                "playbook": body.get("primary", "?"),
                "symbol": body.get("symbol", "?"),
                "started_at": body.get("seal", {}).get("start", ""),
                "seal": body.get("seal", {"start": "", "end": ""}),
                "cursor": body.get("cursor", ""),
                "progress": float(body.get("progress", 0) or 0),
                "finished": bool(body.get("finished")),
                "paused": True,
                "running": False,
                "waiting": False,
                "stored": True,
                "speed": DEFAULT_SPEED,
                "seed": body.get("seed"),
                "trades": int(summary.get("trades", 0) or 0),
                "closed": int(summary.get("closed", 0) or 0),
                "wins": int(summary.get("wins", 0) or 0),
                "half_breakevens": 0,
                "liquidations": 0,
                "refilled": 0.0,
                # ⚠️ 저장된 판은 **모형을 모른다** — 옛 저널에는 그 값이 없다.
                #    지어내지 않고 비운다 (절대 규칙 #8).
                "funding": body.get("funding"),
                "wallet": 0.0,
                "topped_up": 0.0,
                "halted_at": None,
                "skim_pct": float(Decimal(str(body.get("skim_pct", 0))) * 100),
                "reserved": 0.0,
                "withdrawn": 0.0,
                "equity": float(summary.get("cash", 0) or 0),
                "win_rate": None if summary.get("win_rate") is None else float(summary["win_rate"]),
                "cash": float(summary.get("cash", 0) or 0),
                "seed_cash": float(seed_cash),
                "leverage": float(Decimal(str(body.get("leverage", 1)))),
                "return_pct": float(summary.get("return_pct", 0) or 0),
            }
        )
    return rows


@router.get("/sessions")
async def sessions() -> dict[str, Any]:
    """모의 라이브 전부 — **메모리에 살아 있는 것 + 저널에 남은 것**.

    Returns:
        `{sessions: [...]}` — 최근 시작한 것이 앞이다.
    """
    rows = [{**_summary(key, live), "stored": False} for key, live in SESSIONS.items()]
    rows += _journaled()
    rows.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    # 🔴 **감시자가 본 것을 같이 낸다** (T20 ③). 로그에만 적으면 2026-08-19 를 그대로
    #    반복한다 — 판 2개가 죽었는데 화면에는 목록에서 사라진 것으로만 보였다.
    #
    # ⚠️ 목록 안에 못 넣는다. 죽은 판은 **목록에 없는 것**이 증상이므로, 행에 매달면
    #    바로 그 경우에 사라진다.
    # ⭐ **고아 포지션도 같은 배너로 낸다** (2026-08-20). 판이 죽은 것과 포지션이 남은
    #    것은 사람에게 같은 일이다 — *"아무도 관리하지 않는 돈이 거래소에 있다"*.
    # ⭐ **대조 결과도 같은 배너로 낸다** (2026-08-30). 사람에게는 "판이 죽었다" ·
    #    "고아 포지션" · "원장이 갈렸다" 가 전부 같은 일이다 — *아무도 관리하지 않는
    #    돈이 거래소에 있다*. 새 화면을 만들면 그중 하나만 보게 된다.
    now = datetime.now(UTC)
    watch = [*WATCHED.values(), *ORPHANS.values(), *(item.as_watch(now) for item in FOUND)]
    # 🔴 **같은 방향 동시 노출을 센다** (T24 ③). 코인은 상관 0.7+ 라 같은 방향
    #    N판이 사실상 한 포지션 N배다 — 실측: 동시 숏 6건 = 실효 5.3판. 예산 합은
    #    check_funding 이 보는데 방향 노출은 아무도 안 봤다. 막지 않는다 — 보인다.
    exposure: dict[str, int] = {}
    for live in SESSIONS.values():
        held = live.session.position
        if held is not None:
            exposure[held.direction.value] = exposure.get(held.direction.value, 0) + 1
    # 🔴 **건강할 때도 보여야 한다** (2026-08-30). 대조는 갈린 것이 있을 때만 배너로
    #    떴는데, 그러면 정상일 때 화면이 조용하고 **루프가 죽어도 똑같이 조용하다** —
    #    사람이 이 안전장치를 믿을 근거가 없다. 목록 응답에 실어 늘 보이게 한다.
    #    (새 요청을 만들지 않는다 — 화면은 이 응답을 이미 주기적으로 받는다)
    at = RECON.at
    return {
        "sessions": rows,
        "watch": sorted(watch, key=lambda item: item["run"]),
        "exposure": exposure,
        "reconcile": {
            "at": None if at is None else at.isoformat(),
            "period_s": RECONCILE_S,
            # ⚠️ 낡음 = **루프가 멈췄다**. 옛 결과를 현재처럼 보여 주는 것이 최악이다.
            "stale": at is None or (datetime.now(UTC) - at).total_seconds() > RECONCILE_S * 3,
            "blocked": sorted(RECON.blocked),
            "findings": len(FOUND),
            # 계수기 — 손절이 다 못 채워진 채 끝난 횟수 (경보 아님 · 화면은 조용히 적는다).
            "partial": dict(sorted(RECON.partial.items())),
        },
    }


@router.post("/resume/{key}")
async def resume(key: str) -> dict[str, Any]:
    """저널에 남은 판을 **다시 세워** 이어서 걸어간다.

    Args:
        key: 세션 id (저널 파일 이름).

    Returns:
        상태.

    Raises:
        HTTPException: 저널이 없거나 읽을 수 없으면 404.

    Note:
        🔴 **되짚어서 복원한다.** 결정론 코어이므로(절대 규칙 #5) 같은 봉인·같은
        설정으로 커서까지 다시 걸어가면 **같은 원장**이 나온다 — 기록을 역직렬화해서
        내부 상태를 흉내 내는 것보다 정직하고, 어긋나면 그 자체가 결함 신호다.

        ⚠️ 그래서 **시간이 걸린다.** 7일치면 수천 걸음이라 수 분이 든다. 목록은
        그동안 `복원 중` 으로 뜬다.
    """
    if key in SESSIONS:
        return _state(key)
    path = JOURNAL_ROOT / f"{key}.json"
    if not path.exists():
        raise HTTPException(404, f"{key} 저널이 없다 — 지워졌거나 다른 폴더에 있다")
    body = _read_journal(path)
    if body is None:
        raise HTTPException(404, f"{key} 저널을 읽을 수 없다 — 파일이 깨졌다")
    if body.get("live"):
        # 🔴 라이브는 **되짚어서 복원할 수 없다.** 결정론 복원은 봉인 구간을 다시
        #    걸어가는 방식인데(절대 규칙 #5) 라이브에는 고정된 끝이 없다 — 저널의
        #    `seal.end` 는 그 순간의 커서일 뿐이고, 그 사이 거래소에는 새 봉이 쌓인다.
        #
        #    ⛔ 조용히 백테스트로 되살리지 않는다. 그러면 실계좌 포지션을 든 판이 페이크
        #    원장으로 부활해서, 화면은 도는데 거래소와 아무 관계가 없다 (절대 규칙 #8).
        raise HTTPException(
            409,
            f"{key} 는 라이브 세션이다 — `/resume` 으로는 되살릴 수 없다. "
            "라이브 탭에서 새로 띄운다 (봉인 구간이 없어 결정론 복원이 성립하지 않는다)",
        )
    seal: dict[str, Any] = body.get("seal") or {}
    payload: dict[str, Any] = {
        "playbook": body.get("playbook_id") or default_playbook(),
        "symbol": body.get("symbol", "KRW-BTC"),
        "market": body.get("market", Market.UPBIT.value),
        "cash": body.get("seed_cash", "10000000"),
        "leverage": body.get("leverage", 1),
        "skim": float(Decimal(str(body.get("skim_pct", 0))) * 100),
        "days": int(body.get("days", 7)),
        "start": seal.get("start"),
        "flags": body.get("flags"),
        "applied": body.get("applied"),
        # ⭐ 되살린 판도 기본 배속으로 돈다 — 저널은 배속을 안 담는다(담을 이유도 없다.
        #    원장에 영향이 없으므로 재현에 필요한 값이 아니다).
        "speed": DEFAULT_SPEED,
    }
    # ⭐ **처음부터 다시 걸어간다.** 결정론 코어라 같은 봉인·같은 설정이면 같은
    #    원장이 나오고, 커서를 지나 그 뒤로 이어진다. 기록을 역직렬화해 내부 상태를
    #    흉내 내는 것보다 정직하며, 어긋나면 그 자체가 결함 신호다 (절대 규칙 #5).
    payload["session_id"] = key
    return await _start(payload, request=None)


async def _sweep(symbol: str) -> int:
    """그 종목의 **주인 없는 조건부·줄이는 주문**을 거둔다.

    Args:
        symbol: 계약 이름.

    Returns:
        거둔 건수. 못 거뒀거나 포지션이 살아 있으면 0.

    Note:
        🔴 러너가 없을 때 쓰는 경로다 — API 재시작 뒤에 판을 지우는 것이 **가장 흔한**
        삭제 모양이고, 예전에는 이 경로가 아무것도 안 치웠다.

        ⛔ **삭제를 막지 않는다.** 어댑터가 없거나 조회가 실패해도 삭제는 끝나야 한다
        (규칙 #8-1 — 로그·조회 실패가 정리 행동을 막지 않는다).
    """
    # ⚠️ 어댑터 획득은 콘솔과 **같은 자리**를 쓴다 — 여기서 새로 만들면 절대 규칙 #0
    #    (게이트가 어댑터 획득을 독점한다)을 우회하는 두 번째 경로가 생긴다.
    from updown.apps.api.exchange import (
        _instrument,  # pyright: ignore[reportPrivateUsage]
        _orders_adapter,  # pyright: ignore[reportPrivateUsage]
    )
    from updown.orchestration.leftovers import sweep as sweep_leftovers

    try:
        swept = await sweep_leftovers(_orders_adapter(), _instrument(symbol), why="RUN 삭제(저널)")
    except Exception as exc:
        _logger.warning(
            "live_sweep_failed",
            payload={"symbol": symbol, "error": str(exc)[:160], "note": "잔재가 남았을 수 있다"},
        )
        return 0
    return len(swept)


async def _close_from_journal(key: str) -> dict[str, Any]:
    """러너가 없을 때 **저널을 보고** 포지션을 닫는다.

    Args:
        key: RUN id.

    Returns:
        청산 결과. 저널이 없거나 라이브가 아니면 `{}`.

    Note:
        🔴 **API 재시작이 러너를 지운다.** 그 뒤에 RUN 을 지우면 닫을 수단이 없어
        조용히 넘어갔고, 포지션이 **손절도 없이** 거래소에 남았다 (2026-08-18 실측).

        ⚠️ 저널이 아는 것은 종목이다. 어댑터는 게이트에서 새로 받는다 (절대 규칙 #0).

        ⛔ 실패해도 예외를 내지 않는다 — 삭제는 끝나야 한다. 대신 **에러로** 남긴다.
        경고가 아니라 에러인 이유는, 이 실패가 곧 *"관리되지 않는 포지션이 생겼다"* 이고
        사람이 거래소 콘솔에서 손으로 닫아야 하기 때문이다 (절대 규칙 #8).
    """
    body = _read_journal(JOURNAL_ROOT / f"{key}.json")
    if body is None or not body.get("live"):
        return {}
    symbol = str(body.get("symbol", ""))
    market = str(body.get("market", ""))
    if not symbol or market != Market.GATE.value:
        return {}
    try:
        from updown.apps.api.exchange import close as console_close

        out = await console_close({"symbol": symbol})
        _logger.info(
            "live_closed_from_journal",
            payload={"session_id": key, "symbol": symbol, "status": out.get("status")},
        )
        return {"closed": True, "via": "journal", "symbol": symbol, "swept": await _sweep(symbol)}
    except HTTPException as exc:
        # 409 = 포지션이 없다. 그것은 실패가 아니다 — 닫을 것이 없었다.
        #
        # 🔴 **그런데 여기서 그냥 돌아갔다** (사용자 신고 2026-08-21: XRP). 포지션은 이미
        #    손절로 닫혔고 조건부만 남아 있었는데, *"닫을 것이 없다"* 를 *"치울 것이
        #    없다"* 로 읽고 나갔다. 고아 트리거는 24시간을 살아남아 **다음 판의 포지션을
        #    닫는다.**
        if exc.status_code == 409:
            return {"closed": False, "note": "포지션이 없었다", "swept": await _sweep(symbol)}
        _logger.error(
            "live_close_from_journal_failed",
            payload={"session_id": key, "symbol": symbol, "detail": str(exc.detail)[:200]},
        )
        # ⚠️ 종목을 실어 보낸다 — 판이 지워진 뒤 남는 것은 계약이다 (`_note_orphan`).
        return {"closed": False, "symbol": symbol, "error": str(exc.detail)[:200]}
    except Exception as exc:
        _logger.error(
            "live_close_from_journal_failed",
            payload={
                "session_id": key,
                "symbol": symbol,
                "error": str(exc)[:200],
                "note": "관리되지 않는 포지션이 남았다 — 거래소 콘솔에서 닫는다",
            },
        )
        return {"closed": False, "error": str(exc)[:200]}


async def _close_live_position(key: str) -> dict[str, Any]:
    """RUN 을 지우기 전에 **거래소 포지션을 닫는다**.

    Args:
        key: RUN id.

    Returns:
        청산 결과. 라이브가 아니거나 포지션이 없으면 `{}`.

    Note:
        🔴 사용자 질문 2026-08-18: *"내가 RUN 을 지웠을 때, 해당 포지션 증거금이 어떤식으로
        동작하는 지 좀 알려줘야 할 것 같은데?"*

        **예전 답: 아무 일도 안 했다.** 러너만 취소하고 포지션은 거래소에 남았다. 증거금은
        계속 잡혀 있고, 조건부 손절이 24시간에 만료되면 **손절 없는 포지션**이 됐다.

        ⚠️ 실패해도 예외를 내지 않는다 — 삭제는 끝나야 한다. 못 닫았으면 로그에 남고
        사람이 거래소에서 닫아야 한다 (절대 규칙 #8).
    """
    runner = LIVE_RUNNERS.get(key)
    if runner is None:
        # 🔴 **여기가 조용히 아무 일도 안 했다** (사용자 신고 2026-08-18: *"234 계약 포지션
        #    삭제했는데 거래소에서는 삭제가 된게 확실해? 카드에서는 계속 출력되고 있어서"*).
        #
        #    러너는 API 재시작으로 사라진다. 그러면 이 함수가 `{}` 를 돌려주고 삭제가
        #    그냥 진행됐다 — **포지션은 손절도 없이 거래소에 남았다.** 실측으로 확인:
        #    라이브 세션 0개인데 거래소에 234계약 · 조건부 0건.
        #
        # ⇒ **저널을 보고 직접 닫는다.** 러너 없이도 종목을 알 수 있고, 어댑터는
        #   게이트에서 새로 받으면 된다.
        return await _close_from_journal(key)
    try:
        # ⭐ **종목을 실어 보낸다** — 판이 지워진 뒤 남는 것은 계약이라, 그것이 없으면
        #   *"무엇이 안 닫혔나"* 에 답할 수 없다 (`_note_orphan`).
        return {"symbol": runner.instrument.symbol, **dict(await runner.close_all())}
    except Exception as exc:  # 삭제를 막지 않는다
        _logger.error(
            "live_close_failed",
            payload={
                "session_id": key,
                "symbol": runner.instrument.symbol,
                "error": str(exc)[:200],
                "note": "포지션이 거래소에 남아 있을 수 있다 — 사람이 확인한다",
            },
        )
        return {
            "closed": False,
            "symbol": runner.instrument.symbol,
            "error": str(exc)[:200],
        }


ORPHANS: dict[str, dict[str, str]] = {}
"""**닫으려다 실패한 포지션** — 판은 지워졌는데 거래소에 남은 것 (사용자 신고 2026-08-20).

🔴 **삭제가 조용히 반쪽만 됐다.** 실측:

```
02:10:56  SPCX_USDT -5638 시장가 reduce_only
          → 400 PRICE_TOO_DEVIATED (매수호가 109.16 vs 표시가 138.98 — 호가창이 비었다)
02:10:56  live_close_failed
```

로그에는 남았지만 화면은 *"지웠다"* 고 했고, 5638 계약이 관리자 없이 남았다. 사용자는
잔액이 371 로 보이는 것을 **손실로 읽었다** — 실제로는 435 가 그 포지션에 잡혀 있었다.

⇒ 여기 담아 `watch` 에 실어 보낸다. 콘솔이 이미 그 목록을 붉은 배너로 그린다.

⚠️ **판 id 가 아니라 종목으로 센다.** 판은 이미 지워졌으므로 판 id 로는 아무것도 못 찾고,
   남은 것은 계약이다.
"""


def _note_orphan(key: str, closed: dict[str, Any]) -> None:
    """청산이 실패했으면 **남겨서 화면이 말하게 한다**.

    Args:
        key: 지운 판 id.
        closed: `_close_live_position` 의 결과.

    Note:
        ⛔ **삭제를 막지는 않는다.** 못 닫는 이유가 거래소 쪽일 수 있고(호가창이 비었다),
        그때 판을 못 지우게 하면 사람이 할 수 있는 일이 없어진다.

        ⇒ 지우되 **잊지 않는다.** 이것이 §1.2.1 의 모양이다 — 리스크를 줄이는 행동은
          막지 않고, 못 한 것은 반드시 말한다.
    """
    if not closed or closed.get("closed") is not False or not closed.get("error"):
        return
    symbol = str(closed.get("symbol") or "")
    ORPHANS[symbol or key] = {
        "run": symbol or key,
        "code": "orphan_position",
        "detail": (
            f"판 {key} 를 지웠는데 **포지션을 못 닫았다** — {closed.get('error')}. "
            "거래소에 남아 있고 아무도 관리하지 않는다. 콘솔의 포지션 표에서 확인한다"
        ),
        "at": datetime.now(UTC).isoformat(),
    }
    _logger.error("live_orphan_position", payload=ORPHANS[symbol or key])


async def _drop_one(key: str) -> None:
    """RUN 하나를 접는다 — 메모리·저널·**DB 셋 다**.

    Args:
        key: RUN id.

    Note:
        🔴 **저널까지 지워야 실제로 사라진다** (사용자 지적 2026-08-17: 지우려는데
        `404 Not Found`). 예전에는 메모리 등록부만 봐서, 저널에만 남은 RUN 을 지우려
        하면 *"그런 세션 없다"* 로 거절했다 — 목록에는 보이는데 지울 수 없었다.

        ⚠️ 러너를 **먼저** 취소하고 등록부에서 뺀다. 순서가 반대면 러너가 한 바퀴 더
        돌면서 이미 없는 세션을 찾는다.

        ⛔ 없는 키를 조용히 넘긴다 — 지우려는 것이 이미 없으면 목적은 달성된 것이다.
        여기서 404 를 내면 목록 정리가 중간에 멈춘다.

        🔴 **라이브는 포지션까지 닫는다** (`_drop_live`). 러너만 취소하면 거래소에
        **아무도 관리하지 않는 포지션**이 남는다 — 조건부 손절은 24시간에 만료되므로
        그 뒤로는 손절도 없고, 증거금은 계속 잡혀 있으며, 다음 RUN 이 같은 종목이면
        그 포지션에 **얹힌다** (Gate 는 계약당 포지션이 하나다).
    """
    live = SESSIONS.pop(key, None)
    if live is not None and live.runner is not None:
        live.runner.cancel()
    LIVE_RUNNERS.pop(key, None)
    path = JOURNAL_ROOT / f"{key}.json"
    path.unlink(missing_ok=True)
    # 🔴 **닻을 닫는다** (T16 ②). 안 닫으면 같은 종목·매매법으로 다시 띄울 때 지운 판을
    #    이어받아, 방금 지운 매매 목록이 되살아난다.
    #
    # ⛔ **행을 지우지 않는다.** 닫힌 판은 남아 과거 성적이 보존된다 — 지우면 "어제 뭘
    #    했나"에 답할 수 없고, 그 답이 이 표의 존재 이유다.
    if _store is not None:
        await _store.close(key, reason="사람이 RUN 을 지웠다")


@router.delete("/sessions/{key}")
async def drop(key: str) -> dict[str, Any]:
    """RUN 하나를 접는다 (메모리 + 저널).

    Args:
        key: RUN id.

    Returns:
        남은 목록.
    """
    # 🔴 **포지션을 먼저 닫는다.** 러너만 취소하면 거래소에 아무도 관리하지 않는
    #    포지션이 남고, 조건부 손절이 24시간에 만료되면 **손절 없는 포지션**이 된다.
    closed = await _close_live_position(key)
    _note_orphan(key, closed)
    await _drop_one(key)
    body = await sessions()
    if closed:
        # ⚠️ 청산 결과를 응답에 싣는다 — 조용히 닫으면 사람이 그것을 모른다.
        body["closed"] = closed
    return body


@router.post("/sessions/bulk")
async def bulk(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """여러 RUN 을 한 번에 지우거나 시작·중지한다 (사용자 요구 2026-08-17).

    Args:
        payload: `{action: "delete"|"start"|"stop", keys: [...]}`.
            `keys` 가 비어 있고 `all` 이 참이면 **전부**가 대상이다.

    Returns:
        남은 목록.

    Raises:
        HTTPException: 모르는 `action` 이면 400 — 조용히 아무것도 안 하지 않는다.

    Note:
        🔴 **한 번에 처리한다.** 목록이 111 개까지 불어난 적이 있는데, 화면이 하나씩
        지우면 요청이 111 번이고 그 사이 목록이 계속 바뀌어 무엇이 지워졌는지 알 수
        없다.

        ⚠️ `start` 는 저널에만 있는 RUN 을 **되살리지 않는다** — 되살리기는 봉을 다시
        받고 처음부터 걸어가는 무거운 일이라 사람이 하나씩 눌러 확인해야 한다
        (`/resume`). 여기서는 메모리에 있는 것의 일시정지만 푼다.
    """
    action = str(payload.get("action") or "")
    raw: object = payload.get("keys") or []
    keys: list[str] = (
        [str(item) for item in cast("list[object]", raw)] if isinstance(raw, list) else []
    )
    if payload.get("all"):
        keys = [*SESSIONS, *(path.stem for path in JOURNAL_ROOT.glob("*.json"))]
    if action == "delete":
        for key in keys:
            # 🔴 일괄 삭제도 포지션을 닫는다 — 한쪽만 닫으면 "삭제" 의 뜻이 두 가지가 된다.
            # ⚠️ **실패도 일괄에서 똑같이 남긴다.** 한 건씩 지울 때만 잡히면, 정작
            #    여러 판을 한 번에 지우는 순간(가장 흔하다)에 조용히 새어 나간다.
            _note_orphan(key, await _close_live_position(key))
            await _drop_one(key)
    elif action in {"start", "stop"}:
        paused = action == "stop"
        for key in keys:
            live = SESSIONS.get(key)
            if live is None:
                continue
            live.session.paused = paused
            if not paused and not live.running and not live.session.finished:
                live.runner = asyncio.create_task(_walk(key), name=f"walk-{key}")
    elif action == "speed":
        # 🔴 **배속을 목록에서 바꾼다** (사용자 요구 2026-08-17: *"진행 배수도 메인
        #    페이지에서 조절할 수 있게 해줘. 삭제 시작, 중지 쪽에 말야"*).
        #
        #    ⭐ 여기서 한 번에 처리하는 이유는 삭제·시작·중지와 같다 — 판마다
        #      `/control` 을 부르면 요청이 판 수만큼이고, 그 사이 목록이 바뀌어
        #      무엇이 적용됐는지 알 수 없다.
        #
        #    ⚠️ 원장은 안 바뀐다. 배속은 `step()` 을 얼마나 자주 부르나일 뿐
        #      봉을 건너뛰지 않는다 (절대 규칙 #5).
        speed = max(1, int(payload.get("speed", DEFAULT_SPEED)))
        for key in keys:
            live = SESSIONS.get(key)
            if live is not None:
                live.speed = speed
    else:
        raise HTTPException(400, f"모르는 동작이다: {action!r} — delete·start·stop·speed 중 하나")
    return await sessions()


def _only(key: str, frame: str | None) -> Timeframe:
    """작도할 시간축 하나를 정한다.

    Args:
        key: 세션 id.
        frame: 프론트가 보고 있는 시간축. 없으면 플레이북 진입 TF.

    Returns:
        시간축.

    Note:
        ⭐ **하나로 좁히는 것이 성능의 핵심이다.** 5개를 전부 작도하면 응답마다 작도가
        5벌 돌고, 8배속에서 봉 갱신이 눈에 띄게 끊긴다.
    """
    live = _live(key)
    picked = live.session.playbook.timeframe
    if frame:
        try:
            picked = Timeframe(frame)
        except ValueError:
            picked = live.session.playbook.timeframe
    # 🔴 **보는 축을 러너에게 알린다** (2026-08-29 요율 한도 사고). 러너는 급전의
    #    모든 축을 계속 데우고 있었는데, 사람은 그중 하나를 본다 — 계측이 붙고 나서야
    #    숫자가 나왔다: 한도의 **156%** (`used 3747 / limit 2400`).
    #
    #    ⇒ 판정 축은 언제나 데우고, **보기용은 이 알림이 있을 때만** 데운다.
    #
    # ⛔ 알림이 실패해도 화면을 막지 않는다 — 그러면 축이 좀 늙을 뿐이고, 그것은
    #    조회가 아예 안 되는 것보다 훨씬 낫다 (규칙 #8-1).
    runner = LIVE_RUNNERS.get(key)
    if runner is not None:
        with contextlib.suppress(Exception):
            runner.watch(picked)
    return picked


def _live(key: str) -> Live:
    """세션을 꺼낸다.

    Raises:
        HTTPException: 없는 세션이면 404.
    """
    live = SESSIONS.get(key)
    if live is None:
        raise HTTPException(404, f"세션 {key} 가 없다 — 서버가 재시작됐을 수 있다")
    return live


def _chart(live: Live, at: datetime | None, only: Timeframe | None) -> list[dict[str, Any]]:
    """그 시점의 시간축별 화면 — **점검기와 같은 계산·같은 페이로드**.

    Args:
        live: 세션.
        at: 기준 시각. 되감기면 과거.
        only: 이 시간축만 작도한다. None 이면 전부.

    Returns:
        `frames` 목록.

    Note:
        🔴 **보고 있는 시간축 하나만 그린다.** 5개를 전부 작도하면 응답마다 작도·셋업
        탐지가 5벌 돌아 8배속에서 봉 갱신이 끊긴다 — 화면은 어차피 하나만 그린다.

        ⚠️ 시간축을 바꾸면 프론트가 다시 부른다. 그것이 5벌을 매번 계산하는 것보다
        훨씬 싸다.
    """
    session = live.session
    # 🔴 **같은 봉 수면 같은 그림이다 — 다시 그리지 않는다** (실측 2026-08-17).
    #
    #    걸음 1회가 293ms 인데 `/state` 1회가 **3,366ms** 였다 (80% 지점, 15m 1,111봉).
    #    작도가 매번 레벨 원장을 처음부터 다시 쌓고 셋업을 다시 탐지한다 — 러너가 이미
    #    계산한 것을 화면이 또 계산한 것이다.
    #
    #    화면은 200ms 마다 당기려 하는데 응답이 3.4초라, 그 사이 진행된 봉이 한꺼번에
    #    나타난다. 사용자가 말한 *"16배속이든 32배속이든 뚝뚝 하는 형태"* 가 이것이다.
    #
    #    ⭐ 봉은 **덧붙기만 한다**(`feed.view` 가 닫힌 봉만 낸다). 그래서 `봉 수` 가
    #      완전한 열쇠이고, 재사용이 결과를 바꾸지 않는다 (절대 규칙 #5).
    #
    #    ⛔ 되감기(`at`)는 캐시하지 않는다 — 같은 봉 수라도 다른 시점이라 그림이 다르다.
    bars = -1
    if at is None and only is not None:
        bars = len(session.feed.judged(only))
        cached = live.chart.get(only)
        # 🔴 **봉 수만으로는 재생 중에 캐시가 아무 일도 안 한다** (사용자 지적
        #    2026-08-17: *"진행하다 점점 느려지더니 결국 거의 동작을 안할 정도"*).
        #
        #    재생 중에는 봉이 **매 걸음** 늘어나므로 열쇠가 매번 달라지고, 그래서 폴링
        #    하나하나가 3~5초짜리 전체 작도를 했다. 러너가 CPU 를 계속 쓰는 와중이라
        #    응답이 더 늘어지고, 화면은 계속 "로딩" 으로 보인다.
        #
        #    ⇒ **시간으로도 조인다.** 봉이 늘었어도 마지막 작도가 `CHART_TTL` 안이면
        #      그 그림을 다시 준다.
        #
        #    ⚠️ **대가: 차트가 최대 1초 낡는다.** 커서·대시보드·매매 로그는 매번
        #      최신이므로, 어긋나는 것은 캔들과 도형뿐이다. 사람이 눈으로 따라가는
        #      도구에서 1초 지연은 멈춤보다 낫다 — 사용자가 원한 것이 부드러움이다.
        #
        #    ⛔ 되감기(`at`)에는 안 쓴다. 그때는 정확한 시점의 그림이어야 한다.
        if cached is not None:
            drawn_bars, drawn_at, chart = cached
            fresh = time.monotonic() - drawn_at < CHART_TTL
            if drawn_bars == bars or fresh:
                return chart

    views: list[FrameView] = []
    loaded: dict[Timeframe, list[Candle]] = {}
    for frame in session.feed.timeframes:
        if only is not None and frame is not only:
            continue
        # 🔴 **라이브 차트는 커서가 아니라 지금까지 그린다** (2026-08-18 실측).
        #
        #    `view()` 는 커서까지만 준다. 커서는 **진입 축(15m) 봉이 마감될 때만**
        #    움직이므로, 10초봉은 받아 놓고도 최대 15분 동안 화면에 안 나타났다 —
        #    사용자에게는 *"축 진행 자체가 멈춘 것"* 으로 보였다. 봉은 들어오고 있었고
        #    **보여 주지 않았을 뿐이다.**
        #
        # ⛔ **판정은 그대로 `view()` 를 쓴다.** 판정이 커서 밖을 보면 미래 참조이고,
        #   그것은 성적을 통째로 무의미하게 만든다 (절대 규칙 #5).
        #
        # ⚠️ 그래서 **화면과 판정이 다른 봉을 본다.** 라이브에서만 참이고 의도된 것이다 —
        #   사람은 지금을 보고, 판정은 마감된 것만 본다.
        #
        # ⚠️ 되감기(`at`)에는 안 쓴다. 그때는 정확히 그 시점의 그림이어야 한다.
        # 🔴 **능력 검사를 하지 않는다** (2026-08-19 회귀로 배웠다). 예전에는
        #    `hasattr(feed, "display")` 로 라이브인지 물었는데, T15 에서 그 메서드를
        #    `observed` 로 **이름만 바꾸자 검사가 조용히 항상 거짓**이 됐다 — 차트가
        #    다시 커서에 막혔고, 커서는 진입 축(15m) 봉이 마감될 때만 움직이므로 하위
        #    축이 최대 15분 동안 얼어 보였다. 고친 그 버그가 그대로 돌아온 것이다.
        #
        # ⇒ 이제 **묻지 않고 계약을 쓴다.** `observed` 는 두 급전에 다 있고, 봉인 급전은
        #   `judged` 와 같은 값을 낸다 (봉인 구간에는 "지금" 이 없다). 그래서 분기가
        #   필요 없다 — 분기가 없으면 이름을 바꿔도 조용히 안 깨진다.
        #
        # ⚠️ 되감기(`at`)만 판정용 보기를 쓴다. 그때는 **정확히 그 시점**의 그림이어야
        #    하고, `observed` 는 시점을 안 받는다.
        rows: list[Candle] = (
            list(session.feed.observed(frame))
            if at is None
            else list(session.feed.judged(frame, at=at))
        )
        if not rows:
            continue
        # 🔴 **판정과 같은 봉을 준다.** 전에는 마지막 200봉만 넘겨서, 화면은 200봉으로
        #    레벨을 쌓고 세션은 봉인 안 전체(800봉)로 쌓았다 — 실측 스냅샷에서 화면
        #    셋업이 진입 121,900,000 인데 실제 주문은 115,942,000 이었다. 6% 아래의
        #    옛 레벨을 세션만 보고 있었던 것이다.
        #
        #    ⚠️ 이 프로젝트가 같은 사고를 세 번째 겪는다 — 화면(200봉)과 셋업(80봉),
        #    점검기 trend={}, 그리고 이번. **화면이 판정보다 적게 보면 안 된다.**
        views.append(build_frame(rows, frame, live.flags))
        loaded[frame] = rows
    # 🔴 **진입 축 기준선을 다른 축에 얹는다** (사용자 요구 2026-08-18: *"15분봉에서 잡힌
    #    전략 기준으로 동작하는 걸 10초봉에서 보던가 할 수는 있을 거 아냐"*).
    #
    #    축마다 자기 봉으로 따로 탐지하므로 하위 축에서는 아무것도 안 나왔다 — 실측:
    #      10s 800봉 = 가격 폭 0.654% → 박스 0건
    #      15m 800봉 = 가격 폭 4.768% → 박스 5건
    #    버그가 아니다. 0.65% 안에 유의미한 박스가 없는 것이 맞는 답이고, **질문이 달랐다.**
    #
    # ⚠️ 얹는 것은 **가격대뿐**이다 (`PROJECTED_FLAGS`). 추세선·채널은 기울기가 시간 축에
    #    묶여 있어 옮기면 다른 선이 되고, 그것은 투영이 아니라 조작이다.
    entry_frame = session.playbook.timeframe
    anchor = next((item for item in views if item.timeframe is entry_frame), None)
    if anchor is None and entry_frame in session.feed.timeframes:
        # 🔴 **화면이 한 축만 요청하면 앵커가 걸러져 나간다** (사용자 신고 2026-08-18:
        #    *"여전히 10s 에 투영이 안되고 있고"*). `only` 로 걸러진 `views` 에는 그 축
        #    하나만 남으므로 15m 을 못 찾고, 투영이 조용히 건너뛰어졌다.
        #
        # ⇒ 앵커는 **따로 만든다.** 응답에는 넣지 않는다 — 요청한 축만 돌려주는 계약을
        #   바꾸면 화면이 안 쓰는 프레임까지 매번 받는다.
        #
        # ⚠️ 비용이 있다: 축 하나를 더 계산한다. 그래도 15m 800봉은 이미 매 걸음 도는
        #    양이고, 투영이 조용히 안 되는 것보다 낫다 (절대 규칙 #8).
        anchor_rows = list(session.feed.judged(entry_frame, at=at))
        if anchor_rows:
            anchor = build_frame(anchor_rows, entry_frame, live.flags)
    if anchor is not None:
        views = [project(anchor, item) for item in views]
    # 🔴 추세는 **창이 아니라 봉인 안 전체 이력**으로 나온 값을 쓴다. 창 200봉으로
    #    다시 계산하면 판정이 `None` 이라 국면 게이트를 가진 셋업이 0건이 된다.
    trend = {frame: item for frame, item in session.context(at).trend.items() if frame in loaded}
    run = setup_layers.detect(
        session.instrument,
        at or session.cursor,
        loaded,
        {view.timeframe: view.bundle for view in views if view.bundle is not None},
        live.flags,
        trend,
    )
    views = [view.with_layers(run.layers.get(view.timeframe, [])) for view in views]
    views = [_live_only(view, session) for view in views]
    # 🔴 **트레일 청산선(SMA)을 얹는다** (사용자 요구 2026-08-24). full_ride 전략의 실제
    #    청산은 이 SMA 상향 트레일이라, 선을 그리면 봉마다 "어디서 빠져나가는지"가 보인다.
    #    ⭐ 세션이 트레일에 쓰는 그 `sma()` 를 그대로 쓴다 (규칙 #9 · 클라 재구현 금지) —
    #      진입 축(트레일 기준 축)에만 얹는다. 하위 축에 200봉 SMA 를 얹으면 다른 선이 된다.
    period = session.playbook.trail_ma
    if period is not None:
        entry_tf = session.playbook.timeframe
        views = [
            (view.with_layers([ma_layer(view, period)]) if view.timeframe is entry_tf else view)
            for view in views
        ]
    # 🔴 **추세강도(ADX)를 같이 얹는다** (사용자 요구 2026-08-30). 진입 문(롱 35 · 숏 20)도
    #    청산 문(31 · 16)도 전부 이 값이 정하는데 화면에 없었다 — 근거 글에는 인용되고
    #    차트에는 없으니, 사람이 *"왜 안 들어갔지"* 를 확인할 수단이 없었다.
    #
    # ⭐ 판정 축에만 얹는다 — 세션이 그 축의 닫힌 봉으로만 재기 때문이다. 하위 축에
    #    그리면 **다른 값**이 되고, 다른 값을 근거로 화면이 판정을 설명하게 된다.
    entry_tf = session.playbook.timeframe
    gates = adx_gates(session.playbooks)
    views = [
        (
            view.with_layers(
                [
                    adx_overlay(view),
                    adx_gate_layer(gates),
                    slope_layer(view, session.playbooks),
                    vol_layer(view, session.playbooks),
                    stance_layer(view, session, run),
                ]
            )
            if view.timeframe is entry_tf
            else view
        )
        for view in views
    ]
    drawn = ChartSnapshot(
        symbol=session.instrument.symbol,
        as_of=at or session.cursor,
        seed=live.seed,
        flags=live.flags,
        frames=tuple(views),
    ).to_dict()["frames"]
    if bars >= 0 and only is not None:
        live.chart[only] = (bars, time.monotonic(), drawn)
    return drawn


def _rule_number(books: tuple[Playbook, ...], key: str) -> Decimal | None:
    """구성원 룰들에서 파라미터 하나를 찾는다 — **처음 선언한 값**을 쓴다.

    Args:
        books: 세션이 굴리는 플레이북들.
        key: 파라미터 이름.

    Returns:
        찾은 값. 아무 룰도 선언 안 했으면 None.

    Note:
        ⚠️ 없으면 **None 이다.** 기본값을 지어내면 설정을 바꿔도 화면만 옛 값을 그리고,
        그것은 화면이 없는 규칙을 말하는 것이다 (§4.3.1 · 규칙 #8).
    """
    catalog = load_rules()
    for book in books:
        for name in book.setups:
            rule = catalog.get(name)
            if rule is None:
                continue
            value = rule.params.get(key)
            if value is not None:
                return Decimal(str(value))
    return None


def slope_layer(view: FrameView, books: tuple[Playbook, ...]) -> Layer:
    """**SMA 가 내려가고 있나** — 숏 진입의 두 번째 조건.

    Args:
        view: 판정 축 화면.
        books: 세션이 굴리는 플레이북들 (`slope_bars`·`period` 를 여기서 찾는다).

    Returns:
        `overlay.ma_slope` 레이어 — `{bars, now, before, pct}` 한 장.

    Note:
        🔴 **가격이 SMA 아래라고 숏이 아니다.** 급락 직후에는 가격이 한참 아래인데
        거기가 반등 자리일 수 있다 — 그래서 탐지기는 **SMA 선 자체가 하루(6봉) 전보다
        내려와 있을 것**을 더 요구한다 (`level >= earlier` 면 탈락 · T59).

        가격은 하루에도 널뛰지만 SMA200 은 천천히 움직이므로, 선의 방향이 곧
        *"진짜 하락 추세인가"* 다. 그런데 그 조건이 화면에 없어서 **숏을 왜 안 잡았는지**
        확인할 수 없었다.

        ⚠️ 탐지기와 **같은 자리**를 본다: 지금 값과 `slope_bars` 봉 전 값. 다른 봉을
        보면 화면이 판정을 반박한다.
    """
    bars = _rule_number(books, "slope_bars")
    period = _rule_number(books, "period")
    if bars is None or period is None:
        return Layer(SLOPE_FLAG, (), note="이 판에는 SMA 기울기 조건이 없다")
    step = int(bars)
    line = sma([candle.close for candle in view.candles], int(period))
    now = line[-1] if line else None
    before = line[-1 - step] if len(line) > step else None
    if now is None or before is None or not before:
        return Layer(SLOPE_FLAG, (), note=f"SMA{int(period)} 기울기 — 봉 부족")
    return Layer(
        SLOPE_FLAG,
        (
            {
                "bars": str(step),
                "now": str(now),
                "before": str(before),
                "pct": str((now / before - 1) * 100),
            },
        ),
        note=f"SMA{int(period)} 기울기 — {step}봉 전 대비. 숏은 이 값이 **음수**여야 열린다",
    )


def vol_layer(view: FrameView, books: tuple[Playbook, ...]) -> Layer:
    """**얼마나 크게 걸까** — 변동성 타게팅 승수 (T81).

    Args:
        view: 판정 축 화면.
        books: 세션이 굴리는 플레이북들 (`vol_target`·`vol_lookback` 을 여기서 찾는다).

    Returns:
        `overlay.vol_target` 레이어 — `{vol, target, lookback, mult}` 한 장.

    Note:
        🔴 **들어갈지가 아니라 얼마나 크게 들어갈지를 정한다.** 요즘 많이 흔들리면 작게,
        잠잠하면 크게 — `승수 = 목표 / 실현변동성`, 클램프 0.4~2.0.

        ⚠️ 1/ATR 사이징과 **다르다.** 짧은 ATR 로 크기를 정하면 강추세(=돈 되는 국면)에서
        수량이 줄어 OOS 0/5 로 무너진다 (T81 §8-D). 길고 매끄러운 실현변동성이라야
        부호가 맞다 — 그래서 화면도 `lookback` 을 같이 적는다. 값만 보면 어느 쪽인지
        구별할 수 없다.

        ⚠️ 변동성을 못 재면 **승수를 비운다.** 탐지기는 그때 아예 안 간다 (규칙 #8) —
        화면이 1 을 적으면 "평소대로 들어간다" 는 거짓말이 된다.
    """
    target = _rule_number(books, "vol_target")
    if target is None or target <= 0:
        return Layer(VOL_FLAG, (), note="이 판에는 변동성 타게팅이 없다 — 크기가 고정이다")
    back = _rule_number(books, "vol_lookback") or Decimal(120)
    measured = realized_vol([candle.close for candle in view.candles], int(back))[-1]
    if measured is None or measured <= 0:
        return Layer(VOL_FLAG, (), note="변동성을 못 쟀다 — 이 자리는 크기를 정할 근거가 없다")
    vol = Decimal(str(measured))
    mult = max(VOL_CLAMP[0], min(VOL_CLAMP[1], target / vol))
    return Layer(
        VOL_FLAG,
        (
            {
                "vol": str(vol),
                "target": str(target),
                "lookback": str(int(back)),
                "mult": str(mult),
            },
        ),
        note=(
            f"목표 {target}% / 실현 {vol:.2f}%({int(back)}봉) = 크기 {mult:.2f}배 "
            f"(클램프 {VOL_CLAMP[0]}~{VOL_CLAMP[1]})"
        ),
    )


def stance_layer(view: FrameView, session: Session, run: setup_layers.SetupRun) -> Layer:
    """**지금 어느 전략이 서 있나** — 결론 한 줄.

    Args:
        view: 판정 축 화면.
        session: 세션 — **플레이북이 실제로 받은 것**이 여기 있다.
        run: 셋업 탐지 결과. 탐지는 됐지만 안 받은 경우를 가르는 데만 쓴다.

    Returns:
        `overlay.stance` 레이어 — `{state, count, why}`.

    Note:
        🔴 **탐지된 것과 받은 것은 다르다** (2026-08-30 실측으로 잡았다). 처음 만들 때
        탐지 결과(`run`)만 세었더니 BTC 에서 *"진입 자리 1건"* 이라 적어 놓고 **계획선은
        하나도 안 그려졌다** — 사용자가 그 어긋남을 짚었다.

        셋업 레이어는 점검기와 같은 코드라 **플레이북 게이트를 안 거친다.** 그래서
        국면·시간축이 안 맞으면 탐지는 되는데 주문은 안 걸린다 (`_live_only` 가 그
        경우에 note 를 다는 바로 그 상황이다).

        ⇒ 결론의 출처는 **`session.look().proposals`** — 계획선을 그리는 그 값이다.
          같은 값을 봐야 화면이 스스로를 반박하지 않는다.

        상태는 셋이다:

            선다        받았다 → 계획선이 그려진다
            안 받았다   탐지는 됐는데 플레이북이 안 받았다 (국면·시간축)
            현금        탐지도 없다 → 못 넘은 문을 적는다

        ⚠️ **"왜 없는가" 는 근사다.** 탐지기는 탈락 이유를 돌려주지 않으므로 못 넘은
        문을 되짚어 적는다. 단정하면 진짜 이유가 다를 때 사람을 잘못 이끈다.
    """
    taken = len(session.look().proposals)
    if taken:
        return Layer(
            STANCE_FLAG,
            ({"state": "선다", "count": str(taken)},),
            note="플레이북이 받았다 — 진입·손절·목표선이 차트에 그려진다",
        )
    made = [item for layer in run.layers.get(view.timeframe, []) for item in layer.shapes]
    if made:
        # 🔴 여기가 예전에 "진입 자리 1건" 으로 잘못 적히던 자리다.
        return Layer(
            STANCE_FLAG,
            (
                {
                    "state": "안 받았다",
                    "count": str(len(made)),
                    "why": "탐지는 됐지만 플레이북이 안 받았다 — 국면·시간축이 안 맞는다",
                },
            ),
            note="탐지 ≠ 주문. 계획선이 없는 것이 맞다",
        )
    return Layer(
        STANCE_FLAG,
        ({"state": "현금", "why": " · ".join(_why_flat(view, session.playbooks)) or "조건 미상"},),
        note="이 봉에서는 어느 전략도 서지 않았다 — 그래서 계획선이 없다",
    )


def _why_flat(view: FrameView, books: tuple[Playbook, ...]) -> list[str]:
    """**못 넘은 문**을 사람 말로 되짚는다 (근사).

    Args:
        view: 판정 축 화면.
        books: 세션이 굴리는 플레이북들.

    Returns:
        못 넘은 문 설명들. 다 넘었는데도 셋업이 없으면 빈 목록.

    Note:
        ⚠️ **탐지기의 탈락 이유가 아니다.** 같은 값을 보고 되짚는 것이라 순서·우선순위가
        다를 수 있다 — 그래서 단정하지 않고 나열한다.

        ⭐ 종목 제한을 먼저 본다. 캐시캐리는 `symbols` 로 **BTC·ETH 전용**인데
        (알트에는 드리프트라는 원료가 없다 · T66 §4g-1), 그 사실이 화면에 없어서
        *"캐리도 찍혀야 하는 거 아니냐"* 는 물음이 나왔다.
    """
    catalog = load_rules()
    closes = [candle.close for candle in view.candles]
    if not closes:
        return ["봉이 없다"]
    out: list[str] = []
    for book in books:
        for name in book.setups:
            rule = catalog.get(name)
            if rule is None:
                continue
            only = str(rule.params.get("symbols") or "")
            if only and view.candles[-1].instrument.symbol not in {
                x.strip() for x in only.split(",")
            }:
                out.append(f"{name}: 대상 종목 아님({only})")
                continue
            out.extend(_gate_misses(rule, closes, view))
    return out


def _gate_misses(rule: RuleConfig, closes: list[Decimal], view: FrameView) -> list[str]:
    """룰 하나가 **못 넘은 문**들 — 이평 위치 · 추세강도 · 기울기."""
    period = rule.params.get("period")
    if period is None:
        return []
    level = sma(closes, int(period))[-1]
    if level is None:
        return [f"{rule.rule_id}: SMA{int(period)} 봉 부족"]
    power = adx(
        [c.high for c in view.candles],
        [c.low for c in view.candles],
        [c.close for c in view.candles],
    )[-1]
    above = closes[-1] > level
    out: list[str] = []
    long_gate = rule.params.get("long_adx")
    if above and long_gate is not None and power is not None and power < Decimal(str(long_gate)):
        out.append(f"{rule.rule_id}: 롱 문 ADX {power:.1f} < {long_gate}")
    if not above:
        short_gate = rule.params.get("short_adx")
        if short_gate is not None and power is not None and power < Decimal(str(short_gate)):
            out.append(f"{rule.rule_id}: 숏 문 ADX {power:.1f} < {short_gate}")
    else:
        # ⚠️ 숏은 **SMA 아래**여야 한다 — ADX 만 보고 "숏 충족" 으로 읽는 것을 막는다.
        out.append(f"{rule.rule_id}: 숏은 SMA 아래여야 하는데 위다")
    return out


def _drawn_flags(books: tuple[Playbook, ...]) -> list[str]:
    """차트에 그릴 셋업 플래그 — **이 판이 실제로 굴리는 룰만**.

    Args:
        books: 세션이 굴리는 플레이북들 (번들이면 펼친 구성원 전부).

    Returns:
        `setup.<rule_id>` 목록. 순서는 선언 순서를 지킨다.

    Note:
        🔴 **예전에는 `primary_flags` 를 같이 넣었고, 그것이 다른 룰을 가리켰다.**
        어느 버전의 `primary_flags` 가 기본 셋업을 가리키는데 실제 셋업은
        그 변형이다 — 그리고 기본 셋업은 설정이 하나뿐인
        **무게이트·롱온리** 룰이다.

        그래서 차트가 **이 판이 절대 잡지 않을 진입**을 그렸다. 사람이 그것을 보고
        *"왜 여기서 안 들어갔지"* 를 물으면 답이 없다 — 애초에 그 룰로 돌지 않으니까.

        ⚠️ `primary_flags` 자체는 지우지 않는다. 그것은 **주력 계열 표시**(`applied`)로
        따로 쓰이고, 성과 귀속이 거기 걸려 있다 (§5.6.2). 여기서 바로잡는 것은
        *"무엇을 그리는가"* 뿐이다.

        ⭐ 구성원 **전부**를 돈다 — 번들이면 본대와 캐리가 둘 다 그려져야 한다.
        첫째만 보면 캐리 셋업이 화면에서 사라지고, 그러면 *"지금 어느 전략으로 대기
        중인가"* 를 차트가 답할 수 없다.
    """
    seen: list[str] = []
    for item in books:
        for name in item.setups:
            flag = f"setup.{name}"
            if flag not in seen:
                seen.append(flag)
    return seen


def adx_gates(books: tuple[Playbook, ...]) -> tuple[dict[str, str], ...]:
    """이 판이 실제로 쓰는 **ADX 문턱들** — 룰과 플레이북 양쪽에서 모은다.

    Args:
        books: 세션이 굴리는 플레이북들 (`session.playbooks`).

            ⚠️ 세션이 아니라 **플레이북 목록**을 받는다. 세션을 받으면 이 함수가
            세션 전체를 아는 것이 되고, 시험에서 세션을 통째로 세워야 한다 — 읽는
            것은 플레이북뿐이므로 그것만 받는다.

    Returns:
        `{key, label, value, kind}` 들. 선언되지 않은 문턱은 **뺀다** (0 으로 그리면
        "문턱이 0" 처럼 보인다).

    Note:
        🔴 **값을 여기서 짓지 않는다.** 진입 문은 룰 설정(`long_adx`·`short_adx`)에,
        청산 문은 플레이북(`adx_exit_*`)에 있다 — 두 곳에서 읽어 올 뿐이다. 화면용
        기본값을 만들면 설정을 바꿔도 차트만 옛 문턱을 그린다 (§4.3.1).

        ⚠️ 진입 문과 청산 문이 **다른 것이 정상이다** (35 vs 31). 유지 문턱을 진입과
        같이 두면 종가 1.6bp 차가 Wilder 평활을 타고 신호를 가른다 — 화면이 둘을
        같이 보여야 사람이 그 간격을 오해하지 않는다.
    """
    # 🔴 **`playbook` 하나가 아니라 `playbooks` 전부다.** 번들은 세션에 들어오기 전에
    #    구성원으로 펼쳐지고(T68), `session.playbook` 은 그중 **첫째**일 뿐이다 —
    #    번들 플레이북은 본대 + 보조 둘이라
    #    첫째만 보면 **캐리의 문턱이 통째로 사라진다.**
    #
    # ⇒ 그리고 그 둘을 가르는 것이 바로 ADX 다 (35 위면 본대 · 아래면 캐리).
    #   화면이 답해야 할 질문 *"지금 어느 전략으로 대기 중인가"* 가 여기서 나온다.
    #
    # ⚠️ `rules_config()` 를 쓰지 않는다 — 그것은 `rule_id -> enabled` 라 문턱 값이 없다.
    #    문턱은 설정 파일의 `params` 에만 있다 (§4.3.1: 임계값은 설정에서 주입한다).
    catalog = load_rules()
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(key: str, label: str, kind: str, value: object, owner: str) -> None:
        """같은 문턱이 두 번 실리지 않게 넣는다 — 값까지 같아야 중복이다."""
        tag = f"{key}:{value}"
        if tag in seen:
            return
        seen.add(tag)
        found.append(
            {"key": key, "label": label, "value": str(value), "kind": kind, "owner": owner}
        )

    for book in books:
        owner = book.playbook_id
        for name in book.setups:
            rule = catalog.get(name)
            if rule is None:
                continue
            for key, label, kind in (("long_adx", "롱 진입", "in"), ("short_adx", "숏 진입", "in")):
                value = rule.params.get(key)
                # ⚠️ 0 은 "문턱 없음" 이다 (탐지기가 `> 0` 으로만 본다) — 그리지 않는다.
                if value is None or not Decimal(str(value)):
                    continue
                _add(key, label, kind, value, owner)
        for key, label in (("adx_exit_long", "롱 청산"), ("adx_exit_short", "숏 청산")):
            value = getattr(book, key, None)
            if value is not None:
                _add(key, label, "out", value, owner)
        above = getattr(book, "adx_exit_above_long", None)
        if above is not None:
            # ⭐ 캐리의 **본대 인계** — 다른 문턱과 방향이 반대다 (강해지면 나간다).
            _add("adx_exit_above_long", "본대 인계", "up", above, owner)
    return tuple(found)


def adx_gate_layer(gates: tuple[dict[str, str], ...]) -> Layer:
    """문턱 가로선 레이어.

    Args:
        gates: `adx_gates` 결과.

    Returns:
        `overlay.adx_gates` 레이어. 문턱이 하나도 없으면 비고, note 가 그 사실을 말한다.
    """
    note = "" if gates else "이 판에는 ADX 문턱이 없다 — 추세강도가 진입·청산을 안 가른다"
    return Layer(ADX_GATE_FLAG, tuple(gates), note=note)


def _live_only(view: FrameView, session: Session) -> FrameView:
    """회고 재생을 걷어내고 **지금 걸려 있는 주문**을 얹는다.

    Args:
        view: 작도 결과.
        session: 세션.

    Returns:
        정리된 화면.

    Note:
        🔴 **한 화면에 "매매"가 두 벌이면 안 된다.** 점검기 레이어의 `N번째 매매` 는
        as-of 계획을 창 전체에 되굴린 **회고**이고, 원장의 매매는 이 세션이 실제로 낸
        것이다. 둘을 같이 그렸더니 차트에는 매매가 5건인데 원장은 0건이라 화면이
        스스로를 반박했다 (사용자 지적).

        ⇒ 모의 라이브에서는 회고를 **버리고** 실제 주문만 남긴다. 점검기에서는 그대로
        쓴다 — 거기서는 회고가 산출물이다.
    """
    shot = session.look()
    taken = len(shot.proposals)
    layers: list[Layer] = []
    for layer in view.layers:
        shapes = tuple(item for item in layer.shapes if item.get("kind") != "trade")
        # 🔴 도형을 뺐으면 **설명도 빼야 한다.** 점검기 note 의 "매매 N건" 은 회고
        #    재생 수인데, 화면에는 그 도형이 없고 원장은 다른 수를 말한다 — 실측
        #    스냅샷에서 note 가 "매매 6건" 인데 원장은 1건이었다.
        note = re.sub(r" · 매매 [^·]+", "", layer.note)
        # 🔴 **셋업 레이어는 플레이북 게이트를 안 거친다.** 점검기와 같은 코드라
        #    국면과 무관하게 그린다 — 그래서 차트에는 셋업이 떠 있는데 주문은 안 걸리고,
        #    사용자는 "왜 아이템이 안 생기지" 를 붙들게 된다 (실제로 그랬다).
        #
        #    ⇒ 화면이 그 차이를 **말한다** (절대 규칙 #8).
        if layer.flag.startswith("setup.") and shapes and taken == 0:
            note = "⚠️ 탐지는 됐지만 플레이북이 안 받았다 — 국면·시간축이 안 맞는다"
        layers.append(Layer(layer.flag, shapes, note=note))
    # 🔴 **이력을 통째로 낸다.** 예전에는 지금 걸린 주문 하나만 그려서, 걸어갈수록
    #    이전 분석이 화면에서 사라졌다 — 사용자 지적: *"이전에 분석했던 것들이 차트에서
    #    사라지는데, 그러지 않게 계속 반투명 형태로 남게 해줘."*
    #
    #    ⚠️ 봉 번호는 **이 창 기준**이다. 창 밖으로 밀려난 주문은 좌표가 없으므로
    #    `-1` 로 내보내고 화면이 세로선을 생략한다 (가격선은 그대로 남는다).
    # 🔴 **체결은 5m 시각이고 차트는 15m 봉이다.** 정확히 일치하는 봉이 없어
    #    `placed/opened/closed_index` 가 -1 로 떨어졌고, 그래서 진입·청산 세로선이
    #    그려지지 않았다 — 실측 스냅샷에서 손절 건의 opened/closed 가 둘 다 -1 이었다.
    #
    #    ⇒ **담고 있는 봉**을 찾는다 (그 시각 이하의 마지막 봉).
    stamps = [candle.ts for candle in view.candles]

    def at_index(moment: datetime | None) -> int:
        """그 시각을 담고 있는 봉 번호.

        Args:
            moment: 시각. None 이면 -1.

        Returns:
            봉 번호. 창 앞이면 -1 — 화면이 "표기 없음" 으로 읽는다.
        """
        if moment is None or not stamps or moment < stamps[0]:
            return -1
        return bisect_right(stamps, moment) - 1

    live = session.position
    orders = [*session.ledger.records]
    if live is not None and all(item.trade_id != live.trade_id for item in orders):
        orders.append(live)
    marks: list[dict[str, Any]] = []
    for item in orders:
        current = live is not None and item.trade_id == live.trade_id
        marks.append(
            {
                "kind": "filled",
                "state": item.outcome.value,
                # ⭐ 지금 살아 있는 주문만 진하게. 나머지는 반투명 이력이다.
                "current": current,
                # 🔴 체결·청산은 **일어난 사실**이라 진한 세로선, 대기는 예정이라 반투명.
                "settled": item.outcome in (Outcome.TAKE_PROFIT, Outcome.STOP_LOSS),
                "entry": str(item.entry),
                "stop": str(item.planned_stop),
                "first": str(item.planned_first),
                "target": str(item.planned_target),
                "placed_index": at_index(item.placed_at),
                "opened_index": at_index(item.opened_at),
                "closed_index": at_index(item.closed_at),
            }
        )
    if marks:
        note = f"주문 {len(marks)}건"
        if live is not None:
            note += f" · 지금 {live.outcome.value} 진입 {live.entry:,.0f}"
        layers.append(Layer(ORDER_FLAG, tuple(marks), note=note))
    return FrameView(
        timeframe=view.timeframe,
        candles=view.candles,
        layers=tuple(layers),
        note=view.note,
        bundle=view.bundle,
    )


async def _refresh_frame(key: str, frame: Timeframe | None) -> None:
    """**보고 있는 축**을 거래소에서 새로 받는다 (라이브만).

    Args:
        key: 세션 id.
        frame: 화면이 보고 있는 시간축. None 이면 아무것도 안 한다.

    Note:
        🔴 **웹소켓은 진입 축 하나만 구독한다.** 그래서 나머지 축은 시드 뒤로
        **얼어 있었다** (사용자 신고 2026-08-18: *"10초봉이 그냥 안움직이는데?"*).
        15m 은 진입 축이라 살아 있어서 눈에 안 띄었고, 10초봉은 10초마다 바뀌어야
        하므로 얼어 있는 것이 즉시 보였다.

        ⚠️ **요청이 올 때, 보고 있는 축만** 채운다. 9개 축을 다 구독하면 10초봉이 초당
        수십 프레임을 밀어 넣고 그 대부분은 아무도 안 보는 축이다.

        ⛔ 실패해도 화면을 죽이지 않는다 — 낡은 봉이라도 보여 주는 것이 빈 화면보다
        낫고, 얼마나 낡았는지는 `frame_ages` 가 말해 준다 (절대 규칙 #8).
    """
    runner = LIVE_RUNNERS.get(key)
    if runner is None or frame is None:
        return
    try:
        await runner.refresh(frame)
    except Exception as exc:
        _logger.warning(
            "live_frame_refresh_failed",
            payload={"session_id": key, "frame": frame.value, "error": str(exc)[:160]},
        )


def _state(key: str, at: datetime | None = None, only: Timeframe | None = None) -> dict[str, Any]:
    """세션 전체 상태 — 차트 · 원장 · 대시보드 (T13 ⑧⑩)."""
    live = _live(key)
    session = live.session
    book = session.ledger
    rate = book.win_rate
    mean = book.mean_achievement
    return {
        "session_id": key,
        "playbook": session.playbook.attribution,
        # 🔴 **추세추종은 고정 익절이 없다** (full_ride). 목표선은 진입+100R 짜리 먼
        #    자리표시자일 뿐이라(`RIDE_R` 매우 큼), 실제 청산은 트레일 손절이다. 화면이
        #    이 값을 알아야 가짜 목표선을 숨기고 손절을 "청산" 으로 그린다 (사용자 지적
        #    2026-08-24: *"익절선이 에베레스트"* — 44.04 는 100R 자리표시자였다).
        "full_ride": session.playbook.full_ride,
        "playbooks": [item.attribution for item in session.playbooks],
        "symbol": session.instrument.symbol,
        "cursor": session.cursor.isoformat(),
        "viewing": (at or session.cursor).isoformat(),
        "rewound": at is not None and at < session.cursor,
        "seal": {
            "start": session.feed.seal.start.isoformat(),
            "end": session.feed.seal.end.isoformat(),
        },
        "progress": session.feed.progress(),
        "finished": session.finished,
        "paused": session.paused,
        "speed": live.speed,
        "seed": live.seed,
        "timeframes": [frame.value for frame in session.feed.timeframes],
        "flags": list(live.flags),
        "applied": list(live.applied),
        # 🔴 대기 주문을 따로 낸다 — 화면이 "지금 뭘 기다리는지" 를 그린다.
        # 🔴 "왜 아무 일도 안 일어나나" 에 화면이 답할 수 있어야 한다.
        "gate": _gate(session),
        "position": _record(session.position),
        "frames": _chart(live, at, only),
        "dashboard": {
            "trades": len(book.records),
            "closed": len(book.closed),
            "wins": book.wins,
            # ⚠️ 승률이 반익반본으로 채워졌는지 보여야 한다 (§1-0s).
            "half_breakevens": book.half_breakevens,
            # 🔴 **결과별 건수를 그대로 낸다.** 합계만 보면 "목표까지 갔다" 와 "신호 보고
            #    털었다" 가 구별되지 않는다 — 사용자 의심이 그 지점이었다.
            "outcomes": dict(Counter(item.outcome.value for item in book.records)),
            # ⭐ 반익 사유 분포 — 전환 신호가 지배하면 그것은 **신호가 예민하다**는 뜻이지
            #    박스가 얇다는 뜻이 아니다. 처방이 달라진다.
            "half_reasons": dict(
                Counter(item.half_by.value for item in book.records if item.half_by is not None)
            ),
            "win_rate": None if rate is None else float(rate),
            "cash": float(book.cash),
            "seed_cash": float(book.seed_cash),
            "leverage": float(book.leverage),
            # ⭐ 격리 마진 청산을 모델링한다 (유지증거금 0.5%).
            "liquidation_modelled": True,
            "liquidations": book.liquidations,
            # 🔴 깡통 뒤 다시 넣은 돈 — 이것이 0 이 아니면 손익률만으로 못 읽는다.
            # 🔴 **어느 돈 모형으로 낸 값인가** (T14-1). 재투입 규칙이 다르면 누적
            #    손익률의 정의가 다르고, 라벨이 없으면 **모형 차이가 전략 차이로 읽힌다**
            #    (§1-0s 관측 규약).
            "funding": book.funding.value,
            # ⭐ 지갑 — `WALLET` 모형에서 **거래소 잔액에 대응한다**.
            "wallet": float(book.wallet),
            # ⭐ 지갑에서 증거금으로 채워 넣은 총액. `refilled`(밖에서 넣었다고 **가정한**
            #    돈)와 다르다 — 이쪽은 내 돈을 옮긴 것이라 손익률에서 빼지 않는다.
            "topped_up": float(book.topped_up),
            # 🔴 **멈췄으면 그 사실을 말한다.** 조용히 멈추면 "판정 0회" 와 구별되지 않는다.
            "halted_at": book.halted_at,
            "drawdown_pct": float(book.drawdown_pct),
            "max_drawdown_pct": float(book.max_drawdown_pct),
            "tripped_at": book.tripped_at,
            "drawdown_stop_pct": (
                None if book.drawdown_stop_pct is None else float(book.drawdown_stop_pct)
            ),
            "refilled": float(book.refilled),
            # ⭐ 수익 유보 — 굴리는 돈(`cash`)과 금고(`reserved`)를 **따로** 낸다.
            #    합치면 유보가 무슨 일을 했는지 화면이 못 말한다.
            "skim_pct": float(book.skim_pct * 100),
            "reserved": float(book.reserved),
            # ⭐ 금고에서 꺼내 쓴 돈 — 유보가 실제로 한 일이 이 값이다.
            "withdrawn": float(book.withdrawn),
            "equity": float(book.equity),
            "return_pct": float(book.return_pct),
            "mean_achievement": None if mean is None else float(mean),
            "detections": session.detections,
        },
        "log": [_record(item) for item in reversed(book.records)],
    }


def _gate(session: Session) -> dict[str, Any]:
    """지금 플레이북이 도는가, 안 돌면 무엇 때문인가.

    Args:
        session: 세션.

    Returns:
        `{active, trend, has_box, proposals}`.

    Note:
        🔴 **"셋업이 보이는데 매매가 없다"의 답이 여기 있다.** 차트의 셋업 레이어는
        점검기 코드라 국면 게이트를 안 거치는데, 실제 주문은 플레이북을 거친다. 그
        차이를 화면이 말하지 않으면 사람이 없는 버그를 쫓는다.
    """
    shot = session.look()
    trend = shot.trend.get(session.playbook.timeframe)
    # 🔴 **"안 돈다" 와 "돌았는데 자리가 없다" 는 다르다.** 전에는 후보 0 을 통째로
    #    "플레이북이 안 돈다" 로 적었는데, 국면이 맞는데도 그렇게 떠서 사람이 국면을
    #    의심하게 만들었다 — 고칠 곳이 완전히 다르다 (절대 규칙 #8).
    major = major_trend(shot.trend, session.playbook.timeframe)
    running = active_playbooks(
        list(session.playbooks),
        market_group=MarketGroup.of(session.instrument.market),
        timeframe=session.playbook.timeframe,
        trend=major,
        has_box=shot.has_box,
    )
    # 🔴 **막힌 것도 센다** (T26 ⑤). "자리가 없었다"(found 거짓)와 "자리는 있었는데
    #    막았다"(blocked > 0)를 화면이 구별 못 하면 보류가 고장으로만 읽힌다 (규칙 #8).
    held_back = [item for item in shot.proposals if item.blocked]
    return {
        "running": [item.attribution for item in running],
        "playbook": session.playbook.attribution,
        "playbooks": [item.attribution for item in session.playbooks],
        "regimes": [item.value for item in session.playbook.regimes],
        "trend": {frame.value: item.state.value for frame, item in shot.trend.items()},
        "entry_trend": None if trend is None else trend.state.value,
        "major": None if major is None else major.value,
        "has_box": shot.has_box,
        "proposals": len(shot.proposals),
        "blocked": len(held_back),
        "blocked_why": sorted({why for item in held_back for why in item.blocked})[:3],
        # 누적 카운터 (§1-0s) — "관망했다"와 "탐지가 안 돌았다"를 사후에 가른다.
        "seen_proposals": session.seen_proposals,
        "seen_blocked": session.seen_blocked,
        # 브레이커 (T22) — 발동했으면 언제부터 새 진입이 멈췄는지 화면이 말한다.
        "breaker_tripped_at": (
            None if session.breaker_tripped_at is None else session.breaker_tripped_at.isoformat()
        ),
        "loss_limit_pct": (None if session.loss_limit_pct is None else str(session.loss_limit_pct)),
        "active": len(running) > 0,
        "found": len(shot.proposals) > 0,
    }


def _record(item: TradeRecord | None) -> dict[str, Any] | None:
    """매매 기록 하나를 화면용으로 (T13 ⑧ 항목 그대로)."""
    if item is None:
        return None
    planned = item.planned_rr
    realized = item.realized_rr
    got = item.achievement
    gain = item.gain_pct
    return {
        "trade_id": item.trade_id,
        "playbook": item.playbook,
        "actor": item.actor.value,
        "direction": item.direction.value,
        "outcome": item.outcome.value,
        # 🔴 주문을 건 시각과 체결 시각은 다르다. 대기 중이면 체결 시각이 없다 —
        #    없는 것을 있는 척하면 화면이 "언제 샀나"를 거짓으로 말한다.
        "placed_at": item.placed_at.isoformat(),
        "opened_at": None if item.opened_at is None else item.opened_at.isoformat(),
        "closed_at": None if item.closed_at is None else item.closed_at.isoformat(),
        # ⭐ 반익이 나갔는지 — 나갔으면 손절이 본절로 올라가 있다.
        "half_at": None if item.half_at is None else item.half_at.isoformat(),
        # 🔴 **왜 반익했는지**까지 낸다 (사용자 요구 2026-08-17). 계획한 1차 익절에
        #    닿은 것과 전환 신호에 턴 것이 같은 라벨로 뭉개져 있었다.
        "half_by": None if item.half_by is None else item.half_by.value,
        "half_price": None if item.half_price is None else str(item.half_price),
        "entry_fills": [[str(price), str(ratio)] for price, ratio in item.entry_fills],
        "filled_ratio": str(item.filled_ratio),
        "entry": str(item.entry),
        "exit": None if item.exit_price is None else str(item.exit_price),
        "stop": str(item.planned_stop),
        # 🔴 **1차 익절이 빠져 있었다** (사용자 지적 2026-08-18: *"왜 이전에 뜨던
        #    1차 익절, 익절 등이 안뜨지?"*). 차트는 보유 계획을 `state.position` 에서
        #    받는데 그 값은 **러너가 살아 있을 때만** 온다. 없으면 매매 로그의 열린
        #    건으로 떨어지는데, 거기에 `first` 가 없어서 선 둘만 그려졌다.
        "first": str(item.planned_first),
        "target": str(item.planned_target),
        "gain_pct": None if gain is None else float(gain),
        "planned_rr": None if planned is None else float(planned),
        "realized_rr": None if realized is None else float(realized),
        "achievement": None if got is None else float(got),
        # 🔴 **왜 들어갔는가** (T16 ①). 화면에 근거가 없으면 사람이 복기할 때 차트를
        #    다시 눈으로 읽어야 하는데, 그것은 절대 규칙 #11 이 금지한 그 행위다 —
        #    규칙이 낸 값을 규칙이 낸 그대로 보여 준다 (§1-0s).
        #
        # ⛔ 비어 있는 것이 곧 결함은 아니다. `actor` 가 이어받음·사람이면 근거는
        #    원래 없다 — 그래서 `actor` 를 같이 낸다.
        "evidence": evidence_rows(item.evidence),
    }


@router.get("/state/{key}")
async def state(key: str, at: str | None = None, frame: str | None = None) -> dict[str, Any]:
    """지금 상태. `at` 을 주면 **되감아 본다** (T13 ⑤).

    Args:
        key: 세션 id.
        at: 되감을 시각 (UTC ISO). 커서보다 미래면 400.
        frame: 작도할 시간축. 안 주면 플레이북 진입 TF 하나만 그린다.

    Returns:
        상태.

    Raises:
        HTTPException: 봉인 위반이면 400.

    Note:
        🔴 되감기는 **보기만** 한다. 원장도 커서도 안 움직인다 — 사용자 확정대로
        일시정지 상태의 관찰이다.
    """
    moment = None if at is None else datetime.fromisoformat(at).astimezone(UTC)
    try:
        await _refresh_frame(key, _only(key, frame))
        return _state(key, moment, _only(key, frame))
    except SealBreachError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/step/{key}")
async def step(key: str, bars: int = 1, frame: str | None = None) -> dict[str, Any]:
    """봉을 앞으로 민다.

    Args:
        key: 세션 id.
        bars: 밀 봉 수. 배속이 이 값으로 들어온다.
        frame: 작도할 시간축.

    Returns:
        상태 + `stepped`(실제로 민 봉 수).

    Note:
        🔴 **배속이 커도 봉을 건너뛰지 않는다.** `bars` 만큼 `step()` 을 그 횟수만큼
        부른다 — 화면 갱신만 그 뒤에 한 번 한다 (T13 ⑤). 건너뛰면 같은 입력에 다른
        출력이 나온다 (절대 규칙 #5).
    """
    live = _live(key)
    # 🔴 **"한 봉" 은 일시정지 중에 쓰는 버튼이다.** 백그라운드 러너가 생긴 뒤로 재생은
    #    서버가 하고 화면은 보기만 하므로, 이 경로가 살아 있으려면 잠깐 정지를 풀어야
    #    한다. ⚠️ `await` 가 없어 이벤트 루프를 안 넘기므로 러너가 끼어들지 않는다.
    was_paused = live.session.paused
    live.session.paused = False
    moved = 0
    try:
        for _ in range(max(1, bars)):
            if live.session.step() is None:
                break
            moved += 1
    finally:
        live.session.paused = was_paused
    await _refresh_frame(key, _only(key, frame))
    body = _state(key, None, _only(key, frame))
    body["stepped"] = moved
    return body


@router.post("/control/{key}")
async def control(key: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """일시정지·배속·자동매매를 바꾼다 (T13 ⑤).

    Args:
        key: 세션 id.
        payload: `{paused, speed, auto}` 중 준 것만 바꾼다.

    Returns:
        상태.
    """
    live = _live(key)
    if "paused" in payload:
        live.session.paused = bool(payload["paused"])
    if "auto" in payload:
        live.session.auto = bool(payload["auto"])
    if "speed" in payload:
        live.speed = max(1, int(payload["speed"]))
    # ⚠️ 러너가 죽어 있으면 되살린다 — 예외로 끝났거나 서버가 이어받은 세션이다.
    #    ⛔ 끝난 세션은 되살리지 않는다. 봉인 밖으로는 못 간다.
    if not live.running and not live.session.finished:
        live.runner = asyncio.create_task(_walk(key), name=f"walk-{key}")
    return _state(key)


SNAPSHOT_DAYS = 7
"""스냅샷 기본 기간(일). 커서에서 **뒤로** 이만큼.

🔴 **미래는 안 담는다.** 봉인 이후를 넣으면 그 파일을 보는 순간 답을 본 것이 되고,
그것으로 규칙을 고치면 out-of-sample 이 아니게 된다 (절대 규칙 #11 · T13 ④).
앞을 보고 싶으면 **걸어가서** 다시 뜨는 것이 이 도구의 사용법이다.
"""


@router.get("/snapshot/{key}")
async def snapshot(
    key: str,
    days: int = SNAPSHOT_DAYS,
    frame: str | None = None,
    since: str | None = None,
    until: str | None = None,
    candles: bool = True,
) -> dict[str, Any]:
    """지금 상태를 **JSON 한 덩이**로 — 캡처 대신 이걸 붙여 넣는다.

    Args:
        key: 세션 id.
        days: 커서에서 뒤로 담을 일수. `since` 를 주면 무시된다.
        frame: 봉을 담을 시간축. 없으면 진입 TF.
        since: 담기 시작할 시각 (UTC ISO). **화면 시간 바에서 고른 구간**이 여기로 온다.
        until: 담기를 끝낼 시각. 커서보다 미래면 커서로 자른다.
        candles: 봉을 담을지. 끄면 판단 재료만 남아 훨씬 작다.

    Returns:
        세션 정보 · 게이트 · 계획 · 원장 · 레이어 · 캔들.

    Note:
        🔴 **화면을 캡처해 분석하던 것을 대체한다.** 이미지로는 값을 읽을 수 없어
        매번 재현 스크립트를 새로 짜야 했다 — 그 왕복이 이 도구의 병목이었다.

        ⛔ 커서 **이후는 담지 않는다** (`SNAPSHOT_DAYS` 주석).
    """
    live = _live(key)
    session = live.session
    only = _only(key, frame)
    # 🔴 **화면에서 고른 구간이 우선이다.** 시간 바로 범위를 좁히면 그 구간만 담긴다 —
    #    기본 7일치는 101KB 라 붙여넣기가 5만 자에서 잘렸다.
    start = (
        datetime.fromisoformat(since).astimezone(UTC)
        if since
        else session.cursor - timedelta(days=max(1, days))
    )
    # ⛔ 끝은 커서를 넘을 수 없다. 넘기면 봉인이 뚫린다.
    stop = min(
        datetime.fromisoformat(until).astimezone(UTC) if until else session.cursor,
        session.cursor,
    )
    rows = (
        [item for item in session.feed.judged(only) if start <= item.ts <= stop] if candles else []
    )
    body = _state(key, None, only)
    return {
        "taken_at": session.cursor.isoformat(),
        "window": {
            "from": start.isoformat(),
            "to": stop.isoformat(),
            "bars": len(rows),
        },
        "session": {
            key_: body[key_]
            for key_ in ("session_id", "playbook", "playbooks", "symbol", "seal", "seed", "flags")
            if key_ in body
        },
        "gate": body["gate"],
        "dashboard": body["dashboard"],
        "position": body["position"],
        "log": body["log"],
        "layers": [
            {"flag": layer["flag"], "note": layer["note"], "shapes": layer["shapes"]}
            for view in body["frames"]
            for layer in view["layers"]
        ],
        "timeframe": only.value,
        "candles": [candle_json(candle) for candle in rows],
    }


def _flag_names(payload: Mapping[str, Any]) -> list[str]:
    """띄우는 쪽이 보낸 플래그 이름들 — 없으면 빈 목록.

    Args:
        payload: 요청 본문.

    Returns:
        플래그 이름 목록.

    Note:
        ⚠️ 한 줄짜리를 함수로 뺀 이유는 **두 곳이 같은 값을 봐야 하기 때문**이다 —
        판 메타에 남기는 것과 주문 로그에 적는 것이 갈리면, 나중에 둘을 대조할 때
        어느 쪽이 사실인지 알 수 없다.
    """
    raw = payload.get("flags")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in cast("list[object]", raw)]


CUSTOM_BOOK = "custom"
"""차트 주문이 띄우는 **유일한** 플레이북 (사용자 확정 2026-08-30: (다)안).

⛔ `Custom_추세+박스권` 처럼 이름을 매번 만들지 않는다. 성과 귀속의 단위는
`attribution`(= `id@version`) 이고 그것이 **분모**다 — 이름이 조합마다 늘면 분모가
무한히 쪼개져 어떤 것도 표본을 못 채운다 (§5.6.2).

⇒ 이름은 하나, **어떤 플래그를 보고 잡았는지는 판의 메타**에 적는다.
"""


@router.post("/live/custom")
async def live_custom(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """차트에서 손으로 그은 계획으로 **판을 띄우고 그 자리에서 산다** (2026-08-30).

    Args:
        request: 요청 — 매매법·시장 권한을 본다 (T230 · T242). T250 전에는 빠져 있어 관문이
            안 걸렸다.
        payload: `{symbol, market, margin, leverage, short, entry, stop, first,
            target, flags, price_frame}` + 주식은 `shares`(정수 주 · 예산 대신).

    Returns:
        판 상태 + `confirm` (확정 내역). `session_id` 로 기존 RUN 화면이 그대로 열린다.

    Raises:
        HTTPException: 확정에 걸리면 400, 자격증명이 없으면 503.

    Note:
        🔴 **이 판의 주체는 사람이다** (사용자 확정): *"그냥 사람이 정하는대로 다
        들어가는 거야. run이지만 사실 그냥 추적을 위한 깡통을 생각하긴 했어."*

        그래서 `custom` 플레이북에는 셋업도 트레일도 재레버도 없다 — 러너가 하는 일은
        **집행·감시·기록**뿐이다. 진입가·1차 익절·최종 익절은 사람이 낸 값이 그대로
        원장에 들어간다.

        ⚠️ **딱 하나 바뀔 수 있는 것이 손절이다.** 청산 거리 밖의 손절은 절대 체결되지
        않으므로(T120: 청산난 판의 81~84%) 안쪽으로 당긴다. 그것은 사람의 뜻을 바꾸는
        것이 아니라 **닿을 수 없는 값을 닿는 값으로 옮기는** 것이고, 언제나 조이는
        방향이라 절대 규칙 #3 과 같은 사상이다. 바뀌면 `confirm.moved` 로 **말한다**.

        🔴 **확정은 `decision/risk/manual.confirm` 이 한다** — 화면의 `/analysis/validate`
        와 **같은 함수**다. 두 벌로 두면 화면이 "낼 수 있다" 한 계획을 주문 경로가 막고,
        사람은 왜인지 알 수 없다 (절대 규칙 #4).

        ⚠️ **수량은 예산 x 배율에서 나온다** — 여기서 계약수를 따로 만들지 않는다.
        `_send` 의 `contracts_for` 가 유일한 자리이고, 두 번째 사이징 경로를 만들면
        원장이 가정한 수량과 거래소에 나간 수량이 갈린다.
    """
    try:
        entry = Decimal(str(payload["entry"]))
        stop = Decimal(str(payload["stop"]))
        first = Decimal(str(payload["first"]))
        target = Decimal(str(payload.get("target") or payload["first"]))
        leverage = Decimal(str(payload.get("leverage", 1)))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise HTTPException(400, f"계획 값을 못 읽었다: {exc}") from exc
    market = Market(str(payload.get("market", Market.BINANCE.value)))
    short = bool(payload.get("short"))
    # ⭐ T250 — 주식 능력표: 배율 1 · 숏 없음 · 정수 주(예산 = 주수 x 진입가) · 장중만.
    #    코인은 그대로 지나간다.
    caps = capabilities_of(market)
    hours = None if caps.always_open else hours_of(load_calendar(), market, datetime.now(UTC))
    try:
        terms = stock_order_terms(
            caps,
            leverage=leverage,
            short=short,
            shares=payload.get("shares"),
            entry=entry,
            hours=hours,
        )
    except StockOrderRejectedError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    leverage = terms.leverage
    if terms.margin is not None:
        payload = {**payload, "margin": str(terms.margin), "leverage": str(terms.leverage)}

    # ① **돈이 걸리기 전에** 확정한다. 판을 먼저 띄우고 나서 막으면 아무것도 안 하는
    #    빈 판이 남고, 그 판은 화면에서 진짜 판과 구별되지 않는다.
    try:
        got = confirm(
            entry=entry,
            stop=stop,
            first=first,
            target=target,
            leverage=leverage,
            short=short,
            round_trip=load_cost_table(DEFAULT_CONFIG_PATH).for_market(market).round_trip_pct,
            settings=load_risk_settings(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not got.ok:
        raise HTTPException(400, " / ".join(got.blocked))

    # ② 판을 띄운다 — 셋업이 없으므로 **러너가 스스로 진입하는 일은 없다**.
    started = await _live_start(
        {
            **{
                key: value
                for key, value in payload.items()
                # ⛔ 계획 값은 판 설정이 아니다 — 그대로 흘리면 `_live_start` 가 모르는
                #   키를 받고, 나중에 같은 이름의 설정이 생기면 조용히 충돌한다.
                if key not in {"entry", "stop", "first", "target", "short"}
            },
            "playbook": CUSTOM_BOOK,
            "market": market.value,
        },
        request=request,
    )
    key = str(started["session_id"])

    # ③ 쓴 플래그는 **판 메타**(`custom_flags`)에 이미 들어갔다 — `_live_start` 가
    #    payload 를 그대로 받아 저장소에 남긴다. 여기서 또 적으면 두 벌이 된다.
    flags = _flag_names(payload)
    live = _live(key)

    # ④ 사람이 정한 값을 **그대로** 원장에 적는다. 손절만 ① 이 확정한 값이다.
    try:
        record = live.session.buy(
            price=entry,
            stop=got.stop,
            target=target,
            first=first,
            direction=Direction.SHORT if short else Direction.LONG,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    _logger.info(
        "chart_order_placed",
        payload={
            "session_id": key,
            "trade_id": record.trade_id,
            "symbol": str(payload.get("symbol")),
            "market": market.value,
            "direction": record.direction.value,
            "entry": str(entry),
            "stop_asked": str(stop),
            "stop_used": str(got.stop),
            "moved": got.moved,
            "first": str(first),
            "target": str(target),
            "leverage": str(leverage),
            "shares": terms.shares,
            "flags": flags,
            "rr": f"{got.rr:.2f}",
            "need_pct": f"{got.need_pct:.1f}",
            "note": "사람이 그은 계획 — actor=HUMAN, 러너는 집행·감시만 한다",
        },
    )
    body = _state(key)
    body["session_id"] = key
    body["live"] = True
    # ⚠️ 확정 내역을 **같이** 돌려준다. 손절이 옮겨졌는데 화면이 원래 값을 그대로
    #    그리고 있으면 그것이 거짓말이다 (절대 규칙 #8).
    body["confirm"] = as_json(got)
    return body


@router.post("/buy/{key}")
async def buy(key: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """사람이 산다 (T13 ④).

    Args:
        key: 세션 id.
        payload: `{price, stop, target, first, short}`. `first` 를 비우면 진입과
            목표의 한가운데로 잡는다 (걸어가기 화면의 옛 동작).

    Returns:
        상태.

    Raises:
        HTTPException: 이미 보유 중이면 400.
    """
    live = _live(key)
    raw_first = payload.get("first")
    try:
        live.session.buy(
            price=Decimal(str(payload["price"])),
            stop=Decimal(str(payload["stop"])),
            target=Decimal(str(payload["target"])),
            first=None if raw_first in (None, "") else Decimal(str(raw_first)),
            direction=Direction.SHORT if payload.get("short") else Direction.LONG,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _state(key)


@router.post("/sell/{key}")
async def sell(key: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """사람이 판다.

    Args:
        key: 세션 id.
        payload: `{price}`.

    Returns:
        상태.

    Raises:
        HTTPException: 보유 중이 아니면 400.
    """
    live = _live(key)
    try:
        live.session.sell(price=Decimal(str(payload["price"])))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _state(key)
