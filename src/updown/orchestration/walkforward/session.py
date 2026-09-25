"""모의 라이브 세션 — 봉 하나씩 앞으로 굴린다 (T13 ⑤⑨).

## 조립만 한다

봉인은 `sealed`, 기록은 `ledger`, 자리 판정은 `playbook_run.propose()` 가 한다. 여기서
새로 정하는 값은 **하나도 없다** (`orchestration/` 입주 조건).

## 🔴 배속은 렌더링만 건너뛴다

사용자 확정 — *"고배속에서 매매 전략 분석이 늦어질 시, 1배속 기준으로 괜찮다면
문제삼지 않음."*

```
✅ 허용   느려진다 · 화면 갱신을 미룬다
⛔ 금지   봉 분석을 건너뛴다
```

봉을 건너뛰면 **같은 입력에 다른 출력**이 나온다 (절대 규칙 #5). 그래서 배속은 이
파일에 없다 — 여기는 `step()` 한 번에 한 봉만 처리하고, 얼마나 자주 부를지는 부르는
쪽이 정한다. 배속이 결과를 바꿀 수 있는 자리를 애초에 안 만든다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from updown.analysis.detectors.base import MarketContext
from updown.analysis.detectors.registry import SetupRegistry
from updown.analysis.indicators import snapshot as indicator_snapshot
from updown.analysis.indicators.adx import adx
from updown.analysis.indicators.atr import atr as atr_series
from updown.analysis.indicators.ma import sma
from updown.analysis.indicators.macd import macd
from updown.analysis.indicators.reversal import (
    dragonfly,
    engulfing,
    gravestone,
    marubozu,
    reversal_confirmed,
    spinning,
)
from updown.analysis.playbook.types import Playbook
from updown.analysis.structures.box_range import SPAN_COVER, box_span
from updown.analysis.structures.level_book import build_levels, roles_at
from updown.analysis.structures.swing import SwingKind, find_pivots
from updown.analysis.trend.service import evaluate as trend_evaluate
from updown.common.costs import DEFAULT_CONFIG_PATH, MarketCosts, load_cost_table
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.reports import TrendDirection
from updown.common.domain.session import MarketCalendar
from updown.common.domain.setup import TradeSetup
from updown.common.domain.trade_tick import BarDelta
from updown.common.logging.setup import get_logger
from updown.decision.portfolio_rules import Grant
from updown.decision.sizing import DEFAULT_LEVERAGE_CAP, capped_stop, protect_stop, size_for
from updown.marketdata.ingest.delta_store import load_bar_deltas
from updown.marketdata.ingest.timeframes import interval
from updown.orchestration.playbook_run import Proposal, major_trend, propose
from updown.orchestration.walkforward.feed_protocol import Feed
from updown.orchestration.walkforward.fill_protocol import Filler
from updown.orchestration.walkforward.funding import settlement_boundaries
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    HalfBy,
    Ledger,
    Outcome,
    TradeRecord,
    evidence_rows,
    liquidation_price,
    new_trade_id,
    plan_fault,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.candle import Candle
    from updown.common.domain.reports import Indicators
    from updown.common.domain.trend import TrendState

TF_RANK: dict[Timeframe, int] = {
    Timeframe.M5: 0,
    Timeframe.M15: 1,
    Timeframe.H1: 2,
    Timeframe.H4: 3,
    Timeframe.D1: 4,
}
"""시간축의 굵기 순서.

🔴 **국면은 진입 TF 이상에서만 본다.** 그보다 잘은 축의 추세는 잡음이고, 실제로
5m 추세가 국면을 정하고 있었다 (`playbook_run.MAJOR_ORDER` 참고).

⚠️ 성능 문제이기도 하다. 추세 판정은 봉마다 피벗을 다시 찾는데(`find_pivots`), 5m 은
걸음마다 봉이 늘어 1,200봉짜리 계산이 **매 걸음** 돌았다 — 프로파일에서 한 걸음
8.5초 중 8.3초가 거기였다.
"""

CONSEC_CUT_2 = Decimal("0.7")
"""연속 손절 2회 뒤 진입 크기 배수 (consec_cut · 플레이북 선언).

CONSEC_CUT_3 과 함께 safe 변형의 OOS 측정에 들어간 값이다 — 바꾸면 새 플레이북
버전이다 (§5.6.2). ⚠️ playbooks.yml 필드 승격(①)은 safe 변형이 라이브 메인이 되는
시점으로 미룬다 (T63 — 애매하면 ③) — 지금 승격하면 아무도 안 쓰는 설정 표면만 는다.
"""

CONSEC_CUT_3 = Decimal("0.5")
"""연속 손절 3회 이상 뒤 진입 크기 배수 (consec_cut). 근거는 CONSEC_CUT_2 참조."""

HALF_LEFT = Decimal("0.5")
"""반익 뒤 남는 수량 비율 — 청산가를 다시 잴 때 쓴다.

⚠️ 탐지기의 1차 익절 비중(`HALF`)과 **같은 값이어야 한다.** 절반을 덜면
절반이 남는다는 당연한 관계이고, 비중을 바꾸면 여기도 같이 바뀐다.
"""

MACD_EXIT_MIN_BARS = 35
"""MACD 반대 교차 청산(`macd_exit_above_short`)이 판정할 최소 봉 수 — 느린 EMA 26 + 시그널 9."""

_logger = get_logger("walkforward.session")

DELTA_RELOAD_INTERVAL = timedelta(minutes=15)
"""델타 파일을 다시 읽는 주기 — 델타 축(15m)과 같다 (T28).

걸음마다 읽으면 10초 방아쇠에서 초당 여러 번 디스크를 훑는다. 그 비용이 판정을
늦추고, 늦은 판정은 우리가 고치려던 바로 그 병이다.
"""

_SPAN_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def frame_span(frame: Timeframe) -> timedelta:
    """시간축 한 봉의 길이.

    Args:
        frame: 시간축.

    Returns:
        그 봉 하나의 시간 길이.

    Raises:
        ValueError: 값의 단위를 모르는 경우 — 조용히 0 으로 두면 수명 계산이 거짓이 된다.

    Note:
        `Timeframe` 값이 `10s`·`15m`·`1h`·`1d` 꼴이라 뒤 한 글자가 단위다. 표를 따로
        두면 축을 더할 때 한쪽만 늘어난다.
    """
    text = frame.value
    unit = _SPAN_UNITS.get(text[-1])
    if unit is None or not text[:-1].isdigit():
        raise ValueError(f"시간축 {text!r} 의 길이를 모른다")
    return timedelta(seconds=int(text[:-1]) * unit)


class EntryGate(Protocol):
    """펀드 층이 세션에 끼우는 **진입 문** (T279 P3 · 2026-09-18 · T286 으로 크기까지).

    세션은 사기 직전 `grant(at, exposure)` 만 부른다. 구현(`orchestration/rebalancer/gate.py`)은
    펀드의 다른 세션들을 보고 동시 보유 상한(§23)·같은 날 연속 손절 정지(§24)·총 명목 상한(V2)·
    낙폭 브레이크를 판단한다. 세션은 그 규칙의 내용을 모른다 — 허용 크기를 받고 이유를 깔때기에
    적을 뿐이다.
    """

    def grant(self, at: datetime, exposure: Decimal) -> Grant:
        """이 크기로 열어도 되는지 묻고 **허용 크기**를 받는다.

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 **실제** 노출(명목/증거금 = 배율 x 크기 승수).

        Returns:
            허용 크기 · 막은 사유 · 크기를 줄인 장치.
        """
        ...


@runtime_checkable
class LegAwareGate(Protocol):
    """진입을 **낸 다리**를 알려 주면 그 다리의 문으로 답하는 문 (T291 · 이종 합성 매매법).

    한 펀드에 다리가 둘(1H 롱 · 4H 숏)이면 자리·총 명목 상한을 다리마다 따로 센다 — 측정이 그렇게
    쟀다. 구현은 `orchestration/rebalancer/gate.LegGate`. 이 프로토콜이 없는 문은 지금까지처럼
    `grant` 로 묻는다.
    """

    def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        """그 다리의 문에 묻는다.

        Args:
            leg: 진입을 낸 매매법의 귀속 키.
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 실제 노출.

        Returns:
            허용 크기 · 막은 사유 · 크기를 줄인 장치.
        """
        ...


DEFAULT_FRAME_WINDOW = 2_000
"""`Session.frame_window` 기본 — 골든 시험이 전 구간 대비 1.3e-10 로 잠근 창 (T252 · 09-11 켬)."""
STEP_FRAME = Timeframe.M5
FLIP_STOP_BUFFER = Decimal(1)
"""flip_on_engulf 손절 완충 = ATR x 1 (추격·플립과 같은 값 · 순환 import 피해 값만 복제)."""

WICK_STOP_BUFFER = Decimal("0.05")
"""wick_stop 완충 = 진입 봉 범위 x 이 배수 — 꼬리 끝 바로 밖에 손절을 둔다 (T54 B)."""
"""커서를 밀 단위 — 가장 작은 축.

⚠️ 이보다 큰 축으로 밀면 5m 봉을 건너뛰게 되고, 5m 을 쓰는 판단이 그 사이에 일어난
것을 놓친다. **가장 작은 축이 시계**다.
"""


@dataclass(frozen=True, slots=True)
class FrameState:
    """한 시간축의 계산 결과 — 봉이 안 늘면 다시 계산하지 않는다.

    Attributes:
        bars: 그때 보이던 봉 수. 캐시 열쇠다.
        rows: 그 봉들.
        trend: 주 추세. 판정 불가면 None.
        indicators: 공용 지표.
        has_box: 상단·하단이 다 있는가.

    Note:
        🔴 **32배속 요구(T13 ⑤)를 여기서 감당한다.** 5m 로 커서를 밀면 1d 봉은 288걸음에
        한 번만 바뀌는데, 매 걸음 전부 다시 계산하면 5개 시간축 x 수백 봉이 매번 돈다.

        ⛔ **캐시 열쇠는 봉 수다** — 시각이 아니다. 봉이 늘지 않았다면 그 시간축이 아는
        사실은 **정확히 같고**, 그래서 재사용이 결과를 바꾸지 않는다 (절대 규칙 #5).
        시각으로 잡으면 같은 봉 안에서도 매번 다시 계산한다.
    """

    bars: int
    rows: list[Candle]
    trend: TrendState | None
    indicators: Indicators
    has_box: bool
    box_low: Decimal | None = None
    """지금 박스의 하단 띠 아랫변 — 없으면 None (T44 청산 사다리가 읽는다)."""
    box_high: Decimal | None = None
    """지금 박스의 상단 띠 윗변 — 없으면 None."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """한 봉에서 세션이 내놓는 것.

    Attributes:
        at: 그 시점 (커서).
        proposals: 플레이북이 낸 후보들.
        trend: 시간축별 주 추세.
        has_box: 유효한 박스가 있는가.
        opened: 이 봉에서 새로 진입한 기록.
        closed: 이 봉에서 청산된 기록들.
    """

    at: datetime
    proposals: tuple[Proposal, ...]
    trend: dict[Timeframe, TrendState]
    has_box: bool
    opened: TradeRecord | None = None
    closed: tuple[TradeRecord, ...] = ()
    declared: tuple[TrendDirection, Decimal] | None = None
    """박스의 반응으로 선언된 국면 (방향, 해제 레벨) — `regime_source: box` 일 때만 (T46)."""


def funding_blocks(direction: Direction, rate: Decimal | None, cap: Decimal | None) -> bool:
    """펀딩 극단인가 — 롱은 rate > cap, 숏은 rate < -cap 일 때 보류 (0.8.1 · 순수).

    Args:
        direction: 진입 방향.
        rate: 지금 8h 요율. None(백테스트·조회 실패)이면 게이트가 잠잔다.
        cap: 플레이북 문턱. None 이면 동결.

    Returns:
        보류해야 하면 True.
    """
    if rate is None or cap is None:
        return False
    if direction is Direction.LONG:
        return rate > cap
    return rate < -cap


@dataclass(slots=True)
class Session:
    """걸어가기 한 판.

    Attributes:
        instrument: 종목.
        playbooks: 이 세션이 굴릴 매매법들. **국면이 어느 것을 돌릴지 정한다.**
        feed: 봉인된 캔들.
        ledger: 매매 원장.
        paused: 일시정지 여부.
        auto: 시스템 자동 매매를 켤지.

    Note:
        🔴 **매매법이 여럿이다.** 박스권만 굴렸더니 상단이 뚫린 뒤에도 그 아래에서
        타점을 기다렸다 — 사용자 지적: *"돌파 매매로 전환하는 전략 자체가 부재해서
        생긴 일이야."* 국면이 바뀌면 도는 매매법도 바뀐다.

        🔴 **사람과 시스템이 같은 구간을 함께 돈다.** T13 ⑧이 요구하는 *"제안 vs
        사람의 차이"* 는 둘이 같은 봉을 봐야만 나온다.

        ⚠️ 한 번에 **한 포지션**이다. 분할·복리·수량은 RiskManager 소관이고
        (절대 규칙 #4), 여기서 흉내내면 SSoT 가 둘이 된다.
    """

    instrument: Instrument
    playbooks: tuple[Playbook, ...]
    feed: Feed
    ledger: Ledger = field(default_factory=Ledger)
    paused: bool = False
    auto: bool = True
    loss_limit_pct: Decimal | None = None
    """브레이커 문턱 (T22) — RUN 누적 손익률이 이 값(%)만큼 잃으면 **신규 진입을 멈춘다**.

    🔴 **RUN 단위·낙폭 기준**이다 (사용자 확정 — 연속 손절 횟수 기각). 값은 코드에
    박지 않고 판을 띄울 때 주입한다 (§4.3.1). None 이면 꺼짐 — 문턱의 근거가 되는
    낙폭 실측이 쌓이기 전에는 기본으로 켜지 않는다.

    ⚠️ 멈추는 것은 **신규 진입뿐**이다. 손절·청산·스탑 상향은 계속 돈다 (§1.2.1) —
    많이 잃은 판일수록 나가는 길은 열려 있어야 한다.

    ⛔ **자동 재개 없음.** 스스로 다시 켜면 브레이커가 아니라 지연 장치다.
    """
    breaker_tripped_at: datetime | None = None
    """브레이커가 발동한 시각. None 이면 발동 전이다 — 화면·저널이 이유를 말한다."""
    seen_proposals: int = 0
    """이 판이 지금까지 본 후보 수 (관측 규약 §1-0s).

    🔴 순간값(게이트의 proposals)만 있으면 "관망했다"와 "탐지가 안 돌았다"를
    **사후에 구별할 수 없다** — 밤새 매매 0건이 정확히 그 상황이었다 (2026-08-22).
    """
    seen_blocked: int = 0
    """그중 진입 보류로 막힌 수 (T26 ②). 끌 줄이 실제로 몇 번 물었는지가 여기 남는다."""
    _delta_rows: tuple[BarDelta, ...] = ()
    """로드된 봉 델타 (T28) — 컨텍스트에 그대로 실린다. 빈 튜플이 기본이다."""
    _delta_loaded_at: datetime | None = None
    """마지막으로 델타 파일을 읽은 벽시계 시각 — 라이브 갱신 주기용."""

    guarded: bool = True
    """지금 **지켜지고 있는가** — 거짓이면 새 진입을 안 받는다 (2026-08-20).

    🔴 **무방비인데 또 사는 것이 손실의 증폭기였다.** 밤사이 실측: 브로커측 손절을 못 건
    상태에서 3회를 기다리다 시장가로 던지고, 계획이 남아 있어 다시 사고, 또 못 걸고 —
    XRP 17건 · SOL 7건이 전부 그 고리였다. 수수료만 -153 이 나갔다.

    ⚠️ **`auto` 와 다르다.** 저것은 *사람이* 멈춘 것이고 이것은 *집행이* 못 지키는
    상태다. 하나로 합치면 사람이 켠 판을 시스템이 끄고, 사람은 자기가 끈 줄 안다.

    ⛔ **보유분 관리는 계속한다** — 손절 재장착·반익·거래소 대조는 그대로 돈다.
    막는 것은 **새로 사는 것**뿐이다 (spec §1.2.1: 리스크를 줄이는 행동은 안 막는다).

    ⭐ 값을 넣는 것은 러너다. 세션은 거래소를 모르고, 알면 안 된다 (P4).
    """

    liquid: bool = True
    """**나갈 호가가 있는가** — 거짓이면 새 진입을 안 받는다 (사용자 제안 2026-08-20).

    🔴 유동성은 **들어갈 때 한 번만** 봤다. 사용자 지적: *"들고 있는 동안 호가가 마르는
    경우"*. 실측으로 그 상태가 어떻게 끝나는지 안다 — SPCX 는 매수호가가 표시가에서
    22% 아래로 벌어져 **증거금 420 이 15시간 묶였다.**

    ⛔ **말랐다고 던지지 않는다.** 그때 시장가로 던졌으면 -420 이었고, 표시가 아래
    지정가로 기다렸더니 -74 였다. 호가는 잠깐 얇아졌다 돌아오는 일이 흔하다.

    ⇒ 자동으로 하는 것은 **더 묶이지 않게 하는 것뿐**이다. 나가는 값은 사람이 정한다.

    ⚠️ **`guarded` 와 합치지 않는다.** 손절을 다시 걸면 `guarded` 는 True 로 돌아가는데,
    그 순간에도 호가는 말라 있을 수 있다 — 한 스위치면 하나가 다른 하나를 지운다.

    ⭐ 값을 넣는 것은 러너다 (P4).
    """

    funded: bool = True
    """**판들의 예산 합이 계좌 안에 들어가는가** (사용자 신고 2026-08-21).

    🔴 이 검사는 판을 **띄울 때만** 돌았다 (`_budget_room`). 실측:

    ```
    띄울 때   판 6개 x 예산 50 = 300  <  계좌 348   ✅ 통과
    두 시간 뒤 예산 합 300           >  계좌 278   🔴 넘었다
    ```

    예산은 그대로인데 **계좌가 줄었다.** 그러면 새 주문마다 Gate 가
    `LIQUIDATE_IMMEDIATELY` 로 거절한다 — *"넣는 즉시 청산될 주문"* 이라는 뜻이다.

    ⛔ **예산을 자동으로 줄이지 않는다.** 어느 판을 깎을지는 사람이 정할 값이다.
    막고 **얼마가 모자란지 말하는** 것까지가 코드의 일이다.

    ⚠️ **`guarded`·`liquid` 와 따로 둔다** — 셋이 서로 다른 사건이라 한 스위치로 합치면
    새 진입이 멎은 이유를 화면에서 못 가른다.
    """

    filler: Filler | None = None
    """체결을 **알려주는 것** (T19 ③). 없으면 지금까지처럼 시장가로 즉시 산다.

    🔴 **원장이 "샀나" 를 계산하지 않게 하는 자리다.** 봉인 급전에는 봉으로 판정하는
    모형(`SealedFiller`)이, 라이브에는 거래소가 채운 우편함이 들어온다 — 세션은 어느
    쪽인지 모른다 (원칙 P3).

    ⛔ **None 이면 아무것도 안 바뀐다.** 0.1 과 0.4 는 이 값이 없어 시장가 경로
    그대로다 (§5.6.2 동결).
    """

    ref_above: bool | None = None
    """기준 종목(BTC)이 자기 SMA(200) 위인가 — 러너가 주입한다 (T66-e F1).

    None = 모름(게이트 잠듦 · funding 선례) — sealed 백테스트도 None 이라 0.2.0 동작이다.
    """
    ref_return: Decimal | None = None
    """기준 종목(BTC)의 4H `bars` 봉 수익률(비율) — 러너가 주입한다 (T290 국면 문).

    `ref_above` 의 형제다. 봉 수는 선언(`entry_ref_return_band.bars`)이 정한다.
    🔴 None = 모름 → 띠를 선언한 매매법은 **진입을 보류**한다(`ref_above` 와 반대 — 규칙 #8-1).
    sealed 백테스트는 러너가 주입하지 않으면 한 건도 안 들어간다 — 재현 도구가 주입한다.
    """
    ref_band_held: int = 0
    """국면 문(`entry_ref_return_band`)이 보류시킨 진입 수 — 관측용 (§1-0s)."""
    ref_surge: Decimal | None = None
    """기준 종목(BTC)의 직전 `days` 일(UTC 일봉 종가) 수익률(비율) — 러너가 주입한다 (T304 #8).

    `ref_return` 의 형제다. 일수는 선언(`entry_ref_surge_cap.days`)이 정한다.
    🔴 None = 모름 → 상한을 선언한 매매법은 **진입을 보류**한다(규칙 #8-1).
    """
    ref_surge_held: int = 0
    """급등 상한(`entry_ref_surge_cap`)이 보류시킨 진입 수 — 관측용 (§1-0s)."""
    ref_sma_down: bool | None = None
    """기준 종목(BTC) 4H SMA 가 `lag` 봉 전보다 낮은가 — 러너가 주입한다 (T304 #2).

    길이·비교 봉 수는 선언(`entry_ref_sma_down`)이 정한다.
    🔴 None = 모름 → 이 문을 선언한 매매법은 **진입을 보류**한다(규칙 #8-1).
    """
    ref_sma_held: int = 0
    """SMA 하락 문(`entry_ref_sma_down`)이 보류시킨 진입 수 — 관측용 (§1-0s)."""
    ref_vol: tuple[tuple[datetime, Decimal], ...] = ()
    """기준 종목(BTC) 연율 변동성 — `(그 UTC 일봉이 끝난 시각, 변동성)` 오름차순.

    T304 · 변동성 목표.

    러너가 최근 며칠 치를 주입하고, 진입은 **판정 봉 시작 시각까지 끝난** 일봉의 값을 쓴다 —
    연구(`t296_wave115.Setup.sigma_at(진입 봉 시작)`)와 같은 자다.
    비어 있으면 모름 → 보류(규칙 #8-1).
    """
    vol_held: int = 0
    """변동성 목표 크기(`entry_vol_target`)가 값을 몰라 보류시킨 진입 수 — 관측용 (§1-0s)."""
    ref_gate_held: int = 0
    """F1 게이트로 **안 산** 자리 수 (관측 규약 §1-0s)."""
    recent_funding: Decimal | None = None
    """지금 8h 펀딩 요율 — **러너가 걸음마다 주입한다** (0.8.1 펀딩캡).

    🔴 세션은 네트워크를 모른다 (원칙 P3) — 봉과 같은 **사실 입력**이고, 봉인
    급전(백테스트)에서는 None 이라 게이트가 잠잔다 (격자가 규칙 자체를 검증했다).
    """

    model_funding: bool = True
    """8h 정산 경계마다 요율을 열린 매매에 물리는 **모형** (T226) — 봉인 급전(백테스트)·페이퍼용.

    라이브 러너는 False 로 두고 거래소 정산 기록을 `apply_funding` 으로 붙인다 — 둘을 같이 켜면
    두 번 낸다. 요율은 `recent_funding`(러너 주입) 이 있으면 그것, 없으면 `costs.yml` 의
    `funding_pct_per_8h` 고정값이다.
    """
    _funding_tick: datetime | None = None
    """직전 걸음의 가격 축 봉 시각 — 정산 경계를 넘었는지 이것과 비교한다."""
    funding_held: int = 0
    """펀딩 극단으로 **보류한 진입** 수 (§1-0s 관측 규약)."""
    entry_gate: EntryGate | None = None
    """**펀드 층의 진입 문** (T279 P3 · 2026-09-18) — 동시 보유 상한(§23) · 같은 날 연속
    손절 정지(§24).

    세션은 다른 종목을 모른다. 펀드가 세션들을 묶을 때 이 문을 끼워 넣고, 세션은 사기 직전에
    "지금 열어도 되나"만 묻는다. None(단일 세션·백테스트·RUN)이면 없는 것과 같다 — 동결 무변화.
    막힌 자리는 지우지 않고 `gate_held` 와 깔때기 `gate:<이유>` 에 센다(§1-0s).
    """
    leg_leverage: dict[str, Decimal] = field(default_factory=dict[str, Decimal])
    """**다리별 노출** `{귀속 키: 배율}` (T291 · 이종 합성 매매법) — 펀드가 끼운다.

    한 세션에 배율이 다른 두 다리(4x 롱 · 2x 숏)가 실리면 세션(거래소) 배율은 하나(큰 쪽)라,
    작은 다리의 진입 노출을 `원장 배율 → 그 다리 배율` 로 바꿔 적는다. 비어 있으면(단일 세션 ·
    백테스트 · 지금까지의 모든 펀드) 없는 것과 같다 — 동결 무변화.
    """
    gate_held: int = 0
    """진입 문에 막혀 **안 산** 자리 수 (T279 P3 · 관측 규약 §1-0s)."""

    post_only_entry: bool = False
    """지정가 진입을 **post-only(poc)** 로 낼지 (T60 축④ · 2026-08-24).

    🔴 일반 지정가(gtc)는 호가를 넘는 순간 **테이커로 체결**돼 수수료 이득이 없다.
    poc 는 크로스면 거부되므로 메이커 요율(0.02% vs 0.075%)이 보장된다 — 대가는
    빠른 봉에서 진입을 놓칠 수 있다는 것(거부 시 다음 판정에서 재시도).
    `limit_entry` 가 켜진 룰에서만 뜻이 있다. 룰 param `post_only` 로 선언한다.
    """

    limit_entry: bool = False
    """진입을 **걸어 두고 받을지** (T19 ④).

    🔴 **가격도 비중도 여기서 안 만든다.** 계획(`setup.entry_plan`)이 이미 두 다리를
    가격과 비중으로 들고 있는데 러너가 그것을 버리고 시장가 하나를 내고 있었다 —
    2026-08-19 손실의 80.6% 가 그 회전 비용이었다. 이 스위치는 *"계획대로 걸까"* 만
    정하고, 값을 만들면 SSoT 가 둘이 된다 (절대 규칙 #4).

    ⛔ **`False` 면 지금까지와 한 줄도 다르지 않다** (§5.6.2 동결).
    """

    shallow_entry: bool = False
    """사다리를 **한 칸 얕게** 놓는다 (T19 · 0.51).

    🔴 **0.5 의 후보이지 개선이 아니다.** 사용자 우려는 *"진입 확률이 확 떨어질 것
    같은데"* 였고, 0.5 는 계획이 적어 둔 자리(띠 위쪽·아래쪽)에 그대로 건다. 그것이
    너무 깊으면 체결이 안 되고, 그때 이 축이 답이 된다.

    ```
    0.5    다리1 = 띠 위쪽        다리2 = 띠 아래쪽
    0.51   다리1 = 지금값과 띠 위쪽의 중간   다리2 = 띠 위쪽
    ```

    ⭐ **새 상수가 없다.** 중간은 두 값의 평균이고, 두 값은 이미 있다.

    ⚠️ **가격을 만드는 유일한 자리다.** 0.5 는 계획이 정한 값을 그대로 쓰는데 여기는
    한 칸 올린다 — 그래서 이것이 **별도 버전**이어야 하고, 성적으로 판정한다
    (§5.6.7 · 절대 규칙 #12).

    ⛔ **둘을 동시에 돌리지 않는다.** 표본이 반씩 갈려 둘 다 30건에 못 미친다 —
    0.5 의 다리별 체결률을 먼저 보고 필요할 때만 켠다.
    """

    waiting: int = 0
    """지정가를 걸어 두고 **기다린 걸음** 수 — 증거금이 묶인 시간의 대리값이다."""

    expired: int = 0
    """계획이 바뀌어 **거둔** 진입 수 (T19 §8).

    🔴 **역선택을 보는 값이다.** 지정가는 가격이 계속 불리하게 갈 때 제일 잘 채워지므로,
    체결률만 보면 *"좋은 자리만 놓치고 있다"* 가 안 보인다.
    """

    fill_probe: list[dict[str, object]] = field(default_factory=lambda: [])
    """지정가가 **얼마나 아깝게** 안 채워졌나 — 체결 모형 보정용 (2026-08-30 · T165).

    🔴 체결률(`entered / (entered + expired)`)만으로는 백테스트를 못 고친다.
    백테스트가 쓰는 값은 *"봉이 지정가를 관통하면 체결"* 이고, 그것을 보정하려면
    **얼마나 더 관통해야 실제로 채워지나**를 알아야 한다.

    T165 가 이 값을 급소로 만들었다: 추가 관통을 0.05%p 만 요구해도 4.6년 수익이
    BN -24% · Gate -44% 다. 그런데 4h 판정으로는 표본이 몇 주 걸린다 —
    그래서 15m 계측기도 같은 물러서기 0.30% 로 맞춰 뒀다.

    각 항목: `{filled, side, limit, touch_pct, bars}`.
    `touch_pct` = 지정가 대비 **가장 가까이 온 거리**(%). 음수면 관통했다는 뜻이다 —
    **관통했는데 `filled=False` 인 항목이 곧 모형과 현실의 차이**다.
    """

    _touch: tuple[float, int] = (float("inf"), 0)
    """지금 걸려 있는 표의 (최근접 거리 %, 산 봉 수) — `fill_probe` 의 누적기."""

    reconciled: bool = True
    """거래소와 원장이 **맞는가** — 거짓이면 신규 진입을 보류한다 (2026-08-30).

    🔴 사용자 요구(배포 준비): *"어떤 오류로 거래소 원장만 남아있는 경우 큰 손실을
    볼 수 있다."* 경보만 띄우면 사람이 자는 동안 갈린 상태 위에 새 포지션이 쌓인다 —
    관리 안 되는 포지션 옆에 관리되는 포지션을 하나 더 놓는 셈이다.

    ⛔ **나가는 길은 안 막는다** (§1.2.1). 이 스위치는 `step()` 의 **진입 판단에만**
    걸리고, 손절·청산·스탑 상향은 그 위(`_settle`)에서 이미 끝나 있다.

    ⚠️ 기본값은 참이다 — 아무도 안 꽂으면(백테스트·페이퍼 워크) 예전 그대로 돈다.
    꽂는 것은 API 의 대조 루프다 (`reconcile_once`).
    """

    fund_ready: bool = True
    """펀드 멤버라면 **펀드가 문과 예산을 붙였는가** — 거짓이면 신규 진입을 보류한다 (T293).

    🔴 2026-09-22 실측(v1.14.0 재기동): 판 18개를 **전부 되살린 뒤에야** 펀드가 복원된다
    (`autostart_live` → `restore_funds` 순서). 그 사이 먼저 뜬 판은 약 100초 동안

        · 진입 문(`entry_gate`)이 없다 — 자리 6 · 명목 상한 · 낙폭 브레이크를 아무도 안 본다
        · 다리 배율(`leg_leverage`)이 없다 — 숏 다리가 2x 가 아니라 세션 배율 4x 로 잡힌다
        · 원장이 격리 전이다 — 예산이 몫(62.15)이 아니라 장부값(20.72), equity 는 계좌 전액

    인 **그냥 단독 판**으로 걷는다. 그 창에 1시간 봉이 닫히면(17:00:06 이 그랬다) 신호 하나가
    펀드 규칙 밖에서 주문이 된다. 펀드 복원이 실패해 `PENDING_FUNDS` 에 남으면 창은 120초
    단위로 늘어난다.

    ⛔ **나가는 길은 안 막는다** (§1.2.1). `_may_enter` 에만 걸린다 — 손절·청산·스탑 상향은
    그 위(`_settle`)에서 이미 끝나 있다. 되살아난 포지션의 보호는 첫 걸음부터 돈다.

    ⚠️ 기본값은 참이다 — 단독 판 · 백테스트 · 페이퍼 워크는 예전 그대로 돈다. 거짓으로 꽂는 것은
    API 의 판 시작(`_live_start` — 펀드 파일에 이 종목이 있을 때)이고, 참으로 되돌리는 것은
    펀드가 문을 붙이는 자리(`_attach_gate`) 하나다.
    """

    accounting_ok: bool = True
    """원장 손익이 **거래소와 부호까지 맞는가** — 거짓이면 회계가 증명 가능하게 틀렸다.

    🔴 `pnl_sign_split`(원장 실현손익 vs 거래소 실현손익 **부호 반대**) 이 뜨면 러너가
    이 값을 거짓으로 꽂는다. `reconciled` 와 **다른 신호다**: `reconciled` 는 포지션이
    갈린 것(고아·유령·무방비), 이것은 **실현손익 회계**가 틀린 것이다.

    ⛔ 펀드 격리(벽돌 2)가 이걸 읽는다 — 회계가 틀린 세션의 허구 손익을 총자본·TWR 에
    넣지 않고 **마지막 신뢰값에 동결**한다. 재구성(벽돌 3)으로 원장이 실측에 맞으면 풀린다.

    ⚠️ 기본값 참 — 백테스트·페이퍼는 거래소 실측이 없어 이 감사를 안 돌린다.
    """

    verified_realized: Decimal | None = None
    """거래소 실측으로 **귀속된 내 실현손익** — `_pnl_audit` 이 태그+소유권 창으로 센 값.

    🔴 원장 실현(`realized_cash`)이 거래소와 갈렸을 때, 펀드 격리(`SessionBridge`)가
    원장 허구 실현을 **이 실측값으로 갈아끼운다**. 그래야 재시작하며 이미 갈린 채로 떠도
    (동결할 신뢰 기준이 없어도) 펀드가 허구가 아닌 **실측**을 반영한다.

    None 이면 아직 귀속할 거래소 청산이 없다(=검증 못 함). 그때는 원장값을 최선으로 쓴다.
    """

    audit_since: datetime | None = None
    """**원장 재정렬(resync) 워터마크** — 이 시각 이전 거래소 청산은 감사에서 무시한다.

    🔴 재사용된 계정에 며칠치가 쌓이고 원장이 재개(resume)로 낡으면, 어느 것도 진짜
    손익이 아닌 **복구 불가** 상태가 된다. 사람이 *"지금 거래소 상태로 재정렬"* 을 누르면
    이 값을 지금으로 잡아, 감사가 **지금부터의 청산만** 센다 (과거 fiction 을 버린다).

    None 이면 워터마크 없음(전 이력 대상 · 기본).
    """

    realized_anchor: Decimal = Decimal(0)
    """재정렬 시점의 실현손익 — *"재정렬 이후 실현"* = `realized_cash - realized_anchor`.

    🔴 화면·감사가 이 오프셋을 빼서 **재정렬 이후**만 센다. 원장 기록은 안 지운다(감사
    추적용으로 남긴다) — 표시·대조에서만 과거를 잊는다. 기본 0 = 앵커 없음.
    """

    stale_plans: int = 0
    """계획이 **낡아서** 버린 진입 수 (2026-08-19 사고 ⑦).

    🔴 **0 이 아니면 판정 축과 진입 축의 간극이 실제로 계획을 깨고 있다는 뜻이다.**
    조용히 버리면 *"자리를 못 찾았다"* 와 구별되지 않는다 (절대 규칙 #8).

    ⚠️ 이 값이 진입 수보다 크면 그것은 버그가 아니라 **그 축 조합이 안 된다**는
    답이다 — 0.4 가 답해야 할 질문이 바로 그것이다.
    """

    recheck_after_shift: bool = False
    """진입가를 옮긴 뒤 **비용을 다시 재는가** (0.52 · 2026-08-20).

    🔴 0.51 은 진입가만 얕게 당기고 **익절가는 그대로 둔다.** 탐지기의 비용 검사는
    옛 진입가로 통과했고, 실제로 쓰는 값은 다르다 — 그래서 익절이 진입에서 한 눈금
    거리에 서는 계획이 나왔다:

    ```
    5dc2fa  숏  진입 70,718.7  청산 70,718.6  "목표 익절"  -0.45%
    ```

    ⛔ **익절가를 따라 옮기지 않는다.** 익절은 구조물이고 우리가 얕게 들어갔다고
    박스 중앙이 이동하지는 않는다.

    ⚠️ 켜면 **진입 수가 줄어든다.** 그것이 맞다 — 줄어드는 만큼이 원래 갈 수 없던
    자리였다.
    """

    thin_after_shift: int = 0

    thin_legs: int = 0
    """**손절선 너머이거나 너무 가까운 다리**라 안 건 수 (사용자 확정 2026-08-20).

    🔴 평균만 보면 손절선 너머에 걸린 다리가 통과한다. 그 다리가 채워지면 그 매매는
    태어나면서 이미 손절 자리에 있다 — 실측 `b3074fba4b58` 은 **14초** 살았다.

    ⚠️ 이것은 *"세 번 손절하고 네 번째에 먹는다"* 의 세 번 중 하나가 아니다. 표본에
    섞이면 승률과 RR 을 동시에 망친다 (RR 66 짜리가 그렇게 나왔다).
    """

    _dead: set[str] = field(default_factory=set[str])
    """이미 센 **죽은 채 태어난** 매매 id (사용자 신고 2026-08-21).

    🔴 `_collect` 는 대기 중인 매매가 있는 한 **걸음마다** 돈다. 이것이 없으면 같은
    매매 하나를 169번 세고 `죽은채탄생 163` 으로 보인다 — 실제로는 **1건**이었다.
    """

    born_dead: int = 0
    """**채워지고 보니** 손절선 너머였던 수 (§1-0s 관측 규약).

    ⚠️ 다리를 안쪽에 걸어도 가격이 뛰어넘으면 그 너머에서 채워진다 — 막을 수 없는
    경우가 남는다.

    ⛔ **기록을 버리지 않는다.** 거래소에는 진짜 포지션이 있고, 원장에서 빼면 유령
    포지션이 된다. 세는 것으로 끝내고 **표본에서 뺄지는 사람이 정한다** (§5.6.7).
    """
    """옮긴 뒤 **남는 것이 비용도 못 갚아** 버린 자리 수 (0.52).

    🔴 **새 규칙이 값을 만들면 그 값의 분포를 싣는다** (§1-0s). 이 숫자가 없으면
    *"자리가 없었다"* 와 *"자리는 있었는데 얕게 들어가니 남는 게 없었다"* 를 못 가른다.

    ⚠️ 이것이 크면 그 종목·그 시간대에서 **0.52 는 0.5 와 같아야 한다**는 뜻이다 —
    얕게 들어갈 여유가 없는 시장이다.
    """

    half_withheld: int = 0
    """전환 신호가 났지만 **익이 안 나서 안 던** 횟수 (2026-08-20 · 후보 B).

    🔴 **새 규칙이 값을 만들면 그 값의 분포를 싣는다** (§1-0s 관측 규약). 이 숫자가
    없으면 *"규칙이 안 돌았다"* 와 *"돌았는데 조용하다"* 를 구별할 수 없다.

    ⚠️ **이것이 크다고 좋은 것도 나쁜 것도 아니다.** 막은 만큼 수수료를 아꼈다는 뜻이지만,
    동시에 **놓친 반익**도 여기 섞여 있다 — 갈라 보려면 그 뒤 매매가 어떻게 끝났는지를
    봐야 한다. 판단은 사람이 하고, 이 표는 그 재료다.

    ⭐ 실측 기준선 (2026-08-20 밤, 규칙 넣기 전): 끝난 매매 30건 중 **24건**이 진입 직후
    신호 반익이었고 전부 졌다. 규칙이 돌면 그 24 가 여기로 온다.
    """

    registry: SetupRegistry | None = None
    """탐지기 레지스트리 — 주면 `propose()` 가 그것을 쓰고, 없으면 **걸음마다** 룰 YAML 과
    entry point 를 다시 읽어 새로 만든다 (2026-09-08 실측: 4h 봉 9,300개 백테스트에서 걸음마다
    `registry.built` · 10분 넘게 걸렸다).
    긴 봉인 백테스트는 한 번 만들어 넘긴다. 라이브(4h 에 한 번)는 그대로 둬도 무해하다."""

    step_frame: Timeframe = STEP_FRAME
    """**걸음·틱을 밟는 축** — 기본 `STEP_FRAME`(5m). 라이브·기본 백테스트는 안 건드린다.

    ⭐ **Gate 4.5년 백테스트를 위한 스위치** (2026-08-24). Gate 는 5m·15m 이 최근 3개월뿐이고
    4h 만 4.5년 다 있다 — 5m 로 걸으면 과거 구간이 "밟을 게 없어" 0 거래가 된다. 이 값을 4h 로
    두면 4h 로 걸어 전 구간이 성립한다. **기본값은 5m 이라 동결 경로는 한 비트도 안 달라진다.**
    """

    price_frame: Timeframe | None = None
    """**진입가를 적을 봉의 축** — 없으면 `STEP_FRAME`(5m) (T17 ③).

    🔴 **계획과 진입가가 같은 봉에서 나와야 한다.** 계획(손절·사다리)은 방아쇠 봉으로
    서는데 진입가를 다른 축에서 적으면 **한 계획 안에 두 시점이 섞인다.** 실측
    (2026-08-18):

    ```
    기록 진입   64441.7    ← 9시간 전 5분봉 종가 (그 축이 동결됐다)
    1차 익절    64425.2    ← 15분봉 현재가 기준으로 계산
    reach       -16.5      ← 음수. 롱인데 손실 방향
    ```

    ⇒ 4건이 그 계획으로 나가 원장에 `-4.48%` 로 적혔다.

    ⛔ **0.1 은 `None` 이라 한 줄도 안 달라진다** (§5.6.2 동결). 이 값은 룰이
    `trigger_timeframe` 을 선언할 때만 채워진다 — 방아쇠를 10s 로 내리면서 진입가를
    5m 종가로 적으면 그 사고가 **정확히 되풀이된다.**
    """
    flip_on_opposite: bool = False
    """반대 방향 후보가 뜨면 들고 있는 것을 **정리**하나 (0.2).

    🔴 사용자 논리 (2026-08-17):

    > *"숏 진입 판단이 들면, 이미 추세 전환이 확정이라는 뜻이고, 그러면 자연스럽게
    > 롱 포지션에 진입해있었더라면, 추세전환으로 전환익절했어야 했던 거 아냐?"*

    맞다. 그런데 0.1 은 숏 방아쇠(`_rejected`)와 청산 신호(`_turning`)가 **조건이
    완전히 달라** 서로 몰랐다.

    ⚠️ **세션은 룰을 모른다.** 이 값은 `orchestration` 이 조립하며 켠다 — 세션이
    플레이북 id 를 알면 그 자체가 층 위반이다 (`orchestration/` 입주 조건).

    ⛔ 기본값은 거짓이다. 0.1 의 동작을 바꾸면 비교 대상이 사라진다 (§5.6.2).
    """
    journal_path: Path | None = None
    journal_meta: dict[str, Any] = field(default_factory=dict[str, Any])
    _open: TradeRecord | None = None
    _tick_fell_back: bool = False
    """판정 창이 비어 관측 창으로 대체한 적이 있나 — 로그를 한 번만 남기려는 표식.

    걸음마다 도는 자리라 매번 적으면 로그가 그것으로 덮인다. 그러나 **한 번은
    반드시 남겨야** 한다 — 이 대체가 없던 동안 고아가 3판정 연속으로 났다 (T72 §12).
    """
    _waiting: TradeRecord | None = None
    """지정가를 걸어 두고 **아직 안 채워진** 매매 (T19 ③).

    🔴 **원장에 아직 안 넣는다.** 안 산 것을 보유로 적으면 유령 포지션이고, 그 위에서
    손절을 걸려다 실패한다 — 2026-08-19 에 정확히 그 모양의 사고가 났다.

    ⚠️ `_open` 과 다르다. 저것은 **가진 것**이고 이것은 **부른 것**이다.
    """
    _tickets: tuple[str, ...] = ()
    """지금 걸려 있는 표 이름들 — `_waiting` 과 짝이다."""
    _waiting_until: datetime | None = None
    """걸어 둔 표가 **계획 없이도** 살아 있는 마감 시각 (T43 리테스트 대기).

    돌파는 한 봉짜리 사건이라 다음 봉에 계획이 사라진다 — 기본 규칙(계획이 살아 있는
    동안)대로면 리테스트 지정가가 바로 거둬진다. 셋업이 `entry_lifetime_bars` 를 주면
    그 봉 수만큼은 둔다. None 이면 지금과 같다.
    """
    risk_pct: Decimal | None = None
    """건당 리스크 r (자본 대비 · 0.01 = 1%) — 있으면 **배율을 건마다 도출**한다 (T49).

    `exposure = min(r / 손절거리, leverage_cap)` 을 그 매매의 `TradeRecord.leverage` 로 적는다.
    손절이 청산보다 바깥이면 그 자리는 **안 간다** (`unsafe_stops` 에 센다). None 이면 지금처럼
    원장의 고정 배율이다 — 동결 버전 무변화.
    """
    leverage_cap: Decimal = DEFAULT_LEVERAGE_CAP
    """도출된 배율의 상한 (T49). T24: 20배는 21연속 손절에 전멸 — 이 위는 산수로 죽는다."""
    unsafe_stops: int = 0
    """손절이 청산보다 바깥이라 **안 간** 자리 수 (T49 · 관측 규약 §1-0s)."""
    strict_fills: bool = False
    """익절(목표·반익)을 **뚫어야 체결**로 판정한다 (T42 ⑥ · 백테스트 전용).

    `SealedFiller` 의 진입 판정(`<` 엄격)과 같은 잣대. 라이브는 거래소 체결이 답이므로 꺼 둔다.
    켜면 익절 건수가 줄어 잔고가 내려간다 — 그것이 사실에 가까운 쪽이다.
    """
    frame_window: int = DEFAULT_FRAME_WINDOW
    """`_frame` 이 지표를 재는 창의 봉 수 (T252). 0 이면 전 구간(봉인 시작부터 지금까지).

    🔴 걸음마다 전 구간에 `indicator_snapshot.compute` 를 다시 돌리면 한 판이 O(n²) 다
    (15m 1년 셀 9~40분 실측). 창을 자르면 EMA·RSI 계열이 씨앗에 민감해 값이 미세하게
    바뀌므로 창 크기의 근거는 전 구간 대비 차이를 잰 골든 시험
    (`tests/test_frame_window_golden.py` · 창 2,000 = 1.3e-10)이다.
    **2026-09-11 사용자 허가로 기본 2,000** — 저장소 18개를 같은 창으로 재생성한다(규칙 #5 ·
    T252 2단계). 전 구간이 필요하면 0 을 명시한다.
    """
    span_cover: Decimal = SPAN_COVER
    """박스로 인정할 최소 폭 = 왕복 비용 x 이 배수 (T42 ⑤). 룰의 `span_cover` 를 조립층이 넣는다.

    🔴 국면 RANGE(`has_box`)와 탐지기가 **같은 값**을 써야 한다 — 다르면 탐지는 좁은 박스를
    버리는데 국면은 그 박스로 RANGE 를 선언한다. 기본은 상수 6 (동결 무변화).
    """
    stop_at_price: bool = False
    """손절선에 **닿으면 그 가격에** 나간다 — 확인 봉 없이 (T50 B · 측정 스위치).

    기본(거짓)은 몸통 확인: 진입 TF 마감 몸통이 손절선을 넘어야 나가고, 그때 가격은 이미 손절선보다
    한참 아래다 — 0.0.8 실측 38건에서 실현 손실이 계획의 1.34배(중앙)·최대 3배. 라이브는 안 켠다.
    """
    full_ride: bool = False
    """반익을 끄고 **전량 목표까지** 들고 간다 (손익비 · 측정 스위치).

    25개월 업비트 실측: 반익반본이 매매의 15% 를 목표(+0.89%)의 1/5(+0.18%)에 가둔다. 없애면 이기는
    25개월 업비트 실측: 반익반본이 매매의 15% 를 목표(+0.89%)의 1/5(+0.18%)에 가둔다.
    """
    flip_on_engulf: bool = False
    """보유 중 반대 방향 장악/도지가 뜨면 익절하고 반대로 진입 (측정 스위치 · 사용자 설계).

    사용자: *"장악 캔들이 뜨면 익절하고 그 캔들 반대편에서 잡아야 한다. 또 전환이면 또
    뒤집고, 아니면 그 방향으로 추세 전환."* 손절 = 반전 캔들 반대 끝(가장 가깝다).
    """
    stop_cap_ratio: Decimal | None = None
    stop_protect_ratio: Decimal | None = None
    short_allowed: bool = True
    """숏 진입이 되는 시장인가 — 능력표 `short_allowed` (T239 · `apply_playbook_knobs` 가 세팅).

    거짓이면 숏 후보는 진입 후보에서 빠지고 `short_blocked` 로 센다. 현물(주식·업비트)은 거짓.
    """
    short_blocked: int = 0
    """능력표 때문에 버린 숏 후보 수 (T239)."""
    has_liquidation: bool = True
    """청산이 있는 시장인가 — 능력표 `leverage_allowed` (T254 ① · `apply_playbook_knobs` 가 세팅).

    거짓(주식 현물)이면 거래소 조건부 손절은 보호 손절이 아니라 **계획 손절**이다 — 청산이
    없으니 청산 거리 안쪽의 보호 자리가 -70%(NVDA 실측 224 → 68.07)에 걸리고, 앱이 죽으면
    손절이 없는 셈이 된다. Blue-Green 배포의 전제(손절은 거래소에)가 거기서 깨진다.
    """
    pending_ttl_bars: int | None = None
    """대기 지정가를 **판정 봉 몇 개까지** 두나 (T234 · 측정 스위치). None = 계획이 살아 있는 동안.

    연구 엔진 `TrendLab` 은 `ttl_judge=1` — 한 판정 봉이 지나면 취소한다. 세션은 신호가 살아 있는 한
    표를 두어 며칠 뒤 되돌림에 채워지기도 한다(T233 ④ BTC 실측). 두 엔진을 맞대는 실험용.
    """
    flat_at_close: bool = False
    """그날 정규장 마지막 봉에서 전량 나간다 (T241 · 매매법 `flat_at_close`). 달력이 있어야 돈다."""
    calendar: MarketCalendar | None = None
    """세션 달력 — 마감이 있는 시장에서 `apply_playbook_knobs` 가 준다. None 이면 24시간 장."""
    _pending_bars: int = 0
    """지금 대기 표가 본 판정 봉 수 — `pending_ttl_bars` 와 견준다."""
    """보호 손절 비율 — `stop_mode: close` 매매법의 라이브가 거래소에 거는 자리 (T233 ②)."""
    """β — 손절을 **청산거리의 이 비율 안쪽**으로 당긴다 (T120~T146). None 이면 끔.

    ⛔ 세션이 정하지 않는다 — `config/risk.yml` 의 값을 러너가 넣어 준다.
      `decision.risk.require_stop_cap()` 이 배율과 짝을 강제한다 (3x 초과면 필수).
    """
    stop_min_pct: Decimal | None = None
    """손절거리가 이 값보다 **가까우면 그 자리는 안 간다** (T147~T150). None 이면 끔.

    β 의 짝(하한)이다 — 손절거리를 `[하한, β x 청산거리]` 로 가둔다.
    ⛔ 세션이 정하지 않는다 — `config/risk.yml` 의 값을 러너가 넣어 준다.
    """
    wick_stop: bool = False
    """진입 봉 **꼬리 끝**으로 손절을 당긴다 — 스마트 띠보다 가까울 때만 (T54 B · 측정 스위치).

    사용자: *"핀바로 진입했으면 손절은 그 꼬리 끝이다."* 손절이 가까워지면 배율이 커지고
    손익비 분모가 준다 — 대신 노이즈 손절이 는다. 어느 쪽이 이기는지 잰다. 절대 넓히지 않는다.
    """
    pause_on_spinning: bool = False
    """스피닝(양쪽 꼬리)이 뜬 봉에서는 **추격**을 멈춘다 (T54 C · 측정 스위치).

    사용자 분류 ③ — 양쪽 꼬리 = 우유부단 = 횡보 시작. 추세 추격은 그 자리에서 톱질난다.
    박스는 그대로 둔다(횡보는 박스의 자리다) — 추격 플레이북만 이 봉을 건너뛴다.
    """
    no_counter_marubozu: bool = False
    """장대봉 방향에 **거스르는** 진입을 막는다 (T54 D · 측정 스위치).

    사용자 분류 ④ — 무꼬리 장대봉 = 강한 의지 = 추세 지속. 상승 장대봉 직후 숏, 하락
    장대봉 직후 롱은 역추세다. 그 봉에서는 반대 방향 후보를 진입에서 거른다(화면엔 남는다).
    """
    funnel: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    """진입 깔때기 (T50 ③) — 봉이 어느 국면이었나 · 플레이북별 후보/보류/진입 수.

    "기회가 어디서 죽나" 를 추측하지 않으려고 센다. 키: `bars:<국면>` · `cand:<플레이북>` ·
    `blocked:<플레이북>` · `entered:<플레이북>`. 놓친 돌파는 `missed_breakouts` 그대로.
    """
    _add_seen: dict[str, datetime] = field(default_factory=lambda: dict[str, datetime]())
    """불타기 판정이 이미 본 마지막 판정 TF 봉(매매별 · T308). 다시 훑지 않으려는 표시일 뿐 —
    판정 결과(`add_at` · `add_broken`)는 기록에 있어 재시작해도 같은 답이 나온다."""
    flip_on_event: bool = False
    """반대 **돌파 사건**이 뜨면 보유분을 즉시 청산하고 뒤집는다 (T46 ① · 플레이북 플래그).

    `flip_on_opposite`(0.2 · 반대 후보 아무거나)와 **다른 스위치**다 — 그쪽은 동결이라
    한 글자도 안 바꾼다. 이것은 레벨을 든 셋업(돌파)만 본다.
    """
    _declared: tuple[TrendDirection, Decimal] | None = None
    """박스의 반응으로 **선언한 국면** — (방향, 해제 레벨) (T46 · `regime_source: box`).

    리테스트 체결 뒤 첫 진입 TF 마감이 추세 쪽이면 세우고, 뚫린 띠의 반대편 변을 몸통
    중심이 잃으면 지운다. 없으면 SIDEWAYS 다. `look()` 이 국면 스위치와 충돌 규칙에
    이 값을 넘기므로 둘이 같은 것을 본다.
    """
    missed_breakouts: int = 0
    _last_expired: TradeRecord | None = None
    """만료로 거둔 마지막 대기 매매 — 취소 직전에 거래소가 채웠으면 되살린다 (adopt_late_fill)."""
    """리테스트를 기다리다 **안 돌아와서 놓친** 돌파 수 (T43 · 관측 규약 §1-0s).

    🔴 "기다리면 놓친다"를 말로 정하지 않으려고 센다. 이 값과 놓친 돌파의 반사실
    손익이 후보 B·C 의 대가다.
    """
    fill_cost: bool = False
    """청산 때 **체결 유형대로** 비용을 다시 세는가 (T42 ④).

    켜면 지정가 진입(`entry_fills` 있음)·목표 익절(지정가)은 메이커, 손절·전환·레벨
    이탈(시장가)은 테이커로 센다 (`MarketCosts.round_trip_by`). 꺼지면 진입 때 적은
    전부-테이커 값 그대로다 — 동결 경로는 한 비트도 안 달라진다.

    ⚠️ 이것은 비용을 낮춰 살리는 것이 아니라 사실대로 세는 것이다. 그래도 판정값이
    움직이므로 옛 표와 한 줄에 섞지 않는다 (§5.6.2).
    """
    _seen: dict[str, int] = field(default_factory=dict[str, int])
    _cache: dict[Timeframe, FrameState] = field(default_factory=dict[Timeframe, "FrameState"])
    _shot: Snapshot | None = None
    _shot_key: tuple[int, tuple[str, ...], tuple[str, ...]] | None = None

    @property
    def cursor(self) -> datetime:
        """지금 "현재"로 치는 시각."""
        return self.feed.cursor

    @property
    def finished(self) -> bool:
        """봉인 끝까지 걸어갔는가."""
        return self.feed.finished

    @property
    def playbook(self) -> Playbook:
        """대표 매매법 — 진입 TF·귀속 이름의 기본값.

        Note:
            ⚠️ 여럿이 돌 수 있으므로 **첫 번째**를 대표로 쓴다. 화면 제목과 기본 시간축
            용도이며, 실제 판정은 `playbooks` 전체가 한다.
        """
        return self.playbooks[0]

    @property
    def position(self) -> TradeRecord | None:
        """보유 중인 포지션. 없으면 None."""
        return self._open

    def adopt(self, record: TradeRecord) -> None:
        """거래소에 이미 열려 있던 포지션을 **보유 중으로 받아들인다**.

        Args:
            record: 거래소에서 되읽어 만든 기록. 이미 원장에 넣은 뒤 부른다.

        Raises:
            RuntimeError: 이미 보유 중인 경우 — 한 번에 한 포지션이다.

        Note:
            🔴 사용자 지적 2026-08-18: *"연결이 끊겨버리면 1차 익절이나 그런 대응 자체가
            불가능하잖아."* 판이 죽으면 원장이 사라지는데 거래소 포지션은 남고, 그때부터
            아무도 그것을 관리하지 않는다. 이 함수가 그 다리다.

            ⛔ **여기서 계획을 만들지 않는다.** 값은 러너가 거래소에서 되읽어 오고,
            세션은 *"보유 중이다"* 만 받아들인다 — 세션이 값을 지어내면 손절·익절의
            SSoT 가 둘이 된다 (절대 규칙 #4).

            ⚠️ `_settle` 이 이 기록을 정상 매매와 **똑같이** 다룬다. 그래서 반익도
            본절 상향도 다시 돈다 — 그것이 이어받는 이유다.
        """
        if self._open is not None:
            raise RuntimeError("이미 보유 중이다 — 이어받을 수 없다")
        self._open = record

    def release(self) -> None:
        """보유 중 표시를 **놓는다** — 거래소가 이미 닫은 것을 확인했을 때.

        Note:
            🔴 사용자 지적 2026-08-18: *"실제 주문은 이미 손절 난 상태야. (…) 현재
            화면에서는 해당 포지션을 보유 중인걸로 보여."* 맞다. 청산 판정은 진입 축
            봉이 마감돼야 도는데, 브로커측 조건부는 **가격이 닿는 순간** 발동한다.
            그 사이(최대 15분) 원장과 거래소가 갈린다.

            ⛔ **여기서 손익을 계산하지 않는다.** 원장 기록은 러너가 거래소 체결가로
            닫고, 세션은 *"더 이상 보유 중이 아니다"* 만 받아들인다 — 세션이 값을
            지어내면 손절·익절의 SSoT 가 둘이 된다 (절대 규칙 #4).
        """
        self._open = None

    @property
    def detections(self) -> dict[str, int]:
        """근거별 누적 탐지 수 (T13 ⑩ 대시보드)."""
        return dict(self._seen)

    def _bar_deltas(self) -> tuple[BarDelta, ...]:
        """봉 델타를 실어 나른다 (T28) — 파일에서 읽고 라이브에서는 주기적으로 갱신한다.

        Returns:
            시각 오름차순 봉 델타. 파일이 없으면 **빈 튜플** — 없는 것을 0 으로
            꾸미지 않는다. CVD 를 선언한 룰이 빈 칸을 만나면 판정 불가로 센다.

        Note:
            🔴 **델타를 안 쓰는 판은 파일을 읽지도 않는다.** 어떤 플레이북도 CVD 를
            선언하지 않았으면 즉시 빈 튜플이다 — 동결 버전은 디스크를 건드리지 않고,
            한 걸음의 비용도 안 늘어난다 (§5.6.2).

            ⚠️ 갱신 주기는 15분이다 (델타 축과 같다). 걸음마다 읽으면 10초 방아쇠에서
            초당 여러 번 파일을 훑게 된다 — 그 비용이 판정을 늦춘다.
        """
        if not any(item.needs_delta for item in self.playbooks):
            return ()
        now = datetime.now(UTC)
        stale = (
            self._delta_loaded_at is None or (now - self._delta_loaded_at) >= DELTA_RELOAD_INTERVAL
        )
        if stale:
            self._delta_rows = tuple(load_bar_deltas(self.instrument))
            self._delta_loaded_at = now
        return self._delta_rows

    def context(self, at: datetime | None = None) -> MarketContext:
        """그 시점의 분석 재료를 조립한다.

        Args:
            at: 기준 시각. 안 주면 커서. **커서보다 미래면 터진다.**

        Returns:
            컨텍스트.

        Note:
            🔴 **추세는 봉인 안의 전체 이력으로 계산한다.** 창만 쓰면 판정이 `None` 이
            나와 국면 게이트를 가진 셋업이 영원히 0건이다 — 점검기에서 실제로 겪었고,
            거래량 기준선에서도 같은 형태로 겪었다.
        """
        candles: dict[Timeframe, list[Candle]] = {}
        trend: dict[Timeframe, TrendState] = {}
        indicators: dict[Timeframe, Indicators] = {}
        for frame in self.feed.timeframes:
            state = self._frame(frame, at)
            if state is None:
                continue
            # 🔴 **진입 TF 만 캔들로 넣는다.** 탐지기는 `ctx.candles` 를 전부 돌며 시간축마다
            #    셋업을 하나씩 내므로, 5개를 다 넣으면 한 걸음에 셋업이 5건 나온다 —
            #    그중 어느 것으로 진입했는지 기록에 남지 않는다.
            #
            #    상위 시간축은 **추세로** 들어간다 (T13 ⑨ "모든 타임라인의 분석을 통해").
            #    이것이 진입 TF / 참조 TF 분리이며, 선언 스키마를 안 바꾸고 얻는다.
            # 🔴 **방아쇠 축도 넣는다** (T17 · 2026-08-19 실측으로 잡았다). 안 넣으면
            #    탐지기가 그 축 봉을 못 찾아 **판정을 통째로 건너뛴다** — 0.4 가 걸음을
            #    161번 돌고도 후보 0건이었던 이유가 이것이다.
            #
            #    ⚠️ 위 주석이 경계한 *"축마다 셋업이 하나씩"* 은 그대로 막는다 — 탐지기가
            #    방아쇠 축을 **구조물 축으로는 쓰지 않는다** (`detect` 가 건너뛴다).
            #
            # ⛔ `price_frame` 이 없으면(0.1) 아무것도 안 늘어난다 (§5.6.2 동결).
            wanted = {item.timeframe for item in self.playbooks}
            if self.price_frame is not None:
                wanted.add(self.price_frame)
            if frame in wanted:
                candles[frame] = state.rows
            indicators[frame] = state.indicators
            if state.trend is not None:
                trend[frame] = state.trend
        return MarketContext(
            instrument=self.instrument,
            as_of=at or self.cursor,
            candles=candles,
            indicators=indicators,
            structures=(),
            geometry={},
            trend=trend,
            deltas=self._bar_deltas(),
        )

    def _frame(self, frame: Timeframe, at: datetime | None) -> FrameState | None:
        """그 시간축의 계산 결과 — 봉이 안 늘었으면 캐시를 쓴다.

        Args:
            frame: 시간축.
            at: 기준 시각. 되감기(`at` 이 과거)면 **캐시를 오염시키지 않는다.**

        Returns:
            계산 결과. 봉이 없으면 None.

        Note:
            ⛔ 되감기 결과를 캐시에 넣으면, 커서로 돌아왔을 때 봉 수가 같다는 이유로
            **과거 계산을 재사용**하게 된다. 보기만 하는 것이 판정을 오염시키는 셈이다.
        """
        rows: list[Candle] = list(self.feed.judged(frame, at=at))
        if self.frame_window > 0 and len(rows) > self.frame_window:
            # ⭐ T252 — 창을 고정 폭으로 자른다. 캐시 열쇠는 개수 + 마지막 봉 시각이라
            #    개수가 그대로여도 커서가 가면 다시 잰다(아래 2026-08-22 주석).
            rows = rows[-self.frame_window :]
        if not rows:
            return None
        live = at is None
        cached = self._cache.get(frame) if live else None
        # 🔴 봉 **개수만으로는 부족하다.** 창을 고정 폭으로 자르는 피드(백테스트의
        #    CappedFeed)에서는 커서가 전진해도 개수가 그대로라 캐시가 **얼어붙는다** —
        #    방아쇠 축이 이틀 전에 멈춘 채 601걸음을 돈 사고가 이것이다 (2026-08-22).
        #    마지막 봉 시각까지 같아야 같은 창이다.
        if cached is not None and cached.bars == len(rows) and cached.rows[-1].ts == rows[-1].ts:
            return cached
        series = indicator_snapshot.compute(rows)
        # 🔴 진입 TF 보다 잘은 축은 추세를 안 잰다 (`TF_RANK`).
        entry_rank = TF_RANK.get(self.playbook.timeframe, 0)
        state_of = None
        if TF_RANK.get(frame, 0) >= entry_rank:
            history = trend_evaluate(self.instrument, frame, rows)
            state_of = history.states[-1] if history.states else None
        # 🔴 **원장은 진입 TF 에서만 쌓는다.** `has_box` 는 국면 판정에만 쓰이고 그것은
        #    진입 TF 기준이다. 5개 축 전부에서 쌓으면 5m 은 매 걸음 봉이 늘어 1,200봉짜리
        #    ZigZag+접점 누적이 **매번** 돌고, 그것이 8배속에서 봉 갱신이 끊기던 이유다.
        has_box = False
        if frame in {item.timeframe for item in self.playbooks}:
            book = build_levels(rows, series.atr14)
            # 🔴 **셋업과 같은 문턱을 쓴다** — 여기가 국면 `RANGE` 를 정하는데
            #    문턱 없이 판정하면, 셋업이 버리는 얇은 박스로 플레이북이 돌게 된다.
            price = rows[-1].close
            cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(self.instrument.market)
            roles = roles_at(
                book,
                price,
                at=len(rows) - 1,
                min_span=box_span(price, cost.round_trip_pct, cover=self.span_cover),
            )
            has_box = roles.upper is not None and roles.lower is not None
            # ⭐ T44 — 새 박스의 띠를 들고 간다. 청산 사다리(ratchet_boxes)가 "진입 위에
            #    새 박스가 섰나"를 물을 때 여기서 읽는다 — 다시 계산하면 같은 일을 두 번 한다.
            box_low = None if roles.lower is None else roles.lower.zone.low
            box_high = None if roles.upper is None else roles.upper.zone.high
        else:
            box_low = box_high = None
        state = FrameState(
            bars=len(rows),
            rows=rows,
            trend=state_of,
            indicators=series.at(len(rows) - 1),
            has_box=has_box,
            box_low=box_low,
            box_high=box_high,
        )
        if live:
            self._cache[frame] = state
        return state

    def look(self, at: datetime | None = None) -> Snapshot:
        """분석만 한다 — **매매는 안 한다** (되감기 관찰용).

        Args:
            at: 기준 시각. 안 주면 커서.

        Returns:
            그 시점의 스냅샷.

        Note:
            🔴 사용자 확정 — *"과거로 되돌릴 시 일시정지 상태에서 분석·매매는 멈춤."*
            그래서 되감기는 이 함수로만 하고, 원장을 건드리지 않는다.
        """
        entry = self.playbook.timeframe
        state = self._frame(entry, at)
        # ⭐ T46 — 선언이 살아 있는지 먼저 본다 (해제는 판정 전에 일어나야 한다).
        if at is None:
            self._refresh_declaration()
        # 🔴 **탐지기는 진입 TF 봉이 늘 때만 돈다.** 커서는 5m 씩 밀지만 15m 셋업은
        #    세 걸음에 한 번만 달라진다 — 매 걸음 돌리면 원장 재구축(수백 봉 x ZigZag)이
        #    세 배로 돌고, 그것이 8배속에서 봉 갱신이 끊기던 이유다.
        #
        #    ⛔ 열쇠에 **추세도 넣는다.** 상위 TF 추세가 바뀌면 국면이 바뀌어 같은 봉에서도
        #      다른 답이 나온다. 봉 수만 보면 그 변화를 놓친다.
        ctx = self.context(at)
        # 🔴 **탐지기가 읽는 봉의 마지막 시각도 열쇠다.** 봉 수 + 추세만 쓰던 때가
        #    있었는데, 창을 고정 폭으로 자르는 피드(백테스트 CappedFeed)에서는 봉 수가
        #    안 변해 **추세가 뒤집히는 순간에만 탐지가 돌았다** — 서로 다른 두 룰이
        #    3종목 x 25일에서 소수점까지 같은 성적을 낸 사고의 원인이다 (2026-08-22).
        #    `ctx.candles` 가 곧 탐지기의 입력이므로 그 마지막 봉 시각이 바뀌면
        #    반드시 다시 판정한다 — 10초 방아쇠가 봉 사이에서 당겨지는 근거이기도 하다.
        key = (
            0 if state is None else state.bars,
            tuple(
                f"{frame.value}@{rows[-1].ts.isoformat()}"
                for frame, rows in sorted(ctx.candles.items(), key=lambda kv: kv[0].value)
                if rows
            ),
            tuple(
                f"{frame.value}:{item.state.value}"
                for frame, item in sorted(ctx.trend.items(), key=lambda kv: kv[0].value)
            )
            # ⭐ T46 — 선언이 바뀌면 같은 봉에서도 답이 다르다. 열쇠에 넣지 않으면 해제된 뒤에도
            #    옛 스냅샷(선언 중 판정)을 재사용한다 — 캐시 사고(2026-08-22)의 재판이다.
            + (
                (f"declared:{self._declared[0].value}@{self._declared[1]}",)
                if self._declared is not None
                else ()
            ),
        )
        if at is None and self._shot is not None and self._shot_key == key:
            return self._shot
        has_box = state is not None and state.has_box
        # 🔴 **선언된 시간축마다 한 번씩** 묻는다. 매매법마다 진입 TF 가 다를 수 있고,
        #    `propose` 는 한 시간축을 받아 선언과 대조하기 때문이다.
        # ⭐ T46 — `regime_source: box` 면 국면은 판정기가 아니라 **선언**이다. 선언이 없으면
        #    SIDEWAYS. 국면 스위치와 충돌 규칙이 같은 값을 받으므로 판정이 둘로 안 갈린다.
        override = self._regime_override()
        found: tuple[Proposal, ...] = ()
        for frame in sorted({item.timeframe for item in self.playbooks}, key=lambda f: f.value):
            box = self._frame(frame, at)
            found += tuple(
                propose(
                    ctx,
                    timeframe=frame,
                    has_box=box.has_box if box is not None else has_box,
                    playbooks=list(self.playbooks),
                    trend_override=override,
                    registry=self.registry,
                )
            )
        shot = Snapshot(
            at=at or self.cursor,
            proposals=found,
            trend=dict(ctx.trend),
            has_box=has_box,
            declared=self._declared,
        )
        if at is None:
            self._shot, self._shot_key = shot, key
        return shot

    def _may_enter(self, *, idle: bool, tripped: bool) -> bool:
        """**신규 진입을 받아도 되는가** — 스위치를 한 자리에 모은다.

        Args:
            idle: 걸어 둔 표도 보유분도 없다.
            tripped: 낙폭 브레이커가 걸렸다.

        Returns:
            전부 참일 때만 참.

        Note:
            🔴 **여기 있는 것은 전부 "위험을 늘리는 행동"의 문이다.** 나가는 길
            (손절·청산·스탑 상향)은 `_settle` 에서 이미 끝났고 이 함수를 안 거친다
            (§1.2.1). 그래서 여기에 스위치를 더하는 것은 항상 안전한 방향이다.

            ⚠️ 스위치를 **합치지 않는다.** 서로 다른 사건이라 한 개로 묶으면 하나가
            풀릴 때 다른 하나까지 열린다 — `guarded`(손절을 걸었나)와
            `liquid`(나갈 호가가 있나)가 그래서 따로다.

            | 스위치 | 무엇을 막나 |
            |---|---|
            | `auto` | 사람이 "중지" 를 눌렀다 (걸음은 계속 돈다) |
            | `guarded` | 브로커측 손절이 없다 — 무방비 포지션을 하나 더 만들지 않는다 |
            | `liquid` | 호가가 말랐다 — 못 나갈 자리에 들어가지 않는다 |
            | `funded` | 판들의 예산 합이 계좌를 넘는다 |
            | `reconciled` | 거래소와 원장이 갈렸다 (2026-08-30) |
            | `fund_ready` | 펀드 멤버인데 펀드가 아직 문·예산을 안 붙였다 (T293) |
            | `idle` | 이미 걸어 둔 표가 있다 — 같은 자리에 호가를 쌓지 않는다 |
            | `tripped` | 낙폭 브레이커 |
        """
        return (
            self.auto
            and self.guarded
            and self.liquid
            and self.funded
            and self.reconciled
            and self.fund_ready
            and idle
            and not tripped
        )

    def _count(self, key: str) -> None:
        """깔때기 칸 하나를 센다 (T50 ③)."""
        self.funnel[key] = self.funnel.get(key, 0) + 1

    def step(self) -> Snapshot | None:
        """커서를 한 봉 앞으로 밀고, 그 봉에서 판단·집행한다.

        Returns:
            그 봉의 스냅샷. 끝났거나 일시정지면 None.

        Note:
            🔴 **한 번에 한 봉이다.** 배속은 이 함수를 얼마나 자주 부르느냐일 뿐,
            봉을 건너뛰지 않는다 (모듈 docstring).

            ⚠️ 순서가 결과를 바꾼다 — **청산을 먼저** 본다. 같은 봉에서 청산과 진입이
            겹칠 때 진입을 먼저 세면 한 봉에 두 포지션이 된다.
        """
        if self.paused or not self.feed.advance(self.step_frame):
            return None
        shot = self.look()
        # 🔴 **걸어 둔 것을 먼저 거둔다** (T19 ③). 채워진 뒤라야 같은 봉에서 청산
        #    판정도 성립한다 — 뒤에 두면 채워진 그 봉을 판정 없이 흘려보낸다.
        self._collect(shot)
        self._charge_model_funding()
        closed = self._settle(shot)
        # ⚠️ **부른 것이 있으면 또 부르지 않는다.** 안 막으면 걸음마다 새 표를 걸어
        #    같은 자리에 호가가 쌓인다.
        idle = self._open is None and self._waiting is None
        # ⚠️ `guarded` 는 **집행이 지금 지킬 수 있나**다 — 브로커측 손절을 못 건 상태에서
        #    또 사면 무방비 포지션이 하나 더 는다 (2026-08-20).
        # ⚠️ `liquid` 는 **나갈 호가가 있나**다. 둘은 서로 다른 사건이라 한 스위치로
        #    합칠 수 없다 — 손절을 다시 걸면 `guarded` 가 True 로 돌아가는데, 그때
        #    호가는 여전히 말라 있을 수 있다.
        # ⚠️ `funded` 는 **판들의 예산 합이 계좌 안에 드나**다. 넘으면 새 주문이
        #    `LIQUIDATE_IMMEDIATELY` 로 거절되므로, 보내고 거절당하느니 안 보낸다.
        # 🔴 **브레이커** (T22): RUN 누적 손익이 문턱을 넘게 잃었으면 신규 진입을
        #    멈춘다. 청산·손절은 위(_settle)에서 이미 끝났다 — 나가는 길은 안 막는다
        #    (§1.2.1). 발동은 한 번만 기록하고, 자동 재개는 없다.
        if (
            self.loss_limit_pct is not None
            and self.breaker_tripped_at is None
            and self.ledger.return_pct <= -self.loss_limit_pct
        ):
            self.breaker_tripped_at = self.cursor
            _logger.warning(
                "breaker_tripped",
                extra={
                    "return_pct": str(self.ledger.return_pct),
                    "limit": str(self.loss_limit_pct),
                },
            )
            self.journal()
        tripped = self.breaker_tripped_at is not None
        if not self.reconciled:
            self._count("blocked:unreconciled")
        if not self.fund_ready:
            self._count("blocked:awaiting_fund")
        opened = self._enter(shot) if self._may_enter(idle=idle, tripped=tripped) else None
        # 관측 규약 (§1-0s): 순간값만으로는 "관망"과 "탐지 정지"를 사후 구별 못 한다.
        self.seen_proposals += len(shot.proposals)
        self.seen_blocked += sum(1 for item in shot.proposals if item.blocked)
        # ⭐ T50 ③ — 깔때기. 국면 분포와 플레이북별 후보·보류·진입을 센다.
        regime = major_trend(shot.trend, self.playbook.timeframe)
        self._count(f"bars:{'NONE' if regime is None else regime.value}")
        for item in shot.proposals:
            self._count(f"cand:{item.playbook.playbook_id}")
            if item.blocked:
                self._count(f"blocked:{item.playbook.playbook_id}")
        for item in shot.proposals:
            for evidence in item.setup.evidence:
                self._seen[evidence.source] = self._seen.get(evidence.source, 0) + 1
        if opened is not None or closed:
            self.journal()
        return Snapshot(
            at=shot.at,
            proposals=shot.proposals,
            trend=shot.trend,
            has_box=shot.has_box,
            opened=opened,
            closed=closed,
        )

    def journal(self) -> None:
        """원장을 파일로 남긴다 (T13 ⑧).

        Note:
            🔴 **메모리에만 두면 서버 재시작에 통째로 날아간다.** T13 의 산출물은
            *"걸어간 기록"* 이므로 그것이 사라지면 태스크 자체가 무의미하다.

            🔴 **파일 하나 = 모의 라이브 한 판**이다 (사용자 확정). 세션 정보(종목·
            봉인 구간·시드·매매법·시작 금액)가 머리에 있고 **그 안에** 매매가 들어간다.
            매매만 죽 늘어놓으면 나중에 그 기록이 어떤 조건에서 나온 것인지 알 수 없다.

            ⚠️ 매 변경마다 통째로 다시 쓴다. 한 세션의 매매는 많아야 수십 건이라
            비용이 없고, 이어쓰기로 두면 갱신(대기 → 체결 → 청산)을 표현할 수 없다.
        """
        if self.journal_path is None:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "trade_id": item.trade_id,
                "playbook": item.playbook,
                "actor": item.actor.value,
                # 🔴 **방향이 없으면 저장된 판을 방향별로 분석할 수 없다.**
                #
                #    실제로 겪었다 (2026-08-17): 저널의 `stop` 은 계획 손절이 아니라
                #    **현재** 손절이라, 반익반본이 본절로 올린 뒤에는 `stop == entry` 가
                #    된다. 그 값으로 방향을 추론해 숏 19건을 1건으로 셌고, 그 위에
                #    "0.1 은 숏이 손해" 라는 틀린 결론을 세웠다.
                #
                #    ⛔ 추론하게 두지 않는다 — 값을 만들면 그 값을 싣는다 (§1-0s).
                "direction": item.direction.value,
                "outcome": item.outcome.value,
                "placed_at": item.placed_at.isoformat(),
                "opened_at": None if item.opened_at is None else item.opened_at.isoformat(),
                "closed_at": None if item.closed_at is None else item.closed_at.isoformat(),
                # ⭐ 반익 시각·사유도 남긴다. 화면 사상(`_record`)에는 있는데 저널에만
                #    없어서, 저장된 판에서 "목표에 닿아 반익" 과 "신호에 털려 반익" 을
                #    구별할 수 없었다.
                "half_at": None if item.half_at is None else item.half_at.isoformat(),
                "half_by": None if item.half_by is None else item.half_by.value,
                "half_price": None if item.half_price is None else str(item.half_price),
                # ⭐ 불타기(T308) — 판정 시각 · 가격 · 비율. 되돌림으로 기회를 잃었는지도 싣는다.
                "add_at": None if item.add_at is None else item.add_at.isoformat(),
                "add_price": None if item.add_price is None else str(item.add_price),
                "add_frac": str(item.add_frac),
                "add_broken": item.add_broken,
                # ⭐ 다리를 그대로 싣는다 — 평단만 남기면 *"어디서 얼마나 채워졌나"* 를
                #   되짚을 수 없고, 그것이 이 실험이 답해야 할 값이다 (T19 §8).
                "entry_fills": [[str(price), str(ratio)] for price, ratio in item.entry_fills],
                "entry": str(item.entry),
                "stop": str(item.planned_stop),
                "first": str(item.planned_first),
                "target": str(item.planned_target),
                "exit": None if item.exit_price is None else str(item.exit_price),
                "planned_rr": None if item.planned_rr is None else str(item.planned_rr),
                "achievement": None if item.achievement is None else str(item.achievement),
                "gain_pct": None if item.gain_pct is None else str(item.gain_pct),
                # 🔴 **근거를 저널에 싣는다** (T16 ①). 이것이 없으면 저장된 판은
                #    가격만 남아, 없을 때 돌아간 매매를 복기할 수 없다.
                "evidence": evidence_rows(item.evidence),
                "note": item.note,
            }
            for item in self.ledger.records
        ]
        book = self.ledger
        rate = book.win_rate
        mean = book.mean_achievement
        document = {
            **self.journal_meta,
            "cursor": self.cursor.isoformat(),
            "progress": self.feed.progress(),
            "finished": self.finished,
            "summary": {
                "trades": len(book.records),
                "closed": len(book.closed),
                "wins": book.wins,
                "win_rate": None if rate is None else str(rate),
                "cash": str(book.cash),
                "return_pct": str(book.return_pct),
                "mean_achievement": None if mean is None else str(mean),
            },
            "detections": self.detections,
            "trades": rows,
        }
        self.journal_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _tick(self) -> Candle | None:
        """방금 닫힌 **가격 축** 봉 — 체결·청산과 **진입가**가 여기서 나온다.

        Returns:
            `price_frame`(없으면 `STEP_FRAME`)의 마지막 마감 봉. 없으면 None.

        Note:
            🔴 **진입 TF 봉으로 체결을 판정하면 실시간이 아니다.** 커서는 5m 씩 미는데
            15m 봉은 세 걸음에 한 번 바뀌고, 그 봉은 계획이 서기 **전에** 닫혔을 수도
            있다 — 화면에는 셋업이 떠 있는데 매매가 안 들어가는 것이 그 증상이었다.

            지정가 주문은 시간축을 모른다. 가격이 닿으면 체결된다.

            🔴 **방아쇠 축은 커서에 막혀 있었다** (2026-08-19 사고 ①). 커서는 진입
            축(15m) 봉이 마감될 때만 움직이는데 `judged` 는 커서까지만 준다 — 즉
            10초봉을 아무리 받아도 **15분 내내 같은 봉**이 나왔다. 실측:

                DOGE 28건의 placed_at 이 딱 세 값 (04:44:50 · 05:44:50 · 07:59:50)
                전부 15분 경계 직전 10초봉이다

            같은 봉으로 같은 판단을 10초마다 다시 내렸고, 3시간에 36번 진입했다.

            ⇒ 방아쇠 축이 있으면 `observed` 로 본다. **미래 참조가 아니다** — 판정
              시계(커서)와 도착 시계는 원래 다르고, 실거래에서 "판단한 순간"과 "체결된
              순간"이 같을 수 없다. 지금까지가 비정상이었다(15분 전 가격으로 지금 샀다).

            ⭐ **봉인 급전은 안 변한다.** `SealedFeed.observed()` 의 본문이
              `return self.judged(frame)` 이라 백테스트는 한 비트도 안 달라진다 —
              봉인 구간에는 "지금" 이 없기 때문이다.

            ⛔ **0.1 은 `judged` 그대로다.** 무조건 바꾸면 0.1 **라이브**의 진입가만
              신선해져 백테스트와 갈라진다 (§5.6.2 동결).
        """
        frame = self.price_frame
        if frame is None:
            rows = list(self.feed.judged(self.step_frame))
            if not rows:
                # 🔴 **여기가 비면 체결 흡수가 통째로 멎는다** (T72 §10~§12 · 3판정 연속).
                #
                #    `_collect` 는 `bar is None` 이면 즉시 나가는데, 그러면 우편함을
                #    **열지도 않는다** — 거래소는 채웠고 원장은 모르는 **고아**가 되고,
                #    손절도 안 걸린다. 실측 3회 8건. 원장이 매매를 하나도 못 적으므로
                #    라이브 성과 측정 자체가 성립하지 않았다.
                #
                #    방아쇠 축을 선언 안 한 룰(추세·캐리)은 `judged(5m)` 을 보는데,
                #    커서가 4h 에 묶여 있는 동안 그 창이 통째로 커서 뒤로 흘러가면 빈다.
                #
                # ⭐ **백테스트는 한 비트도 안 변한다.** `SealedFeed.observed()` 의 본문이
                #   `return self.judged(frame)` 이라, judged 가 비면 observed 도 비고
                #   그대로 None 이 나간다 (위 Note 의 동결 근거와 같은 논리).
                #
                # ⚠️ 라이브에서 이것은 **미래 참조가 아니다** — 라이브에 미래는 없고,
                #   커서보다 새 봉은 그냥 **지금**이다.
                rows = list(self.feed.observed(self.step_frame))
                if rows and not self._tick_fell_back:
                    self._tick_fell_back = True
                    _logger.warning(
                        "session_tick_fell_back_to_observed",
                        payload={
                            "step_frame": self.step_frame.value,
                            "cursor": self.cursor.isoformat(),
                            "note": "판정 창이 비어 관측 창으로 대체 — 없으면 체결 흡수가 멎는다",
                        },
                    )
        else:
            rows = list(self.feed.observed(frame))
        return rows[-1] if rows else None

    def _engulf_reversal(self, *, against: bool) -> bool:
        """보유 방향에 **거스르는 장악형 또는 도지**가 확인됐는가 (flip_on_engulf).

        Args:
            against: 롱을 들고 있으면 True — 하락(반대) 반전을 묻는다.

        Returns:
            반대 장악형 또는 반대 방향 도지면 True.
        """
        rows = list(self.feed.judged(self.playbook.timeframe))
        if len(rows) < 2:
            return False
        bullish = not against
        # ⭐ 도지는 방향을 가린다 — 숏 청산(상승 전환)은 잠자리(아래꼬리),
        #    롱 청산(하락)은 비석(위꼬리). 스피닝·망치는 전환이 아니다 (사용자 도지 분류).
        #    스피닝(양쪽 꼬리)·망치는 전환 신호가 아니다 (사용자 도지 분류 2026-08-23).
        strong_doji = dragonfly(rows[-1]) if bullish else gravestone(rows[-1])
        return engulfing(rows, bullish=bullish) or strong_doji

    def _open_reversal(self, closed: TradeRecord, bar: Candle) -> TradeRecord | None:
        """반전 캔들 반대편으로 즉시 뒤집는다 — 손절 = 반전 캔들 반대 끝 + ATR 완충.

        Args:
            closed: 방금 익절한 기록 — 반대 방향으로 뒤집는다.
            bar: 지금 봉 (진입가).

        Returns:
            새 반대 포지션. 손절 거리가 비용보다 좁으면 None (안 뒤집고 관망).

        Note:
            ⚠️ 이 길은 `_may_enter` 도 `_gate` 도 안 거친다(청산과 한 걸음에 묶인 뒤집기라서).
            그래서 펀드를 기다리는 동안(T293)은 여기서 따로 막는다 — 뒤집기도 **새 포지션**이고,
            문 없이 열리면 자리·명목 상한 밖이다. 청산 자체는 이미 끝났으므로 나가는 길은 안 막힌다.
        """
        if not self.fund_ready:
            self._count("blocked:awaiting_fund")
            return None
        rows = list(self.feed.judged(self.playbook.timeframe))
        pivot = rows[-1] if rows else bar
        series = atr_series([c.high for c in rows], [c.low for c in rows], [c.close for c in rows])
        atr = series[-1] if series else None
        buffer = atr * FLIP_STOP_BUFFER if atr is not None else Decimal(0)
        long = closed.direction is Direction.SHORT  # 뒤집으면 반대
        entry = bar.close
        stop = (pivot.low - buffer) if long else (pivot.high + buffer)
        risk = (entry - stop) if long else (stop - entry)
        cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(self.instrument.market)
        if entry <= 0 or risk <= 0 or risk / entry < cost.round_trip_pct:
            return None
        target = entry + risk * Decimal(2) if long else entry - risk * Decimal(2)
        record = TradeRecord(
            trade_id=new_trade_id(),
            playbook=closed.playbook,
            actor=Actor.SYSTEM,
            direction=Direction.LONG if long else Direction.SHORT,
            placed_at=bar.ts,
            opened_at=bar.ts,
            entry=entry,
            planned_stop=stop,
            planned_target=target,
            planned_first=target,
            outcome=Outcome.OPEN,
            cost_pct=cost.round_trip_pct,
            leverage=self.ledger.leverage,
        )
        self.ledger.add(record)
        self._open = record
        self._count(f"entered:flip_{record.playbook.split('@')[0]}")
        self.journal()
        return record

    def _closes_day(self, bar: Candle) -> bool:
        """이 봉이 그날 정규장의 **마지막** 봉인가 — 달력이 없으면(24시간 장) 거짓.

        Args:
            bar: 방금 닫힌 가격 축 봉.

        Returns:
            봉의 끝이 오늘 정규장 마감에 닿으면 참.
        """
        if self.calendar is None:
            return False
        _, closes = self.calendar.next_events(self.instrument.market, bar.ts)
        if closes is None:
            return False
        frame = self.price_frame or self.step_frame
        return bar.ts + interval(frame) >= closes

    def _turning(self, *, against: bool) -> bool:
        """보유 방향에 **거스르는** 전환이 확인됐는가.

        Args:
            against: 롱을 들고 있으면 True — 하락 전환을 묻는다.

        Returns:
            3연속 반대색 봉 또는 반대 장악형이면 True.

        Note:
            🔴 **진입 TF 로 본다.** 커서는 5m 씩 미는데 5m 전환은 잡음이라, 5m 으로
            물으면 거의 매 봉 청산 신호가 뜬다. 사고 파는 판단은 같은 자로 재야 한다.

            ⚠️ 판정 자체는 지표가 소유한다 (`indicators/reversal.py`) — 여기서 다시
            쓰면 탐지기의 진입 기준과 청산 기준이 갈린다.
        """
        rows = list(self.feed.judged(self.playbook.timeframe))
        if len(rows) < 3:
            return False
        # ⭐ **3연속 같은 색 또는 장악형** — 둘 중 하나면 전환이다.
        #
        # ⛔ 한때 장악형을 여기서 뺐다가 **되돌렸다** (2026-08-17). 근거는 *"장악캔들이
        #    오더블록을 만드는데 같은 봉이 청산까지 하면 그 구조가 쓰이기도 전에
        #    끝난다"* 였는데, 그것은 **내 추론이었고 사용자 설명과 어긋났다**:
        #
        #    > *"장악캔들은 혼자로써 추세 전환으로 치는 거니까."*
        #
        #    ⚠️ 게다가 3틱 룰 변경과 **같이** 넣어서, 그때의 손익 하락을 3틱 탓으로
        #      귀속했다 — 두 축을 한 번에 바꾸면 공로도 책임도 못 가른다 (§5.6.7).
        #
        # ⭐ 오더블록은 이것과 **경쟁하지 않는다.** 레벨 원장에 들어가 박스 변이 되고,
        #   그 이탈은 돌파 셋업이 잡는다 — 역할이 서로 다르다.
        return reversal_confirmed(rows, bullish=not against)

    def _confirm_bar(self) -> Candle | None:
        """손절을 확인할 봉 — **진입 TF 의 마지막 마감봉**.

        Returns:
            진입 TF 봉. 없으면 None.

        Note:
            🔴 **5m 로 손절을 판정하면 스마트 박스가 무의미해진다.** 완충을 15m 기하로
            만들어 놓고 판정은 5m 꼬리로 하면, 완충이 하는 일이 없다. 사고 파는 판단은
            같은 자로 재야 한다 (`_turning` 과 같은 이유).

            ⚠️ 같은 15m 봉이 5m 걸음 세 번 동안 반복해서 보인다 — 판정은 멱등하므로
            무해하고, 이탈이 확인되는 **첫 걸음**에 끝난다.
        """
        rows = list(self.feed.judged(self.playbook.timeframe))
        return rows[-1] if rows else None

    def _opposite(self, shot: Snapshot, held: TradeRecord, *, events_only: bool = False) -> bool:
        """이 봉의 후보 중에 **보유 방향과 반대**인 것이 있는가.

        Args:
            shot: 이 봉의 스냅샷.
            held: 보유 중인 기록.
            events_only: 참이면 **돌파 사건**(레벨을 든 셋업)만 센다 (T46). 박스의 반등·거절
                후보는 저항·지지 근처에서 늘 뜨므로, 그것으로 뒤집으면 돌파 롱이 저항 띠의
                숏 거절 후보에 바로 잘린다 — 실측 8일 스모크에서 돌파 7건 보유 0.0h.

        Returns:
            반대 방향 후보가 하나라도 있으면 True.

        Note:
            ⭐ **계획 기하가 방향을 말한다** — 손절이 진입 위면 숏이다. 셋업에 방향
            필드를 새로 만들지 않는다 (`_enter` 가 쓰는 것과 같은 판정이라, 둘이
            갈라질 여지가 없다).
        """
        want_long = held.direction is Direction.LONG
        for item in shot.proposals:
            setup = item.setup
            if events_only and setup.hold_level is None:
                continue
            is_short = setup.stop_loss > setup.avg_entry
            if is_short is want_long:
                return True
        return False

    def _settle(self, shot: Snapshot | None = None) -> tuple[TradeRecord, ...]:
        """보유 포지션이 이 봉에서 끝났는지 본다.

        Args:
            shot: 이 봉의 스냅샷. 반대 신호 청산(0.2)에 쓴다. 없으면 안 본다.

        Returns:
            청산된 기록들.

        Note:
            🔴 **손절을 먼저** 본다. 한 봉에서 둘 다 닿을 수 있고 봉 안의 순서는 알 수
            없다 — 낙관적으로 익절을 먼저 세면 성과가 조용히 부풀려진다.
        """
        held = self._open
        if held is None:
            return ()
        bar = self._tick()
        if bar is None:
            return ()
        # 🔴 **방향에 따라 부등호가 뒤집힌다.** 롱은 저가가 손절을 깨고 고가가 목표를
        #    치지만, 숏은 정반대다 — 한 곳만 안 고치면 숏 성적이 통째로 거짓이 된다.
        long = held.direction is Direction.LONG
        # 🔴 **청산을 가장 먼저 본다** (사용자 요구 2026-08-17). 거래소는 손절 주문을
        #    기다려 주지 않는다 — 마크가가 청산가에 닿으면 그 자리에서 끝난다.
        #
        #    ⚠️ 확인 봉이 아니라 **5m 꼬리**로 판정한다. 규칙 B(몸통 확인)는 우리가
        #      만든 손절 규칙이고, 청산은 **거래소가 집행**하는 것이라 확인을 안 해 준다.
        #    ⭐ 반익했으면 남은 수량이 절반이라 청산가가 **두 배 멀다**.
        liquidation = liquidation_price(
            held.entry,
            held.leverage,
            long=long,
            remaining=HALF_LEFT if held.half_at is not None else Decimal(1),
        )
        if liquidation is not None and (
            bar.low <= liquidation if long else bar.high >= liquidation
        ):
            done = held.closed(at=bar.ts, price=liquidation, outcome=Outcome.LIQUIDATED)
            self.ledger.replace(done)
            self._open = None
            self.journal()
            return (done,)
        # 🔴 **손절은 몸통이 이탈해야 손절이다** (규칙 B · 플레이북 문서 ⑦-1):
        #
        #    > *"아래로 돌파 (**캔들 몸통의 대부분이 이탈**했는지) 확인 후 손절"*
        #
        #    ⛔ 예전에는 **5분봉 저가**가 손절선을 스치기만 해도 끝났다. 페이크 꼬리를
        #      피하라고 스마트 박스로 완충을 만들어 놓고, 판정은 가장 예민한 축의
        #      **꼬리 끝**으로 하고 있었다 — 세션 첫 질문이 그것이었다:
        #      *"페이크로 살짝 삐져나왔다고 손절 나오는 건 너무 아쉬운데?"*
        #
        #    ⇒ **진입 TF 봉의 몸통 중심**이 손절선을 넘어야 한다. 몸통의 절반 초과가
        #      이탈했다는 뜻이며, 새 상수가 필요 없다.
        #
        #    ⚠️ **대가**: 확인을 기다리는 사이 가격이 더 밀린다. 그래서 청산가를
        #      `planned_stop` 이 아니라 **실제 종가**로 적는다 — 계획가로 적으면
        #      원장이 손실을 축소해 말한다 (관측 규약 §1-0s).
        confirm = self._confirm_bar()
        body = None if confirm is None else (confirm.open + confirm.close) / Decimal(2)
        hit_stop = body is not None and (
            body < held.planned_stop if long else body > held.planned_stop
        )
        # ⭐ T50 B — 닿으면 그 가격. 확인 봉을 기다리며 더 내려간 만큼(실측 +34%)을 안 낸다.
        # ⭐ T233 ② — 매매법 선언 `stop_mode: touch` 도 같은 길이다 (측정 스위치 `stop_at_price` 는
        #    그대로 둔다 · 라이브는 이 선언으로 보호 손절/미러를 가른다).
        touched = (self.stop_at_price or self._book_of(held).stop_mode == "touch") and (
            bar.low <= held.planned_stop if long else bar.high >= held.planned_stop
        )
        if touched:
            hit_stop = True
        # ⭐ T42 ⑥ — 백테스트는 익절도 **뚫어야 체결**로 본다 (`strict_fills`). 진입 지정가는
        #    `SealedFiller` 가 `<` 엄격인데 익절만 `>=` 면 들어갈 땐 엄격, 나갈 땐 후하다 —
        #    고가가 내 지정가와 같으면 앞 줄이 먼저 채워지고 나는 못 판다. 라이브는 거래소 체결이
        #    답이라 이 플래그가 꺼져 있고(기본), 옛 동작 그대로다.
        if self.strict_fills:
            hit_target = bar.high > held.planned_target if long else bar.low < held.planned_target
            hit_half = bar.high > held.planned_first if long else bar.low < held.planned_first
        else:
            hit_target = bar.high >= held.planned_target if long else bar.low <= held.planned_target
            hit_half = bar.high >= held.planned_first if long else bar.low <= held.planned_first
        # 🔴 **이 매매를 낸 플레이북의 청산 규칙을 쓴다** (T42 ③). 세트에서 대표
        #    플레이북의 플래그로 모든 매매를 닫으면, 돌파 매매가 박스의 규칙으로 닫힌다.
        book = self._book_of(held)
        # ⭐ T46 — 체결 뒤 **첫 반응**을 본다. 추세 쪽이면 선언, 되돌아왔으면 바로 나간다.
        failed = self._first_reaction(held, book, confirm, bar)
        if failed is not None:
            return failed
        held = self._open if self._open is not None else held
        if hit_stop and (confirm is not None or touched):
            # ⭐ **반익 뒤 본절은 손절이 아니다** (사용자 정정). 절반을 이미 벌었고
            #    나머지를 본전에 뺀 것이라 라벨이 그 경로를 말해야 한다.
            outcome = Outcome.HALF_BREAKEVEN if held.half_at is not None else Outcome.STOP_LOSS
            done = held.closed(
                at=bar.ts,
                # ⭐ T239 — 갭으로 손절선을 건너뛰었으면 체결가는 **시가**다 (계획가는 낙관).
                #    코인은 드물고 주식은 밤마다 있다. 몸통 확인(close) 경로는 종가 그대로.
                price=(
                    self._gap_stop_fill(held, bar) if touched or confirm is None else confirm.close
                ),
                outcome=outcome,
                cost_pct=self._exit_cost(held, outcome),
            )
        elif hit_target:
            done = held.closed(
                at=bar.ts,
                price=held.planned_target,
                outcome=Outcome.TAKE_PROFIT,
                cost_pct=self._exit_cost(held, Outcome.TAKE_PROFIT),
            )
        elif (
            book.hold_level
            and held.hold_level is not None
            and confirm is not None
            and body is not None
            and (body < held.hold_level if long else body > held.hold_level)
        ):
            # 🔴 **레벨 이탈** (T44 후보 B) — 뚫린 저항(= 새 지지)을 몸통 중심이 다시
            #    잃었다. 손절과 같은 자(몸통 중심·진입 TF)라 새 상수가 없다. 캔들 색은
            #    안 본다 — 그것이 이 후보의 전부다.
            done = held.closed(
                at=bar.ts,
                price=confirm.close,
                outcome=Outcome.LEVEL_EXIT,
                cost_pct=self._exit_cost(held, Outcome.LEVEL_EXIT),
            )
        else:
            # ⭐ T241 — 일중 매매법은 마감 봉에서 전량 나간다 (`flat_at_close`). 손절·목표가 먼저다.
            if self.flat_at_close and self._closes_day(bar):
                done = held.closed(
                    at=bar.ts,
                    price=bar.close,
                    outcome=Outcome.TIME_EXIT,
                    cost_pct=self._exit_cost(held, Outcome.TIME_EXIT),
                )
                self.ledger.replace(done)
                self._open = None
                self.journal()
                return (done,)
            # 🔴 **국면이 반대로 뒤집혔으면 손절을 본절로 조인다** (T26 B · 사용자 확정
            #    2026-08-22). 신규 차단(`Proposal.blocked`)은 새 진입만 다루고, 이미
            #    든 것이 정확히 그 리스크다 — *"추세 전환 때 리스크를 최대한 줄인다."*
            #
            #    ⚠️ **이익이 왕복 비용을 넘었을 때만.** 손실 중에 본절로 올리면 손절선이
            #      현재가 반대쪽에 서서 즉시 청산(C안)이 되어 버린다 — 신호 반익이
            #      겪은 병과 같다. 손실 중 보유분은 원래 손절이 지킨다.
            #
            #    ⭐ 상향만 일어난다 (규칙 #3): `behind` 가 본절 아래(숏은 위)일 때만
            #      움직이므로, 반익이 이미 올린 본절을 되내리는 경로가 없다.
            #
            #    🪞 거울상은 공짜다 — 방향 부호(`direction.sign`)가 양쪽을 가른다.
            # 🔴 **트레일링** (T32): 확정된 스윙 저점(롱)/고점(숏)이 지금 손절보다
            #    유리한 쪽에 서면 손절을 거기로 끌어올린다. 추세가 스윙 구조를 지키는
            #    한 안 잘리고, 깨면 그 자리에서 나온다 — "길게 먹기"의 실체다.
            #
            #    ⭐ 상향만 일어난다 (규칙 #3): 후보가 기존 손절보다 유리할 때만 움직인다.
            #    ⚠️ 현재가를 넘는 후보는 버린다 — 손절이 가격 반대편에 서면 즉시 청산이다.
            #    🪞 거울상은 방향 분기 그대로다 — 롱은 저점 위로, 숏은 고점 아래로.
            # 🔴 **청산 봉은 이 매매를 낸 매매법의 시간축에서 읽는다** (T291 · 2026-09-20).
            #    세션 대표(`self.playbook`)의 축으로 읽으면 1H 매매법과 4H 매매법을 한 세션에
            #    묶었을 때 4H 숏이 1H SMA20 에서 닫힌다 — 다른 매매법이 된다. 매매법이 하나거나
            #    묶음의 축이 같으면(지금까지의 모든 선언) `book.timeframe` 이 대표의 축과 같아
            #    한 글자도 안 달라진다.
            if book.trail_stops:
                state = self._frame(book.timeframe, None)
                if state is not None:
                    swings = find_pivots(state.rows, book.timeframe)
                    kind = SwingKind.LOW if long else SwingKind.HIGH
                    picks = [item for item in swings if item.kind is kind]
                    # ⭐ T279 28차 C2 — 연구 엔진 `confirmed_swing_low(i, 10)` 과 같게: 마지막 N 봉
                    #    안의 확정 스윙 중 가장 낮은 저점(롱)을 앵커로, 거기서 pad x ATR(직전 봉)
                    #    만큼 뺀다. 둘 다 None 이면 T32 동결 동작(가장 최근 스윙 · 여유 없음)이다.
                    if book.trail_lookback is not None:
                        floor_index = len(state.rows) - 1 - book.trail_lookback
                        picks = [item for item in picks if item.index >= floor_index]
                    anchors = [item.price for item in picks]
                    if anchors:
                        if book.trail_lookback is not None:
                            found = min(anchors) if long else max(anchors)
                        else:
                            found = anchors[-1]
                        if book.trail_pad_atr is not None:
                            spans = atr_series(
                                [row.high for row in state.rows],
                                [row.low for row in state.rows],
                                [row.close for row in state.rows],
                            )
                            prior = spans[-2] if len(spans) >= 2 else None
                            if prior is not None and prior > 0:
                                pad = book.trail_pad_atr * prior
                                found = found - pad if long else found + pad
                        better = found > held.planned_stop if long else found < held.planned_stop
                        safe = found < bar.close if long else found > bar.close
                        if better and safe:
                            held = replace(held, planned_stop=found)
                            self._open = held
                            self.ledger.replace(held)
                            self.journal()
            # 🔴 **이동평균 트레일** (T58 B-3). 진입 TF 의 SMA(N) 위로 손절을
            #    끌어올린다 — close>SMA 추세필터의 청산이다. 상향만·현재가 안 넘게 (규칙 #3).
            if book.trail_ma is not None:
                state = self._frame(book.timeframe, None)
                if state is not None and len(state.rows) > book.trail_ma:
                    level = sma([row.close for row in state.rows], book.trail_ma)[-1]
                    if level is not None:
                        better = level > held.planned_stop if long else level < held.planned_stop
                        safe = level < bar.close if long else level > bar.close
                        if better and safe:
                            held = replace(held, planned_stop=level)
                            self._open = held
                            self.ledger.replace(held)
                            self.journal()
            # 🔴 **박스 사다리** (T44 후보 C · 사용자 흐름 ⑫ "새 박스로 기준 이동").
            #    진입 위(숏은 아래)에 새 박스가 서면 손절을 그 박스의 바깥 띠로 올린다.
            #    상향만 일어난다 (규칙 #3) — 기존 손절보다 유리하고 현재가를 안 넘을 때만.
            if book.ratchet_boxes and held.hold_level is not None:
                state = self._frame(book.timeframe, None)
                anchor = None if state is None else (state.box_low if long else state.box_high)
                if anchor is not None:
                    beyond = anchor > held.entry if long else anchor < held.entry
                    better = anchor > held.planned_stop if long else anchor < held.planned_stop
                    safe = anchor < bar.close if long else anchor > bar.close
                    if beyond and better and safe:
                        held = replace(held, planned_stop=anchor)
                        self._open = held
                        self.ledger.replace(held)
                        self.journal()
            if book.guard_holdings and shot is not None:
                regime = major_trend(shot.trend, book.timeframe)
                opposed = regime is (TrendDirection.DOWN if long else TrendDirection.UP)
                gained = (bar.close - held.entry) * held.direction.sign / held.entry
                behind = held.planned_stop < held.entry if long else held.planned_stop > held.entry
                if opposed and behind and gained >= held.cost_pct:
                    held = replace(held, planned_stop=held.entry)
                    self._open = held
                    self.ledger.replace(held)
                    self.journal()
            # ⭐ **반익반본** (§6.9) — 1차 익절에 닿으면 절반을 덜고 손절을 본절로 올린다.
            #    전에는 이 분기가 아예 없어서, 1차·2차 익절선에 다 닿았는데도 "보유중"
            #    으로 남아 있었다 (사용자 발견). 계획에 적어 두고 집행하지 않은 것이다.
            #
            #    ⛔ 손절 **상향만** 허용된다 (절대 규칙 #3). 본절은 진입가이므로 항상
            #      기존 손절보다 위다.
            if held.half_at is None and hit_half and not (self.full_ride or book.full_ride):
                # ⭐ **왜 반익했는지 남긴다** (사용자 요구 2026-08-17) — 계획한 1차
                #    익절에 닿은 것과 전환 신호에 턴 것은 전혀 다른 사건이다.
                self._open = replace(
                    held,
                    half_at=bar.ts,
                    planned_stop=held.entry,
                    half_by=HalfBy.TARGET,
                    # ⭐ 계획한 1차 익절선에 **닿아서** 나간 것이므로 그 값이 체결가다.
                    half_price=held.planned_first,
                )
                self.ledger.replace(self._open)
                self.journal()
                return ()
            # 🔴 **신호도 청산한다** (사용자 흐름 ⑤ · ⑫).
            #
            #    > ⑤ *"절반 구간에 닿았거나, **하락 추세전환 신호**가 있을 경우 절반 익절"*
            #    > ⑫ *"하락 추세전환 신호가 있을 경우 익절"*
            #
            #    ⚠️ 돌파 매매는 위에 저항이 없어 **가격 목표만으로는 못 끝낸다** —
            #    측정 이동폭은 신호가 끝내 안 올 때의 천장일 뿐이고, 실제로 나가는
            #    자리는 여기다.
            #
            #    ⭐ 한 규칙이 둘을 다 덮는다 — **아직 반익 전이면 반익(⑤), 이미
            #      반익했으면 전량(⑫)**. 따로 쓰면 한쪽만 고쳐진다.
            # ⭐ **반대 방향 후보 = 청산 신호** (0.2 · `flip_on_opposite`).
            #    숏 자리가 떴다는 것은 전환이 확정됐다는 뜻이고, 그러면 롱은 이미
            #    나왔어야 한다. 0.1 은 두 판정이 서로 몰랐다.
            flipped = self.flip_on_opposite and shot is not None and self._opposite(shot, held)
            # ⭐ T46 ① — 반대 **돌파 사건**이 뜨면 즉시 정리하고 같은 걸음에 뒤집는다.
            #    사용자: *"상단에서 숏을 쳤는데 돌파하면, 즉시 청산하고 롱으로."*
            flipped = flipped or (
                self.flip_on_event
                and shot is not None
                and self._opposite(shot, held, events_only=True)
            )
            # ⭐ T51 — 반대 전환이 확인되면 **전량** 나간다 (반익 경로를 안 탄다). 같은 걸음에서
            #    `_enter` 가 trend_flip 후보로 뒤집는다 — 사용자 설계 "청산하고 바로 갈아탄다".
            if book.flip_on_turn and self._turning(against=long):
                flipped = True
            reverse = False
            if self.flip_on_engulf and self._engulf_reversal(against=long):
                flipped = True
                reverse = True
            # 🔴 **ADX 약화 청산** (0.7.0 · T62 갭 분해 A안). 진입 TF ADX(14)가 유지
            #    문턱(진입 문턱 - 4 · 히스테리시스) **이하**로 마감하면 전량 나간다.
            #
            #    격자 실측: SMA 트레일 단독은 추세가 꺾여 SMA 까지 되돌아오는 동안
            #    이익을 게워냈다 (Gate 4.5년 +313% · MDD 77) — ADX 약화에 나가면 같은
            #    구간이 +1912% · MDD 40 이다. OOS 5창 4승 · 두 거래소 동일 방향 (T62).
            #
            #    ⚠️ 유지 문턱을 진입(35/20)과 같이 두면 거래소 간 종가 1.6bp 차가
            #      Wilder 평활을 타고 신호를 가른다(knife-edge) — -4 가 그 처방이다
            #      (실측 격차 67%→13%).
            #    ⚠️ 닫힌 봉만 계산하므로 4h 봉 안에서 값이 안 변한다 — 걸음이 5m 이어도
            #      판정은 봉 마감 기준이다 (절대 규칙 #5).
            adx_gate = book.adx_exit_long if long else book.adx_exit_short
            if not flipped and adx_gate is not None:
                gauge = self._frame(book.timeframe, None)
                if gauge is not None and gauge.rows:
                    power = adx(
                        [row.high for row in gauge.rows],
                        [row.low for row in gauge.rows],
                        [row.close for row in gauge.rows],
                    )[-1]
                    if power is not None and power <= adx_gate:
                        flipped = True
            # 🔴 **본대 인계 청산** (T66-e · 보조 플레이북). 판정 TF ADX 가 문턱 **이상**
            #    마감 = 0.8.1 롱 진입 신호. 캐리는 전량 나가고, 같은 종목의 본대 판이
            #    자기 규칙대로 진입한다 (방식 B). 위 약화 청산의 거울상 — 캐리는
            #    추세가 강해지면 나간다.
            if not flipped and long and book.adx_exit_above_long is not None:
                gauge = self._frame(book.timeframe, None)
                if gauge is not None and gauge.rows:
                    power = adx(
                        [row.high for row in gauge.rows],
                        [row.low for row in gauge.rows],
                        [row.close for row in gauge.rows],
                    )[-1]
                    if power is not None and power >= book.adx_exit_above_long:
                        flipped = True
            # 🔴 **레짐 소멸 청산** (T66-e). 판정 TF 종가가 SMA(N) 아래 **마감** —
            #    캐리 구간의 정의가 사라졌다. 마감 기준이 측정 의미론이다 (인트라바
            #    SMA 터치 변형은 ETH -16%p 로 기각 · T66 §4j).
            if not flipped and long and book.ma_exit_below_long is not None:
                gauge = self._frame(book.timeframe, None)
                if gauge is not None and len(gauge.rows) > book.ma_exit_below_long:
                    level = sma([row.close for row in gauge.rows], book.ma_exit_below_long)[-1]
                    if level is not None and gauge.rows[-1].close < level:
                        flipped = True
            # 🔴 **숏 거울상** (T290) — 판정 TF 종가가 SMA(N) **위로 마감**하면 숏을 전량 정리한다.
            #    닫힌 삼각수렴 하방 이탈 숏의 청산이다. None 이면 이 가지는 없는 것과 같다.
            if not flipped and not long and book.ma_exit_above_short is not None:
                gauge = self._frame(book.timeframe, None)
                if gauge is not None and len(gauge.rows) > book.ma_exit_above_short:
                    level = sma([row.close for row in gauge.rows], book.ma_exit_above_short)[-1]
                    if level is not None and gauge.rows[-1].close > level:
                        flipped = True
            # 🔴 **MACD 반대 교차 청산** (T302 · T303) — 판정 TF 의 MACD 선이 시그널
            #    위에서 마감하면 숏을 전량 정리한다. 4H MACD 3중 신호 숏의 청산이다.
            #    None 이면 이 가지는 없는 것과 같다.
            if not flipped and not long and book.macd_exit_above_short:
                gauge = self._frame(book.timeframe, None)
                if gauge is not None and len(gauge.rows) > MACD_EXIT_MIN_BARS:
                    series = macd([row.close for row in gauge.rows])
                    line, signal = series.line[-1], series.signal[-1]
                    if line is not None and signal is not None and line > signal:
                        flipped = True
            # ⭐ **불타기 판정** (T308) — 이 봉에서 나가지 않는 롱만 본다. 기록만 한다:
            #    추가 주문 · 펀드 문(명목 상한 · 증거금 · 브레이크)은 원장 · 러너 몫이다.
            if not flipped and long and book.add_on is not None:
                self._maybe_add(book)
                held = self._open if self._open is not None else held
            # 🔴 **추세가 반대로 선언되기 전까지 보유** (T32 후보 D · `hold_while_trend`).
            #    전환 익절(캔들 패턴)은 15m 되돌림에 일찍 끊는다 — 돌파 롱 11건이 상승장에서
            #    -2.43% 였던 이유다. 이 스위치가 켜지면 캔들 패턴을 안 보고, 1h 주 추세가
            #    **반대로 선언될 때** 전량 나온다. 횡보·판정전은 들고 간다 — 그동안은
            #    손절·목표·보유분 조이기가 지킨다. 반대 후보(flipped)는 그대로 전량이다.
            #
            #    ⛔ 동결 버전(스위치 꺼짐)은 아래 elif 그대로다 — 한 글자도 안 달라진다.
            if book.hold_while_trend and shot is not None:
                regime = major_trend(shot.trend, book.timeframe)
                opposed = regime is (TrendDirection.DOWN if long else TrendDirection.UP)
                if not flipped and not opposed:
                    return ()
                flipped = True
            elif book.hold_level and held.hold_level is not None:
                # ⭐ T44 — 레벨을 들고 있는 매매는 캔들 색을 안 본다. 나가는 길은 손절·
                #    목표·레벨 이탈(위)·반대 후보(flipped)뿐이다.
                if not flipped:
                    return ()
            elif book.hold_through_turn:
                # ⭐ T50 C — 캔들 색을 안 본다. 나가는 길은 손절·목표·반익·반대 후보뿐.
                if not flipped:
                    return ()
            elif not flipped and not self._turning(against=long):
                return ()
            # 🔴 **반대 자리가 떴으면 전량 정리한다 — 절반이 아니다** (사용자 정정
            #    2026-08-17):
            #
            #    > *"포지션은 2개일수가 없다니까? 어차피 포지션이 숏일때 롱이 뜬다고
            #    > 판단됐으면 숏을 전환 익절하고 롱으로 진입하면 되는 거잖아."*
            #
            #    맞다. 그런데 처음 구현은 아래 반익 분기로 떨어져 **절반만 덜고
            #    자리를 안 비웠다.** 그러면 `_enter` 가 `self._open is None` 에서
            #    막혀 반대 진입이 영영 안 된다 — 신호는 났는데 못 타는 상태다.
            #
            #    ⭐ 같은 봉에서 정리와 진입이 이어진다: `step()` 이 `_settle` 을 먼저
            #      부르고 그 다음 `_enter` 를 부른다. 자리를 비우면 바로 뒤집힌다.
            if flipped:
                done = held.closed(
                    at=bar.ts,
                    price=bar.close,
                    outcome=Outcome.SIGNAL_EXIT,
                    cost_pct=self._exit_cost(held, Outcome.SIGNAL_EXIT),
                )
                self.ledger.replace(done)
                self._open = None
                self.journal()
                if reverse:
                    self._open_reversal(done, bar)

                return (done,)
            if held.half_at is None:
                # 🔴 **익이 나야 반익이다** (사용자 확정 2026-08-20 · 후보 B).
                #
                #    반익은 **지금 값에 절반을 파는 것**이다. 진입가 근처에서 팔면 그
                #    절반의 실현은 `0 - 비용` 이라 **확정 손실**이다 — 리스크 관리가
                #    아니라 거래소에 수수료를 내고 크기를 줄이는 일이다.
                #
                #    실측 (2026-08-20 밤): 끝난 매매 30건 중 **24건이 진입 직후 신호
                #    반익**이었고 전부 졌다. 수수료만 -153 이 나갔다.
                #
                # ⭐ **본절 문제까지 같이 풀린다.** 이익일 때만 반익하면 그 순간 가격이
                #   진입가보다 유리한 쪽에 있으므로, 본절 손절(= 진입가)이 현재가의
                #   **올바른 쪽**에 서고 거래소가 받는다. 어젯밤 `stop_guard_failed`
                #   33회 · `panic_close` 10회 · 무방비 포지션 3개가 전부 그 반대였다.
                #
                # ⚠️ **문턱은 자의적이지 않다** — 실측 왕복 비용(`config/costs.yml`)이다.
                #   필요 승률을 `(1+c)/(1+RR)` 로 잡는 것과 같은 자를 쓴다.
                #
                # ⛔ **덜지 않을 뿐 손절은 그대로 둔다.** 여기서 손절을 건드리면 이익도
                #   안 났는데 본절로 올리는 셈이고, 그것이 고치려는 바로 그 병이다.
                move = (bar.close - held.entry) * held.direction.sign / held.entry
                if move < held.cost_pct:
                    self.half_withheld += 1
                    return ()
                self._open = replace(
                    held,
                    half_at=bar.ts,
                    planned_stop=held.entry,
                    half_by=HalfBy.SIGNAL,
                    # 🔴 **신호 반익은 계획가가 아니라 지금 값에 나간다** (사고 ③).
                    #    여기에 `planned_first` 를 쓰면 닿은 적도 없는 가격에 판 것이
                    #    되고, 그 한 줄이 28건을 전부 이긴 매매로 만들었다.
                    half_price=bar.close,
                )
                self.ledger.replace(self._open)
                self.journal()
                return ()
            # 🔴 **한 번의 신호로 전량까지 털지 않는다** (사용자 발견 2026-08-17).
            #
            #    `_turning` 은 진입 TF(15m) 창을 보는데 커서는 5m 씩 민다. 같은 15m 봉
            #    안에서 세 번 물으면 **매번 같은 답**이 나오므로,
            #
            #    ```
            #    걸음 k    신호 True · 반익 전   →  절반을 던다
            #    걸음 k+1  신호 True · 반익 함   →  나머지 전량 청산   (5분 뒤, 같은 봉)
            #    ```
            #
            #    이 되어 반익이 **이름만 남고** 두 절반이 거의 같은 가격에 나갔다.
            #    문서 ⑤~⑦ 은 *"절반 익절 → 대기 → 지지 재접촉 관찰"* 인데 그 대기가
            #    통째로 없었다.
            #
            #    ⇒ 전량 청산은 **반익한 봉보다 뒤에 온 신호**여야 한다.
            if confirm is not None and held.half_at >= confirm.ts:
                return ()
            # ⭐ **목표에 닿아서 판 것과 신호를 보고 판 것을 구별한다** (사용자 요구).
            #    같은 `익절` 로 적으면 *"목표까지 갔다"* 로 읽히는데 사실이 아니다.
            done = held.closed(
                at=bar.ts,
                price=bar.close,
                outcome=Outcome.SIGNAL_EXIT,
                cost_pct=self._exit_cost(held, Outcome.SIGNAL_EXIT),
            )
        self.ledger.replace(done)
        self._open = None
        return (done,)

    @property
    def declared(self) -> tuple[TrendDirection, Decimal] | None:
        """박스의 반응으로 선언된 국면 (방향, 해제 레벨). 없으면 None (T46)."""
        return self._declared

    @property
    def _box_regime(self) -> bool:
        """세트 중 하나라도 `regime_source: box` 면 세션 전체가 선언을 쓴다 — 세트는 한 구성이다."""
        return any(item.regime_source == "box" for item in self.playbooks)

    def _regime_override(self) -> TrendDirection | None:
        """`propose()` 에 넘길 국면 — 박스 소스가 아니면 None(판정기 그대로)."""
        if not self._box_regime:
            return None
        return self._declared[0] if self._declared is not None else TrendDirection.SIDEWAYS

    def _refresh_declaration(self) -> None:
        """선언을 **해제**할지 본다 — 뚫린 띠의 반대편 변을 진입 TF 몸통 중심이 잃으면 (T46).

        Note:
            손절·레벨 이탈과 같은 자(몸통 중심)다. 새 상수가 없다. 선언은 포지션과 독립이라
            매매가 끝난 뒤에도 살아 있고, 눌림목 C 가 그동안 일한다.
        """
        if self._declared is None:
            return
        confirm = self._confirm_bar()
        if confirm is None:
            return
        direction, level = self._declared
        body = (confirm.open + confirm.close) / Decimal(2)
        lost = body < level if direction is TrendDirection.UP else body > level
        if lost:
            self._declared = None

    def _maybe_add(self, book: Playbook) -> None:
        """확인된 강한 돌파면 불타기 시각 · 가격을 기록에 적는다 (T308 · 366 ~ 370차).

        진입 뒤 판정 TF 종가가 한 번도 진입가 아래로 안 닫힌 채 진입가 x (1 + 문턱) 이상에서
        닫히면 그 봉 종가가 추가 가격이다. 진입가 아래 마감이 먼저 오면 이 매매는 추가하지 않는다.
        한 매매에 한 번이다.

        Args:
            book: 이 매매를 낸 플레이북. `add_on` 이 있어야 부른다.

        Note:
            ⚠️ 여기서는 **판정만** 한다. 얼마나 살지(처음 실제 명목 x 비율 · 반올림 · 명목 상한 ·
            증거금 · 브레이크)는 펀드 원장과 실계좌 러너가 정한다 — 세션은 자리를 모른다.
        """
        held, rule = self._open, book.add_on
        if (
            held is None
            or rule is None
            or held.opened_at is None
            or held.add_at is not None
            or held.add_broken
        ):
            return
        gauge = self._frame(book.timeframe, None)
        if gauge is None:
            return
        seen = self._add_seen.get(held.trade_id)
        fresh: list[Candle] = []
        # 🔴 진입 **뒤에 열린** 봉만 본다 — 진입 봉(돌파봉) 종가는 진입가 그 자체다.
        for row in reversed(gauge.rows):
            if row.ts < held.opened_at or (seen is not None and row.ts <= seen):
                break
            fresh.append(row)
        if not fresh:
            return
        self._add_seen[held.trade_id] = fresh[0].ts
        level = held.entry * (Decimal(1) + rule.confirm_pct)
        for row in reversed(fresh):
            if row.close < held.entry:
                held = replace(held, add_broken=True)
                break
            if row.close >= level:
                held = replace(
                    held,
                    add_at=row.ts + interval(book.timeframe),
                    add_price=row.close,
                    add_frac=rule.frac,
                )
                self._count(f"add_on:{book.playbook_id}")
                break
        else:
            return
        self._open = held
        self.ledger.replace(held)
        self.journal()

    def _first_reaction(
        self,
        held: TradeRecord,
        book: Playbook,
        confirm: Candle | None,
        bar: Candle,
    ) -> tuple[TradeRecord, ...] | None:
        """체결 뒤 **첫 진입 TF 마감** 하나로 선언하거나 나간다 (T46 · 한 번만).

        Args:
            held: 보유 기록.
            book: 이 매매를 낸 플레이북.
            confirm: 마지막 마감 진입 TF 봉.
            bar: 진입가 축의 마지막 봉 (청산 시각용).

        Returns:
            확인에 실패해 닫았으면 그 기록. 확인됐거나 볼 차례가 아니면 None.

        Note:
            실측 2026-08-22 (T46): 첫 마감이 진입가의 추세 쪽이면 이행 90.9%, 되돌아왔으면
            24.4%. 판정은 **첫 마감 봉 하나**다 — N 봉을 고르면 축이 하나 는다.
            ⛔ `regime_source: trend`(동결)에서는 한 번도 안 돈다.
        """
        if book.regime_source != "box" or held.hold_level is None or held.confirmed is not None:
            return None
        if held.opened_at is None or confirm is None or confirm.ts <= held.opened_at:
            return None
        long = held.direction is Direction.LONG
        with_trend = confirm.close > held.entry if long else confirm.close < held.entry
        if with_trend:
            self._open = replace(held, confirmed=True)
            self.ledger.replace(self._open)
            self._declared = (
                TrendDirection.UP if long else TrendDirection.DOWN,
                held.hold_level,
            )
            self.journal()
            return None
        done = replace(held, confirmed=False).closed(
            at=bar.ts,
            price=confirm.close,
            outcome=Outcome.CONFIRM_FAIL,
            cost_pct=self._exit_cost(held, Outcome.CONFIRM_FAIL),
        )
        self.ledger.replace(done)
        self._open = None
        self.journal()
        return (done,)

    def _candle_blocks(self, bar: Candle, direction: Direction, playbook_id: str) -> bool:
        """이 봉의 캔들 문법이 이 방향 진입을 막는가 (T54 C·D).

        Args:
            bar: 진입 축의 마지막 마감 봉(방아쇠 봉).
            direction: 계획 기하가 말한 방향.
            playbook_id: 후보를 낸 플레이북 id — 추격만 스피닝에 멈춘다.

        Returns:
            막으면 True. 스위치가 다 꺼져 있으면 항상 False(동결 무변화).
        """
        if self.no_counter_marubozu:
            if marubozu(bar, up=True) and direction is Direction.SHORT:
                self._count("blocked:marubozu")
                return True
            if marubozu(bar, up=False) and direction is Direction.LONG:
                self._count("blocked:marubozu")
                return True
        if self.pause_on_spinning and "chase" in playbook_id and spinning(bar):
            self._count("blocked:spinning")
            return True
        return False

    def stop_too_tight(self, record: TradeRecord) -> bool:
        """손절이 하한보다 **가까운가** — 참이면 그 자리는 안 간다 (T147~T150).

        Args:
            record: 손절이 확정된 기록 (β 상한까지 적용된 뒤여야 한다).

        Returns:
            거르면 True.

        Note:
            🔴 **왜 거르나** (T147 진단): 손절난 판의 손절거리 중앙이 **1.52%** 인데
            이긴 판은 **6.47%** 였다 (t=-18.1). SMA200 바로 옆에서 들어가면 손절이
            1.5% 자리에 서고 정상 잡음이 그걸 털어 간다 — `hard_sl` 905건이
            -2,238,106 이고 그중 롱 241건은 **승률 0%** 였다.

            ⚠️ **ADX 는 이 판들을 못 가른다** (t=-1.3). 지금 게이트로는 안 걸러지므로
            별도의 문이 필요했다.

            ⚠️ **β 를 적용한 뒤**의 거리로 잰다 — 실제로 맞을 손절이 그것이기 때문이다
            (연구 엔진 `min_stop_pct` 와 같은 순서).
        """
        floor = self.stop_min_pct
        if floor is None or record.entry <= 0:
            return False
        return abs(record.entry - record.planned_stop) / record.entry < floor

    def _gap_stop_fill(self, held: TradeRecord, bar: Candle) -> Decimal:
        """터치 손절의 체결가 — 봉이 손절선 너머에서 열렸으면 시가, 아니면 손절선 (T239).

        Args:
            held: 보유 기록.
            bar: 손절이 닿은 봉.

        Returns:
            체결가.
        """
        if held.direction is Direction.LONG:
            return (
                min(held.planned_stop, bar.open)
                if bar.open < held.planned_stop
                else held.planned_stop
            )
        return (
            max(held.planned_stop, bar.open) if bar.open > held.planned_stop else held.planned_stop
        )

    def stop_mode_of(self, record: TradeRecord) -> str:
        """이 매매를 낸 매매법의 손절 판정 방식 (T233 ②).

        Args:
            record: 보유 중이거나 끝난 기록.

        Returns:
            `close`(봉 마감 몸통 판정) 또는 `touch`(닿으면 그 가격).
        """
        return self._book_of(record).stop_mode

    def guard_price(self, record: TradeRecord) -> Decimal:
        """라이브가 거래소에 걸 조건부 손절 자리.

        Args:
            record: 보유 중인 기록.

        Returns:
            `touch` 면 계획 손절 그대로(1.5.0 까지의 동작). 청산이 없는 시장(`has_liquidation`
            거짓 · T254)도 계획 손절 그대로. `close` 면 보호 손절(청산 거리의
            `stop_protect_ratio`). 비율이 없으면 **계획 손절 그대로** — 무방비보다 터치 손절이
            낫다(규칙 #8-1). 설정 누락 자체는 `apply_playbook_knobs` 가 판을 띄울 때 막는다.

        Note:
            🔴 close 매매법의 **정상 손절은 여기 없다.** 세션이 봉 마감 몸통으로 판정해
            `STOP_LOSS` 로 닫으면 러너가 `_apply_exit` 로 시장가 청산한다 — 백테스트와 같은
            규칙이 라이브에서 도는 길.
        """
        if self.stop_mode_of(record) == "touch":
            return record.planned_stop
        # ⭐ T254 ① — 청산이 없는 시장은 보호 손절이 뜻이 없다. 계획 손절을 거래소에 건다.
        if not self.has_liquidation:
            return record.planned_stop
        ratio = self.stop_protect_ratio
        if ratio is None:
            return record.planned_stop
        return protect_stop(
            entry=record.entry,
            stop=record.planned_stop,
            leverage=self.ledger.leverage,
            ratio=ratio,
            short=record.direction is Direction.SHORT,
        )

    def _cap_to_liquidation(self, record: TradeRecord) -> TradeRecord:
        """β — 손절을 **청산거리 안쪽으로** 당긴다 (T120~T146 · 조이는 방향만).

        Args:
            record: 손절이 정해진 기록.

        Returns:
            상한 안으로 당긴 기록. β 가 꺼져 있거나 이미 안쪽이면 **원본 그대로**.

        Note:
            🔴 **3x 를 넘기는 판은 이것 없이 돌면 안 된다.** 청산난 판의 81~84% 가
            "손절이 청산보다 바깥" 인 판이었다 (T120). `require_stop_cap` 이 설정
            단계에서 그 조합을 막지만, 값을 실제로 적용하는 곳은 여기다.

            ⚠️ 배율은 새 손절 거리로 **다시 도출한다** — 손절이 가까워졌는데 배율이
            그대로면 리스크가 줄어든 채로 간다 (그건 계획이 아니라 사고다).
        """
        ratio = self.stop_cap_ratio
        if ratio is None:
            return record
        capped = capped_stop(
            entry=record.entry,
            stop=record.planned_stop,
            leverage=self.ledger.leverage,
            ratio=ratio,
            short=record.direction is Direction.SHORT,
        )
        if capped == record.planned_stop:
            return record
        exposure = self._rescaled_leverage(record, capped)
        if exposure is None:
            return record
        tightened = replace(record, planned_stop=capped, leverage=exposure)
        if plan_fault(tightened):
            return record
        self._count("stop_liq_cap")
        return tightened

    def _tighten(self, record: TradeRecord, bar: Candle) -> TradeRecord:
        """손절을 당긴다 — **β 상한 → 진입 봉 꼬리 끝** 순서로, 더 가까울 때만.

        Args:
            record: 손절이 스마트 띠로 잡힌 기록.
            bar: 진입(방아쇠) 봉 — 이 봉의 꼬리 끝이 새 손절 후보다.

        Returns:
            당긴 기록. 두 단계 다 **조이는 방향으로만** 움직인다 (절대 규칙 #3).
            배율은 새 손절 거리로 다시 도출한다.

        Note:
            β 상한이 **먼저**다 — 꼬리가 β 상한보다 가까우면 꼬리가 이기고, 멀면
            β 가 이긴다. 둘 다 "더 가까운 쪽" 규칙이라 순서는 결과를 안 바꾸지만,
            β 는 스위치와 무관하게 항상 돌아야 하므로 앞에 둔다.
        """
        record = self._cap_to_liquidation(record)
        if not self.wick_stop:
            return record
        span = bar.high - bar.low
        if span <= 0:
            return record
        buffer = span * WICK_STOP_BUFFER
        short = record.direction is Direction.SHORT
        cand = (bar.high + buffer) if short else (bar.low - buffer)
        # 더 가까운(당긴) 쪽일 때만 · 손절은 진입 반대편에 남아야 한다.
        tighter = cand < record.planned_stop if short else cand > record.planned_stop
        correct = cand > record.entry if short else cand < record.entry
        if not (tighter and correct):
            return record
        exposure = self._rescaled_leverage(record, cand)
        if exposure is None:
            return record
        tightened = replace(record, planned_stop=cand, leverage=exposure)
        if plan_fault(tightened):
            return record
        self._count("wick_stop")
        return tightened

    def _rescaled_leverage(self, record: TradeRecord, new_stop: Decimal) -> Decimal | None:
        """손절을 당긴 뒤의 배율 — 크기 승수는 지키고 손절 거리 몫만 다시 도출한다 (T232).

        Args:
            record: 손절·배율이 정해진 기록. `leverage` 에는 이미 변동성 타게팅(`size_mult`) ·
                숏 비중(`short_size_mult`) · 연속손절 축소가 곱해져 있다.
            new_stop: 당긴 손절.

        Returns:
            새 배율. 손절이 청산보다 바깥이면 None.

        Note:
            🔴 2026-09-09 실측(T232): 예전엔 `_exposure()` 값으로 덮어썼다. r 이 없는 매매법
            (추세 VS)에서 그 값은 원장 고정 배율이라 β 가 손절을 당길 때마다 변동성 타게팅과
            숏 0.51 이 지워져
            모든 매매가 6x 로 나갔다 — 연구 엔진(+128%)과 세션(-143%)이 갈린 첫 원인. r 이 있으면
            새 손절 거리로 다시 도출하되 승수 비율(`record.leverage / 옛 도출값`)은 유지한다.
        """
        risk_pct = self._book_of(record).risk_pct
        before = self._exposure(record.entry, record.planned_stop, risk_pct=risk_pct)
        after = self._exposure(record.entry, new_stop, risk_pct=risk_pct)
        if after is None:
            return None
        if before is None or before <= 0:
            return after
        # 승수(변동성 타게팅 · 숏 비중 · 연속손절)는 record.leverage / before 에 들어 있다.
        return after * (record.leverage / before)

    def _exposure(
        self, entry: Decimal, stop: Decimal, risk_pct: Decimal | None = None
    ) -> Decimal | None:
        """이 자리의 **명목/자본** — r 이 없으면 원장 고정 배율, 있으면 r / 손절거리 (T49).

        risk_pct 는 **제안을 낸 플레이북의 r** 이다 (T68 번들 — 한 세션에 추세(고정
        배율)와 캐리(r 도출)가 공존하므로 세션 전역이 아니라 건별로 온다). None 이면
        세션 값(risk_pct) → 그것도 None 이면 원장 고정 배율 — 동결 무변화.

        Args:
            entry: 계획 진입가 (분할이면 평단).
            stop: 손절가.
            risk_pct: 제안을 낸 플레이북의 건당 리스크 (T68 — 없으면 세션 값).

        Returns:
            `TradeRecord.leverage` 에 적을 값. 손절이 청산보다 바깥이면 None — 그 자리는 안 간다.

        Note:
            계산은 `decision.sizing.size_for` 가 한다 (절대 규칙 #4 — 수량의 SSoT 는 decision).
            여기서는 받아서 적을 뿐이다. 손절이 진입과 같아 분모가 없는 계획은 `plan_fault`
            가 어차피 거르지만, 그 전에 오면 안 가는 쪽으로 센다.
        """
        r = risk_pct if risk_pct is not None else self.risk_pct
        if r is None:
            return self.ledger.leverage
        try:
            sized = size_for(risk_pct=r, entry=entry, stop=stop, leverage_cap=self.leverage_cap)
        except ValueError:
            self.unsafe_stops += 1
            return None
        if not sized.safe:
            self.unsafe_stops += 1
            _logger.warning(
                "session_stop_outside_liquidation",
                payload={
                    "entry": str(entry),
                    "stop": str(stop),
                    "exposure": str(sized.exposure),
                    "liquidation_room": str(sized.liquidation_room),
                    "note": "손절이 청산보다 바깥이다 — 손절은 장식이고 청산이 먼저 온다. 안 간다",
                },
            )
            return None
        return sized.exposure

    def _consec_loss_scale(self) -> Decimal:
        """직전 **연속 손절** 수로 진입 노출을 줄이는 배수 (T60).

        Returns:
            2연속 손절이면 0.7 · 3연속 이상이면 0.5 · 아니면 1. 원장의 닫힌 시스템
            기록을 끝에서부터 훑어 이익이 나온 순간 멈춘다.

        Note:
            🔴 **원장을 읽어 결정론을 지킨다** (규칙 #5) — 별도 카운터를 두면 재시작·되읽기에서
            어긋난다. 사람 매매(actor≠SYSTEM)는 세지 않는다.
        """
        losses = 0
        for item in reversed(self.ledger.closed):
            if item.actor is not Actor.SYSTEM:
                continue
            gain = item.gain_pct
            if gain is not None and gain > 0:
                break
            losses += 1
        return CONSEC_CUT_3 if losses >= 3 else CONSEC_CUT_2 if losses >= 2 else Decimal(1)

    def _leg_scaled(self, exposure: Decimal, leg: str) -> Decimal:
        """다리 배율이 선언돼 있으면 노출을 **그 다리의 배율 기준으로** 바꾼다 (T291).

        Args:
            exposure: 원장 배율 기준으로 계산한 노출(크기 승수가 이미 곱해져 있다).
            leg: 진입을 낸 매매법의 귀속 키.

        Returns:
            `exposure x 다리 배율 ÷ 원장 배율`. 다리 선언이 없으면 그대로 — 동결 무변화.

        Note:
            승수(기울기·숏 비중·연속 손절)는 비율이라 그대로 남는다. 다리 배율이 원장 배율보다
            클 수는 없다(펀드가 세션 배율을 다리들의 최댓값으로 띄운다) — 크면 거래소 배율이 모자라
            주문이 거절되므로 여기서 원장 배율로 자른다.
        """
        wanted = self.leg_leverage.get(leg)
        base = self.ledger.leverage
        if wanted is None or base <= 0:
            return exposure
        return exposure * min(wanted, base) / base

    def _book_by_owner(self, owner: str) -> Playbook:
        """귀속 키로 매매법을 찾는다 — 못 찾으면 대표 매매법(지정가 경로가 쓴다)."""
        return next((item for item in self.playbooks if item.attribution == owner), self.playbook)

    def _vol_scaled(self, exposure: Decimal, book: Playbook, at: datetime) -> Decimal | None:
        """변동성 목표 크기를 곱한다 (T304 · 혼합 2.0.0-V · 320 · 321차).

        Args:
            exposure: 다리 노출까지 반영한 노출.
            book: 진입을 낸 매매법 — `entry_vol_target` 이 없으면 그대로 돌려준다(동결 무변화).
            at: 판정 봉의 시작 시각(UTC) — 이 시각까지 **끝난** UTC 일봉의 변동성을 쓴다.

        Returns:
            배수를 곱한 노출. 변동성을 모르면 None — 호출자는 이 봉을 건너뛴다(규칙 #8-1).

        Note:
            연구(`t296_wave115` V-inv)는 진입 봉 시작 시각에 `sigma_at` 을 불렀다 — 같은 자를 쓰려고
            러너가 며칠 치를 주입하고 여기서 고른다.
            배수 분포는 깔때기(`vol:up` · `vol:down`)에 센다.
        """
        target = book.entry_vol_target
        if target is None:
            return exposure
        sigma = next((value for end, value in reversed(self.ref_vol) if end <= at), None)
        if sigma is None or sigma <= 0:
            self.vol_held += 1
            self._count("vol_unknown")
            _logger.info(
                "session_entry_vol_held",
                payload={
                    "at": at.isoformat(),
                    "known": len(self.ref_vol),
                    "note": "기준(BTC) 변동성을 모른다 — 이 봉엔 새로 안 든다 (T304 · 규칙 #8-1)",
                },
            )
            return None
        mult = target.mult(sigma)
        if mult != 1:
            self._count("vol:up" if mult > 1 else "vol:down")
        return exposure * mult

    def _gate(self, at: datetime, exposure: Decimal, leg: str | None = None) -> Decimal | None:
        """펀드 문에 **이 크기로 열어도 되는지** 묻고 허용 크기를 받는다 (T279 P3 · T286).

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 이 자리에 쓰려는 실제 노출(명목/증거금).
            leg: 진입을 낸 매매법의 귀속 키 — 다리별 문(`LegAware` · T291)은 이것으로 문을 고른다.

        Returns:
            허용 크기(요청값 이하). 막히면 None — 호출자는 이 봉을 건너뛴다.

        Note:
            단일 세션(백테스트·RUN)은 문이 없어 요청값을 그대로 돌려준다 — 이 줄이 없는 것과 같다.
            줄어든 경우(`gate:fit`)를 따로 세는 이유는 §1-0s 관측 규약이다: **새 규칙이 값을
            만들면 그 값의 분포를 리포트에 싣는다.** 줄인 횟수를 안 세면 상한이 실제로 몇 번
            일했는지 알 수 없고, 123차에서 겪은 "27% 가 조용히 버려지고 있었다" 를 반복한다.
        """
        if self.entry_gate is None:
            return exposure
        gate = self.entry_gate
        given = (
            gate.grant_for(leg, at, exposure)
            if leg is not None and isinstance(gate, LegAwareGate)
            else gate.grant(at, exposure)
        )
        if given.blocked is not None:
            self.gate_held += 1
            self._count(f"gate:{given.blocked}")
            _logger.info(
                "session_entry_gate_held",
                payload={
                    "why": given.blocked,
                    "at": at.isoformat(),
                    "note": "펀드 규칙 — 이 봉엔 안 산다",
                },
            )
            return None
        if given.shrunk is not None:
            # 🔴 **장치별로 센다** — 브레이크와 총 명목 맞춤은 각각 진입의 절반 넘게 걸린다.
            #    한 칸에 섞으면 "상한이 실제로 몇 번 일했나" 를 되물을 수 없다 (§1-0s).
            self._count(f"gate:fit:{given.shrunk}")
            _logger.info(
                "session_entry_gate_fit",
                payload={
                    "want": str(exposure),
                    "granted": str(given.size),
                    "by": given.shrunk,
                    "at": at.isoformat(),
                    "note": "펀드 규칙에 맞춰 줄여서 진입 (T286)",
                },
            )
        return given.size

    def book_for(self, direction: Direction) -> Playbook:
        """그 방향의 포지션을 **맡을 매매법** — 거래소에서 되읽은 포지션의 귀속에 쓴다 (T291).

        Args:
            direction: 되읽은 포지션의 방향.

        Returns:
            그 방향의 청산 규칙을 선언한 첫 매매법. 없으면 대표 매매법(지금까지의 동작).

        Note:
            🔴 한 세션에 1H 롱 다리와 4H 숏 다리가 실려 있는데 되읽은 숏을 대표(롱 다리)로 귀속하면,
            그 숏은 **자기 청산 규칙(SMA 위 마감) 없이** 손절만 남는다. 방향별 청산 선언이 곧 "이
            매매법이 그 방향을 든다" 는 표시다. 매매법이 하나면 늘 대표라 동작이 같다.
        """
        if len(self.playbooks) < 2:
            return self.playbook
        short = direction is Direction.SHORT
        for item in self.playbooks:
            mine = (
                (item.ma_exit_above_short, item.adx_exit_short, item.macd_exit_above_short)
                if short
                else (item.ma_exit_below_long, item.adx_exit_long, item.adx_exit_above_long)
            )
            if any(value is not None for value in mine):
                return item
        return self.playbook

    def _book_of(self, held: TradeRecord) -> Playbook:
        """이 매매를 낸 플레이북 — 귀속 키로 찾는다 (T42 ③).

        Args:
            held: 보유 기록.

        Returns:
            그 플레이북. 못 찾으면(옛 기록·사람 매매) 대표 플레이북.
        """
        return next(
            (item for item in self.playbooks if item.attribution == held.playbook),
            self.playbook,
        )

    def _exit_cost(self, held: TradeRecord, outcome: Outcome) -> Decimal | None:
        """청산 유형으로 다시 센 왕복 비용 — `fill_cost` 가 꺼져 있으면 None (T42 ④).

        Args:
            held: 닫히는 기록.
            outcome: 어떻게 끝났나.

        Returns:
            새 비용 비율. None 이면 진입 때 값 그대로다.

        Note:
            진입이 지정가로 채워졌으면(`entry_fills`) 메이커, 목표 익절은 지정가라
            메이커, 나머지(손절·전환·레벨 이탈)는 시장가라 테이커다. 반익반본의 반익
            다리는 지정가일 수도 신호일 수도 있어 **테이커로 둔다** — 낙관하지 않는다.
        """
        if not self.fill_cost:
            return None
        cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(self.instrument.market)
        return cost.round_trip_by(
            entry_is_maker=len(held.entry_fills) > 0,
            exit_is_maker=outcome is Outcome.TAKE_PROFIT,
        )

    def _bid(
        self,
        setup: TradeSetup,
        bar: Candle,
        direction: Direction,
        *,
        first: Decimal,
        target: Decimal,
        cost: MarketCosts,
        owner: str,
        short_mult: Decimal | None = None,
        consec_mult: Decimal = Decimal(1),
        risk_pct: Decimal | None = None,
    ) -> None:
        """계획의 다리에 **지정가를 걸어 둔다** (T19 ④).

        Args:
            setup: 탐지기가 낸 셋업 — 다리의 가격과 비중이 여기 있다.
            bar: 진입가 축의 마지막 마감 봉.
            direction: 계획 기하가 말한 방향.
            first: 1차 익절.
            target: 전량 익절.
            cost: 비용표.
            owner: 이 후보를 낸 플레이북의 귀속 키 (T42 ③).
            short_mult: 숏 노출 배수 (T59 비대칭 레버리지). None 이면 1(롱과 동일).
            consec_mult: 연속 손절 축소 배수 (T60). 1 이면 축소 없음.
            risk_pct: 제안을 낸 플레이북의 건당 리스크 (T68 번들 — 없으면 세션 값).

        Returns:
            없다. 아직 산 것이 아니므로 원장에 안 들어간다.

        Note:
            🔴 **아직 매매가 아니다.** `_waiting` 에만 둔다 — 안 산 것을 보유로 적으면
            유령 포지션이고, 그 위에서 손절을 걸려다 실패한다 (2026-08-19 의 모양).

            ⭐ **손익비를 걸기 전에 안다.** 시장가는 얼마에 살지 모른 채 사서 체결된
            뒤에야 RR 이 정해지는데, 지정가는 값을 못 박으므로 기하 검사(`plan_fault`)가
            **주문을 내기 전에** 성립한다.

            ⚠️ **이미 지나간 가격은 기다리지 않는다.** 지금 값이 이미 그 다리보다
            유리하면 그 자리는 **바로 채워진 것으로** 본다 — 다만 체결가는 우리가 부른
            값이다. 봉이 더 갔다고 더 좋은 값을 주면 백테스트가 매번 바닥에 산다.
        """
        assert self.filler is not None
        long = direction is Direction.LONG
        trade_id = new_trade_id()
        legs = setup.entry_plan
        planned = sum((leg.ratio for leg in legs), Decimal(0))
        if self.shallow_entry and legs:
            # ⭐ **한 칸 올린다** (0.51). 다리1 을 *지금값과 원래 다리1 의 중간*으로,
            #    다리2 를 *원래 다리1* 로 — 사다리 전체가 얕아지고 체결이 쉬워진다.
            #
            # ⚠️ 비중은 그대로다. 축을 둘 흔들지 않는다 (§5.6.7).
            #
            # 🔴 **다리가 하나면 다리를 늘리지 않는다** (2026-08-20 실측 버그).
            #    예전 코드는 `else near` 로 원본을 둘째 다리에 그대로 썼고, 그러면
            #    비중이 **복제**돼 합이 2.0 이 된다 — 예산의 두 배가 나갔다:
            #
            #      박스권 (0.5, 0.5) → 0.5 · 0.5 = 1.0   OK
            #      돌파   (1.0,)     → 1.0 · 1.0 = 2.0   🔴  live_position_stacked 8 vs 4
            #
            #    0.51 의 뜻은 *"사다리를 한 칸 얕게"* 이지 *"다리를 늘린다"* 가 아니다 —
            #    다리 수를 바꾸는 것은 다른 축이다 (§5.6.7).
            near = legs[0]
            shifted = replace(near, price=(bar.close + near.price) / Decimal(2))
            legs = (
                (shifted, replace(near, ratio=sum((leg.ratio for leg in legs[1:]), Decimal(0))))
                if len(legs) > 1
                else (shifted,)
            )
        weight = sum((leg.ratio for leg in legs), Decimal(0))
        if weight <= 0:
            return
        # 🔴 **비중 합은 손대기 전과 같아야 한다.** 위 한 줄은 이번 버그를 막지만 이
        #    검사는 **앞으로 이 코드를 건드리는 누구든** 막는다 — 이번 것도 첫날에
        #    잡혔을 것이다.
        #
        # ⛔ **고쳐서 진행하지 않는다.** 몰래 정규화하면 버그가 살아 있는 채로 돌고,
        #    예산의 두 배가 나가는 것은 안전 문제다 (절대 규칙 #8).
        if weight != planned:
            self.stale_plans += 1
            _logger.error(
                "session_ladder_weight_broken",
                payload={
                    "planned": str(planned),
                    "built": str(weight),
                    "legs": len(legs),
                    "note": "사다리 비중이 계획과 다르다 — 주문을 내지 않는다",
                },
            )
            return
        average = sum((leg.price * leg.ratio for leg in legs), Decimal(0)) / weight
        # 🔴 **옮긴 진입가로 비용을 다시 잰다** (0.52 · 2026-08-20 실측).
        #
        #    탐지기는 이미 같은 검사를 한다 — 손절폭·익절폭이 왕복 비용을 못 갚으면
        #    계획을 안 만든다 (`RISK_FLOOR` · `COST_COVER`). 그런데 0.51 이 **그 뒤에**
        #    진입가를 옮긴다. 검사는 옛 값으로 통과했고 실제로 쓰는 값은 다르다.
        #
        #    숏이 박스 위에서 들어가는데 진입을 아래로 당기면 목표(박스 중앙)까지 거리가
        #    줄어든다. 실측:
        #
        #      5dc2fa  숏  진입 70,718.7  청산 70,718.6  "목표 익절"  -0.45%
        #      1c3662  숏  진입 70,878.5  청산 70,877.6  "목표 익절"  -0.45%
        #
        #    익절이 진입에서 **한 눈금 거리**(눈금 0.6)에 서고, 닿자마자 비용 때문에
        #    손실이다. 17:07~17:11 에 다섯 건이 그렇게 났다.
        #
        # ⛔ **익절가를 따라 옮기지 않는다.** 익절은 구조물(박스 중앙·반대 가장자리)이고,
        #    우리가 얕게 들어갔다고 박스 중앙이 이동하지는 않는다 — 옮기면 없는 자리를
        #    지어내는 것이다. 줄어든 거리는 **얕게 들어간 대가**이고, 그래서 답은
        #    *"남는 것이 비용도 못 갚는 자리는 안 간다"* 다.
        #
        # ⭐ 새 규칙을 만들지 않는다. **있는 자를 제때 쓴다.**
        if self.recheck_after_shift:
            risk = abs(average - setup.stop_loss)
            reward = abs(first - average)
            floor = cost.round_trip_pct * average
            if risk < floor or reward < floor:
                self.thin_after_shift += 1
                return
        # 🔴 **다리마다 본다** (사용자 확정 2026-08-20 · 실측 b3074fba4b58).
        #
        #    평균만 보면 **손절선 너머에 걸린 다리**가 통과한다. 그 다리가 채워지는 순간
        #    그 매매는 **태어나면서 이미 손절 자리**에 있다:
        #
        #      숏 진입 1.2043  손절 1.2033   ← 진입이 손절 위다 (숏은 아래여야 한다)
        #      22:29:50 진입 → 22:30:04 손절 = 14초
        #
        # ⚠️ 이것은 *"세 번 손절하고 네 번째에 먹는다"* 의 세 번 중 하나가 **아니다.**
        #    노이즈 한 틱에 죽으므로 네 번째까지 갈 기회 자체가 없다 — 표본에 섞이면
        #    승률과 RR 을 동시에 망친다 (RR 66 짜리가 그렇게 나왔다).
        #
        # ⛔ **새 문턱을 만들지 않는다.** 손절 쪽은 `RISK_FLOOR`(왕복 비용 1배)와 같은
        #    자를 쓴다 — 그 값은 산수이지 튜닝이 아니다.
        floor = cost.round_trip_pct * average
        # ⭐ 숏은 손절이 **위**에 있어야 한다 — 다리가 그 위로 가면 방향이 뒤집힌 것이다.
        short = direction is Direction.SHORT
        far = min((abs(leg.price - setup.stop_loss) for leg in legs), default=Decimal(0))
        wrong = any((leg.price > setup.stop_loss) is short for leg in legs)
        if wrong or far < floor:
            self.thin_legs += 1
            _logger.warning(
                "session_leg_too_close",
                payload={
                    "trade_id": trade_id,
                    "stop": str(setup.stop_loss),
                    "legs": [str(leg.price) for leg in legs],
                    "floor": str(floor),
                    "note": "손절선 너머이거나 비용도 못 갚는 거리다 — 걸지 않는다",
                },
            )
            return
        # ⭐ T49 — 지정가라 계획 평단이 먼저 정해진다 → 배율도 걸기 전에 정해진다.
        exposure = self._exposure(average, setup.stop_loss, risk_pct=risk_pct)
        if exposure is None:
            return
        # 🔴 **비대칭 레버리지** (T59) — 숏은 노출을 배수로 줄인다 (롱3/숏1). 롱온리 3x 를
        #    수익·낙폭 둘 다 이긴 구성이다. 롱은 그대로.
        if direction is Direction.SHORT and short_mult is not None:
            exposure *= short_mult
        # 🔴 **연속 손절 축소** (T60) — 직전 연속 손절 수로 노출을 줄인다.
        exposure *= consec_mult
        # ⭐ **탐지기가 낸 크기 승수** (T81 변동성 타게팅). 기본 1 이라 무변화다.
        #    분석이 *'이 자리는 평소보다 작게'* 를 판단하고, 수량은 여전히
        #    decision 이 정한다 — 여기서는 곱하기만 한다.
        exposure *= setup.size_mult
        exposure = self._leg_scaled(exposure, owner)  # T291 — 시장가 경로와 같은 자
        scaled = self._vol_scaled(exposure, self._book_by_owner(owner), bar.ts)  # T304 — 같은 자
        if scaled is None:
            return
        exposure = scaled
        # 🔴 **펀드의 진입 문** (T279 P3 · T286) — 시장가 경로와 같은 자다. 예전에는 `_enter` 가
        #    문을 **선언 배율**로 한 번 물었고 여기서 계산한 실제 노출과 어긋났다(위험 기반
        #    사이징일 때). 이제 각 경로가 **자기가 쓸 크기**로 묻는다.
        gated = self._gate(bar.ts, exposure, owner)
        if gated is None:
            return
        exposure = gated
        record = TradeRecord(
            trade_id=trade_id,
            playbook=owner,
            actor=Actor.SYSTEM,
            direction=direction,
            placed_at=bar.ts,
            # ⚠️ **아직 안 열렸다.** 채워질 때 `_collect` 가 이 값을 넣는다.
            opened_at=None,
            entry=average,
            planned_stop=setup.stop_loss,
            planned_target=target,
            planned_first=first,
            outcome=Outcome.OPEN,
            cost_pct=cost.round_trip_pct,
            leverage=exposure,
            hold_level=setup.hold_level,
            evidence=setup.evidence,
        )
        # 🔴 **부르기 전에 기하를 본다.** 지정가라 진입가가 이미 정해져 있으므로,
        #    성립하지 않는 계획은 **주문조차 안 나간다** (사고 ⑦ 이 하려던 일이다).
        if plan_fault(record):
            self.stale_plans += 1
            return
        record = self._tighten(record, bar)
        # 🔴 손절 하한 (T147~T150) — β 로 조인 **뒤**의 거리로 잰다.
        #    실제로 맞을 손절이 그것이기 때문이다.
        if self.stop_too_tight(record):
            self._count("stop_too_tight")
            return
        tickets: list[str] = []
        for index, leg in enumerate(legs):
            ticket = f"{trade_id}:{index}"
            self.filler.place(ticket, price=leg.price, ratio=leg.ratio, long=long)
            tickets.append(ticket)
        self._waiting = record
        self._tickets = tuple(tickets)
        self._pending_bars = 0
        # ⭐ T43 — 리테스트 다리는 계획이 사라져도 이 시각까지 둔다 (돌파는 한 봉짜리 사건).
        self._waiting_until = (
            None
            if setup.entry_lifetime_bars is None
            else bar.ts + frame_span(self.playbook.timeframe) * setup.entry_lifetime_bars
        )

    def pending_entry(self) -> tuple[TradeRecord, tuple[str, ...], datetime | None] | None:
        """대기 중인 계획과 표 이름들 — **영속화용 읽기** (T218).

        Returns:
            `(계획, 표 이름들, 대기 만료 시각)`. 대기가 없으면 None.
        """
        if self._waiting is None:
            return None
        return self._waiting, self._tickets, self._waiting_until

    def restore_pending(
        self,
        record: TradeRecord,
        tickets: tuple[str, ...],
        waiting_until: datetime | None,
    ) -> bool:
        """다시 뜬 판이 저장돼 있던 대기 계획을 **그대로 이어받는다** (T218).

        Args:
            record: 기다리던 매매.
            tickets: 아직 살아 있는(우편함에 심어진) 표 이름들.
            waiting_until: 표의 수명(T43). None 이면 계획이 사는 동안.

        Returns:
            이어받았으면 True. 이미 기다리는 것이 있거나 보유 중이면 False — 두 계획이 겹치면
            사다리가 두 번 나간다 (2026-08-21 사고와 같은 모양).

        Note:
            🔴 **판정을 새로 하지 않는다.** 이 계획은 그 봉에서 정당하게 세운 것이고, 거래소에
            그 주문이 실제로 걸려 있다. 다음 걸음의 `_collect` 가 평소처럼 채움·수명·계획 유효를
            본다 — 즉 되살린 뒤의 운명은 재시작이 없었을 때와 같다.
        """
        if self._waiting is not None or self._open is not None or not tickets:
            return False
        self._waiting = record
        self._tickets = tuple(tickets)
        self._waiting_until = waiting_until
        return True

    @property
    def waiting_trade(self) -> TradeRecord | None:
        """지정가를 걸어 두고 **아직 안 채워진** 매매 (T19 ⑤).

        Returns:
            대기 중인 기록. 없으면 None.

        Note:
            ⚠️ 러너가 주문을 낼 때 쓴다.  와 다르다 — 저것은 **가진 것**이고
            이것은 **부른 것**이다.
        """
        return self._waiting

    def _charge_model_funding(self) -> None:
        """봉이 정산 경계(00·08·16 UTC)를 넘었으면 열린 매매에 요율을 더한다 (T226 모형).

        Note:
            규칙은 발굴 스캔과 같다 — **진입 < 정산 <= 이번 봉**. 롱은 요율만큼 내고(비용 +)
            숏은 받는다. `model_funding` 이 False(라이브)면 시각만 따라간다. 요율이 없으면
            (설정에도 없음) 안 물린다 —
            0 으로 두는 것은 "보유가 길수록 유리한 백테스트" 라 여기서는 설정값이 답이다.
        """
        bar = self._tick()
        if bar is None:
            return
        previous, self._funding_tick = self._funding_tick, bar.ts
        held = self._open
        if not self.model_funding or held is None or previous is None:
            return
        boundaries = settlement_boundaries(previous, bar.ts)
        if not boundaries:
            return
        rate = self.recent_funding
        if rate is None:
            rate = (
                load_cost_table(DEFAULT_CONFIG_PATH)
                .for_market(self.instrument.market)
                .funding_pct_per_8h
            )
        if rate is None or rate == 0:
            return
        opened = held.opened_at or held.placed_at
        crossed = sum(1 for boundary in boundaries if boundary > opened)
        if crossed == 0:
            return
        charge = rate * held.direction.sign * crossed
        self.apply_funding(paid=Decimal(0), pct=charge)
        self._count("funding:settlements")

    def apply_realized_adjust(self, amount: Decimal) -> None:
        """열린 매매에 재레버 감축의 실현 손익을 더한다 (T229).

        Args:
            amount: USDT · 부호째 (거래소 `pnl` 행과 같은 부호 — 번 것이 양수).
        """
        held = self._open
        if held is None or amount == 0:
            return
        held = replace(held, realized_adjust=held.realized_adjust + amount)
        self._open = held
        self.ledger.replace(held)

    def apply_funding(
        self,
        *,
        paid: Decimal,
        pct: Decimal,
        keys: tuple[str, ...] = (),
        reset: bool = False,
    ) -> None:
        """열린 매매에 펀딩을 더한다 — 라이브(정산 기록)와 모형(경계) 둘 다 이 입구를 쓴다 (T226).

        Args:
            paid: USDT (양수 = 냈다). 모형은 0 — 명목을 모른다.
            pct: 명목 대비 비율 (양수 = 비용).
            keys: 이번에 붙인 정산 열쇠들 — 매매에 남아 재시작 뒤 같은 정산을 거른다 (0114).
            reset: 누적을 버리고 이번 값으로 시작한다 — 열쇠 없이 부푼 옛 기록을 바로잡을 때만.
        """
        held = self._open
        if held is None:
            return
        base_paid = Decimal(0) if reset else held.funding_paid
        base_pct = Decimal(0) if reset else held.funding_pct
        base_keys: tuple[str, ...] = () if reset else held.funding_keys
        held = replace(
            held,
            funding_paid=base_paid + paid,
            funding_pct=base_pct + pct,
            funding_keys=tuple(dict.fromkeys((*base_keys, *keys))),
        )
        self._open = held
        self.ledger.replace(held)

    def _collect(self, shot: Snapshot) -> None:
        """걸어 둔 지정가를 **거두거나 채운다** (T19 ③).

        Args:
            shot: 이 봉의 스냅샷 — 계획이 아직 살아 있는지 여기서 본다.

        Note:
            🔴 **원장이 "샀나" 를 계산하지 않는다.** 답은 `filler` 가 준다 — 봉인
            급전에는 봉으로 판정하는 모형이, 라이브에는 거래소가 채운 우편함이다.
            2026-08-19 사고 ③ 이 *"얼마에 팔았나"* 를 계산해서 났고, 진입 쪽에서
            같은 짓을 하면 **유령 포지션**이 된다.

            🔴 **수명은 시계가 아니라 계획이 정한다.** 30초 같은 상수는 근거가 없다 —
            주문이 살아 있어야 하는 이유는 **계획이 살아 있기 때문**이므로, 판정이
            다시 돌아 그 자리가 사라지면 거둔다.

            ⭐ **한쪽만 채워져도 매매다.** 다리1 만 채워지면 계획의 절반이 들어간
            것이고 `filled_ratio` 가 그 사실을 든다 — 손절·익절은 비율이라 성립한다.

            ⚠️ **버린 수를 센다** (`expired`). 지정가는 가격이 계속 불리하게 갈 때 제일
            잘 채워지므로, 체결률만 보면 *"좋은 자리만 놓치고 있다"* 가 안 보인다.
        """
        waiting = self._waiting
        if waiting is None or self.filler is None:
            return
        bar = self._tick()
        if bar is None:
            return
        self.waiting += 1
        got = [
            fill for ticket in self._tickets if (fill := self.filler.poll(ticket, bar)) is not None
        ]
        fills = (*waiting.entry_fills, *((item.price, item.ratio) for item in got))
        waiting = self._with_fills(waiting, fills, bar.ts)
        # ⭐ **계획이 아직 그 자리인가.** 같은 방향 후보가 남아 있으면 계속 기다린다.
        #    ⚠️ 막힌 후보(`blocked`)는 계획을 **못 지탱한다** — 진입이 보류된 자리에
        #    걸어 둔 표가 살아 있으면 보류가 말뿐이 된다 (T26 ②).
        alive = any(
            (item.setup.stop_loss > item.setup.avg_entry) is (waiting.direction is Direction.SHORT)
            for item in shot.proposals
            if not item.blocked
        )
        left = Decimal(1) - sum((ratio for _, ratio in fills), Decimal(0))
        # 🔴 **표가 전부 거절됐고 한 조각도 못 샀으면 기다릴 대상이 없다** (2026-08-25
        #    실측 · 사용자 신고 "BN 은 XRP·DOGE 말고 주문이 안 들어갔다"). 포스트온리
        #    크로스 거절(-5022/POC_IMMEDIATE) 뒤 러너는 표를 폐기했는데 세션은 후보가
        #    사는 한 계속 기다렸다 — `_waiting` 이 자리를 점유해 **다음 봉의 새 계획
        #    까지 막는** 교착. 접어야 다음 걸음이 새 봉 값으로 다시 계획한다
        #    (= "거부는 다음 판정에서 재시도" 의 실체).
        rejected_all = (
            not fills
            and bool(self._tickets)
            and all(self.filler.rejected(ticket) for ticket in self._tickets)
        )
        if rejected_all:
            _logger.info(
                "session_entry_rejected_replan",
                payload={
                    "trade_id": waiting.trade_id,
                    "tickets": len(self._tickets),
                    "note": "표가 전부 거절됐다 — 계획을 접고 다음 봉에서 새로 계획한다",
                },
            )
        # ⭐ T43 — 수명이 적힌 표(리테스트 다리)는 계획이 사라져도 그 시각까지 산다.
        patient = self._waiting_until is not None and bar.ts < self._waiting_until
        # ⭐ T234 — 만료 봉 수가 있으면 그 뒤로는 계획이 살아 있어도 표를 거둔다 (연구 `ttl_judge`).
        self._pending_bars += 1
        if self.pending_ttl_bars is not None and self._pending_bars > self.pending_ttl_bars:
            alive = False
            patient = False
        # 🔴 표가 사는 동안 **가장 가까이 온 거리**를 갱신한다 (T165 보정용).
        #    음수 = 관통. 관통했는데 안 채워지면 그것이 모형과 현실의 차이다.
        self._track_touch(waiting, bar)
        if not rejected_all and (alive or patient) and left > 0:
            self._waiting = waiting
            return
        # 계획이 사라졌거나 전부 채워졌다 — 남은 표를 거둔다.
        for ticket in self._tickets:
            self.filler.cancel(ticket)
        timed = self._waiting_until is not None
        self._waiting = None
        self._tickets = ()
        self._waiting_until = None
        if not fills:
            # ⛔ 한 조각도 못 샀다. **기록을 만들지 않는다** — 안 산 것은 매매가 아니다.
            self.expired += 1
            self._close_probe(waiting, filled=False)
            # ⭐ 취소를 보내기 **전에** 거래소가 채웠을 수 있다 — 러너가 취소 실패를 보면
            #    이것으로 되살린다 (adopt_late_fill).
            self._last_expired = waiting
            if timed:
                # 🔴 리테스트를 기다렸는데 안 돌아왔다 — "기다리면 놓친다"의 실체 (§1-0s).
                self.missed_breakouts += 1
            return
        self._open_from_waiting(waiting)

    def _with_fills(
        self,
        waiting: TradeRecord,
        fills: tuple[tuple[Decimal, Decimal], ...],
        opened_at: datetime,
    ) -> TradeRecord:
        """채워진 조각들로 대기 기록의 **평균 진입가**를 적는다 — 손절선 너머면 죽은채탄생으로 센다.

        Args:
            waiting: 대기 중인 기록.
            fills: (가격, 비중) 조각 전부 — 이전 걸음에서 채워진 것까지 합친 값.
            opened_at: 기록에 적을 진입 시각 — 봉 마감은 봉의 시각, 봉 사이 흡수는 흡수한 시각.

        Returns:
            평균 진입가·조각·시각이 적힌 기록. 조각이 없으면 받은 그대로.

        Note:
            `_collect`(봉 마감) 와 `absorb_fills`(봉 사이) 가 **같은 계산**을 쓴다.
            2026-09-06 사고(`docs/incidents/2026-09-06_limit_fill_between_bars.md`)의
            교훈은 체결 확인이 봉을 기다리면 안 된다는 것이지, 체결가를 다르게 셈해도
            된다는 것이 아니다.

            🔴 **채워진 값으로 한 번 더 본다** (사용자 확정 2026-08-20). 다리를 손절선
            안쪽에 걸어도 **가격이 뛰어넘으면 그 너머에서 채워진다.** 그러면 그 매매는
            태어나면서 이미 손절 자리에 있고, 노이즈 한 틱에 죽는다 (실측 b3074fba4b58 —
            14초).

            ⛔ **기록을 버리지 않는다.** 거래소에는 진짜 포지션이 열려 있다 — 원장에서
            빼면 **유령 포지션**이 되고, 그것이 이 프로젝트에서 가장 위험한 상태다.
            세되, **세는 것으로 끝낸다.** 표본에서 빼는 규칙은 사람이 정할 일이고
            (§5.6.7), 지금 필요한 것은 *"이런 것이 몇 건이나 되는가"* 다 (§1-0s).

            🔴 **매매 하나당 한 번만 센다** (사용자 신고 2026-08-21). 대기 중인 매매가
            있는 한 걸음마다 도는 자리라, 같은 매매 하나를 169번 세고 `죽은채탄생 163`
            으로 보인 적이 있다. 실제로는 **1건**이었다 — 숫자를 못 믿게 만드는 것이
            세지 않는 것보다 나쁘다.
        """
        if not fills:
            return waiting
        weight = sum((ratio for _, ratio in fills), Decimal(0))
        average = sum((price * ratio for price, ratio in fills), Decimal(0)) / weight
        waiting = replace(waiting, entry=average, entry_fills=fills, opened_at=opened_at)
        gap = abs(average - waiting.planned_stop)
        past = (average > waiting.planned_stop) is (waiting.direction is Direction.SHORT)
        if (past or gap < waiting.cost_pct * average) and waiting.trade_id not in self._dead:
            self._dead.add(waiting.trade_id)
            self.born_dead += 1
            # ⭐ **왜 그런지를 기록에 남긴다** (사용자 확정 2026-08-21). `note` 는
            #   이미 있는 칸이라 새 개념이 없고, 나중에 사람이 표본을 거를 근거다.
            waiting = replace(
                waiting,
                note="체결가가 손절선 너머다 — 계획 기하가 뒤집혔고 RR 통계에 안 들어간다",
            )
            _logger.error(
                "session_born_dead",
                payload={
                    "trade_id": waiting.trade_id,
                    "entry": str(average),
                    "stop": str(waiting.planned_stop),
                    "note": "체결가가 손절선 너머이거나 비용도 못 갚는 거리다 — "
                    "노이즈에 죽는다. 성과 표본으로 읽으면 안 된다",
                },
            )
        return waiting

    def _open_from_waiting(self, waiting: TradeRecord) -> None:
        """대기 기록을 **보유 포지션으로 올린다** — 남은 표를 거두고 원장에 적는다.

        Args:
            waiting: 평균 진입가가 적힌 기록 (`_with_fills` 를 거친 값).

        Note:
            `_collect` 와 `absorb_fills` 가 공유한다. 원장에 적는 순서가 두 자리에서
            다르면 한쪽만 손절 등록(`_guard_stop`)의 전제를 못 갖춘다.
        """
        if self.filler is not None:
            for ticket in self._tickets:
                self.filler.cancel(ticket)
        self._waiting = None
        self._tickets = ()
        self._waiting_until = None
        self._close_probe(waiting, filled=True)
        self.ledger.add(waiting)
        self._open = waiting
        self._count(f"entered:{waiting.playbook}")
        self.journal()

    def absorb_fills(self, at: datetime | None = None) -> bool:
        """봉 사이에 **거래소가 채운 것을 원장에 반영한다** — 판정이 아니라 사실 확인이다.

        Args:
            at: 흡수한 시각 — 라이브 러너가 준다. None 이면 마지막 마감 봉의 시각을 쓴다
                (⚠️ 재시작 직후에는 그 봉이 몇 시간 전 것일 수 있다 — 1.0.2 실측: 12:04 체결이
                11:55 로 적혔다).

        Returns:
            전량이 채워져 **포지션이 열렸으면** True. 아직 대기 중이거나 아무것도 안
            채워졌으면 False.

        Note:
            🔴 2026-09-06 실계좌 사고: 4h 판정 축에서 NEAR 지정가가 봉 중간(12:04)에
            채워졌는데 `_collect` 는 다음 걸음(16:00)까지 안 돌아 **원장이 4시간 동안
            모르고 조건부 손절이 없었다.** 대조·감사는 그 사이 고아 경보를 냈다.

            ⚠️ 여기서 **새 판정을 하지 않는다** (절대 규칙 #5). 계획이 아직 살아 있는지
            (`alive`)·표를 거둘지는 여전히 봉 마감의 `_collect` 가 정한다 — 이 함수는
            *"전부 채워졌다"* 는 사실 하나만 원장에 옮긴다. 부분 체결은 기록만 갱신하고
            표를 그대로 둔다 (나머지 다리는 계획이 정한 수명대로 산다).

            ⭐ 체결가·죽은채탄생·원장 등록은 `_collect` 와 **같은 함수**를 쓴다 —
            봉 사이에 반영됐다고 값이 달라지면 사고가 다른 모양으로 되돌아온다.
        """
        waiting = self._waiting
        if waiting is None or self.filler is None:
            return False
        bar = self._tick()
        if bar is None:
            return False
        got = [
            fill for ticket in self._tickets if (fill := self.filler.poll(ticket, bar)) is not None
        ]
        if not got:
            return False
        fills = (*waiting.entry_fills, *((item.price, item.ratio) for item in got))
        waiting = self._with_fills(waiting, fills, at or bar.ts)
        left = Decimal(1) - sum((ratio for _, ratio in fills), Decimal(0))
        if left > 0:
            self._waiting = waiting
            return False
        self._open_from_waiting(waiting)
        return True

    def _track_touch(self, waiting: TradeRecord, bar: Candle) -> None:
        """이 봉에서 지정가에 **얼마나 가까이 왔나**를 기록한다 (음수 = 관통)."""
        limit = waiting.entry
        if limit <= 0:
            return
        long = waiting.direction is Direction.LONG
        edge = bar.low if long else bar.high
        gap = (edge - limit) / limit * 100 if long else (limit - edge) / limit * 100
        near, bars = self._touch
        self._touch = (min(near, float(gap)), bars + 1)

    def _close_probe(self, waiting: TradeRecord, *, filled: bool) -> None:
        """표 하나가 끝났다 — 체결 여부와 최근접 거리를 남기고 초기화한다.

        Note:
            ⚠️ 목록은 최근 200건만 들고 있는다. 계측이 메모리를 먹으면 안 된다.
        """
        near, bars = self._touch
        if bars:
            self.fill_probe.append(
                {
                    "filled": filled,
                    "side": waiting.direction.value,
                    "limit": str(waiting.entry),
                    "touch_pct": round(near, 4),
                    "bars": bars,
                }
            )
            del self.fill_probe[:-200]
        self._touch = (float("inf"), 0)

    def adopt_late_fill(self, trade_id: str, price: Decimal, ratio: Decimal) -> TradeRecord | None:
        """만료로 거둔 지정가가 **취소 직전에 채워졌을 때** 그 체결을 보유로 되살린다.

        Args:
            trade_id: 표 이름에서 되뽑은 매매 id.
            price: 거래소가 말한 체결가.
            ratio: 그 다리의 비중.

        Returns:
            되살린 기록. 되살릴 대기 매매가 없거나 이미 보유 중이면 None.

        Note:
            🔴 2026-08-23 사고 — 세션이 대기 만료로 취소를 부탁하고 _waiting 을 비웠는데,
            거래소는 그 사이 주문을 채웠다. 러너는 취소 실패를 "이미 채워졌거나 만료"로 보고
            표를 뗐고, 체결은 아무 데도 안 실렸다 → 원장 없음 / 거래소 보유중이 남고 조건부
            손절도 안 걸렸다 (guard_stop 은 원장 보유만 본다). 계획은 만료된 대기 기록이
            그대로 들고 있으므로 지어낼 것이 없다 — 진입가·체결 시각만 사실로 바꾼다.
        """
        waiting = self._last_expired
        if waiting is None or waiting.trade_id != trade_id or self._open is not None:
            return None
        bar = self._tick()
        at = bar.ts if bar is not None else self.cursor
        record = replace(
            waiting,
            entry=price,
            opened_at=at,
            entry_fills=((price, ratio),),
            outcome=Outcome.OPEN,
        )
        self.ledger.add(record)
        self._open = record
        self._last_expired = None
        self._count(f"entered:{record.playbook}")
        self.journal()
        return record

    def _enter(self, shot: Snapshot) -> TradeRecord | None:
        """후보가 나온 **그 자리에서 즉시 산다** (시장가).

        Args:
            shot: 이 봉의 스냅샷.

        Returns:
            체결된 기록. 후보가 없으면 None.

        Note:
            🔴 **주문을 거는 순간 원장에 항목이 생긴다** (사용자 요구). 예전에는 체결
            돼야 생겨서, 시스템이 자리를 잡고 지정가를 걸어 둔 구간이 화면에서 *"아무 일도
            없는 것"* 처럼 보였다.

            ⚠️ *"계획이 있다"* 와 *"체결됐다"* 는 다르다. 봉이 실제로 그 가격을 거래해야
            체결로 센다 — 안 그러면 현재가보다 한참 아래에 지정가를 걸고 체결됐다고 세게
            된다 (실제로 19% 아래 진입이 나온 적이 있다).

            ⛔ 계획이 사라지면 **취소**로 닫는다. 그대로 두면 시스템이 더 이상 원하지
            않는 자리에 주문이 남는다.
        """
        cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(self.instrument.market)
        # 🔴 **막힌 후보로는 사지 않는다** (T26 ② · `Proposal.blocked`). 후보를 지우는
        #    것이 아니라 **진입에서만** 거른다 — 화면·반대 신호 청산은 계속 본다.
        open_to_entry = [item for item in shot.proposals if not item.blocked]
        if not self.short_allowed:
            # ⭐ T239 — 현물 시장: 숏 후보는 시장이 받지 않는다. 지우지 않고 센다 (§1-0s).
            shorts = [item for item in open_to_entry if item.setup.stop_loss > item.setup.avg_entry]
            self.short_blocked += len(shorts)
            open_to_entry = [item for item in open_to_entry if item not in shorts]
        if not open_to_entry:
            return None
        bar = self._tick()
        if bar is None:
            return None
        chosen = open_to_entry[0]
        setup = chosen.setup
        # 🔴 **귀속은 후보를 낸 플레이북이다** (T42 ③). 세션 대표(`self.playbook`)로
        #    적으면 세트의 62건이 전부 첫 플레이북으로 찍혀 도구별 손익을 못 가른다.
        owner = chosen.playbook.attribution
        target = setup.tp_ladder[-1].price if setup.tp_ladder else setup.avg_entry
        first = setup.tp_ladder[0].price if setup.tp_ladder else target
        # 🔴 **사다리를 실제 진입가로 다시 평가한다** (2026-08-18 사고의 근본 원인).
        #
        #    탐지기는 `_ladder(middle, target, entry, ...)` 에서 **탐지 시점 가격**으로
        #    *"반익이 비용을 갚는가"* 를 계산한다. 그런데 우리가 기록하는 진입가는
        #    `bar.close` — **다른 축(STEP_FRAME)의 다른 순간**이다.
        #
        #    두 값이 벌어지면 1차 익절이 진입 **아래**로 갈 수 있다. 실제로 그랬다:
        #
        #      기록 진입   64441.7          (5m 종가)
        #      1차 익절    64425.2          (15m 탐지가 기준으로 계산)
        #      reach       -16.5            ← 음수. 이 사다리는 애초에 나오면 안 됐다
        #
        #    ⇒ 4건이 그 계획으로 나가 **-4.48%** 로 끝났다. 라벨은 `반익반본` 인데
        #      1차 익절이 손실 가격이었으므로 *"익절 라벨이 붙은 손실"* 이다.
        #
        # ⇒ **탐지기와 같은 규칙을 실제 진입가로 다시 적용한다.** 반익이 비용을 못 갚으면
        #   1차를 버리고 전량 목표까지 간다 — 그것이 `_ladder` 가 원래 하는 일이고,
        #   여기서는 진입가만 진짜 값으로 바꿔 같은 판단을 다시 내리는 것이다.
        #
        # ⚠️ 자리를 버리지 않는다 (사용자 확정 2026-08-17). 진입은 하되 도구를 뺀다.
        # 방향은 계획 기하가 말한다 (아래  과 같은 규칙 — 여기가 먼저다).
        going_short = setup.stop_loss > setup.avg_entry
        if first != target and setup.tp_ladder:
            reach = (bar.close - first) if going_short else (first - bar.close)
            if bar.close <= 0 or reach / bar.close < cost.round_trip_pct * Decimal(2):
                # 반익이 비용을 못 갚는다 — 1차를 버리고 전량 목표까지 간다.
                first = target
        # ⭐ **방향은 계획 기하가 말한다.** 손절이 진입 위면 숏이다.
        direction = Direction.SHORT if setup.stop_loss > setup.avg_entry else Direction.LONG
        # ⭐ T54 C·D — 캔들 문법 진입 필터. 자리를 지우지 않고 **진입에서만** 거른다.
        if self._candle_blocks(bar, direction, chosen.playbook.playbook_id):
            return None
        # 🔴 **펀딩 극단 진입 보류** (0.8.1 · 격자 검증 §strategy_improvements §5).
        #    극단 양수 요율에 새 롱을 열면 보유 비용 최고점 + 쏠림 꼭짓점 근처를 사는
        #    셈이다 (음수 극단의 숏도 거울상). 자리를 지우지 않는다 — 다음 봉에 요율이
        #    내려오면 정상 진입한다. 보유분은 안 건드린다.
        if chosen.playbook.entry_ref_ma_gate is not None and self.ref_above is False:
            # F1 (0.3.0) — BTC 레짐이 죽어 있으면 새로 안 산다. 보유분은 안 건드린다.
            self.ref_gate_held += 1
            _logger.info(
                "session_entry_ref_gate_held",
                payload={"note": "기준(BTC) SMA 아래 — 이 봉엔 새로 안 산다 (F1)"},
            )
            return None
        band = chosen.playbook.entry_ref_return_band
        if band is not None and (self.ref_return is None or not band.holds(self.ref_return)):
            # T290 — 기준(BTC) 수익률이 띠 밖(상승·하락 국면)이면 새로 안 들어간다. 보유분은 그대로.
            # 🔴 **모르면(None) 보류한다** — `entry_ref_ma_gate` 는 모르면 잠들지만(검증된 0.2.0
            #    폴백이 있어서다) 이 매매법은 국면 밖에서 음수라 폴백이 없다. 신규 진입은 리스크
            #    증가 행동이고 분류가 불분명하면 기본값은 보류다(절대 규칙 #8-1).
            self.ref_band_held += 1
            self._count("ref_band")
            _logger.info(
                "session_entry_ref_band_held",
                payload={
                    "ref_return": None if self.ref_return is None else str(self.ref_return),
                    "band": [str(band.low), str(band.high)],
                    "note": "기준(BTC) 수익률이 띠 밖이다 — 이 봉엔 새로 안 들어간다 (T290)",
                },
            )
            return None
        surge = chosen.playbook.entry_ref_surge_cap
        if surge is not None and (self.ref_surge is None or not surge.holds(self.ref_surge)):
            # T304 #8 — 기준(BTC)이 직전 며칠 급등했으면 새로 안 들어간다. 보유분은 그대로.
            # 🔴 모르면(None) 보류한다 — 신규 진입은 리스크 증가 행동이다(절대 규칙 #8-1).
            self.ref_surge_held += 1
            self._count("ref_surge")
            _logger.info(
                "session_entry_ref_surge_held",
                payload={
                    "ref_surge": None if self.ref_surge is None else str(self.ref_surge),
                    "cap": [surge.days, str(surge.high)],
                    "note": "기준(BTC)이 직전 며칠 급등했다 — 이 봉엔 새로 안 들어간다 (T304 #8)",
                },
            )
            return None
        if chosen.playbook.entry_ref_sma_down is not None and self.ref_sma_down is not True:
            # T304 #2 — 기준(BTC) 4H SMA 가 내려가는 중일 때만 든다(311차 R2). 보유분은 그대로.
            # 🔴 모르면(None) 보류한다 — 신규 진입은 리스크 증가 행동이다(절대 규칙 #8-1).
            self.ref_sma_held += 1
            self._count("ref_sma")
            _logger.info(
                "session_entry_ref_sma_held",
                payload={
                    "ref_sma_down": self.ref_sma_down,
                    "note": "기준(BTC) 4H SMA 가 내려가는 중이 아니다 — 새로 안 든다 (T304 #2)",
                },
            )
            return None
        if funding_blocks(direction, self.recent_funding, chosen.playbook.funding_cap):
            self.funding_held += 1
            _logger.info(
                "session_entry_funding_held",
                payload={
                    "direction": direction.value,
                    "rate": str(self.recent_funding),
                    "cap": str(chosen.playbook.funding_cap),
                    "note": "펀딩 극단 — 이 봉엔 새로 안 산다",
                },
            )
            return None
        # 🔴 **지정가를 걸고 기다리지 않는다 — 방아쇠가 당겨지면 그 자리에서 산다.**
        #
        #    > *"박스권에서 위쪽으로 돌파된 시점 그때 그냥 최대한 빨리 매수한다고
        #    > 생각하면 돼. 굳이 가격이 박스권에 닿아있을 필요가 없다는 거야."*
        #
        #    탐지기가 이미 "지금이 그 순간"일 때만 후보를 내므로, 대기는 그 순간을
        #    흘려보내는 것일 뿐이었다. 실측에서 주문 45건 중 30건이 **취소**였고 —
        #    걸었다 거뒀다만 반복했다 (사용자 지적).
        # ⭐ **계획대로 걸어 두고 받는다** (T19 ④ · 지지 반등 한정).
        #
        #    사용자 지적 2026-08-19: *"숏이라면 진입가보다 높을수록 이득이고 롱이면
        #    낮을수록 이득인데, 꼬리 끝에서 진입가 사이에서 진입을 시도해야 하는 게
        #    아니냐"*. 맞고, 계획은 원래 그렇게 적혀 있다 —
        #
        #      1차  하단 레벨 상단   (발목)
        #      2차  하단 레벨 하단   (발바닥)
        #
        #    그런데 이 함수가 그 두 다리를 버리고 **지금 종가**로 시장가를 냈다. 방아쇠가
        #    *"꼬리가 닿았고 위로 마감"* 이라, 롱은 **정의상 그 봉의 최악 가격**이다.
        #
        # ⚠️ 돌파는 여전히 시장가다 — 그쪽은 *"돌파된 그 순간 최대한 빨리"* 가 규칙이고
        #    자리(zone)가 아니라 순간(moment)이다. 여기 오는 것은 반등 셋업뿐이다.
        if self.limit_entry and self.filler is not None and len(setup.entry_plan) > 0:
            return self._bid(
                setup,
                bar,
                direction,
                first=first,
                target=target,
                cost=cost,
                owner=owner,
                short_mult=chosen.playbook.short_size_mult,
                consec_mult=(
                    self._consec_loss_scale() if chosen.playbook.consec_cut else Decimal(1)
                ),
                risk_pct=chosen.playbook.risk_pct,
            )
        # ⭐ T49 — 배율은 r 과 손절 거리에서 나온다. 청산보다 바깥 손절이면 이 자리는 안 간다.
        exposure = self._exposure(bar.close, setup.stop_loss, risk_pct=chosen.playbook.risk_pct)
        if exposure is None:
            return None
        # 🔴 **비대칭 레버리지** (T59) — 숏만 노출을 배수로 줄인다 (롱3/숏1). 롱은 그대로.
        if direction is Direction.SHORT and chosen.playbook.short_size_mult is not None:
            exposure *= chosen.playbook.short_size_mult
        # 🔴 **연속 손절 축소** (T60) — 직전 연속 손절 수로 노출을 줄인다.
        if chosen.playbook.consec_cut:
            exposure *= self._consec_loss_scale()
        # ⭐ 탐지기가 낸 크기 승수 (T81). 기본 1 — 지정가 경로와 같은 규칙이다.
        exposure *= setup.size_mult
        # ⭐ T291 — 다리 배율이 선언돼 있으면 그 다리의 노출로 바꾼다(없으면 그대로).
        exposure = self._leg_scaled(exposure, owner)
        # ⭐ T304 — 변동성 목표 크기. 다리 노출 뒤 · 펀드 문 앞(연구 `size_fn` 과 같은 순서).
        scaled = self._vol_scaled(exposure, chosen.playbook, bar.ts)
        if scaled is None:
            return None
        exposure = scaled
        # 🔴 **펀드의 진입 문** (T279 P3 · T286) — 자리 · 같은 날 연속 손절 정지 · 총 명목 상한 ·
        #    낙폭 브레이크. 자리를 지우지 않고 진입에서만 거르거나 **크기를 줄인다**.
        gated = self._gate(bar.ts, exposure, owner)
        if gated is None:
            return None
        exposure = gated
        record = TradeRecord(
            trade_id=new_trade_id(),
            playbook=owner,
            actor=Actor.SYSTEM,
            direction=direction,
            placed_at=bar.ts,
            opened_at=bar.ts,
            entry=bar.close,
            planned_stop=setup.stop_loss,
            planned_target=target,
            planned_first=first,
            outcome=Outcome.OPEN,
            cost_pct=cost.round_trip_pct,
            leverage=exposure,
            hold_level=setup.hold_level,
            # 🔴 **왜 들어갔는지를 값과 함께 적는다** (T16 ①). 원장이 가격만 남기면
            #    판이 죽은 뒤에는 *"왜 들어갔는지 모르는 매매"* 가 되고, 근거는 거래소
            #    어디에도 없어 되읽을 수 없다.
            evidence=setup.evidence,
        )
        # 🔴 **진입가가 확정된 뒤에 기하를 다시 본다** (2026-08-19 사고 ⑦).
        #
        #    탐지기는 판정 축 가격으로 `risk_now > 0` 을 통과시켰다. 그런데 실제 진입은
        #    **그보다 나중**이고, 그 사이 가격이 손절선을 넘었으면 그 계획은 이미
        #    매매가 아니다 — 실측에서 롱 28건의 손절이 진입과 **같은 값**이 됐고,
        #    거래소가 `Trigger.Price must < last_price` 로 전부 거절했다.
        #
        # ⛔ **기록을 만들고 주문만 막지 않는다.** 그러면 원장에 *보유중인데 아무도 안
        #    가진* 행이 남고, 자가 점검이 그것을 이상으로 올린다.
        #
        # ⚠️ **버린 수를 센다** (§1-0s). 이 값이 크면 그것은 버그가 아니라 *"판정 축과
        #    진입 축의 간극이 너무 멀다"* 는 뜻이고, 그것이 이 실험이 답해야 할 질문이다.
        fault = plan_fault(record)
        if fault:
            self.stale_plans += 1
            return None
        record = self._tighten(record, bar)
        # 🔴 손절 하한 (T147~T150) — β 로 조인 **뒤**의 거리로 잰다.
        #    실제로 맞을 손절이 그것이기 때문이다.
        if self.stop_too_tight(record):
            self._count("stop_too_tight")
            return
        self.ledger.add(record)
        self._open = record
        self._count(f"entered:{record.playbook}")
        return record

    def buy(
        self,
        price: Decimal,
        stop: Decimal,
        target: Decimal,
        *,
        first: Decimal | None = None,
        direction: Direction = Direction.LONG,
        actor: Actor = Actor.HUMAN,
    ) -> TradeRecord:
        """사람이 손으로 산다 (T13 ④ · 차트 주문 2026-08-30).

        Args:
            price: 진입가.
            stop: 손절가.
            target: 최종 목표가.
            first: 1차 익절가. 🔴 **비우면 진입과 목표의 한가운데**로 잡는다 —
                걸어가기 화면의 옛 동작이고, 그때는 1차를 물을 자리가 없었다.
            direction: 롱/숏. 기본은 롱이다.
            actor: 누가 냈나 — 사람(기본) 또는 AI(T248 · 사람이 확인했거나 자동 모드).

        Returns:
            새 기록.

        Raises:
            RuntimeError: 이미 보유 중인 경우.

        Note:
            ⛔ **사람이 누른 것은 정답지가 아니다** (절대 규칙 #11). `actor` 로 갈라
            두는 목적은 채점이 아니라 *"시스템은 왜 이 자리를 놓쳤나"* 를 보는 것이다.

            🔴 **`first` 를 받는 이유** (사용자 확정 2026-08-30: *"그냥 사람이 정하는
            대로 다 들어가는 거야"*). 차트 주문은 1차 익절선을 **손으로 끌어서** 정한다.
            그런데 여기서 한가운데로 다시 계산하면 사람이 끈 선이 조용히 버려지고,
            화면에는 그 선이 그대로 그려져 있다 — **화면과 원장이 갈린다.**

            ⚠️ 값을 확정하는 것은 이 함수가 아니다. 청산 상한·기하 검사는
            `decision/risk/manual.confirm` 이 하고 여기는 **받은 값을 원장에 적는다**.
            확정을 두 곳에 두면 두 곳이 갈린다 (절대 규칙 #4).
        """
        if self._open is not None:
            raise RuntimeError("이미 보유 중이다 — 한 번에 한 포지션이다")
        cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(self.instrument.market)
        record = TradeRecord(
            trade_id=new_trade_id(),
            playbook=self.playbook.attribution,
            actor=actor,
            direction=direction,
            placed_at=self.cursor,
            opened_at=self.cursor,
            entry=price,
            planned_stop=stop,
            planned_target=target,
            planned_first=first if first is not None else (price + target) / Decimal(2),
            outcome=Outcome.OPEN,
            cost_pct=cost.round_trip_pct,
            leverage=self.ledger.leverage,
        )
        self.ledger.add(record)
        self._open = record
        self._count(f"entered:{record.playbook}")
        return record

    def sell(self, price: Decimal) -> TradeRecord:
        """사람이 손으로 판다.

        Args:
            price: 청산가.

        Returns:
            청산된 기록.

        Raises:
            RuntimeError: 보유 중이 아닌 경우.
        """
        held = self._open
        if held is None:
            raise RuntimeError("보유 중이 아니다")
        outcome = Outcome.TAKE_PROFIT if price >= held.entry else Outcome.STOP_LOSS
        done = held.closed(at=self.cursor, price=price, outcome=outcome)
        self.ledger.replace(done)
        self._open = None
        return done


def run_to_end(session: Session, *, limit: int = 100_000) -> Sequence[Snapshot]:
    """끝까지 걸어간다 — 시험·스모크용.

    Args:
        session: 세션.
        limit: 최대 걸음 수. 무한 루프 방지용이다.

    Returns:
        무언가 일어난 봉의 스냅샷들.

    Note:
        ⚠️ 화면은 이 함수를 쓰지 않는다. 사람이 보면서 걸어가는 것이 T13 의 목적이고,
        여기는 *"끝까지 도는가"* 를 확인하는 용도다.
    """
    out: list[Snapshot] = []
    for _ in range(limit):
        shot = session.step()
        if shot is None:
            break
        if shot.opened is not None or shot.closed or shot.proposals:
            out.append(shot)
    return out
