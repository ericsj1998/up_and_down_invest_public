"""대시보드 명세 — 모델은 **무엇을 보여줄지** 만 고르고 값은 도구 결과에서만 (T256 · 순수).

모델이 HTML 이나 차트 코드를 쓰지 않는다. 닫힌 부품 목록(카드 · 표 · 칩 · 글 · 스파크라인)의
명세(JSON)만 내고, 숫자 칸은 전부 `{"from": "<도구>.<경로>"}` 참조여야 한다. 리터럴 숫자는
거부한다 — 그 자리가 곧 환각 자리다.
`resolve` 가 참조를 이번 턴의 도구 결과로 채우고, 없는 참조는 빈 칸으로 두며 `missing` 에 적는다
(T249 "환각 건수" 채점 원료).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

KINDS = frozenset({"cards", "table", "chips", "text", "sparkline"})
"""부품 종류 — 닫힌 목록. 모르는 종류는 블록째 버린다(조용히 그리지 않는다)."""
MAX_BLOCKS = 8
MAX_ROWS = 50
MAX_ITEMS = 12

_PATH = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*)$")
_STEP = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


@dataclass(slots=True)
class Resolved:
    """채운 명세 + 근거 없는 칸.

    Attributes:
        spec: 값이 채워진 명세 (화면이 그대로 그린다).
        missing: 채우지 못한 참조 경로들 — 환각 후보.
        dropped: 버린 블록 사유들 (모르는 종류 · 리터럴 숫자).
    """

    spec: dict[str, Any]
    missing: list[str] = field(default_factory=list[str])
    dropped: list[str] = field(default_factory=list[str])


def lookup(results: dict[str, Any], path: str) -> tuple[bool, Any]:
    """`도구.키[0].키` 경로를 도구 결과에서 찾는다.

    Args:
        results: 도구 이름 → 결과 dict (이번 턴).
        path: 참조 경로.

    Returns:
        `(찾았나, 값)`. 경로 문법이 틀리거나 중간이 없으면 `(False, None)`.
    """
    match = _PATH.match(path.strip())
    if not match:
        return False, None
    tool, rest = match.group(1), match.group(2)
    if tool not in results:
        return False, None
    node: Any = results[tool]
    for step in _STEP.finditer(rest):
        key, index = step.group(1), step.group(2)
        if key is not None:
            if not isinstance(node, dict) or key not in cast("dict[str, Any]", node):
                return False, None
            node = cast("dict[str, Any]", node)[key]
        else:
            i = int(index)
            if not isinstance(node, list) or i >= len(cast("list[Any]", node)):
                return False, None
            node = cast("list[Any]", node)[i]
    return True, node


def _fill(value: Any, results: dict[str, Any], missing: list[str], dropped: list[str]) -> Any:
    """값 칸 하나 — 참조면 채우고, 리터럴 숫자면 거부(None + dropped), 문자열·불리언은 그대로."""
    if isinstance(value, dict):
        ref = cast("dict[str, Any]", value).get("from")
        if isinstance(ref, str):
            found, got = lookup(results, ref)
            if not found:
                missing.append(ref)
                return None
            return got
        return None
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        dropped.append(f"리터럴 숫자 {value!r} — 도구 결과 참조만 허용")
        return None
    return None


def resolve(spec: dict[str, Any], results: dict[str, Any]) -> Resolved:
    """명세의 참조를 도구 결과로 채운다.

    Args:
        spec: 모델이 낸 명세 `{title, blocks: [...]}`.
        results: 이번 턴의 도구 이름 → 결과.

    Returns:
        채운 명세 · 근거 없는 참조 · 버린 블록.
    """
    out = Resolved(spec={"title": str(spec.get("title") or ""), "blocks": []})
    raw_blocks = spec.get("blocks")
    if not isinstance(raw_blocks, list):
        out.dropped.append("blocks 가 목록이 아니다")
        return out
    for raw in cast("list[object]", raw_blocks)[:MAX_BLOCKS]:
        if not isinstance(raw, dict):
            out.dropped.append("블록이 객체가 아니다")
            continue
        block = cast("dict[str, Any]", raw)
        kind = str(block.get("kind") or "")
        if kind not in KINDS:
            out.dropped.append(f"모르는 부품 {kind!r}")
            continue
        made = _block(kind, block, results, out)
        if made is not None:
            out.spec["blocks"].append(made)
    return out


def _block(
    kind: str, block: dict[str, Any], results: dict[str, Any], out: Resolved
) -> dict[str, Any] | None:
    """블록 하나를 종류별 규칙으로 채운다.

    - `text` 는 참조가 없다(글만).
    - `cards` 는 항목의 `value` 칸마다 `_fill` — 리터럴 숫자는 여기서 거부된다.
    - `chips` · `sparkline` · `table` 은 `from` 목록 참조 하나에서 행을 받는다. 표의 열은
      그 행의 키(`a.b` 꼴 허용)다.

    참조가 없거나 못 찾으면 블록을 버리지 않고 **빈 항목**으로 그린다 — 근거 없는 칸이
    비어 보이는 것이 곧 채점 신호다.

    Args:
        kind: `KINDS` 안의 부품 종류 (호출 전에 걸러진다).
        block: 모델이 낸 블록 명세.
        results: 이번 턴의 도구 이름 → 결과.
        out: `missing` · `dropped` 를 쌓는 곳.

    Returns:
        화면이 그릴 블록. 지금은 늘 값을 돌려주지만 서명은 버릴 여지를 남긴다.
    """
    title = str(block.get("title") or "")
    if kind == "text":
        text = block.get("text")
        return {"kind": kind, "title": title, "text": str(text) if isinstance(text, str) else ""}
    if kind == "cards":
        items: list[dict[str, Any]] = []
        for raw in cast("list[object]", block.get("items") or [])[:MAX_ITEMS]:
            if not isinstance(raw, dict):
                continue
            item = cast("dict[str, Any]", raw)
            items.append(
                {
                    "label": str(item.get("label") or ""),
                    "value": _fill(item.get("value"), results, out.missing, out.dropped),
                    "unit": str(item.get("unit") or ""),
                }
            )
        return {"kind": kind, "title": title, "items": items}
    if kind == "chips":
        found, rows = _from_list(block, results, out)
        if not found:
            return {"kind": kind, "title": title, "items": []}
        return {
            "kind": kind,
            "title": title,
            "items": [
                str(r)
                if not isinstance(r, dict)
                else str(
                    cast("dict[str, Any]", r).get("label")
                    or cast("dict[str, Any]", r).get("symbol")
                    or ""
                )
                for r in rows[:MAX_ITEMS]
            ],
        }
    if kind == "sparkline":
        found, rows = _from_list(block, results, out)
        values: list[float] = []
        for r in rows[:200] if found else []:
            try:
                values.append(float(r))
            except (TypeError, ValueError):
                continue
        return {"kind": kind, "title": title, "values": values}
    # table — 행은 도구 결과의 목록 참조, 열은 그 행의 키
    found, rows = _from_list(block, results, out)
    columns_raw = cast("list[object]", block.get("columns") or [])
    columns: list[dict[str, str]] = []
    for raw in columns_raw[:MAX_ITEMS]:
        if isinstance(raw, dict):
            c = cast("dict[str, Any]", raw)
            key = str(c.get("key") or "")
            if key:
                columns.append({"key": key, "label": str(c.get("label") or key)})
        elif isinstance(raw, str):
            columns.append({"key": raw, "label": raw})
    table_rows: list[list[Any]] = []
    for r in rows[:MAX_ROWS] if found else []:
        if not isinstance(r, dict):
            continue
        row = cast("dict[str, Any]", r)
        table_rows.append([_cell(row, c["key"]) for c in columns])
    return {"kind": kind, "title": title, "columns": columns, "rows": table_rows}


def _from_list(
    block: dict[str, Any], results: dict[str, Any], out: Resolved
) -> tuple[bool, list[Any]]:
    """블록의 `from` 참조를 목록으로 푼다.

    참조 자체가 없으면 명세 결함이라 `dropped` 에, 있는데 못 찾거나 목록이 아니면 환각
    후보라 `missing` 에 적는다 — 둘은 채점에서 다른 뜻이다.

    Args:
        block: `from` 을 가진 블록 명세.
        results: 이번 턴의 도구 이름 → 결과.
        out: 사유를 쌓는 곳.

    Returns:
        `(찾았나, 행들)`. 못 찾으면 `(False, [])`.
    """
    ref = block.get("from")
    if not isinstance(ref, str):
        out.dropped.append(f"{block.get('kind')} 블록에 from 참조가 없다")
        return False, []
    found, got = lookup(results, ref)
    if not found or not isinstance(got, list):
        out.missing.append(ref)
        return False, []
    return True, cast("list[Any]", got)


def _cell(row: dict[str, Any], key: str) -> Any:
    """행에서 `a.b` 꼴 키도 따라간다 (metrics.per.value)."""
    node: Any = row
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = cast("dict[str, Any]", node).get(part)
    if isinstance(node, dict | list):
        return None
    return node


__all__ = ["KINDS", "MAX_BLOCKS", "MAX_ITEMS", "MAX_ROWS", "Resolved", "lookup", "resolve"]
