"""거래소 공지 제목 읽기 — 제목 규칙으로 기초 자산 이름을 뽑고 진입 봉 시각을 셈한다 (T1045).

공지 목록은 제목과 올린 시각뿐이다. 어떤 제목을 사건으로 보고(포함 · 제외 낱말) 어디서부터
어디까지를 자산 이름 자리로 읽는지(시작 · 끝 정규식 · 이름 꼴 · 빼는 낱말)는 **설정이 정한다**
(`config/announcements.yml`) — 코드는 그 규칙을 그대로 적용할 뿐이다. 실계좌 작업 · 펀드 재현이 같은
함수를 쓴다(T968 교훈 — 입력을 한쪽에만 꽂으면 재현의 다리가 0 이 된다).

순수 함수다(시각 · 네트워크 없음 · 절대 규칙 #5). 짝짓기(순위 이름 → 주문 계약)는
`decision.universe_names` 가 맡는다.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "announcements.yml"


class AnnouncementConfigError(ValueError):
    """공지 규칙 설정이 없거나 꼴이 틀렸다 — 작업을 띄우기 전에 터뜨린다(절대 규칙 #8)."""


@dataclass(frozen=True, slots=True)
class TitleRule:
    """공지 갈래 하나의 제목 규칙.

    Attributes:
        kind: 갈래 이름(설정 열쇠 · `Announcement.kind`).
        source: 목록을 내는 거래소(작업이 원천을 고른다).
        catalog: 그 거래소 공지 목록 번호.
        include: 제목에 **모두** 들어 있어야 하는 낱말.
        exclude: 하나라도 들어 있으면 버리는 낱말.
        start: 자산 이름 자리의 시작 — 이 정규식에 처음 맞은 곳 **뒤**부터 읽는다.
        end: 자산 이름 자리의 끝 — 시작 뒤 글에서 이 정규식에 처음 맞은 곳 **앞**까지.
        token: 자산 이름 꼴(정규식 · 단어 경계 포함).
        stop: 이름 꼴에 맞아도 버리는 낱말(통화 · 접속사 등).
        pair_prefix: 제목이 이것으로 시작하면 이름 끝의 `quote` 를 떼어 기초 자산으로 읽는다
            (계약 이름으로 적힌 공지). 비면 떼지 않는다.
        quote: 결제 통화(이름 끝 떼기 · 순위 이름 짓기).
    """

    kind: str
    source: str
    catalog: int
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    start: str
    end: str
    token: str
    stop: frozenset[str] = frozenset()
    pair_prefix: str = ""
    quote: str = "USDT"

    def __post_init__(self) -> None:
        """정규식이 깨졌거나 낱말이 비면 띄우기 전에 터뜨린다."""
        if not self.kind or not self.source or self.catalog <= 0:
            raise AnnouncementConfigError(f"공지 갈래 {self.kind!r} 의 이름 · 원천 · 목록 번호")
        if not self.include:
            raise AnnouncementConfigError(
                f"{self.kind}: 포함 낱말이 없다 — 모든 공지가 사건이 된다"
            )
        for label, pattern in (("start", self.start), ("end", self.end), ("token", self.token)):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise AnnouncementConfigError(f"{self.kind}.{label} 정규식: {exc}") from exc


def load_title_rules(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, TitleRule]:
    """설정 파일의 갈래들을 읽는다.

    Args:
        path: 설정 파일.

    Returns:
        갈래 이름 → 규칙.

    Raises:
        AnnouncementConfigError: 파일이 없거나 칸이 빠졌거나 모르는 칸이 있는 경우.
    """
    if not path.exists():
        raise AnnouncementConfigError(f"공지 규칙 설정이 없다: {path}")
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    kinds = cast("dict[str, Any]", raw.get("kinds") or {})
    allowed = {
        "source",
        "catalog",
        "include",
        "exclude",
        "start",
        "end",
        "token",
        "stop",
        "pair_prefix",
        "quote",
    }
    out: dict[str, TitleRule] = {}
    for name, body_raw in kinds.items():
        if not isinstance(body_raw, Mapping):
            raise AnnouncementConfigError(f"kinds.{name} 는 매핑이어야 한다")
        body = cast("Mapping[str, Any]", body_raw)
        extra = sorted(set(body) - allowed)
        if extra:
            raise AnnouncementConfigError(f"kinds.{name} 에 모르는 칸: {extra}")
        try:
            out[str(name)] = TitleRule(
                kind=str(name),
                source=str(body["source"]),
                catalog=int(body["catalog"]),
                include=tuple(str(x) for x in body.get("include") or ()),
                exclude=tuple(str(x) for x in body.get("exclude") or ()),
                start=str(body["start"]),
                end=str(body["end"]),
                token=str(body["token"]),
                stop=frozenset(str(x) for x in body.get("stop") or ()),
                pair_prefix=str(body.get("pair_prefix") or ""),
                quote=str(body.get("quote") or "USDT"),
            )
        except KeyError as exc:
            raise AnnouncementConfigError(f"kinds.{name} 에 칸이 빠졌다: {exc}") from exc
    return out


def tokens_of(title: str, rule: TitleRule) -> tuple[str, ...]:
    """제목 하나에서 기초 자산 이름들을 뽑는다(나온 차례 · 겹침 그대로).

    Args:
        title: 공지 제목.
        rule: 갈래 규칙.

    Returns:
        이름들. 사건 제목이 아니면 빈 튜플.

    Note:
        자리 = `start` 에 처음 맞은 곳 뒤 → `end` 에 처음 맞은 곳 앞. 이름 꼴(`token`)에 맞는
        낱말 가운데 `stop` 을 뺀다. 제목이 `pair_prefix` 로 시작하면(계약 이름으로 적힌 공지)
        이름 끝의 결제 통화를 뗀다 — 떼고 남는 것이 없으면 그대로 둔다.
    """
    if not title or any(word not in title for word in rule.include):
        return ()
    if any(word in title for word in rule.exclude):
        return ()
    head = re.search(rule.start, title)
    if head is None:
        return ()
    seg = title[head.end() :]
    tail = re.search(rule.end, seg)
    if tail is not None:
        seg = seg[: tail.start()]
    pair = bool(rule.pair_prefix) and title.startswith(rule.pair_prefix)
    out: list[str] = []
    for word in re.findall(rule.token, seg):
        if word in rule.stop:
            continue
        if pair and word.endswith(rule.quote) and len(word) > len(rule.quote):
            word = word[: -len(rule.quote)]
        out.append(word)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class TitleHit:
    """제목에서 뽑은 이름 하나의 가장 이른 공지.

    Attributes:
        token: 기초 자산 이름.
        released_ms: 올린 시각(UTC 밀리초).
        article_id: 목록의 글 번호(없으면 빈 문자열).
        title: 그 공지 제목.
    """

    token: str
    released_ms: int
    article_id: str
    title: str

    @property
    def announced_at(self) -> datetime:
        """올린 시각(UTC)."""
        return datetime.fromtimestamp(self.released_ms / 1000, tz=UTC)


def earliest_hits(articles: Iterable[Mapping[str, Any]], rule: TitleRule) -> dict[str, TitleHit]:
    """글 목록에서 이름마다 **가장 이른 공지 하나**를 고른다.

    Args:
        articles: `{"title": 제목, "rel": 올린 시각 ms, "id": 글 번호}` 꼴 글들(순서 무관).
        rule: 갈래 규칙.

    Returns:
        이름 → 가장 이른 공지. 같은 시각이면 먼저 읽은 글.

    Note:
        올린 시각이 없거나 숫자가 아닌 글은 건너뛴다(시각을 모르면 진입 봉을 못 셈한다).
    """
    out: dict[str, TitleHit] = {}
    for art in articles:
        title = str(art.get("title") or "")
        raw = art.get("rel")
        if raw is None:
            continue
        try:
            rel = int(raw)
        except (TypeError, ValueError):
            continue
        for token in tokens_of(title, rule):
            seen = out.get(token)
            if seen is None or rel < seen.released_ms:
                out[token] = TitleHit(token, rel, str(art.get("id") or ""), title)
    return out


def rank_name(token: str, names: Collection[str], quote: str = "USDT") -> str | None:
    """기초 자산 이름 → 순위 거래소 무기한 이름(`XYZUSDT` 다음 `1000XYZUSDT`).

    Args:
        token: 기초 자산 이름.
        names: 순위 거래소의 무기한 이름들.
        quote: 결제 통화.

    Returns:
        있는 이름. 둘 다 없으면 None.
    """
    for name in (f"{token}{quote}", f"1000{token}{quote}"):
        if name in names:
            return name
    return None


def entry_bar_start(announced_at: datetime, delay_s: int, frame: timedelta) -> datetime:
    """공지 + `delay_s` 초 **뒤에** 시작하는 첫 봉의 시작 시각(같으면 그 봉).

    Args:
        announced_at: 공지 시각(UTC).
        delay_s: 공지 뒤 쉬는 초(공지를 읽고 주문할 틈).
        frame: 봉 길이(정시 1H 면 1 시간).

    Returns:
        봉 시작 시각(UTC · 봉 경계).

    Raises:
        ValueError: 시각에 시간대가 없거나 봉 길이가 양수가 아닌 경우.
    """
    if announced_at.tzinfo is None:
        raise ValueError("공지 시각에 시간대가 없다")
    span = int(frame.total_seconds())
    if span <= 0:
        raise ValueError(f"봉 길이는 양수다: {frame}")
    at = int(announced_at.timestamp()) + delay_s
    return datetime.fromtimestamp(-(-at // span) * span, tz=UTC)


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "AnnouncementConfigError",
    "TitleHit",
    "TitleRule",
    "earliest_hits",
    "entry_bar_start",
    "load_title_rules",
    "rank_name",
    "tokens_of",
]
