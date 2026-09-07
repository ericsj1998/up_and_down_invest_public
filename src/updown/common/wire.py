"""화면으로 나가는 **전선 위의 모양** — 한 곳에서 짓는다 (2026-08-30).

## 왜 생겼나

사용자 질문: *"막 코드 하드코딩되어있고 다 따로 쓰고 그러고 있는 거 아니지?"*

세어 보니 봉 하나를 사전으로 만드는 코드가 **여섯 곳**에 있었고, 여섯 개가 글자까지
똑같았다:

    ai_analysis.py:701   analysis.py:237   analysis.py:357
    live_stream.py:134   walkforward.py:1473   walkforward.py:5068

지금은 같지만 **같게 유지될 이유가 없다.** 한 곳에 필드를 더하면 나머지 다섯은 안
따라오고, 화면은 같은 봉을 어디서 받았느냐에 따라 다르게 읽는다.

## ⚠️ 왜 `common/` 인가 — 처음에 `apps/api/` 에 뒀다가 옮겼다

거기 두니 **가장 큰 경로가 못 썼다.** RUN 차트에 800봉을 보내는
`orchestration/inspection/snapshot.py` 는 `apps/` 를 import 할 수 없다 (의존 방향은
CI 가 강제한다). 그래서 "한 곳으로 모았다" 고 해 놓고 **정작 제일 많이 쓰는 입구가
남아 있었다** (사용자 감사 2026-08-30 에 걸렸다).

⇒ 전선 위의 모양은 **모두가 쓰는 것**이므로 가장 아래 층에 둔다.

## 🔴 숫자를 문자열로 낸다

`Decimal` 을 float 로 내리면 화면이 반올림한 값을 다시 보내고, 그 값으로 주문이
나간다 — 값이 오가며 **조용히 달라진다**. 이 프로젝트는 호가 단위가 틀린 주문이
거절되는 사고를 겪었다.
"""

from __future__ import annotations

from typing import Any

from updown.common.domain.candle import Candle


def candle_json(candle: Candle, *, closed: bool | None = None) -> dict[str, Any]:
    """봉 하나를 화면이 읽는 모양으로.

    Args:
        candle: 도메인 캔들.
        closed: 마감된 봉인가. **실시간 스트림만** 넘긴다 — 요청-응답 입구는 이미
            마감된 것만 주므로 이 필드가 뜻을 갖지 않는다.

    Returns:
        `{ts, open, high, low, close, volume}` (+ `closed` 를 준 경우 그것).

    Note:
        ⚠️ **시각은 ISO 문자열이고 UTC 다** (절대 규칙 #7). 표시만 KST 로 바꾸는 것은
        화면의 일이다 — 여기서 바꾸면 어느 시각인지 받는 쪽이 알 수 없다.

        ⛔ `closed` 를 **기본값으로 채우지 않는다.** 안 준 곳에 `True` 를 박으면
        "마감됐다" 는 사실이 아닌 곳에서도 사실처럼 나간다.
    """
    body: dict[str, Any] = {
        "ts": candle.ts.isoformat(),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
    }
    if closed is not None:
        body["closed"] = closed
    return body
