"""거시 지표 어댑터 — 출처 여럿을 지표 목록 하나로 (T262).

지표마다 따로 시도하고 따로 실패한다. 결과는 `(지표들, 실패들)` — 실패에는 **왜** 가 들어 있어
화면과 채팅이 "못 띄우는 것은 이유와 함께" 보여 준다(사용자 요구 2026-09-10).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from updown.common.domain.macro import Indicator, cpi_yoy, pct_change, vix_band
from updown.common.logging.setup import get_logger
from updown.marketdata.macro.client import MacroClient
from updown.marketdata.macro.parse import (
    parse_bls_series,
    parse_cboe_vix_csv,
    parse_effr,
    parse_yahoo_chart,
)

_logger = get_logger("marketdata.macro")


class TossIndicatorSource(Protocol):
    """토스 쪽 출처 — 환율과 국내 지표 (`TossAdapter` 가 구조적으로 만족한다)."""

    async def exchange_rate(self, base: str = "USD", quote: str = "KRW") -> dict[str, Any]:
        """환율 응답.

        Args:
            base: 기준 통화.
            quote: 표시 통화.

        Returns:
            `rate` · `midRate` · `validFrom` 이 든 dict.
        """
        ...

    async def indicator_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        """시장 지표 현재가 행들.

        Args:
            symbols: 카탈로그 심볼들.

        Returns:
            `symbol` · `lastPrice` · `timestamp` 가 든 행들.
        """
        ...


@dataclass(frozen=True, slots=True)
class YahooSpec:
    """야후로 받는 지표 하나."""

    key: str
    label: str
    symbol: str
    unit: str
    scale: Decimal = Decimal(1)
    note: str = ""


YAHOO_SPECS: tuple[YahooSpec, ...] = (
    YahooSpec("vix", "VIX 공포지수", "^VIX", "pt"),
    YahooSpec("nq", "나스닥100 선물", "NQ=F", "pt", note="CME NQ 근월물"),
    YahooSpec("sp500", "S&P 500", "^GSPC", "pt"),
    # ⚠️ 야후 `^TNX` 는 이미 % 값이다(실측 4.837) — CBOE 원지수(x10)가 아니다. 나누면 0.48% 가 된다.
    YahooSpec("us10y", "미국 10년물 금리", "^TNX", "%", note="CBOE 10년물 수익률 지수"),
    YahooSpec("dxy", "달러 인덱스", "DX-Y.NYB", "pt"),
    YahooSpec("gold", "금 선물", "GC=F", "USD", note="COMEX 근월물 · 온스당"),
    YahooSpec("wti", "WTI 원유", "CL=F", "USD", note="NYMEX 근월물 · 배럴당"),
)
"""야후 차트로 받는 것들 — 비공식 API 라 막히면 그 지표만 실패 목록으로 간다."""

TOSS_INDICATORS: tuple[tuple[str, str, str, str], ...] = (
    ("kospi", "코스피", "KOSPI", "pt"),
    ("kosdaq", "코스닥", "KOSDAQ", "pt"),
    ("kr10y", "한국 10년물 금리", "KR_BOND_10Y", "%"),
)
"""토스 시장지표 카탈로그에서 받는 것들."""

KEYS: tuple[str, ...] = (
    *(s.key for s in YAHOO_SPECS),
    "usdkrw",
    "effr",
    "cpi",
    *(k for k, _l, _s, _u in TOSS_INDICATORS),
)
"""지표 열쇠 전부 — 채팅 도구의 `keys` 인자가 고를 수 있는 것."""


class MacroAdapter:
    """지표 목록을 만든다 — 출처마다 따로 시도한다."""

    def __init__(self, client: MacroClient, toss: TossIndicatorSource | None = None) -> None:
        """어댑터를 만든다.

        Args:
            client: 공개 출처 클라이언트.
            toss: 토스 출처(환율·국내 지표). None 이면 환율은 야후로, 국내 지표는 실패로 간다.
        """
        self._client = client
        self._toss = toss

    async def indicators(
        self, keys: tuple[str, ...] | None = None
    ) -> tuple[list[Indicator], list[dict[str, str]]]:
        """지표들과 실패들.

        Args:
            keys: 고를 열쇠들. None 이면 전부.

        Returns:
            `(지표 목록 · KEYS 순서, [{key, label, reason}])`.
        """
        wanted = set(KEYS if keys is None else keys)
        jobs: list[tuple[str, str, Callable[[], Awaitable[Indicator | list[Indicator]]]]] = []
        for spec in YAHOO_SPECS:
            if spec.key in wanted:
                jobs.append((spec.key, spec.label, lambda s=spec: self._yahoo(s)))
        if "usdkrw" in wanted:
            jobs.append(("usdkrw", "원달러 환율", self._usdkrw))
        if "effr" in wanted:
            jobs.append(("effr", "미국 기준금리(EFFR)", self._effr))
        if "cpi" in wanted:
            jobs.append(("cpi", "미국 CPI (전년 대비)", self._cpi))
        toss_keys = [row for row in TOSS_INDICATORS if row[0] in wanted]
        if toss_keys:
            jobs.append(("toss", "국내 지표", lambda: self._toss_batch(toss_keys)))
        results = await asyncio.gather(*(job() for _k, _l, job in jobs), return_exceptions=True)
        found: dict[str, Indicator] = {}
        failures: list[dict[str, str]] = []
        for (key, label, _job), result in zip(jobs, results, strict=True):
            if isinstance(result, BaseException):
                if key == "toss":
                    for k, lbl, _s, _u in toss_keys:
                        failures.append({"key": k, "label": lbl, "reason": str(result)[:160]})
                else:
                    failures.append({"key": key, "label": label, "reason": str(result)[:160]})
                _logger.info(
                    "macro_source_failed", payload={"key": key, "detail": str(result)[:160]}
                )
            elif isinstance(result, list):
                for item in result:
                    found[item.key] = item
            else:
                found[key] = result
        # 토스 묶음에서 빠진 것(카탈로그 응답 누락)도 실패로 적는다.
        for k, lbl, _s, _u in toss_keys:
            if k not in found and not any(f["key"] == k for f in failures):
                failures.append({"key": k, "label": lbl, "reason": "응답에 없음"})
        ordered = [found[k] for k in KEYS if k in found]
        return ordered, failures

    async def _yahoo(self, spec: YahooSpec) -> Indicator:
        try:
            price, prev, as_of = parse_yahoo_chart(await self._client.yahoo_chart(spec.symbol))
        except Exception:
            if spec.key != "vix":
                raise
            # ⭐ VIX 만은 폴백이 있다 — CBOE 일별 CSV(장 마감 값).
            close, prev_close, day = parse_cboe_vix_csv(await self._client.cboe_vix_csv())
            month, date, year = day.split("/")
            as_of = datetime(int(year), int(month), int(date), tzinfo=UTC)
            return self._vix(close, prev_close, as_of, "CBOE (일별 CSV · 장 마감)")
        value = price * spec.scale
        prev_scaled = None if prev is None else prev * spec.scale
        if spec.key == "vix":
            return self._vix(value, prev_scaled, as_of, "Yahoo Finance")
        return Indicator(
            key=spec.key,
            label=spec.label,
            value=value,
            unit=spec.unit,
            source="Yahoo Finance",
            as_of=as_of,
            change_pct=pct_change(value, prev_scaled),
            note=spec.note,
        )

    @staticmethod
    def _vix(
        value: Decimal, prev: Decimal | None, as_of: datetime | None, source: str
    ) -> Indicator:
        band, tone = vix_band(value)
        return Indicator(
            key="vix",
            label="VIX 공포지수",
            value=value,
            unit="pt",
            source=source,
            as_of=as_of,
            change_pct=pct_change(value, prev),
            band=band,
            tone=tone,
            note="20 미만 안정 · 20~29 약한 공포 · 30 이상 강한 공포",
        )

    async def _usdkrw(self) -> Indicator:
        if self._toss is None:
            price, prev, as_of = parse_yahoo_chart(await self._client.yahoo_chart("KRW=X"))
            return Indicator(
                key="usdkrw",
                label="원달러 환율",
                value=price,
                unit="KRW",
                source="Yahoo Finance",
                as_of=as_of,
                change_pct=pct_change(price, prev),
                note="1달러당 원 · 토스 자격증명이 없어 야후",
            )
        body = await self._toss.exchange_rate("USD", "KRW")
        mid = Decimal(str(body.get("midRate") or body.get("rate")))
        stamp = body.get("validFrom")
        as_of = datetime.fromisoformat(str(stamp)) if isinstance(stamp, str) and stamp else None
        return Indicator(
            key="usdkrw",
            label="원달러 환율",
            value=mid,
            unit="KRW",
            source="토스증권 (매매기준율)",
            as_of=as_of,
            note=f"매수 환율 {body.get('rate')} · 1분 갱신",
        )

    async def _effr(self) -> Indicator:
        rate, low, high, day = parse_effr(await self._client.nyfed_effr())
        as_of = datetime.fromisoformat(day).replace(tzinfo=UTC) if day else None
        target = f"목표범위 {low}~{high}%" if low is not None and high is not None else ""
        return Indicator(
            key="effr",
            label="미국 기준금리(EFFR)",
            value=rate,
            unit="%",
            source="뉴욕연준",
            as_of=as_of,
            note=f"실효연방기금금리 · {target}".rstrip(" ·"),
        )

    async def _cpi(self) -> Indicator:
        points = parse_bls_series(await self._client.bls_series())
        period, index, yoy = cpi_yoy(points)
        year, month = period.split("-")
        as_of = datetime(int(year), int(month), 1, tzinfo=UTC)
        if yoy is None:
            raise ValueError(f"CPI {period} 의 전년 동월이 없다")
        return Indicator(
            key="cpi",
            label="미국 CPI (전년 대비)",
            value=yoy,
            unit="%",
            source="BLS (CPI-U · 비계절조정)",
            as_of=as_of,
            note=f"{period} 지수 {index}",
        )

    async def _toss_batch(self, rows: list[tuple[str, str, str, str]]) -> list[Indicator]:
        if self._toss is None:
            raise RuntimeError("토스 자격증명이 없다")
        symbols = [s for _k, _l, s, _u in rows]
        got = {str(r.get("symbol")): r for r in await self._toss.indicator_prices(symbols)}
        out: list[Indicator] = []
        for key, label, symbol, unit in rows:
            row: Mapping[str, Any] | None = got.get(symbol)
            if row is None or row.get("lastPrice") in (None, ""):
                continue
            stamp = row.get("timestamp")
            as_of = datetime.fromisoformat(str(stamp)) if isinstance(stamp, str) and stamp else None
            out.append(
                Indicator(
                    key=key,
                    label=label,
                    value=Decimal(str(row["lastPrice"])),
                    unit=unit,
                    source="토스증권 (시장지표)",
                    as_of=as_of,
                )
            )
        return out


__all__ = ["KEYS", "TOSS_INDICATORS", "YAHOO_SPECS", "MacroAdapter", "TossIndicatorSource"]
