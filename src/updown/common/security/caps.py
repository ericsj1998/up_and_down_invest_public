"""기능별 권한(Cap)과 권한 묶음(Collection) — 등급 하나를 기능 열 개로 편다 (사용자 2026-09-07).

> *"[데모 거래] [실거래] [감사] [권한 관리] [리포트] [데모 계좌 조회] [데모 RUN 조회] [실거래 계좌 조회]
> [실거래 RUN 조회] 이런식으로 기능별 권한이 다 보이게 … 권한 컬렉션도 내가 정의하고 편집할 수 있게."*

## 두 층

    Cap         기능 하나 — 창구(경로·메서드·서버)가 요구하는 것. 코드가 정한다 (여기 표).
    Collection  Cap 들의 묶음 — 관리자가 화면에서 만들고 고친다 (`role_collections` 표).
                내장 여섯 개(게스트·실거래 조회 게스트·열람자·거래자·관리자·슈퍼 관리자)는 지울 수 없고,
                이름은 고정이되 안의 Cap 은 고칠 수 있다.

계정 하나 = 묶음 하나 + 개별로 더 준 Cap(`extra_caps`). 유효 권한 = 묶음 + 개별.
승인 대기(묶음 없음)는 `PENDING_CAPS`(데모 조회·리포트) — 사용자 확정 2026-08-30 *"대기도 읽기는 된다"*.

## 🔴 옛 등급(`Role`)은 남는다 — 파생값으로

`accounts.role` 은 여러 곳(화면 라벨 · 게스트 판별 · 보류 판정 · 감사)이 읽는다. 묶음을 바꾸면
`role_for` 가 등급을 다시 계산해 같이 적는다: 권한 관리가 있으면 admin · 실거래가 있으면 trader ·
묶음이 있으면 viewer · 없으면 pending. 게스트는 게스트.

## 🔴 관리자 권한은 슈퍼 관리자만 준다

`MANAGE_USERS`·`MANAGE_ROLES` 가 든 묶음이나 Cap 을 남에게 주려면 주는 사람이 `MANAGE_ROLES` 를
쥐어야 한다 (`may_assign`). 관리자가 관리자를 찍어 내는 길을 막는다.

⛔ 여기는 **순수 함수**만 — DB·요청을 모른다. 시험이 진리표를 그대로 돌린다.
"""

# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from updown.common.security.markets import (
    BUILTIN_MARKET_POLICIES,
    DEFAULT_MARKET_POLICY,
    MarketPolicy,
)
from updown.common.security.playbooks import BUILTIN_POLICIES, DEFAULT_POLICY, PlaybookPolicy
from updown.common.security.roles import (
    ADMIN_PREFIXES,
    PUBLIC_PATHS,
    READ_METHODS,
    Role,
)


class Cap(StrEnum):
    """기능 하나 — 값은 DB 에 그대로 저장된다 (바꾸면 마이그레이션)."""

    DEMO_TRADE = "demo_trade"
    LIVE_TRADE = "live_trade"
    AUDIT = "audit"
    REPORT = "report"
    DEMO_ACCOUNT_READ = "demo_account_read"
    DEMO_RUNS_READ = "demo_runs_read"
    LIVE_ACCOUNT_READ = "live_account_read"
    LIVE_RUNS_READ = "live_runs_read"
    DEMO_EDIT = "demo_edit"
    DEMO_DELETE = "demo_delete"
    LIVE_EDIT = "live_edit"
    LIVE_DELETE = "live_delete"
    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"


CAP_LABELS: dict[Cap, str] = {
    Cap.DEMO_TRADE: "데모 거래",
    Cap.LIVE_TRADE: "실거래",
    Cap.AUDIT: "감사",
    Cap.REPORT: "리포트",
    Cap.DEMO_ACCOUNT_READ: "데모 계좌 조회",
    Cap.DEMO_RUNS_READ: "데모 RUN 조회",
    Cap.LIVE_ACCOUNT_READ: "실거래 계좌 조회",
    Cap.LIVE_RUNS_READ: "실거래 RUN 조회",
    Cap.DEMO_EDIT: "데모 수정",
    Cap.DEMO_DELETE: "데모 삭제",
    Cap.LIVE_EDIT: "실거래 수정",
    Cap.LIVE_DELETE: "실거래 삭제",
    Cap.MANAGE_USERS: "권한 관리",
    Cap.MANAGE_ROLES: "권한 묶음 편집 (슈퍼)",
}
"""화면에 보이는 이름 — 사용자가 적은 그대로 (수정·삭제 넷은 2026-09-08 추가)."""

CAP_GROUPS: dict[Cap, str] = {
    Cap.DEMO_ACCOUNT_READ: "데모",
    Cap.DEMO_RUNS_READ: "데모",
    Cap.DEMO_TRADE: "데모",
    Cap.DEMO_EDIT: "데모",
    Cap.DEMO_DELETE: "데모",
    Cap.LIVE_ACCOUNT_READ: "실거래",
    Cap.LIVE_RUNS_READ: "실거래",
    Cap.LIVE_TRADE: "실거래",
    Cap.LIVE_EDIT: "실거래",
    Cap.LIVE_DELETE: "실거래",
    Cap.REPORT: "공통",
    Cap.AUDIT: "공통",
    Cap.MANAGE_USERS: "관리",
    Cap.MANAGE_ROLES: "관리",
}

ADMIN_CAPS: frozenset[Cap] = frozenset({Cap.MANAGE_USERS, Cap.MANAGE_ROLES})
"""이것이 든 묶음·Cap 은 **슈퍼 관리자만** 남에게 준다."""

TRADE_CAPS: frozenset[Cap] = frozenset({Cap.DEMO_TRADE, Cap.LIVE_TRADE})
"""주문이 나가는 기능 — 분당 상한 · 리더 락 · 재인증이 이 둘에 걸린다."""

MUTATION_CAPS: frozenset[Cap] = frozenset(
    {Cap.DEMO_TRADE, Cap.LIVE_TRADE, Cap.DEMO_EDIT, Cap.LIVE_EDIT, Cap.DEMO_DELETE, Cap.LIVE_DELETE}
)
"""상태를 바꾸는 기능 전부 — 거래 · 수정 · 삭제."""

_DEMO_MUTATE = frozenset({Cap.DEMO_TRADE, Cap.DEMO_EDIT, Cap.DEMO_DELETE})
_LIVE_MUTATE = frozenset({Cap.LIVE_TRADE, Cap.LIVE_EDIT, Cap.LIVE_DELETE})

PENDING_CAPS: frozenset[Cap] = frozenset({Cap.DEMO_ACCOUNT_READ, Cap.DEMO_RUNS_READ, Cap.REPORT})
"""승인 대기(묶음 없음)가 갖는 것 — 데모를 둘러보고 리포트를 본다. 주문은 못 낸다."""


@dataclass(frozen=True, slots=True)
class Collection:
    """권한 묶음 하나.

    Attributes:
        name: 열쇠 (영문 · 고정). 계정 행이 이 이름을 가리킨다.
        label: 화면 이름.
        caps: 든 기능들.
        builtin: 내장 — 지울 수 없다. 안의 기능은 고칠 수 있다.
    """

    name: str
    label: str
    caps: frozenset[Cap]
    builtin: bool = False
    policy: PlaybookPolicy | None = None
    """매매법 기본 정책 (T230). None 이면 내장값(`playbooks.BUILTIN_POLICIES`) — `policy_of` 가 푼다."""
    market_policy: MarketPolicy | None = None
    """시장 기본 정책 (T242). None 이면 내장값(`markets.BUILTIN_MARKET_POLICIES`) — `market_policy_of` 가 푼다."""


_DEMO_READ = frozenset({Cap.DEMO_ACCOUNT_READ, Cap.DEMO_RUNS_READ})
_LIVE_READ = frozenset({Cap.LIVE_ACCOUNT_READ, Cap.LIVE_RUNS_READ})
_ALL = frozenset(Cap)

BUILTIN_COLLECTIONS: tuple[Collection, ...] = (
    # 게스트는 데모에서 판을 만들고 고칠 수 있되 **지우지는 못한다** — 남이 만든 데모 판을 지우는 것은 관리자 몫.
    Collection(
        "guest",
        "게스트",
        _DEMO_READ | {Cap.DEMO_TRADE, Cap.DEMO_EDIT, Cap.REPORT},
        builtin=True,
    ),
    Collection(
        "live_watch_guest",
        "실거래 조회 게스트",
        _DEMO_READ | _LIVE_READ | {Cap.DEMO_TRADE, Cap.DEMO_EDIT, Cap.REPORT},
        builtin=True,
    ),
    Collection("viewer", "열람자", _DEMO_READ | {Cap.REPORT}, builtin=True),
    Collection(
        "trader",
        "거래자",
        _DEMO_READ | _LIVE_READ | MUTATION_CAPS | {Cap.REPORT},
        builtin=True,
    ),
    Collection("admin", "관리자", _ALL - {Cap.MANAGE_ROLES}, builtin=True),
    Collection("super_admin", "슈퍼 관리자", _ALL, builtin=True),
)
"""내장 묶음 — 마이그레이션이 표에 심고, 표에 없으면(옛 DB) 이 값으로 돈다."""

BUILTIN_BY_NAME: dict[str, Collection] = {item.name: item for item in BUILTIN_COLLECTIONS}

LEGACY_COLLECTION: dict[Role, str | None] = {
    Role.PENDING: None,
    Role.VIEWER: "viewer",
    Role.TRADER: "trader",
    Role.ADMIN: "super_admin",
    Role.GUEST: "guest",
}
"""옛 등급 → 묶음. 마이그레이션 백필과, 묶음 없이 등급만 아는 호출자(시험)의 기본값."""


def policy_of(collection: Collection | None, role: Role | None) -> PlaybookPolicy:
    """묶음(또는 등급)의 매매법 기본 정책 (T230).

    Args:
        collection: 계정의 묶음. None 이면 등급으로 내장 묶음을 찾는다.
        role: 옛 등급 — 묶음 없이 등급만 아는 호출자(시험).

    Returns:
        표에 적힌 정책이 있으면 그것, 없으면 내장 기본값, 그것도 없으면 `DEFAULT_POLICY`(보기 + 견본 백테스트).
    """
    if collection is not None:
        if collection.policy is not None:
            return collection.policy
        return BUILTIN_POLICIES.get(collection.name, DEFAULT_POLICY)
    name = LEGACY_COLLECTION.get(role) if role is not None else None
    return BUILTIN_POLICIES.get(name or "", DEFAULT_POLICY)


def market_policy_of(collection: Collection | None, role: Role | None) -> MarketPolicy:
    """묶음(또는 등급)의 시장 기본 정책 (T242) — `policy_of` 와 같은 규칙.

    Args:
        collection: 계정의 묶음. None 이면 등급으로 내장 묶음을 찾는다.
        role: 옛 등급.

    Returns:
        정책. 모르면 `DEFAULT_MARKET_POLICY`(보기·백테스트만).
    """
    if collection is not None:
        if collection.market_policy is not None:
            return collection.market_policy
        return BUILTIN_MARKET_POLICIES.get(collection.name, DEFAULT_MARKET_POLICY)
    name = LEGACY_COLLECTION.get(role) if role is not None else None
    return BUILTIN_MARKET_POLICIES.get(name or "", DEFAULT_MARKET_POLICY)


def parse_caps(text: str | None) -> frozenset[Cap]:
    """쉼표로 이어 적은 Cap 들을 푼다 — 모르는 이름은 버린다 (옛 값이 문을 열지 않게).

    Args:
        text: `"demo_trade,report"` 꼴. None·빈 문자열은 빈 집합.

    Returns:
        아는 Cap 만 모은 집합.
    """
    if not text:
        return frozenset()
    out: set[Cap] = set()
    for item in text.split(","):
        key = item.strip()
        if not key:
            continue
        try:
            out.add(Cap(key))
        except ValueError:
            continue
    return frozenset(out)


def dump_caps(caps: Iterable[Cap]) -> str:
    """Cap 들을 저장 문자열로 — 순서를 고정한다 (같은 집합 = 같은 글자).

    Args:
        caps: 저장할 기능들. 중복은 하나로.

    Returns:
        `Cap` 선언 순서로 이은 쉼표 문자열.
    """
    order = {cap: index for index, cap in enumerate(Cap)}
    return ",".join(cap.value for cap in sorted(set(caps), key=lambda cap: order[cap]))


def caps_for_role(role: Role | None) -> frozenset[Cap]:
    """등급만 아는 호출자의 기본 권한 — 내장 묶음 값. 로그인 안 했으면 없음.

    Args:
        role: 옛 등급. None 은 로그인하지 않은 사람.

    Returns:
        그 등급에 대응하는 내장 묶음의 Cap 들. 대기 등급은 `PENDING_CAPS`.
    """
    if role is None:
        return frozenset()
    name = LEGACY_COLLECTION.get(role)
    if name is None:
        return PENDING_CAPS
    return BUILTIN_BY_NAME[name].caps


def effective_caps(
    *,
    role: Role,
    collection: str,
    extra: frozenset[Cap],
    table: dict[str, Collection],
) -> frozenset[Cap]:
    """계정 하나의 유효 권한 — 묶음 + 개별.

    Args:
        role: 등급 (게스트 판별 · 묶음이 없을 때의 기본).
        collection: 계정이 가리키는 묶음 이름. 빈 문자열이면 없음.
        extra: 개별로 더 준 기능.
        table: 지금 표에 있는 묶음들 (내장 포함).

    Returns:
        유효 권한. 묶음 이름이 표에 없으면(지워졌다) 등급 기본으로 떨어진다 — 조용히 전부 잃지 않게.
    """
    if collection and collection in table:
        base = table[collection].caps
    elif role is Role.GUEST:
        base = table.get("guest", BUILTIN_BY_NAME["guest"]).caps
    elif collection:
        base = caps_for_role(role)
    else:
        base = PENDING_CAPS
    return base | extra


def role_for(caps: frozenset[Cap], *, has_collection: bool, guest: bool = False) -> Role:
    """유효 권한에서 옛 등급을 되계산한다 — 화면 라벨·보류 판정이 이것을 본다.

    Args:
        caps: 유효 권한.
        has_collection: 묶음이 배정돼 있나. 없으면 개별 Cap 이 있어도 **승인 대기**다.
        guest: 게스트 행인가.

    Returns:
        등급.
    """
    if guest:
        return Role.GUEST
    if not has_collection:
        return Role.PENDING
    if Cap.MANAGE_USERS in caps or Cap.MANAGE_ROLES in caps:
        return Role.ADMIN
    if Cap.LIVE_TRADE in caps:
        return Role.TRADER
    return Role.VIEWER


def may_assign(actor: frozenset[Cap], target: Iterable[Cap]) -> bool:
    """주는 사람이 이 기능들을 남에게 줄 수 있나.

    Args:
        actor: 주는 사람의 유효 권한.
        target: 주려는 기능들 (묶음의 것이든 개별이든).

    Returns:
        관리 기능(`ADMIN_CAPS`)이 섞여 있으면 `MANAGE_ROLES` 가 있어야 한다. 그 외는 `MANAGE_USERS`.
    """
    wanted = frozenset(target)
    if wanted & ADMIN_CAPS:
        return Cap.MANAGE_ROLES in actor
    return Cap.MANAGE_USERS in actor


class Access(StrEnum):
    """Cap 이 아닌 요구 둘 — 공개 · 로그인만."""

    PUBLIC = "public"
    SIGNED_IN = "signed_in"


MUTATION_PREFIXES = ("/exchange", "/walkforward", "/rebalancer")
"""수정·삭제 기능이 갈리는 경로 — 이 밖의 쓰기는 전부 거래 기능(잠긴 기본값)."""
EDIT_PATH_MARKERS = ("/auto", "/leverage", "/control/", "/vault", "/basket", "/playbook", "/resync")
"""거래(주문)가 아닌 **수정** 창구 — 자동 켬/끔 · 배율 · 세션 제어 · 금고 · 바스켓 · 전략 · 재정렬."""
ACCOUNT_READ_PREFIXES = ("/exchange",)
DEMO_RUNS_READ_PREFIXES = ("/walkforward", "/rebalancer")
LIVE_RUNS_READ_PREFIXES = ("/walkforward/live", "/rebalancer")
"""실계좌에서 **실거래 RUN 조회**를 요구하는 경로 — 옛 `MONEY_READ_PREFIXES` 와 같다 (사용자 확정 2026-08-30).
`/walkforward/sessions` 같은 목록은 실계좌에서도 로그인만 하면 본다 — 그 판정을 바꾸지 않는다."""
REPORT_PREFIXES = ("/report",)
SELF_SERVICE_PREFIXES = ("/auth/tokens", "/mcp", "/chart-order")
"""로그인만 하면 되는 경로 — 개인 토큰 관리와 MCP 끝점 (T263). MCP 도구는 조회·제안뿐이다."""
ROLES_PREFIX = "/auth/roles"


def required_cap(method: str, path: str, *, live: bool) -> Cap | Access:
    """이 요청이 요구하는 기능 하나.

    Args:
        method: HTTP 메서드.
        path: 경로 (쿼리 제외).
        live: 이 서버가 실계좌인가 — 같은 경로가 데모에서는 데모 기능, 실계좌에서는 실거래 기능을 요구한다.

    Returns:
        Cap, 또는 `Access.PUBLIC`(로그인 불필요) · `Access.SIGNED_IN`(로그인만 하면 됨 — 차트·지표·근거).

    Note:
        🔴 **모르는 쓰기 경로는 거래 기능을 요구한다** — `roles.need_for` 와 같은 원칙. 새 POST 를 만들면
        아무것도 안 해도 잠긴다.
    """
    clean = path.rstrip("/") or "/"
    if clean in PUBLIC_PATHS:
        return Access.PUBLIC
    if any(clean == p or clean.startswith(p + "/") for p in SELF_SERVICE_PREFIXES):
        return Access.SIGNED_IN
    if clean.startswith(ROLES_PREFIX):
        return Cap.MANAGE_USERS if method.upper() in READ_METHODS else Cap.MANAGE_ROLES
    if any(clean.startswith(prefix) for prefix in ADMIN_PREFIXES):
        return Cap.MANAGE_USERS
    if any(clean.startswith(prefix) for prefix in REPORT_PREFIXES):
        return Cap.REPORT
    if method.upper() in READ_METHODS:
        if any(clean.startswith(prefix) for prefix in ACCOUNT_READ_PREFIXES):
            return Cap.LIVE_ACCOUNT_READ if live else Cap.DEMO_ACCOUNT_READ
        runs = LIVE_RUNS_READ_PREFIXES if live else DEMO_RUNS_READ_PREFIXES
        if any(clean.startswith(prefix) for prefix in runs):
            return Cap.LIVE_RUNS_READ if live else Cap.DEMO_RUNS_READ
        return Access.SIGNED_IN
    # 쓰기 — 삭제 · 수정 · 거래 (2026-09-08 사용자 요청: 넷을 따로 준다)
    if any(clean.startswith(prefix) for prefix in MUTATION_PREFIXES):
        if method.upper() == "DELETE":
            return Cap.LIVE_DELETE if live else Cap.DEMO_DELETE
        if method.upper() == "PUT" or any(marker in clean for marker in EDIT_PATH_MARKERS):
            return Cap.LIVE_EDIT if live else Cap.DEMO_EDIT
    return Cap.LIVE_TRADE if live else Cap.DEMO_TRADE
