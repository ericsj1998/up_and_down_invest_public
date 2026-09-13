"""연준 회의 일정 페이지 → `config/calendar.yml` 의 `fomc.dates` (T276 · 2026-09-13).

    uv run python scripts/ops/sync_fomc.py            # 페이지를 읽고 다르면 파일을 고친다
    uv run python scripts/ops/sync_fomc.py --check    # 고치지 않고 다르면 종료 1 (CI · 점검)

연준은 일정을 API 로 주지 않는다(FRED 101 은 자료 갱신일이라 매일 한 줄). 그래서 페이지를 긁되
**앱이 페이지에 기대지 않는다** — 앱은 파일만 읽고, 이 배치가 파일을 채운다. 파일에 값이 있으니
무엇을 보여 주는지 항상 눈으로 확인된다.

🔴 페이지에서 회의를 하나도 못 읽으면 "일정 없음" 이 아니라 **실패(종료 2)** 다. 구조가 바뀐
것이지 회의가 없어진 것이 아니다(규칙 #8).

남기는 범위: 올해와 그 뒤 연도 전부(페이지는 보통 다음 해까지 준다). 지난해는 뺀다 — 달력은 앞을
본다.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from updown.marketdata.calendar.config import load_calendar_config  # noqa: E402
from updown.marketdata.calendar.fomc import parse_fomc_page  # noqa: E402

PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
CONFIG = ROOT / "config" / "calendar.yml"
USER_AGENT = "Mozilla/5.0 UpAndDownInvest/1.0"
_DATES_BLOCK = re.compile(
    r"(?P<head>^  dates:\n)(?P<rows>(?:    - \d{4}-\d{2}-\d{2}\n)+)",
    re.MULTILINE,
)


def fetch_page() -> str:
    """페이지 원문을 받는다.

    Returns:
        HTML.

    Raises:
        httpx.HTTPError: 못 받았다 — 조용히 빈 문자열로 만들지 않는다.
    """
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=20.0, headers=headers, follow_redirects=True) as http:
        response = http.get(PAGE)
        response.raise_for_status()
        return response.text


def rewrite(text: str, dates: list[str]) -> str:
    """설정 본문에서 `fomc.dates` 목록만 바꾼다 — 주석·다른 절은 그대로.

    Args:
        text: `calendar.yml` 본문.
        dates: ISO 날짜들(정렬돼 있어야 한다).

    Returns:
        새 본문.

    Raises:
        ValueError: `dates:` 블록을 못 찾았다 — 파일 모양이 바뀌었으면 사람이 본다.
    """
    rows = "".join(f"    - {d}\n" for d in dates)
    new, count = _DATES_BLOCK.subn(lambda m: m.group("head") + rows, text, count=1)
    if count != 1:
        raise ValueError("calendar.yml 에서 fomc.dates 블록을 못 찾았다")
    return new


def main(argv: list[str] | None = None) -> int:
    """페이지 → 파일.

    Args:
        argv: 명령행. None 이면 `sys.argv`.

    Returns:
        0 같거나 고침 · 1 `--check` 에서 다름 · 2 페이지를 못 읽음.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="고치지 않는다. 다르면 종료 1")
    args = parser.parse_args(argv)

    try:
        html = fetch_page()
    except httpx.HTTPError as exc:
        print(f"🔴 연준 페이지를 못 받았다: {type(exc).__name__}")
        return 2
    meetings = parse_fomc_page(html)
    if not meetings:
        print("🔴 페이지에서 회의를 하나도 못 읽었다 — 구조가 바뀌었다.")
        print("   파서(marketdata/calendar/fomc.py)와 픽스처를 본다.")
        return 2

    this_year = datetime.now(UTC).year
    wanted = sorted({m.decides.isoformat() for m in meetings if m.decides.year >= this_year})
    current = [d.isoformat() for d in load_calendar_config(CONFIG).fomc.dates]
    print(f"페이지: {len(meetings)}회 읽음 · {this_year}년 이후 {len(wanted)}회")
    if wanted == current:
        print("✅ calendar.yml 과 같다")
        return 0

    gone = sorted(set(current) - set(wanted))
    new = sorted(set(wanted) - set(current))
    print("차이 —", f"빠짐 {gone}" if gone else "", f"새로 {new}" if new else "")
    if args.check:
        return 1
    CONFIG.write_text(rewrite(CONFIG.read_text(encoding="utf-8"), wanted), encoding="utf-8")
    print(f"✍️  calendar.yml fomc.dates 를 {len(wanted)}개로 고쳤다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
