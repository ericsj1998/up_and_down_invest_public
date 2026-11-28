"""바이낸스 공지 목록 읽기 — 웹 공개 경로에서 제목 · 올린 시각만 받는다 (T1045).

키 없이 공개 웹 경로(`/bapi/composite/v1/public/cms/article/list/query`)를 쓴다. 문서에 없는 경로라
바뀌거나 막힐 수 있다 — 그래서 꼴이 어긋나면 조용히 빈 목록을 내지 않고 예외를 던진다(부르는 쪽이
그 갈래를 "모름" 으로 두고 경고 · 알림 · 규칙 #8). 주문 경로가 아니다.

개인 정보를 싣지 않는다(일반 이름의 User-Agent).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from updown.common.http.outbound import Outbound

CMS_BASE = "https://www.binance.com"
CMS_LIST = "/bapi/composite/v1/public/cms/article/list/query"
USER_AGENT = "updown-client"
TIMEOUT_S = 15.0


class AnnouncementFeedError(RuntimeError):
    """목록 응답의 꼴이 어긋났다 — 경로가 바뀌었거나 막혔을 수 있다."""


@dataclass(frozen=True, slots=True)
class Article:
    """공지 글 하나(목록 줄).

    Attributes:
        id: 글 번호(문자열).
        title: 제목.
        rel: 올린 시각(UTC 밀리초 · 목록의 `releaseDate`).
    """

    id: str
    title: str
    rel: int

    def as_row(self) -> dict[str, Any]:
        """제목 규칙 함수(`analysis.announcements.earliest_hits`)가 읽는 꼴."""
        return {"id": self.id, "title": self.title, "rel": self.rel}


def parse_articles(payload: object) -> tuple[list[Article], int | None]:
    """목록 응답 → 글들 · 전체 수.

    Args:
        payload: 해석된 JSON.

    Returns:
        `(글들, 전체 글 수 | None)`. 글이 없는 쪽이면 빈 목록.

    Raises:
        AnnouncementFeedError: 봉투(`data.catalogs[0].articles`) 꼴이 아니거나 글에 제목 · 시각이
            없다.
    """
    if not isinstance(payload, dict):
        raise AnnouncementFeedError(f"공지 목록 응답이 객체가 아니다: {type(payload).__name__}")
    body = cast("dict[str, Any]", payload)
    data = body.get("data")
    if not isinstance(data, dict):
        raise AnnouncementFeedError(f"공지 목록에 data 가 없다: code={body.get('code')!r}")
    catalogs = cast("dict[str, Any]", data).get("catalogs")
    if not isinstance(catalogs, list) or not catalogs:
        raise AnnouncementFeedError("공지 목록에 catalogs 가 없다")
    first = cast("list[Any]", catalogs)[0]
    if not isinstance(first, dict):
        raise AnnouncementFeedError("공지 목록 catalogs[0] 이 객체가 아니다")
    cat = cast("dict[str, Any]", first)
    raw_total = cat.get("total")
    total = (
        int(raw_total) if isinstance(raw_total, int | str) and str(raw_total).isdigit() else None
    )
    rows: object = cat.get("articles") or []
    if not isinstance(rows, list):
        raise AnnouncementFeedError("공지 목록 articles 가 배열이 아니다")
    out: list[Article] = []
    for raw in cast("list[Any]", rows):
        if not isinstance(raw, dict):
            raise AnnouncementFeedError("공지 글이 객체가 아니다")
        item = cast("dict[str, Any]", raw)
        title, rel = item.get("title"), item.get("releaseDate")
        if not isinstance(title, str) or not isinstance(rel, int | float):
            raise AnnouncementFeedError(f"공지 글에 제목 · 올린 시각이 없다: id={item.get('id')!r}")
        out.append(Article(id=str(item.get("id") or ""), title=title, rel=int(rel)))
    return out, total


class BinanceAnnouncementSource:
    """바이낸스 공지 목록(공개 웹 경로) 읽기."""

    def __init__(self, outbound: Outbound | None = None) -> None:
        """클라이언트를 만든다.

        Args:
            outbound: 시험용 클라이언트. None 이면 공개 주소 위의 기본 클라이언트.
        """
        self._http = outbound or Outbound(
            "BINANCE_CMS",
            timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            base_url=CMS_BASE,
        )
        self.calls = 0

    async def aclose(self) -> None:
        """연결을 닫는다."""
        await self._http.aclose()

    async def articles(
        self, catalog: int, page: int, size: int
    ) -> tuple[list[Article], int | None]:
        """목록 한 쪽.

        Args:
            catalog: 목록 번호.
            page: 쪽 번호(1 부터).
            size: 쪽 크기.

        Returns:
            `(글들, 전체 글 수 | None)` — 새 글이 앞(목록 순서 그대로).

        Raises:
            OutboundError: 전송 실패 · 200 아님(재시도 뒤).
            AnnouncementFeedError: 응답 꼴이 어긋남.
        """
        self.calls += 1
        payload = await self._http.get_json(
            CMS_LIST,
            params={
                "type": "1",
                "catalogId": str(catalog),
                "pageNo": str(page),
                "pageSize": str(size),
            },
        )
        return parse_articles(payload)


__all__ = [
    "AnnouncementFeedError",
    "Article",
    "BinanceAnnouncementSource",
    "parse_articles",
]
