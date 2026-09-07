"""마크다운 상대 링크 검사 — 저장소 안의 `[텍스트](경로)` 가 실제 파일을 가리키는지 본다.

문서를 옮길 때 링크가 조용히 끊기는 것을 막는 도구다. 결과는 "끊긴 링크 목록 + 개수" 이고,
`--baseline N` 을 주면 그보다 많을 때 종료 코드 1 로 실패한다 (CI 래칫).

    uv run python scripts/dev/check_md_links.py            # 전부 출력
    uv run python scripts/dev/check_md_links.py --baseline 0

검사 범위: git 이 추적하는 `*.md`. `http(s)://` · `mailto:` · `#앵커만` 은 건너뛴다.
디렉터리를 가리키는 링크는 그 디렉터리가 있으면 통과다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REF = re.compile(r"^\[[^\]]+\]:\s+(\S+)", re.M)


def tracked_markdown() -> list[Path]:
    """git 이 추적하는 마크다운 파일."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    return [ROOT / p for p in out.decode("utf-8").split("\0") if p]


def targets_in(text: str) -> list[str]:
    """본문에서 링크 대상만 뽑는다 (인라인 + 참조식)."""
    found = LINK.findall(text) + REF.findall(text)
    return [t for t in found if not t.startswith(("http://", "https://", "mailto:", "#"))]


def broken_links(path: Path) -> list[tuple[str, str]]:
    """파일 하나의 끊긴 링크 `(대상, 이유)` 목록."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    for raw in targets_in(text):
        target = unquote(raw.split("#", 1)[0].strip("<>"))
        if not target:
            continue
        resolved = (
            (path.parent / target).resolve()
            if not target.startswith("/")
            else ROOT / target.lstrip("/")
        )
        if not resolved.exists():
            shown = (
                str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else str(resolved)
            )
            out.append((raw, shown))
    return out


def main() -> int:
    """진입점."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--baseline", type=int, default=None, help="이 수를 넘으면 실패")
    parser.add_argument("--quiet", action="store_true", help="개수만")
    args = parser.parse_args()
    total = 0
    for md in tracked_markdown():
        bad = broken_links(md)
        if not bad:
            continue
        total += len(bad)
        if not args.quiet:
            print(f"{md.relative_to(ROOT)}")
            for raw, resolved in bad:
                print(f"    {raw}  ->  {resolved}")
    print(f"broken links: {total}")
    if args.baseline is not None and total > args.baseline:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
