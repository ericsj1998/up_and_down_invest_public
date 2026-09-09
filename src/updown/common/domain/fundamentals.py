"""재무 사실 — 공시에서 온 원자료 한 칸과 그 개념 매핑 (T243 · 2026-09-09).

지표(PER · 부채비율 …)는 여기 없다 — 그것은 `analysis/fundamentals/` 가 이 사실들로 **계산**한다.
여기는 "무엇이 공시됐나" 만 담는다: 어느 회사가 · 어느 기간의 · 어느 개념을 · 얼마로 · 언제 냈나.

## 시점 정합 (point-in-time · T09 정신)

`filed_at` 이 사실의 절반이다. 2020 회계연도 매출은 2020-09 에 끝났지만 **2020-10-30 에야
알려졌다** — 백테스트가 10월 초 시점에 그 값을 쓰면 미래 참조다. 그래서 같은 기간의 값이 여러
공시(원본 · 정정 · 다음 해 10-K 의
비교 열)에 실려 있어도 전부 따로 보관하고, 읽는 쪽이 "그때 알 수 있던 것" 만 고른다.

## 개념 매핑은 코드가 아니라 설정이다

회계 태그(`us-gaap:Revenues`)는 회사마다 · 연도마다 다르다. `config/fundamentals/us_gaap.yml` 이
우리 이름(`revenue`) → 태그 폴백 순서를 선언하고, 코드는 우리 이름만 안다 (spec §4.3.1 —
임계값·매핑을 코드에 박지 않는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "fundamentals" / "us_gaap.yml"
)


class FundamentalsConfigError(ValueError):
    """재무 설정을 읽을 수 없다 — 기본값으로 넘어가지 않는다 (절대 규칙 #8).

    매핑이 빠지면 지표가 조용히 None 이 되고, 문턱이 빠지면 점수가 조용히 후해진다.
    """


class FactKind(StrEnum):
    """기간 값인가 시점 값인가."""

    FLOW = "flow"
    """기간 값 — 손익·현금흐름. `period_start ~ period_end`."""
    INSTANT = "instant"
    """시점 값 — 재무상태표·주식수. `period_start == period_end`."""


@dataclass(frozen=True, slots=True)
class FinancialFact:
    """공시에서 온 값 한 칸.

    Attributes:
        source: 출처 — `edgar` · `dart`.
        entity_id: 출처 기관의 발행자 식별자 (EDGAR = CIK · DART = corp_code). 공시 링크를 만드는 데
            쓴다.
        symbol: 종목 코드.
        concept: 우리 이름 (`revenue` · `equity` …). 설정의 키다.
        tag: 원본 태그 (`us-gaap:Revenues`) — 어느 폴백이 맞았는지 남긴다.
        unit: 단위 (`USD` · `shares` · `USD/shares`).
        period_start: 기간 시작. 시점 값이면 `period_end` 와 같다.
        period_end: 기간 끝.
        value: 값.
        fiscal_year: 회계연도.
        fiscal_period: `FY` · `Q1` · `Q2` · `Q3` · `Q4`.
        form: 공시 서식 (`10-K` · `10-Q`).
        filed_at: 공시일 (UTC aware). **이 시각 전에는 몰랐던 값이다.**
        accession: 공시 접수 번호.
    """

    source: str
    entity_id: str
    symbol: str
    concept: str
    tag: str
    unit: str
    period_start: date
    period_end: date
    value: Decimal
    fiscal_year: int
    fiscal_period: str
    form: str
    filed_at: datetime
    accession: str

    @property
    def kind(self) -> FactKind:
        """시점 값이면 INSTANT."""
        return FactKind.INSTANT if self.period_start == self.period_end else FactKind.FLOW

    @property
    def duration_days(self) -> int:
        """기간 길이(일). 시점 값은 0."""
        return (self.period_end - self.period_start).days


@dataclass(frozen=True, slots=True)
class Filing:
    """공시 한 건 — 사실들의 출처.

    Attributes:
        accession: 접수 번호.
        form: 서식.
        filed_at: 공시일.
        url: 원문 링크. 출처가 링크를 못 만들면 None.
    """

    accession: str
    form: str
    filed_at: datetime
    url: str | None


@dataclass(frozen=True, slots=True)
class ConceptSpec:
    """우리 이름 하나의 매핑.

    Attributes:
        name: 우리 이름.
        kind: 기간/시점.
        unit: 받아들이는 단위.
        tags: 폴백 순서의 원본 태그 (`taxonomy:Tag`).
    """

    name: str
    kind: FactKind
    unit: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DebtThresholds:
    """부채 위험 깃발 문턱.

    Attributes:
        debt_to_equity_max: 총부채/자본이 이보다 크면 깃발.
        net_debt_to_ebitda_max: 순부채/EBITDA 가 이보다 크면 깃발.
        interest_coverage_min: 영업이익/이자가 이보다 작으면 깃발.
        current_ratio_min: 유동비율이 이보다 작으면 깃발.
    """

    debt_to_equity_max: Decimal
    net_debt_to_ebitda_max: Decimal
    interest_coverage_min: Decimal
    current_ratio_min: Decimal


@dataclass(frozen=True, slots=True)
class ScoreRules:
    """저평가 점수 규칙 — 설정 `score` 블록.

    Attributes:
        percentile_years: 자기 역사 창(년).
        min_history_points: 백분위에 필요한 최소 표본.
        min_price_metrics: 점수에 필요한 최소 가격 지표 수.
        penalty_per_flag: 깃발 하나당 감점.
        debt: 깃발 문턱.
    """

    percentile_years: int
    min_history_points: int
    min_price_metrics: int
    penalty_per_flag: Decimal
    debt: DebtThresholds


@dataclass(frozen=True, slots=True)
class FundamentalsConfig:
    """`us_gaap.yml` 전체.

    Attributes:
        concepts: 우리 이름 → 매핑.
        score: 점수 규칙.
    """

    concepts: Mapping[str, ConceptSpec]
    score: ScoreRules


def _decimal(block: Mapping[str, object], key: str, where: str) -> Decimal:
    raw = block.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise FundamentalsConfigError(f"{where}.{key} 가 없거나 숫자가 아니다")
    return Decimal(str(raw))


def _int(block: Mapping[str, object], key: str, where: str) -> int:
    raw = block.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise FundamentalsConfigError(f"{where}.{key} 는 양의 정수여야 한다")
    return raw


def parse_fundamentals_config(raw: Mapping[str, object]) -> FundamentalsConfig:
    """설정 매핑을 해석한다 (IO 없음 — 시험이 직접 부른다).

    Args:
        raw: `yaml.safe_load` 결과.

    Returns:
        해석된 설정.

    Raises:
        FundamentalsConfigError: 개념 블록이 비었거나 태그가 `taxonomy:Tag` 꼴이 아니거나 문턱이
            빠진 경우.
    """
    concepts_raw = raw.get("concepts")
    if not isinstance(concepts_raw, dict) or not concepts_raw:
        raise FundamentalsConfigError("`concepts` 매핑이 비었다")
    concepts: dict[str, ConceptSpec] = {}
    for name, block in cast("Mapping[str, object]", concepts_raw).items():
        if not isinstance(block, dict):
            raise FundamentalsConfigError(f"concepts.{name} 이 매핑이 아니다")
        spec = cast("Mapping[str, object]", block)
        kind_raw = spec.get("kind")
        try:
            kind = FactKind(str(kind_raw))
        except ValueError as exc:
            raise FundamentalsConfigError(
                f"concepts.{name}.kind={kind_raw!r} (flow · instant)"
            ) from exc
        unit = spec.get("unit")
        if not isinstance(unit, str) or not unit:
            raise FundamentalsConfigError(f"concepts.{name}.unit 이 없다")
        tags_raw = spec.get("tags")
        if not isinstance(tags_raw, list) or not tags_raw:
            raise FundamentalsConfigError(f"concepts.{name}.tags 가 비었다")
        tags: list[str] = []
        for tag in cast("list[object]", tags_raw):
            if not isinstance(tag, str) or tag.count(":") != 1:
                raise FundamentalsConfigError(
                    f"concepts.{name}.tags 의 {tag!r} 는 taxonomy:Tag 꼴이어야 한다"
                )
            tags.append(tag)
        concepts[name] = ConceptSpec(name=name, kind=kind, unit=unit, tags=tuple(tags))

    score_raw = raw.get("score")
    if not isinstance(score_raw, dict):
        raise FundamentalsConfigError("`score` 매핑이 없다 — 점수 규칙은 설정이 정한다")
    score = cast("Mapping[str, object]", score_raw)
    debt_raw = score.get("debt")
    if not isinstance(debt_raw, dict):
        raise FundamentalsConfigError("`score.debt` 매핑이 없다")
    debt = cast("Mapping[str, object]", debt_raw)
    rules = ScoreRules(
        percentile_years=_int(score, "percentile_years", "score"),
        min_history_points=_int(score, "min_history_points", "score"),
        min_price_metrics=_int(score, "min_price_metrics", "score"),
        penalty_per_flag=_decimal(score, "penalty_per_flag", "score"),
        debt=DebtThresholds(
            debt_to_equity_max=_decimal(debt, "debt_to_equity_max", "score.debt"),
            net_debt_to_ebitda_max=_decimal(debt, "net_debt_to_ebitda_max", "score.debt"),
            interest_coverage_min=_decimal(debt, "interest_coverage_min", "score.debt"),
            current_ratio_min=_decimal(debt, "current_ratio_min", "score.debt"),
        ),
    )
    return FundamentalsConfig(concepts=concepts, score=rules)


def load_fundamentals_config(path: Path | None = None) -> FundamentalsConfig:
    """설정을 파일에서 읽는다.

    Args:
        path: 설정 경로. None 이면 `config/fundamentals/us_gaap.yml`.

    Returns:
        해석된 설정.

    Raises:
        FundamentalsConfigError: 파일이 없거나 형식이 틀린 경우.
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        raise FundamentalsConfigError(f"재무 설정이 없다: {target}")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FundamentalsConfigError(f"{target} 의 최상위가 매핑이 아니다")
    return parse_fundamentals_config(cast("Mapping[str, object]", raw))


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ConceptSpec",
    "DebtThresholds",
    "FactKind",
    "Filing",
    "FinancialFact",
    "FundamentalsConfig",
    "FundamentalsConfigError",
    "ScoreRules",
    "load_fundamentals_config",
    "parse_fundamentals_config",
]
