"""이의제기를 **규칙으로 환원**해 본다 — 이것이 타입 B 를 정답지로 만들지 않는 장치다.

## 되묻는 질문 하나

    "사람이 그린 그림을 만들어내는 파라미터가 존재하는가?"

    존재한다     → **파라미터 문제**다. 그 값을 축 후보로 올려 out-of-sample 이
                   판정한다. 눈은 후보를 제안했을 뿐 확정하지 않았다 (규칙 #12)
    존재 안 한다 → 🔴 **더 값진 발견**이다. 어떤 설정으로도 그 그림이 안 나오면
                   정의 자체가 사람이 생각하는 규칙과 다르다는 뜻이고, 그때는
                   파라미터가 아니라 알고리즘을 고쳐야 한다

절대 규칙 #11 이 정한 기준이 *"그 판단이 규칙으로 환원되어 코드에 남는가"* 다.
이 모듈이 그 환원을 **자동으로 시도**하므로, 손그림이 손그림으로 남지 않는다.

## ⛔ 여기서 이긴 값을 채택하지 않는다

탐색이 찾은 값은 **후보**다. 바로 쓰면 사람이 그린 한 장에 맞춘 것이고 그것이 정확히
§5.6.2 가 금지하는 자동조율이다. 표본 하나에 맞춘 값은 표본 하나에서만 이긴다.

## 탐색 범위가 좁은 것은 의도다

`EDITABLE` 의 격자만 훑는다. 넓히면 반드시 뭔가는 맞고, "맞는 값이 있다"는 답이
무의미해진다 — 자유도가 크면 어떤 그림이든 재현되기 때문이다.
"""

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from math import exp, log
from typing import TYPE_CHECKING, Any, cast

from updown.analysis.indicators import snapshot as indicator_snapshot
from updown.analysis.structures.params import StructureParams
from updown.orchestration.inspection.overrides import (
    EDITABLE,
    apply_overrides,
    keys_for,
    override_defaults,
)
from updown.orchestration.inspection.snapshot import build_frame

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from updown.common.domain.candle import Candle

from updown.common.domain.instrument import Timeframe

MAX_TRIES = 240
"""훑을 조합 수 상한.

한 조합마다 작도를 다시 하므로 200봉 기준 대략 수 초다. 상한이 없으면 화면이 멈춘 것
처럼 보이고, 사용자는 도구가 고장 났다고 판단한다.
"""


@dataclass(frozen=True, slots=True)
class Candidate:
    """사람 그림을 재현한(또는 가장 가까운) 설정 하나.

    Attributes:
        overrides: 그 설정 (`key -> 값`).
        distance: 목표와의 거리. 0 이면 정확히 재현했다.
        count: 그 설정에서 나온 도형 수.
    """

    overrides: dict[str, str]
    distance: float
    count: int


@dataclass(frozen=True, slots=True)
class Search:
    """탐색 결과.

    Attributes:
        reproduced: 정확히 재현하는 설정이 있었는가.
        best: 가장 가까운 후보들 (거리 오름차순, 최대 3).
        tried: 실제로 시도한 조합 수.
        verdict: 사람이 읽을 결론.

    Note:
        후보를 **3개까지만** 낸다. 규칙 #12 가 "근거 있는 최대 3개"로 못박아 둔 것과
        같은 이유다 — 실험이 많을수록 우연히 이기는 것이 나온다.
    """

    reproduced: bool
    best: tuple[Candidate, ...]
    tried: int
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        """JSON 으로.

        Returns:
            재현 여부·시도 수·판정·가장 가까운 설정들.
        """
        return {
            "reproduced": self.reproduced,
            "tried": self.tried,
            "verdict": self.verdict,
            "best": [
                {"overrides": item.overrides, "distance": item.distance, "count": item.count}
                for item in self.best
            ],
        }


def _grid(key: str) -> list[str]:
    """한 파라미터의 훑을 값들.

    Args:
        key: 파라미터 키.

    Returns:
        문자열 값 목록. 정수는 전 범위, 소수는 12칸으로 나눈다.

    Note:
        소수를 촘촘히 훑지 않는 이유는 위 docstring 과 같다 — 자유도가 크면 무엇이든
        재현되고 그러면 "재현됐다"가 아무 정보도 아니다.
    """
    spec = next(item for item in EDITABLE if item.key == key)
    if spec.kind == "int":
        return [str(value) for value in range(int(spec.low), int(spec.high) + 1)]
    steps = 12
    span = (spec.high - spec.low) / steps
    return [str((spec.low + span * i).quantize(Decimal("0.01"))) for i in range(steps + 1)]


def search_for_count(
    candles: "Sequence[Candle]",
    timeframe: Timeframe,
    flag: str,
    target: int,
    keys: "Sequence[str] | None" = None,
) -> Search:
    """그 플래그의 도형 수가 `target` 이 되는 설정을 찾는다.

    Args:
        candles: as-of 로 잘린 봉들.
        timeframe: 시간축.
        flag: 대상 플래그 id.
        target: 사람이 기대한 도형 수. `removed` 이의가 쌓이면 "이만큼이어야 한다"가
            나온다.
        keys: 훑을 파라미터. None 이면 이 플래그에 걸린 것 전부.

    Returns:
        탐색 결과.

    Note:
        🔴 **개수는 대리 지표다.** 사람이 원한 것은 "그 선이 저기 있어야 한다"이지
        "선이 15개여야 한다"가 아니다. 개수가 맞아도 다른 선일 수 있으므로 결론에
        그 한계를 적는다 — 도구가 자기 한계를 숨기면 그 위의 판단이 전부 흔들린다.
    """
    # 🔴 `Editable.flag`(소유자)가 아니라 `keys_for`(의존)로 고른다. 소유 파라미터가
    #    없는 플래그도 다른 값이 움직인다 — swing_trendline 이 그랬다.
    wanted = list(keys) if keys else list(keys_for(flag))
    if not wanted:
        return Search(False, (), 0, f"{flag} 에 걸린 파라미터가 없다 — 탐색할 것이 없다")

    base = override_defaults()
    found: list[Candidate] = []
    tried = 0
    # 한 번에 한 축만 흔든다 (§5.6.7). 두 축을 동시에 훑으면 조합이 폭발하고,
    # 무엇이 그 그림을 만들었는지도 말할 수 없게 된다.
    for key in wanted:
        for value in _grid(key):
            if tried >= MAX_TRIES:
                break
            tried += 1
            tuned = apply_overrides({key: value})
            frame = build_frame(
                candles, timeframe, [flag], params=tuned.params, deviation=tuned.deviation
            )
            count = frame.layers[0].count if frame.layers else 0
            changed = {key: value} if value != base[key] else {}
            found.append(Candidate(changed, float(abs(count - target)), count))

    found.sort(key=lambda item: (item.distance, len(item.overrides)))
    closest = found[0] if found else None
    gap = closest.distance if closest else float("inf")
    return Search(
        reproduced=gap == 0,
        best=tuple(found[:3]),
        tried=tried,
        verdict=_verdict(target, gap, closest.count if closest else 0),
    )


def _verdict(target: int, gap: float, nearest: int) -> str:
    """거리에서 결론을 낸다 — **가까운 것과 못 맞춘 것을 가른다**.

    Args:
        target: 사람이 기대한 수.
        gap: 가장 가까운 후보와의 거리.
        nearest: 그 후보가 낸 수.

    Returns:
        사람이 읽을 결론.

    Note:
        🔴 처음엔 "0 이 아니면 정의 문제"라고 적었는데 **틀렸다.** 목표 15 에 14 가
        나온 것은 격자를 12칸으로 훑어서 생긴 해상도 차이지 정의가 다른 것이 아니다.
        그 문구대로면 파라미터만 조금 옮기면 될 일에 알고리즘을 다시 쓰러 간다.

        그래서 **가깝다**를 따로 둔다: 목표의 10% 또는 2개 이내면 파라미터 쪽 문제로
        본다. 두 오진의 대가가 다르기 때문에 경계를 관대하게 잡았다 —
        "파라미터 보라"고 잘못 말하면 몇 분 낭비지만, "알고리즘 다시 쓰라"고 잘못
        말하면 며칠이 날아간다.
    """
    if gap == 0:
        return (
            "재현하는 설정이 있다 — **파라미터 문제**다. 축 후보로 올려 "
            "out-of-sample 이 판정한다 (규칙 #12). ⚠️ 개수만 맞춘 것이라 "
            "같은 선인지는 눈으로 확인해야 한다"
        )
    if gap <= max(2.0, target * 0.1):
        return (
            f"정확히 {target}개는 아니지만 {nearest}개까지 간다 — 격자를 12칸으로 "
            f"훑은 해상도 차이일 뿐 **파라미터 문제**로 본다. 그 근처 값을 축 후보로 "
            f"올린다 (규칙 #12)"
        )
    return (
        f"🔴 어떤 설정으로도 {target}개 근처에 못 간다 (가장 가까운 값 {nearest}개) — "
        f"**정의 자체**가 사람이 생각하는 규칙과 다르다는 뜻이다. 파라미터가 아니라 "
        f"알고리즘을 봐야 한다"
    )


def count_of(
    candles: "Sequence[Candle]",
    timeframe: Timeframe,
    flag: str,
    params: StructureParams | None = None,
    deviation: Decimal | None = None,
) -> int:
    """지금 설정에서 그 플래그가 내는 도형 수.

    Args:
        candles: 봉들.
        timeframe: 시간축.
        flag: 플래그 id.
        params: 구조물 파라미터.
        deviation: ZigZag 편차.

    Returns:
        도형 수.
    """
    kwargs: dict[str, Any] = {}
    if params is not None:
        kwargs["params"] = params
    if deviation is not None:
        kwargs["deviation"] = deviation
    frame = build_frame(candles, timeframe, [flag], **kwargs)
    return frame.layers[0].count if frame.layers else 0


__all__ = [
    "MATCH_ATR",
    "Candidate",
    "Search",
    "count_of",
    "line_distance",
    "search_for_count",
    "search_for_line",
]


MATCH_ATR = Decimal("0.25")
"""두 선을 **같은 선**으로 볼 거리 (ATR 배수).

`config/structures.yml` 의 `trendline.touch_atr_multiple` 과 **같은 값**이다. 우연이
아니라 같은 질문이기 때문이다 — "닿았다고 볼 것인가". 사람이 그린 선과 알고리즘이
그린 선이 서로의 접점 허용 오차 안에 있으면 같은 선으로 본다.

⛔ 고정 %를 쓰지 않는다. 같은 0.2% 가 1h 에서 0.407xATR, 5m 에서 1.671xATR 로
**4.1배 다른 뜻**이었던 실측이 있다 (§1-0h).
"""

NEAR_ATR = Decimal(1)
"""이 안쪽이면 "가깝다" — 격자 해상도 문제로 본다.

`MATCH_ATR` 과 가르는 이유는 두 오진의 대가가 다르기 때문이다. "파라미터 보라"고
잘못 말하면 몇 분 낭비지만 "알고리즘 다시 쓰라"고 잘못 말하면 며칠이 날아간다.
"""

_SAMPLES = 9
"""선을 비교할 때 훑을 지점 수. 양 끝만 보면 기울기가 다른데 끝이 맞는 선을 놓친다."""


def _at(shape: "Mapping[str, Any]", key: str) -> float:
    """도형 dict 에서 숫자 하나를 꺼낸다 (가격은 문자열로 온다)."""
    try:
        return float(str(shape.get(key, 0)))
    except (TypeError, ValueError):
        return 0.0


def _price_on(x1: float, y1: float, x2: float, y2: float, at: float) -> float:
    """두 점을 잇는 **직선**의 `at` 지점 가격."""
    if x2 == x1:
        return y1
    return y1 + (y2 - y1) * (at - x1) / (x2 - x1)


def _log_price_on(x1: float, y1: float, x2: float, y2: float, at: float) -> float:
    """두 점을 잇는 **로그 직선**의 `at` 지점 가격.

    Args:
        x1: 첫 점의 봉 번호.
        y1: 첫 점의 가격.
        x2: 끝 점의 봉 번호.
        y2: 끝 점의 가격.
        at: 볼 봉 번호.

    Returns:
        그 지점 가격. 가격이 0 이하면 로그를 못 취하므로 직선으로 물러난다.

    Note:
        로그 직선은 **일정 비율**로 오르내리는 선이다 (`swing_trendline.py` 서두).
    """
    if x2 == x1 or y1 <= 0 or y2 <= 0:
        return _price_on(x1, y1, x2, y2, at)
    ratio = (at - x1) / (x2 - x1)
    return exp(log(y1) + (log(y2) - log(y1)) * ratio)


def _as_float(value: object) -> float | None:
    """숫자로 읽을 수 있으면 float, 아니면 None.

    Args:
        value: JSON 에서 온 아무 값.

    Returns:
        float 또는 None.

    Note:
        가격이 **문자열**로 온다 (`Decimal` 규약). `float()` 에 아무 객체나 넘기면
        타입 검사가 못 잡으므로 받을 종류를 여기서 못박는다.
    """
    if isinstance(value, int | float | str | Decimal):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _curve_of(shape: "Mapping[str, Any]") -> list[tuple[float, float]]:
    """도형이 **실제로 그려지는 점들** (`curve`). 없으면 빈 목록.

    Args:
        shape: 도형 dict.

    Returns:
        `(봉 번호, 가격)` 목록. 형태가 조금이라도 어긋나면 **빈 목록**을 낸다 —
        절반만 읽으면 짧아진 선과 비교하게 되고, 그 거리는 아무 뜻이 없다.
    """
    raw = shape.get("curve")
    if not isinstance(raw, list | tuple):
        return []
    # JSON 경계다 — 원소 타입을 아는 사람이 없으므로 `object` 로 못박고 하나씩 검사한다.
    points = cast("Sequence[object]", raw)
    if len(points) < 2:
        return []
    out: list[tuple[float, float]] = []
    for item in points:
        if not isinstance(item, list | tuple):
            return []
        pair = cast("Sequence[object]", item)
        if len(pair) != 2:
            return []
        x, y = _as_float(pair[0]), _as_float(pair[1])
        if x is None or y is None:
            return []
        out.append((x, y))
    return out


def _price_on_curve(points: "Sequence[tuple[float, float]]", at: float) -> float:
    """`curve` 위의 `at` 지점 가격 — 점 사이는 선형 보간.

    Args:
        points: `(봉 번호, 가격)` 목록. 봉 번호 오름차순이다.
        at: 볼 봉 번호.

    Returns:
        그 지점 가격. 범위 밖이면 가장 가까운 끝값.

    Note:
        점이 24개라 구간 안에서는 곡률이 거의 없다 (`swing_trendline.SAMPLES`).
        점 사이를 다시 로그로 풀 이유가 없다.
    """
    if at <= points[0][0]:
        return points[0][1]
    for (x1, y1), (x2, y2) in pairwise(points):
        if at <= x2:
            return _price_on(x1, y1, x2, y2, at)
    return points[-1][1]


def line_distance(
    drawn: "Sequence[tuple[float, float]]",
    shape: "Mapping[str, Any]",
    atr: float,
) -> float | None:
    """사람이 그린 선과 알고리즘 선의 거리 — **ATR 배수**.

    Args:
        drawn: 사람이 찍은 두 점 `[(봉번호, 가격), (봉번호, 가격)]`.
        shape: 알고리즘 추세선 (`x1,y1,x2,y2`).
        atr: 이 구간의 ATR. 0 이면 잴 자가 없다.

    Returns:
        겹치는 구간에서의 평균 가격차를 ATR 로 나눈 값. **겹치는 구간이 없거나 ATR 이
        0 이면 None** — "멀다"가 아니라 "비교 불가"다.

    Note:
        🔴 겹치는 x 구간이 없으면 비교하지 않는다. 서로 다른 시기에 그어진 두 선은
        가격이 우연히 비슷해도 같은 선이 아니다. 이것을 0 이나 큰 수로 뭉개면 엉뚱한
        선이 "재현됐다"로 나온다.

        양 끝만 보지 않고 `_SAMPLES` 지점을 훑는다 — 기울기가 다른데 끝점만 맞는 선을
        같은 선으로 세면 안 된다.

        🔴 **두 선을 같은 공간에서 읽는다.** 도형에 `curve` 가 있으면 그 선은 로그
        직선이므로(`swing_trendline.py`) 사람이 찍은 두 점도 로그로 잇는다. 처음엔
        양쪽을 다 직선으로 폈는데, 그것이 로그 곡선을 **현(chord)** 으로 근사하는
        일이라 있지도 않은 거리를 만들어 냈다:

            폭 8% 창    0.15xATR   (`MATCH_ATR` 0.25 안쪽 — 안 보였다)
            폭 30% 창   0.6~0.9xATR
            3배 구간    수 배

        사람이 앵커만 옮긴 선인데 *"🔴 정의 자체가 다르다"* 가 나오는 경로였다. 화면이
        곡선을 보여 주고 판정은 현으로 하면, 도구가 자기 그림을 배신한다 (절대 규칙 #8).
    """
    if atr <= 0 or len(drawn) != 2:
        return None
    (hx1, hy1), (hx2, hy2) = drawn[0], drawn[1]
    # 비교 구간은 **그려진 만큼**이다. `curve` 는 연장분까지 담고 있고, 사람은 그 그려진
    # 것을 보고 선을 그었다.
    curve = _curve_of(shape)
    if curve:
        sx1, sx2 = curve[0][0], curve[-1][0]
    else:
        sx1, sx2 = _at(shape, "x1"), _at(shape, "x2")
    sy1, sy2 = _at(shape, "y1"), _at(shape, "y2")

    low = max(min(hx1, hx2), min(sx1, sx2))
    high = min(max(hx1, hx2), max(sx1, sx2))
    if high <= low:
        return None

    total = 0.0
    for step in range(_SAMPLES):
        at = low + (high - low) * step / (_SAMPLES - 1)
        if curve:
            mine = _log_price_on(hx1, hy1, hx2, hy2, at)
            theirs = _price_on_curve(curve, at)
        else:
            mine = _price_on(hx1, hy1, hx2, hy2, at)
            theirs = _price_on(sx1, sy1, sx2, sy2, at)
        total += abs(mine - theirs)
    return total / _SAMPLES / atr


def _atr_over(candles: "Sequence[Candle]", low: int, high: int) -> float:
    """구간 평균 ATR.

    Args:
        candles: 봉들.
        low: 시작 봉 번호.
        high: 끝 봉 번호.

    Returns:
        평균 ATR. 값이 없으면 0.
    """
    series = indicator_snapshot.compute(list(candles)).atr14
    window = [
        float(value)
        for value in series[max(low, 0) : min(high, len(series)) + 1]
        if value is not None
    ]
    return sum(window) / len(window) if window else 0.0


def search_for_line(
    candles: "Sequence[Candle]",
    timeframe: Timeframe,
    flag: str,
    drawn: "Sequence[tuple[float, float]]",
    keys: "Sequence[str] | None" = None,
) -> Search:
    """사람이 **그 자리에 그린 선**을 만들어내는 설정을 찾는다.

    Args:
        candles: as-of 로 잘린 봉들.
        timeframe: 시간축.
        flag: 대상 플래그. 지금은 `structure.trendline` 만 뜻이 있다.
        drawn: 사람이 찍은 두 점 `[(봉번호, 가격), (봉번호, 가격)]`.
        keys: 훑을 파라미터. None 이면 이 플래그에 걸린 것 전부.

    Returns:
        탐색 결과. `distance` 는 **ATR 배수**다.

    Note:
        🔴 개수 탐색(`search_for_count`)과 무엇이 다른가 — 개수는 **완전히 다른 선**
        으로도 맞는다. 사람이 주장한 것은 "그 선이 저기"이므로 좌표로 재야 한다.

        설정마다 나온 선 중 **가장 가까운 하나**를 본다. 질문이 "이 설정이 그 선을
        만드는가"이지 "모든 선이 그 선인가"가 아니기 때문이다.
    """
    if len(drawn) != 2:
        return Search(False, (), 0, "점 두 개를 찍어야 선이 된다")
    # 🔴 `Editable.flag`(소유자)가 아니라 `keys_for`(의존)로 고른다. 소유 파라미터가
    #    없는 플래그도 다른 값이 움직인다 — swing_trendline 이 그랬다.
    wanted = list(keys) if keys else list(keys_for(flag))
    if not wanted:
        return Search(False, (), 0, f"{flag} 에 걸린 파라미터가 없다 — 탐색할 것이 없다")

    lo = int(min(drawn[0][0], drawn[1][0]))
    hi = int(max(drawn[0][0], drawn[1][0]))
    atr = _atr_over(candles, lo, hi)
    if atr <= 0:
        return Search(False, (), 0, "이 구간 ATR 이 없다 — 거리를 잴 자가 없다")

    base = override_defaults()
    found: list[Candidate] = []
    tried = 0
    for key in wanted:
        for value in _grid(key):
            if tried >= MAX_TRIES:
                break
            tried += 1
            tuned = apply_overrides({key: value})
            frame = build_frame(
                candles, timeframe, [flag], params=tuned.params, deviation=tuned.deviation
            )
            layer = frame.layers[0] if frame.layers else None
            gaps = [
                distance
                for shape in (layer.shapes if layer else ())
                if (distance := line_distance(drawn, shape, atr)) is not None
            ]
            if not gaps:
                continue
            found.append(
                Candidate(
                    {key: value} if value != base[key] else {},
                    min(gaps),
                    layer.count if layer else 0,
                )
            )

    found.sort(key=lambda item: (item.distance, len(item.overrides)))
    closest = found[0] if found else None
    gap = closest.distance if closest else float("inf")
    return Search(
        reproduced=gap <= float(MATCH_ATR),
        best=tuple(found[:3]),
        tried=tried,
        verdict=_line_verdict(gap),
    )


def _line_verdict(gap: float) -> str:
    """좌표 거리에서 결론을 낸다.

    Args:
        gap: 가장 가까운 선까지의 거리 (ATR 배수).

    Returns:
        사람이 읽을 결론.
    """
    if gap <= float(MATCH_ATR):
        return (
            f"✅ 그 자리에 선을 긋는 설정이 있다 (거리 {gap:.2f}xATR ≤ {MATCH_ATR}) — "
            f"**파라미터 문제**다. 축 후보로 올려 out-of-sample 이 판정한다 (규칙 #12)"
        )
    if gap <= float(NEAR_ATR):
        return (
            f"거의 같은 선까지 간다 (거리 {gap:.2f}xATR) — 격자 해상도 차이로 보이며 "
            f"**파라미터 문제**로 본다. 그 근처 값을 축 후보로 올린다"
        )
    if gap == float("inf"):
        return (
            "🔴 어떤 설정에서도 **그 구간에 선을 긋지 않는다** — 개수를 맞추는 문제가 "
            "아니라 그 자리에 선이 생기지 않는다. 정의를 봐야 한다"
        )
    return (
        f"🔴 가장 가까운 선도 {gap:.2f}xATR 떨어져 있다 — **정의 자체**가 사람이 보는 "
        f"규칙과 다르다는 뜻이다. 파라미터가 아니라 알고리즘을 봐야 한다"
    )
