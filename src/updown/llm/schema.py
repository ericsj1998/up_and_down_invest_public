"""LLM 차트 분석 응답 스키마와 **엄격 파서** (Phase 5 §5-4).

## 왜 코드가 아니라 JSON 인가

위임받은 결정이다. 근거 셋:

1. **LLM 이 생성한 코드를 실행하는 것은 임의 코드 실행**이다. 샌드박스를 만들어도 그
   샌드박스가 새 공격면이 된다.
2. 우리에겐 **이미 렌더링 계약이 있다** — `orchestration/backtest/chart.py` 가 추세선·
   박스·띠를 좌표로 투영한다. 참고 이미지의 `@4h OB` 음영이 그 산출물이다.
3. JSON 은 **검증하고 거부할 수 있다.** 코드는 돌려 봐야 안다.

## ⛔ 어긴 응답을 고쳐서 살리지 않는다

소형 모델은 스키마를 자주 어긴다. **그것도 결과다.** 고쳐서 통과시키면 "이 모델은 형식을
못 지킨다"는 사실이 사라지는데, 그게 비교의 핵심 정보다 (절대 규칙 #8).

## 가격은 문자열로 받는다

`float` 로 파싱하면 소수가 조용히 바뀐다. 프로젝트 전역이 `Decimal` 인 이유가 그것이고,
LLM 경계라고 예외를 두면 그 경계에서 값이 틀어진다.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, cast

MAX_CONVICTION = 100


class SchemaError(ValueError):
    """응답이 스키마를 지키지 않았다.

    Note:
        이 예외는 **버그가 아니라 관측**이다. 모델별 무효응답률로 집계된다.
    """


class TrendCall(StrEnum):
    """LLM 이 판정한 추세."""

    UP = "UP"
    DOWN = "DOWN"
    RANGE = "RANGE"


class LevelKind(StrEnum):
    """수평선 종류."""

    SUPPORT = "support"
    RESISTANCE = "resistance"


class ZoneKind(StrEnum):
    """면(구간) 종류 — 참고 이미지의 음영 박스에 해당한다."""

    DEMAND = "demand"
    SUPPLY = "supply"


@dataclass(frozen=True, slots=True)
class Level:
    """수평 지지·저항 하나."""

    kind: LevelKind
    price: Decimal
    label: str


@dataclass(frozen=True, slots=True)
class Zone:
    """음영 구간 하나 (`@4h OB` 같은 것)."""

    kind: ZoneKind
    low: Decimal
    high: Decimal
    timeframe: str
    label: str


@dataclass(frozen=True, slots=True)
class Point:
    """추세선 끝점 — 시각과 가격."""

    ts: datetime
    price: Decimal


@dataclass(frozen=True, slots=True)
class Trendline:
    """추세선 하나."""

    kind: LevelKind
    start: Point
    end: Point


@dataclass(frozen=True, slots=True)
class TradePlan:
    """LLM 이 제시한 매매 계획.

    🔴 이것은 `LlmProposal` 의 일부이며 **주문이 아니다** (스펙 §5.3.1).
    집행값의 SSoT 는 언제나 RiskManager 다.

    Attributes:
        stop_loss: 손절가.
        take_profit_half: 절반 익절가.
        take_profit_full: 전체 익절가.
        conviction_pct: 구매 추천 정도 0~100.
    """

    stop_loss: Decimal
    take_profit_half: Decimal
    take_profit_full: Decimal
    conviction_pct: int


@dataclass(frozen=True, slots=True)
class ChartAnalysis:
    """차트 분석 응답 하나 — 파싱·검증을 통과한 것만 존재한다."""

    trend: TrendCall
    levels: tuple[Level, ...]
    zones: tuple[Zone, ...]
    trendlines: tuple[Trendline, ...]
    plan: TradePlan
    reasoning: str


def _price(raw: object, field: str) -> Decimal:
    """가격 한 칸을 Decimal 로.

    Args:
        raw: 원본 값. 문자열을 기대하지만 숫자도 받는다 (모델이 자주 숫자로 준다).
        field: 오류 메시지용 이름.

    Returns:
        Decimal.

    Raises:
        SchemaError: 숫자로 읽을 수 없거나 0 이하인 경우.
    """
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SchemaError(f"{field} 를 수로 읽을 수 없다: {raw!r}") from exc
    if value <= 0:
        raise SchemaError(f"{field} 가 0 이하다: {value}")
    return value


def _require(payload: dict[str, Any], key: str) -> Any:
    """필수 칸을 꺼낸다.

    Args:
        payload: 응답 dict.
        key: 칸 이름.

    Returns:
        값.

    Raises:
        SchemaError: 칸이 없는 경우.
    """
    if key not in payload:
        raise SchemaError(f"필수 칸 '{key}' 가 없다")
    return payload[key]


def _plan(payload: dict[str, Any], entry: Decimal) -> TradePlan:
    """계획을 파싱하고 **롱 온리 정합성**까지 본다.

    Args:
        payload: `plan` 칸.
        entry: 현재가(진입 기준). 정합성 판정의 기준선이다.

    Returns:
        계획.

    Raises:
        SchemaError: 손절이 진입 이상이거나 익절이 진입 이하인 경우.

    Note:
        🔴 이 검사가 없으면 "손절 위·익절 아래" 같은 뒤집힌 계획이 통과하고, 그것이
        `judge()` 에서 진입 즉시 익절로 세어져 **승률만 부풀린다**. 우리 셋업에 이미
        같은 결함이 있었다 (§1-0s). 롱 온리이므로 부등호는 하나뿐이다 (절대 규칙 #10).
    """
    stop = _price(_require(payload, "stop_loss"), "stop_loss")
    half = _price(_require(payload, "take_profit_half"), "take_profit_half")
    full = _price(_require(payload, "take_profit_full"), "take_profit_full")
    raw_conviction = _require(payload, "conviction_pct")
    try:
        conviction = int(Decimal(str(raw_conviction)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SchemaError(f"conviction_pct 를 수로 읽을 수 없다: {raw_conviction!r}") from exc
    if not 0 <= conviction <= MAX_CONVICTION:
        raise SchemaError(f"conviction_pct 가 0~100 밖이다: {conviction}")

    if stop >= entry:
        raise SchemaError(f"손절 {stop} 이 진입 {entry} 이상이다 — 롱 온리 전제 위반")
    if half <= entry or full <= entry:
        raise SchemaError(f"익절({half}/{full})이 진입 {entry} 이하다 — 진입 즉시 익절이 된다")
    if full < half:
        raise SchemaError(f"전체 익절 {full} 이 절반 익절 {half} 보다 낮다")
    return TradePlan(
        stop_loss=stop,
        take_profit_half=half,
        take_profit_full=full,
        conviction_pct=conviction,
    )


def _levels(raw: object) -> tuple[Level, ...]:
    """수평선 목록을 파싱한다.

    Args:
        raw: `levels` 칸. 없으면 빈 목록으로 본다 — 선을 못 찾는 것은 정상이다.

    Returns:
        수평선들.

    Raises:
        SchemaError: 항목 형식이 틀린 경우.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SchemaError(f"levels 가 배열이 아니다: {type(raw).__name__}")
    found: list[Level] = []
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict):
            raise SchemaError("levels 항목이 객체가 아니다")
        entry: dict[str, Any] = cast(dict[str, Any], item)
        try:
            kind = LevelKind(str(_require(entry, "kind")))
        except ValueError as exc:
            raise SchemaError(f"levels.kind 가 support/resistance 가 아니다: {entry}") from exc
        found.append(
            Level(
                kind=kind,
                price=_price(_require(entry, "price"), "levels.price"),
                label=str(entry.get("label", "")),
            )
        )
    return tuple(found)


def _zones(raw: object) -> tuple[Zone, ...]:
    """음영 구간 목록을 파싱한다.

    Args:
        raw: `zones` 칸.

    Returns:
        구간들.

    Raises:
        SchemaError: 형식 오류 또는 `low >= high`.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SchemaError(f"zones 가 배열이 아니다: {type(raw).__name__}")
    found: list[Zone] = []
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict):
            raise SchemaError("zones 항목이 객체가 아니다")
        entry: dict[str, Any] = cast(dict[str, Any], item)
        try:
            kind = ZoneKind(str(_require(entry, "kind")))
        except ValueError as exc:
            raise SchemaError(f"zones.kind 가 demand/supply 가 아니다: {entry}") from exc
        low = _price(_require(entry, "low"), "zones.low")
        high = _price(_require(entry, "high"), "zones.high")
        if low >= high:
            raise SchemaError(f"zones 의 low {low} 가 high {high} 이상이다")
        found.append(
            Zone(
                kind=kind,
                low=low,
                high=high,
                timeframe=str(entry.get("timeframe", "")),
                label=str(entry.get("label", "")),
            )
        )
    return tuple(found)


def _point(raw: object, field: str) -> Point:
    """추세선 끝점 하나.

    Args:
        raw: `{"ts": ..., "price": ...}`.
        field: 오류 메시지용 이름.

    Returns:
        끝점.

    Raises:
        SchemaError: 형식 오류 또는 시각이 UTC aware 가 아닌 경우.
    """
    if not isinstance(raw, dict):
        raise SchemaError(f"{field} 가 객체가 아니다")
    entry: dict[str, Any] = cast(dict[str, Any], raw)
    try:
        moment = datetime.fromisoformat(str(_require(entry, "ts")))
    except ValueError as exc:
        raise SchemaError(f"{field}.ts 가 ISO8601 이 아니다: {entry.get('ts')!r}") from exc
    if moment.tzinfo is None:
        # 절대 규칙 #7 — naive 를 UTC 로 가정하면 시간대만큼 선이 밀린다.
        raise SchemaError(f"{field}.ts 에 시간대가 없다: {moment} (UTC 로 달라)")
    return Point(ts=moment, price=_price(_require(entry, "price"), f"{field}.price"))


def _trendlines(raw: object) -> tuple[Trendline, ...]:
    """추세선 목록을 파싱한다.

    Args:
        raw: `trendlines` 칸.

    Returns:
        추세선들.

    Raises:
        SchemaError: 형식 오류.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SchemaError(f"trendlines 가 배열이 아니다: {type(raw).__name__}")
    found: list[Trendline] = []
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict):
            raise SchemaError("trendlines 항목이 객체가 아니다")
        entry: dict[str, Any] = cast(dict[str, Any], item)
        try:
            kind = LevelKind(str(_require(entry, "kind")))
        except ValueError as exc:
            raise SchemaError("trendlines.kind 가 support/resistance 가 아니다") from exc
        found.append(
            Trendline(
                kind=kind,
                start=_point(_require(entry, "from"), "trendlines.from"),
                end=_point(_require(entry, "to"), "trendlines.to"),
            )
        )
    return tuple(found)


def extract_json(text: str) -> str:
    """응답 본문에서 JSON 객체를 꺼낸다.

    Args:
        text: 모델 원문.

    Returns:
        JSON 문자열.

    Raises:
        SchemaError: 객체를 찾을 수 없는 경우.

    Note:
        ```json 펜스와 앞뒤 잡담만 벗긴다. **내용은 고치지 않는다** — 따옴표를 채우거나
        쉼표를 지우는 식의 복구를 넣으면 "형식을 못 지킨다"는 사실이 사라진다.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise SchemaError("응답에서 JSON 객체를 찾을 수 없다")
    return text[start : end + 1]


def parse_analysis(text: str, entry: Decimal) -> ChartAnalysis:
    """모델 원문을 검증된 분석으로.

    Args:
        text: 모델이 돌려준 원문.
        entry: 현재가 — 계획 정합성의 기준선이다.

    Returns:
        분석.

    Raises:
        SchemaError: 어느 단계든 스키마를 어긴 경우.
    """
    try:
        payload = json.loads(extract_json(text))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"JSON 파싱 실패: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaError("최상위가 객체가 아니다")
    body: dict[str, Any] = cast(dict[str, Any], payload)

    try:
        trend = TrendCall(str(_require(body, "trend")).upper())
    except ValueError as exc:
        raise SchemaError(f"trend 가 UP/DOWN/RANGE 가 아니다: {body.get('trend')!r}") from exc

    plan_raw = _require(body, "plan")
    if not isinstance(plan_raw, dict):
        raise SchemaError("plan 이 객체가 아니다")

    return ChartAnalysis(
        trend=trend,
        levels=_levels(body.get("levels")),
        zones=_zones(body.get("zones")),
        trendlines=_trendlines(body.get("trendlines")),
        plan=_plan(cast(dict[str, Any], plan_raw), entry),
        reasoning=str(body.get("reasoning", "")),
    )
