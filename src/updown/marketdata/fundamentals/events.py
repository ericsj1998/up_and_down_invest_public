"""공시를 사건으로 읽는다 — 8-K 항목 번호가 곧 분류다 (T277 1단계 · 2026-09-12).

## 왜 LLM 이 먼저가 아닌가

뉴스 감성 점수는 사전 기반이라 금융 문맥에 약하고(*"실적 부진 우려를 딛고 흑자 전환"* 을 음수로
잡는다), 이미 시장에 반영된 소식은 논조가 좋아도 가격이 안 움직인다. 그래서 **이벤트 유형**이 낫다 —
그런데 미국 공시는 **SEC 가 정한 항목 번호로 이미 분류돼** 있다.

2026-09-12 실호출(AAPL `submissions`)로 확인했다.

    2026-07-30  8-K  items='2.02,9.01'   실적 발표
    2026-04-20  8-K  items='5.02'        임원 변경
    2026-02-24  8-K  items='5.07,9.01'   주주총회 표결

한 번 호출에 최근 1,000건이 오고 `items`·`form`·`filingDate`·`primaryDocument` 가 같이 온다.
그래서 **분류는 공짜**이고, LLM 은 그 위에서 규모(계약금액이 시총의 몇 %인가) 같은 것만 맡으면 된다.

## 여기서 하지 않는 것

⛔ **방향을 말하지 않는다.** 이 모듈은 "무슨 일이 있었나" 까지다. 호재냐 악재냐, 오를까 내릴까는
   여기서 정하지 않는다 — 그것은 사후 수익률이 채점할 몫이다(규칙 #2 · #11).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"
"""공시 원문이 사는 곳 — `edgar.py` 의 `filing_url` 과 같은 뿌리(폴더까지). 여기는 대표 문서까지
간다."""

ITEM_LABELS: dict[str, str] = {
    "1.01": "중요 계약 체결",
    "1.02": "중요 계약 종료",
    "1.03": "파산·법정관리",
    "2.01": "자산 취득·처분 완료",
    "2.02": "실적 발표",
    "2.03": "채무 발생",
    "2.04": "채무 조기상환 사유",
    "2.05": "구조조정 비용 확정",
    "2.06": "자산 손상",
    "3.01": "상장 규정 위반·상장폐지 통지",
    "3.02": "미등록 주식 발행",
    "3.03": "주주 권리 변경",
    "4.01": "회계법인 교체",
    "4.02": "과거 재무제표 신뢰 불가",
    "5.01": "경영권 변동",
    "5.02": "임원 변경",
    "5.03": "정관 변경·회계연도 변경",
    "5.07": "주주총회 표결 결과",
    "7.01": "공정공시(Reg FD)",
    "8.01": "기타 중요 사항",
    "9.01": "재무제표·첨부",
}
"""8-K 항목 번호 → 사람이 읽는 이름 (SEC 서식 8-K 기준).

⚠️ `9.01` 은 첨부 목록이라 거의 모든 8-K 에 붙는다 — 단독으로는 뜻이 없어 `headline` 이 뒤로 민다."""

NOISE_ITEMS = frozenset({"9.01"})
"""단독으로는 정보가 없는 항목 — 제목을 고를 때 마지막으로 민다."""

FORM_LABELS: dict[str, str] = {
    "8-K": "수시 공시",
    "10-K": "연간 보고서",
    "10-Q": "분기 보고서",
    "4": "내부자 거래",
    "3": "내부자 최초 신고",
    "5": "내부자 연간 신고",
    "144": "내부자 매도 예정",
    "S-1": "증권 신고",
    "424B2": "증권 발행 조건",
    "25": "상장 폐지 신청",
}
"""서식 → 사람이 읽는 이름. 8-K 가 아니어도 사건이 되는 것들이 있다(Form 4 내부자 거래)."""


@dataclass(frozen=True, slots=True)
class FilingEvent:
    """공시 하나를 사건으로 읽은 것.

    Attributes:
        form: 서식 (`8-K` · `4` …).
        form_label: 서식의 한국어 이름.
        items: 8-K 항목 번호들 (다른 서식이면 빈 튜플).
        labels: 항목 번호의 한국어 이름들 — 번호 순서 그대로.
        headline: 대표 이름 한 줄. 8-K 면 첫 **뜻 있는** 항목, 아니면 서식 이름.
    """

    form: str
    form_label: str
    items: tuple[str, ...]
    labels: tuple[str, ...]
    headline: str


def parse_items(raw: str) -> tuple[str, ...]:
    """`items` 칸을 번호 튜플로.

    Args:
        raw: `"2.02,9.01"` 같은 문자열. 빈 값이면 빈 튜플.

    Returns:
        번호들. 공백을 털고 빈 조각은 버린다. 순서는 받은 대로(SEC 가 번호순으로 준다).
    """
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def read_filing(form: str, items_raw: str = "") -> FilingEvent:
    """서식과 항목 번호를 사람이 읽는 사건으로 (순수).

    Args:
        form: 서식 이름.
        items_raw: `items` 칸 원문. 8-K 가 아니면 보통 빈 값이다.

    Returns:
        사건. **모르는 번호는 버리지 않고 번호 그대로** 이름에 남긴다 — 조용히 사라지면
        새 항목이 생겼을 때 눈치채지 못한다 (규칙 #8).

    Note:
        `headline` 은 `9.01`(첨부 목록)처럼 단독으로 뜻이 없는 항목을 뒤로 민다. 실적 발표가
        `2.02,9.01` 로 오는데 첨부가 제목이 되면 무슨 일인지 안 보인다.
    """
    items = parse_items(items_raw)
    labels = tuple(ITEM_LABELS.get(item, item) for item in items)
    form_label = FORM_LABELS.get(form, form)
    meaningful = [item for item in items if item not in NOISE_ITEMS]
    if meaningful:
        headline = ITEM_LABELS.get(meaningful[0], meaningful[0])
    elif items:
        headline = ITEM_LABELS.get(items[0], items[0])
    else:
        headline = form_label
    return FilingEvent(
        form=form, form_label=form_label, items=items, labels=labels, headline=headline
    )


def is_material(event: FilingEvent) -> bool:
    """사람에게 보여 줄 만한 사건인가 (순수).

    Args:
        event: 읽은 사건.

    Returns:
        참이면 목록에 올린다. 첨부 목록만 있는 8-K 처럼 **내용이 없는 것**은 거짓.

    Note:
        판단이 아니라 **잡음 거르기**다. 호재·악재를 가르는 것이 아니다.
    """
    if event.form != "8-K":
        return event.form in FORM_LABELS
    return any(item not in NOISE_ITEMS for item in event.items)


@dataclass(frozen=True, slots=True)
class RecentFiling:
    """`submissions` 의 공시 한 건 — 사건으로 읽은 것 + 원문 링크.

    Attributes:
        filed_at: 공시일.
        accession: 접수 번호.
        event: 사건(서식·항목 이름).
        url: 원문 링크 — 대표 문서가 있으면 그 문서, 없으면 색인 폴더. **항상 있다**(링크 없는
            항목은 애초에 만들지 않는다 · T277 규칙).
        description: SEC 가 붙인 대표 문서 설명(`primaryDocDescription`). 없으면 빈 값.
        material: 사람에게 보여 줄 만한가 (`is_material`).
    """

    filed_at: date
    accession: str
    event: FilingEvent
    url: str
    description: str = ""
    material: bool = True

    def as_json(self) -> dict[str, Any]:
        """화면 모양 — 방향 칸은 없다.

        Returns:
            `{filed_at, accession, form, form_label, items, labels, headline, url, description,
            material}`.
        """
        return {
            "filed_at": self.filed_at.isoformat(),
            "accession": self.accession,
            "form": self.event.form,
            "form_label": self.event.form_label,
            "items": list(self.event.items),
            "labels": list(self.event.labels),
            "headline": self.event.headline,
            "url": self.url,
            "description": self.description,
            "material": self.material,
        }


def document_url(cik: str, accession: str, primary_document: str = "") -> str:
    """공시 원문 링크 — 대표 문서가 있으면 그 문서, 없으면 색인 폴더.

    Args:
        cik: CIK (앞 0 은 경로에서 빠진다).
        accession: 접수 번호 (`0000320193-26-000068` · 경로에서는 대시가 빠진다).
        primary_document: `primaryDocument` (`aapl-20260730.htm`). 빈 값이면 폴더까지만.

    Returns:
        `https://www.sec.gov/Archives/edgar/data/<cik>/<접수번호>/<문서>`.
    """
    folder = f"{ARCHIVE_URL}/{cik.lstrip('0') or '0'}/{accession.replace('-', '')}/"
    return folder + primary_document if primary_document else folder


def parse_submissions(body: Mapping[str, Any], *, limit: int = 1000) -> list[RecentFiling]:
    """EDGAR `submissions` 응답(열 단위 배열)을 공시 목록으로 (순수).

    Args:
        body: 응답 JSON. `cik` 와 `filings.recent.{form, filingDate, items, accessionNumber,
            primaryDocument, primaryDocDescription}` 을 읽는다.
        limit: 앞에서 몇 건(응답은 최신순이다).

    Returns:
        최신순. 접수 번호나 날짜가 깨진 행은 버린다 — 링크를 못 만들면 항목이 아니다.

    Note:
        SEC 는 배열 길이가 서로 같다고 약속하지만 믿지 않는다 — 짧은 배열은 빈 값으로 읽는다.
    """
    cik = str(body.get("cik") or "")
    filings = cast("Mapping[str, Any]", body.get("filings") or {})
    recent = cast("Mapping[str, Any]", filings.get("recent") or {})

    def column(name: str) -> list[Any]:
        """열 하나 — 배열이 아니면 빈 열."""
        got = recent.get(name)
        return cast("list[Any]", got) if isinstance(got, list) else []

    forms = column("form")
    dates = column("filingDate")
    items = column("items")
    accessions = column("accessionNumber")
    documents = column("primaryDocument")
    descriptions = column("primaryDocDescription")

    def at(rows: list[Any], index: int) -> str:
        """열의 i 번째 — 짧은 열이면 빈 값."""
        return str(rows[index]) if index < len(rows) and rows[index] is not None else ""

    out: list[RecentFiling] = []
    for index in range(min(len(forms), limit)):
        accession = at(accessions, index)
        if not accession or not cik:
            continue
        try:
            filed_at = date.fromisoformat(at(dates, index))
        except ValueError:
            continue
        event = read_filing(at(forms, index), at(items, index))
        out.append(
            RecentFiling(
                filed_at=filed_at,
                accession=accession,
                event=event,
                url=document_url(cik, accession, at(documents, index)),
                description=at(descriptions, index),
                material=is_material(event),
            )
        )
    return out


__all__ = [
    "FORM_LABELS",
    "ITEM_LABELS",
    "NOISE_ITEMS",
    "FilingEvent",
    "RecentFiling",
    "document_url",
    "is_material",
    "parse_items",
    "parse_submissions",
    "read_filing",
]
