"""다크 모드 대비 검사 — app.css 에 **박힌 어두운 글자색**을 찾는다 (2026-09-12).

    uv run python scripts/dev/dark_contrast.py

밝은 테마에서 고른 색(#a12226 같은)이 다크 바탕에 그대로 남으면 테두리만 보이고 글자는 안 읽힌다
(사용자 신고 2026-09-12 "붉은 버튼 색깔이 다크모드에서 가독성이 떨어진다"). WCAG 상대 휘도로 재서
다크 바탕(#0e141b 바탕 · #161d26 카드) 대비 3 미만인 것만 보고한다.

⚠️ **light 규칙 줄을 가리킨다.** 그 선택자에 `[data-theme="dark"]` 대응이 이미 있으면 문제가 아니다 —
   보고된 선택자로 다크 규칙이 있는지 같이 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path("/home/ericsj1998/projects/up_and_down_invest/web/src/app.css")
CANVAS = (0x0E, 0x14, 0x1B)
CARD = (0x16, 0x1D, 0x26)


def lum(rgb: tuple[int, int, int]) -> float:
    """WCAG 상대 휘도."""
    out = 0.0
    for value, weight in zip(rgb, (0.2126, 0.7152, 0.0722), strict=True):
        s = value / 255
        out += weight * (s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return out


def ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """두 색의 대비비."""
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def rgb_of(text: str) -> tuple[int, int, int] | None:
    """`#rgb` · `#rrggbb` 를 튜플로. 아니면 None."""
    got = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", text.strip())
    if got is None:
        return None
    raw = got.group(1)
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


lines = CSS.read_text(encoding="utf-8").splitlines()
dark_depth = 0
depth = 0
selector = ""
found: list[str] = []
for no, line in enumerate(lines, 1):
    stripped = line.strip()
    if "{" in stripped and not stripped.startswith("/*"):
        head = stripped.split("{")[0].strip()
        if head:
            selector = head
        if 'data-theme="dark"' in head or "prefers-color-scheme: dark" in head:
            dark_depth = depth + 1
        depth += stripped.count("{")
    depth -= stripped.count("}")
    if dark_depth and depth < dark_depth:
        dark_depth = 0
    if dark_depth:
        continue
    got = re.match(r"color:\s*(#[0-9a-fA-F]{3,6})\s*;", stripped)
    if got is None:
        continue
    rgb = rgb_of(got.group(1))
    if rgb is None:
        continue
    worst = min(ratio(rgb, CANVAS), ratio(rgb, CARD))
    if worst < 3:
        found.append(f"  {no:5}  {got.group(1)}  대비 {worst:.1f}  {selector}")

print(f"다크에서 대비 3 미만인 글자색 {len(found)}건")
print("\n".join(found))
