"""셋업 탐지를 점검기에서 돌린다 — **실전과 같은 재료로**.

## 🔴 왜 `build_frame` 안에 못 넣었나

구조물·지표는 시간축 하나만 보면 계산된다. **셋업은 아니다** — 탐지기는
`MarketContext` 를 받고 그 안의 여러 시간축을 함께 본다 (상위 TF 추세, 멀티 TF 합류).

시간축마다 단일 TF 컨텍스트를 만들어 돌리면 화면에는 셋업이 그려지지만 **실전이 만드는
것과 다른 셋업**이다. 점검기가 그런 그림을 보여 주면, 이 도구가 막으려던 바로 그
사고("화면이 그럴듯한데 계산이 다르다")를 도구 자신이 만든다.

그래서 컨텍스트를 **한 번만** 조립하고, 나온 셋업을 시간축으로 흩는다.

## ⚠️ 실전과 다른 점 둘 — 숨기지 않는다

    structures = ()   DB 의 활성 구조물 이력을 안 읽는다. §4.13 as-of 렌더링용이고
                      지금 플러그인들은 `geometry` 만 쓰지만, 언젠가 쓰면 달라진다
    trend = {}        추세 서비스(§4.16)를 안 태운다. 추세 게이트가 붙은 탐지기는
                      `NO_TREND` 로 기각될 수 있다

둘 다 `note` 에 적어 화면이 말한다. 조용히 두면 "이 셋업은 원래 안 잡히는구나"로
읽히고, 그것은 관측이 아니라 도구의 결함이다 (절대 규칙 #8).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from updown.analysis.detectors.base import MarketContext
from updown.analysis.detectors.registry import SetupRegistry
from updown.analysis.detectors.rules import load_rules
from updown.analysis.indicators import snapshot as indicator_snapshot
from updown.analysis.structures.bundle import StructureBundle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.domain.setup import TradeSetup
from updown.common.domain.trend import TrendState
from updown.orchestration.inspection.snapshot import Layer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from updown.common.domain.candle import Candle

SETUP_PREFIX = "setup."

CONTEXT_NOTE = (
    "⚠️ 실전과 두 가지가 다르다 — DB 구조물 이력을 안 읽고(structures=()), "
    "추세 서비스를 안 태운다(trend={}). 추세 게이트가 붙은 룰은 여기서 덜 잡힐 수 있다"
)


@dataclass(frozen=True, slots=True)
class SetupRun:
    """셋업 탐지 한 판.

    Attributes:
        layers: 시간축별 레이어들. 탐지기가 시간축을 안 붙이면 진입 TF 로 간다.
        note: 컨텍스트 한계 설명. 화면이 그대로 보여 준다.
        failures: 돌다가 죽은 룰 (`rule_id -> 이유`). **조용히 비우지 않는다.**
    """

    layers: dict[Timeframe, list[Layer]]
    note: str
    failures: dict[str, str]


def _setup_shape(setup: TradeSetup) -> dict[str, Any]:
    """셋업 하나를 그리기용 dict 로.

    Args:
        setup: 셋업.

    Returns:
        직렬화용 dict. 가격은 문자열이다.

    Note:
        `entry_plan` 을 **배열로** 낸다. 분할 진입이면 점이 여러 개 찍혀야 한다는
        지적(T19 의 출발점)이 여기서도 같이 적용된다 — 평단 하나만 내면 화면이
        3분할을 1분할처럼 보여 준다.
    """
    return {
        "setup_type": setup.setup_type,
        "rule_version": setup.rule_version,
        "trigger": setup.entry_trigger.value,
        "avg_entry": str(setup.avg_entry),
        "stop": str(setup.stop_loss),
        "rr": str(setup.rr_ratio),
        "confidence": setup.confidence,
        "entry_plan": [
            {"price": str(leg.price), "ratio": str(leg.ratio)} for leg in setup.entry_plan
        ],
        "tp_ladder": [
            {
                "price": str(step.price),
                "ratio": str(step.ratio),
                "then": None if step.then is None else step.then.value,
            }
            for step in setup.tp_ladder
        ],
        "stop_candidates": [
            {"price": str(item.price), "timeframe": item.timeframe.value}
            for item in setup.stop_candidates
        ],
        "evidence": [str(item) for item in setup.evidence],
    }


def _timeframe_of(setup: TradeSetup, fallback: Timeframe) -> Timeframe:
    """이 셋업을 어느 차트에 그릴까.

    Args:
        setup: 셋업.
        fallback: 못 정하면 쓸 시간축.

    Returns:
        시간축.

    Note:
        `TradeSetup` 에는 시간축 필드가 없다 — 손절 후보에만 붙어 있다 (축 ② 판정용).
        **1순위 손절과 같은 후보**의 시간축을 쓴다. 그것이 이 계획을 지탱하는 구조물이
        선 시간축이기 때문이다.
    """
    for item in setup.stop_candidates:
        if item.price == setup.stop_loss:
            return item.timeframe
    return fallback


def detect(
    instrument: Instrument,
    as_of: datetime,
    candles: "Mapping[Timeframe, Sequence[Candle]]",
    geometry: "Mapping[Timeframe, StructureBundle]",
    flags: "Sequence[str]",
    trend: "Mapping[Timeframe, TrendState] | None" = None,
) -> SetupRun:
    """고른 셋업 룰들을 **한 컨텍스트에서** 돌린다.

    Args:
        instrument: 종목.
        as_of: 점검 시점.
        candles: 시간축별 **as-of 로 잘린** 봉들.
        geometry: 시간축별 작도 결과 (`build_frame` 이 만든 것과 같은 파라미터여야 한다).
        flags: `setup.<rule_id>` 형태의 플래그들. 다른 것은 무시한다.
        trend: 시간축별 추세 상태. **창 앞쪽 워밍업까지 써서** 계산한 값을 넘긴다 —
            창 안에서 구하면 `None` 이 나와 국면 게이트를 가진 셋업이 영원히 0건이다.

    Returns:
        탐지 결과. 룰 하나가 죽어도 나머지는 돈다.

    Note:
        🔴 **룰 하나의 예외가 화면 전체를 죽이지 않는다.** 다만 조용히 빈 결과로
        넘기지도 않는다 — `failures` 에 이유가 남고 레이어 `note` 에 실린다.
        "이 룰은 아무것도 못 찾았다"와 "이 룰이 터졌다"는 완전히 다른 정보다.
    """
    wanted = [flag.removeprefix(SETUP_PREFIX) for flag in flags if flag.startswith(SETUP_PREFIX)]
    if not wanted:
        return SetupRun({}, "", {})

    fallback = min(candles, key=lambda frame: len(candles[frame]), default=None)
    if fallback is None:
        return SetupRun({}, "봉이 없다", {})

    indicators = {
        frame: indicator_snapshot.compute(list(rows)).at(len(rows) - 1)
        for frame, rows in candles.items()
        if len(rows) > 0
    }
    context = MarketContext(
        instrument=instrument,
        as_of=as_of,
        candles={frame: list(rows) for frame, rows in candles.items()},
        indicators=indicators,
        # ⚠️ 위 docstring 의 "실전과 다른 점" 둘이 여기다.
        structures=(),
        geometry=dict(geometry),
        # 🔴 **추세를 밖에서 받는다.** 예전에는 `{}` 였고, 그래서 국면 게이트를 가진
        #    셋업이 점검기에서 **영원히 0건**이었다 — 박스권 셋업이 그것이다.
        #
        #    ⚠️ 창 안에서 계산하면 안 된다. 197봉으로는 추세 판정이 `None` 이라 결국
        #    같은 0건이 된다 — 워밍업이 창을 넘어선다. 거래량 기준선에서 겪은 것과
        #    **같은 형태**의 함정이다.
        trend=dict(trend or {}),
    )

    registry = SetupRegistry.from_plugins(load_rules())
    layers: dict[Timeframe, list[Layer]] = {}
    failures: dict[str, str] = {}
    for rule_id in wanted:
        flag = f"{SETUP_PREFIX}{rule_id}"
        try:
            found = registry.get(rule_id).detector.detect(context)
        except Exception as exc:
            failures[rule_id] = f"{type(exc).__name__}: {exc}"
            for frame in candles:
                layers.setdefault(frame, []).append(
                    Layer(flag, (), note=f"🔴 룰이 예외로 죽었다 — {failures[rule_id]}")
                )
            continue

        by_frame: dict[Timeframe, list[dict[str, Any]]] = {}
        for setup in found:
            frame = _timeframe_of(setup, fallback)
            by_frame.setdefault(frame, []).append(_setup_shape(setup))
        for frame in candles:
            shapes = by_frame.get(frame, [])
            layers.setdefault(frame, []).append(
                Layer(flag, tuple(shapes), note="" if shapes else CONTEXT_NOTE)
            )

    return SetupRun(layers, CONTEXT_NOTE, failures)
