"""바이낸스 시세를 **어디서 볼 것인가** — 주문이 나가는 곳을 따라간다 (2026-08-30).

사용자 2026-08-30: *"테스트넷 콘솔은 테스트넷 쓰는거고, 라이브로 넘어갔을 때 그걸로
쓰는게 맞지."*

## 🔴 왜 원래 라이브로 박혀 있었나 — 그리고 왜 틀렸나

Gate 를 따라 *"조회는 라이브를 본다"* 로 맞춰 뒀다. Gate 에서는 그것이 옳다: Gate
testnet 호가창은 라이브와 다르고, Gate 라이브 웹소켓은 멀쩡히 돈다.

**바이낸스에서는 같은 선택이 두 가지를 한꺼번에 깼다:**

    ① 라이브 선물 WS 가 이 네트워크에서 **데이터를 안 준다**
       구독은 성공하는데(`{"result": null, "id": 1}`) kline 이 0건이다.
       실측: 경로구독 · 합친스트림 · aggTrade **전부 0건**.
       → 20초 REST 폴링으로 내려갔고, 차트가 20초에 한 번 움직였다

    ② 주문은 testnet 에서 체결되는데 화면은 **라이브 호가**를 그렸다
       다른 시장을 보면서 이 시장에 주문하는 것이다. 이 프로젝트는 그 어긋남으로
       이미 사고를 냈다 — 라이브 명세로 만든 손절가가 testnet 에서 거절됐고,
       20배 숏이 손절 없이 굴렀다

## 실측 (2026-08-30 · BTCUSDT)

    라이브 선물 WS    60초에 **0건**
    현물 WS           30건 · 라이브 선물 대비 괴리 중앙 +0.011%
    testnet 선물 WS   **113건** · 괴리 중앙 -0.025% · 최대 +0.069%
    testnet REST      1m·5m·15m·1h·4h 전부 400봉 · **거래 0인 봉 0%**

⇒ testnet 은 봉도 충분하고 라이브를 바짝 따라간다. 그리고 **우리 주문이 거기서
  체결된다** — 차트가 보여줘야 할 호가창이 바로 그것이다.

## ⚠️ 과거와 진행 중 봉은 **같은 곳**에서 와야 한다

REST 만 testnet 으로 바꾸고 소켓을 라이브로 두면(또는 그 반대), 오른쪽 끝에서
0.05% 짜리 이음매가 생긴다. 그래서 이 모듈이 **둘을 같이** 정한다.
"""

from __future__ import annotations

from updown.common.config import AppEnv, load_settings

LIVE_BASE = "https://fapi.binance.com"
"""라이브 USDT-M 선물 REST."""

TESTNET_BASE = "https://testnet.binancefuture.com"
"""testnet USDT-M 선물 REST — 주문이 나가는 곳과 같다."""

LIVE_WS = "wss://fstream.binance.com/ws"
"""라이브 선물 소켓.

⛔ **이 네트워크에서는 조용하다** (2026-08-30 실측 — 구독은 되고 kline 0건).
라이브로 넘어갈 때 이 사실을 다시 재야 한다: 그때도 조용하면 라이브 차트가 20초
폴링으로 떨어진다.
"""

TESTNET_WS = "wss://fstream.binancefuture.com/ws"
"""testnet 선물 소켓 — 실측 60초에 113건."""


def _live() -> bool:
    """지금 실주문 환경인가.

    Returns:
        참이면 라이브. 못 읽으면 **거짓**(testnet).

    Note:
        ⛔ **모르면 testnet 이다.** 설정을 못 읽었을 때 라이브로 떨어지면, 그 실수가
        실계좌 시세로 화면을 그리면서 testnet 에 주문을 내는 상태를 만든다 — 정확히
        지금 고치는 그 어긋남이다. 안전한 쪽으로 틀린다.
    """
    try:
        return load_settings().app_env is AppEnv.LIVE
    except Exception:
        return False


def binance_base() -> str:
    """시세 REST 베이스 — 주문 환경을 따른다.

    Returns:
        실계좌면 라이브 URL, 아니면 테스트넷 URL. 시세와 주문이 다른 곳을 보면 호가 단위가 어긋난다.
    """
    return LIVE_BASE if _live() else TESTNET_BASE


def binance_ws() -> str:
    """시세 소켓 URL — REST 와 **같은 곳**이어야 한다.

    Returns:
        `binance_base` 와 같은 환경의 WS URL.
    """
    return LIVE_WS if _live() else TESTNET_WS
