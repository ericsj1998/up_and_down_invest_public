"""리포트 기간별 지갑 그래프 재료 프로브 (읽기 전용) — api 컨테이너 안에서 돈다.

`bash scripts/ops/remote.sh scripts/ops/probe_wallet.py`.
1.20.1 배포 뒤 `load_wallet` 이 실계좌 장부로 넣은 돈 · 지갑 잔고 · 번 돈을 내는지
24시간 · 7일 · 30일 로 본다. 요약 금액만 — 키는 안 찍는다.
"""

import asyncio

from updown.orchestration.report.daily import load_wallet, window_for


async def main() -> None:
    for hours in (24, 168, 720):
        got = await load_wallet(window_for(hours))
        if got is None:
            print(f"{hours}h: None — 장부를 못 읽었다")
            continue
        print(
            f"{hours}h: 점 {len(got['points'])} · 넣은 돈 {got['principal']} · "
            f"지갑 {got['balance']} · 번 돈 {got['earned']} · "
            f"구간 입출금 {got['deposits']} · 시작까지 닿음 {got['reached']}"
        )


asyncio.run(main())
