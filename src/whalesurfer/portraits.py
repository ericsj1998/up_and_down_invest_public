"""WhaleSurfer 인물 사진 — 위키백과 요약 API 썸네일 + 위키미디어 저작자 · 라이선스 (T442 D5).

사용자 2026-10-08 "네가 말한 대로". 규칙:
- 설정 표(`managers.yml`)에 `image:` 가 있으면 그것(수동 적재 · 출처는 사람이 안다).
- 없고 `wiki:` 가 ""(빈 문자열)이 아니면 위키백과 `page/summary/{제목}` 의 썸네일을 쓴다.
  제목이 없으면 인물 이름.
- 사진이 없으면 머리글자(화면이 그린다). 라이선스는 파일 메타(`imageinfo.extmetadata`)의
  Artist · LicenseShortName 로 **저작자 표시를 화면에 단다**(위키미디어 공용은 상업 이용 가능하나
  저작자 표시가 필수).
- 결과는 파일 캐시(`cache/whalesurfer/portraits.json`) — 위키백과를 매번 부르지 않는다.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, cast

from updown.common.http.outbound import Outbound, OutboundError
from updown.common.logging.setup import get_logger

_logger = get_logger("api.whalesurfer.images")

SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
META_URL = "https://en.wikipedia.org/w/api.php?action=query&prop=imageinfo&iiprop=extmetadata&format=json&titles={title}"
_TAGS = re.compile(r"<[^>]+>")


def _plain(s: str) -> str:
    return html.unescape(_TAGS.sub("", s)).strip()


def file_title(image_url: str) -> str | None:
    """원본 이미지 URL → `File:이름`.

    썸네일 URL 은 `/thumb/…/이름/000px-이름` 꼴이라 원본 URL 을 쓴다.
    """
    # 요약 API 의 원본 URL 에는 `?campaign=…` 같은 꼬리가 붙는다 — 경로만 본다.
    path = urllib.parse.urlsplit(image_url).path
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
    return f"File:{name}" if name else None


class PortraitSource:
    """위키백과 초상 + 캐시."""

    def __init__(self, cache_path: Path, contact: str, outbound: Outbound | None = None) -> None:
        """만든다.

        Args:
            cache_path: 캐시 JSON(`{제목: {image, credit, page, title}}`).
            contact: 위키미디어 API 가 요구하는 연락처(User-Agent 에 넣는다 · 로그에 안 찍는다).
            outbound: 시험용 HTTP 층.
        """
        self._cache_path = cache_path
        self._http = outbound or Outbound(
            "WIKIPEDIA",
            timeout=20.0,
            headers={
                "User-Agent": f"up_and_down_invest WhaleSurfer ({contact})",
                "Accept": "application/json",
            },
        )
        self._cache: dict[str, dict[str, Any]] = {}
        if cache_path.exists():
            self._cache = cast(
                "dict[str, dict[str, Any]]", json.loads(cache_path.read_text(encoding="utf-8"))
            )

    def _save(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")

    async def portrait(self, title: str) -> dict[str, Any]:
        """제목 → `{image, credit, page, title}` (없으면 image None · 이유는 `note`)."""
        key = title.strip()
        if key in self._cache:
            return self._cache[key]
        result: dict[str, Any] = {
            "image": None,
            "credit": None,
            "page": None,
            "title": key,
            "note": None,
        }
        try:
            summary = cast(
                "dict[str, Any]",
                await self._http.get_json(
                    SUMMARY_URL.format(title=urllib.parse.quote(key.replace(" ", "_")))
                ),
            )
        except (OutboundError, ValueError) as exc:
            result["note"] = f"위키백과 요약 없음: {str(exc)[:60]}"
            self._cache[key] = result
            self._save()
            return result
        result["page"] = (
            cast("dict[str, Any]", summary.get("content_urls") or {}).get("desktop", {}).get("page")
        )
        result["title"] = summary.get("title") or key
        thumb = cast("dict[str, Any]", summary.get("thumbnail") or {}).get("source")
        original = cast("dict[str, Any]", summary.get("originalimage") or {}).get("source")
        if not thumb:
            result["note"] = "위키백과 문서에 사진이 없다"
            self._cache[key] = result
            self._save()
            return result
        result["image"] = thumb
        ftitle = file_title(original or thumb)
        if ftitle:
            try:
                meta = cast(
                    "dict[str, Any]",
                    await self._http.get_json(META_URL.format(title=urllib.parse.quote(ftitle))),
                )
                query = cast("dict[str, Any]", meta.get("query") or {})
                pages = cast("dict[str, Any]", query.get("pages") or {})
                empty: dict[str, Any] = {}
                page = cast("dict[str, Any]", next(iter(pages.values()), empty))
                info = cast("list[dict[str, Any]]", page.get("imageinfo") or [{}])[0]
                em = cast("dict[str, Any]", info.get("extmetadata") or {})
                artist_raw = cast("dict[str, Any]", em.get("Artist") or {}).get("value")
                lic_raw = cast("dict[str, Any]", em.get("LicenseShortName") or {}).get("value")
                artist = _plain(str(artist_raw or ""))
                lic = str(lic_raw or "").strip()
                result["credit"] = " · ".join(x for x in (artist, lic, "Wikimedia Commons") if x)
            except (OutboundError, ValueError) as exc:
                result["credit"] = "Wikimedia Commons"
                _logger.info(
                    "portrait_meta_unavailable", payload={"title": key, "detail": str(exc)[:80]}
                )
        self._cache[key] = result
        self._save()
        return result


__all__ = ["PortraitSource", "file_title"]
