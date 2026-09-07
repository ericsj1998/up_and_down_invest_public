"""마디마다 **위·아래 두 선** — 사람이 실제로 긋는 추세선 (축 J6).

## 🔴 기존 추세선과 종류가 다르다

`trendline.py` 는 *"모든 피벗 쌍 중 3접점이 맞는 선"* 을 찾는다. 400봉 1h 에서 피벗
111개 → 선 쌍 **6,105가지** → 살아남은 선 135개다. 개수를 줄여도(축 J5) 물건 자체는
그대로다 — 사용자 지적: *"추세선 상위로 확인해도 전혀 유효한 추세선이 아니라니까?
누가 추세선을 이렇게 그려"*.

사용자가 그리는 방식은 다르다 (`docs/ref_image`):

    "각 밸런스, 임밸런스마다 추세선은 가장 윗 추세선, 그리고 아래 추세선
     이렇게 2개만 딱 있는거야"

참고 이미지의 삼성전자 1h 는 **하락 레그 하나에 평행 채널 2선**이고, SHIB 15m 은
하락 레그에 저항선 · 상승 레그에 지지선이 각각 하나씩이다. 조합 탐색이 아니라
**구간을 감싸는 경계**다.

## 표준 알고리즘 — 회귀 채널 (Raff / Linear Regression Channel)

트레이딩뷰의 `Linear Regression Channel` · `Raff Regression Channel` 이 같은 것이다:

    1. 구간의 종가에 **최소제곱 직선**을 맞춘다 (추세의 중심)
    2. 그 직선을 위로 평행 이동해 **몸통 상단 최대**에 닿게 한다 → 상단
    3. 아래로 평행 이동해 **몸통 하단 최소**에 닿게 한다 → 하단

⚠️ 표준 구현은 고·저가(꼬리)를 쓰지만 여기서는 **몸통**이다. 마디 자체가 몸통 기준
이므로(`balance.py`) 채널만 꼬리 기준이면 둘이 다른 극값을 보게 된다.

## ⭐ 자유 파라미터가 0개다

접점 수도, 허용 오차도, 쌍 열거도 없다. 구간이 정해지면 **답이 하나로 결정된다.**
그래서 "왜 그 값인가"를 물을 값이 아예 없다 — 축 J1(상위 N개)이 탈락한 이유가
"N 을 근거로 답할 수 없다"였는데 여기는 그 문제가 존재하지 않는다.

선의 개수도 결정된다: **마디 수 x 2**. 400봉에 마디가 7개면 14선이다.

## ⚠️ 이것이 기존 추세선을 대체한다고 아직 말하지 않는다

`trendline` 은 P1-8 비교 기준선이 걸린 측정 대상이다. 여기서는 **새 개념**으로 붙이고
(플래그 `structure.leg_channel`), 둘 중 무엇을 쓸지는 out-of-sample 이 정한다
(절대 규칙 #12).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from updown.analysis.structures.balance import body_high, body_low
from updown.common.numeric import fixed_context

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.analysis.structures.balance import Leg
    from updown.common.domain.candle import Candle

MIN_BARS = 4
"""이보다 짧은 마디에는 선을 긋지 않는다.

3봉 이하에 회귀선을 맞추면 기울기가 노이즈다. 마디 자체는 유효할 수 있으므로 마디를
버리지는 않고 **채널만 건너뛴다**.
"""


@dataclass(frozen=True, slots=True)
class LegChannel:
    """마디 하나를 감싸는 채널.

    Attributes:
        start: 시작 봉 번호 (전역).
        end: 끝 봉 번호 (전역).
        kind: 마디 종류 (`imbalance` / `balance`).
        direction: 마디 방향 (`up` / `down` / `flat`).
        slope: 봉당 가격 변화 (회귀 직선의 기울기).
        upper_start: 상단선의 시작 가격.
        upper_end: 상단선의 끝 가격.
        lower_start: 하단선의 시작 가격.
        lower_end: 하단선의 끝 가격.
        width: 상·하단 간격 (가격).

    Note:
        상·하단은 **평행**하다 (같은 기울기). 참고 이미지의 채널이 그렇고, 평행이
        아니면 "채널"이 아니라 쐐기가 된다 — 그것은 별개 개념이라 섞지 않는다.
    """

    start: int
    end: int
    kind: str
    direction: str
    slope: Decimal
    upper_start: Decimal
    upper_end: Decimal
    lower_start: Decimal
    lower_end: Decimal
    width: Decimal

    def to_dict(self) -> dict[str, Any]:
        """화면용 dict — 좌표 규약은 추세선과 같다 (`x1/x2`).

        Returns:
            직렬화용 dict. 가격은 문자열이다.
        """
        return {
            "x1": self.start,
            "x2": self.end,
            "anchor_x2": self.end,
            "kind": self.kind,
            "direction": self.direction,
            "slope": str(self.slope),
            "upper1": str(self.upper_start),
            "upper2": str(self.upper_end),
            "lower1": str(self.lower_start),
            "lower2": str(self.lower_end),
            "width": str(self.width),
        }


def least_squares(closes: "Sequence[Decimal]") -> tuple[Decimal, Decimal]:
    """최소제곱 직선 — `(절편, 기울기)`.

    ⭐ 공개 함수다. 박스권 탐지기의 게이트가 기울기를 쓴다 — private 인 채로
    밖에서 쓰면 경계가 틀렸다는 신호이고, 실제로 pyright 가 그것을 잡았다.

    Args:
        closes: 구간 종가들. x 는 0..n-1 로 둔다.

    Returns:
        절편과 봉당 기울기.

    Note:
        x 를 0 부터 다시 세는 이유는 수치 안정성이다. 전역 봉 번호(수천)를 그대로
        쓰면 `sum(x^2)` 이 커져 정밀도가 깎인다.
    """
    n = len(closes)
    with fixed_context():
        mean_x = Decimal(n - 1) / 2
        mean_y = sum(closes, Decimal(0)) / n
        num = sum(
            ((Decimal(i) - mean_x) * (y - mean_y) for i, y in enumerate(closes)),
            Decimal(0),
        )
        den = sum(((Decimal(i) - mean_x) ** 2 for i in range(n)), Decimal(0))
        slope = Decimal(0) if den == 0 else num / den
        return mean_y - slope * mean_x, slope


def _channel(
    candles: "Sequence[Candle]", start: int, end: int, kind: str, direction: str
) -> LegChannel | None:
    """구간 하나를 감싸는 채널 — 마디용·전체용이 **같은 계산**을 쓴다.

    Args:
        candles: 전체 캔들 (전역 좌표).
        start: 시작 봉 번호.
        end: 끝 봉 번호 (포함).
        kind: 채널 종류 표시.
        direction: 방향 표시.

    Returns:
        채널. 구간이 `MIN_BARS` 보다 짧으면 None.

    Note:
        마디 채널과 전체 채널을 **따로 구현하지 않는다.** 둘이 갈라지면 같은 화면에서
        큰 흐름과 마디가 서로 다른 규칙으로 그려지고, 그 어긋남은 시장의 성질처럼
        보이지만 사실은 구현 차이다.
    """
    window = candles[start : end + 1]
    if len(window) < MIN_BARS:
        return None

    intercept, slope = least_squares([bar.close for bar in window])
    with fixed_context():
        fitted = [intercept + slope * Decimal(i) for i in range(len(window))]
        up = max(body_high(bar) - value for bar, value in zip(window, fitted, strict=True))
        down = min(body_low(bar) - value for bar, value in zip(window, fitted, strict=True))
        last = Decimal(len(window) - 1)
        return LegChannel(
            start=start,
            end=end,
            kind=kind,
            direction=direction,
            slope=slope,
            upper_start=intercept + up,
            upper_end=intercept + slope * last + up,
            lower_start=intercept + down,
            lower_end=intercept + slope * last + down,
            width=up - down,
        )


def whole(candles: "Sequence[Candle]") -> LegChannel | None:
    """창 **전체**를 감싸는 채널 하나 — 큰 흐름의 위·아래 선.

    Args:
        candles: 전체 캔들.

    Returns:
        채널. 봉이 `MIN_BARS` 보다 적으면 None.

    Note:
        사용자 요구: *"전체 큰 흐름의 추세선을 위 아래로 그려주는 마디 채널도 하나
        있으면 좋을 것 같은데."*

        마디 채널(`channels`)은 마디마다 하나라 **국면**을 보여 주고, 이것은 창 전체를
        하나로 봐서 **흐름**을 보여 준다. 둘은 다른 질문에 답하므로 하나가 다른 하나를
        대체하지 않는다.

        🔴 방향을 사람이 고르지 않는다 — **회귀 기울기의 부호**가 정한다. 기울기가
        0 이면 횡보다. 마디처럼 ZigZag 로 방향을 정하지 않는 이유는 창 전체에 대응하는
        마디가 없기 때문이다(마디는 창을 여러 개로 쪼갠 것이다).

        ⭐ 여기도 자유 파라미터가 0개다. 창이 정해지면 답이 하나로 결정된다.
    """
    if len(candles) < MIN_BARS:
        return None
    _, slope = least_squares([bar.close for bar in candles])
    direction = "flat" if slope == 0 else ("up" if slope > 0 else "down")
    return _channel(candles, 0, len(candles) - 1, "window", direction)


def channel_of(candles: "Sequence[Candle]", leg: "Leg") -> LegChannel | None:
    """마디 하나를 감싸는 채널을 만든다.

    Args:
        candles: 전체 캔들 (전역 좌표).
        leg: 마디.

    Returns:
        채널. 마디가 `MIN_BARS` 보다 짧으면 None.

    Note:
        🔴 **몸통 기준이다** (꼬리 아님).

        처음엔 꼬리 끝에 맞추고 "몸통으로 맞추면 스윕 봉이 채널 밖으로 나간다"고
        적었는데, **그것이 오히려 맞다.** 이유가 둘이다:

        1. **마디 자체가 몸통 기준이다** (`balance.body_low` · `body_high`). 마디를
           감싸는 채널만 꼬리 기준이면 둘이 서로 다른 극값을 보게 되고, 그 어긋남은
           알고리즘 결함처럼 보이지만 사실은 정의가 두 벌인 것이다.
        2. 사용자 매매 룰이 몸통 기준이다 — *"꼬리를 기준으로 하지 말고 캔들의 몸통을
           기준으로 판단해"* (밸런스 구조 명세 때 이미 지정됐다).

        ⚠️ 그래서 **스윕 꼬리는 채널 밖으로 나간다.** 그것이 의도다 — 유동성 스윕은
        구간을 정의하는 사건이 아니라 구간을 시험하는 사건이다.
    """
    return _channel(candles, leg.start, leg.end, leg.kind.value, leg.direction.value)


def channels(candles: "Sequence[Candle]", legs: "Sequence[Leg]") -> tuple[LegChannel, ...]:
    """마디마다 채널 하나 — **개수가 결정된다**.

    Args:
        candles: 전체 캔들.
        legs: 밸런스·임밸런스 마디들 (`balance.structure`).

    Returns:
        채널들. 마디 순서 그대로다.

    Note:
        🔴 여기에는 **탐색이 없다.** 마디가 정해지면 채널은 하나로 결정되므로
        "몇 개가 나올까"를 묻지 않아도 된다 — 마디 수만큼 나온다. 과탐지가 구조적으로
        불가능하다는 것이 이 방식을 고른 이유다.
    """
    return tuple(channel for leg in legs if (channel := channel_of(candles, leg)) is not None)
