"""누가 무엇을 할 수 있나 — **순수 판정** (사용자 요구 2026-08-30 배포 준비).

> *"외부에서 URL 로 접속할 수 있게 하는 게 목표기 때문에 위험할 수 있잖아."*

지금 API 에는 인증이 **하나도 없다**. `web`(nginx)이 `/api/` 를 그대로 넘기므로,
URL 을 아는 사람은 `POST /walkforward/live`(판 생성) · `/exchange/close-position`(청산) ·
`/rebalancer`(펀드 삭제)를 그대로 부를 수 있다.

## 🔴 기본이 **거부**다

허용 목록을 두고 나머지를 막는다. 반대로 하면(막을 것을 나열) **새 엔드포인트를 만들 때
깜빡한 것이 곧 구멍**이 되고, 이 저장소는 엔드포인트가 계속 는다.

    GET · HEAD      → 읽기 권한
    그 외 전부       → 거래 권한          ← 새 POST 는 **자동으로** 보호된다
    관리자 경로      → 관리자 권한

⚠️ 판정을 순수 함수로 떼어 낸 이유는 `live_gate.py` 와 같다 — 진리표를 DB·HTTP 없이
   시험할 수 있어야 한다. 인증 코드의 버그는 조용하고, 조용한 버그가 제일 비싸다.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """계정 등급.

    Attributes:
        PENDING: 가입은 했지만 **관리자 승인 전**. 읽기만 된다 (사용자 확정 2026-08-30).
        VIEWER: 승인됨 — 읽기. **주문은 못 한다.**
        TRADER: 거래 허용 — 주문·판 생성·청산. 사용자 관리는 못 한다.
        ADMIN: 전부. 가입 승인과 등급 변경은 이 등급만 한다.

    Note:
        🔴 **승인과 거래 권한은 별개다** (사용자 확정 2026-08-30). 가입을 승인하면
        `VIEWER` 가 되고, 주문을 맡기려면 **한 번 더** `TRADER` 로 올려야 한다.

        승인 한 번이 곧 주문 권한이 되면 실수가 비싸다 — 승인은 목록을 훑으며 여러 건을
        빠르게 누르는 동작이고, 그 손놀림으로 실계좌 주문 권한이 나가면 안 된다.

        ⚠️ 그래서 `PENDING` 과 `VIEWER` 는 **할 수 있는 일이 같다.** 승인은
          "존재를 인정한다" 이고, 권한은 등급을 올려야 생긴다. 그 사실을 감추지 않는다.
    """

    PENDING = "pending"
    VIEWER = "viewer"
    TRADER = "trader"
    ADMIN = "admin"
    GUEST = "guest"
    """게스트 — 구글 없이 단추 하나로 들어온 익명 (T221 · 2026-09-05). **읽기만**, 승격 대상 아님.

    ⛔ 이 등급은 **데모 서버(`APP_ENV != live`)에서만 발급**된다. 실계좌 서버는 게스트 이메일
    (`GUEST_EMAIL`)을 쪽지에서 봐도 "로그인 안 함" 으로 친다 (`auth.caller_of`) — 데모 쿠키가 실계좌
    API 에 닿아도 아무것도 못 본다.
    """


GUEST_EMAIL = "guest@demo"
"""게스트 계정의 고정 이메일 — 계정 표(실계좌·데모 공유 · 2026-09-07)에 한 줄로 산다.
실계좌 API 는 이 쪽지를 `is_guest_email` 로 거른다."""


def is_guest_email(email: str) -> bool:
    """게스트 이메일인가 — 실계좌 API 가 쪽지를 거를 때 쓴다.

    Args:
        email: 세션 쪽지의 이메일. 공백·대소문자는 무시한다.

    Returns:
        `@demo` 로 끝나면 True. 고정값 `GUEST_EMAIL` 하나만 보지 않는 이유는 데모 계정이
        늘어도 실계좌 쪽 거름망을 손대지 않기 위해서다.
    """
    return email.strip().lower().endswith("@demo")


class Need(StrEnum):
    """그 요청이 요구하는 것.

    Attributes:
        PUBLIC: 로그인 없이 된다 (health · 로그인 경로 자체).
        READ: 조회.
        TRADE: 돈이 움직이거나 상태가 바뀐다.
        ADMIN: 사용자 관리.
    """

    PUBLIC = "public"
    READ = "read"
    TRADE = "trade"
    ADMIN = "admin"


PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/health/ready",
        "/auth/login",
        "/auth/callback",
        "/auth/me",
        "/auth/logout",
        # T221 — 게스트 입장. 데모 서버만 답한다(실계좌 서버는 404). 응답에 계좌 값은 없다.
        "/auth/guest",
        # 보류된 사람이 관리자에게 문의하는 창구 — 라우트 안에서 로그인 여부를 본다 (2026-09-07).
        "/auth/contact",
    }
)
"""로그인 없이 닿아도 되는 경로.

⚠️ `/auth/me` 가 여기 있는 이유: 화면이 **로그인 여부를 물어볼 창구**가 필요하다.
   응답은 "누구인가" 뿐이고 거래 데이터가 없다.

⛔ 여기에 경로를 더할 때는 **그 응답에 계좌·성적·주문이 한 톨도 없어야** 한다.
"""

SELF_SERVICE_PREFIXES = ("/auth/tokens", "/mcp", "/chart-order")
"""로그인만 하면 되는 경로 (T263 · `caps.SELF_SERVICE_PREFIXES` 와 같은 목록).

개인 토큰 관리 · MCP 끝점.
"""

ADMIN_PREFIXES = (
    "/auth/users",
    "/auth/contacts",
    "/auth/settings",
    "/auth/roles",
    "/admin/logs",
    "/admin/resources",
    # 보안 점검 #8 (2026-09-11): 규칙 조사·재현은 봉 수만큼 CPU 를 먹는 관리자 도구다.
    "/admin/inspect",
    "/admin/reproduce",
    # T275 (2026-09-11): 토스 프록시 — 이 서버의 토스 client 로 대신 부른다.
    # 요율이 서버 것이라 관리자만.
    "/admin/toss",
)
"""관리자만 닿는 경로 앞자리 — 가입 승인·등급 변경 · **로그 내려받기**(T211) · **자원 창**(T215).

로그에는 주문·잔고·오류 전문이 있다. 열람자에게 성적표는 보여도 원문 로그는 관리자 몫이다.
"""

MONEY_READ_PREFIXES = ("/exchange", "/walkforward/live", "/rebalancer")
"""**실계좌에서는 읽기조차 거래 권한을 요구하는** 경로 앞자리.

사용자 확정 2026-08-30: *"실거래 콘솔이 보이는 건 옳지 않다."*

🔴 **경로가 아니라 환경이 문을 정한다.** 이 경로들은 지금 전부 테스트넷을 가리킨다
(`OrderGateway(AppEnv.PAPER)` · `GATE_TESTNET_*`). 그런데 실주문이 열리면(P2-8)
**같은 경로가 진짜 돈을 가리키게 된다** — 코드는 한 줄도 안 바뀌는데 뜻이 바뀐다.

    dev · paper  →  읽기 권한  (열람자도 페이퍼 콘솔을 본다 — 사용자 확정)
    live         →  거래 권한  (잔고·포지션·주문은 맡긴 사람만 본다)

⛔ **지금 막아 버리면 페이퍼 콘솔이 같이 죽는다.** 열람자에게 페이퍼 콘솔과 리포트를
주기로 한 것이 사용자 결정이고, 그 화면이 바로 이 경로들을 읽는다 — `/exchange/state`
하나만 봐도 페이퍼 콘솔(`ConsoleTab`)과 관문 목록(`LiveTab`)이 같이 쓴다.

⚠️ **나중에 기억해서 거는 문은 안 걸린다.** 실주문을 여는 날은 할 일이 많고, 그날
읽기 권한을 떠올릴 것이라고 기대하면 안 된다 — 그래서 **미리** 걸어 둔다. 지금은
아무 동작도 바꾸지 않고, 환경이 바뀌는 순간 저절로 조인다.
"""

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
"""읽기로 치는 HTTP 메서드. 나머지는 전부 거래 권한을 요구한다."""


def need_for(method: str, path: str, *, live: bool = False) -> Need:
    """이 요청이 무엇을 요구하나.

    Args:
        method: HTTP 메서드.
        path: 경로 (쿼리 제외).
        live: **실계좌 환경인가**. 참이면 돈을 보여 주는 경로는 읽기도 거래 권한이다
            (`MONEY_READ_PREFIXES`). 기본은 거짓 — 테스트넷에서는 열람자도 본다.

    Returns:
        요구 등급.

    Note:
        🔴 **모르는 경로는 읽기가 아니라 메서드로 정한다.** 새 `POST` 를 만들면
        아무것도 안 해도 거래 권한이 걸린다 — 깜빡함이 구멍이 되지 않게 하는 것이
        이 함수의 존재 이유다.

        🔴 **`live` 를 인자로 받는 이유**: 환경을 여기서 읽으면 이 함수가 순수하지
        않게 되고, 그러면 진리표를 시험할 수 없다 (`live_gate.py` 와 같은 사정).
        환경을 아는 것은 미들웨어의 일이고, 판정은 값만 받는다.
    """
    clean = path.rstrip("/") or "/"
    if clean in PUBLIC_PATHS:
        return Need.PUBLIC
    # ⭐ T263 — 개인 토큰 관리와 MCP 끝점은 로그인만(읽기 등급). 기능 단위 판정은
    #    `caps.required_cap` 이 한다.
    if any(clean == p or clean.startswith(p + "/") for p in SELF_SERVICE_PREFIXES):
        return Need.READ
    if any(clean.startswith(prefix) for prefix in ADMIN_PREFIXES):
        return Need.ADMIN
    if method.upper() in READ_METHODS:
        # ⚠️ 실계좌에서만 조인다 — 테스트넷에서 조이면 열람자의 페이퍼 콘솔이 죽는다.
        if live and any(clean.startswith(prefix) for prefix in MONEY_READ_PREFIXES):
            return Need.TRADE
        return Need.READ
    return Need.TRADE


def may_audit(role: Role | None, granted: bool) -> bool:
    """**감사 권한** — 백테스트·합성 미래의 최종 손익 · 연차별 손익을 볼 수 있나 (2026-09-06).

    Args:
        role: 계정 등급. None 이면 로그인하지 않았다.
        granted: 관리자가 그 계정에 감사를 **따로** 준 값 (`accounts.audit`).

    Returns:
        관리자는 늘 참. 그 외는 준 값 그대로. 로그인 안 했으면 거짓.

    Note:
        등급이 아니라 **별도 플래그**인 이유: 감사는 거래 권한과 직교한다 — 주문은 못 내지만
        성적을 검토하는 사람(감사)과, 주문은 내지만 성적표는 못 보는 사람이 둘 다 있을 수 있다.
        게스트·열람자는 차트 · 지표 · 매매법 · 봉 · 매매 표기까지는 보고, **돈이 얼마가 됐나**만
        못 본다.
    """
    if role is None:
        return False
    if role is Role.ADMIN:
        return True
    return granted


def allows(role: Role | None, need: Need) -> bool:
    """그 등급이 그 요구를 채우나.

    Args:
        role: 계정 등급. None 이면 **로그인하지 않았다**.
        need: 요구 등급.

    Returns:
        허용이면 참.

    Note:
        ⚠️ 승인 대기(`PENDING`)도 **읽기는 된다** (사용자 확정 2026-08-30) — 승인 전에도
        무엇을 하는 시스템인지 볼 수 있어야 한다는 판단이다.

        🔴 **거래는 `TRADER` 부터**다 — 승인(`VIEWER`)만으로는 주문이 안 나간다.
        승인은 목록을 훑으며 빠르게 누르는 동작이라, 그 손놀림으로 실계좌 주문 권한이
        나가면 안 된다 (사용자 확정 2026-08-30).

        ⛔ 로그인 안 한 사람은 `PUBLIC` 말고는 아무것도 못 본다.
    """
    if need is Need.PUBLIC:
        return True
    if role is None:
        return False
    if need is Need.READ:
        return True
    if need is Need.TRADE:
        return role in {Role.TRADER, Role.ADMIN}
    return role is Role.ADMIN
