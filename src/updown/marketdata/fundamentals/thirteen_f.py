"""13F-HR(기관 보유 보고) — EDGAR 에서 받아 보유 줄로 만든다 (T442 WhaleSurfer · 2026-10-08).

사실(2026-10-08 조사 · 설계의 전제):
- 13F 는 **롱 보유만** 보고한다(분기 말 기준 · 분기 끝 45일 안 접수). **공매도는 보고 항목이
  아니다.** 옵션은 ``putCall`` 열(Put · Call)로만 보인다 — "숏 성향" 은 Put 보유로만 간접 추정된다.
- 2023-01-03 이후 접수분부터 ``value`` 가 달러다(그 전은 천 달러 · SEC 2022 개정). 여기서
  ``value_usd`` 로 맞춘다.
- 종목 식별자는 **CUSIP 뿐**이다(티커 없음). 티커 매핑은 바깥(설정 표 · OpenFIGI)의 일이다.
- 정정 보고(13F-HR/A)는 "전체 다시" 와 "추가만" 두 종류가 있어 v0 에서는 13F-HR 만 읽는다.

이 모듈은 I/O(EDGAR 요청)와 모양 바꾸기만 한다 — 누가 많이 샀는지 같은 셈은 상위 계층의 일이다.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, cast

from updown.marketdata.fundamentals.adapter import FundamentalsError

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS_URL = "https://data.sec.gov/submissions"
DOLLARS_SINCE = date(2023, 1, 3)
"""이 날 이후 접수분의 ``value`` 는 달러, 그 전은 천 달러."""
FORM = "13F-HR"
THOUSAND = 1000


class ThirteenFError(FundamentalsError):
    """13F 보고를 못 읽었다(정보표 없음 · XML 깨짐)."""


class EdgarLike(Protocol):
    """이 모듈이 EDGAR 클라이언트에 바라는 것 — 시험에서 가짜로 바꿔 끼운다."""

    async def submissions(self, cik: str) -> dict[str, Any]:
        """``data.sec.gov/submissions/CIK….json``."""
        ...

    async def get_json(self, url: str) -> object:
        """``data.sec.gov`` 의 JSON(옛 접수 묶음)."""
        ...

    async def get_text(self, url: str) -> str:
        """``www.sec.gov/Archives/…`` 문서 본문(접수 폴더 목록 JSON · 정보표 XML)."""
        ...


@dataclass(frozen=True, slots=True)
class Holding13F:
    """정보표 한 줄."""

    issuer: str
    title: str
    cusip: str
    value_usd: int
    shares: int
    sh_prn: str
    """SH(주) · PRN(원금)."""
    put_call: str | None
    """"Put" · "Call" · None(현물)."""
    discretion: str


@dataclass(frozen=True, slots=True)
class Report13F:
    """13F-HR 한 건."""

    cik: str
    entity: str
    form: str
    accession: str
    filed: date
    period: date | None
    holdings: tuple[Holding13F, ...]

    @property
    def total_value_usd(self) -> int:
        """보유 가치 합(달러)."""
        return sum(h.value_usd for h in self.holdings)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _txt(fields: Mapping[str, ET.Element], name: str) -> str:
    node = fields.get(name)
    return (node.text or "").strip() if node is not None else ""


def parse_infotable(text: str, *, dollars: bool = True) -> list[Holding13F]:
    """정보표 XML → 보유 줄.

    Args:
        text: ``infotable.xml`` 본문. 네임스페이스는 무시한다(접수마다 접두가 다르다).
        dollars: ``value`` 가 달러면 True, 천 달러(2023 전)면 False.

    Returns:
        파일 순서 그대로의 보유 줄. ``infoTable`` 이 하나도 없으면 빈 목록.

    Raises:
        ThirteenFError: XML 이 아니다.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ThirteenFError(f"정보표 XML 이 깨졌다: {exc}") from exc
    out: list[Holding13F] = []
    for node in root.iter():
        if _local(node.tag) != "infoTable":
            continue
        fields: dict[str, ET.Element] = {_local(child.tag): child for child in node}
        value = int(float(_txt(fields, "value") or 0))
        shares, sh_prn = 0, "SH"
        amount = fields.get("shrsOrPrnAmt")
        if amount is not None:
            sub: dict[str, ET.Element] = {_local(c.tag): c for c in amount}
            shares = int(float(_txt(sub, "sshPrnamt") or 0))
            sh_prn = _txt(sub, "sshPrnamtType") or "SH"
        out.append(
            Holding13F(
                issuer=_txt(fields, "nameOfIssuer"),
                title=_txt(fields, "titleOfClass"),
                cusip=_txt(fields, "cusip").upper(),
                value_usd=value if dollars else value * THOUSAND,
                shares=shares,
                sh_prn=sh_prn,
                put_call=_txt(fields, "putCall") or None,
                discretion=_txt(fields, "investmentDiscretion"),
            )
        )
    return out


def pick_reports(submissions: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    """Submissions 에서 최근 13F-HR 접수를 새것부터 고른다.

    Args:
        submissions: ``data.sec.gov/submissions/CIK….json``.
        limit: 몇 건.

    Returns:
        ``[{accession, filed(date), period(date|None), form}, …]`` 새것부터. 없으면 빈 목록.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    filed = recent.get("filingDate", [])
    periods = recent.get("reportDate", [])
    out: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if form != FORM:
            continue
        out.append(
            {
                "accession": accs[i],
                "filed": date.fromisoformat(filed[i]),
                "period": date.fromisoformat(periods[i])
                if i < len(periods) and periods[i]
                else None,
                "form": form,
            }
        )
        if len(out) >= limit:
            break
    return out


def infotable_name(index: Mapping[str, Any]) -> str | None:
    """접수 폴더 ``index.json`` 에서 정보표 파일 이름을 고른다.

    Args:
        index: ``{"directory": {"item": [{"name": …}, …]}}``.

    Returns:
        이름에 infotable/informationtable 이 든 XML, 없으면 ``primary_doc.xml`` 이 아닌 첫 XML,
        그것도 없으면 None.
    """
    directory = cast("Mapping[str, Any]", index.get("directory") or {})
    items = cast("list[Mapping[str, Any]]", directory.get("item") or [])
    names = [str(it.get("name") or "") for it in items]
    xmls = [n for n in names if n.lower().endswith(".xml") and n.lower() != "primary_doc.xml"]
    for name in xmls:
        low = name.lower()
        if "infotable" in low or "informationtable" in low:
            return name
    return xmls[0] if xmls else None


def folder_url(cik: str, accession: str) -> str:
    """접수 폴더 URL — CIK 앞 0 과 접수 번호의 대시가 경로에서 빠진다."""
    return f"{ARCHIVE_URL}/{cik.lstrip('0') or '0'}/{accession.replace('-', '')}"


async def all_filings(client: EdgarLike, subs: Mapping[str, Any]) -> dict[str, Any]:
    """Submissions 의 `recent`(최근 1,000건)에 옛 묶음(`filings.files[*].name`)을 이어 붙인 한 표.

    큰 보고자(13D · Form 4 를 많이 내는 곳)는 13년치 13F-HR 이 `recent` 밖으로 밀려난다 — 역사
    전체를 읽을 때(T443 백테스트)만 쓴다. 묶음 파일은 `data.sec.gov/submissions/<name>` 에 있다.
    """
    filings = cast("Mapping[str, Any]", subs.get("filings") or {})
    recent = cast("Mapping[str, Any]", filings.get("recent") or {})
    merged: dict[str, list[Any]] = {
        k: list(cast("list[Any]", recent.get(k) or []))
        for k in ("form", "accessionNumber", "filingDate", "reportDate")
    }
    for chunk in cast("list[Mapping[str, Any]]", filings.get("files") or []):
        name = str(chunk.get("name") or "")
        if not name:
            continue
        body = cast("Mapping[str, Any]", await client.get_json(f"{SUBMISSIONS_URL}/{name}"))
        for k in merged:
            merged[k] += list(cast("list[Any]", body.get(k) or []))
    return {"name": subs.get("name"), "filings": {"recent": merged}}


async def recent_reports(
    client: EdgarLike, cik: str, limit: int = 1, *, deep: bool = False, strict: bool = True
) -> list[Report13F]:
    """최근 13F-HR 을 받아 보유 줄까지 만든다 — 접수 한 건에 EDGAR 요청 둘(index.json · 정보표).

    Args:
        client: EDGAR 클라이언트(연락처 헤더 · 초당 10 요청 스로틀은 거기 있다).
        cik: 보고자 CIK(앞 0 있어도 된다).
        limit: 최근 몇 분기.
        deep: True 면 옛 접수 묶음까지 읽어 `recent` 밖의 13F 도 본다(`all_filings`).
        strict: False 면 정보표 XML 이 없는 접수(2013-06 전의 텍스트 13F)는 건너뛴다. True 면 예외.

    Returns:
        새것부터. 13F-HR 이 없는 CIK 면 빈 목록.

    Raises:
        ThirteenFError: 정보표 파일을 못 찾았거나(strict) XML 이 깨졌다.
    """
    subs = await client.submissions(cik)
    if deep:
        subs = await all_filings(client, subs)
    entity = str(subs.get("name") or cik)
    out: list[Report13F] = []
    for meta in pick_reports(subs, limit):
        folder = folder_url(cik, meta["accession"])
        # 접수 폴더 목록도 www.sec.gov(문서 저장소)라 정보표와 같은 길(`get_text`)로 받는다.
        try:
            index = json.loads(await client.get_text(f"{folder}/index.json"))
        except json.JSONDecodeError as exc:
            raise ThirteenFError(f"접수 폴더 목록이 JSON 이 아니다: {meta['accession']}") from exc
        name = (
            infotable_name(cast("Mapping[str, Any]", index)) if isinstance(index, Mapping) else None
        )
        if name is None:
            if not strict:
                continue  # 2013-06 전 텍스트 13F — XML 정보표가 없다
            raise ThirteenFError(f"정보표 XML 을 못 찾았다: {meta['accession']}")
        text = await client.get_text(f"{folder}/{name}")
        out.append(
            Report13F(
                cik=cik,
                entity=entity,
                form=meta["form"],
                accession=meta["accession"],
                filed=meta["filed"],
                period=meta["period"],
                holdings=tuple(parse_infotable(text, dollars=meta["filed"] >= DOLLARS_SINCE)),
            )
        )
    return out


__all__ = [
    "ARCHIVE_URL",
    "DOLLARS_SINCE",
    "FORM",
    "EdgarLike",
    "Holding13F",
    "Report13F",
    "ThirteenFError",
    "all_filings",
    "folder_url",
    "infotable_name",
    "parse_infotable",
    "pick_reports",
    "recent_reports",
]
