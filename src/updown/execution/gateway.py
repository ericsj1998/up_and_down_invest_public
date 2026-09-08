"""`OrderGateway` — 브로커 어댑터를 획득하는 **유일한 지점** (spec §12.4, plan D-12).

> ⚠️ **절대 규칙 #0** — 주문 경로는 브로커 어댑터를 직접 생성하거나 import 하지 않는다.
> 반드시 `OrderGateway.resolve_adapter()` 를 통해 얻는다.

단순 판정 함수보다 강한 구조를 택한 이유: 판정 함수는 **부르지 않으면 우회된다.**
어댑터를 얻는 경로 자체를 하나로 좁히면 우회할 곳이 없어진다.
`tests/test_gateway_bypass.py` 가 이 독점을 정적으로 강제한다.

## 🔴 2026-09-04 — LIVE 분기가 열렸다 (T157 · 사용자 결정: 소액 실거래를 G1 검증으로)

`live_adapter()` 가 `APP_ENV=live` + `LIVE_ORDERS=1` + `GATE_API_*` 셋을 요구해 실계좌 어댑터를
만들고, `OrderGateway(env, live=...)` 는 live 환경에서만 그것을 받는다. 호출부는 `order_adapter()`
하나만 부른다 — 환경이 문을 정하고, 스위치가 켜졌는데 키가 없으면 페이퍼로 떨어지지 않고 죽는다.
아래 표는 그 전(Phase 0~1)의 기록이다.

## Phase 0~1 의 상태 (기록)

**어떤 조건에서도 어댑터를 반환하지 않는다.** 두 경로 모두 예외다:

| 판정 | Phase 0~1 결과 | 이유 |
|------|---------------|------|
| `LIVE` | `LiveOrderBlockedError` | 실주문 어댑터를 붙이지 않았다. G1 미달 상태의 실거래는 금지다 |
| `PAPER` | `PaperAdapterUnavailableError` | `PaperAdapter` 가 아직 없다 (P2-1) |

예외를 **둘로 나눈 것이 중요하다.** P2-1 에서 바뀌는 것은 `PAPER` 분기뿐이고,
`LIVE` 분기는 G1 통과 + P2-8 까지 그대로 남아야 한다. 하나의 예외로 뭉치면 그때
둘을 함께 여는 실수가 생긴다.
"""

import os

from updown.common.config import AppEnv
from updown.common.security.live_gate import RoutingTarget, UserLiveToggle, decide_routing
from updown.marketdata.adapter import BrokerAdapter


class OrderGatewayError(RuntimeError):
    """게이트가 어댑터를 제공할 수 없다."""


class LiveOrderBlockedError(OrderGatewayError):
    """실주문이 물리적으로 차단되어 있다 (plan D-12).

    Note:
        이 예외가 사라지는 시점은 **G1 통과 + P2-8 실거래 개시**다. Phase 0~1 동안
        이것이 발생하는 것은 정상이며, 오히려 발생하지 않는다면 게이트가 뚫린 것이다.
    """


class PaperCredentialsError(OrderGatewayError):
    """페이퍼 자격증명이 없다.

    Note:
        ⛔ 빈 시크릿으로 진행하지 않는다 — 그렇게 만든 서명은 형태가 멀쩡해서 401 이
        날 때까지 알 수 없다 (절대 규칙 #8).
    """


class PaperAdapterUnavailableError(OrderGatewayError):
    """페이퍼 어댑터가 아직 없다 (P2-1 에서 도입).

    Note:
        Phase 0~1 에서는 폴백 대상이 존재하지 않는다. 이 예외가 "실주문 절대 불가"의
        실체다 — 게이트가 페이퍼로 라우팅하려 해도 줄 것이 없다.
    """


class OrderGateway:
    """브로커 어댑터 획득의 단일 관문 (spec §12.4).

    Note:
        생성자가 `AppEnv` 만 받고 어댑터 레지스트리를 받지 않는 것은 의도다.
        Phase 0~1 에는 줄 수 있는 어댑터가 없고, 주입 지점을 미리 열어 두면
        "테스트용으로만" 실주문 어댑터를 꽂는 경로가 생긴다.
        P2-1 에서 `PaperAdapter` 를 붙일 때 명시적으로 확장한다.
    """

    def __init__(
        self,
        env: AppEnv,
        *,
        paper: BrokerAdapter | None = None,
        live: BrokerAdapter | None = None,
    ) -> None:
        """게이트를 만든다.

        Args:
            env: 실행 환경. 설정에서 온 값이며 여기서 바꿀 수 없다.
            paper: 페이퍼 주문 어댑터. None 이면 페이퍼 경로도 예외다.
            live: 실계좌 주문 어댑터 — **`env` 가 live 일 때만 받는다** (T157 · 2026-09-04).

        Raises:
            ValueError: live 어댑터를 live 가 아닌 환경에 넣은 경우.

        Note:
            🔴 **게이트가 어댑터를 직접 만들지 않고 받는다.** 만들게 하면 이 파일이
            자격증명을 읽는 파일이 되고, 그 다음에는 라이브 키도 읽을 수 있게 된다 —
            "테스트용으로만" 실주문 어댑터를 꽂는 경로가 바로 그렇게 생긴다.

            `live` 인자는 사용자 결정(2026-09-04 · 소액 실거래를 G1 검증으로)으로 열렸다.
            만드는 함수는 `live_adapter()` 하나이고, 그것은 APP_ENV=live · LIVE_ORDERS=1 ·
            GATE_API_* 셋을 요구한다. dev·paper 프로세스에 실계좌 어댑터가 들어오는 것은
            판정이 PAPER 라 안 쓰이더라도 **오배선**이므로 여기서 거부한다.
        """
        if live is not None and env is not AppEnv.LIVE:
            raise ValueError(
                f"라이브 어댑터는 APP_ENV=live 에서만 주입할 수 있다 (env={env.value}). "
                "dev·paper 프로세스에 실계좌 어댑터가 들어온 것은 오배선이다 (spec §12.4)"
            )
        self._env = env
        self._paper = paper
        self._live = live

    @property
    def env(self) -> AppEnv:
        """이 게이트가 판정에 쓰는 환경."""
        return self._env

    def resolve_adapter(self, toggle: UserLiveToggle) -> BrokerAdapter:
        """이중 게이트를 통과시켜 브로커 어댑터를 돌려준다.

        Args:
            toggle: 사용자 live 토글.

        Returns:
            주문에 사용할 어댑터.

        Raises:
            LiveOrderBlockedError: 판정이 `LIVE` 인 경우 — Phase 0~1 은 실주문 어댑터가
                존재하지 않는다.
            PaperAdapterUnavailableError: 판정이 `PAPER` 인 경우 — `PaperAdapter` 가
                P2-1 에서 도입된다.

        Note:
            **Phase 0~1 에서는 진리표 4케이스 전부가 예외로 끝난다.** 반환 타입이
            `BrokerAdapter` 인데 실제로 반환하는 경로가 없는 것은 미완성이 아니라
            **의도된 차단 상태**다 (plan D-12).

            이중 안전: 설령 이 게이트가 뚫려도 Phase 0 의 `UpbitAdapter` 는 조회 전용이라
            `submit_order` 가 미구현 예외다 (P0-7-6).
        """
        target = decide_routing(self._env, toggle)

        if target is RoutingTarget.LIVE:
            # 🔴 **여기가 열렸다** (T157 · 2026-09-04 사용자 결정: 소액 실거래를 G1 검증으로).
            #    주입된 라이브 어댑터가 있을 때만이다 — `live_adapter()` 만 만들 수 있고,
            #    그 함수는 APP_ENV=live + LIVE_ORDERS=1 + GATE_API_* 셋을 요구한다.
            if self._live is not None:
                return self._live
            raise LiveOrderBlockedError(
                f"실주문이 차단되어 있다 (user={toggle.user_id}, env={self._env.value}). "
                "라이브 어댑터가 주입되지 않았다 — `live_adapter()` 는 APP_ENV=live · "
                "LIVE_ORDERS=1 · GATE_API_KEY/SECRET 셋이 다 있어야 돌려준다 (T157)."
            )

        # 🔴 **여기가 열렸다** (T13 · 2026-08-17). 페이퍼 경로만이다.
        #
        #    ⛔ 위 LIVE 분기는 손대지 않았다. 두 예외를 따로 둔 이유가 이것이고,
        #      `tests/test_live_gate.py` 가 "페이퍼만 열리고 실주문은 남는다" 를 검증한다.
        #
        #    잠금 셋 (`execution/gate_paper.py`):
        #      1. `GatePaperAdapter` 생성자가 **testnet 이 아닌 클라이언트를 거부**한다
        #      2. 자격증명은 `GATE_TESTNET_*` 만 읽는다 — 라이브 키 이름은 안 본다
        #      3. 잔고 브로커 이름이 `gate-testnet` 이라 페이크 성적이 실적으로 안 섞인다
        if self._paper is not None:
            return self._paper

        raise PaperAdapterUnavailableError(
            f"페이퍼 라우팅으로 판정됐으나 페이퍼 어댑터가 주입되지 않았다 "
            f"(user={toggle.user_id}, env={self._env.value}). "
            "`OrderGateway(env, paper=...)` 로 넘긴다 — 게이트가 자격증명을 직접 읽지 "
            "않는 이유는, 읽으면 이 파일이 키를 아는 파일이 되고 그 다음에는 라이브 키도 "
            "읽을 수 있게 되기 때문이다 (spec §12.4)."
        )


def paper_adapter(quotes: object) -> BrokerAdapter:
    """Testnet 페이퍼 어댑터를 만든다 — **여기서만** 만들 수 있다.

    Args:
        quotes: 공개 조회 어댑터 (`GateAdapter`). 조회는 여기에 위임한다.

    Returns:
        주문 가능한 페이퍼 어댑터.

    Raises:
        PaperCredentialsError: `GATE_TESTNET_*` 가 없는 경우.
        TypeError: `quotes` 가 Gate 조회 어댑터가 아닌 경우.

    Note:
        🔴 **AST 가드가 이 파일만 허용한다** (절대 규칙 #0 · `test_gateway_bypass.py`).
        API 층이 `GatePaperAdapter` 를 직접 만들려 하자 가드가 잡았다 — 그것이 맞다.
        구체 어댑터를 아는 파일이 늘면 게이트가 관문이 아니게 된다.

        ⚠️ **그래서 이 파일이 자격증명을 읽는다.** 앞서 "게이트가 키를 알면 안 된다" 고
        적었는데, 규칙 #0 이 더 센 제약이라 그쪽을 따른다. 대신 **`GATE_TESTNET_*` 만**
        읽는다 — 라이브 키 이름(`GATE_API_*`)은 이 함수가 모른다.

        ⛔ 라이브 어댑터를 만드는 함수는 **없다.** 만들면 이 파일이 실주문을 낼 수 있게
        되고, `resolve_adapter` 의 LIVE 차단이 뜻을 잃는다.
    """
    from updown.execution.binance_paper import BinancePaperAdapter
    from updown.execution.gate_paper import GatePaperAdapter
    from updown.marketdata.binance.adapter import BinanceAdapter
    from updown.marketdata.binance.trade_client import BinanceTradeClient
    from updown.marketdata.gate.adapter import GateAdapter
    from updown.marketdata.gate.trade_client import GateTradeClient

    if isinstance(quotes, BinanceAdapter):
        # T62 — 바이낸스 testnet. Gate 와 같은 3중 잠금: 기본 base 가 testnet 인
        # 클라이언트 + 생성자 거부 + TESTNET 키만 읽기.
        key = os.environ.get("BINANCE_TESTNET_API_KEY", "").strip()
        secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "").strip()
        if not key or not secret:
            raise PaperCredentialsError(
                "BINANCE_TESTNET_API_KEY / _SECRET 가 없다 — `.env.dev` 에 넣고 API 를 "
                "다시 띄운다. 라이브 키 이름은 이 경로가 읽지 않는다 (spec §8)"
            )
        return BinancePaperAdapter(BinanceTradeClient(key, secret), quotes)

    if not isinstance(quotes, GateAdapter):
        raise TypeError(
            f"Gate/Binance 조회 어댑터가 필요하다 — 받은 것: {type(quotes).__name__}. "
            "페이퍼 어댑터는 조회를 위임하므로 짝이 맞아야 한다"
        )
    key = os.environ.get("GATE_TESTNET_API_KEY", "").strip()
    secret = os.environ.get("GATE_TESTNET_API_SECRET", "").strip()
    if not key or not secret:
        raise PaperCredentialsError(
            "GATE_TESTNET_API_KEY / _SECRET 가 없다 — `.env.dev`/`.env.demo` 에 넣고 API 를 다시 "
            "띄운다. 라이브 키 이름(GATE_API_*)은 이 경로가 읽지 않는다 (spec §8)"
        )
    # ⛔ base_url 을 넘기지 않는다 — `GateTradeClient` 기본값이 testnet 이고,
    #    `GatePaperAdapter` 가 testnet 아닌 클라이언트를 거부한다 (이중 잠금).
    return GatePaperAdapter(GateTradeClient(key, secret), quotes)


class LiveCredentialsError(OrderGatewayError):
    """실계좌 자격증명이 없다 — 빈 키로 서명하지 않는다 (절대 규칙 #8)."""


LIVE_ORDERS_FLAG = "LIVE_ORDERS"
"""운영자 스위치 — `.env.live` 에 `LIVE_ORDERS=1`. 키가 있어도 이것이 꺼져 있으면 테스트넷이다."""


def live_adapter(quotes: object) -> BrokerAdapter:
    """Gate **실계좌** 어댑터 — **여기서만** 만들 수 있다 (T157 · 2026-09-04).

    세 조건을 **전부** 요구한다. 하나라도 빠지면 어댑터가 아니라 예외다:

    1. `APP_ENV=live` — dev·paper 프로세스는 실키가 있어도 못 만든다
    2. `LIVE_ORDERS=1` — 운영자 스위치. 키를 넣어 두고도 테스트넷으로 돌리는 날이 있다
    3. `GATE_API_KEY` / `GATE_API_SECRET` — `Settings` 의 SecretStr 로만 읽는다

    Args:
        quotes: 공개 조회 어댑터 (`GateAdapter`). 조회는 위임한다 — 페이퍼와 같다.

    Returns:
        실계좌 주문 어댑터.

    Raises:
        LiveOrderBlockedError: 환경이 live 가 아니거나 스위치가 꺼져 있다.
        LiveCredentialsError: 실키가 없다.
        TypeError: Gate 조회 어댑터가 아니다 — **Binance 실계좌는 없다** (사용자 결정: Gate 만).
    """
    from updown.common.config import load_settings
    from updown.execution.gate_paper import GateLiveAdapter
    from updown.marketdata.gate.adapter import GateAdapter
    from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

    settings = load_settings()
    if settings.app_env is not AppEnv.LIVE:
        raise LiveOrderBlockedError(
            f"실계좌 어댑터는 APP_ENV=live 에서만 만든다 (env={settings.app_env.value})"
        )
    if not settings.live_orders:
        raise LiveOrderBlockedError(
            f"운영자 스위치가 꺼져 있다 — `.env.live` 에 {LIVE_ORDERS_FLAG}=1 을 넣어야 실주문이다"
        )
    if not isinstance(quotes, GateAdapter):
        raise TypeError(
            f"실계좌는 Gate 만 연다 (사용자 결정 2026-09-04) — 받은 것: {type(quotes).__name__}"
        )
    if settings.gate_api_key is None or settings.gate_api_secret is None:
        raise LiveCredentialsError(
            "GATE_API_KEY / GATE_API_SECRET 가 없다 — `.env.live` 에 넣는다 (선물 R/W · 출금 OFF · "
            "IP 화이트리스트). 빈 키로는 만들지 않는다 (규칙 #8)"
        )
    client = GateTradeClient(
        settings.gate_api_key.get_secret_value(),
        settings.gate_api_secret.get_secret_value(),
        base_url=LIVE_BASE_URL,
    )
    return GateLiveAdapter(client, quotes)


def order_adapter(quotes: object, *, user_id: str) -> BrokerAdapter:
    """호출부가 쓰는 **유일한** 주문 어댑터 획득 경로 — 환경이 문을 정한다.

    | APP_ENV | LIVE_ORDERS | 결과 |
    |---|---|---|
    | dev · paper | 무관 | 테스트넷 (`paper_adapter`) |
    | live | 0 / 없음 | 테스트넷 |
    | **live** | **1** | **실계좌** (`live_adapter`) — 키가 없으면 예외 (페이퍼로 안 떨어진다) |

    Args:
        quotes: 공개 조회 어댑터.
        user_id: 이중 게이트의 사용자 식별자 (로그·예외 메시지용).

    Returns:
        환경에 맞는 주문 어댑터.

    Note:
        ⛔ live 에서 스위치가 켜졌는데 키가 없으면 **예외**다. 페이퍼로 떨어뜨리면 "실거래인
        줄 알았는데 페이크머니" 가 되고 그 성적을 실적으로 적게 된다 (spec §12.4).
    """
    from updown.common.config import load_settings

    settings = load_settings()
    env = settings.app_env
    armed = env is AppEnv.LIVE and settings.live_orders
    toggle = UserLiveToggle(user_id=user_id, live_enabled=armed)
    # ⭐ **어댑터(=서명 클라이언트·연결 풀)를 재사용한다** (2026-09-05 · 1 GB 서버 실측).
    #    호출마다 새 `GateTradeClient` 를 만들면 요청마다 TLS 핸드셰이크가 새로 일어난다 —
    #    콘솔 폴링 2 req/s 에서 그것이 CPU 의 큰 몫이었다(10분에 어댑터 생성 172회).
    #    키는 (환경 · 스위치 · 조회 어댑터 종류). 시크릿이 바뀌면 프로세스를 다시 띄우는 것이
    #    맞으므로 캐시는 프로세스 수명이다.
    cache_key = (env.value, armed, type(quotes).__name__)
    cached = _ORDER_ADAPTERS.get(cache_key)
    if cached is None:
        live = live_adapter(quotes) if armed else None
        paper = None if armed else paper_adapter(quotes)
        cached = OrderGateway(env, paper=paper, live=live).resolve_adapter(toggle)
        _ORDER_ADAPTERS[cache_key] = cached
    return cached


_ORDER_ADAPTERS: dict[tuple[str, bool, str], BrokerAdapter] = {}
"""`order_adapter` 의 프로세스 수명 캐시 — 같은 환경·스위치·거래소면 같은 어댑터(연결 풀)를 준다."""
