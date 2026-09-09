"""`EdgarAdapter` — 티커 → CIK → companyfacts → 사실 (T243).

CIK 표(`company_tickers.json`)는 수천 행짜리 한 파일이라 **한 번 받아 시한 캐시**한다 — 종목마다
다시 받으면
그 파일이 요청의 대부분이 된다(`speccache` 의 교훈). 실패는 캐시하지 않는다.
"""

from __future__ import annotations

import time

from updown.common.domain.fundamentals import Filing, FinancialFact, FundamentalsConfig
from updown.marketdata.fundamentals.adapter import UnknownEntityError
from updown.marketdata.fundamentals.client import EdgarClient
from updown.marketdata.fundamentals.mapping import SOURCE, parse_company_facts

TICKERS_TTL_S = 24 * 3600.0
"""CIK 표를 들고 있는 시간. 상장·티커 변경은 드물고, 하루 뒤 따라오면 충분하다."""

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"


def filing_url(entity_id: str, accession: str) -> str:
    """공시 원문(색인) 링크.

    Args:
        entity_id: CIK. 앞 0 은 경로에서 빠진다.
        accession: 접수 번호 (`0000320193-20-000096`). 경로에서는 대시가 빠진다.

    Returns:
        `https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/`.
    """
    cik = entity_id.lstrip("0") or "0"
    return f"{ARCHIVE_URL}/{cik}/{accession.replace('-', '')}/"


class EdgarAdapter:
    """SEC EDGAR 재무 출처 — `FundamentalsAdapter` 구현."""

    source = SOURCE

    def __init__(
        self, client: EdgarClient, config: FundamentalsConfig, *, tickers_ttl: float = TICKERS_TTL_S
    ) -> None:
        """어댑터를 만든다.

        Args:
            client: EDGAR 클라이언트.
            config: 개념 매핑.
            tickers_ttl: CIK 표 캐시 시간(초).
        """
        self._client = client
        self._config = config
        self._tickers_ttl = tickers_ttl
        self._tickers: dict[str, str] | None = None
        self._tickers_at = 0.0

    async def aclose(self) -> None:
        """HTTP 연결을 닫는다."""
        await self._client.aclose()

    async def cik_of(self, symbol: str) -> str:
        """티커의 CIK.

        Args:
            symbol: 티커.

        Returns:
            10자리 CIK.

        Raises:
            UnknownEntityError: 표에 없다.
        """
        now = time.monotonic()
        if self._tickers is None or now - self._tickers_at > self._tickers_ttl:
            self._tickers = await self._client.company_tickers()
            self._tickers_at = now
        cik = self._tickers.get(symbol.upper())
        if cik is None:
            raise UnknownEntityError(f"EDGAR 티커 표에 {symbol} 이 없다")
        return cik

    async def facts(self, symbol: str) -> list[FinancialFact]:
        """종목의 재무 사실 전부 (설정에 매핑된 개념만).

        Args:
            symbol: 티커.

        Returns:
            공시일 오름차순.
        """
        cik = await self.cik_of(symbol)
        body = await self._client.company_facts(cik)
        return parse_company_facts(body, symbol=symbol.upper(), config=self._config)

    async def filings(self, symbol: str) -> list[Filing]:
        """사실이 실린 공시 목록.

        Args:
            symbol: 티커.

        Returns:
            공시일 오름차순 · 접수 번호 중복 없음.
        """
        return filings_of(await self.facts(symbol))


def filings_of(facts: list[FinancialFact]) -> list[Filing]:
    """사실들에서 공시 목록을 뽑는다 (접수 번호 하나에 한 건).

    Args:
        facts: 사실.

    Returns:
        공시일 오름차순.
    """
    seen: dict[str, Filing] = {}
    for fact in facts:
        if fact.accession in seen:
            continue
        url = filing_url(fact.entity_id, fact.accession) if fact.source == SOURCE else None
        seen[fact.accession] = Filing(
            accession=fact.accession, form=fact.form, filed_at=fact.filed_at, url=url
        )
    return sorted(seen.values(), key=lambda f: (f.filed_at, f.accession))


__all__ = ["ARCHIVE_URL", "TICKERS_TTL_S", "EdgarAdapter", "filing_url", "filings_of"]
