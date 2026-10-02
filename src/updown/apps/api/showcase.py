"""쇼케이스 모드 — 포트폴리오용 데모 API 를 CPU 를 아껴 띄운다 (사용자 2026-10-02).

사용자: *"이직 준비 중이라 포폴에 이 사이트를 올렸는데 데모가 꺼져 있다 · CPU 소모를 줄이면서
동작하게"* · *"그래도 펀드가 동작하긴 해야 할 것 같은데"*. 데모는 2026-09-30 에 꺼졌다 — 1 GB ·
기준선 10% 서버에서 데모가 버스트 크레딧을 갉아, 실계좌 api 가 스틸로 리더 락을 잃었다(세 번).

쇼케이스에서 데모 api 는 **데모 펀드만 돌린다**:

- 돈다: 판 · 펀드 되살리기 · 리밸런싱 · 감시(watchdog) · 거래소 대조 · 하루 한 점 자산 스냅샷.
- 안 돈다: 예비 신호 훑기(가장 무거움 · T331) · 웹 푸시 알림 · 일간 리포트 · AI 채점 · 봉 예열 ·
  메모리 비트 · 자원 비트(in-proc engine).
- 거절: 판 · 펀드를 **새로 띄우거나 바꾸는** 거래 요청(403 + 안내) — 판 수가 고정돼 부하가 안 는다.

CPU 천장(0.25 코어)과 낮은 몫(cpu_shares 128)은 `compose.live.yml` 이 건다 — 방문자가 무엇을
눌러도 데모만 느려지고 실계좌 api 는 CPU 를 먼저 받는다.

켜짐: `UPDOWN_SHOWCASE=1` **이고 실계좌가 아닐 때만**(`LIVE_ORDERS` · `STOCK_LIVE_ORDERS` 가 1 이면
무시) — 실계좌 api 에 잘못 붙어도 거래가 멈추지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

FLAG = "UPDOWN_SHOWCASE"
MESSAGE = (
    "포트폴리오 데모(쇼케이스)입니다 — 데모 펀드는 돌고 있지만, 새 판 · 펀드를 만들거나 "
    "바꾸는 것은 꺼져 있습니다. 화면 · 차트 · 분석 · 기록은 그대로 둘러볼 수 있습니다."
)


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """쇼케이스인가 — 플래그가 정확히 1 이고 실계좌 주문이 꺼져 있을 때만.

    Args:
        environ: 환경 사전. 시험이 넣는다. None 이면 `os.environ`.

    Returns:
        켜져 있으면 True.
    """
    env = os.environ if environ is None else environ
    if env.get(FLAG, "").strip() != "1":
        return False
    return (
        env.get("LIVE_ORDERS", "0").strip() != "1"
        and env.get("STOCK_LIVE_ORDERS", "0").strip() != "1"
    )
