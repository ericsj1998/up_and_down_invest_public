"""추세선·평행 채널 작도 — **순수 함수** (P1-1-3, P1-1-4 · spec §6.5, §4.3.1).

## 꼬리 끝 기준은 여기서 지킬 것이 없다

spec §6.5 의 전역 규칙("추세선은 꼬리 끝 기준으로 작도 — 몸통 아님")은 `swing.py` 에서
이미 강제된다. 스윙 하이의 가격이 `high`, 로우가 `low` 이므로 그 좌표를 앵커로 쓰면
자동으로 꼬리 끝이다. **이 모듈은 `open`/`close` 를 앵커 좌표로 쓰지 않는다** — 규칙을
한 곳에서만 지키면 어기는 경로가 생기지 않는다.

몸통(`open`/`close`)은 다른 용도로 쓴다: **유효성 검증**이다 (아래).

## 유효성 판정이 §6.5 의 진입 조건과 같은 규칙이다

§6.5 의 진입은 "추세선 위에 캔들 몸통이 있고 아래로 꼬리가 걸리는 형태"다. 뒤집으면
**몸통이 선을 넘은 선은 애초에 그 추세선이 아니다.** 그래서 접점 수만 세지 않고 구간 내
몸통 이탈 봉을 세어 0 이어야 유효로 인정한다 (`max_body_violations`).

꼬리 이탈은 위반이 아니다. 그것이 §6.5 가 말하는 정상 형태다.

## 결측 구간을 가로지르는 선은 그리지 않는다 ⚠️

x축이 **봉 번호**이므로, 82봉이 빠진 구멍을 가로질러 두 극값을 이으면 기울기가 무의미하다
— 없는 봉만큼의 가격 변화가 한 칸에 압축된다. 거래소 점검 중 가격이 튀면 그 선은 순전히
인공물이다.

→ 두 앵커가 **같은 연속 구간**에 있을 때만 후보로 삼는다.

반면 `box.py` 의 **수평 레벨에는 이 제한이 없다.** 점검 전후에 같은 가격이 막혔다면 그것은
여전히 같은 레벨이다 — 수평선은 기울기가 없어 없는 봉의 영향을 받지 않는다. 기울어진
선만 봉 간격에 의존한다.

## 구간(span) 안에서만 검증한다

몸통 이탈 검사는 **첫 앵커 ~ 마지막 앵커** 사이만 본다. 마지막 앵커 이후는 미래이고,
그곳의 몸통 이탈은 "이 선이 잘못 그려졌다"가 아니라 **"이 선이 깨졌다"**는 생애주기
사건(`StructureStatus.INVALIDATED`)이다. 둘을 섞으면 깨진 선을 애초에 없던 선으로
취급해 as-of 렌더링(spec §4.13)에서 과거 재현이 불가능해진다.

## 결정론 (원칙 P1)

모든 Decimal 연산을 **고정 컨텍스트**에서 수행한다 (`numeric.fixed_context`). 주변
코드가 `getcontext().prec` 을 바꿔도 결과가 같아야 골든 스냅샷이 성립한다. 기울기는
생성 시점에 양자화해 저장하므로 **저장된 값과 계산에 쓰는 값이 동일**하다 — 스냅샷과
실행이 어긋나지 않는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import combinations

from updown.analysis.structures.params import (
    ChannelParams,
    OverdetectionGuard,
    TrendlineParams,
)
from updown.analysis.structures.swing import SwingKind, SwingPoint, contiguous_segments
from updown.analysis.structures.tolerance import (
    TOUCH_ATR_MULTIPLE,
    AtrTolerance,
    from_candles,
)
from updown.common.domain.candle import Candle
from updown.common.domain.structure import Anchor
from updown.common.numeric import DECIMAL_CONTEXT, fixed_context

_SLOPE_QUANTUM = Decimal("0.000000001")
"""기울기 양자화 단위(1e-9 가격/봉).

240봉 구간에서 누적 오차가 2.4e-7 이며, 접점 허용 오차(10bp)보다 12자리 작다.
양자화 자체가 목적이다 — 저장값과 계산값을 같게 만들어 스냅샷 드리프트를 없앤다.
"""


class TrendlineKind(StrEnum):
    """추세선 종류.

    Attributes:
        SUPPORT: 지지선 — 스윙 **로우**들을 이은다. 앵커 가격은 `low`.
        RESISTANCE: 저항선 — 스윙 **하이**들을 이은다. 앵커 가격은 `high`.
    """

    SUPPORT = "support"
    RESISTANCE = "resistance"


@dataclass(frozen=True, slots=True)
class Line:
    """봉 번호를 x축으로 하는 직선.

    Attributes:
        base_index: 기준 봉 번호.
        base_price: 기준 봉에서의 선 가격.
        slope_per_bar: 봉 1개당 가격 변화. 양자화되어 있다.

    Note:
        x축이 **시간이 아니라 봉 번호**다. 결측 구간에서 시간을 x축으로 쓰면 없는 봉
        만큼 시간이 흘러 선이 완만해진다 (`swing.py` 의 결측 처리와 같은 이유).
    """

    base_index: int
    base_price: Decimal
    slope_per_bar: Decimal

    def price_at(self, index: int) -> Decimal:
        """봉 번호에서의 선 가격.

        Args:
            index: 봉 번호.

        Returns:
            `base_price + slope x (index - base_index)`. 미래 봉 번호도 받는다 — 연장선.
        """
        return self.base_price + self.slope_per_bar * (index - self.base_index)

    def shifted(self, offset: Decimal) -> "Line":
        """같은 기울기로 평행 이동한 선 — 채널의 반대편을 만든다.

        Args:
            offset: 가격 오프셋 (음수면 아래).

        Returns:
            새 선. 원본은 그대로 (불변).
        """
        return Line(self.base_index, self.base_price + offset, self.slope_per_bar)


@dataclass(frozen=True, slots=True)
class Trendline:
    """유효 추세선 (spec §6.5).

    Attributes:
        kind: 지지선/저항선.
        line: 기하 정보.
        touches: 이 선에 닿은 스윙들. **전부 꼬리 끝 좌표**다.
        body_violations: 구간 내 몸통 이탈 봉 수.

    Note:
        `touches` 는 2개가 아니라 실제 닿은 전부다. 접점 수가 곧 이 선의 신뢰도이며
        (spec §6.5 진입 근거), 합류 판정에도 쓰인다.
    """

    kind: TrendlineKind
    line: Line
    touches: tuple[SwingPoint, ...]
    body_violations: int

    @property
    def touch_count(self) -> int:
        """접점 수 — 클수록 시장이 반복적으로 인정한 선이다."""
        return len(self.touches)

    @property
    def span_bars(self) -> int:
        """첫 접점 ~ 마지막 접점 봉 수."""
        return self.touches[-1].index - self.touches[0].index

    def anchors(self) -> tuple[Anchor, ...]:
        """영속화용 앵커 좌표 (spec §4.13 `lines[].anchors`).

        Returns:
            접점들의 앵커 — 선을 다시 그릴 때 기울기가 아니라 좌표를 저장한다.
        """
        return tuple(touch.to_anchor() for touch in self.touches)


@dataclass(frozen=True, slots=True)
class Channel:
    """평행 채널 (spec §6.5).

    Attributes:
        basis: 채널의 근거가 된 유효 추세선.
        lower: 하단 경계.
        upper: 상단 경계.
        opposite_touches: 반대편 경계에 닿은 스윙들.

    Note:
        spec §6.5 의 "채널 상단 도달 시 절반 익절"과 "채널 복귀 봉마감 진입"이 이
        경계를 기준으로 판정된다. 단, **판정 자체는 이 모듈이 하지 않는다** — 리테스트는
        §4.3.2 공용 로직의 몫이고 여기는 경계를 제공할 뿐이다.
    """

    basis: Trendline
    lower: Line
    upper: Line
    opposite_touches: tuple[SwingPoint, ...]


def _segment_index(candles: Sequence[Candle]) -> list[int]:
    """봉마다 소속 연속 구간 번호를 매긴다.

    Note:
        구간 경계는 결측이다. 두 앵커가 다른 번호를 가지면 그 사이에 없는 봉이 있다는
        뜻이므로 선을 긋지 않는다 (모듈 docstring).
    """
    labels = [0] * len(candles)
    for number, (start, end) in enumerate(contiguous_segments(candles, candles[0].timeframe)):
        for index in range(start, end):
            labels[index] = number
    return labels


def _line_through(first: SwingPoint, second: SwingPoint) -> Line:
    """두 스윙을 지나는 선. 기울기는 양자화해 저장값과 계산값을 같게 만든다.

    Note:
        나눗셈과 양자화 **둘 다** 고정 컨텍스트로 한다. 나눗셈만 고정하면 `quantize` 가
        주변 컨텍스트를 써서 좁은 정밀도에서 `InvalidOperation` 이 난다 — P1-1 개발 중
        실제로 겪었다.
    """
    slope = DECIMAL_CONTEXT.divide(second.price - first.price, Decimal(second.index - first.index))
    return Line(first.index, first.price, DECIMAL_CONTEXT.quantize(slope, _SLOPE_QUANTUM))


def _touching(
    line: Line,
    pivots: Sequence[SwingPoint],
    tolerance: AtrTolerance,
    span: tuple[int, int] | None = None,
    min_gap_bars: int = 0,
) -> tuple[SwingPoint, ...]:
    """선에 닿은 스윙들 — 허용 오차는 **그 봉의 ATR 배수**다 (`Phase01` §1-0h).

    Args:
        line: 대상 직선.
        pivots: 판정할 스윙들.
        tolerance: 봉별 허용 오차.
        span: `[start, end]` 봉 범위. 주면 **이 범위 안만** 센다 (아래 Note).
        min_gap_bars: 접점으로 따로 셀 최소 봉 간격. 0 이면 제한 없다.

    Returns:
        접점들. `index` 오름차순이다.

    Note:
        허용 오차가 **ATR 배수**인 이유: 고정 %는 시간축 사이에서 4.1배, 같은 시간축의
        저·고변동 구간 사이에서 2배 넘게 다른 뜻이 된다 (`structures/tolerance.py` 실측).
        절대 금액은 애초에 종목 간 이식이 안 된다.

        **`span` 밖의 접점은 세지 않는다 (외삽 금지).** 두 앵커로 그은 선을 앵커 밖으로
        연장해 접점을 세면, 거의 수평인 선이 200봉 밖에서 허용 오차 띠로 아무 스윙이나
        긁어 온다 — 봉 6~45 로 그은 선을 봉 242 의 점으로 "확인"하는 셈이다. 기울기
        불확실성은 외삽 거리에 비례해 커지므로 그 확인은 근거가 없다.

        손실은 없다. 후보를 **모든 쌍**으로 만들기 때문에, 봉 6 과 242 가 진짜 한 선에
        있다면 그 쌍이 직접 후보가 되고 중간 접점들은 내삽으로 세어진다.

        **`min_gap_bars` 보다 가까운 접점은 같은 사건으로 본다.** 붙어 있는 두 봉이
        각각 선에 닿는 것은 접점 2개가 아니라 이중 바닥 하나다. `min_anchor_distance_bars`
        를 앵커 쌍에만 적용하면 그 파라미터가 절반만 지켜진다.
    """
    touches: list[SwingPoint] = []
    for pivot in pivots:
        if span is not None and not span[0] <= pivot.index <= span[1]:
            continue
        expected = line.price_at(pivot.index)
        if expected <= 0:
            continue
        if abs(pivot.price - expected) > tolerance.at(pivot.index):
            continue
        if touches and pivot.index - touches[-1].index < min_gap_bars:
            continue
        touches.append(pivot)
    return tuple(touches)


def _count_body_violations(
    candles: Sequence[Candle],
    line: Line,
    kind: TrendlineKind,
    span: tuple[int, int],
    tolerance: AtrTolerance,
) -> int:
    """구간 내에서 몸통이 선을 넘은 봉 수 (spec §6.5).

    Note:
        꼬리는 세지 않는다. "몸통은 선 위, 꼬리는 걸려도 된다"가 §6.5 의 정상 형태다.
    """
    violations = 0
    for index in range(span[0], span[1] + 1):
        candle = candles[index]
        expected = line.price_at(index)
        if expected <= 0:
            continue
        margin = tolerance.at(index)
        if kind is TrendlineKind.SUPPORT:
            if min(candle.open, candle.close) < expected - margin:
                violations += 1
        elif max(candle.open, candle.close) > expected + margin:
            violations += 1
    return violations


def _slope_budget(candles: Sequence[Candle]) -> Decimal:
    """봉당 허용 기울기 크기 — **창당 한 번만** 구한다 (축 J4-D).

    Args:
        candles: 작도 창 전체.

    Returns:
        `창 고저폭 / 창 봉 수`. 봉이 모자라거나 고저폭이 0 이면 **0** 이며, 그때는
        호출부가 검사를 건너뛴다 (0 을 임계값으로 쓰면 모든 선이 걸린다).

    Note:
        🔴 **쌍마다 다시 구하면 안 된다.** 처음에 판정 함수 안에서 `min(low)`·`max(high)`
        를 돌렸더니 후보 쌍 6,328개 x 400봉 = **250만 회**가 되어 작도가 **28~128%
        느려졌다** — 선을 줄이려다 시간을 늘린 것이다.

        비교를 "이동폭 vs 고저폭"에서 "기울기 vs 고저폭/봉수"로 바꾸면 같은 판정을
        **비교 한 번**으로 한다. 부등식 양변을 봉 수로 나눈 것이라 결과가 같다.
    """
    if len(candles) < 2:
        return Decimal(0)
    with fixed_context():
        low = min(candle.low for candle in candles)
        high = max(candle.high for candle in candles)
        return max(Decimal(0), (high - low) / Decimal(len(candles)))


def _travels_beyond_range(line: Line, budget: Decimal) -> bool:
    """선이 **자기 창 안에서** 관측 가격 범위보다 크게 움직이는가 — 축 J4-D.

    Args:
        line: 검사할 선.
        budget: `_slope_budget()` 값. 0 이면 판정하지 않는다.

    Returns:
        벗어나면 True (= 버릴 대상).

    Note:
        **새 파라미터가 0개다.** 비교 대상이 창 자신의 고저폭이므로 임계값을 고를 일이
        없다 — J1(상위 N개)이 탈락한 이유가 "N 을 근거로 답할 수 없다"였고, 여기는
        관측값 둘의 비교라 그 문제가 없다.

        판정 근거: 추세선은 **관측된 가격에 대한 기준선**이다. 자기가 그려진 창 폭만큼
        연장했을 때 이미 가격 범위를 통째로 벗어난다면, 그 선은 앵커 몇 개 주변을 빼면
        어디서도 지지·저항으로 작동할 수 없다. 실측에서 이런 선이 **44~45%**였고,
        차트에 53,500원 종목의 추세선이 -520,032 로 찍힌 것이 그 형태였다.
    """
    return budget > 0 and abs(line.slope_per_bar) > budget


def _pierced_by_wick(
    candles: Sequence[Candle],
    line: Line,
    kind: TrendlineKind,
    span: tuple[int, int],
    tolerance: AtrTolerance,
) -> bool:
    """앵커 구간 안에서 **꼬리가 선을 뚫고 지나간** 봉이 있는가 — 축 J4-H.

    Args:
        candles: 작도 창 전체.
        line: 검사할 선.
        kind: 지지선/저항선.
        span: 앵커 구간 `(시작, 끝)`.
        tolerance: 접점 허용 오차. **"걸린다"와 "관통했다"를 가르는 잣대**다.

    Returns:
        관통한 봉이 있으면 True (= 버릴 대상).

    Note:
        `_count_body_violations` 는 **몸통**만 본다 — §6.5 의 "몸통은 선 위, 꼬리는
        걸려도 된다"를 그대로 옮긴 것이다. 그런데 그 조문이 말하는 "걸린다"는 접점
        허용 오차 안이라는 뜻이지, 꼬리가 **얼마든지** 내려가도 된다는 뜻이 아니다.

        그래서 오차 **밖으로** 나간 꼬리만 관통으로 센다. 새 파라미터가 0개이고,
        "걸린다"의 정의를 이미 쓰고 있는 값(`touch_atr_multiple`)에서 그대로 가져온다.

        이것이 남은 누수의 형태다: 중간 스윙의 몸통은 선 위에 있는데 꼬리가 한참
        아래를 찍은 경우, 지금은 통과한다 — 가격이 그 선 아래에서 거래됐는데도.
    """
    for index in range(span[0], span[1] + 1):
        candle = candles[index]
        expected = line.price_at(index)
        if expected <= 0:
            continue
        margin = tolerance.at(index)
        if kind is TrendlineKind.SUPPORT:
            if candle.low < expected - margin:
                return True
        elif candle.high > expected + margin:
            return True
    return False


def _passes_j4(
    candles: Sequence[Candle],
    line: Line,
    kind: TrendlineKind,
    touches: Sequence[SwingPoint],
    tolerance: AtrTolerance,
    settings: TrendlineParams,
) -> bool:
    """축 J4 후보 필터 — 우연 3접점 배제 (`docs/rules/rule_candidates.md`).

    Args:
        candles: 작도 창 전체.
        line: 검사할 선.
        kind: 지지선/저항선.
        touches: 접점들.
        tolerance: 접점 허용 오차.
        settings: 추세선 파라미터 (`overdetection_guard` 를 읽는다).

    Returns:
        통과하면 True.

    Note:
        기본값이 `NONE`(끔)인 것이 의도다. 후보를 켠 채로 출하하면 **P1-8 비교 기준선이
        사라진다** — 같은 룰로 잰 매트릭스 위에서 개선을 증명해야 한다
        (`rule_candidates.md` "왜 지금 당장이 아니라 P1-8 후인가").
    """
    guard = settings.overdetection_guard
    if guard not in {OverdetectionGuard.WICK_PIERCE, OverdetectionGuard.BOTH}:
        return True
    # 기울기(J4-D)는 후보 생성 직후에 이미 걸렸다 — 여기서는 접점이 필요한 검사만 한다.
    span = (touches[0].index, touches[-1].index)
    return not _pierced_by_wick(candles, line, kind, span, tolerance)


def _on_line(line: Line, pivots: Sequence[SwingPoint], tolerance: AtrTolerance) -> bool:
    """이 스윙들이 **전부** 그 선 위에 있는가 (`_touching` 과 같은 소속 규칙).

    Args:
        line: 기준 직선.
        pivots: 판정할 스윙들.
        tolerance: 접점 허용 오차.

    Returns:
        하나라도 벗어나면 False.

    Note:
        `_touching` 을 재사용하지 않는 이유: 그쪽은 `span`·`min_gap_bars` 로 **걸러내는**
        함수라 "몇 개가 닿았나"를 돌려준다. 여기 필요한 것은 "전부 닿았나"이므로 같은
        부등호를 쓰되 필터링은 하지 않는다.
    """
    for pivot in pivots:
        expected = line.price_at(pivot.index)
        if expected <= 0:
            return False
        if abs(pivot.price - expected) > tolerance.at(pivot.index):
            return False
    return True


def _dedupe(candidates: list[Trendline], tolerance: AtrTolerance) -> list[Trendline]:
    """**같은 선의 다른 표현**을 버린다 — 판정은 접점의 기하 소속이다 (축 J2).

    Args:
        candidates: 같은 종류(지지/저항)의 후보들.
        tolerance: 접점 허용 오차. **새 파라미터가 아니라 작도에 쓴 그 값**이다.

    Returns:
        살아남은 선들. 정렬 키가 고정돼 있어 어느 표현이 남는지도 결정론적이다.

    Note:
        접점 5개인 선은 두 앵커 조합 10가지로 거의 같은 선이 10번 만들어진다.

        ⭐ **P1-6 실측이 술어의 오류를 드러냈다.** 원래는 접점 **인덱스 집합**의 포함
        관계만 봤다:

            if any(indices <= existing for existing in kept_sets)

        그래서 접점이 `{5, 20, 40}` 인 선과 `{5, 20, 55}` 인 선은 **기하학적으로 거의
        같은 선이어도** 서로 부분집합이 아니라 둘 다 살아남았다. 400봉 창에 40개 이상
        남던 선의 대부분이 이 경우이며, 그 결과 구조물 띠가 가격 축을 거의 전부 덮어
        합류 판정이 무의미해졌다 (`docs/rules/fvg_trendline_rules.md` §5.1).

        의도는 맞았고 **질문이 틀렸다.** 물어야 할 것은 "인덱스가 부분집합인가"가 아니라
        **"이 접점들이 저 선 위에 있는가"** 다.

        **기준은 이미 선언한 접점 허용 오차 그 자체다** — 시스템은 소속을 이미 정의해
        뒀고(`|피벗 - 선| <= kxATR` 이면 접점), 그것을 대칭으로 적용하면 "B 의 모든 접점이
        A 위에 있으면 B 는 A 의 중복"이 **정의에서 따라 나온다.** 새 자유도가 0 이고,
        오차를 바꾸면 접점 판정도 같이 바뀌므로 병합만 따로 조절할 수 없다 (§5.6.2).
    """
    ordered = sorted(
        candidates,
        key=lambda t: (
            -t.touch_count,
            -t.span_bars,
            t.touches[0].index,
            t.line.slope_per_bar,
        ),
    )
    kept: list[Trendline] = []
    for candidate in ordered:
        # 접점 수가 많은 선이 먼저 오므로, 뒤 후보가 앞 선에 얹히면 그것이 중복이다.
        if any(_on_line(existing.line, candidate.touches, tolerance) for existing in kept):
            continue
        kept.append(candidate)
    return kept


def detect_trendlines(
    candles: Sequence[Candle],
    pivots: Sequence[SwingPoint],
    params: TrendlineParams | None = None,
    tolerance: AtrTolerance | None = None,
) -> list[Trendline]:
    """유효 접점 추세선을 모두 찾는다 (spec §6.5).

    Args:
        candles: `ts` 오름차순 캔들. 몸통 이탈 검증에 쓴다.
        pivots: `swing.find_pivots()` 결과 — **`zigzag()`/`prior_swings()` 결과가 아니다.**
        params: 추세선 파라미터. None 이면 표준값.
        tolerance: 접점 허용 오차. None 이면 캔들에서 ATR 을 **직접 계산**한다 —
            편의용이며, 운영 경로는 `compute_bundle` 이 만든 것을 넘겨 재계산을 피한다.

    Returns:
        접점 수 내림차순 유효 추세선. 없으면 빈 리스트다 — **"추세선 없음"도 유효한
        답**이며 없는 선을 지어내지 않는다 (spec §4.20).

    Note:
        지지선은 스윙 로우끼리, 저항선은 스윙 하이끼리만 잇는다. 섞어 이으면 그것은
        추세선이 아니다.

        입력이 zigzag 결과면 안 되는 이유는 `swing` 모듈 docstring 에 있다 — 오르는
        저점들이 하나로 뭉개져 지지선이 사라진다.

        시간축은 `candles[0].timeframe` 에서 읽는다. `Candle` 이 자기 시간축을 들고
        있으므로 별도 인자로 받으면 어긋날 여지만 생긴다.
    """
    if not candles:
        return []
    settings = params or TrendlineParams()
    band = tolerance or from_candles(candles, TOUCH_ATR_MULTIPLE)
    segment_of = _segment_index(candles)
    slope_budget = (
        _slope_budget(candles)
        if settings.overdetection_guard in {OverdetectionGuard.SLOPE_RANGE, OverdetectionGuard.BOTH}
        else Decimal(0)
    )
    found: list[Trendline] = []
    with fixed_context():
        for kind, wanted in (
            (TrendlineKind.SUPPORT, SwingKind.LOW),
            (TrendlineKind.RESISTANCE, SwingKind.HIGH),
        ):
            same_kind = [pivot for pivot in pivots if pivot.kind is wanted]
            candidates: list[Trendline] = []
            for first, second in combinations(same_kind, 2):
                if second.index - first.index < settings.min_anchor_distance_bars:
                    continue
                if segment_of[first.index] != segment_of[second.index]:
                    continue  # 결측 구간을 가로지르는 선은 기울기가 무의미하다
                line = _line_through(first, second)
                # 🔴 기울기 검사는 **여기서** 한다 — 선을 긋는 즉시 알 수 있는 값이다.
                #
                # 뒤(후보 확정 후)에 두면 정확도는 같지만 **속도가 전혀 안 는다**:
                #   실측 BTC 15m — 선 66% 감소, 작도 시간 -5%(오히려 느려짐).
                # 비용의 대부분이 `_touching` + `_count_body_violations` 인데 그것을
                # 다 치른 뒤 버리기 때문이다. 앞으로 당기면 그 비용 자체를 건너뛴다.
                if _travels_beyond_range(line, slope_budget):
                    continue
                touches = _touching(
                    line,
                    same_kind,
                    band,
                    span=(first.index, second.index),
                    min_gap_bars=settings.min_anchor_distance_bars,
                )
                if len(touches) < settings.min_touches:
                    continue
                violations = _count_body_violations(
                    candles,
                    line,
                    kind,
                    (touches[0].index, touches[-1].index),
                    band,
                )
                if violations > settings.max_body_violations:
                    continue
                # 축 J4 — 우연 3접점 배제. 기본은 **끔**(기존 동작 보존)이며 후보를
                # 켤 때만 걸린다 (`TrendlineParams.overdetection_guard`).
                if not _passes_j4(candles, line, kind, touches, band, settings):
                    continue
                candidates.append(Trendline(kind, line, touches, violations))
            found.extend(_dedupe(candidates, band))
        found.sort(key=lambda t: (-t.touch_count, -t.span_bars, t.kind, t.touches[0].index))
    return found


def build_channel(
    candles: Sequence[Candle],
    trendline: Trendline,
    pivots: Sequence[SwingPoint],
    params: ChannelParams | None = None,
    trendline_params: TrendlineParams | None = None,
    tolerance: AtrTolerance | None = None,
) -> Channel | None:
    """추세선을 한쪽 경계로 삼아 평행 채널을 만든다 (spec §6.5).

    Args:
        candles: `ts` 오름차순 캔들.
        trendline: 채널의 근거가 될 유효 추세선.
        pivots: 반대편 접점 판정에 쓸 `find_pivots()` 결과 전체.
        params: 채널 파라미터.
        trendline_params: 반대편 접점 판정 기준. 추세선과 **같은 최소 간격**을 써야
            한다 — 한쪽 경계는 3접점을 요구하면서 반대편은 붙어 있는 두 봉을 2접점으로
            인정하면 채널의 두 변이 다른 기준으로 판정된다.
        tolerance: 접점 허용 오차. **추세선 작도와 같은 것을 넘겨야 한다** — 두 변이
            다른 오차로 판정되면 채널이 기울어진 이유를 설명할 수 없다.

    Returns:
        채널. 반대편 접점이 부족하면 **None** — 그것은 채널이 아니라 "추세선 하나 +
        어쩌다 찍은 극값"이다.

    Note:
        반대편 선은 구간 내에서 **선에서 가장 멀리 벗어난 꼬리**를 지나게 놓는다.
        꼬리 기준인 것은 여기서도 §6.5 전역 규칙이 이어지기 때문이다.
    """
    settings = params or ChannelParams()
    line_settings = trendline_params or TrendlineParams()
    band = tolerance or from_candles(candles, TOUCH_ATR_MULTIPLE)
    start, end = trendline.touches[0].index, trendline.touches[-1].index
    with fixed_context():
        if trendline.kind is TrendlineKind.SUPPORT:
            offset = max(
                candles[i].high - trendline.line.price_at(i) for i in range(start, end + 1)
            )
            lower, upper = trendline.line, trendline.line.shifted(offset)
            opposite_kind = SwingKind.HIGH
            opposite_line = upper
        else:
            offset = min(candles[i].low - trendline.line.price_at(i) for i in range(start, end + 1))
            lower, upper = trendline.line.shifted(offset), trendline.line
            opposite_kind = SwingKind.LOW
            opposite_line = lower

        opposite_touches = _touching(
            opposite_line,
            [pivot for pivot in pivots if pivot.kind is opposite_kind],
            band,
            span=(start, end),
            min_gap_bars=line_settings.min_anchor_distance_bars,
        )
    if len(opposite_touches) < settings.min_opposite_touches:
        return None
    return Channel(trendline, lower, upper, opposite_touches)
