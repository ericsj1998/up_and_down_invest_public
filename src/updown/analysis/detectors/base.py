"""셋업 탐지 플러그인 계약 (spec §4.3.1).

> 새로운 차트 개념(강의·경험에서 얻는 룰)을 **파일 하나 추가하는 수준**으로 언제든
> 등록할 수 있어야 한다.

계약은 네 조각이다:

1. `RuleParams` — 임계값·배수를 **코드가 아닌 설정에서** 주입받는 통로
2. `MarketContext` — 플러그인이 공유하는 공용 재료 (멀티 TF 캔들·지표·구조물)
3. `SetupDetector` — 플러그인이 구현할 프로토콜
4. `TradeSetup` — 출력 (`common.domain.setup`)

`MarketContext` 가 구조물을 공유하는 것이 핵심이다. 남의 구조물이 보여야
"오더블록+FVG 중첩", "추세선과 겹치는 자리" 같은 **합류(confluence) 판정**이 가능하다.
플러그인마다 스윙 포인트를 따로 계산하면 합류를 볼 수 없다.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Protocol, runtime_checkable

from updown.analysis.indicators.seasonal_volume import VolumeBaseline
from updown.analysis.structures.bundle import StructureBundle
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.reports import Indicators
from updown.common.domain.setup import TradeSetup
from updown.common.domain.structure import Structure
from updown.common.domain.trade_tick import BarDelta
from updown.common.domain.trend import TrendState

type ParamValue = Decimal | int | float | bool | str
"""룰 파라미터로 허용하는 값 타입.

`Any` 를 쓰지 않는다 (CLAUDE.md 규약 3). 임계값·배수·스위치·라벨이면 충분하며,
중첩 구조가 필요해지면 그것은 파라미터가 아니라 룰 자체의 설계 문제다.
"""


@dataclass(frozen=True, slots=True)
class RuleParams:
    """룰 파라미터 — 설정(DB/YAML)에서 주입되는 임계값 묶음 (spec §4.3.1).

    Attributes:
        rule_id: 룰 식별자. 예: `order_block`.
        version: 룰 버전. 개정 시 증가하며 성과가 `rule_id@version` 단위로 귀속된다
            (spec §4.3.1, §4.14).
        values: 파라미터 이름 → 값.

    Note:
        **임계값을 코드에 박지 않는다** (spec §4.3.1, CLAUDE.md 규약 1). 관리자
        페이지에서 룰별 on/off·파라미터 수정·버킷별 적용 여부를 제어할 수 있어야 하는데,
        하드코딩된 값은 그 화면에서 보이지 않는다.
    """

    rule_id: str
    version: str
    values: Mapping[str, ParamValue]


@dataclass(frozen=True, slots=True)
class MarketContext:
    """플러그인이 공유하는 분석 재료 (spec §4.3.1).

    Attributes:
        instrument: 대상 종목.
        as_of: 분석 기준 시각 (UTC).
        candles: 타임프레임별 캔들. `ts` 오름차순이다.
        indicators: 타임프레임별 공용 지표 (MA/ATR/RSI/거래량 등).
        structures: **저장소에서 읽은** 활성 구조물 (spec §4.13 as-of 렌더링용).
        geometry: 타임프레임별 **이번 창에서 작도한** 공용 구조물 — 스윙·추세선·채널·박스.
            **합류(confluence) 판정의 재료다.**
        trend: 타임프레임별 추세 상태 (spec §4.16). 워밍업이 모자란 시간축은 **키가
            아예 없다** — `None` 값을 넣지 않는 이유는 "판정 불가"와 "안 넣었다"를
            구분할 필요가 없기 때문이다. 둘 다 게이트에서 `NO_TREND` 로 기각된다.
        volume_baseline: 거래량 배수 색인 (T03). None 이면 탐지기가 **옛 20봉 평균**으로
            떨어진다 — 그 폴백이 있는 이유는 합성 테스트가 전체 이력을 못 만들기
            때문이고, 측정 경로에서는 항상 채운다.

    Note:
        ⚠️ **`structures` 와 `geometry` 는 다른 것이다.** 전자는 DB 행에서 온 이력이라
        기울기 같은 기하 정보가 없고, 후자는 이번 창에서 계산한 작도 결과다. P1-6 실측
        전까지 플러그인들은 어느 쪽도 쓰지 않아 **합류 점수가 항상 0** 이었고, `fvg` 는
        합류가 게이트라 셋업이 영원히 0건이었다 (`structures/bundle.py` 참조).

        `geometry` 에 기본값을 두지 않은 것이 의도다. 비워도 되게 하면 "구조물을 안
        넘겼다"가 조용히 "합류 없음"이 되고, 그것이 위 결함의 형태였다 (절대 규칙 #8).
        **미래 참조(lookahead) 금지** — `as_of` 이후의 캔들은 여기에 들어오지 않는다.
        백테스트에서는 `BacktestAdapter` 가 데이터 접근 자체를 막아 이를 강제한다
        (spec §4.11). 플러그인이 선의로 지키는 것에 의존하지 않는다.

        구조물은 **삭제되지 않고 상태 전이**한다 (spec §4.3.1). 여기 담기는 것은
        `StructureStatus.ACTIVE` 인 것들이며, 무효화된 구조물의 이력은
        `structures` 테이블에 남아 as-of 렌더링(spec §4.13)에 쓰인다.
    """

    instrument: Instrument
    as_of: datetime
    candles: Mapping[Timeframe, Sequence[Candle]]
    indicators: Mapping[Timeframe, Indicators]
    structures: Sequence[Structure]
    geometry: Mapping[Timeframe, StructureBundle]
    trend: Mapping[Timeframe, TrendState]
    volume_baseline: VolumeBaseline | None = None
    deltas: tuple[BarDelta, ...] = ()
    """봉 델타 (매수/매도 체결량 · 15m · 시각 오름차순) — T28 CVD 확인의 재료.

    🔴 **비어 있는 것이 기본이다.** 델타를 선언하지 않은 룰은 이 칸을 안 보고,
    그래서 동결 버전은 한 글자도 안 달라진다 (§5.6.2). CVD 를 선언한 룰이 이 칸이
    비어 있으면 **판정 불가**로 세고 진입하지 않는다 — 없는 값으로 확인한 척하지
    않는다 (절대 규칙 #8).

    ⚠️ as-of 는 부르는 쪽이 아니라 **탐지기가** 지킨다 — 여기엔 로드된 전체가 실리고,
    탐지기는 판정 봉 시각 이하만 잘라 쓴다 (`CvdSeries` 규약).
    """


class RuleDetectorBase:
    """`SetupDetector` 의 `id` · `version` · `params` 세 프로퍼티 — 룰 파일마다 다시 적지 않는다.

    복제 정리(2026-09-06)에서 탐지기 8개가 같은 세 프로퍼티를 각자 들고 있던 것을 모았다.
    구현체는 `rule_params` 를 채우기만 한다 (dataclass 필드든 `__init__` 대입이든).

    Attributes:
        RULE_ID: 상수 id 를 쓰는 룰은 여기 적는다. None 이면 **설정의 `rule_id`** — 같은 탐지기를
            별칭(`<id>_ema` 꼴)으로 나란히 돌리는 길이라 변형이 있는 탐지기가 이 규칙이다.
        RULE_VERSION: 상수 버전. None 이면 설정의 `version` (성과가 `id@version` 으로 귀속된다).
        rule_params: 설정에서 주입된 파라미터.
    """

    RULE_ID: ClassVar[str | None] = None
    RULE_VERSION: ClassVar[str | None] = None
    rule_params: RuleParams

    @property
    def id(self) -> str:
        """룰 식별자 — 상수가 있으면 상수, 없으면 설정의 것."""
        return self.RULE_ID or self.rule_params.rule_id

    @property
    def version(self) -> str:
        """룰 버전 — 상수가 있으면 상수, 없으면 설정의 것."""
        return self.RULE_VERSION or self.rule_params.version

    @property
    def params(self) -> RuleParams:
        """주입된 파라미터 — 측정 리포트가 그대로 싣는다."""
        return self.rule_params


@runtime_checkable
class SetupDetector(Protocol):
    """셋업 탐지 플러그인 (spec §4.3.1).

    구현체는 `analysis/detectors/` 에 파일 하나로 추가하고 레지스트리에 한 줄
    등록한다. 예정 플러그인: `order_block`(§6.3), `trendline_channel`(§6.5),
    `fvg`(§6.6), `cup_handle`(§6.7), `diamond`(§6.8).

    Note:
        **롱 온리** — 하락형 구조물(하락 FVG, IFVG, 공급 오더블록)을 탐지하더라도
        그것은 진입 근거가 아니라 청산·회피·익절 근거다 (spec §12.8, 절대 규칙 #10).

        출력은 **완결된 계획 템플릿**이어야 한다. 분할 진입·손절·익절 사다리·스탑
        정책 힌트가 다 채워진 `TradeSetup` 이며, "숫자 없는 매수 추천"은 금지다
        (spec §4.3.1).
    """

    @property
    def id(self) -> str:
        """룰 식별자. 예: `order_block`, `fvg`, `cup_handle`, `trendline_channel`."""
        ...

    @property
    def version(self) -> str:
        """룰 버전. 개정 시 증가하며 백테스트 성과 귀속 단위가 된다."""
        ...

    @property
    def params(self) -> RuleParams:
        """설정에서 주입된 파라미터."""
        ...

    def detect(self, ctx: MarketContext) -> list[TradeSetup]:
        """공유 재료에서 셋업을 탐지한다.

        Args:
            ctx: 분석 재료. `ctx.as_of` 이후 데이터는 들어 있지 않다.

        Returns:
            탐지된 셋업들. 없으면 빈 리스트다 — **"셋업 없음"도 유효한 답**이며
            없는 진입가를 지어내지 않는다 (spec §4.20).

        Note:
            **결정론적이어야 한다** (원칙 P1). 난수·현재시각을 직접 참조하지 않는다.
            시각이 필요하면 `ctx.as_of` 를 쓴다 — 그래야 백테스트가 재현된다.
        """
        ...


type DetectorFactory = Callable[[RuleParams], SetupDetector]
"""룰 파라미터 → 탐지기. entry point `updown.detectors` 가 등록하는 것이 이것이다 (T224)."""
