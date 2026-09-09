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
FUZZY_CUTOFF = 0.75


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
        for alias, (symbol, group, name) in self.entries.items():
            if needle in alias or alias in needle:
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


def load_aliases(path: Path | None = None) -> AliasBook:
    """사전을 파일에서 읽는다.

    Args:
        path: 경로. None 이면 `config/ai_aliases.yml`.

    Returns:
        사전. 파일이 없으면 빈 사전 — 별칭은 편의라 없어도 종목 코드 그대로는 풀린다.
    """
    target = path or DEFAULT_ALIASES_PATH
    if not target.exists():
        return AliasBook({})
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return AliasBook({})
    return parse_aliases(cast("Mapping[str, object]", raw))


__all__ = ["DEFAULT_ALIASES_PATH", "AliasBook", "Resolved", "load_aliases", "parse_aliases"]
