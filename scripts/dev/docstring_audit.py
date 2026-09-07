"""공개 함수의 docstring 절(Args/Returns/Raises) 누락을 세는 래칫 — Google 스타일(CLAUDE.md §2).

ruff 의 `D` 규칙은 "docstring 이 있는가" 와 형식을 보지만, 인자가 있는 함수에 `Args:` 절이
있는지는 `Args:` 절이 **있을 때만** 검사한다(D417). 그래서 절이 통째로 빠진 함수는 ruff 를
통과한다. 이 스크립트가 그 구멍을 센다.

    uv run python scripts/dev/docstring_audit.py                    # 패키지별 집계
    uv run python scripts/dev/docstring_audit.py --list common      # 그 패키지의 누락 목록
    uv run python scripts/dev/docstring_audit.py --strict           # 누락 0 이 아니면 실패 (CI)
    uv run python scripts/dev/docstring_audit.py --baseline 220 590 # (Args, Returns) 상한

규칙: 공개 함수(`_` 로 시작하지 않음) 중 — 인자(self·cls 제외)가 있는데 `Args:` 가 없다 ·
값을 돌려주는 반환 주석이 있는데 `Returns:`/`Yields:` 가 없다 · `raise` 가 있는데 `Raises:` 가
없다. `@property` 와 `@overload` 는 뺀다.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "updown"
SKIP_DECORATORS = {"property", "overload", "cached_property", "setter"}


@dataclass
class Gap:
    """누락 하나."""

    path: str
    line: int
    name: str
    kinds: list[str] = field(default_factory=list[str])


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for d in node.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _returns_value(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if node.returns is None:
        return False
    return not (isinstance(node.returns, ast.Constant) and node.returns.value is None)


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """자식 노드를 걷되 **중첩 함수·클래스 안은 들어가지 않는다** — 안쪽 raise 는 안쪽 것이다."""
    stack: list[ast.AST] = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _always_raises(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """본문이 raise 뿐인 함수 — 미지원 어댑터 메서드. Returns 절을 요구하지 않는다."""
    body = node.body[1:] if ast.get_docstring(node) else node.body
    return bool(body) and all(isinstance(s, ast.Raise) for s in body)


def gaps_in(path: Path) -> list[Gap]:
    """파일 하나의 누락 목록."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[Gap] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") or _decorator_names(node) & SKIP_DECORATORS:
            continue
        doc = ast.get_docstring(node) or ""
        gap = Gap(str(path.relative_to(ROOT)), node.lineno, node.name)
        if not doc:
            gap.kinds.append("docstring")
            out.append(gap)
            continue
        positional = node.args.args + node.args.kwonlyargs
        params = [a.arg for a in positional if a.arg not in ("self", "cls")]
        if node.args.vararg:
            params.append(node.args.vararg.arg)
        if params and "Args:" not in doc:
            gap.kinds.append("Args")
        returns_doc = "Returns:" in doc or "Yields:" in doc
        if _returns_value(node) and not _always_raises(node) and not returns_doc:
            gap.kinds.append("Returns")
        if any(isinstance(n, ast.Raise) for n in _own_nodes(node)) and "Raises:" not in doc:
            gap.kinds.append("Raises")
        if gap.kinds:
            out.append(gap)
    return out


def main() -> int:
    """진입점."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--list", metavar="PKG", help="이 최상위 패키지의 누락 함수를 나열")
    parser.add_argument(
        "--baseline", nargs=2, type=int, metavar=("ARGS", "RETURNS"), help="넘으면 실패"
    )
    parser.add_argument("--strict", action="store_true", help="누락이 하나라도 있으면 실패")
    args = parser.parse_args()

    kinds = ("Args", "Returns", "Raises", "docstring")
    per_pkg: dict[str, dict[str, int]] = {}
    gaps: list[Gap] = []
    for path in sorted(SRC.rglob("*.py")):
        pkg = path.relative_to(SRC).parts[0]
        row = per_pkg.setdefault(pkg, dict.fromkeys(kinds, 0))
        for g in gaps_in(path):
            gaps.append(g)
            for k in g.kinds:
                row[k] += 1
    total = {k: sum(r[k] for r in per_pkg.values()) for k in kinds}

    if args.list:
        prefix, single = f"src/updown/{args.list}/", f"src/updown/{args.list}.py"
        for g in gaps:
            if g.path.startswith(prefix) or g.path == single:
                print(f"{g.path}:{g.line} {g.name} — {', '.join(g.kinds)}")
        return 0

    def line(name: str, row: dict[str, int]) -> str:
        cells = " ".join(f"{row[k]:{w}d}" for k, w in zip(kinds, (6, 8, 7, 7), strict=True))
        return f"{name:14s} {cells}"

    print(f"{'package':14s} {'Args':>6s} {'Returns':>8s} {'Raises':>7s} {'no-doc':>7s}")
    for pkg, row in sorted(per_pkg.items(), key=lambda kv: -kv[1]["Args"]):
        print(line(pkg, row))
    print(line("total", total))
    if args.strict and any(total.values()):
        for g in gaps:
            print(f"  {g.path}:{g.line} {g.name} — {', '.join(g.kinds)}")
        print("strict: 공개 함수의 docstring 절이 빠졌다 (Google 스타일 · CLAUDE.md §2)")
        return 1
    over_args = total["Args"] > args.baseline[0] if args.baseline else False
    over_returns = total["Returns"] > args.baseline[1] if args.baseline else False
    if over_args or over_returns:
        print(f"ratchet: Args {total['Args']} / Returns {total['Returns']} over {args.baseline}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
