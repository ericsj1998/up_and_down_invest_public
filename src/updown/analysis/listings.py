"""상장 시각에서 셈하는 사건 — 무기한 첫 1H 봉 · 첫 봉 거래대금 문 · 날 오프셋 (T1052).

공지 목록 사건(`analysis.announcements`)과 같은 사건 표(`announcement_events`)에 들어가는
**미리 아는 사건**이다. 계약의 첫 1H 봉 시작(= 상장 시각)에 설정의 날 오프셋을 더한 시각이 사건
시각이고, 사건 시각은 **진입 봉 시작에 맞춘다**(룰 지연 0 — 첫 봉 시작이 정시라 사건 시각도
정시다).

어떤 계약을 셈에 넣나(결제 통화 · 이름 꼴 · 빼는 접두) · 첫 봉 거래대금 문(같은 시각 기준 계약 1H
거래대금 대비 비율) · 오프셋은 **설정이 정한다**(`config/announcements.yml` 의 `listings`) —
코드는 그 규칙을 그대로 적용할 뿐이다. 실계좌 작업 · 펀드 재현이 같은 함수를 쓴다(T968 교훈 —
입력을 한쪽에만 꽂으면 재현의 다리가 0 이 된다).

🔴 상장 시각은 거래소 목록의 `onboardDate` 가 아니라 **첫 1H 봉 시작**이다 — `onboardDate` 는 정시가
아닌 값(08:30 · 11:15 …)이 많아 같은 시각으로 쓰면 첫 봉과 한 봉 어긋나고, 옛 상장은 자리표시 값
(07:00 · 00:00)이라 몇 시간 ~ 며칠 앞선다(T1052 §4 실측). `onboardDate` 는 "첫 봉을 물어볼 만큼
최근인가" 를 거르는 데만 쓴다(작업 쪽).

순수 함수다(시각 · 네트워크 없음 · 절대 규칙 #5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import yaml

from updown.analysis.announcements import DEFAULT_CONFIG_PATH, AnnouncementConfigError

REASON_LISTED = "listed"
"""사건 문을 지났고 아직 계약 짝을 안 지은 줄 — 사건 시각이 다가오면 작업이 짝을 짓는다."""
REASON_HISTORY = "history"
"""사건 시각이 이미 지났거나 상장이 오래돼 첫 봉을 안 물은 줄 — 같은 이름을 다시 안 묻게 적는다."""
REASON_BEFORE = "before_since"
REASON_NO_VOLUME = "no_volume"
REASON_NO_REF = "no_ref"
REASON_SMALL = "small_first_bar"


@dataclass(frozen=True, slots=True)
class ListingRule:
    """상장 시각 사건 갈래 하나의 규칙.

    Attributes:
        kind: 갈래 이름(설정 열쇠 · `Announcement.kind`).
        source: 상장 자료를 내는 거래소(작업이 원천을 고른다).
        offset_days: 첫 1H 봉 시작 + 이 날 = 사건 시각(= 진입 봉 시작).
        min_first_ratio: 첫 1H 봉 거래대금(종가 x 거래량)이 같은 시각 기준 계약 1H 봉 거래대금의 이
            비율 이상이어야 사건이다.
        ref_symbol: 기준 계약 이름(그 거래소 이름).
        quote: 결제 통화 — 계약 이름이 이것으로 끝나야 셈에 넣는다.
        exclude_prefix: 이것으로 시작하는 계약은 뺀다(스테이블 · 지수 계약 등).
        listed_since: 이보다 앞에 상장한 계약은 사건이 아니다(자료 시작 · 없으면 제한 없음).
    """

    kind: str
    source: str
    offset_days: int
    min_first_ratio: Decimal
    ref_symbol: str
    quote: str = "USDT"
    exclude_prefix: tuple[str, ...] = ()
    listed_since: datetime | None = None

    def __post_init__(self) -> None:
        """값이 말이 되는지 — 띄우기 전에 터뜨린다(절대 규칙 #8)."""
        if not self.kind or not self.source or not self.ref_symbol or not self.quote:
            raise AnnouncementConfigError(
                f"상장 사건 갈래 {self.kind!r} 의 이름 · 원천 · 기준 계약"
            )
        if self.offset_days < 1:
            raise AnnouncementConfigError(f"{self.kind}: 오프셋은 1 일 이상: {self.offset_days}")
        if not Decimal(0) <= self.min_first_ratio:
            raise AnnouncementConfigError(
                f"{self.kind}: 첫 봉 비율은 0 이상: {self.min_first_ratio}"
            )
        if self.listed_since is not None and self.listed_since.tzinfo is None:
            raise AnnouncementConfigError(f"{self.kind}: listed_since 에 시간대가 없다")

    @property
    def offset(self) -> timedelta:
        """첫 봉 시작 → 사건 시각."""
        return timedelta(days=self.offset_days)


def load_listing_rules(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, ListingRule]:
    """설정 파일의 `listings` 갈래들을 읽는다.

    Args:
        path: 설정 파일(공지 규칙과 같은 파일).

    Returns:
        갈래 이름 → 규칙. `listings` 블록이 없으면 빈 사전.

    Raises:
        AnnouncementConfigError: 파일이 없거나 칸이 빠졌거나 모르는 칸이 있거나 값이 틀린 경우.
    """
    if not path.exists():
        raise AnnouncementConfigError(f"공지 규칙 설정이 없다: {path}")
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    kinds = cast("dict[str, Any]", raw.get("listings") or {})
    allowed = {
        "source",
        "offset_days",
        "min_first_ratio",
        "ref_symbol",
        "quote",
        "exclude_prefix",
        "listed_since",
    }
    out: dict[str, ListingRule] = {}
    for name, body_raw in kinds.items():
        if not isinstance(body_raw, Mapping):
            raise AnnouncementConfigError(f"listings.{name} 는 매핑이어야 한다")
        body = cast("Mapping[str, Any]", body_raw)
        extra = sorted(set(body) - allowed)
        if extra:
            raise AnnouncementConfigError(f"listings.{name} 에 모르는 칸: {extra}")
        try:
            since_raw = body.get("listed_since")
            since = (
                None
                if since_raw in (None, "")
                else datetime.fromisoformat(str(since_raw)).replace(tzinfo=UTC)
            )
            out[str(name)] = ListingRule(
                kind=str(name),
                source=str(body["source"]),
                offset_days=int(body["offset_days"]),
                min_first_ratio=Decimal(str(body["min_first_ratio"])),
                ref_symbol=str(body["ref_symbol"]),
                quote=str(body.get("quote") or "USDT"),
                exclude_prefix=tuple(str(x) for x in body.get("exclude_prefix") or ()),
                listed_since=since,
            )
        except KeyError as exc:
            raise AnnouncementConfigError(f"listings.{name} 에 칸이 빠졌다: {exc}") from exc
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AnnouncementConfigError(f"listings.{name} 값: {exc}") from exc
    return out


def listing_candidate(symbol: str, rule: ListingRule) -> bool:
    """이 계약 이름을 셈에 넣나 — 결제 통화로 끝나고 · ASCII 이고 · 빼는 접두가 아닌 것.

    Args:
        symbol: 거래소 계약 이름(`XYZUSDT`).
        rule: 갈래 규칙.

    Returns:
        넣으면 참. 계약 종류(무기한 · 코인)는 거래소 사실이라 부르는 쪽이 따로 본다.
    """
    if not symbol.isascii() or not symbol.endswith(rule.quote) or len(symbol) <= len(rule.quote):
        return False
    return not any(symbol.startswith(prefix) for prefix in rule.exclude_prefix)


def listing_due(
    first_open: datetime,
    first_quote: Decimal,
    ref_quote: Decimal | None,
    rule: ListingRule,
) -> tuple[datetime | None, str]:
    """첫 1H 봉 하나 → 사건 시각(= 진입 봉 시작) 또는 사건이 아닌 까닭.

    Args:
        first_open: 첫 1H 봉 시작(UTC · 정시).
        first_quote: 첫 봉 거래대금(종가 x 거래량).
        ref_quote: 같은 시각 기준 계약 1H 봉 거래대금. 그 봉이 없으면 None.
        rule: 갈래 규칙.

    Returns:
        `(사건 시각 | None, 이유)` — 사건이면 `(첫 봉 + 오프셋, "listed")`.

    Raises:
        ValueError: 시각에 시간대가 없는 경우.

    Note:
        기준 봉이 없거나 거래대금이 0 이면 사건이 아니다(모르는 값으로 문을 지난 척하지 않는다 ·
        규칙 #8). 비율 문은 `첫 봉 거래대금 >= 비율 x 기준 거래대금`(같으면 지남).
    """
    if first_open.tzinfo is None:
        raise ValueError("첫 봉 시각에 시간대가 없다")
    if rule.listed_since is not None and first_open < rule.listed_since:
        return None, REASON_BEFORE
    if first_quote <= 0:
        return None, REASON_NO_VOLUME
    if ref_quote is None or ref_quote <= 0:
        return None, REASON_NO_REF
    if first_quote < rule.min_first_ratio * ref_quote:
        return None, REASON_SMALL
    return first_open + rule.offset, REASON_LISTED


__all__ = [
    "REASON_BEFORE",
    "REASON_HISTORY",
    "REASON_LISTED",
    "REASON_NO_REF",
    "REASON_NO_VOLUME",
    "REASON_SMALL",
    "ListingRule",
    "listing_candidate",
    "listing_due",
    "load_listing_rules",
]
