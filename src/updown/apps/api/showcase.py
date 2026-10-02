"""쇼케이스 모드 — 포트폴리오용 데모 API 를 CPU 를 거의 안 쓰고 띄운다 (사용자 2026-10-02).

사용자: *"이직 준비 중이라 포폴에 이 사이트를 올렸는데 데모가 꺼져 있다 · CPU 소모를 줄이면서
동작하게."* 데모는 2026-09-30 에 꺼졌다 — 1 GB · 기준선 10% 서버에서 데모가 되살린 판(테스트넷
펀드 6판)의 봉 조회 · 예비 신호 훑기 · 대조 루프가 버스트 크레딧을 갉아, 실계좌 api 가 스틸로
리더 락을 잃었다(세 번).

쇼케이스는 **거래 리더를 잡지 않는다** — 그래서 판 · 펀드 되살리기 · 리밸런싱 · 대조 · 예비 신호 ·
알림 · 리포트 루프가 하나도 안 돈다(`main._autostart_and_loops` 전체). 화면 · 차트 · 분석 · 백테스트
조회 · 기록은 그대로 뜬다(전부 요청이 올 때만 일한다). 대신 판 · 펀드를 **새로 띄우는 거래 요청은
안내와 함께 거절**한다 — 리더 없이 받으면 아무도 관리하지 않는 판이 생긴다.

켜짐: `UPDOWN_SHOWCASE=1` **이고 실계좌가 아닐 때만**(`LIVE_ORDERS` · `STOCK_LIVE_ORDERS` 가 1 이면
무시) — 실계좌 api 에 잘못 붙어도 거래가 멈추지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

FLAG = "UPDOWN_SHOWCASE"
MESSAGE = (
    "포트폴리오 데모(쇼케이스)입니다 — 판 · 펀드 실행은 꺼져 있습니다. "
    "화면 · 차트 · 분석 · 기록은 그대로 둘러볼 수 있습니다."
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
