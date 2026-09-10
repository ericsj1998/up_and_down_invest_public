"""구글 로그인 · 가입 승인 · 재인증 (2026-08-30 배포 준비).

사용자 요구 3가지:

    ① 구글 로그인 화면, 리다이렉트
    ② 관리자가 다른 사람의 **가입 허가**를 확인하고 줄 수 있음
    ③ 실제 거래소에 주문이 왔다 갔다 할 때 **보안 확인** (= 구글 재인증)

## 흐름

    /auth/login     → 구글로 보낸다 (state 쿠키를 굽는다)
    구글 로그인
    /auth/callback  → 코드를 **서버끼리** 토큰으로 바꾸고 세션 쿠키를 굽는다
    /auth/me        → 화면이 "나 누구야" 를 묻는 창구

## 왜 라이브러리(authlib 등)를 안 쓰나

우리가 쓰는 것은 **authorization code flow** 하나이고, 그 실체는 리다이렉트 한 번과
서버끼리의 POST 한 번이다. `httpx` 는 이미 있다. 인증 의존성이 늘면 갱신할 것이 늘고,
그 취약점은 조용히 치명적이다.

## 🔴 id_token 을 왜 서명 검증 없이 읽나

우리는 **구글에게 직접, TLS 로, 우리 client secret 을 붙여** 물어보고 그 응답으로
받는다. 중간에 낄 사람이 없으므로 그 자리에서는 서명이 더 말해 주는 것이 없다.

⛔ 이 근거는 **`/auth/callback` 안, 코드 교환 응답**에만 성립한다. `id_token` 을
   브라우저나 다른 곳에서 받는 경로를 나중에 만들면 그때는 JWKS 검증이 필요하다.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import time
import urllib.parse
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.analysis.playbook.select import load_playbooks
from updown.common.db.models.accounts import (
    Account,
    AccountContact,
    ApiToken,
    MarketGrantRow,
    PlaybookGrantRow,
    RoleCollection,
)
from updown.common.db.models.enums import LogLevel
from updown.common.db.models.ops import AppSetting, EventLog
from updown.common.domain.instrument import Market
from updown.common.http.outbound import NO_RETRY, Outbound, OutboundError
from updown.common.logging.context import actor_context, get_trace_id, new_trace_id
from updown.common.logging.setup import get_logger
from updown.common.security.caps import (
    ADMIN_CAPS,
    BUILTIN_BY_NAME,
    CAP_GROUPS,
    CAP_LABELS,
    LEGACY_COLLECTION,
    TRADE_CAPS,
    Access,
    Cap,
    Collection,
    caps_for_role,
    dump_caps,
    effective_caps,
    market_policy_of,
    may_assign,
    parse_caps,
    policy_of,
    required_cap,
    role_for,
)
from updown.common.security.markets import (
    GROUPS,
    MarketGrant,
    MarketPolicy,
    effective_market_grant,
    group_of,
)
from updown.common.security.playbooks import PlaybookGrant, PlaybookPolicy, effective_grant
from updown.common.security.roles import (
    GUEST_EMAIL,
    READ_METHODS,
    Need,
    Role,
    is_guest_email,
    may_audit,
    need_for,
)
from updown.common.security.session import COOKIE, FRESH_S, MAX_AGE_S, BadTokenError, Session
from updown.common.security.session import issue as issue_note
from updown.common.security.session import read as read_note
from updown.common.security.standing import (
    HOLD_AFTER,
    HOLD_MESSAGE,
    Standing,
    contact_allowed,
    hold_starts_at,
    standing_of,
)

router = APIRouter(prefix="/auth", tags=["auth"])
_logger = get_logger("api.auth")

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "openid email profile"

STATE_COOKIE = "updown_oauth_state"
STATE_MAX_AGE = 600
"""state 쿠키 수명(초) — 10분. 로그인 화면을 열어 두고 오래 지나면 다시 시작한다."""

_HTTP_OK = 200


class AuthNotConfiguredError(RuntimeError):
    """구글 로그인 설정이 없다.

    Note:
        ⛔ **조용히 통과시키지 않는다** (절대 규칙 #8). 설정이 없다는 것은 인증이
        없다는 뜻이고, 인증 없이 도는 것이 바로 막으려던 상태다.
    """


@dataclass(frozen=True, slots=True)
class GoogleApp:
    """구글 OAuth 설정 — 값이 다 있을 때만 만들어진다."""

    client_id: str
    client_secret: str
    redirect_uri: str
    session_secret: str
    admins: frozenset[str]


def google_app() -> GoogleApp:
    """설정에서 구글 앱을 읽는다.

    Returns:
        설정.

    Raises:
        AuthNotConfiguredError: 하나라도 비었을 때.

    Note:
        🔴 **하나라도 없으면 못 만든다.** 반쪽 설정으로 로그인 화면만 뜨면 사람은
        보호받는다고 믿는데 실제로는 아무도 안 막힌다 — 그것이 최악이다.
    """
    from updown.common.config import load_settings

    found = load_settings()
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLIENT_ID", found.google_client_id),
            ("GOOGLE_CLIENT_SECRET", found.google_client_secret),
            ("GOOGLE_REDIRECT_URI", found.google_redirect_uri),
            ("SESSION_SECRET", found.session_secret),
        )
        if not value
    ]
    if missing:
        raise AuthNotConfiguredError(f"구글 로그인 설정이 없다 — {', '.join(missing)}")
    assert found.google_client_id and found.google_redirect_uri
    assert found.google_client_secret and found.session_secret
    raw = found.admin_emails or ""
    return GoogleApp(
        client_id=found.google_client_id,
        client_secret=found.google_client_secret.get_secret_value(),
        redirect_uri=found.google_redirect_uri,
        session_secret=found.session_secret.get_secret_value(),
        admins=frozenset(item.strip().lower() for item in raw.split(",") if item.strip()),
    )


class InsecureTransportError(RuntimeError):
    """개발이 아닌 환경에서 **평문 HTTP** 로 세션을 구우려 했다.

    Note:
        🔴 조용히 `Secure` 없는 쿠키를 주면 세션이 **평문으로 오간다**. 그 상태는
        화면상 완전히 정상이라 아무도 모른다 — 이 프로젝트가 반복해서 데인 모양이다.
        ⇒ 로그인이 **안 되는 쪽**을 고른다 (절대 규칙 #8).
    """


def secure_cookies(request: Request) -> bool:
    """쿠키에 `Secure` 를 붙일까 — HTTPS 로 들어왔나.

    Args:
        request: 들어온 요청. 프록시 헤더 `x-forwarded-proto` 를 먼저 본다.

    Returns:
        HTTPS 면 True. dev 에서 평문이면 False.

    Raises:
        InsecureTransportError: 개발이 아닌데 평문 HTTP 로 들어왔을 때.

    Note:
        ⚠️ 프록시 뒤라 `request.url.scheme` 은 `http` 일 수 있다. nginx 가 넘기는
        `x-forwarded-proto` 를 먼저 본다.

        ⛔ **로컬(dev)에서는 뗀다** — 무조건 붙이면 개발에서 쿠키가 안 실려 로그인이
        안 된다. 그 예외를 **dev 로만** 좁힌다.

        🔴 **paper·live 에서 평문이면 아예 거부한다.** 배포해 놓고 HTTPS 를 안 걸면
        세션 쿠키가 평문으로 오가는데, 화면은 멀쩡해서 아무도 모른다. 프록시가
        `x-forwarded-proto` 를 안 넘기는 오설정도 여기서 걸린다 — 그때 로그인이
        깨지는 것이 조용히 취약한 것보다 낫다.
    """
    from updown.common.config import AppEnv, load_settings

    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if proto == "https":
        return True
    if load_settings().app_env is AppEnv.DEV:
        return False
    raise InsecureTransportError(
        "평문 HTTP 로는 세션을 만들지 않는다 — 앞단 프록시에 TLS 를 걸고 "
        "`X-Forwarded-Proto: https` 를 넘겨야 한다"
    )


def _bake(response: Response, name: str, value: str, *, request: Request, age: int) -> None:
    """쿠키를 굽는다 — **httpOnly · SameSite=Lax**.

    Note:
        🔴 `httponly` 는 XSS 로 세션이 새는 길을 끊는다. JS 가 못 읽으므로 화면은
        토큰을 다루지 않고, 브라우저가 알아서 붙여 보낸다.
        🔴 `samesite=lax` 는 남의 사이트에서 온 POST 에 쿠키를 안 싣는다 (CSRF 1차 방어).
    """
    response.set_cookie(
        name,
        value,
        max_age=age,
        httponly=True,
        secure=secure_cookies(request),
        samesite="lax",
        path="/",
    )


@router.get("/login")
async def login(request: Request, next_path: str = "/") -> RedirectResponse:
    """구글 로그인 화면으로 보낸다 (요구 ①).

    Args:
        request: 요청.
        next_path: 로그인 뒤 돌아갈 화면 경로.

    Returns:
        구글로 가는 리다이렉트.

    Raises:
        HTTPException: 503 — 구글 OAuth 설정이 없다 (빈 로그인 화면을 조용히 그리지 않는다).

    Note:
        🔴 **state 로 CSRF 를 막는다.** 무작위 값을 쿠키에 굽고 같은 값을 구글에
        넘긴다. 돌아왔을 때 둘이 같아야 우리가 시작한 로그인이다 — 없으면 공격자가
        자기 계정으로 남을 로그인시킬 수 있다 (로그인 CSRF).

        ⚠️ `next_path` 는 **경로만** 받는다. 절대 URL 을 그대로 쓰면 열린 리다이렉트가
        되어 우리 도메인을 발판으로 남의 사이트로 보낼 수 있다.
    """
    try:
        app = google_app()
    except AuthNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc
    state = secrets.token_urlsafe(24)
    safe = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    query = urllib.parse.urlencode(
        {
            "client_id": app.client_id,
            "redirect_uri": app.redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": f"{state}:{safe}",
            # ⭐ 재인증(요구 ③)에서 이 값이 `select_account` 로 바뀐다 — 아래 참조.
            # 🔴 재인증(요구 ③)은 **비밀번호를 다시 치게** 한다 — `select_account` 는 구글 세션이
            #    살아 있으면 계정 클릭 한 번으로 통과해 "자리 비운 노트북" 위협을 못 막았다
            #    (보안 점검 2026-09-10).
            "prompt": "login" if request.query_params.get("force") else "",
            "max_age": "0" if request.query_params.get("force") else "",
        }
    )
    made = RedirectResponse(f"{GOOGLE_AUTH}?{query}", status_code=302)
    try:
        _bake(made, STATE_COOKIE, state, request=request, age=STATE_MAX_AGE)
    except InsecureTransportError as exc:
        # 🔴 500 이 아니라 **이유를 말한다** — 이건 버그가 아니라 배포 설정 문제이고,
        #    스택 트레이스보다 한 줄이 사람을 고치게 만든다.
        raise HTTPException(503, str(exc)) from exc
    return made


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    """구글이 되돌려 보낸 코드를 세션으로 바꾼다 (요구 ①·③).

    Args:
        request: 요청.
        code: 구글이 준 인가 코드.
        state: 우리가 넘겼던 값.

    Returns:
        원래 보려던 화면으로 가는 리다이렉트.

    Raises:
        HTTPException: 설정이 없거나(503) state 가 안 맞거나 코드 교환이 실패(400).

    Note:
        🔴 **state 를 먼저 본다.** 코드 교환보다 앞이다 — 남이 시작한 로그인이면
        구글에 물어볼 이유조차 없다.
    """
    try:
        app = google_app()
    except AuthNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc
    want = request.cookies.get(STATE_COOKIE, "")
    token, _, next_path = state.partition(":")
    if not want or not secrets.compare_digest(token, want):
        raise HTTPException(400, "로그인 요청이 우리 것이 아니다 — 처음부터 다시 한다")
    if not code:
        raise HTTPException(400, "구글이 코드를 안 줬다")

    # 한 번만 보낸다 — 인증 코드는 1회용이라 재시도해도 같은 답이다 (T264 2차 · `NO_RETRY`).
    http = Outbound("GOOGLE", timeout=15, policy=NO_RETRY)
    try:
        got = await http.request(
            "POST",
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": app.client_id,
                "client_secret": app.client_secret,
                "redirect_uri": app.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    except OutboundError as exc:
        _logger.warning("google_token_failed", payload={"detail": exc.exc_type or "transport"})
        raise HTTPException(400, "구글 인증에 실패했다") from exc
    finally:
        await http.aclose()
    if got.status_code != _HTTP_OK:
        # ⛔ 구글 응답을 그대로 사람에게 보여 주지 않는다 — 설정값이 섞여 나올 수 있다.
        _logger.warning("google_token_failed", payload={"status": got.status_code})
        raise HTTPException(400, "구글 인증에 실패했다")
    claims = _claims(str(got.json().get("id_token", "")))
    email = str(claims.get("email", "")).strip().lower()
    if not email or not claims.get("email_verified", False):
        raise HTTPException(400, "확인된 이메일이 없는 계정이다")

    now = time.time()
    # ⭐ 보안 점검 #4 완성 (2026-09-11): 구글이 `prompt=login` 을 무시하고 SSO 로 통과시키면
    #    `auth_time` 이 옛날이다 — 그때는 "방금 인증" 으로 치지 않는다(재인증 문이 다시 잠긴다).
    auth_at = _auth_at_of(claims, now)
    await _touch_account(email, claims, admins=app.admins)
    made = RedirectResponse(next_path or "/", status_code=302)
    try:
        _bake(
            made,
            COOKIE,
            issue_note(Session(email=email, issued_at=now, auth_at=auth_at), app.session_secret),
            request=request,
            age=MAX_AGE_S,
        )
    except InsecureTransportError as exc:
        raise HTTPException(503, str(exc)) from exc
    made.delete_cookie(STATE_COOKIE, path="/")
    return made


_factory: async_sessionmaker[AsyncSession] | None = None


def _store() -> async_sessionmaker[AsyncSession]:
    """계정 저장소 — 안 붙었으면 503. 라우터 6곳이 같은 두 줄을 적던 것을 모았다 (2026-09-06).

    Returns:
        세션 팩토리.

    Raises:
        HTTPException: 503 — 앱 기동 때 저장소가 안 붙었다.
    """
    if _factory is None:
        raise HTTPException(503, "계정 저장소가 안 붙었다")
    return _factory


def attach_accounts(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """계정 저장소를 붙인다 — API 기동 훅이 부른다 (`attach_store` 와 같은 자리).

    Args:
        factory: 세션 팩토리. None 이면 뗀다 (테스트가 쓴다).

    Note:
        ⚠️ **엔진을 여기서 만들지 않는다** — 별도 풀이 생기고 아무도 안 닫는다.
    """
    global _factory
    _factory = factory


async def _touch_account(email: str, claims: dict[str, Any], *, admins: frozenset[str]) -> None:
    """로그인한 사람을 표에 만들거나 갱신한다.

    Args:
        email: 소문자 이메일.
        claims: 구글이 준 값 (이름·사진).
        admins: 설정에 적힌 첫 관리자들.

    Raises:
        HTTPException: 차단된 계정이면 403.

    Note:
        🔴 **새 계정은 `PENDING` 이다.** 설정(`ADMIN_EMAILS`)에 적힌 이메일만 처음
        만들 때 `ADMIN` 이 된다 (사용자 확정 2026-08-30).

        ⛔ **이미 있는 계정의 등급은 설정으로 안 바꾼다.** 설정 한 줄로 남의 등급이
        바뀌면 관리자 화면의 기록(`approved_by`)과 사실이 갈린다 — 등급 변경은
        화면에서, 누가 했는지 남기며 한다.
    """
    factory = _store()
    now = datetime.now(UTC)
    created_pending = False
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == email))
        if found is None:
            first = email in admins
            session.add(
                Account(
                    email=email,
                    name=str(claims.get("name", "")),
                    picture=str(claims.get("picture", "")),
                    role=Role.ADMIN if first else Role.PENDING,
                    role_collection="super_admin" if first else "",
                    approved_at=now if first else None,
                    approved_by="설정(ADMIN_EMAILS)" if first else "",
                    last_login_at=now,
                )
            )
            _logger.info("account_created", payload={"email": email, "admin": first})
            created_pending = not first
        else:
            if found.deleted_at is not None:
                # ⭐ 지운 계정이 다시 왔다 — 대기 계정으로 0 부터 (T266). 권한·토큰은 지울 때
                #    비웠다.
                revive_account(found, now)
                _logger.info("account_revived", payload={"email": email})
                created_pending = True
            elif found.blocked:
                # ⛔ 차단은 삭제와 다르다 — 차단은 되살아나지 않는다.
                raise HTTPException(403, "차단된 계정이다")
            found.name = str(claims.get("name", "")) or found.name
            found.picture = str(claims.get("picture", "")) or found.picture
            found.last_login_at = now
    if created_pending:
        # 🔴 새 가입자를 관리자에게 알린다 (사용자 2026-09-08 "게스트가 발생하면 메일").
        #    로그인 응답을 메일에 묶지 않는다 — SMTP 가 몇 초 걸리거나 죽어도 가입은 끝나야 한다.
        #    실패는 `signup_notice_failed` 로만 남는다 (§1.2.1 · 리스크 감소 행동이 아니다).
        task = asyncio.create_task(_notify_signup(email, str(claims.get("name", "")), now=now))
        _signup_tasks.add(task)
        task.add_done_callback(_signup_tasks.discard)


def _auth_at_of(claims: dict[str, Any], now: float) -> float:
    """구글 id_token 의 `auth_time` 을 믿되, 미래·결측은 `now`.

    Args:
        claims: id_token 클레임.
        now: 지금(epoch 초).

    Returns:
        세션 쪽지에 적을 인증 시각 — `min(now, auth_time)`.
    """
    raw = claims.get("auth_time")
    try:
        stamp = float(raw) if raw is not None else now
    except (TypeError, ValueError):
        return now
    return min(now, stamp) if stamp > 0 else now


async def account_of(email: str) -> Account | None:
    """이메일로 계정을 읽는다 — **미들웨어가 매 요청 부른다**.

    Args:
        email: 소문자 이메일.

    Returns:
        계정. 없으면 None.

    Note:
        🔴 **등급을 쪽지에서 안 읽고 여기서 읽는 이유**: 쪽지에 담으면 관리자가 등급을
        내려도 그 쪽지가 만료될 때까지 옛 권한이 산다 — 권한 회수가 12시간 뒤에 듣는다.
    """
    if _factory is None:
        return None
    factory = _store()
    async with factory() as session:
        return await session.scalar(sa.select(Account).where(Account.email == email))


@dataclass(frozen=True, slots=True)
class Caller:
    """이번 요청을 보낸 사람 — 미들웨어가 `request.state.caller` 에 둔다."""

    email: str
    role: Role
    fresh: bool
    """마지막 구글 인증이 최근인가 (요구 ③)."""
    auth_at: float = 0.0
    """마지막 구글 인증 시각 (epoch 초) — 화면 타이머가 기한을 그린다 (2026-09-03)."""
    audit: bool = False
    """감사 권한 플래그 (`accounts.audit`). 판정은 `roles.may_audit` — 관리자는 늘 참."""
    standing: Standing = Standing.ACTIVE
    """처지 — 보류(`HELD`)면 `_pass` 가 `/auth/*` 공개 창구 말고는 전부 막는다 (2026-09-07)."""
    caps: frozenset[Cap] | None = None
    """유효 권한 (묶음 + 개별). None 이면 등급의 내장 묶음으로 본다 — 등급만 아는 호출자(시험)용."""
    collection: str = ""
    via_token: bool = False
    """개인 API 토큰(Bearer)으로 왔나 — 읽기와 MCP 만 된다 (T263). 구글 쪽지면 거짓."""
    """권한 묶음 이름."""
    policy: PlaybookPolicy | None = None
    """묶음의 매매법 정책 (T230). None 이면 등급의 내장값으로 본다."""
    playbook_rows: Mapping[str, PlaybookGrant] = field(default_factory=dict[str, PlaybookGrant])
    market_policy: MarketPolicy | None = None
    market_rows: Mapping[str, MarketGrant] = field(default_factory=dict[str, MarketGrant])
    """사람별 매매법 덮어쓰기 행들 (`playbook_grants`)."""

    @property
    def held(self) -> bool:
        """임시 보류인가."""
        return self.standing is Standing.HELD

    @property
    def granted(self) -> frozenset[Cap]:
        """지금 쥔 기능들."""
        return self.caps if self.caps is not None else caps_for_role(self.role)

    def has(self, cap: Cap) -> bool:
        """이 기능을 쥐었나.

        Args:
            cap: 묻는 기능.

        Returns:
            묶음 + 개별 부여를 합친 `granted` 에 들어 있으면 참.
        """
        return cap in self.granted

    def playbook(self, playbook_id: str) -> PlaybookGrant:
        """이 매매법의 유효 권한 — 사람별 행 > 묶음 정책 · 감사는 보기·백테스트를 전부 연다 (T230).

        Args:
            playbook_id: 매매법 id.

        Returns:
            `view` · `backtest` · `trade` 가 정해진 권한.
        """
        policy = self.policy if self.policy is not None else policy_of(None, self.role)
        return effective_grant(
            playbook_id,
            policy=policy,
            row=self.playbook_rows.get(playbook_id),
            audit=self.has(Cap.AUDIT),
        )

    def market(self, group: str) -> MarketGrant:
        """이 시장 갈래의 유효 권한 — 사람별 행 > 묶음 정책 · 감사는 보기·백테스트를 연다 (T242).

        Args:
            group: `coin` · `domestic` · `foreign`.

        Returns:
            `view` · `backtest` · `trade`.
        """
        policy = (
            self.market_policy
            if self.market_policy is not None
            else market_policy_of(None, self.role)
        )
        return effective_market_grant(
            group, policy=policy, row=self.market_rows.get(group), audit=self.has(Cap.AUDIT)
        )


COLLECTIONS_CACHE_S = 60.0
_collections_cache: tuple[float, dict[str, Collection]] | None = None


async def collections() -> dict[str, Collection]:
    """권한 묶음 표 — 내장 여섯 + 관리자가 만든 것. 60초 캐시 (미들웨어가 매 요청 부른다).

    Returns:
        이름 → 묶음. 표를 못 읽으면 내장값 — 설정 조회 실패가 문을 잠그면 안 된다.
    """
    global _collections_cache
    now = time.monotonic()
    if _collections_cache is not None and now - _collections_cache[0] < COLLECTIONS_CACHE_S:
        return _collections_cache[1]
    table: dict[str, Collection] = dict(BUILTIN_BY_NAME)
    if _factory is not None:
        try:
            async with _store()() as session:
                rows = list(await session.scalars(sa.select(RoleCollection)))
            for row in rows:
                table[row.name] = Collection(
                    row.name,
                    row.label or row.name,
                    parse_caps(row.caps),
                    builtin=row.builtin,
                    policy=(
                        PlaybookPolicy.from_json(row.playbook_policy)
                        if row.playbook_policy is not None
                        else None
                    ),
                    market_policy=(
                        MarketPolicy.from_json(row.market_policy)
                        if row.market_policy is not None
                        else None
                    ),
                )
        except Exception as exc:
            _logger.warning("role_collections_unreadable: %s", str(exc)[:160])
    _collections_cache = (now, table)
    return table


def invalidate_collections() -> None:
    """묶음을 고친 뒤 캐시를 비운다 — 이 프로세스에는 즉시, 다른 프로세스에는 1분 안에 듣는다."""
    global _collections_cache
    _collections_cache = None


PLAYBOOK_ROWS_CACHE_S = 60.0
_playbook_rows_cache: dict[str, tuple[float, dict[str, PlaybookGrant]]] = {}


async def playbook_rows_of(email: str) -> dict[str, PlaybookGrant]:
    """한 사람의 매매법 덮어쓰기 행들 — 60초 캐시 (미들웨어가 매 요청 부른다 · T230).

    Args:
        email: 소문자 이메일.

    Returns:
        매매법 id → 행. 표를 못 읽으면 빈 dict — 묶음 기본값으로 떨어진다.
    """
    now = time.monotonic()
    hit = _playbook_rows_cache.get(email)
    if hit is not None and now - hit[0] < PLAYBOOK_ROWS_CACHE_S:
        return hit[1]
    rows: dict[str, PlaybookGrant] = {}
    if _factory is not None:
        try:
            async with _store()() as session:
                found = await session.scalars(
                    sa.select(PlaybookGrantRow).where(PlaybookGrantRow.email == email)
                )
                for row in found:
                    rows[row.playbook_id] = PlaybookGrant(
                        row.playbook_id, row.view, row.backtest, row.trade
                    )
        except Exception as exc:
            _logger.warning("playbook_grants_unreadable: %s", str(exc)[:160])
    _playbook_rows_cache[email] = (now, rows)
    return rows


_market_rows_cache: dict[str, tuple[float, dict[str, MarketGrant]]] = {}


async def market_rows_of(email: str) -> dict[str, MarketGrant]:
    """한 사람의 시장 덮어쓰기 행들 — 60초 캐시 (T242 · `playbook_rows_of` 와 같은 결).

    Args:
        email: 소문자 이메일.

    Returns:
        갈래 → 행.
    """
    now = time.monotonic()
    hit = _market_rows_cache.get(email)
    if hit is not None and now - hit[0] < PLAYBOOK_ROWS_CACHE_S:
        return hit[1]
    rows: dict[str, MarketGrant] = {}
    if _factory is not None:
        try:
            async with _store()() as session:
                found = await session.scalars(
                    sa.select(MarketGrantRow).where(MarketGrantRow.email == email)
                )
                for row in found:
                    rows[row.market_group] = MarketGrant(
                        row.market_group, row.view, row.backtest, row.trade
                    )
        except Exception as exc:
            _logger.warning("market_grants_unreadable: %s", str(exc)[:160])
    _market_rows_cache[email] = (now, rows)
    return rows


def invalidate_market_rows(email: str | None = None) -> None:
    """시장 덮어쓰기 행을 고친 뒤 캐시를 비운다.

    Args:
        email: 한 사람만. None 이면 전부.
    """
    if email is None:
        _market_rows_cache.clear()
    else:
        _market_rows_cache.pop(email, None)


def invalidate_playbook_rows(email: str | None = None) -> None:
    """덮어쓰기 행을 고친 뒤 캐시를 비운다.

    Args:
        email: 그 사람만. None 이면 전부 (묶음 정책이 바뀌었을 때).
    """
    if email is None:
        _playbook_rows_cache.clear()
    else:
        _playbook_rows_cache.pop(email, None)


def _record_permission(
    session: AsyncSession,
    *,
    email: str,
    what: str,
    before: object,
    after: object,
    by: str,
) -> None:
    """권한 변경 이력 — `event_logs` 에 한 줄 (추가만 · 규칙 8-2 · T230).

    Args:
        session: 같은 트랜잭션 — 권한 변경과 이력이 함께 들어가거나 함께 안 들어간다.
        email: 대상 계정.
        what: `collection` · `caps` · `playbook` · `role_policy`.
        before: 바꾸기 전 값 (JSON 가능한 것).
        after: 바꾼 뒤 값.
        by: 바꾼 사람.
    """
    session.add(
        EventLog(
            trace_id=get_trace_id() or new_trace_id(),
            actor=by,
            module="apps.api.auth",
            level=LogLevel.INFO,
            event_type="permission_changed",
            payload_json={"email": email, "what": what, "before": before, "after": after, "by": by},
        )
    )


def require_playbook_trade(request: Request, playbook_ids: Iterable[str]) -> None:
    """이 매매법으로 판·펀드를 열 권한(T230 `trade`)이 없으면 403.

    서버 축(`demo_trade`/`live_trade`)은 미들웨어가 이미 봤다 — 여기는 "이 매매법을" 만 본다.

    Args:
        request: 요청 (`state.caller`). 호출자가 없으면(시험 우회) 통과.
        playbook_ids: 쓰려는 매매법들.

    Raises:
        HTTPException: 403 `playbook_forbidden`.
    """
    who = getattr(request.state, "caller", None)
    if who is None:
        return
    for playbook_id in playbook_ids:
        if not who.playbook(playbook_id).trade:
            raise HTTPException(
                403,
                {
                    "code": "playbook_forbidden",
                    "playbook": playbook_id,
                    "message": f"{playbook_id} 매매법을 쓸 권한이 없다 — 관리자가 준다",
                },
            )


def require_fresh(request: Request) -> None:
    """돈이 움직이는 함수 첫 줄에서 **최근 인증**을 본다 (요구 ③ · 보안 점검 2026-09-10).

    Args:
        request: 요청 (`state.caller`). 호출자가 없으면(시험 우회) 통과.

    Raises:
        HTTPException: 401 — 재인증 주소를 `detail.reauth` 로 준다(미들웨어 응답과 같은 모양).

    Note:
        미들웨어의 `TRADE_PATHS` 접두어 검사는 경로가 늘 때마다 빠진다(AI 주문 · 온보딩 펀드 ·
        일괄 삭제가 실제로 빠져 있었다). 경로가 아니라 **주문이 나가는 함수**가 문을 든다.
    """
    who = getattr(request.state, "caller", None)
    if who is None or who.fresh:
        return
    raise HTTPException(
        401,
        {
            "detail": "보안 확인이 필요하다 — 구글 재인증 뒤 다시 시도한다",
            "reauth": "/auth/login?force=1",
        },
    )


def require_market_trade(request: Request, market: Market) -> None:
    """이 시장 갈래에서 판·펀드를 열 권한(T242 `trade`)이 없으면 403.

    Args:
        request: 요청 (`state.caller`). 호출자가 없으면(시험 우회) 통과.
        market: 띄우려는 시장.

    Raises:
        HTTPException: 403 `market_forbidden`.
    """
    who = getattr(request.state, "caller", None)
    if who is None:
        return
    group = group_of(market)
    if not who.market(group).trade:
        raise HTTPException(
            403,
            {
                "code": "market_forbidden",
                "market": market.value,
                "group": group,
                "message": f"{market.value} 시장에서 거래할 권한이 없다 — 관리자가 준다",
            },
        )


def _playbooks_json(
    item: Account, picked: Collection | None, grants: Mapping[str, PlaybookGrant], *, audit: bool
) -> list[dict[str, Any]]:
    """관리자 표 한 줄의 매매법 칸 — 선언된 매매법마다 유효 권한 + 덮어쓰기 여부."""
    policy = policy_of(picked, item.role)
    out: list[dict[str, Any]] = []
    for book in load_playbooks():
        eff = effective_grant(
            book.playbook_id, policy=policy, row=grants.get(book.playbook_id), audit=audit
        )
        out.append(
            {
                **eff.as_json(),
                "label": book.label or book.attribution,
                "custom": book.playbook_id in grants,
            }
        )
    return out


def _markets_json(
    item: Account, picked: Collection | None, grants: Mapping[str, MarketGrant], *, audit: bool
) -> list[dict[str, Any]]:
    """관리자 표 한 줄의 시장 칸 — 갈래 셋마다 유효 권한 + 덮어쓰기 여부 (T242)."""
    policy = market_policy_of(picked, item.role)
    return [
        {
            **effective_market_grant(
                group, policy=policy, row=grants.get(group), audit=audit
            ).as_json(),
            "custom": group in grants,
        }
        for group in GROUPS
    ]


def _caps_of(account: Account, table: dict[str, Collection]) -> frozenset[Cap]:
    """계정 행의 유효 권한."""
    return effective_caps(
        role=account.role,
        collection=account.role_collection or "",
        extra=parse_caps(account.extra_caps),
        table=table,
    )


async def caller_of(request: Request) -> Caller | None:
    """쿠키를 읽어 **누구인가**를 낸다.

    Args:
        request: 요청. 세션 쿠키만 본다.

    Returns:
        호출자. 쿠키가 없거나 서명이 상했거나 만료됐으면 None.

    Note:
        ⛔ 예외를 밖으로 안 낸다 — 쪽지가 이상한 것은 "로그인 안 함" 과 같은 처리다.
        여기서 500 이 나면 로그인 화면조차 못 뜬다.
    """
    bearer = request.headers.get("authorization", "")
    if bearer.startswith("Bearer ") and bearer[7:].strip().startswith(TOKEN_PREFIX):
        # ⭐ T263 — 개인 토큰. 쪽지가 아니라 표에서 주인을 찾는다. 되돌린 토큰은 없는 것과 같다.
        email_of_token = await token_owner(bearer[7:].strip())
        if email_of_token is None:
            return None
        email, fresh, auth_at, via_token = email_of_token, False, 0.0, True
        issued_at = None
    else:
        token = request.cookies.get(COOKIE, "")
        if not token:
            return None
        try:
            app = google_app()
            note = read_note(token, app.session_secret, now=time.time())
        except (AuthNotConfiguredError, BadTokenError):
            return None
        email, fresh, auth_at = note.email, note.fresh(time.time()), note.auth_at
        via_token = False
        issued_at = note.issued_at
    # 🔴 **실계좌 서버는 게스트 쪽지를 안 받는다** (T221). 데모 서버가 발급한 게스트 쿠키는
    #    서명이 같아 여기서도 풀리지만, 게스트는 실계좌를 **보지도** 못해야 한다. 계정 행이
    #    없어 막히는 것에 기대지 않고 여기서 명시적으로 거른다 — 행이 생기는 사고가 문을 열면
    #    안 된다.
    if on_real_money() and is_guest_email(email):
        return None
    found = await account_of(email)
    if found is None or found.blocked:
        return None
    # ⭐ T267 #9 — 로그아웃 뒤의 옛 쪽지는 서명이 맞아도 없는 것과 같다.
    cutoff = found.sessions_invalid_before
    if issued_at is not None and cutoff is not None and issued_at < cutoff.timestamp():
        return None
    table = await collections()
    caps = _caps_of(found, table)
    return Caller(
        email=found.email,
        role=found.role,
        fresh=fresh,
        auth_at=auth_at,
        via_token=via_token,
        audit=Cap.AUDIT in caps,
        caps=caps,
        collection=found.role_collection or "",
        policy=policy_of(table.get(found.role_collection or ""), found.role),
        playbook_rows=await playbook_rows_of(found.email),
        market_policy=market_policy_of(table.get(found.role_collection or ""), found.role),
        market_rows=await market_rows_of(found.email),
        standing=standing_of(
            role=found.role,
            blocked=found.blocked,
            created_at=found.created_at,
            hold_released_until=found.hold_released_until,
            now=datetime.now(UTC),
            hold_after=await hold_after(),
        ),
    )


HOLD_AFTER_KEY = "auth.hold_after_hours"
"""`app_settings` 의 열쇠 — 승인 없이 몇 시간 뒤 보류하나 (관리자 설정 · 2026-09-07)."""
HOLD_AFTER_MAX_HOURS = 24 * 365
HOLD_AFTER_CACHE_S = 60.0
_hold_after_cache: tuple[float, timedelta] | None = None


async def hold_after() -> timedelta:
    """승인 없이 얼마나 지나면 보류하나 — 관리자 설정, 없으면 `HOLD_AFTER`(30일).

    Returns:
        유예 시간.

    Note:
        🔴 미들웨어가 **매 요청** 부른다 — DB 를 매번 읽지 않고 60초 캐시한다. 관리자가 바꾸면
        `set_hold_after` 가 캐시를 비우므로 그 프로세스에는 즉시, 다른 프로세스(팔로워)에는
        1분 안에 듣는다. 못 읽으면 기본값 — 설정 조회 실패가 문을 잠그면 안 된다.
    """
    global _hold_after_cache
    now = time.monotonic()
    if _hold_after_cache is not None and now - _hold_after_cache[0] < HOLD_AFTER_CACHE_S:
        return _hold_after_cache[1]
    value = HOLD_AFTER
    if _factory is not None:
        try:
            async with _store()() as session:
                raw = await session.scalar(
                    sa.select(AppSetting.value).where(AppSetting.key == HOLD_AFTER_KEY)
                )
            if raw:
                hours = float(raw)
                if 1 <= hours <= HOLD_AFTER_MAX_HOURS:
                    value = timedelta(hours=hours)
        except Exception as exc:
            _logger.warning("hold_after_unreadable: %s", str(exc)[:160])
    _hold_after_cache = (now, value)
    return value


async def set_hold_after(hours: float, *, by: str) -> timedelta:
    """보류 유예를 바꾼다 — 관리자 화면이 부른다.

    Args:
        hours: 시간 (1 ~ `HOLD_AFTER_MAX_HOURS`).
        by: 바꾼 사람.

    Returns:
        저장된 유예.

    Raises:
        HTTPException: 400 범위 밖.
    """
    global _hold_after_cache
    if not 1 <= hours <= HOLD_AFTER_MAX_HOURS:
        raise HTTPException(400, f"보류 기준은 1~{HOLD_AFTER_MAX_HOURS}시간")
    factory = _store()
    async with factory() as session, session.begin():
        found = await session.get(AppSetting, HOLD_AFTER_KEY)
        if found is None:
            session.add(AppSetting(key=HOLD_AFTER_KEY, value=str(hours)))
        else:
            found.value = str(hours)
    _hold_after_cache = None
    _logger.info("hold_after_changed", payload={"hours": hours, "by": by})
    return timedelta(hours=hours)


def _hours(span: timedelta) -> float:
    """유예를 시간(소수)으로 — 화면이 그대로 보여 준다."""
    return round(span.total_seconds() / 3600, 2)


def exchanges_ready() -> list[str]:
    """**주문 어댑터를 실제로 얻을 수 있는** 거래소 — 키가 없으면 빠진다 (2026-09-06).

    🔴 설정(`UPDOWN_MARKETS`)이 아니라 **키**로 판정한다 (사용자 지적: *"ENV 기준으로 분기
    가르고 있는 건 아니겠지?"*). 목록은 설정이 좁히지만, 들어가려면 `order_adapter` 가 그 거래소의
    자격증명을 읽어 어댑터를 돌려줘야 한다 — 라이브면 실계좌 키, 그 외면 테스트넷 키. 빈 목록 =
    이 API 로는 어느 계좌에도 닿을 수 없다 → 화면은 첫 진입을 데모로 돌리고, 실계좌 모드로 굳이
    오면 "테스트넷 API 키 설정이 필요합니다".

    ⚠️ 거래소를 **부르지는 않는다.** `/auth/me` 는 로그인 전에도 누구나 1분마다 두드리는 창구라,
    여기서 잔액을 조회하면 익명 요청이 거래소 요율을 태운다(S2 · 403 2,498건의 재판). 실제 호출
    검증은 콘솔 카드(잔액)가 한다.

    Returns:
        어댑터를 얻을 수 있는 거래소 이름들 (`UPDOWN_MARKETS` 순서). 빈 목록이면 이 API 로는
        어느 계좌에도 닿을 수 없다.
    """
    from updown.common.domain.instrument import Market
    from updown.execution.gateway import order_adapter
    from updown.marketdata.provider import MarketDataProvider

    provider = MarketDataProvider()
    ready: list[str] = []
    for name in provider.live_markets():
        try:
            order_adapter(provider.adapter_for(Market(name)), user_id="me")
        except Exception:
            continue
        ready.append(name)
    return ready


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    """화면이 *"나 누구야"* 를 묻는 창구 — **로그인 안 해도 답한다**.

    Args:
        request: 요청. 쿠키로 호출자를 알아본다.

    Returns:
        `{signed_in, email, role, fresh, configured}`.

    Note:
        ⚠️ 여기 응답에는 **계좌·성적·주문이 한 톨도 없다** (`PUBLIC_PATHS` 규약).
        화면이 로그인 화면을 그릴지 정하려면 이 창구가 필요하다.
    """
    try:
        google_app()
        configured = True
    except AuthNotConfiguredError:
        configured = False
    who = await caller_of(request)
    # ⭐ 어느 서버가 답했나 — 실계좌(live) 인가 데모(paper·dev) 인가 (T221). 화면의 Demo Trading
    #    표시가 이것을 본다. nginx 가 `updown_mode` 쿠키로 요청을 갈라 보내므로, 이 값은
    #    "지금 내 요청이 닿은 곳" 이다.
    mode = "live" if on_real_money() else "demo"
    exchanges = exchanges_ready()
    if who is None:
        return {"signed_in": False, "configured": configured, "mode": mode, "exchanges": exchanges}
    return {
        "signed_in": True,
        "configured": configured,
        "mode": mode,
        "exchanges": exchanges,
        "guest": who.role is Role.GUEST,
        "email": who.email,
        "role": who.role.value,
        "fresh": who.fresh,
        # ⭐ 화면의 은행식 인증 타이머 (사용자 요청 2026-09-03) — 재인증 기한 시각.
        "fresh_until": who.auth_at + FRESH_S,
        # ⭐ 기능별 권한 (2026-09-07) — 화면은 이 목록으로 단추를 켜고 끈다. 판정은 서버가 한다.
        "caps": sorted(cap.value for cap in who.granted),
        "collection": who.collection,
        "may_trade": who.has(Cap.LIVE_TRADE if on_real_money() else Cap.DEMO_TRADE),
        # ⭐ T242 — 시장 갈래별 권한. 화면이 스위치·판 시작 칸을 잠그고, 판정은 서버가 다시 한다.
        "markets": {group: who.market(group).as_json() for group in GROUPS},
        "may_admin": who.has(Cap.MANAGE_USERS),
        "may_roles": who.has(Cap.MANAGE_ROLES),
        # ⭐ 감사 권한 — 백테스트·합성 미래의 최종 손익·연차별 손익을 보나 (사용자 2026-09-06).
        "may_audit": may_audit(who.role, who.audit),
        # ⭐ 어느 돈이 도는지 — 상단바가 늘 보여 준다 (T220 UX 점검 2026-09-05). 계좌 값은 아니다.
        "real_money": on_real_money(),
        # ⭐ 처지 (2026-09-07) — 보류면 화면은 문의 카드만 그린다. 다른 경로는 어차피 403 이다.
        "standing": who.standing.value,
        "held": who.held,
        "hold_at": await _hold_at_of(who),
        "hold_after_hours": _hours(await hold_after()),
    }


async def _hold_at_of(who: Caller) -> str | None:
    """승인 대기인 사람에게 **언제 보류되는지** — 화면이 남은 시간을 그린다.

    Args:
        who: 호출자.

    Returns:
        ISO 시각. 대기가 아니거나 계정을 못 읽으면 None.
    """
    if who.standing is not Standing.PENDING:
        return None
    found = await account_of(who.email)
    if found is None:
        return None
    span = await hold_after()
    if found.hold_released_until is not None:
        # 관리자가 풀어 준 기한이 가입+유예보다 늦으면 그것이 다음 보류 시각이다.
        starts = hold_starts_at(found.created_at, hold_after=span)
        until = found.hold_released_until
        if starts is None or until > starts:
            return until.isoformat()
    starts = hold_starts_at(found.created_at, hold_after=span)
    return starts.isoformat() if starts else None


@router.post("/logout")
async def logout(request: Request) -> Response:
    """세션 쿠키를 지우고, 서버에도 "이 시각 전 쪽지는 무효" 를 적는다 (T267 #9).

    Args:
        request: 요청 — 쿠키의 주인을 알아 그 계정의 `sessions_invalid_before` 를 지금으로.

    Returns:
        204. 쿠키 삭제 헤더가 실린다.

    Note:
        게스트는 한 계정 행을 여럿이 쓰므로 서버측 폐기를 하지 않는다 — 한 사람의 로그아웃이
        다른 게스트를 내쫓으면 안 된다. 개인 토큰(Bearer)은 쿠키가 아니라 여기 해당 없음.
    """
    made = Response(status_code=204)
    made.delete_cookie(COOKIE, path="/")
    who = await caller_of(request)
    if who is not None and not who.via_token and who.role is not Role.GUEST:
        factory = _store()
        async with factory() as session, session.begin():
            found = await session.scalar(sa.select(Account).where(Account.email == who.email))
            if found is not None:
                found.sessions_invalid_before = datetime.now(UTC)
        _logger.info("logout_revoked", payload={"email": who.email})
    return made


MODE_COOKIE = "updown_mode"
"""Demo Trading 스위치 — nginx 가 이 쿠키(`demo`/`live`)를 보고 요청을 데모 API 로 갈라 보낸다
(T221).

⚠️ **httpOnly 가 아니다** — 화면의 스위치가 JS 로 바꾼다. 이 쿠키는 권한이 아니라 **행선지**다:
어디로 가든 각 서버가 자기 DB 의 계정·등급으로 다시 판정한다. 값을 조작해 얻는 것은 "다른 서버에
물어보기" 뿐이다.
"""
GUEST_AGE_S = 60 * 60 * 4
"""게스트 세션 수명 — 4시간. 둘러보기용이라 길 필요가 없다."""


@router.post("/guest")
async def guest(request: Request) -> Response:
    """게스트 입장 — 구글 없이 단추 하나로 **데모(테스트넷) 읽기 전용** 세션을 발급한다 (T221).

    Args:
        request: 요청. 쿠키의 `Secure` 여부를 정하는 데 쓴다.

    Returns:
        게스트 세션 쿠키가 실린 응답.

    Raises:
        HTTPException: 404 실계좌 서버 · 503 계정 저장소/OAuth 설정 없음 · 403 게스트 입장이
            막혀 있음.

    Note:
        🔴 **데모 서버에서만** 답한다. 실계좌 서버(`APP_ENV=live`)는 404 — 그 서버에는
        게스트라는 개념이 없다. nginx 도 이 경로를 늘 데모 API 로 보낸다(이중 안전).

        계정 행 `guest@demo` 는 데모 DB 에 한 줄로 산다(없으면 만든다 · 등급 GUEST · 승격 대상
        아님). 여러 사람이 같은 행을 공유한다 — 게스트는 아무것도 만들 수 없으므로 소유가 성립하지
        않는다.
    """
    if on_real_money():
        raise HTTPException(
            404, "실계좌 서버에는 게스트 입장이 없다 — Demo Trading 으로 전환해서 들어온다"
        )
    factory = _store()
    app = google_app()  # 세션 비밀키 — 구글 설정과 같은 곳에서 읽는다
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == GUEST_EMAIL))
        if found is None:
            session.add(
                Account(
                    email=GUEST_EMAIL,
                    name="게스트",
                    picture="",
                    role=Role.GUEST,
                    role_collection="guest",
                    approved_at=now,
                    approved_by="시스템(게스트)",
                    last_login_at=now,
                )
            )
            _logger.info("guest_account_created", payload={"email": GUEST_EMAIL})
        else:
            if found.blocked:
                raise HTTPException(403, "게스트 입장이 막혀 있다")
            found.last_login_at = now
    stamp = time.time()
    token = issue_note(
        Session(email=GUEST_EMAIL, issued_at=stamp, auth_at=stamp), app.session_secret
    )
    made = JSONResponse({"ok": True, "mode": "demo", "guest": True})
    try:
        _bake(made, COOKIE, token, request=request, age=GUEST_AGE_S)
    except InsecureTransportError as exc:
        # 로그인(/login)과 같은 처리 — 배포 설정 문제이지 버그가 아니다. 500 대신 이유 한 줄.
        raise HTTPException(503, str(exc)) from exc
    # 행선지 쿠키 — 이후 요청이 전부 데모 API 로 가게. JS 가 읽고 바꿔야 하므로 httpOnly 가 아니다.
    made.set_cookie(
        MODE_COOKIE,
        "demo",
        max_age=60 * 60 * 24 * 365,
        secure=secure_cookies(request),
        samesite="lax",
        path="/",
    )
    _logger.info("guest_signed_in", payload={"ip": request.client.host if request.client else ""})
    return made


@router.get("/users")
async def users() -> dict[str, Any]:
    """가입한 사람들 — 관리자 콘솔이 읽는다 (요구 ②).

    Returns:
        `{rows: [...]}`. 승인 대기가 **먼저** 온다.

    Raises:
        HTTPException: 503 — 계정 저장소가 안 붙었다.

    Note:
        ⚠️ 미들웨어가 이미 관리자만 통과시킨다 (`ADMIN_PREFIXES`). 여기서 또 검사하지
        않는다 — 두 곳에서 검사하면 한쪽만 고치는 날이 온다.
    """
    factory = _store()
    async with factory() as session:
        found = list(
            await session.scalars(
                sa.select(Account)
                .where(Account.deleted_at.is_(None))
                .order_by(Account.created_at.desc())
            )
        )
        open_counts = {
            email: int(count)
            for email, count in await session.execute(
                sa.select(AccountContact.email, sa.func.count())
                .where(AccountContact.handled_at.is_(None))
                .group_by(AccountContact.email)
            )
        }
        grants_by_email: dict[str, dict[str, PlaybookGrant]] = {}
        for grant in await session.scalars(sa.select(PlaybookGrantRow)):
            grants_by_email.setdefault(grant.email, {})[grant.playbook_id] = PlaybookGrant(
                grant.playbook_id, grant.view, grant.backtest, grant.trade
            )
        markets_by_email: dict[str, dict[str, MarketGrant]] = {}
        for mg in await session.scalars(sa.select(MarketGrantRow)):
            markets_by_email.setdefault(mg.email, {})[mg.market_group] = MarketGrant(
                mg.market_group, mg.view, mg.backtest, mg.trade
            )
    now = datetime.now(UTC)
    span = await hold_after()
    table = await collections()
    # 보류·대기가 먼저 — 관리자가 할 일이 있는 줄부터. 그다음 등급 순.
    order = {Role.PENDING: 0, Role.VIEWER: 1, Role.TRADER: 2, Role.ADMIN: 3}
    rows = [
        _account_row(
            item,
            now=now,
            contacts_open=open_counts.get(item.email, 0),
            hold_after=span,
            table=table,
            grants=grants_by_email.get(item.email),
            market_grants=markets_by_email.get(item.email),
        )
        for item in found
    ]
    rank = {"held": 0, "pending": 1, "active": 2, "blocked": 3}
    rows.sort(key=lambda row: (rank.get(str(row["standing"]), 9), order.get(Role(row["role"]), 9)))
    return {
        "rows": rows,
        "now": now.isoformat(),
        "hold_after_hours": _hours(span),
        # ⭐ 이 표가 어느 서버의 계정인가 — 계정·등급은 서버(실계좌/데모)마다 따로다 (2026-09-07).
        "mode": "live" if on_real_money() else "demo",
    }


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    """관리자 설정 — 지금은 보류 유예 하나 (2026-09-07).

    Returns:
        `{hold_after_hours, max_hours}` — 지금 값과 화면이 허용할 상한.
    """
    return {"hold_after_hours": _hours(await hold_after()), "max_hours": HOLD_AFTER_MAX_HOURS}


@router.post("/settings")
async def put_settings(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """관리자 설정을 바꾼다 — `{hold_after_hours}`.

    Args:
        request: 요청. 누가 바꿨는지 남긴다.
        payload: `{hold_after_hours: number}`.

    Returns:
        저장된 값.

    Raises:
        HTTPException: 400 값 형식·범위 · 503 저장소 없음.
    """
    who = getattr(request.state, "caller", None)
    try:
        hours = float(payload.get("hold_after_hours", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "hold_after_hours 는 숫자다") from exc
    span = await set_hold_after(hours, by=who.email if who else "?")
    return {"hold_after_hours": _hours(span), "max_hours": HOLD_AFTER_MAX_HOURS}


def _account_row(
    item: Account,
    *,
    now: datetime,
    contacts_open: int = 0,
    hold_after: timedelta = HOLD_AFTER,
    table: dict[str, Collection] | None = None,
    grants: Mapping[str, PlaybookGrant] | None = None,
    market_grants: Mapping[str, MarketGrant] | None = None,
) -> dict[str, Any]:
    """관리자 표의 한 줄 — 등급과 처지를 같이 낸다 (2026-09-07).

    Args:
        item: 계정.
        now: 처지 판정 시각.
        contacts_open: 아직 처리 안 한 문의 수.
        hold_after: 보류 유예 (관리자 설정).
        table: 권한 묶음 표 — 없으면 내장값.
        grants: 이 사람의 매매법 덮어쓰기 행들 (T230). 없으면 묶음 기본값만으로 그린다.
        market_grants: 이 사람의 시장 덮어쓰기 행들 (T242). 없으면 묶음 기본값만으로 그린다.

    Returns:
        JSON 으로 나가는 행.
    """
    standing = standing_of(
        role=item.role,
        blocked=item.blocked,
        created_at=item.created_at,
        hold_released_until=item.hold_released_until,
        now=now,
        hold_after=hold_after,
    )
    hold_at = hold_starts_at(item.created_at, hold_after=hold_after)
    lookup = table if table is not None else dict(BUILTIN_BY_NAME)
    caps = _caps_of(item, lookup)
    picked = lookup.get(item.role_collection or "")
    return {
        "id": str(item.id),
        "email": item.email,
        "name": item.name,
        "picture": item.picture,
        "role": item.role.value,
        "blocked": item.blocked,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
        # ⭐ 기능별 권한 (2026-09-07) — 묶음 + 개별. 옛 플래그 둘은 파생값이다.
        "collection": item.role_collection or "",
        "collection_label": picked.label
        if picked
        else ("게스트" if item.role is Role.GUEST else "승인 대기"),
        "caps": sorted(cap.value for cap in caps),
        "extra_caps": sorted(cap.value for cap in parse_caps(item.extra_caps)),
        "audit": Cap.AUDIT in caps,
        "demo_trade": Cap.DEMO_TRADE in caps,
        # ⭐ 매매법별 권한 (T230) — 선언된 매매법마다 보기·백테스트·사용 + 덮어쓰기 여부
        "playbooks": _playbooks_json(item, picked, grants or {}, audit=Cap.AUDIT in caps),
        "markets": _markets_json(item, picked, market_grants or {}, audit=Cap.AUDIT in caps),
        "standing": standing.value,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "approved_at": item.approved_at.isoformat() if item.approved_at else None,
        "approved_by": item.approved_by,
        "last_login_at": (item.last_login_at.isoformat() if item.last_login_at else None),
        "hold_at": hold_at.isoformat() if hold_at else None,
        "hold_released_until": (
            item.hold_released_until.isoformat() if item.hold_released_until else None
        ),
        "note": item.note,
        "contacted_at": item.contacted_at.isoformat() if item.contacted_at else None,
        "contacts_open": contacts_open,
    }


@router.post("/users/{email}/hold")
async def set_hold(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """보류를 풀거나(기한을 두고) 다시 건다 (사용자 2026-09-07).

    Args:
        request: 요청. 누가 했는지 남긴다.
        email: 대상.
        payload: `{days}` — 양수면 그 날수만큼 보류를 푼다(그 뒤 다시 보류). 0 이면 지금 바로
            보류 규칙으로 되돌린다(하루가 지났으면 즉시 보류).

    Returns:
        갱신된 행.

    Raises:
        HTTPException: 503 저장소 없음 · 404 계정 없음 · 400 값 범위.

    Note:
        승인(`role=viewer`)이 보류를 **영구히** 푸는 길이고, 이것은 *"조금 더 둘러보게 두자"* 다.
        보류는 승인 대기에만 있으므로 승인된 사람에게는 아무 효과가 없다.
    """
    who = getattr(request.state, "caller", None)
    factory = _store()
    target = email.strip().lower()
    try:
        days = int(payload.get("days", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "days 는 정수다") from exc
    if not 0 <= days <= 365:
        raise HTTPException(400, "days 는 0~365")
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        found.hold_released_until = now + timedelta(days=days) if days > 0 else None
        _logger.info(
            "account_hold_changed",
            payload={"email": target, "days": days, "by": who.email if who else "?"},
        )
        row = _account_row(found, now=now, hold_after=await hold_after(), table=await collections())
    return row


@router.post("/users/{email}/note")
async def set_note(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """관리자 메모를 적는다 — 누구인지, 왜 승인/차단했는지 (2026-09-07).

    Args:
        request: 요청.
        email: 대상.
        payload: `{note}` (1,000자 한도).

    Returns:
        `{email, note}`.

    Raises:
        HTTPException: 503 · 404 · 400 길이.
    """
    who = getattr(request.state, "caller", None)
    factory = _store()
    target = email.strip().lower()
    note = str(payload.get("note", "")).strip()
    if len(note) > 1000:
        raise HTTPException(400, "메모는 1,000자까지")
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        found.note = note
        _logger.info(
            "account_note_changed",
            payload={"email": target, "chars": len(note), "by": who.email if who else "?"},
        )
    return {"email": target, "note": note}


@router.delete("/users/{email}")
async def delete_user(request: Request, email: str) -> dict[str, Any]:
    """계정을 지운다 — 차단과 다르다 (2026-09-07).

    Args:
        request: 요청.
        email: 대상.

    Returns:
        `{email, deleted: true}`.

    Raises:
        HTTPException: 503 · 404 · 400 자기 자신 · 관리자.

    Note:
        🔴 **소프트 삭제다** (T266 · 2026-09-10). 행을 지우지 않고 `deleted_at` + `blocked` 를
        적는다.
        같이 **토큰을 되돌리고 권한 행(플레이북·시장)을 비운다** — 하드 삭제 때는 이메일로 매인 이
        행들이 남아, 같은 이메일이 다시 로그인하면 새 계정에 옛 권한·토큰이 그대로 붙었다.
        다시 오면 `_touch_account` 가 **대기 계정으로 되살린다**(권한 0). 다시 못 오게 하려면 차단.
        문의 기록·채팅 스레드는 남긴다 — 표에 사람이 없어도 언제 두드렸는지는 기록이다.
    """
    who = getattr(request.state, "caller", None)
    factory = _store()
    target = email.strip().lower()
    if who is not None and who.email == target:
        raise HTTPException(400, "자기를 지울 수 없다")
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None or found.deleted_at is not None:
            raise HTTPException(404, f"{target} 계정이 없다")
        if found.role is Role.ADMIN:
            raise HTTPException(400, "관리자는 지울 수 없다 — 먼저 등급을 내린다")
        erase_account(found, now)
        revoked = await session.execute(
            sa.update(ApiToken)
            .where(ApiToken.email == target, ApiToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        grants = await session.execute(
            sa.delete(PlaybookGrantRow).where(PlaybookGrantRow.email == target)
        )
        markets = await session.execute(
            sa.delete(MarketGrantRow).where(MarketGrantRow.email == target)
        )
        _logger.info(
            "account_deleted",
            payload={
                "email": target,
                "by": who.email if who else "?",
                "tokens_revoked": _rows_touched(revoked),
                "grants_dropped": _rows_touched(grants) + _rows_touched(markets),
            },
        )
    return {"email": target, "deleted": True}


def _rows_touched(result: object) -> int:
    """UPDATE/DELETE 결과의 행 수 — 로그용. 커서가 아니면 0."""
    if isinstance(result, CursorResult):
        return int(cast("CursorResult[Any]", result).rowcount)
    return 0


def erase_account(found: Account, now: datetime) -> None:
    """계정을 소프트 삭제 상태로 만든다 — 행은 남고 문은 닫힌다 (T266).

    Args:
        found: 계정 행.
        now: 삭제 시각.
    """
    found.deleted_at = now
    found.blocked = True


def revive_account(found: Account, now: datetime) -> None:
    """지운 계정이 다시 로그인했다 — **새 대기 계정처럼** 되살린다 (T266).

    Args:
        found: 계정 행.
        now: 로그인 시각.

    Note:
        등급·묶음·승인·감사 깃발을 전부 0 으로 되돌린다. 권한 행과 토큰은 지울 때 이미 없앴다.
        되살린 사람이 예전에 무엇이었는지는 `event_logs`·앱 로그가 안다 — 표는 현재 상태다.
    """
    found.deleted_at = None
    found.blocked = False
    found.role = Role.PENDING
    found.role_collection = ""
    found.approved_at = None
    found.approved_by = ""
    found.audit = False
    found.last_login_at = now


@router.get("/contacts")
async def contacts(limit: int = 200) -> dict[str, Any]:
    """관리자 문의 목록 — 처리 안 한 것이 먼저, 최신순 (2026-09-07).

    Args:
        limit: 최대 행 수.

    Returns:
        `{rows, open}` — `open` 은 처리 안 한 수.
    """
    factory = _store()
    limit = max(1, min(limit, 1000))
    async with factory() as session:
        found = list(
            await session.scalars(
                sa.select(AccountContact)
                .order_by(AccountContact.handled_at.is_not(None), AccountContact.created_at.desc())
                .limit(limit)
            )
        )
    rows = [
        {
            "id": str(item.id),
            "email": item.email,
            "message": item.message,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "mailed": item.mailed,
            "handled_at": item.handled_at.isoformat() if item.handled_at else None,
            "handled_by": item.handled_by,
        }
        for item in found
    ]
    return {"rows": rows, "open": sum(1 for row in rows if row["handled_at"] is None)}


@router.post("/contacts/{contact_id}/handled")
async def contact_handled(request: Request, contact_id: str) -> dict[str, Any]:
    """문의 하나를 처리했다고 표시한다.

    Args:
        request: 요청. 누가 처리했는지 남긴다.
        contact_id: 문의 id.

    Returns:
        `{id, handled_at}`.

    Raises:
        HTTPException: 503 · 404 · 400 id 형식.
    """
    who = getattr(request.state, "caller", None)
    factory = _store()
    try:
        key = uuid.UUID(contact_id)
    except ValueError as exc:
        raise HTTPException(400, "문의 id 형식이 아니다") from exc
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        found = await session.get(AccountContact, key)
        if found is None:
            raise HTTPException(404, "그 문의가 없다")
        if found.handled_at is None:
            found.handled_at = now
            found.handled_by = who.email if who else "?"
    return {"id": contact_id, "handled_at": now.isoformat()}


CONTACT_SUBJECT = "[업앤다운] 계정 승인 문의"


@router.post("/contact")
async def contact_admin(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """보류·대기 중인 사람이 관리자에게 문의한다 — 기록하고, 메일이 설정돼 있으면 보낸다.

    Args:
        request: 요청. 쿠키로 호출자를 알아본다 (공개 경로라 미들웨어가 안 붙인다).
        payload: `{message}` (선택 · 1,000자).

    Returns:
        `{recorded, mailed, recipients}` — `recipients` 는 **수**다. 관리자 주소는 안 나간다.

    Raises:
        HTTPException: 401 로그인 없음 · 403 게스트/승인된 계정(문의할 이유가 없다) ·
            429 간격 안 · 503 저장소 없음.

    Note:
        🔴 메일 발송은 SMTP 라 스레드로 보낸다 — 이벤트 루프를 몇 초 세우면 콘솔 폴링이 같이 선다
        (T225). 발송 실패는 기록에 `mailed=false` 로 남고 관리자 화면이 보여 준다 — 조용히 삼키지
        않는다 (절대 규칙 #8).
    """
    who = await caller_of(request)
    if who is None:
        raise HTTPException(401, "로그인이 필요하다")
    if who.role is Role.GUEST:
        raise HTTPException(403, "게스트는 문의할 수 없다 — 구글로 로그인한다")
    if who.standing is Standing.ACTIVE:
        raise HTTPException(403, "이미 승인된 계정이다")
    message = str(payload.get("message", "")).strip()[:1000]
    factory = _store()
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == who.email))
        if found is None:
            raise HTTPException(401, "로그인이 필요하다")
        if not contact_allowed(found.contacted_at, now):
            raise HTTPException(429, "문의를 이미 보냈다 — 30분 뒤 다시 보낼 수 있다")
        found.contacted_at = now
        row = AccountContact(email=who.email, message=message)
        session.add(row)
        row_id = row.id
    mailed, recipients = await _mail_admins(
        subject=f"{CONTACT_SUBJECT} — {who.email}",
        body=_contact_body(who, message=message, now=now),
    )
    if mailed:
        async with factory() as session, session.begin():
            saved = await session.get(AccountContact, row_id)
            if saved is not None:
                saved.mailed = True
    _logger.info(
        "account_contact",
        payload={"email": who.email, "standing": who.standing.value, "mailed": mailed},
    )
    return {"recorded": True, "mailed": mailed, "recipients": recipients}


_signup_tasks: set[asyncio.Task[None]] = set()
"""떠 있는 가입 알림 태스크 — 참조를 잃으면 GC 가 중간에 죽인다."""

SIGNUP_SUBJECT = "[업앤다운] 새 가입자"


def signup_body(email: str, name: str, *, now: datetime, hold_after: timedelta) -> str:
    """관리자에게 가는 새 가입자 알림 본문 — 누가 · 언제 · 언제 보류되나.

    Args:
        email: 가입한 이메일.
        name: 구글 이름 (없을 수 있다).
        now: 가입 시각 (aware).
        hold_after: 지금 설정된 유예 — 그 뒤엔 보류된다.

    Returns:
        평문 본문.
    """
    hold_at = now + hold_after
    lines = [
        "업앤다운 새 가입자 (승인 대기)",
        "",
        f"이메일: {email}",
        f"이름: {name or '(없음)'}",
        f"가입 시각: {now.isoformat(timespec='seconds')}",
        f"보류 예정: {hold_at.isoformat(timespec='seconds')} "
        f"(유예 {hold_after.days}일 {hold_after.seconds // 3600}시간)",
        "",
        "관리 화면 → 계정 에서 승인 · 유예 연장 · 차단을 정한다. 승인 전까지는 데모만 볼 수 있다.",
    ]
    return "\n".join(lines)


async def _notify_signup(email: str, name: str, *, now: datetime) -> None:
    """새 가입자 메일 — 결과는 로그로만 (`signup_notice_mailed` · `signup_notice_failed`)."""
    try:
        mailed, recipients = await _mail_admins(
            subject=f"{SIGNUP_SUBJECT} — {email}",
            body=signup_body(email, name, now=now, hold_after=await hold_after()),
        )
    except Exception:
        _logger.exception("signup_notice_failed", payload={"email": email})
        return
    _logger.info(
        "signup_notice_mailed" if mailed else "signup_notice_skipped",
        payload={"email": email, "mailed": mailed, "recipients": recipients},
    )


def _contact_body(who: Caller, *, message: str, now: datetime) -> str:
    """관리자에게 가는 본문 — 누가 · 어떤 처지 · 무슨 말."""
    lines = [
        "업앤다운 계정 승인 문의",
        "",
        f"이메일: {who.email}",
        f"처지: {who.standing.value} ({HOLD_MESSAGE if who.held else '승인 대기'})",
        f"시각: {now.isoformat(timespec='seconds')}",
        "",
        "메시지:",
        message or "(없음)",
        "",
        "관리 화면 → 계정 에서 승인·보류 해제·차단을 정한다.",
    ]
    return "\n".join(lines)


async def _mail_admins(*, subject: str, body: str) -> tuple[bool, int]:
    """설정에 적힌 관리자들에게 메일을 보낸다.

    Args:
        subject: 제목.
        body: 본문.

    Returns:
        `(보냈나, 수신자 수)`. SMTP 나 수신자가 없으면 `(False, 0)` — 안 보냈다는 사실을 돌려준다.
    """
    from updown.common.config import load_settings
    from updown.orchestration.report.daily import mail_settings_of
    from updown.orchestration.report.mail import send_mail

    settings = load_settings()
    raw = settings.admin_emails or settings.report_to or ""
    to = [item.strip() for item in raw.split(",") if item.strip()]
    mail = mail_settings_of(settings)
    if mail is None or not to:
        return False, len(to)
    try:
        sent = await asyncio.to_thread(send_mail, mail, to=to, subject=subject, body=body)
    except Exception:
        _logger.exception("contact_mail_failed")
        return False, len(to)
    return bool(sent), len(to)


@router.post("/users/{email}/blocked")
async def set_blocked(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """차단하거나 푼다.

    Args:
        request: 요청. 미들웨어가 붙인 호출자(관리자)를 읽는다.
        email: 대상 이메일.
        payload: `{blocked: bool}`. 기본은 차단.

    Returns:
        갱신된 계정 행.

    Raises:
        HTTPException: 503 저장소 없음 · 404 계정 없음 · 400 자기 자신이거나 마지막 관리자.

    Note:
        🔴 **삭제 대신 차단이다.** 지우면 같은 이메일로 다시 가입해 승인 목록에 또 뜨고,
        훑으며 누르다 실수로 승인될 수 있다.
    """
    who = getattr(request.state, "caller", None)
    factory = _store()
    target = email.strip().lower()
    wanted = bool(payload.get("blocked", True))
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        if found.deleted_at is not None:
            # 지운 계정은 `blocked` 도 True 다 — 여기서 풀면 삭제된 계정으로 로그인이 된다
            # (점검 2026-09-10)
            raise HTTPException(
                409, f"{target} 은 지운 계정이다 — 다시 로그인하면 대기 계정으로 되살아난다"
            )
        if wanted and who is not None and who.email == target:
            raise HTTPException(400, "자기를 차단할 수 없다")
        if wanted and who is not None and not who.has(Cap.MANAGE_ROLES):
            # 일반 관리자가 슈퍼 관리자를 차단해 잠그던 구멍 — 관리자 권한을 쥔 계정은
            # 슈퍼 관리자만 막는다
            table = await collections()
            if _caps_of(found, table) & ADMIN_CAPS:
                raise HTTPException(403, "관리자 권한을 쥔 계정은 슈퍼 관리자만 차단할 수 있다")
        if wanted and found.role is Role.ADMIN:
            admins = await session.scalar(
                sa.select(sa.func.count()).select_from(Account).where(Account.role == Role.ADMIN)
            )
            if (admins or 0) <= 1:
                raise HTTPException(400, "마지막 관리자다 — 차단하면 아무도 승인할 수 없다")
        found.blocked = wanted
        _logger.info(
            "account_blocked_changed",
            payload={
                "email": target,
                "blocked": wanted,
                "by": who.email if who is not None else "?",
            },
        )
    return {"email": target, "blocked": wanted}


TRADE_PATHS = (
    "/walkforward/live",
    # 🔴 보안 리뷰 (2026-09-03): 아래 여섯도 **거래소로 주문이 나가는 경로**다 —
    #    수동 매수/매도, 세션 삭제(라이브 포지션 시장가 청산), 잔량 청소, 재개, 입양.
    #    접두어에서 빠져 있어 12시간짜리 세션 쿠키만으로 재인증 없이 돈이 움직였다.
    "/walkforward/buy",
    "/walkforward/sell",
    "/walkforward/sessions",
    "/walkforward/leftovers",
    "/walkforward/resume",
    "/walkforward/adopt",
    "/exchange/",
    "/rebalancer",
    # 보안 점검 (2026-09-10): AI 주문 확정 · 온보딩 펀드 생성도 거래소로 주문이 나간다.
    "/ai/chat/orders",
    "/assistant/create",
)
"""**재인증까지 요구하는** 경로 앞자리 (요구 ③).

사용자 확정 2026-08-30: *"실제 거래소에 주문이 왔다 갔다 할 때 보안 확인 필수"*.

⚠️ 거래 권한(`Need.TRADE`)과 **다른 문**이다. 권한은 *"이 사람이 해도 되나"* 이고
재인증은 *"방금 사람이 거기 있었나"* 다 — 노트북을 열어 둔 채 자리를 비우면 앞의
문은 통과하고 뒤의 문은 막힌다.

⛔ 모든 쓰기에 걸지 않는다. 판 일시정지·설정 변경까지 재인증을 요구하면
사람이 로그인만 하다 만다. **거래소로 주문이 나가는 경로**에만 건다.
(기한은 `common/security/session.FRESH_S` — 5분에서 1시간으로 조정 · 2026-09-03)
"""


def on_real_money() -> bool:
    """지금이 **진짜 돈이 걸린 환경인가**.

    Returns:
        `APP_ENV=live` 면 참.

    Note:
        🔴 **경로가 아니라 환경이 문을 정한다.** `/exchange/*` 와 `/walkforward/live/*` 는
        지금 전부 테스트넷을 가리키지만(`OrderGateway(AppEnv.PAPER)` · `GATE_TESTNET_*`),
        실주문이 열리면(P2-8) **같은 경로가 진짜 돈을 가리킨다** — 코드는 한 줄도 안
        바뀌는데 뜻이 바뀐다. 그날 읽기 권한을 떠올릴 것이라고 기대하면 안 된다.

        ⛔ **설정을 못 읽으면 조인다.** 여기서 관대하면 오설정이 곧 구멍이고, 그 구멍은
        조용하다 (절대 규칙 #8). 막혀서 시끄러운 쪽이 낫다.
    """
    from updown.common.config import AppEnv, load_settings

    try:
        return load_settings().app_env is AppEnv.LIVE
    except Exception:  # 설정을 못 읽으면 가장 조이는 쪽으로 (규칙 #8)
        return True


async def guard(request: Request, call_next: Any) -> Response:
    """모든 요청 앞에 서는 문 — **기본은 거부**다.

    Args:
        request: 요청.
        call_next: 다음 처리.

    Returns:
        응답. 막히면 401/403.

    Note:
        🔴 **여기가 유일한 강제 지점이다.** 화면에 로그인을 붙이는 것만으로는 아무
        소용이 없다 — URL 을 아는 사람은 API 를 직접 부른다.

        🔴 **설정이 없으면 전부 막는다.** 인증이 안 켜진 채로 외부에 열리는 것이
        막으려던 바로 그 상태다 (절대 규칙 #8). 단 `/health` 와 로그인 경로는 연다 —
        아니면 로그인조차 못 하고 컨테이너가 unhealthy 로 죽는다.

        ⚠️ **문서 경로(`/docs`·`/openapi.json`)도 막힌다.** 엔드포인트 목록은 공격자에게
        지도다. `need_for` 가 GET 을 읽기로 치므로 로그인한 사람만 본다.

        🔴 **실계좌에서는 읽기도 조인다** (사용자 확정 2026-08-30: *"실거래 콘솔이
        보이는 건 옳지 않다"*). 잔고·포지션·주문을 보여 주는 경로는 `live` 에서
        거래 권한을 요구한다 — 지금(테스트넷)은 아무 동작도 바뀌지 않는다.
    """
    need = need_for(request.method, request.url.path, live=on_real_money())
    if need is Need.PUBLIC:
        return cast("Response", await call_next(request))

    # 🔴 테스트 백도어 (사용자 승인 2026-09-02 · 기본 꺼짐) — 합성 데이터 워크 검증처럼
    #    스크립트가 API 를 두드려야 할 때만 연다. 세 겹 안전:
    #    ① AUTH_TEST_BYPASS=1 이 명시돼야 켜진다 (미설정 = 지금까지와 동일)
    #    ② 실계좌(on_real_money)면 설정돼 있어도 무시한다 — 프로덕션 전환 시 자동 무력화
    #    ③ 로컬 호스트(127.0.0.1/::1)에서 온 요청만 — 외부 노출 포트로는 안 뚫린다
    # ⭐ T263 — 자격증명(쿠키·개인 토큰)이 있으면 그 사람이다. 우회는 **아무것도 없을 때만** —
    #    안 그러면 로컬에서 토큰 경로를 실측할 수 없다(호출자가 비어 MCP 도구가 안 돈다).
    who = await caller_of(request)
    if (
        who is None
        and os.environ.get("AUTH_TEST_BYPASS") == "1"
        and not on_real_money()
        and request.client is not None
        and request.client.host in ("127.0.0.1", "::1", "localhost")
    ):
        _logger.warning("auth_test_bypass: %s %s", request.method, request.url.path)
        with actor_context("test-bypass@local"):
            return cast("Response", await call_next(request))

    request.state.caller = who
    # ⭐ 보안 점검 #7 (2026-09-11): 토큰이 드는 `/ai/*` 쓰기는 게스트에게 없다 — 끝점마다
    #    따로 거르던 것을 한 곳으로. 읽기(GET)는 그대로(리포트·대화 목록은 끝점이 판단).
    if (
        who is not None
        and who.role is Role.GUEST
        and request.url.path.startswith("/ai/")
        and request.method.upper() not in READ_METHODS
    ):
        return JSONResponse({"detail": "게스트는 AI 기능을 쓸 수 없다 — 구글 로그인 뒤에"}, 403)
    # 🔴 **누가 시작한 흐름인지 감사 로그가 알아야 한다** (2026-08-30). `trace_id` 는
    #    흐름을 잇지만 사람을 안 말해 준다 — 여기서 한 번 넣으면 async 경계를 넘어
    #    따라가므로, 로그를 남기는 모든 함수에 인자로 끌고 다닐 필요가 없다.
    with actor_context(None if who is None else who.email):
        return await _pass(request, call_next, need=need, who=who)


@dataclass(frozen=True, slots=True)
class _Actor:
    """권한을 바꾸는 사람 — 미들웨어가 붙인 호출자, 또는 시험 우회(None)."""

    email: str
    caps: frozenset[Cap]


def _actor_of(request: Request) -> _Actor | None:
    who = getattr(request.state, "caller", None)
    if who is None:
        return None
    return _Actor(email=who.email, caps=who.granted)


async def _holders_of(session: AsyncSession, cap: Cap, table: dict[str, Collection]) -> int:
    """이 기능을 쥔(차단 안 된) 계정 수 — 마지막 슈퍼 관리자 보호."""
    rows = list(await session.scalars(sa.select(Account).where(Account.blocked.is_(False))))
    return sum(1 for row in rows if cap in _caps_of(row, table))


async def _apply_grant(
    session: AsyncSession,
    found: Account,
    *,
    actor: _Actor | None,
    collection: str | None = None,
    add: Iterable[Cap] = (),
    remove: Iterable[Cap] = (),
) -> dict[str, Any]:
    """계정의 묶음·개별 기능을 바꾼다 — 모든 권한 변경이 이 한 곳을 지난다 (2026-09-07).

    Args:
        session: 열린 세션 (호출자가 begin 한다).
        found: 대상 계정.
        actor: 바꾸는 사람. None 이면 시험 우회 — 검사 없이 적용.
        collection: 새 묶음 이름. None 이면 그대로. 빈 문자열 = 묶음 없음(승인 대기).
        add: 개별로 더 줄 기능.
        remove: 개별에서 뺄 기능.

    Returns:
        갱신된 행.

    Raises:
        HTTPException: 400 모르는 묶음·기능 · 403 줄 수 없는 기능 · 400 자기 강등·마지막 슈퍼.

    Note:
        🔴 **관리 기능은 슈퍼 관리자만 준다** (`may_assign`). 관리자가 관리자를 만들거나, 관리자의
        기능을 빼는 것도 슈퍼 관리자 몫이다 — 아니면 관리자 둘이 서로를 내릴 수 있다.
    """
    table = await collections()
    before = _caps_of(found, table)
    next_collection = found.role_collection or ""
    if collection is not None:
        if collection and collection not in table:
            raise HTTPException(400, f"모르는 권한 묶음이다 — {collection!r}")
        next_collection = collection
    extra = set(parse_caps(found.extra_caps))
    extra |= set(add)
    extra -= set(remove)
    after = (
        effective_caps(
            role=found.role, collection=next_collection, extra=frozenset(extra), table=table
        )
        if next_collection or found.role is Role.GUEST
        else (
            frozenset(extra)
            | effective_caps(role=Role.PENDING, collection="", extra=frozenset(), table=table)
        )
    )
    if actor is not None:
        gained = after - before
        lost = before - after
        touched_admin = bool((gained | lost) & ADMIN_CAPS) or bool(before & ADMIN_CAPS)
        if touched_admin and Cap.MANAGE_ROLES not in actor.caps:
            raise HTTPException(403, "관리자 권한은 슈퍼 관리자만 주거나 거둘 수 있다")
        if not may_assign(actor.caps, gained):
            raise HTTPException(403, "그 기능을 줄 권한이 없다")
        if actor.email == found.email and (before & ADMIN_CAPS) - after:
            raise HTTPException(400, "자기 관리자 권한은 못 내린다")
    if Cap.MANAGE_ROLES in before and Cap.MANAGE_ROLES not in after:
        others = await _holders_of(session, Cap.MANAGE_ROLES, table) - 1
        if others <= 0:
            raise HTTPException(400, "마지막 슈퍼 관리자다 — 내리면 아무도 권한 묶음을 못 고친다")
    found.role_collection = next_collection
    found.extra_caps = dump_caps(extra)
    was = found.role
    found.role = role_for(
        after, has_collection=bool(next_collection), guest=found.role is Role.GUEST
    )
    if next_collection and found.approved_at is None:
        found.approved_at = datetime.now(UTC)
        found.approved_by = actor.email if actor else "?"
    _logger.info(
        "account_grant_changed",
        payload={
            "email": found.email,
            "collection": next_collection,
            "extra": sorted(cap.value for cap in extra),
            "role": f"{was.value}→{found.role.value}",
            "by": actor.email if actor else "?",
        },
    )
    _record_permission(
        session,
        email=found.email,
        what="caps",
        before={"collection": found.role_collection, "caps": sorted(c.value for c in before)},
        after={"collection": next_collection, "caps": sorted(c.value for c in after)},
        by=actor.email if actor else "?",
    )
    return _account_row(
        found,
        now=datetime.now(UTC),
        hold_after=await hold_after(),
        table=table,
        grants=await _grants_in(session, found.email),
    )


async def _grants_in(session: AsyncSession, email: str) -> dict[str, PlaybookGrant]:
    """열린 세션에서 한 사람의 덮어쓰기 행들 (캐시 안 거침 — 방금 바꾼 것을 그대로 보여 준다)."""
    rows = await session.scalars(sa.select(PlaybookGrantRow).where(PlaybookGrantRow.email == email))
    return {r.playbook_id: PlaybookGrant(r.playbook_id, r.view, r.backtest, r.trade) for r in rows}


@router.put("/users/{email}/playbooks/{playbook_id}")
async def set_playbook_grant(
    request: Request, email: str, playbook_id: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """한 사람의 매매법 권한을 덮어쓴다 — `{view, backtest, trade}` (T230).

    Args:
        request: 요청 (관리자).
        email: 대상.
        playbook_id: 매매법 id.
        payload: 칸 셋. 빠진 칸은 참.

    Returns:
        갱신된 계정 행.

    Raises:
        HTTPException: 400 보기 없는 백테스트/사용 · 404 계정·매매법 없음.
    """
    if playbook_id not in {book.playbook_id for book in load_playbooks()}:
        raise HTTPException(404, f"모르는 매매법이다 — {playbook_id!r}")
    grant = PlaybookGrant(
        playbook_id,
        view=bool(payload.get("view", True)),
        backtest=bool(payload.get("backtest", True)),
        trade=bool(payload.get("trade", True)),
    )
    try:
        grant.validate()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    actor = _actor_of(request)
    by = actor.email if actor else "?"
    target = email.strip().lower()
    factory = _store()
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        row = await session.get(PlaybookGrantRow, (target, playbook_id))
        before = (
            None
            if row is None
            else PlaybookGrant(playbook_id, row.view, row.backtest, row.trade).as_json()
        )
        if row is None:
            session.add(
                PlaybookGrantRow(
                    email=target,
                    playbook_id=playbook_id,
                    view=grant.view,
                    backtest=grant.backtest,
                    trade=grant.trade,
                    granted_by=by,
                )
            )
        else:
            row.view, row.backtest, row.trade, row.granted_by = (
                grant.view,
                grant.backtest,
                grant.trade,
                by,
            )
        _record_permission(
            session, email=target, what="playbook", before=before, after=grant.as_json(), by=by
        )
        await session.flush()
        invalidate_playbook_rows(target)
        table = await collections()
        return _account_row(
            found,
            now=datetime.now(UTC),
            hold_after=await hold_after(),
            table=table,
            grants=await _grants_in(session, target),
            market_grants=await _market_grants_in(session, target),
        )


@router.delete("/users/{email}/playbooks/{playbook_id}")
async def clear_playbook_grant(request: Request, email: str, playbook_id: str) -> dict[str, Any]:
    """덮어쓰기를 지운다 — 그 매매법은 묶음 기본값으로 돌아간다 (T230).

    Args:
        request: 요청 (관리자).
        email: 대상.
        playbook_id: 매매법 id.

    Returns:
        갱신된 계정 행. 덮어쓰기가 없었으면 그대로.

    Raises:
        HTTPException: 404 계정 없음.
    """
    actor = _actor_of(request)
    by = actor.email if actor else "?"
    target = email.strip().lower()
    factory = _store()
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        row = await session.get(PlaybookGrantRow, (target, playbook_id))
        if row is not None:
            before = PlaybookGrant(playbook_id, row.view, row.backtest, row.trade).as_json()
            await session.delete(row)
            _record_permission(
                session, email=target, what="playbook", before=before, after=None, by=by
            )
            await session.flush()
        invalidate_playbook_rows(target)
        table = await collections()
        return _account_row(
            found,
            now=datetime.now(UTC),
            hold_after=await hold_after(),
            table=table,
            grants=await _grants_in(session, target),
            market_grants=await _market_grants_in(session, target),
        )


async def _grant_route(
    request: Request,
    email: str,
    *,
    collection: str | None,
    add: Iterable[Cap],
    remove: Iterable[Cap],
) -> dict[str, Any]:
    factory = _store()
    target = email.strip().lower()
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        return await _apply_grant(
            session, found, actor=_actor_of(request), collection=collection, add=add, remove=remove
        )


def _caps_in(payload: dict[str, Any], key: str) -> list[Cap]:
    out: list[Cap] = []
    raw: object = payload.get(key) or []
    if not isinstance(raw, list):
        raise HTTPException(400, f"{key} 는 목록이다")
    for item in cast("list[object]", raw):
        try:
            out.append(Cap(str(item)))
        except ValueError as exc:
            raise HTTPException(400, f"모르는 기능이다 — {item!r}") from exc
    return out


async def _market_grants_in(session: AsyncSession, email: str) -> dict[str, MarketGrant]:
    """열린 세션에서 한 사람의 시장 덮어쓰기 행들 (캐시 안 거침)."""
    rows = await session.scalars(sa.select(MarketGrantRow).where(MarketGrantRow.email == email))
    return {r.market_group: MarketGrant(r.market_group, r.view, r.backtest, r.trade) for r in rows}


@router.put("/users/{email}/markets/{group}")
async def set_market_grant(
    request: Request, email: str, group: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """한 사람의 시장 권한을 덮어쓴다 — `{view, backtest, trade}` (T242).

    Args:
        request: 요청 (관리자).
        email: 대상.
        group: `coin` · `domestic` · `foreign`.
        payload: 칸 셋. 빠진 칸은 참.

    Returns:
        갱신된 계정 행.

    Raises:
        HTTPException: 400 모르는 갈래·보기 없는 백테스트/거래 · 404 계정 없음.
    """
    grant = MarketGrant(
        group,
        view=bool(payload.get("view", True)),
        backtest=bool(payload.get("backtest", True)),
        trade=bool(payload.get("trade", True)),
    )
    try:
        grant.validate()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    actor = _actor_of(request)
    by = actor.email if actor else "?"
    target = email.strip().lower()
    factory = _store()
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        row = await session.get(MarketGrantRow, (target, group))
        before = (
            None if row is None else MarketGrant(group, row.view, row.backtest, row.trade).as_json()
        )
        if row is None:
            session.add(
                MarketGrantRow(
                    email=target,
                    market_group=group,
                    view=grant.view,
                    backtest=grant.backtest,
                    trade=grant.trade,
                    granted_by=by,
                )
            )
        else:
            row.view, row.backtest, row.trade, row.granted_by = (
                grant.view,
                grant.backtest,
                grant.trade,
                by,
            )
        _record_permission(
            session, email=target, what="market", before=before, after=grant.as_json(), by=by
        )
        await session.flush()
        invalidate_market_rows(target)
        table = await collections()
        return _account_row(
            found,
            now=datetime.now(UTC),
            hold_after=await hold_after(),
            table=table,
            grants=await _grants_in(session, target),
            market_grants=await _market_grants_in(session, target),
        )


@router.delete("/users/{email}/markets/{group}")
async def clear_market_grant(request: Request, email: str, group: str) -> dict[str, Any]:
    """시장 덮어쓰기를 지운다 — 그 갈래는 묶음 기본값으로 돌아간다 (T242).

    Args:
        request: 요청 (관리자).
        email: 대상.
        group: 갈래.

    Returns:
        갱신된 계정 행.

    Raises:
        HTTPException: 404 계정 없음.
    """
    actor = _actor_of(request)
    by = actor.email if actor else "?"
    target = email.strip().lower()
    factory = _store()
    async with factory() as session, session.begin():
        found = await session.scalar(sa.select(Account).where(Account.email == target))
        if found is None:
            raise HTTPException(404, f"{target} 계정이 없다")
        row = await session.get(MarketGrantRow, (target, group))
        if row is not None:
            before = MarketGrant(group, row.view, row.backtest, row.trade).as_json()
            await session.delete(row)
            _record_permission(
                session, email=target, what="market", before=before, after=None, by=by
            )
            await session.flush()
        invalidate_market_rows(target)
        table = await collections()
        return _account_row(
            found,
            now=datetime.now(UTC),
            hold_after=await hold_after(),
            table=table,
            grants=await _grants_in(session, target),
            market_grants=await _market_grants_in(session, target),
        )


@router.post("/users/{email}/collection")
async def set_collection(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """권한 묶음을 배정한다 — `{collection}` (빈 문자열 = 승인 대기로).

    Args:
        request: 요청. 주는 사람의 권한을 본다.
        email: 대상.
        payload: `{collection: str}`.

    Returns:
        갱신된 행.
    """
    return await _grant_route(
        request, email, collection=str(payload.get("collection", "")), add=(), remove=()
    )


@router.post("/users/{email}/caps")
async def set_caps(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """개별 기능을 더하거나 뺀다 — `{add: [...], remove: [...]}`.

    Args:
        request: 요청.
        email: 대상.
        payload: 더할 것과 뺄 것. 묶음이 주는 기능은 여기서 못 뺀다 — 묶음을 바꾼다.

    Returns:
        갱신된 행.
    """
    return await _grant_route(
        request,
        email,
        collection=None,
        add=_caps_in(payload, "add"),
        remove=_caps_in(payload, "remove"),
    )


@router.post("/users/{email}/role")
async def set_role(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """옛 창구 — 등급 이름을 내장 묶음으로 바꿔 배정한다 (`viewer`·`trader`·`admin`·`pending`).

    Args:
        request: 요청.
        email: 대상.
        payload: `{role}`.

    Returns:
        갱신된 행.

    Raises:
        HTTPException: 400 모르는 등급.
    """
    try:
        wanted = Role(str(payload.get("role", "")))
    except ValueError as exc:
        raise HTTPException(400, f"모르는 등급이다 — {payload.get('role')!r}") from exc
    return await _grant_route(
        request, email, collection=LEGACY_COLLECTION.get(wanted) or "", add=(), remove=()
    )


@router.post("/users/{email}/audit")
async def set_audit(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """감사 기능을 개별로 주거나 거둔다 — `{audit: bool}` (옛 창구 · 이제 `caps` 의 한 경우).

    Args:
        request: 호출자(관리자) 확인용.
        email: 대상 계정.
        payload: `{audit: bool}` — 없으면 준다.

    Returns:
        갱신된 계정 행 (`_grant_route` 가 돌려주는 모양).
    """
    wanted = bool(payload.get("audit", True))
    return await _grant_route(
        request,
        email,
        collection=None,
        add=[Cap.AUDIT] if wanted else [],
        remove=[] if wanted else [Cap.AUDIT],
    )


@router.post("/users/{email}/demo_trade")
async def set_demo_trade(
    request: Request, email: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """데모 거래 기능을 개별로 주거나 거둔다 — `{demo_trade: bool}` (옛 창구).

    Args:
        request: 호출자(관리자) 확인용.
        email: 대상 계정.
        payload: `{demo_trade: bool}` — 없으면 준다.

    Returns:
        갱신된 계정 행 (`_grant_route` 가 돌려주는 모양).
    """
    wanted = bool(payload.get("demo_trade", True))
    return await _grant_route(
        request,
        email,
        collection=None,
        add=[Cap.DEMO_TRADE] if wanted else [],
        remove=[] if wanted else [Cap.DEMO_TRADE],
    )


COLLECTION_NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


@router.get("/roles")
async def list_roles() -> dict[str, Any]:
    """권한 묶음들과 기능 목록 — 관리자 화면의 묶음 카드가 읽는다.

    Returns:
        `{collections: [{name, label, caps, builtin, in_use}], caps: [{key, label, group}]}`.
    """
    invalidate_collections()
    table = await collections()
    factory = _store()
    async with factory() as session:
        counts = {
            str(name): int(count)
            for name, count in await session.execute(
                sa.select(Account.role_collection, sa.func.count()).group_by(
                    Account.role_collection
                )
            )
        }
    return {
        "collections": [
            {
                "name": item.name,
                "label": item.label,
                "caps": sorted(cap.value for cap in item.caps),
                "builtin": item.builtin,
                "in_use": counts.get(item.name, 0),
                "playbook_policy": policy_of(item, None).to_json(),
                "market_policy": market_policy_of(item, None).to_json(),
            }
            for item in sorted(table.values(), key=lambda one: (not one.builtin, one.name))
        ],
        "caps": [
            {"key": cap.value, "label": CAP_LABELS[cap], "group": CAP_GROUPS[cap]} for cap in Cap
        ],
    }


@router.put("/roles/{name}")
async def put_role(
    request: Request, name: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """권한 묶음을 만들거나 고친다 — `{label, caps: [...]}` (슈퍼 관리자).

    Args:
        request: 요청.
        name: 묶음 이름 (영문 소문자·숫자·밑줄 · 2~32자).
        payload: 화면 이름과 기능들.

    Returns:
        저장된 묶음.

    Raises:
        HTTPException: 400 이름·기능 형식 · 400 `super_admin` 에서 묶음 편집 기능을 뺌.

    Note:
        묶음을 고치면 **그 묶음을 가진 모든 계정**에 1분 안에 듣는다. 내장도 안의 기능은 고친다.
    """
    key = name.strip().lower()
    if not COLLECTION_NAME.match(key):
        raise HTTPException(400, "묶음 이름은 영문 소문자로 시작하는 2~32자 (a-z 0-9 _)")
    caps = frozenset(_caps_in(payload, "caps"))
    label = str(payload.get("label", "")).strip()[:40] or key
    if key == "super_admin" and Cap.MANAGE_ROLES not in caps:
        raise HTTPException(400, "슈퍼 관리자 묶음에서 권한 묶음 편집을 뺄 수 없다")
    # ⭐ 매매법 정책 (T230) — 주면 바꾸고, 안 주면 그대로 (없던 묶음은 내장값 또는 기본값).
    policy = (
        PlaybookPolicy.from_json(payload.get("playbook_policy"))
        if "playbook_policy" in payload
        else None
    )
    market_policy = (
        MarketPolicy.from_json(payload.get("market_policy")) if "market_policy" in payload else None
    )
    actor = _actor_of(request)
    by = actor.email if actor else "?"
    factory = _store()
    async with factory() as session, session.begin():
        found = await session.get(RoleCollection, key)
        before_caps = None if found is None else found.caps
        before_policy = None if found is None else found.playbook_policy
        before_market = None if found is None else found.market_policy
        if found is None:
            found = RoleCollection(
                name=key, label=label, caps=dump_caps(caps), builtin=key in BUILTIN_BY_NAME
            )
            session.add(found)
        else:
            found.label = label
            found.caps = dump_caps(caps)
        if policy is not None:
            found.playbook_policy = policy.to_json()
        if market_policy is not None:
            found.market_policy = market_policy.to_json()
        found.updated_by = by
        _record_permission(
            session,
            email=f"@{key}",
            what="role_policy",
            before={
                "caps": before_caps,
                "playbook_policy": before_policy,
                "market_policy": before_market,
            },
            after={
                "caps": dump_caps(caps),
                "playbook_policy": found.playbook_policy,
                "market_policy": found.market_policy,
            },
            by=by,
        )
    invalidate_collections()
    invalidate_playbook_rows()
    invalidate_market_rows()
    _logger.info(
        "role_collection_saved",
        payload={
            "name": key,
            "caps": sorted(cap.value for cap in caps),
            "by": actor.email if actor else "?",
        },
    )
    saved = (await collections()).get(key)
    return {
        "name": key,
        "label": label,
        "caps": sorted(cap.value for cap in caps),
        "playbook_policy": policy_of(saved, None).to_json(),
        "market_policy": market_policy_of(saved, None).to_json(),
    }


@router.delete("/roles/{name}")
async def delete_role(request: Request, name: str) -> dict[str, Any]:
    """권한 묶음을 지운다 — 내장은 못 지우고, 쓰는 계정이 있으면 거절.

    Args:
        request: 요청.
        name: 묶음 이름.

    Returns:
        `{name, deleted: true}`.

    Raises:
        HTTPException: 404 없음 · 400 내장 · 409 사용 중.
    """
    key = name.strip().lower()
    if key in BUILTIN_BY_NAME:
        raise HTTPException(400, "내장 묶음은 지울 수 없다 — 안의 기능만 고친다")
    actor = _actor_of(request)
    factory = _store()
    async with factory() as session, session.begin():
        found = await session.get(RoleCollection, key)
        if found is None:
            raise HTTPException(404, "그 묶음이 없다")
        using = await session.scalar(
            sa.select(sa.func.count()).select_from(Account).where(Account.role_collection == key)
        )
        if (using or 0) > 0:
            raise HTTPException(409, f"{using}명이 이 묶음을 쓴다 — 먼저 다른 묶음으로 옮긴다")
        await session.delete(found)
    invalidate_collections()
    _logger.info(
        "role_collection_deleted", payload={"name": key, "by": actor.email if actor else "?"}
    )
    return {"name": key, "deleted": True}


TRADE_RATE_MAX = 30
"""사람 하나가 1분에 낼 수 있는 **돈이 움직이는 요청** 수 (T39 ⑤ · 2026-09-04).

인증이 뚫려도 피해 폭을 줄인다. 사람이 손으로 누르는 속도는 분당 몇 번이고, 30 은 그 위에
넉넉하다 — 스크립트가 훔친 쿠키로 청산·RUN 생성을 쏟아붓는 것만 막는다. 읽기엔 안 건다.
"""
TRADE_RATE_WINDOW_S = 60.0
_trade_hits: dict[str, deque[float]] = {}
"""이메일 → 최근 거래 요청 시각들 (프로세스 안 · 재시작에 비워진다 — 그래도 된다)."""


def _trade_rate_exceeded(email: str, now: float) -> bool:
    """창 안 요청이 상한을 넘나. 넘으면 이번 요청은 세지 않는다 (막힌 요청이 창을 늘리지 않게)."""
    hits = _trade_hits.setdefault(email, deque())
    while hits and now - hits[0] > TRADE_RATE_WINDOW_S:
        hits.popleft()
    if len(hits) >= TRADE_RATE_MAX:
        return True
    hits.append(now)
    return False


async def _pass(request: Request, call_next: Any, *, need: Need, who: Caller | None) -> Response:
    """권한을 보고 통과시키거나 막는다 — `guard` 가 행위자 컨텍스트 안에서 부른다."""
    # 🔴 임시 보류 (사용자 2026-09-07) — 승인 없이 하루가 지난 대기 계정은 **아무 창구도** 못
    #    두드린다. 공개 경로(`/auth/me`·`/auth/logout`·`/auth/contact`)는 여기까지 안 온다.
    #    등급 문(`allows`)보다 먼저다 — 대기 등급은 읽기가 허용되기 때문이다.
    if who is not None and who.held:
        return JSONResponse({"detail": HOLD_MESSAGE, "held": True}, status_code=403)
    # ⭐ T263 — 개인 토큰은 읽기와 MCP 만. 돈이 움직이는 길은 여전히 구글 로그인 화면이다.
    if who is not None and who.via_token and not token_allowed(request.method, request.url.path):
        return JSONResponse(
            {"detail": "개인 토큰은 읽기와 MCP 만 된다 — 이 일은 화면에서 구글 로그인으로"},
            status_code=403,
        )
    # ⭐ 기능별 권한 (사용자 2026-09-07) — 경로·메서드·서버가 요구하는 기능 하나를 계정이 쥐었나.
    #    `need`(옛 등급 요구)는 공개 경로 판정에만 쓰고, 문은 Cap 으로 연다.
    req = required_cap(request.method, request.url.path, live=on_real_money())
    if req is Access.PUBLIC:
        return cast("Response", await call_next(request))
    if who is None:
        return JSONResponse({"detail": "로그인이 필요하다"}, status_code=401)
    if req is not Access.SIGNED_IN and not who.has(req):
        label = CAP_LABELS[req] if req in CAP_LABELS else str(req)
        return JSONResponse(
            {
                "detail": f"권한이 없다 — 필요한 기능: {label}",
                "role": who.role.value,
                "need": str(req),
            },
            status_code=403,
        )
    del need
    # 🔴 **팔로워는 돈이 움직이는 요청을 받지 않는다** (T212 블루그린 · 2026-09-04).
    #    api 가 둘 뜨는 것은 배포의 설계다. 팔로워(`TradingLeader` 미보유)가 거래 POST 를
    #    받아 자기(빈) 세션으로 RUN 을 시작하면 그것이 곧 이중매매다 — 리더 락이 막으려던
    #    바로 그 사고. 503 + 이유를 준다. nginx 가 backup 슬롯으로 재시도한다.
    #    읽기는 통과한다 — 팔로워의 콘솔은 잠깐 비어 보일 뿐 위험하지 않다.
    if req in TRADE_CAPS and request.method.upper() not in READ_METHODS:
        # T39 ⑤ — 사람당 분당 상한. 여기 오면 `who` 는 있다.
        if _trade_rate_exceeded(who.email, time.monotonic()):
            _logger.warning(
                "trade_rate_limited: %s %s %s", who.email, request.method, request.url.path
            )
            return JSONResponse(
                {"detail": f"거래 요청이 너무 잦다 — 1분에 {TRADE_RATE_MAX}번까지. 잠시 뒤 다시"},
                status_code=429,
                headers={"Retry-After": "10"},
            )
        gate = getattr(request.app.state, "trading_leader", None)
        if gate is not None and not gate.is_leader:
            return JSONResponse(
                {
                    "detail": "이 인스턴스는 거래 리더가 아니다 — 배포 전환 중이다. 잠시 뒤 다시",
                    "leader": False,
                },
                status_code=503,
                headers={"Retry-After": "5"},
            )
    # 🔴 요구 ③ — 거래소로 나가는 경로는 **최근 인증**까지 본다.
    #    (여기 오면 `allows` 를 통과했으므로 `who` 는 반드시 있다 — 로그인 안 한 사람은
    #     `Need.TRADE` 를 절대 통과 못 한다.)
    #
    # 🔴 **읽기에는 안 건다** (2026-08-30). 실계좌에서 잔고 조회가 `Need.TRADE` 로
    #    올라가는데(`MONEY_READ_PREFIXES`), 재인증을 같이 걸면 **콘솔 폴링이 5분마다
    #    401** 이 된다 — 화면이 몇 초에 한 번씩 `/exchange/state` 를 당기므로 사실상
    #    쓸 수 없게 된다.
    #
    #    재인증의 뜻은 *"방금 사람이 거기 있었나"* 이고, 그 질문이 필요한 것은 **돈이
    #    움직일 때**다. 보는 것은 되돌릴 수 없는 일이 아니다 — 등급 문(`allows`)이
    #    이미 열람자를 막았고, 여기서 한 번 더 막으면 맡긴 사람이 화면을 못 본다.
    if (
        req in TRADE_CAPS
        and request.method.upper() not in READ_METHODS
        and request.url.path.startswith(TRADE_PATHS)
        and not who.fresh
    ):
        return JSONResponse(
            {
                "detail": "보안 확인이 필요하다 — 구글 재인증 뒤 다시 시도한다",
                "reauth": "/auth/login?force=1",
            },
            status_code=401,
        )
    return cast("Response", await call_next(request))


def _claims(id_token: str) -> dict[str, Any]:
    """`id_token` 의 가운데 토막을 편다.

    Args:
        id_token: 구글이 준 JWT.

    Returns:
        담긴 값들.

    Note:
        🔴 **서명을 검증하지 않는 근거**: 이 값은 방금 구글에게 직접, TLS 로, 우리
        client secret 을 붙여 물어본 응답이다. 중간에 낄 사람이 없다.

        ⛔ 그 근거는 **이 자리에만** 성립한다 — 브라우저에서 받은 `id_token` 을
        똑같이 다루면 안 된다. 그때는 JWKS 검증이 필요하다.
    """
    import base64
    import json

    parts = id_token.split(".")
    if len(parts) != 3:
        raise HTTPException(400, "구글 응답을 못 읽었다")
    body = parts[1]
    try:
        return dict(json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "구글 응답을 못 읽었다") from exc


# ── 개인 API 토큰 (T263 MCP) ─────────────────────────────────────────────────

TOKEN_PREFIX = "updn_"
"""토큰 값의 머리 — 로그·문서에서 알아보게 하고, Bearer 가 우리 것인지 가른다."""
TOKEN_USED_EVERY_S = 60.0
"""`last_used_at` 갱신 간격 — 도구 호출마다 쓰면 읽기 경로에 쓰기가 붙는다."""
_token_seen: dict[str, float] = {}


def new_token() -> str:
    """새 토큰 값 — 무작위 32바이트(URL-safe). 한 번만 보여 주고 해시만 남긴다.

    Returns:
        `updn_` 머리가 붙은 값.
    """
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """토큰 → SHA-256 16진(64자). 표에는 이것만 둔다.

    Args:
        token: 토큰 값.

    Returns:
        64자 16진 문자열.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_allowed(method: str, path: str) -> bool:
    """개인 토큰으로 되는 요청인가 — 읽기 메서드 또는 MCP 끝점 (순수).

    Args:
        method: HTTP 메서드.
        path: 경로.

    Returns:
        읽기(GET·HEAD·OPTIONS)거나 `/mcp` 면 참. 그 밖의 쓰기는 거짓 — 주문·설정 변경은 화면에서.
    """
    clean = path.rstrip("/") or "/"
    return clean == "/mcp" or clean.startswith("/mcp/") or method.upper() in READ_METHODS


async def token_owner(token: str) -> str | None:
    """토큰 값 → 주인 이메일. 없거나 되돌렸으면 None. 1분에 한 번 `last_used_at` 을 찍는다.

    Args:
        token: `updn_…` 값.

    Returns:
        소문자 이메일 또는 None.
    """
    if _factory is None:
        return None
    digest = hash_token(token)
    factory = _store()
    async with factory() as session:
        row = await session.scalar(
            sa.select(ApiToken).where(ApiToken.token_hash == digest, ApiToken.revoked_at.is_(None))
        )
        if row is None:
            return None
        now = time.monotonic()
        if now - _token_seen.get(digest, 0.0) > TOKEN_USED_EVERY_S:
            _token_seen[digest] = now
            row.last_used_at = datetime.now(UTC)
            await session.commit()
        return row.email


def _token_row(row: ApiToken) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


def _signed_in_or_401(request: Request) -> Caller:
    who = getattr(request.state, "caller", None)
    if not isinstance(who, Caller):
        raise HTTPException(401, "로그인이 필요하다")
    return who


@router.get("/tokens")
async def list_tokens(request: Request) -> dict[str, Any]:
    """내 토큰 목록 — 값은 없다(해시뿐).

    Args:
        request: 요청(로그인한 사람).

    Returns:
        `{"tokens": [{id, name, created_at, last_used_at, revoked_at}]}`.

    Raises:
        HTTPException: 401 로그인 없음 · 503 계정 저장소 없음.
    """
    who = _signed_in_or_401(request)
    factory = _store()
    async with factory() as session:
        rows = (
            await session.scalars(
                sa.select(ApiToken)
                .where(ApiToken.email == who.email)
                .order_by(ApiToken.created_at.desc())
            )
        ).all()
    return {"tokens": [_token_row(r) for r in rows]}


@router.post("/tokens")
async def create_token(request: Request) -> dict[str, Any]:
    """토큰을 만든다 — 값은 이 응답에만 있다.

    Args:
        request: 본문 `{"name": "..."}`. 구글 재인증이 최근(`fresh`)이어야 한다 — 토큰은 열쇠라
            돈이 움직이는 일과 같은 문턱을 둔다.

    Returns:
        `{"id", "name", "token", "created_at"}`.

    Raises:
        HTTPException: 401 로그인/재인증 · 403 게스트·토큰으로 토큰 · 503 저장소 없음.
    """
    who = _signed_in_or_401(request)
    if who.via_token:
        raise HTTPException(403, "토큰으로 토큰을 만들 수 없다 — 화면에서 구글 로그인으로")
    if is_guest_email(who.email):
        raise HTTPException(403, "게스트는 토큰을 만들 수 없다")
    if not who.fresh:
        raise HTTPException(401, "보안 확인이 필요하다 — 구글 재인증 뒤 다시 시도한다")
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    body = cast("dict[str, Any]", payload if isinstance(payload, dict) else {})
    name = str(body.get("name") or "MCP").strip()[:60] or "MCP"
    token = new_token()
    factory = _store()
    async with factory() as session:
        row = ApiToken(email=who.email, name=name, token_hash=hash_token(token))
        session.add(row)
        await session.commit()
        await session.refresh(row)
        made = _token_row(row)
    _logger.info("api_token_created", payload={"email": who.email, "name": name, "id": made["id"]})
    return {**made, "token": token}


@router.delete("/tokens/{token_id}")
async def revoke_token(request: Request, token_id: str) -> dict[str, Any]:
    """토큰을 되돌린다 — 내 것만. 되돌린 토큰은 즉시 안 통한다.

    Args:
        request: 요청(로그인한 사람).
        token_id: 토큰 id.

    Returns:
        `{"revoked": true, "id"}`.

    Raises:
        HTTPException: 401 · 404 내 토큰이 아니거나 없음.
    """
    who = _signed_in_or_401(request)
    try:
        wanted = uuid.UUID(token_id)
    except ValueError as exc:
        raise HTTPException(404, "그런 토큰이 없다") from exc
    factory = _store()
    async with factory() as session:
        row = await session.scalar(
            sa.select(ApiToken).where(ApiToken.id == wanted, ApiToken.email == who.email)
        )
        if row is None:
            raise HTTPException(404, "그런 토큰이 없다")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await session.commit()
            _token_seen.pop(row.token_hash, None)
    _logger.info("api_token_revoked", payload={"email": who.email, "id": token_id})
    return {"revoked": True, "id": token_id}
