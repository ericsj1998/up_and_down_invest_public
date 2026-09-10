"""마켓 캘린더 대조 프로브 (T259 2차) — api 컨테이너 안에서 돈다. 시크릿은 찍지 않는다.

토스 `/api/v1/market-calendar/{US,KR}` 의 전일·당일·익일 정규장 창과 우리
`config/market_sessions.yml` 판정을 대조한다. 어긋난 날이 있으면 종료 코드 1 — 휴장 목록을 다음
해까지 안 넣었거나 조기마감을 빠뜨린 것이다.

    bash scripts/ops/remote.sh scripts/ops/probe_calendar.py
"""

from __future__ import annotations

import asyncio
import sys

from updown.apps.api import exchange as exchange_api
from updown.common.config import load_settings


async def main() -> int:
    """두 시장을 대조하고 결과를 찍는다."""
    settings = load_settings()
    print("APP_ENV", settings.app_env.value)
    bad = 0
    for market in ("NASDAQ", "KRX"):
        try:
            body = await exchange_api.calendar_check(market)
        except Exception as exc:
            print(f"[{market}] 대조 실패: {str(exc)[:160]}")
            bad += 1
            continue
        days = ", ".join(f"{d['date']}={d['regular'] or '휴장'}" for d in body["days"])
        print(f"[{market}] {days}")
        for f in body["findings"]:
            bad += 1
            print(
                f"  ⚠️ {f['day']} {f['code']}: 우리={f['ours']} · 토스={f['toss']} — {f['detail']}"
            )
        if not body["findings"]:
            print("  ✅ 어긋남 없음")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
