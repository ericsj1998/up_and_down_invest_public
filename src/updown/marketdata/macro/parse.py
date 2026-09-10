"""거시 출처 응답 → 값 (순수 · 시험 대상)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast


class MacroParseError(ValueError):
    """출처 응답의 모양이 기대와 다르다 — 값을 지어내지 않고 실패로 낸다."""


def _decimal(value: object, what: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MacroParseError(f"{what} 가 숫자가 아니다: {value!r}") from exc


def parse_yahoo_chart(body: Mapping[str, Any]) -> tuple[Decimal, Decimal | None, datetime | None]:
    """야후 `v8/finance/chart` 응답 → (현재가, 전일 종가, 시각).

    Args:
        body: JSON 본문.

    Returns:
        `meta.regularMarketPrice` · `meta.chartPreviousClose`(없으면 `previousClose`) ·
        `regularMarketTime`.

    Raises:
        MacroParseError: 결과가 없거나 가격이 없다.
    """
    raw_chart = body.get("chart")
    chart: Mapping[str, Any] = (
        cast("Mapping[str, Any]", raw_chart) if isinstance(raw_chart, Mapping) else {}
    )
    results = cast("list[Any]", chart.get("result") or [])
    if not results or not isinstance(results[0], Mapping):
        raise MacroParseError(f"야후 결과 없음: {chart.get('error')!r}"[:160])
    first = cast("Mapping[str, Any]", results[0])
    meta = cast("Mapping[str, Any]", first.get("meta") or {})
    price = meta.get("regularMarketPrice")
    if price is None:
        raise MacroParseError("야후 meta.regularMarketPrice 없음")
    prev_raw = meta.get("chartPreviousClose", meta.get("previousClose"))
    prev = None if prev_raw is None else _decimal(prev_raw, "전일 종가")
    stamp = meta.get("regularMarketTime")
    as_of = datetime.fromtimestamp(int(stamp), tz=UTC) if isinstance(stamp, int | float) else None
    return _decimal(price, "현재가"), prev, as_of


def parse_effr(body: Mapping[str, Any]) -> tuple[Decimal, Decimal | None, Decimal | None, str]:
    """뉴욕연준 `rates/unsecured/effr/last/1` → (EFFR %, 목표 하한, 목표 상한, 기준일).

    Args:
        body: JSON 본문.

    Returns:
        `refRates[0]` 의 값들 — 목표범위가 없으면 None.

    Raises:
        MacroParseError: 행이 없다.
    """
    rows = cast("list[Any]", body.get("refRates") or [])
    if not rows or not isinstance(rows[0], Mapping):
        raise MacroParseError("EFFR 행 없음")
    row = cast("Mapping[str, Any]", rows[0])
    low = row.get("targetRateFrom")
    high = row.get("targetRateTo")
    return (
        _decimal(row.get("percentRate"), "EFFR"),
        None if low is None else _decimal(low, "목표 하한"),
        None if high is None else _decimal(high, "목표 상한"),
        str(row.get("effectiveDate") or ""),
    )


def parse_bls_series(body: Mapping[str, Any]) -> list[tuple[str, Decimal]]:
    """BLS v1 `timeseries/data/{series}` → `("YYYY-MM", 값)` 들 (월 행만 · 연간 M13 제외).

    Args:
        body: JSON 본문.

    Returns:
        월 행들(순서 무관).

    Raises:
        MacroParseError: 실패 응답이거나 행이 없다.
    """
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise MacroParseError(f"BLS 실패: {body.get('message')!r}"[:160])
    results = cast("Mapping[str, Any]", body.get("Results") or {})
    series = cast("list[Any]", results.get("series") or [])
    if not series or not isinstance(series[0], Mapping):
        raise MacroParseError("BLS 시계열 없음")
    out: list[tuple[str, Decimal]] = []
    head = cast("Mapping[str, Any]", series[0])
    for raw_row in cast("list[Any]", head.get("data") or []):
        if not isinstance(raw_row, Mapping):
            continue
        row = cast("Mapping[str, Any]", raw_row)
        period = str(row.get("period") or "")
        if not period.startswith("M") or period == "M13":
            continue
        out.append((f"{row.get('year')}-{period[1:]}", _decimal(row.get("value"), "CPI")))
    if not out:
        raise MacroParseError("BLS 월 행 없음")
    return out


def parse_cboe_vix_csv(text: str) -> tuple[Decimal, Decimal | None, str]:
    """CBOE `VIX_History.csv` → (마지막 종가, 그 전 종가, 날짜 `MM/DD/YYYY`).

    Args:
        text: CSV 본문.

    Returns:
        마지막 두 행의 종가와 마지막 날짜.

    Raises:
        MacroParseError: 행이 둘 미만이다.
    """
    rows = [line.split(",") for line in text.strip().splitlines() if "," in line]
    data = [row for row in rows if len(row) >= 5 and row[0][:1].isdigit()]
    if not data:
        raise MacroParseError("CBOE CSV 행 없음")
    last = data[-1]
    prev = data[-2] if len(data) > 1 else None
    return (
        _decimal(last[4], "VIX 종가"),
        None if prev is None else _decimal(prev[4], "전일"),
        last[0],
    )


__all__ = [
    "MacroParseError",
    "parse_bls_series",
    "parse_cboe_vix_csv",
    "parse_effr",
    "parse_yahoo_chart",
]
