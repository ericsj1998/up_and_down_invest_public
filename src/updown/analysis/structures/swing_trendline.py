"""추세선 — **스윙 두 개를 잇는다** (사용자 명세, 2026-08-13).

## 명세 원문

    스윙 하이 : 앞뒤에 캔들이 2~3개 정도 있고, 중앙에 고점이 가장 높은 캔들
    스윙 로우 : 앞뒤에 캔들이 2~3개 정도 있고, 중앙에 저점이 가장 낮은 캔들

    1. 스윙 하이 2개를 이음 -> 하락 추세선 -> 저항
    2. 스윙 로우 2개를 이음 -> 상승 추세선 -> 지지

    단, 추세선을 그렸을 때, 차트와 점점 갭이 벌어질 수록, 추세선은 힘을 잃는다.
    이를 해결하기 위해, 로그 차트를 통해 해결함.

## 🔴 2점을 **고르는** 것이 아니라 **찾는** 것이다

사용자 보완 (2026-08-13):

    딱 2점을 픽스한다기 보단, 최대한 많은 캔들이 닿게, 최대한 길게 그리는 게 나을 것
    같아. 캔들과 추세선 사이 공간이 최대한 적은 게 좋은거지, 즉 **영역으로 봐야한다**

그래서 이웃한 스윙을 그냥 잇지 않고 **볼록 껍질(convex hull)** 을 쓴다.

껍질의 변은 정의상 *"구간 안 모든 봉 위에(아래에) 있으면서 가장 낮게(높게) 붙는 선"*
이다 — 즉 **공간이 최소인 선**이 공짜로 나온다. 껍질 밖의 두 점을 이으면 반드시 어떤
봉을 뚫으므로 추세선이 아니고, 껍질 위 두 점을 이으면 공간이 더 넓다.

순위는 사용자가 말한 **그 순서**다:

    1. 닿은 봉 수      (많을수록)
    2. 걸친 봉 수      (길수록)
    3. 평균 공간 / ATR (좁을수록)

⛔ 셋을 하나의 점수로 섞지 않는다. 섞으려면 가중치가 필요하고 그 값은 근거로 답할 수
없다 (축 J1 이 탈락한 이유). 사전식으로 비교하면 가중치가 아예 없다.

## 🔴 `trendline.py` 와 무엇이 다른가

| | 기존 | 여기 |
|---|---|---|
| 접점 | **3개 필요** | **2개로 긋는다** |
| 후보 | 모든 피벗 쌍 (6,105가지) | **볼록 껍질의 변**만 |
| 스케일 | 선형 | **로그** |
| 개수 | 135개 | 최대 4개 (사용자 지정) |

기존 구현은 "3접점이 우연히 맞는 선"을 6,105번 시도해서 찾았다. 여기서는 껍질을 한 번
훑어 후보를 만든다 — 껍질의 변은 스윙 수보다 적으므로 조합이 아예 없다.

## 왜 로그인가

선형 차트에서 추세선은 **일정 금액**씩 오르내리는 선이다. 시장은 일정 **비율**로
움직이므로, 가격대가 달라지면 같은 각도가 다른 뜻이 된다 — 사용자 표현으로
*"차트와 점점 갭이 벌어질수록 추세선은 힘을 잃는다"*.

로그 공간에서 직선은 **일정 비율**을 뜻하므로 가격대가 변해도 뜻이 유지된다. 그래서
선을 `ln(가격)` 에서 긋고, 선형 차트에 그릴 때는 되돌린다 — 그때 직선이 **곡선**으로
보이는 것이 정상이다 (사용자: *"실제 차트에서는 곡선 추세선으로 분석도 가능하다"*).

## 🔴 꼬리냐 몸통이냐 — **재 보고 고른다**

사용자 (2026-08-13): *"추세선은 꼬리 기준이 좋을 때가 있고, 몸통 기준이 좋을 때가
있대. 차트 내 영역이 최대한 추세선과 잘 붙어있는 기준으로 계산해주는 게 더 나을 것
같아."*

그래서 앵커 가격을 **두 벌** 만든다 — 어느 폭에 20% 규칙(`PIERCE`)을 적용하느냐가 다르다:

    꼬리 기준  폭 = `high - low`          (봉 전체)
    몸통 기준  폭 = 시가~종가

같은 쌍에서 선을 둘 긋고 **캔들에 더 붙는 쪽**을 남긴다. 붙는 정도는 선과 캔들 극값
사이 평균 거리(ATR 배수)로 잰다 (`fit_gap`).

⭐ 새 파라미터가 0개다. "언제 꼬리를 쓰나"를 사람이 정하지 않고 **창마다 데이터가
정한다.**

⚠️ 기준은 **후보를 만드는 방식**일 뿐이다. 지켜야 할 20% 경계는 기준과 무관하게
**봉 전체**로 잰다 (`push_out`) — 그러지 않았을 때 몸통 기준 선이 꼬리의 중앙을
뚫었다.

## 앵커는 프랙탈 피벗이다 (ZigZag 전환점이 아니다)

전환점으로 만들었더니 앵커가 너무 적었다 — 실측(200봉 1h · 10개 창): 같은 종류 스윙
2~5개 → 껍질 변 1~3개 → 방향 필터 통과 **0~3개**. 사용자: *"추세선이 가끔 하나만
그려진다."*

피벗은 같은 창에서 50~60개이고, 껍질이 후보를 알아서 줄이므로 조합이 폭발하지 않는다.
바꾼 뒤 실측: **3~4개** (평균 3.8), 중앙 관통 0봉.

## 한 선에 맞은편 평행선이 붙는다

`with_parallel` 이 구간 안에서 가장 먼 봉까지 밀어 채널을 만든다 — 회귀 채널
(`leg_channel`)이 상·하단을 만드는 방식과 같은 생각이고, 여기서도 파라미터가 0개다.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.common.numeric import fixed_context

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.candle import Candle

MAX_LINES = 4
"""화면·판정에 남길 추세선 수.

사용자 지정: *"추세선은 내가 봤을 때 한 4개 정도가 최대야"*. 근거는 성과가 아니라
**사람이 한 화면에서 읽을 수 있는 양**이며, 그래서 성과로 조정하지 않는다 (§5.6.2).

⛔ 축 J1(상위 N개)이 "N 을 근거로 답할 수 없다"로 탈락한 것과 구분된다 — 거기서는
N 이 탐지 임계값이었고, 여기서는 **이미 완성된 목록의 표시 상한**이다.
"""

WICK = "wick"
BODY = "body"
BASES = (WICK, BODY)
"""추세선 앵커를 어디서 잡는가 — 둘 다 재고 잘 붙는 쪽을 쓴다."""


MIN_TOUCHES = 2
"""선이 **자기 앵커 위에 실제로 있어야** 한다.

새 값이 아니라 *"직선을 정의하는 점의 수"* 다. 그래서 조정할 여지가 없다 — 1점으로는
직선이 안 정해지고, 3점을 요구하면 다른 규칙(고전 3접점)이 된다.

## 🔴 0접점 선이 실제로 나왔다

`push_out` 이 선을 밀면 앵커가 선에서 떨어질 수 있다. 실측(200봉 1h · 10개 창)에서
후보 51개 중 **13개가 접점 0개**였고, 그중에는 124봉·108봉짜리도 있었다 — 아무 봉에도
안 닿으면서 화면을 가로지르는 선이다. 사용자가 *"잡음류의 추세선"* 이라고 한 것에
이런 것이 섞여 있었다.

⛔ 고전 표준인 3접점은 여기서 **너무 세다** — 같은 실측에서 창당 **1.0개**만 남아
"가끔 하나만 그려진다"로 되돌아간다. 이 선들은 이미 볼록 껍질이 걸러 낸 것이라
`trendline.py`(모든 쌍 열거)와 같은 문턱이 필요하지 않다.
"""

TOUCH_ATR = Decimal("0.25")
"""추세선에 **닿았다**고 볼 거리 (ATR 배수).

`config/structures.yml` 의 `trendline.touch_atr_multiple` 과 같은 값이다. "닿았다"의
정의를 두 벌 만들면 두 모듈이 같은 화면에서 다른 말을 한다.
"""

SAMPLES = 24
"""로그 직선을 선형 차트에 그릴 때 찍을 점 수.

로그 공간의 직선은 선형 차트에서 곡선이다. 양 끝만 이으면 그 곡률이 사라져 **다른
선**이 된다.
"""


PIERCE = Decimal("0.2")
"""선이 캔들을 **뚫어도 되는 깊이** (그 봉 폭의 비율).

## 🔴 "안 뚫는 선"은 차트에서 멀어진다

처음엔 어떤 봉도 안 뚫게 만들었다 — 앵커를 꼬리 끝에 두고, 뚫으면 꼬리 끝에 닿을
때까지 밖으로 밀었다. 그랬더니 사용자 지적: *"약간 다 차트가 삐져나오지 않게 하려고
하다 보니까 계속 차트에서 좀 먼 상황이네."*

당연한 결과다. 꼬리 하나가 길게 튀면 선 전체가 그 하나에 끌려 나머지 전부에서 멀어진다.
**꼬리 끝을 안 뚫는 선**과 **가격에 붙는 선**은 같은 것이 아니었다.

## 사용자 규칙 (2026-08-13)

    캔들을 어느 정도까지는 관통해도 되지만, **20~80% 사이 구간**을 관통하는 건
    추세선이 약간 잘못 그려져 있다 봐야하는 거지

그래서 경계를 꼬리 끝이 아니라 **바깥 20% 의 안쪽 끝**에 둔다:

    저항선이 내려올 수 있는 한계   high - 0.2 x (high - low)
    지지선이 올라갈 수 있는 한계   low  + 0.2 x (high - low)

⛔ 성과로 조정하지 않는다 (§5.6.2). 사용자가 준 값이고, 근거는 백테스트가 아니라
**사람이 차트를 읽는 방식**이다 — `MAX_LINES` 가 4인 것과 같은 성격이다.
"""


@dataclass(frozen=True, slots=True)
class SwingTrendline:
    """스윙 두 개를 이은 추세선.

    Attributes:
        kind: `resistance`(하락 추세선) 또는 `support`(상승 추세선).
        start: 첫 스윙의 봉 번호.
        end: 둘째 스윙의 봉 번호.
        start_price: 첫 스윙 가격.
        end_price: 둘째 스윙 가격.
        ratio_per_bar: 봉당 가격 **비율** (로그 기울기를 되돌린 값). 1 보다 크면 상승.
        basis: 앵커를 어디서 잡았나 (`wick` / `body`). **창마다 데이터가 정한다.**
        pushed: 봉을 뚫어서 바깥으로 밀어낸 선인가. 화면이 이 사실을 말해야 한다.
        parallel: 맞은편 평행선을 만드는 **배수**. 1 이면 아직 안 구했다.
    """

    kind: str
    start: int
    end: int
    start_price: Decimal
    end_price: Decimal
    ratio_per_bar: Decimal
    basis: str = WICK
    pushed: bool = False
    parallel: Decimal = Decimal(1)

    @property
    def span_bars(self) -> int:
        """두 앵커가 걸친 봉 수."""
        return self.end - self.start

    @property
    def rising(self) -> bool:
        """올라가는 선인가 — **화면 색은 이것이 정한다**.

        Note:
            🔴 `kind` 가 아니라 **기울기**로 답한다. 둘은 지금 같은 뜻이지만(저항은
            내려가고 지지는 올라간다) 색이 이름표를 따르면, 나중에 정의가 바뀌었을 때
            화면이 실제와 다른 색을 칠하고도 아무도 모른다. 색은 선이 **하는 일**을
            말해야 한다.
        """
        return self.ratio_per_bar > 1

    def parallel_at(self, index: int) -> Decimal:
        """맞은편 평행선의 `index` 봉 가격.

        Args:
            index: 봉 번호.

        Returns:
            평행선 가격. `parallel` 이 1 이면 본선과 같다.
        """
        with fixed_context():
            return self.price_at(index) * self.parallel

    def price_at(self, index: int) -> Decimal:
        """`index` 봉에서의 선 가격 — **로그 보간**이다.

        Args:
            index: 봉 번호. 두 스윙 밖이어도 된다 (연장).

        Returns:
            그 지점의 가격.
        """
        with fixed_context():
            span = Decimal(index - self.start)
            return self.start_price * (self.ratio_per_bar**span)

    def extent(self, until: int) -> int:
        """어디까지 늘려 그릴까 — **앵커 구간 길이만큼**.

        Args:
            until: 차트 마지막 봉 번호.

        Returns:
            연장 끝 봉 번호.

        Note:
            🔴 차트 끝까지 늘리지 않는다. 늘렸더니 앵커 구간이 35봉인 선이 200봉을
            가로질러, 서로 **앵커가 안 겹치는 선들이 화면에서는 겹쳐 보였다** — 사용자가
            *"선 기울기 레벨에서 거의 유사하게 겹치는 경우"* 라고 한 것의 실체다
            (실측: 그 화면 세 선의 앵커 구간 겹침이 **0봉**이었다).

            규칙은 새로 만들지 않았다. `backtest/chart.extend_to` 가 옛 추세선에 대해
            같은 이유로 이미 쓰고 있다 — *"선이 존재하는 만큼만 그린다."* 짧은 선이
            멀리까지 유효하다고 말할 근거가 없다.
        """
        return min(until, self.end + max(1, self.span_bars))

    def points(self, until: int) -> list[tuple[int, Decimal]]:
        """선형 차트에 그릴 좌표들 — 곡선이 되도록 여러 점을 찍는다.

        Args:
            until: 차트 마지막 봉 번호. 실제 연장 폭은 `extent` 가 정한다.

        Returns:
            `(봉 번호, 가격)` 목록.

        Note:
            양 끝만 이으면 로그 직선의 곡률이 사라진다. 그 선은 **다른 선**이고,
            사용자가 "곡선 추세선"이라고 부른 것이 바로 이 곡률이다.
        """
        last = max(self.extent(until), self.end)
        step = max(1, (last - self.start) // SAMPLES)
        xs = list(range(self.start, last + 1, step))
        if xs[-1] != last:
            xs.append(last)
        return [(x, self.price_at(x)) for x in xs]

    def to_dict(self, until: int) -> dict[str, Any]:
        """화면용 dict.

        Args:
            until: 연장해서 그릴 마지막 봉 번호.

        Returns:
            직렬화용 dict. 가격은 문자열이다.
        """
        drawn = self.points(until)
        return {
            "kind": self.kind,
            "basis": self.basis,
            "pushed": self.pushed,
            # 🔴 화면 색은 이것으로 정한다 — 이름표가 아니라 **기울기**다.
            "rising": self.rising,
            "x1": self.start,
            "x2": self.end,
            "anchor_x2": self.end,
            "y1": str(self.start_price),
            "y2": str(self.end_price),
            "ratio_per_bar": str(self.ratio_per_bar),
            "parallel_ratio": str(self.parallel),
            # 🔴 로그 직선은 선형 차트에서 곡선이다. 화면이 이 점들을 이어 그린다.
            "curve": [[x, str(price)] for x, price in drawn],
            # 맞은편 평행선. 본선과 같으면(배수 1) 안 그린다.
            "parallel": (
                []
                if self.parallel == 1
                else [[x, str(price * self.parallel)] for x, price in drawn]
            ),
        }


def _anchor(candle: "Candle", *, higher: bool, basis: str) -> Decimal:
    """선이 넘어오면 안 되는 **경계** — 캔들 바깥 20% 의 안쪽 끝.

    Args:
        candle: 그 봉.
        higher: 고점 쪽 경계(저항선용)면 True.
        basis: `wick` 이면 고·저가, `body` 면 시가·종가로 폭을 잰다.

    Returns:
        경계 가격.

    Note:
        🔴 **꼬리 끝이 아니다.** 꼬리 끝을 앵커로 쓰면 길게 튄 꼬리 하나가 선 전체를
        끌고 가 나머지 봉에서 멀어진다 (`PIERCE` 참조 — 사용자가 지적한 그 현상이다).

        여기가 경계이자 앵커다. 껍질을 이 점들로 쌓으면 **껍질의 변이 곧 규칙을 지키는
        선**이라 따로 검사할 것이 없다 — 20~80% 를 안 뚫는다는 조건이 구성으로 보장된다.

        폭이 0인 봉(사가=종가, 고가=저가)이면 경계가 그 값 자체다. 그것이 맞다 —
        뚫을 폭이 없는 봉이다.
    """
    if basis == BODY:
        low, high = min(candle.open, candle.close), max(candle.open, candle.close)
    else:
        low, high = candle.low, candle.high
    band = (high - low) * PIERCE
    return high - band if higher else low + band


def swings_from_turns(
    candles: "Sequence[Candle]",
    turns: "Sequence[int]",
    basis: str = WICK,
) -> list[SwingPoint]:
    """ZigZag 전환점을 스윙 점으로 바꾼다.

    Args:
        candles: 전체 캔들.
        turns: 전환점 봉 번호들 (오름차순).
        basis: `wick` 이면 고·저가, `body` 면 시가·종가 중 바깥쪽.

    Returns:
        스윙 점들.

    Note:
        🔴 프랙탈 스윙 목록에서 전환점 근처를 **골라내지 않는다.**

        처음엔 그렇게 만들었다가 실측에서 0개가 나왔다 — ZigZag 는 **몸통** 극값으로
        전환을 잡고 프랙탈 스윙은 **꼬리** 극값이라, 두 목록의 봉 번호가 자주 안 맞는다.
        허용 오차를 주는 방법도 있지만 그러면 "몇 봉까지"라는 답할 수 없는 값이 또
        생긴다. 전환점에서 **직접** 만들면 그 값이 필요 없다.

        고점·저점 판정은 **이웃 전환점과의 비교**다. ZigZag 는 고·저를 번갈아 잡으므로
        앞뒤보다 높으면 고점이다.
    """
    if len(turns) < 2:
        return []
    points: list[SwingPoint] = []
    for position, index in enumerate(turns):
        if not 0 <= index < len(candles):
            continue
        neighbour = turns[position - 1] if position else turns[1]
        if not 0 <= neighbour < len(candles):
            continue
        # 🔴 앞이든 뒤든 **비교 방향은 같다** — 이웃보다 높으면 고점이다.
        #    처음엔 첫 점만 뒤집었는데 틀렸다. 뒤 이웃보다 높다는 것도 "여기서
        #    내려간다"는 뜻이라 고점이 맞다 (테스트가 잡았다).
        higher = candles[index].close >= candles[neighbour].close
        points.append(
            SwingPoint(
                index=index,
                ts=candles[index].ts,
                price=_anchor(candles[index], higher=higher, basis=basis),
                kind=SwingKind.HIGH if higher else SwingKind.LOW,
            )
        )
    return points


def swings_from_pivots(
    candles: "Sequence[Candle]",
    pivots: "Sequence[SwingPoint]",
    basis: str = WICK,
) -> list[SwingPoint]:
    """프랙탈 피벗을 **경계 가격**으로 다시 매긴 스윙 점들.

    Args:
        candles: 전체 캔들.
        pivots: `swing.find_pivots` 결과 (고·저 전부, 교대 정리 **전**).
        basis: `wick` 또는 `body`.

    Returns:
        스윙 점들, 봉 번호 오름차순.

    Note:
        🔴 **왜 ZigZag 전환점 대신 프랙탈인가.**

        전환점으로 만들면 앵커가 너무 적다. 실측(200봉 1h · 10개 창): 마디 4~8개 →
        전환점 5~9개 → 같은 종류 스윙 **2~5개** → 껍질 변 1~3개 → 방향 필터 통과
        **0~3개**. 사용자가 *"추세선이 가끔 하나만 그려진다"* 고 한 것이 이 상태다.

        프랙탈 피벗은 같은 창에서 50~60개다. 껍질이 후보를 알아서 줄이므로(껍질 위
        점만 변이 된다) 옛 추세선처럼 조합이 폭발하지 않는다 — **더 많이 주고 껍질이
        고르게** 하는 것이 맞다.

        ⚠️ 피벗의 가격은 꼬리 끝이라 여기서 다시 매긴다. 그대로 쓰면 20% 규칙이
        깨진다 (`_anchor`).
    """
    return [
        SwingPoint(
            index=point.index,
            ts=point.ts,
            price=_anchor(candles[point.index], higher=point.kind is SwingKind.HIGH, basis=basis),
            kind=point.kind,
        )
        for point in pivots
        if 0 <= point.index < len(candles)
    ]


def _cross(
    origin: tuple[int, Decimal], first: tuple[int, Decimal], second: tuple[int, Decimal]
) -> Decimal:
    """외적 — 세 점의 회전 방향.

    Args:
        origin: 기준점.
        first: 첫 점.
        second: 둘째 점.

    Returns:
        양수면 반시계, 음수면 시계, 0 이면 일직선.
    """
    return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
        second[0] - origin[0]
    )


def _hull(points: "Sequence[tuple[int, Decimal]]", *, upper: bool) -> list[tuple[int, Decimal]]:
    """볼록 껍질의 한쪽 (Andrew's monotone chain).

    Args:
        points: `(봉 번호, 로그 가격)` 오름차순.
        upper: True 면 위쪽 껍질(저항), False 면 아래쪽(지지).

    Returns:
        껍질 위의 점들, 봉 번호 오름차순.

    Note:
        표준 알고리즘이라 파라미터가 없다. 위쪽 껍질의 변은 **구간 안 모든 점 위에
        있으면서 가장 낮게 붙는 선**이므로 "공간 최소"가 구성으로 보장된다.
    """
    chain: list[tuple[int, Decimal]] = []
    for point in points:
        while len(chain) >= 2:
            turn = _cross(chain[-2], chain[-1], point)
            if (turn >= 0) if upper else (turn <= 0):
                chain.pop()
            else:
                break
        chain.append(point)
    return chain


def _line(
    first: SwingPoint, second: SwingPoint, kind: str, basis: str = WICK
) -> SwingTrendline | None:
    """스윙 두 개를 로그 공간에서 잇는다.

    Args:
        first: 앞 스윙.
        second: 뒤 스윙.
        kind: `resistance` 또는 `support`.
        basis: 앵커 기준 (`wick` / `body`). 기록용이며 계산에는 안 쓴다.

    Returns:
        추세선. 가격이 0 이하이거나 두 스윙이 같은 봉이면 None.

    Note:
        `ln` 을 쓰므로 가격이 양수여야 한다. 코인·주식 가격은 항상 양수지만, 검증 없이
        `ln(0)` 을 부르면 조용히 예외가 나므로 여기서 막는다.
    """
    span = second.index - first.index
    if span <= 0 or first.price <= 0 or second.price <= 0:
        return None
    with fixed_context():
        # 로그 기울기 = 봉당 로그 가격 변화. 되돌리면 **봉당 비율**이다.
        ratio = ((second.price.ln() - first.price.ln()) / Decimal(span)).exp()
        return SwingTrendline(
            kind=kind,
            start=first.index,
            end=second.index,
            start_price=first.price,
            end_price=second.price,
            ratio_per_bar=ratio,
            basis=basis,
        )


@dataclass(frozen=True, slots=True)
class _Scored:
    """순위를 매길 후보 하나."""

    line: SwingTrendline
    touches: int
    span: int
    gap_atr: Decimal


def push_out(line: SwingTrendline, candles: "Sequence[Candle]") -> SwingTrendline:
    """선이 캔들 **중앙(20~80%)** 을 뚫으면 그만큼만 평행 이동한다 — 기울기는 그대로.

    Args:
        line: 후보 선.
        candles: 전체 캔들.

    Returns:
        구간 안 어떤 봉의 중앙도 안 뚫는 선. 이미 안 뚫으면 원본 그대로.

    Note:
        🔴 사용자 지정 (2026-08-13): *"차트를 가로지르는 것도 솔직히 추세 자체만은
        맞지만, 차라리 이런 경우 **고점 혹은 저점에 그려줘야** 한다는 거지"*.

        두 스윙을 이은 선은 **방향**은 맞는데 위치가 안쪽일 수 있다 — 사이에 더 높은
        고점(더 낮은 저점)이 있으면 선이 캔들을 가로지른다. 그때 선을 버리면 그 추세를
        통째로 잃으므로, **기울기를 유지한 채 밖으로 민다.**

        로그 공간이라 "민다"는 **일정 비율을 곱한다**는 뜻이다. 그래서 이동 후에도
        봉당 비율(`ratio_per_bar`)이 그대로다 — 선형 공간에서 평행 이동하면 비율이
        틀어진다.

        ⚠️ 미는 목적지가 **꼬리 끝이 아니라 20% 경계**다 (`PIERCE`). 꼬리 끝까지 밀면
        길게 튄 꼬리 하나에 선이 끌려가 차트에서 멀어진다 — 사용자가 지적한 그 현상이다.
        여기서는 꼬리를 **뚫은 채로** 멈춘다.

        🔴 경계는 선의 기준(`basis`)과 **무관하게 캔들 전체(고가~저가)** 로 잰다.

        선의 기준을 따르게 했더니 몸통 기준 선이 꼬리의 20~80% 를 뚫었다 — 실측에서
        선 6개가 전부 1~4봉씩 중앙을 관통했다. 사용자 규칙은 *"캔들의 중간 영역"* 이고
        그 캔들은 꼬리를 포함한 봉 전체다. 기준은 **후보를 만드는 방식**일 뿐이고,
        지켜야 할 선은 하나다.
    """
    support = line.kind == "support"
    with fixed_context():
        shift = Decimal(0)
        for index in range(line.start, line.end + 1):
            if not 0 <= index < len(candles):
                continue
            edge = _anchor(candles[index], higher=not support, basis=WICK)
            if edge <= 0:
                continue
            over = edge.ln() - line.price_at(index).ln()
            shift = min(shift, over) if support else max(shift, over)
        if shift == 0:
            return line
        factor = shift.exp()
        return SwingTrendline(
            kind=line.kind,
            start=line.start,
            end=line.end,
            start_price=line.start_price * factor,
            end_price=line.end_price * factor,
            ratio_per_bar=line.ratio_per_bar,
            basis=line.basis,
            pushed=True,
        )


def with_parallel(line: SwingTrendline, candles: "Sequence[Candle]") -> SwingTrendline:
    """맞은편 **평행선**을 달아 준다 — 한 선으로 채널을 만든다.

    Args:
        line: 후보 선.
        candles: 전체 캔들.

    Returns:
        `parallel` 이 채워진 사본. 잴 봉이 없으면 원본 그대로.

    Note:
        사용자 요구: *"해당 추세선 기반으로 평행이동된 선을 하나 그려서, (마디 채널에서
        했던 것처럼) 파악할 수 있게."*

        지지선(올라가는 선)의 맞은편은 **위**, 저항선의 맞은편은 **아래**다. 구간 안에서
        가장 멀리 있는 봉까지 밀어 두 선이 가격을 감싸게 한다 — 회귀 채널(`leg_channel`)
        이 상·하단을 만드는 방식과 같은 생각이다.

        ⭐ 새 파라미터가 없다. 미는 거리는 데이터가 정하고, 목적지는 본선과 **같은 20%
        경계**다 (`PIERCE`) — 맞은편만 다른 규칙을 쓰면 한 채널의 두 선이 서로 다른
        뜻을 갖게 된다.

        로그 공간이라 "평행"은 **일정 비율**이다. 선형 평행선은 가격대가 달라지면
        간격의 뜻이 변해 채널이 아니게 된다.
    """
    support = line.kind == "support"
    with fixed_context():
        shift = Decimal(0)
        for index in range(line.start, line.end + 1):
            if not 0 <= index < len(candles):
                continue
            # 맞은편 극단 — 지지선이면 위쪽(고가) 경계, 저항선이면 아래쪽.
            edge = _anchor(candles[index], higher=support, basis=WICK)
            if edge <= 0:
                continue
            over = edge.ln() - line.price_at(index).ln()
            shift = max(shift, over) if support else min(shift, over)
        if shift == 0:
            return line
        return replace(line, parallel=shift.exp())


def fit_gap(line: SwingTrendline, candles: "Sequence[Candle]") -> Decimal:
    """선과 **캔들** 사이 평균 거리 — 꼬리·몸통 중 어느 쪽이 잘 붙는지 가르는 자.

    Args:
        line: 후보 선.
        candles: 전체 캔들.

    Returns:
        구간 안 평균 거리. 캔들이 없으면 0.

    Note:
        🔴 순위용 `_score` 와 **다른 자**다. `_score` 는 각 기준의 스윙 점으로 재는데,
        그러면 꼬리 선은 꼬리 스윙에, 몸통 선은 몸통 스윙에 붙어 있어 둘 다 거의 0 이
        나온다 — **같은 자로 재지 않으면 비교가 안 된다.**

        여기서는 저항선은 **고가**, 지지선은 **저가**와 잰다. 그것이 "차트 내 영역"의
        경계이기 때문이다 (사용자: *"차트 내 영역이 최대한 추세선과 잘 붙어있는 기준"*).

        ⚠️ 꼬리가 고르면 꼬리 기준이 이기고, 꼬리 하나가 크게 튀면 그 하나에 끌려간
        꼬리 선보다 몸통 선이 이긴다. 그 판단을 사람이 안 하고 창마다 데이터가 한다.
    """
    support = line.kind == "support"
    with fixed_context():
        total = Decimal(0)
        count = 0
        for index in range(line.start, line.end + 1):
            if not 0 <= index < len(candles):
                continue
            edge = candles[index].low if support else candles[index].high
            total += abs(edge - line.price_at(index))
            count += 1
        return total / count if count else Decimal(0)


def _score(
    line: SwingTrendline,
    swings: "Sequence[SwingPoint]",
    candles: "Sequence[Candle]",
    atr: Decimal,
    tolerance: Decimal,
) -> _Scored:
    """사용자가 말한 세 기준을 잰다.

    Args:
        line: 후보 선.
        swings: 같은 종류 스윙 점들 (닿은 수를 여기서 센다).
        candles: 전체 캔들 (공간을 여기서 잰다). 비면 공간이 0 이다.
        atr: 구간 평균 ATR. 공간을 ATR 로 나눠 종목·시간축을 넘어 비교 가능하게 한다.
        tolerance: "닿았다"로 볼 거리 (ATR 배수).

    Returns:
        점수가 붙은 후보.

    Note:
        허용 오차는 `trendline.touch_atr_multiple`(0.25) 과 **같은 값**을 쓴다.
        "닿았다"의 정의를 두 벌 만들면 두 모듈이 다른 말을 하게 된다.
    """
    inside = [p for p in swings if line.start <= p.index <= line.end]
    limit = atr * tolerance
    touches = sum(1 for p in inside if abs(p.price - line.price_at(p.index)) <= limit)
    with fixed_context():
        # 🔴 공간은 **캔들** 기준이다 (`fit_gap`). 스윙 점으로 재면 꼬리 선은 꼬리
        #    스윙에, 몸통 선은 몸통 스윙에 붙어 있어 둘 다 0 이 나오고, 그러면 두 기준을
        #    한 줄에 세울 수 없다. 캔들은 기준과 무관한 공통의 자다.
        mean = fit_gap(line, candles) if candles else Decimal(0)
        return _Scored(line, touches, line.end - line.start, mean / atr if atr > 0 else mean)


def spans_a_leg(line: SwingTrendline, legs: "Sequence[tuple[int, int]]") -> bool:
    """이 선이 **마디를 통째로 하나 이상 품는가**.

    Args:
        line: 후보 선.
        legs: 마디 구간들 `(시작, 끝)`.

    Returns:
        구간 안에 온전히 들어오는 마디가 있으면 True. 마디 목록이 비면 **True**
        (거를 근거가 없다).

    Note:
        🔴 사용자가 준 말이 그대로 규칙이다 — *"한 구간 안에 너무 여러 추세선을 잔뜩
        그려놨어."* 여기서 **한 구간 = 마디 하나**이고, 마디보다 짧은 선은 그 구간의
        내부를 묘사할 뿐 구조를 가로지르지 않는다.

        짧은 구간에는 어떤 선을 그어도 맞는다. 그래서 맞았다는 사실이 정보가 아니다
        (사용자: *"틀리는 추세선을 그리는 것 자체가 불가능한 것 같은데"*).

        ⭐ **새 숫자가 없다.** "몇 봉 이상"을 정하면 그 값을 근거로 답해야 하는데
        (축 J1 이 탈락한 이유), 마디는 이미 ZigZag 가 정한 구조다. 시간축·종목마다
        적정 길이가 다른 문제도 저절로 풀린다 — 마디가 그 창의 자이기 때문이다.

        ## 왜 "경계를 넘는다"가 아니라 "통째로 품는다"인가

        둘 다 재 봤다 (200봉 1h · 10개 창):

            경계를 넘는다     창당 2.6개 · 중앙값 42봉 · **20봉 미만이 9개**
            통째로 품는다     창당 1.5개 · 중앙값 70봉 · 20봉 미만 1개

        경계 넘기만 요구하면 4봉짜리 선이 경계에 걸쳐 통과한다. 사용자는 *"차라리 이런
        잡음류의 추세선은 빼주는 게 나을 것 같아"* 라고 했으므로 강한 쪽을 쓴다.

        ⚠️ 대가가 있다 — 10개 창 중 **2개에서 선이 0개**가 된다. 그 창에는 구조를
        가로지르는 선이 실제로 없다는 뜻이고, 화면이 이유를 적는다. 큰 흐름은
        `structure.trend_channel` 이 따로 보여 주므로 창이 비지는 않는다.
    """
    if not legs:
        return True
    return any(line.start <= start and end <= line.end for start, end in legs)


def same_line(
    first: SwingTrendline, second: SwingTrendline, atr: Decimal, tolerance: Decimal
) -> bool:
    """두 선이 **구별되지 않을 만큼 겹치는가**.

    Args:
        first: 한 선.
        second: 다른 선.
        atr: 구간 평균 ATR.
        tolerance: "닿았다"로 볼 ATR 배수.

    Returns:
        겹치는 구간의 평균 거리가 허용 오차 안이면 True. 겹치는 구간이 없거나 ATR 이
        0 이면 **False** — 비교할 근거가 없으면 다른 선으로 둔다.

    Note:
        🔴 기준을 새로 만들지 않았다. **"닿았다"의 허용 오차가 곧 "같은 선"의 정의**다
        (`TOUCH_ATR` · `reproduce.MATCH_ATR` 과 같은 값). 한 선에 닿는 봉은 다른
        선에도 닿는다는 뜻이므로, 둘은 **증거로서 구별되지 않는다.** 그런 선을 둘 다
        남기면 같은 사실을 두 번 세는 것이다.

        사용자: *"선 기울기 레벨에서 거의 유사하게 겹치는 경우가 있는데 이런 경우 가장
        유효한 선 1개만 쓰는 게 나을 것 같은데."*

        겹치는 구간이 **앵커 구간끼리**인 것에 주의한다. 연장분까지 넣으면 멀리서
        벌어지는 두 선이 "다르다"로 갈리는데, 연장분은 아직 일어나지 않은 자리다.
    """
    if atr <= 0:
        return False
    low = max(first.start, second.start)
    high = min(first.end, second.end)
    if high <= low:
        return False
    with fixed_context():
        total = Decimal(0)
        for index in range(low, high + 1):
            total += abs(first.price_at(index) - second.price_at(index))
        return total / Decimal(high - low + 1) <= atr * tolerance


def detect(
    swings: "Sequence[SwingPoint]",
    body_swings: "Sequence[SwingPoint] | None" = None,
    atr: Decimal | None = None,
    candles: "Sequence[Candle]" = (),
    limit: int = MAX_LINES,
    tolerance: Decimal = TOUCH_ATR,
    legs: "Sequence[tuple[int, int]]" = (),
) -> tuple[SwingTrendline, ...]:
    """껍질의 변에서 추세선을 고른다 — **많이 닿고 · 길고 · 공간이 좁은** 순.

    Args:
        swings: **꼬리 기준** 스윙 점들 (봉 번호 오름차순).
        body_swings: **몸통 기준** 스윙 점들. 같은 전환점이어야 한다. None 이면
            꼬리 기준만 쓴다.
        atr: 구간 평균 ATR. None 이면 공간을 ATR 로 정규화하지 않는다.
        candles: 전체 캔들. **꼬리·몸통 중 어느 쪽이 잘 붙는지**를 여기서 잰다.
            비우면 먼저 온 기준(꼬리)이 남는다.
        limit: 남길 최대 개수. **목표가 아니라 상한**이다 — 후보가 모자라면 그만큼만
            나온다. 잡음으로 자리를 채우지 않는다.
        tolerance: "닿았다"로 볼 ATR 배수.
        legs: 마디 구간들 `(시작, 끝)`. 마디를 하나도 못 품는 선을 버린다
            (`spans_a_leg`). 비우면 그 검사를 안 한다.

    Returns:
        추세선들, 오래된 것부터.

    Note:
        🔴 **껍질의 변만 후보다.** 껍질 밖 두 점을 이으면 반드시 어떤 봉을 뚫으므로
        추세선이 아니고, 껍질 위 다른 쌍을 이으면 공간이 더 넓다. 그래서 "가장 좋은
        선"을 탐색할 필요 없이 후보가 이미 최소다.

        방향 조건은 명세 그대로다 — 고점을 이어 **내려가면** 저항, 저점을 이어
        **올라가면** 지지다.

        순위는 사용자가 말한 순서를 **사전식**으로 적용한다 (닿은 수 → 길이 → 공간 →
        최신). 가중치로 섞지 않는 이유는 그 값을 근거로 답할 수 없기 때문이다.
    """
    scale = atr if atr and atr > 0 else Decimal(1)
    sets: list[tuple[str, Sequence[SwingPoint]]] = [(WICK, swings)]
    if body_swings:
        sets.append((BODY, body_swings))

    # 🔴 같은 전환점 쌍에서 꼬리·몸통 두 선을 긋고 **캔들에 더 붙는 쪽만** 남긴다.
    #    "언제 꼬리를 쓰나"를 사람이 정하지 않고 창마다 데이터가 정한다.
    best: dict[tuple[str, int, int], tuple[_Scored, Decimal]] = {}
    for basis, points in sets:
        for kind, wanted, descending, upper in (
            ("resistance", SwingKind.HIGH, True, True),
            ("support", SwingKind.LOW, False, False),
        ):
            same = [p for p in points if p.kind is wanted and p.price > 0]
            if len(same) < 2:
                continue
            with fixed_context():
                logged = [(p.index, p.price.ln()) for p in same]
            for left, right in pairwise(_hull(logged, upper=upper)):
                first = next(p for p in same if p.index == left[0])
                second = next(p for p in same if p.index == right[0])
                line = _line(first, second, kind, basis)
                if line is None or (second.price < first.price) is not descending:
                    continue
                # 🔴 방향은 맞는데 위치가 안쪽이면 **밖으로 민다** (버리지 않는다).
                line = push_out(line, candles)
                scored = _score(line, same, candles, scale, tolerance)
                key = (kind, line.start, line.end)
                # 🔴 기준 선택은 **캔들과의 거리**로 한다 (`fit_gap`). 순위용 점수는
                #    각 기준의 스윙 점으로 재므로 두 기준을 비교할 자가 못 된다.
                fit = fit_gap(line, candles)
                rival = best.get(key)
                if rival is None or fit < rival[1]:
                    best[key] = (scored, fit)

    # 🔴 두 가지를 버린다.
    #    ① 자기 앵커에도 안 닿는 선 (`MIN_TOUCHES`) — 밀린 선은 앵커에서 떨어질 수
    #       있고, 아무 봉에도 안 닿는 선은 추세선이 아니라 낙서다.
    #    ② 마디를 하나도 못 품는 선 (`spans_a_leg`) — 구조가 아니라 구간 내부다.
    found: list[_Scored] = [
        scored
        for scored, _ in best.values()
        if scored.touches >= MIN_TOUCHES and spans_a_leg(scored.line, legs)
    ]

    # 🔴 **길이가 먼저다.** 원래는 접점 수가 먼저였는데, 앵커를 프랙탈로 바꾼 뒤
    #    접점이 변별력을 잃었다 — 실측에서 살아남은 선은 거의 전부 2접점(자기 앵커)
    #    이다. 그 상태에서 접점을 앞에 두면 우연히 군집을 지나는 **짧은 선**이 위로
    #    올라온다. 사용자: *"큰 단위의 추세선은 오히려 잘 그려졌는데, 한 구간 안에
    #    너무 여러 추세선을 잔뜩 그려놨어."*
    #
    #    길이는 그 선이 **틀릴 기회를 몇 번 가졌는가**이기도 하다. 짧은 선은 어디에
    #    그어도 맞으므로 맞았다는 사실이 정보가 아니다 (사용자: *"틀리는 추세선을
    #    그리는 것 자체가 불가능한 것 같은데"*).
    #
    #    마지막 동점은 **최신**이 이긴다 (*"갭이 벌어질수록 추세선은 힘을 잃는다"*).
    found.sort(key=lambda item: (-item.span, -item.touches, item.gap_atr, -item.line.end))

    # 🔴 구별되지 않는 선은 **하나만** 남긴다 (`same_line`).
    #
    #    정렬이 끝난 뒤에 훑으므로 남는 것은 **먼저 온 쪽 = 더 긴 선**이다. 사용자가
    #    "가장 유효한 선 1개만"이라고 한 그 하나이고, 순위 규칙이 이미 무엇이 유효한지
    #    정해 두었으므로 여기서 다시 고르지 않는다.
    kept: list[_Scored] = []
    for item in found:
        if any(same_line(item.line, other.line, scale, tolerance) for other in kept):
            continue
        kept.append(item)
        if limit > 0 and len(kept) >= limit:
            break
    picked = kept if limit > 0 else []
    # 🔴 평행선은 **고른 뒤에** 단다. 후보 전부에 달면 버릴 선까지 계산하게 되고,
    #    순위에는 아무 영향이 없다 (평행선은 순위 기준이 아니다).
    return tuple(
        with_parallel(item.line, candles) for item in sorted(picked, key=lambda item: item.line.end)
    )
