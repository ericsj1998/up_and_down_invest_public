"""EDGAR companyfacts JSON → `FinancialFact` (T243).

companyfacts 의 모양:

    {"cik": 320193, "entityName": "Apple Inc.",
     "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
         {"start": "2019-09-29", "end": "2020-09-26", "val": 274515000000,
          "accn": "0000320193-20-000096", "fy": 2020, "fp": "FY", "form": "10-K",
          "filed": "2020-10-30", "frame": "CY2020"}, ...]}}},
               "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [...]}}}}}

시점 값(재무상태표)은 `start` 가 없다. 같은 기간이 여러 공시에 실린다(원본 · 다음 해 비교 열) —
**전부 남긴다.**
어느 것을 쓸지는 `filed_at` 으로 읽는 쪽이 고른다 (시점 정합).

폴백: 같은 `(period_start, period_end, accession)` 이 여러 태그로 오면 설정의 **앞 태그**를 쓴다.
회사가 연도 중간에 태그를 갈아탄 경우(Revenues → RevenueFromContract…)는 기간이 겹치지 않으므로
둘 다 살아남아 이어진다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from updown.common.domain.fundamentals import FactKind, FinancialFact, FundamentalsConfig

SOURCE = "edgar"

# 기간 값인데 duration 이 이 밖이면 버린다 — 1개월짜리 전환기 보고 같은 조각은 분기 합산을 망친다.
MIN_FLOW_DAYS = 60
MAX_FLOW_DAYS = 400


def _date(raw: object) -> date | None:
    """ISO 날짜 문자열을 날짜로 — 아니면 None (행을 버리는 신호)."""
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _decimal(raw: object) -> Decimal | None:
    """`val` 을 Decimal 로 — 유한한 숫자만.

    Args:
        raw: JSON 값.

    Returns:
        Decimal. `bool`(int 의 하위형이라 따로 막는다) · 파싱 실패 · NaN/Infinity 는 None.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse_company_facts(
    body: Mapping[str, object], *, symbol: str, config: FundamentalsConfig
) -> list[FinancialFact]:
    """EDGAR companyfacts 응답을 설정에 매핑된 사실로 바꾼다.

    Args:
        body: `EdgarClient.company_facts` 결과.
        symbol: 종목 코드 (행에 새긴다 — EDGAR 는 CIK 만 안다).
        config: 개념 매핑.

    Returns:
        `filed_at` · `period_end` 오름차순 사실. 매핑에 없는 태그 · 단위가 다른 행 · 날짜/값이 깨진
        행은 버린다.
    """
    cik_raw = body.get("cik")
    entity_id = str(cik_raw).zfill(10) if isinstance(cik_raw, int | str) else ""
    facts_raw = body.get("facts")
    taxonomies: Mapping[str, object] = (
        cast("Mapping[str, object]", facts_raw) if isinstance(facts_raw, dict) else {}
    )

    seen: dict[tuple[str, date, date, str], FinancialFact] = {}
    for spec in config.concepts.values():
        for tag in spec.tags:
            taxonomy, _, name = tag.partition(":")
            tax_block = taxonomies.get(taxonomy)
            if not isinstance(tax_block, dict):
                continue
            concept_block = cast("Mapping[str, object]", tax_block).get(name)
            if not isinstance(concept_block, dict):
                continue
            units = cast("Mapping[str, object]", concept_block).get("units")
            if not isinstance(units, dict):
                continue
            rows = cast("Mapping[str, object]", units).get(spec.unit)
            if not isinstance(rows, list):
                continue
            for raw in cast("list[object]", rows):
                fact = _row(
                    raw,
                    spec_name=spec.name,
                    kind=spec.kind,
                    tag=tag,
                    unit=spec.unit,
                    symbol=symbol,
                    entity_id=entity_id,
                )
                if fact is None:
                    continue
                key = (spec.name, fact.period_start, fact.period_end, fact.accession)
                # 앞 태그가 이미 채웠으면 그대로 — 폴백 순서가 우선순위다.
                seen.setdefault(key, fact)
    return sorted(seen.values(), key=lambda f: (f.filed_at, f.period_end, f.concept))


def _row(
    raw: object,
    *,
    spec_name: str,
    kind: FactKind,
    tag: str,
    unit: str,
    symbol: str,
    entity_id: str,
) -> FinancialFact | None:
    """EDGAR companyfacts 의 값 행 하나를 사실로 — 못 믿을 행은 None.

    Args:
        raw: `units.<unit>` 배열의 원소.
        spec_name: 설정의 개념 이름 (`revenue` 처럼 태그와 무관한 우리 이름).
        kind: 시점(INSTANT) 값인가 기간(FLOW) 값인가.
        tag: 이 행이 온 XBRL 태그 (`us-gaap:Revenues`).
        unit: 단위 (`USD` · `shares`).
        symbol: 종목 코드 — EDGAR 는 CIK 만 알아 행에 새긴다.
        entity_id: 10자리 CIK.

    Returns:
        사실. 다음은 None — 날짜·값·접수번호가 깨진 행, 시점 값인데 `start != end` 인 행(태그
        설정이 틀린 것), 기간 값인데 길이가 `MIN_FLOW_DAYS`~`MAX_FLOW_DAYS` 밖인 행(전환기 조각).
        `fy`/`fp`/`form` 은 없어도 산다 — 기말 연도·빈 문자열로 채운다.
    """
    if not isinstance(raw, dict):
        return None
    row = cast("Mapping[str, object]", raw)
    end = _date(row.get("end"))
    filed = _date(row.get("filed"))
    value = _decimal(row.get("val"))
    accession = row.get("accn")
    if end is None or filed is None or value is None or not isinstance(accession, str):
        return None
    start = _date(row.get("start"))
    if kind is FactKind.INSTANT:
        # 시점 값에 start 가 붙어 오면 그 태그가 사실은 기간 값이다 — 설정이 틀린 것이니 버린다.
        if start is not None and start != end:
            return None
        start = end
    else:
        if start is None:
            return None
        days = (end - start).days
        if days < MIN_FLOW_DAYS or days > MAX_FLOW_DAYS:
            return None
    fy = row.get("fy")
    fp = row.get("fp")
    form = row.get("form")
    return FinancialFact(
        source=SOURCE,
        entity_id=entity_id,
        symbol=symbol,
        concept=spec_name,
        tag=tag,
        unit=unit,
        period_start=start,
        period_end=end,
        value=value,
        fiscal_year=fy if isinstance(fy, int) and not isinstance(fy, bool) else end.year,
        fiscal_period=fp if isinstance(fp, str) else "",
        form=form if isinstance(form, str) else "",
        filed_at=datetime(filed.year, filed.month, filed.day, tzinfo=UTC),
        accession=accession,
    )


__all__ = ["SOURCE", "parse_company_facts"]
