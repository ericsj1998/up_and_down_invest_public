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

from updown.common.cache import TtlCache
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

EFFR_TTL_S = 3600.0
"""뉴욕연준 EFFR 은 하루 한 값 — 한 시간 기억."""
CPI_TTL_S = 12 * 3600.0
"""BLS CPI 는 월 한 값 · 공개 API 하루 상한 — 12시간 기억."""


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
        # ⭐ 일·월 단위 출처는 오래 기억한다 (2026-09-11 실측: BLS 공개 API 는 하루 요청 상한이 있어
        #    /macro 폴링(60초 캐시)이 반나절 만에 "daily threshold reached" 로 CPI 를 잃었다).
        self._slow = TtlCache[Indicator]("macro.slow", EFFR_TTL_S, register=False)

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
        """야후 차트로 지표 하나 — 비공식 API 라 언제든 막힐 수 있다.

        Args:
            spec: 야후 심볼·단위·배율.

        Returns:
            현재가와 전일 대비. VIX 는 `_vix` 로 띠·색을 붙인다.

        Raises:
            Exception: 야후 호출·파싱 실패. VIX **만** CBOE 일별 CSV(장 마감 값)로 폴백하고,
                나머지는 그대로 올려 `indicators` 가 실패 목록에 이유와 함께 넣는다 — 다른 출처로
                대신하면 단위·시점이 달라 같은 지표가 아니다.
        """
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
        """VIX 지표 — 값에 공포 띠(`vix_band`)와 색을 붙인다. 야후·CBOE 가 같은 모양으로 온다.

        Args:
            value: 지수 값.
            prev: 전일 값. 없으면 변화율도 없다.
            as_of: 시점.
            source: 표시할 출처 이름 — 폴백이면 그 사실이 화면에 보여야 한다.

        Returns:
            띠·색·설명이 붙은 지표.
        """
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
        """원달러 환율 — 토스 매매기준율이 먼저, 토스 자격증명이 없으면 야후 `KRW=X`.

        Returns:
            지표. 토스는 `midRate`(없으면 `rate`)를 값으로, 매수 환율을 설명에 적는다 — 국내 계좌가
            실제로 쓰는 환율이라 야후 중간값보다 우리 손익에 가깝다. 야후 경로는 설명에 그 이유를
            남긴다.

        Raises:
            Exception: 출처 호출·파싱 실패 — `indicators` 가 실패 목록으로 넘긴다.
        """
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
        """EFFR — 한 시간 기억 (`EFFR_TTL_S`). 실제 조회는 `_effr_fetch`."""
        return await self._slow.get_or_fetch("effr", self._effr_fetch, ttl_s=EFFR_TTL_S)

    async def _effr_fetch(self) -> Indicator:
        """뉴욕연준 EFFR 마지막 1건 — 하루 한 값이라 캐시 뒤에서만 불린다.

        Returns:
            실효연방기금금리와 목표범위(있으면 설명에).

        Raises:
            Exception: 출처 호출·파싱 실패 — 캐시에 남지 않고 `indicators` 가 실패 목록으로 넘긴다.
        """
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
        """CPI — 12시간 기억 (`CPI_TTL_S`). 실제 조회는 `_cpi_fetch`."""
        return await self._slow.get_or_fetch("cpi", self._cpi_fetch, ttl_s=CPI_TTL_S)

    async def _cpi_fetch(self) -> Indicator:
        """BLS CPI-U 시계열에서 전년 동월 대비 — 공개 v1 API 는 하루 상한이 있어 캐시 뒤에서만.

        Returns:
            최신 달의 전년 대비(%)와, 설명에 지수·전달 값. 전달 값을 같이 두는 이유는 발표 당일
            "이전 → 실제" 를 보여 주기 위해서다 (T276) — 방향 판단이 아니라 사실이다 (규칙 #2).

        Raises:
            ValueError: 최신 달의 전년 동월이 시계열에 없다 (3년치 응답이라 정상이면 있다).
            Exception: 출처 호출·파싱 실패 — `indicators` 가 실패 목록으로 넘긴다.
        """
        points = parse_bls_series(await self._client.bls_series())
        period, index, yoy = cpi_yoy(points)
        year, month = period.split("-")
        as_of = datetime(int(year), int(month), 1, tzinfo=UTC)
        if yoy is None:
            raise ValueError(f"CPI {period} 의 전년 동월이 없다")
        # ⭐ 전달 값도 같이 — 발표 당일 "이전 → 실제" 를 보여 주려면 둘이 필요하다 (T276 2단계 #3).
        #    방향이 아니라 사실이다(규칙 #2).
        earlier = [p for p in points if p[0] != period]
        prev_note = ""
        if earlier:
            prev_period, _prev_index, prev_yoy = cpi_yoy(earlier)
            if prev_yoy is not None:
                prev_note = f" · 전달({prev_period}) {prev_yoy.quantize(Decimal('0.01'))}%"
        return Indicator(
            key="cpi",
            label="미국 CPI (전년 대비)",
            value=yoy,
            unit="%",
            source="BLS (CPI-U · 비계절조정)",
            as_of=as_of,
            note=f"{period} 지수 {index}{prev_note}",
        )

    def forget(self, key: str) -> None:
        """느린 출처(CPI·EFFR)의 기억을 지운다 — 발표 시각을 지났는데 값이 낡았을 때 한 번 (T276).

        Args:
            key: 지표 열쇠(`cpi` · `effr`).

        Note:
            BLS 는 하루 요청 상한이 있다(2026-09-11 실측). 부르는 쪽이 "발표 시각 뒤 · 값이 낡음 ·
            10분에 한 번" 을 지켜야 한다 — 여기서는 지우기만 한다.
        """
        self._slow.forget(key)

    async def _toss_batch(self, rows: list[tuple[str, str, str, str]]) -> list[Indicator]:
        """토스 시장지표(코스피·코스닥·국채)를 **한 번의 호출**로 — 지표마다 부르면 요율만 쓴다.

        Args:
            rows: `TOSS_INDICATORS` 중 요청된 것들 (열쇠 · 이름 · 토스 심볼 · 단위).

        Returns:
            응답에 있고 `lastPrice` 가 빈 값이 아닌 것만. 빠진 것은 여기서 실패로 만들지 않는다 —
            `indicators` 가 "응답에 없음" 으로 적는다 (묶음 하나가 지표 여럿이라 실패도 나눠
            적는다).

        Raises:
            RuntimeError: 토스 자격증명이 없다 — 묶음 전체가 실패 목록으로 간다.
        """
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
