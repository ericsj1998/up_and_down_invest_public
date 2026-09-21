"""종목 묶음 — 종목 순위 화면의 탭과 태그 (2026-09-22).

사용자 요구: *"탭 하나는 코인이고, 남은 하나는 META 구글 애플 뭐 이런 거 추종하는 USDT, 그리고
지수 추종 … 숏을 추종하는 종목은 따로 태그 달아줘야 할 것 같아."*

값의 단일 출처는 `config/symbol_groups.yml` 이다 — 코드에는 종목 이름이 하나도 없다
(spec §4.3.1 · 임계값·성질을 코드에 박지 않는다). 서버가 이 표를 읽어 화면에 말해 주고,
화면은 탭 이름도 종목 이름도 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "symbol_groups.yml"


class SymbolGroupConfigError(ValueError):
    """묶음 설정을 읽을 수 없다 — 조용히 "전부 코인" 으로 넘어가지 않는다 (절대 규칙 #8).

    설정이 깨졌는데 기본값으로 돌면, 주식 추종 계약이 코인 탭에 섞인 채 아무도 모른다 —
    2026-09-22 까지 실제로 그랬다(토큰화 주식 넷이 코인 목록에 있었다).
    """


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """탭 하나."""

    key: str
    label: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class TagInfo:
    """태그 하나 — 탭과 별개로, 같은 탭 안에서 성질이 다른 것을 가른다."""

    key: str
    label: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """한 종목의 묶음 정보."""

    symbol: str
    group: str
    name: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SymbolGroups:
    """묶음 표 전체."""

    default: str
    groups: tuple[GroupInfo, ...]
    tags: tuple[TagInfo, ...]
    markets: dict[str, dict[str, SymbolInfo]]

    def of(self, market: str, symbol: str) -> SymbolInfo:
        """그 종목의 묶음 — 표에 없으면 기본 묶음(코인)이다."""
        found = self.markets.get(market, {}).get(symbol)
        return found if found is not None else SymbolInfo(symbol=symbol, group=self.default)

    def declared(self, market: str, group: str) -> tuple[str, ...]:
        """그 거래소의 그 묶음에 **선언된** 종목들 (기본 묶음은 선언이 없으므로 빈 튜플)."""
        return tuple(
            sorted(s for s, info in self.markets.get(market, {}).items() if info.group == group)
        )


@lru_cache(maxsize=4)
def load_symbol_groups(path: Path | None = None) -> SymbolGroups:
    """묶음 표를 읽는다 (프로세스 수명 동안 캐시 — 설정은 재기동으로 바뀐다).

    Args:
        path: 설정 파일. 비면 `config/symbol_groups.yml`.

    Raises:
        SymbolGroupConfigError: 파일이 없거나 모양이 틀린 경우 · 선언 안 된 묶음·태그를
            가리키는 경우.
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        raise SymbolGroupConfigError(f"종목 묶음 설정이 없다: {target}")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SymbolGroupConfigError(f"종목 묶음 설정이 객체가 아니다: {target}")
    body = cast("dict[str, Any]", raw)
    groups = tuple(
        GroupInfo(key=str(g["key"]), label=str(g["label"]), hint=str(g.get("hint", "")))
        for g in cast("list[dict[str, Any]]", body.get("groups") or [])
    )
    keys = {g.key for g in groups}
    default = str(body.get("default", ""))
    if default not in keys:
        raise SymbolGroupConfigError(f"기본 묶음 {default!r} 이 groups 에 없다: {sorted(keys)}")
    tags = tuple(
        TagInfo(key=str(k), label=str(v["label"]), hint=str(v.get("hint", "")))
        for k, v in cast("dict[str, dict[str, Any]]", body.get("tags") or {}).items()
    )
    tag_keys = {t.key for t in tags}
    markets: dict[str, dict[str, SymbolInfo]] = {}
    for market, by_group in cast("dict[str, dict[str, Any]]", body.get("markets") or {}).items():
        table: dict[str, SymbolInfo] = {}
        for group, symbols in cast("dict[str, dict[str, Any]]", by_group or {}).items():
            if group not in keys:
                raise SymbolGroupConfigError(f"{market}: 선언 안 된 묶음 {group!r}")
            if group == default:
                raise SymbolGroupConfigError(
                    f"{market}: 기본 묶음 {group!r} 에는 종목을 적지 않는다"
                    " — 없는 것이 전부 기본이다"
                )
            for symbol, spec in (symbols or {}).items():
                detail = cast("dict[str, Any]", spec or {})
                marks = tuple(str(t) for t in detail.get("tags") or ())
                unknown = [t for t in marks if t not in tag_keys]
                if unknown:
                    raise SymbolGroupConfigError(f"{market}·{symbol}: 선언 안 된 태그 {unknown}")
                if symbol in table:
                    raise SymbolGroupConfigError(f"{market}·{symbol}: 두 묶음에 같이 있다")
                table[str(symbol)] = SymbolInfo(
                    symbol=str(symbol), group=group, name=str(detail.get("name", "")), tags=marks
                )
        markets[str(market)] = table
    return SymbolGroups(default=default, groups=groups, tags=tags, markets=markets)
