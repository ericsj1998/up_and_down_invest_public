"""종목 별칭 사전 + 퍼지 매칭 (T248 `symbol_resolve` · 순수).

의도 분류는 모델이 하고, 이름 → 종목만 여기서 푼다. 못 푼 이름은 "모른다" 로 돌려주고 호출부가
로그에 모은다 —
사전을 키우는 재료다. 규칙 기반 키워드 매칭으로 의도를 가르지 않는다(유사어를 다 못 적는다).
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

DEFAULT_ALIASES_PATH = Path(__file__).resolve().parents[4] / "config" / "ai_aliases.yml"
DEFAULT_NAMES_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "fundamentals" / "universe_names.yml"
)
"""토스가 준 한글·영문 이름표 (T260 후속 · `seed_universe.py` 생성). 손 별칭이 우선한다."""
NAME_GROUPS = {
    "NASDAQ": "foreign",
    "NYSE": "foreign",
    "AMEX": "foreign",
    "KOSPI": "domestic",
    "KOSDAQ": "domestic",
    "KRX": "domestic",
}
FUZZY_CUTOFF = 0.75
MIN_CONTAINED = 3
"""별칭이 질문 **안에** 들어 있다고 볼 최소 길이.

그보다 짧으면 정확 일치, 또는 질문이 별칭 안에 든 것만 잡는다.
"""


@dataclass(frozen=True, slots=True)
class Resolved:
    """푼 결과 하나.

    Attributes:
        symbol: 종목 코드 (코인은 기초 자산 `BTC` — 시장 표기는 호출부가 붙인다).
        group: `foreign` · `domestic` · `coin`.
        name: 대표 이름.
        confidence: 1.0 정확 · 그 아래는 퍼지.
    """

    symbol: str
    group: str
    name: str
    confidence: float


@dataclass(frozen=True, slots=True)
class AliasBook:
    """사전.

    Attributes:
        entries: 소문자 별칭 → (symbol, group, 대표 이름).
    """

    entries: Mapping[str, tuple[str, str, str]]

    def resolve(self, query: str, *, limit: int = 3) -> list[Resolved]:
        """이름을 푼다 — 정확 일치 → 포함 → 퍼지 순.

        Args:
            query: 사람이 쓴 이름.
            limit: 후보 수.

        Returns:
            신뢰도 내림차순 후보. 비면 모른다.
        """
        needle = query.strip().lower()
        if not needle:
            return []
        exact = self.entries.get(needle)
        if exact is not None:
            return [Resolved(exact[0], exact[1], exact[2], 1.0)]
        found: dict[str, Resolved] = {}
        # ⭐ 낱말 단위 정확 일치 — "구글 주식" 의 "구글" 은 2글자라 포함 규칙(≥3)에 걸리지 않아
        #    모델이 "GOOGL 인가 GOOG 인가" 되물었다(2026-09-10 4·5차 실측 2/2). 낱말 전체가
        #    별칭과 같으면 짧아도 안전하다 — 한 글자 종목이 긴 질문 안에 "들어 있는" 것과 다르다.
        for token in needle.split():
            hit = self.entries.get(token)
            if hit is not None:
                found.setdefault(hit[0], Resolved(hit[0], hit[1], hit[2], 0.95))
        if found:
            return sorted(found.values(), key=lambda r: -r.confidence)[:limit]
        for alias, (symbol, group, name) in self.entries.items():
            # ⛔ 짧은 별칭이 긴 질문 안에 "들어 있다" 고 잡지 않는다 — 이름표가 들어오며
            #    한 글자 종목(A · J · T · V …)이 생겨 "JPMorgan" 이 A(애질런트)로 풀렸다
            #    (2026-09-10 실측).
            if needle in alias or (len(alias) >= MIN_CONTAINED and alias in needle):
                found.setdefault(symbol, Resolved(symbol, group, name, 0.9))
        if found:
            return sorted(found.values(), key=lambda r: -r.confidence)[:limit]
        for alias in difflib.get_close_matches(
            needle, list(self.entries), n=limit, cutoff=FUZZY_CUTOFF
        ):
            symbol, group, name = self.entries[alias]
            ratio = difflib.SequenceMatcher(None, needle, alias).ratio()
            found.setdefault(symbol, Resolved(symbol, group, name, round(ratio, 2)))
        return sorted(found.values(), key=lambda r: -r.confidence)[:limit]


def parse_aliases(raw: Mapping[str, object]) -> AliasBook:
    """YAML 매핑 → 사전.

    Args:
        raw: `{"stocks": {SYM: [별칭...]}, "domestic": {...}, "coins": {...}}`.

    Returns:
        사전. 종목 코드 자체도 별칭으로 들어간다(소문자).
    """
    entries: dict[str, tuple[str, str, str]] = {}
    for section, group in (("stocks", "foreign"), ("domestic", "domestic"), ("coins", "coin")):
        block = raw.get(section)
        if not isinstance(block, dict):
            continue
        for symbol, names in cast("Mapping[object, object]", block).items():
            code = str(symbol)
            aliases = (
                [str(n) for n in cast("list[object]", names)] if isinstance(names, list) else []
            )
            title = aliases[0] if aliases else code
            entries[code.lower()] = (code, group, title)
            for alias in aliases:
                entries[alias.strip().lower()] = (code, group, title)
    return AliasBook(entries)


def parse_names(raw: Mapping[str, object]) -> dict[str, tuple[str, str, str]]:
    """이름표(`universe_names.yml`) → 사전 항목.

    Args:
        raw: `{SYM: {ko, en, market}}`.

    Returns:
        소문자 별칭 → (symbol, group, 대표 이름). 대표 이름은 한글(`ko`) · 없으면 영문 ·
        없으면 코드.
        시장을 모르는 행은 해외로 본다(후보 파일이 미국주식이다).

    Note:
        "오라클" 처럼 사람이 부르는 이름은 토스가 이미 갖고 있다 — 손 별칭(`ai_aliases.yml`)에 없어
        모델이 되묻던 것(2026-09-10 사용자 신고)을 여기서 푼다.
    """
    entries: dict[str, tuple[str, str, str]] = {}
    for symbol, row in raw.items():
        if not isinstance(row, dict):
            continue
        item = cast("Mapping[str, object]", row)
        code = str(symbol).upper()
        ko = str(item.get("ko") or "").strip()
        en = str(item.get("en") or "").strip()
        group = NAME_GROUPS.get(str(item.get("market") or ""), "foreign")
        title = ko or en or code
        entries[code.lower()] = (code, group, title)
        for alias in (ko, en):
            if alias:
                entries[alias.lower()] = (code, group, title)
    return entries


def load_aliases(path: Path | None = None, names_path: Path | None = None) -> AliasBook:
    """사전을 파일에서 읽는다 — 손 별칭 + 토스 이름표.

    Args:
        path: 손 별칭 경로. None 이면 `config/ai_aliases.yml`.
        names_path: 이름표 경로. None 이면 `config/fundamentals/universe_names.yml`.

    Returns:
        사전. 파일이 없으면 빈 사전 — 별칭은 편의라 없어도 종목 코드 그대로는 풀린다.
        같은 이름이 둘 다에 있으면 **손 별칭이 이긴다**(사람이 고른 대표 이름 · 갈래).
    """
    entries: dict[str, tuple[str, str, str]] = {}
    names = names_path or DEFAULT_NAMES_PATH
    if names.exists():
        raw_names = yaml.safe_load(names.read_text(encoding="utf-8"))
        if isinstance(raw_names, dict):
            entries.update(parse_names(cast("Mapping[str, object]", raw_names)))
    target = path or DEFAULT_ALIASES_PATH
    if target.exists():
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            entries.update(parse_aliases(cast("Mapping[str, object]", raw)).entries)
    return AliasBook(entries)


__all__ = [
    "DEFAULT_ALIASES_PATH",
    "DEFAULT_NAMES_PATH",
    "AliasBook",
    "Resolved",
    "load_aliases",
    "parse_aliases",
    "parse_names",
]
