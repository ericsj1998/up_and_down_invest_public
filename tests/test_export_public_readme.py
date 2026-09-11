"""공개본 README 파생 — 메인 README 하나에서 만들고, 비공개 문서 링크가 남지 않는다.

`public/README.md` 를 따로 두면 메인만 고쳐지고 공개본은 낡는다(2026-09-11 실측). 그래서 내보낼 때
메인 README 를 변환한다. 이 시험은 그 변환이 (1) 비공개 링크를 글로 바꾸고 (2) 공개 전용 절을 붙이고
(3) 다이어그램과 화면 표를 그대로 두는지를 실제 README 로 확인한다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "export_public", ROOT / "scripts/dev/export_public.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_public"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_public_readme_has_no_private_links() -> None:
    """비공개 경로로 가는 링크가 하나도 남지 않는다 — 글자만 남는다."""
    mod = _load()
    text = mod.public_readme((ROOT / "README.md").read_text(encoding="utf-8"))
    links = re.findall(r"\]\(([^)]+)\)", text)
    leaked = [t for t in links if t.startswith(mod.PRIVATE_LINK_PREFIXES)]
    assert leaked == [], leaked


def test_public_readme_keeps_diagrams_and_screens() -> None:
    """다이어그램 4개와 화면 표의 이미지는 메인과 같다."""
    mod = _load()
    src = (ROOT / "README.md").read_text(encoding="utf-8")
    out = mod.public_readme(src)
    assert out.count("```mermaid") == src.count("```mermaid") == 4
    assert set(re.findall(r"docs/readmeimage/\w+\.png", out)) == set(
        re.findall(r"docs/readmeimage/\w+\.png", src)
    )


def test_public_readme_public_only_sections_and_numbering() -> None:
    """매매법 붙이기 · 라이선스 절이 있고, 번호 절은 1부터 빠짐없이 이어진다."""
    mod = _load()
    out = mod.public_readme((ROOT / "README.md").read_text(encoding="utf-8"))
    assert "## " in out and "매매법 붙이기" in out and "## " in out and "라이선스" in out
    numbers = [int(n) for n in re.findall(r"^## (\d+)\. ", out, re.M)]
    assert numbers == list(range(1, len(numbers) + 1)), numbers
    assert "견본 매매법 하나" in out
    assert "시초가 박스" not in out


def test_public_readme_is_derived_on_export(tmp_path: Path) -> None:
    """`public/README.md` 가 없으면 내보내기가 README 를 파생한다."""
    mod = _load()
    src = tmp_path / "src"
    (src / "public").mkdir(parents=True)
    (src / "README.md").write_text(
        "# 제목\n\n| 규모 (오늘) | |\n|---|---|\n| a | b |\n\n**목차**\n\n---\n\n"
        "## 화면\n\n표\n\n## 1. 전체 구조\n\n"
        "본문 [비공개](docs/strategy/x.md) 와 [공개](docs/platform/y.md)\n",
        encoding="utf-8",
    )
    dest = tmp_path / "dest"
    mod.export(dest, src=src, tracked=["README.md"], overlay=src / "public")
    got = (dest / "README.md").read_text(encoding="utf-8")
    assert "[비공개]" not in got and "비공개 와" in got
    assert "[공개](docs/platform/y.md)" in got
    assert "라이선스" in got
