"""근거 플래그 — 트리거와 분리해 "이때 이 근거도 있었나"만 기록한다 (P1 §1-0k).

## 트리거와 플래그는 다른 역할이다

| | 역할 | 과탐지가 문제인가 |
|---|---|---|
| **트리거** | 진입 시점을 만든다 | ⛔ 치명적 — 연 10,376건이면 진입이 홍수가 된다 |
| **플래그** | 그 진입에 근거가 있었는지 **표시만** | ✅ **무해** |

이 구분이 축 J4 를 우회한다. `fvg`·`trendline_channel` 은 과탐지 때문에 셋업 트리거로는
못 쓰지만(`config/rules/fvg.yml`), **플래그로는 지금 그대로 켜서 잴 수 있다.** 진입은
여전히 `order_block` 이 만들고 플래그는 참/거짓만 남긴다.

결과는 둘 중 하나이고 **어느 쪽이든 답이다**:

- 한계 기여가 나온다 → 그 근거는 J4 없이도 유효하다
- 거의 모든 진입에서 참이라 `¬E` 집단이 비어 판정 불가 → 그것이 **"합류 게이트가 죽었다"의
  정량적 증거**이고, J4 의 필요성이 추측이 아니라 수치가 된다

## 왜 탐지 봉에서만 평가하는가

플래그를 모든 봉에서 돌리면 비용이 트리거와 같아진다(5m 1년치 = 105,120봉). 그런데
필요한 것은 **진입이 생긴 봉의 상태**뿐이다 — 연 514건이면 0.5% 만 평가하면 된다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from updown.analysis.detectors.base import MarketContext, SetupDetector
from updown.analysis.structures.channel_state import ChannelStance, channel_state
from updown.common.domain.instrument import Timeframe
from updown.common.domain.trend import TrendDirection


@runtime_checkable
class FlagEvaluator(Protocol):
    """근거 하나가 이 시점에 참인지 답한다.

    Note:
        `SetupDetector` 와 별도 프로토콜인 이유는 **반환값이 다르기** 때문이다.
        탐지기는 진입 계획이 담긴 `TradeSetup` 을 만들지만, 플래그는 참/거짓만 낸다.
        플래그가 셋업을 만들 수 있으면 "표시만 한다"는 계약이 깨진다.
    """

    @property
    def name(self) -> str:
        """원장에 기록될 이름. `rule_id@version` 처럼 버전이 붙는 것이 좋다."""
        ...

    def holds(self, context: MarketContext, trend: TrendDirection | None) -> bool:
        """이 시점에 근거가 성립하는가.

        Args:
            context: 탐지 시점의 시장 맥락.
            trend: 상위 TF 추세. 색인을 주지 않았으면 None.

        Returns:
            참/거짓.
        """
        ...


@dataclass(frozen=True, slots=True)
class DetectorFlag:
    """탐지기를 플래그로 감싼다 — **셋업을 하나라도 냈으면 참**.

    Attributes:
        detector: 감쌀 탐지기.
        label: 원장에 쓸 이름.

    Note:
        진입 계획·손절·익절을 **버린다.** 플래그는 "있었나"만 답하므로 그 값들이 필요
        없고, 들고 있으면 나중에 누가 그걸로 진입을 만들고 싶어진다.

        과탐지 룰이어도 안전한 것이 이 래퍼의 핵심이다 — 셋업 개수가 몇이든 참/거짓
        하나로 접힌다.

        ⚠️ **속도 문제를 풀지는 않는다.** `SetupDetector.detect` 는 리스트를 돌려주므로
        과탐지 비용은 여전히 탐지기가 낸다. 이 래퍼가 막는 것은 **진입 홍수**이지
        연산량이 아니다.
    """

    detector: SetupDetector
    label: str

    @property
    def name(self) -> str:
        """원장에 기록될 이름."""
        return self.label

    def holds(self, context: MarketContext, trend: TrendDirection | None) -> bool:
        """탐지기가 이 맥락에서 셋업을 냈는가.

        Args:
            context: 탐지 시점의 시장 맥락.
            trend: 사용하지 않는다 (탐지기가 필요하면 `context` 에서 읽는다).

        Returns:
            셋업이 하나라도 있으면 True.
        """
        del trend
        return any(True for _ in self.detector.detect(context))


@dataclass(frozen=True, slots=True)
class TrendFlag:
    """상위 TF 추세가 상승인가 (§5.5 `추세` 카테고리).

    Attributes:
        label: 원장에 쓸 이름.

    Note:
        §5.4-2 추세 **게이트**와 재료는 같지만 역할이 다르다. 게이트는 역추세 셋업을
        **버리고**, 이 플래그는 **기록만** 한다. 게이트를 끈 채(`apply_gate=False`)
        플래그로 재면 "추세가 승률을 얼마나 올리는가"가 한계 기여로 나온다 — 버려진
        셋업은 애초에 표본에 없어서 게이트 상태로는 그 질문에 답할 수 없다.
    """

    label: str = "trend_up"

    @property
    def name(self) -> str:
        """원장에 기록될 이름."""
        return self.label

    def holds(self, context: MarketContext, trend: TrendDirection | None) -> bool:
        """상위 TF 추세가 상승인가.

        Args:
            context: 사용하지 않는다.
            trend: 상위 TF 추세.

        Returns:
            상승이면 True. **추세를 모르면 False** 가 아니라 호출부가 미평가로
            처리해야 하지만, 색인이 없는 실행에서는 이 플래그를 아예 빼는 것이 맞다.
        """
        del context
        return trend is TrendDirection.UP


@dataclass(frozen=True, slots=True)
class VolumeSurgeFlag:
    """직전 평균 대비 거래량이 배수 이상인가 (§5.5 `거래량·유동성` 카테고리).

    Attributes:
        multiple: 참으로 볼 배수.
        timeframe: 어느 시간축의 지표를 볼지.
        label: 원장에 쓸 이름.

    Note:
        지금은 `order_block` **내부 가산점**으로만 쓰이고 독립 표가 아니다 (§1-0i 표).
        플래그로 꺼내면 "거래량이 실제로 승률을 올리는가"를 따로 잴 수 있다 — 내부
        가산으로 묻혀 있으면 그 질문 자체가 성립하지 않는다.

        배수를 직접 계산하지 않고 **이미 있는 `Indicators.volume_ratio` 를 읽는다**
        (`indicators/ma.py`). 같은 값을 두 번 구현하면 언젠가 갈라지고, 그때 어느 쪽이
        맞는지 알 수 없다 (절대 규칙 #9 의 취지와 같다).

        임계값은 설정에서 주입한다 (코딩 규약 "임계값·배수는 코드에 박지 않는다").
    """

    multiple: float
    timeframe: Timeframe
    label: str = "volume_surge"

    @property
    def name(self) -> str:
        """원장에 기록될 이름 — 배수를 이름에 넣어 프리셋을 구분한다."""
        return f"{self.label}@{self.multiple:g}x"

    def holds(self, context: MarketContext, trend: TrendDirection | None) -> bool:
        """마지막 봉의 거래량 배수가 임계 이상인가.

        Args:
            context: 탐지 시점의 시장 맥락.
            trend: 사용하지 않는다.

        Returns:
            배수 이상이면 True.

        Note:
            **배수를 산출할 수 없으면 False** 다 (`volume_ratio is None`). 봉이 모자란
            구간을 참으로 보면 시계열 앞자락이 전부 근거 있는 진입이 된다.
        """
        del trend
        indicators = context.indicators.get(self.timeframe)
        if indicators is None:
            return False
        ratio = indicators.volume_ratio
        return ratio is not None and ratio >= self.multiple


@dataclass(frozen=True, slots=True)
class ChannelStanceFlag:
    """가격이 채널 **영역**의 어디에 있는가 (P1 §1-0o).

    Attributes:
        stance: 참으로 볼 위치.
        timeframe: 판정할 시간축.

    Note:
        ## 왜 레벨이 아니라 영역인가

        추세선을 "닿았나"로 쓰면 선이 창당 100개일 때 아무 가격이나 닿아 필터가 죽는다
        (축 J4). 반면 **위치**는 선이 몇 개든 각 채널이 위/안/아래 하나로 접히므로
        개수에 오염되지 않는다 — 그래서 §1-0m 이 추세선을 투영에서 뺀 근거가 이
        플래그에는 해당하지 않는다.

        ## 왜 플래그로 먼저 넣는가

        게이트로 넣으면 그 즉시 표본이 바뀌어 "채널 위치가 승률을 올리는가"를 잴 수
        없다. 플래그는 진입을 만들지 않으므로 §4.14 한계 기여로 **먼저 재고**, 그
        결과를 보고 게이트 여부를 판단한다 (`DetectorFlag` 와 같은 논거).

        ⛔ 진입 모드 자동 선택(채널 위=돌파 / 안=눌림목)은 §4.15 Market Regime(P1-12)
        소관이다. 그전에 넣으면 국면 정의를 성과에 맞춰 고르게 된다 (§5.6.2).
    """

    stance: ChannelStance
    timeframe: Timeframe

    @property
    def name(self) -> str:
        """원장에 기록될 이름."""
        return f"channel_{self.stance.value}"

    def holds(self, context: MarketContext, trend: TrendDirection | None) -> bool:
        """이 시점의 채널 위치가 지정한 값인가.

        Args:
            context: 탐지 시점의 시장 맥락.
            trend: 사용하지 않는다.

        Returns:
            일치하면 True. 채널이 없으면 `UNKNOWN` 만 참이다 — "채널이 없다"와
            "채널 안에 있다"를 구분한다 (절대 규칙 #8).
        """
        del trend
        geometry = context.geometry.get(self.timeframe)
        candles = context.candles.get(self.timeframe)
        if geometry is None or not candles:
            return self.stance is ChannelStance.UNKNOWN
        last = len(candles) - 1
        state = channel_state(candles[last].close, geometry.channels, last)
        return state.stance is self.stance


def evaluate_flags(
    flags: Sequence[FlagEvaluator],
    context: MarketContext,
    trend: TrendDirection | None,
) -> frozenset[str]:
    """이 시점에 참인 플래그 이름들.

    Args:
        flags: 평가할 플래그들.
        context: 탐지 시점의 시장 맥락.
        trend: 상위 TF 추세.

    Returns:
        참인 플래그 이름 집합.

    Note:
        평가된 전체 목록은 호출부가 `frozenset(f.name for f in flags)` 로 따로 든다 —
        **"거짓"과 "평가 안 함"을 가르려면 둘 다 필요하다** (`evidence.EvidenceLedger`).
    """
    return frozenset(flag.name for flag in flags if flag.holds(context, trend))
