"""인증·인가의 **진리표** (사용자 요구 2026-08-30 배포 준비).

> *"외부에서 URL 로 접속할 수 있게 하는 게 목표기 때문에 위험할 수 있잖아."*

배포 전 실측: API 에 인증 코드가 **0** 이었다. `web`(nginx)이 `/api/` 를 그대로
넘기므로 URL 을 아는 사람이 `POST /walkforward/live`(판 생성) ·
`/exchange/close-position`(청산) · `/rebalancer`(펀드 삭제)를 그대로 부를 수 있었다.

## 이 파일이 지키는 것

인증 버그는 **조용하다** — 뚫려도 화면이 멀쩡하다. 그래서 판정을 순수 함수로 떼고
칸마다 하나씩 짚는다 (`live_gate.py` 와 같은 이유).
"""

import time

import pytest

from updown.common.security.roles import (
    ADMIN_PREFIXES,
    MONEY_READ_PREFIXES,
    PUBLIC_PATHS,
    Need,
    Role,
    allows,
    need_for,
)
from updown.common.security.session import (
    COOKIE,
    FRESH_S,
    BadTokenError,
    Session,
    issue,
    read,
)

SECRET = "test-secret-키"


class TestTheDefaultIsDeny:
    """🔴 허용 목록을 두고 나머지를 막는다 — 반대로 하면 **깜빡한 것이 곧 구멍**이다."""

    @pytest.mark.parametrize(
        ("method", "path", "want"),
        [
            ("GET", "/health", Need.PUBLIC),
            ("GET", "/auth/me", Need.PUBLIC),
            ("GET", "/walkforward/sessions", Need.READ),
            ("GET", "/exchange/state", Need.READ),
            # 🔴 새 POST 는 **아무것도 안 해도** 거래 권한이 걸린다
            ("POST", "/walkforward/live", Need.TRADE),
            ("POST", "/exchange/close-position", Need.TRADE),
            ("DELETE", "/rebalancer/abc", Need.TRADE),
            ("PATCH", "/무언가/새로/생긴/경로", Need.TRADE),
            ("POST", "/auth/users/5/approve", Need.ADMIN),
            ("GET", "/auth/users", Need.ADMIN),
        ],
    )
    def test_what_a_request_needs(self, method: str, path: str, want: Need) -> None:
        assert need_for(method, path) is want

    def test_a_brand_new_write_endpoint_is_protected_without_anyone_remembering(self) -> None:
        """이 시험이 이 설계의 전부다 — 목록에 없는 쓰기는 자동으로 막힌다."""
        assert need_for("POST", "/아직/없는/기능") is Need.TRADE
        assert not allows(Role.PENDING, Need.TRADE)
        assert not allows(None, Need.TRADE)

    def test_a_trailing_slash_does_not_open_a_hole(self) -> None:
        """⚠️ `/auth/users/` 가 관리자 검사를 피해 가면 안 된다."""
        assert need_for("GET", "/auth/users/") is Need.ADMIN
        assert need_for("GET", "/health/") is Need.PUBLIC


class TestWhoMayDoWhat:
    @pytest.mark.parametrize(
        ("role", "need", "ok"),
        [
            # 로그인 안 함 — 공개 말고는 아무것도
            (None, Need.PUBLIC, True),
            (None, Need.READ, False),
            (None, Need.TRADE, False),
            (None, Need.ADMIN, False),
            # 승인 대기 — 읽기만 (사용자 확정 2026-08-30)
            (Role.PENDING, Need.READ, True),
            (Role.PENDING, Need.TRADE, False),
            (Role.PENDING, Need.ADMIN, False),
            # 승인됨 — 읽기까지. **주문은 못 한다**
            (Role.VIEWER, Need.READ, True),
            (Role.VIEWER, Need.TRADE, False),
            (Role.VIEWER, Need.ADMIN, False),
            # 거래 허용 — 주문은 되지만 사용자 관리는 못 한다
            (Role.TRADER, Need.READ, True),
            (Role.TRADER, Need.TRADE, True),
            (Role.TRADER, Need.ADMIN, False),
            # 관리자 — 전부
            (Role.ADMIN, Need.READ, True),
            (Role.ADMIN, Need.TRADE, True),
            (Role.ADMIN, Need.ADMIN, True),
        ],
    )
    def test_truth_table(self, role: Role | None, need: Need, ok: bool) -> None:
        assert allows(role, need) is ok

    def test_approval_alone_never_grants_trading(self) -> None:
        """🔴 **승인과 거래 권한은 별개다** (사용자 확정 2026-08-30).

        승인은 목록을 훑으며 여러 건을 빠르게 누르는 동작이다. 그 손놀림으로 실계좌
        주문 권한이 나가면 안 된다 — 맡기려면 `TRADER` 로 **한 번 더** 올려야 한다.
        """
        assert not allows(Role.VIEWER, Need.TRADE)
        assert allows(Role.TRADER, Need.TRADE)

    def test_a_trader_cannot_make_more_traders(self) -> None:
        """⛔ 거래 권한이 **권한을 나눠 주는 권한**으로 번지면 안 된다."""
        assert not allows(Role.TRADER, Need.ADMIN)

    def test_every_role_is_covered(self) -> None:
        """새 등급을 만들고 판정에 안 넣으면 **조용히 관리자 취급**될 수 있다."""
        for role in Role:
            assert allows(role, Need.READ) is True
            assert allows(role, Need.ADMIN) is (role is Role.ADMIN)


class TestThePublicListStaysSmall:
    def test_nothing_that_shows_money_is_public(self) -> None:
        """⛔ 공개 경로의 응답에는 계좌·성적·주문이 한 톨도 없어야 한다."""
        for path in PUBLIC_PATHS:
            assert path.startswith(("/health", "/auth/")), f"{path} 는 공개면 안 된다"

    def test_admin_paths_are_under_auth(self) -> None:
        # 2026-09-04: 관리자 전용 경로가 `/auth/users` 밖에도 생겼다
        # (`/admin/logs` · `/admin/resources`). 요점은 "관리자 접두어가 공개 경로와
        # 겹치지 않는다" 이지 문자열이 /auth/ 로 시작하느냐가 아니다.
        assert all(prefix.startswith(("/auth/", "/admin/")) for prefix in ADMIN_PREFIXES)
        assert not any(
            prefix.startswith(("/health", "/auth/login", "/auth/callback"))
            for prefix in ADMIN_PREFIXES
        )


class TestTheSessionNote:
    def test_it_round_trips(self) -> None:
        now = time.time()
        made = Session(email="a@b.com", issued_at=now, auth_at=now)
        assert read(issue(made, SECRET), SECRET, now=now) == made

    def test_a_forged_note_is_refused(self) -> None:
        """🔴 남의 서명으로는 못 들어온다."""
        now = time.time()
        token = issue(Session("a@b.com", now, now), SECRET)
        with pytest.raises(BadTokenError):
            read(token, "다른-비밀키", now=now)

    def test_a_tampered_body_is_refused(self) -> None:
        """본문만 바꿔 관리자 이메일로 둔갑하는 길을 막는다."""
        now = time.time()
        token = issue(Session("a@b.com", now, now), SECRET)
        body, _, sign = token.partition(".")
        with pytest.raises(BadTokenError):
            read(f"{body}x.{sign}", SECRET, now=now)

    @pytest.mark.parametrize("junk", ["", ".", "abc", "abc.", ".abc", "a.b.c"])
    def test_junk_is_refused_without_crashing(self, junk: str) -> None:
        with pytest.raises(BadTokenError):
            read(junk, SECRET, now=time.time())

    def test_an_expired_note_is_refused(self) -> None:
        now = time.time()
        token = issue(Session("a@b.com", now, now), SECRET)
        with pytest.raises(BadTokenError):
            read(token, SECRET, now=now + 60 * 60 * 13)

    def test_an_empty_secret_never_signs(self) -> None:
        """⛔ 빈 키로 서명하면 **누구나 위조**한다 — 조용히 도는 것이 최악이다."""
        with pytest.raises(ValueError, match="비밀키"):
            issue(Session("a@b.com", 0, 0), "")
        with pytest.raises(BadTokenError):
            read("a.b", "", now=0)


class TestRecentAuthGuardsOrders:
    """요구 ③ — *"실제 거래소에 주문이 왔다 갔다 할 때 보안 확인"* = 구글 재인증."""

    def test_a_fresh_login_passes(self) -> None:
        now = time.time()
        assert Session("a@b.com", now, now).fresh(now)

    def test_an_old_login_does_not(self) -> None:
        """세션이 살아 있는 것과 **방금 사람이 거기 있었다**는 다른 사실이다."""
        now = time.time()
        stale = Session("a@b.com", now - 3600, auth_at=now - FRESH_S - 1)
        assert not stale.fresh(now)
        assert read(issue(stale, SECRET), SECRET, now=now) == stale  # 세션 자체는 유효

    def test_a_future_timestamp_is_not_fresh(self) -> None:
        """⚠️ 서버 시계가 흔들리면(이 프로젝트가 겪었다) 재인증이 영원히 통과한다."""
        now = time.time()
        assert not Session("a@b.com", now, auth_at=now + 600).fresh(now)


class TestItRefusesPlainHttpOutsideDev:
    """🔴 배포해 놓고 HTTPS 를 안 걸면 **세션 쿠키가 평문으로 오간다**.

    그 상태는 화면상 완전히 정상이라 아무도 모른다 — 이 프로젝트가 반복해서 데인
    모양이다. 그래서 로그인이 **안 되는 쪽**을 고른다 (절대 규칙 #8).
    """

    @staticmethod
    def ask(env: str, proto: str) -> bool | str:
        """`(환경, 들어온 프로토콜)` → 참/거짓, 또는 거부 사유."""
        from types import SimpleNamespace

        import updown.common.config as config_mod
        from updown.apps.api.auth import InsecureTransportError, secure_cookies
        from updown.common.config import AppEnv

        fake = SimpleNamespace(
            headers={"x-forwarded-proto": proto} if proto else {},
            url=SimpleNamespace(scheme="http"),
        )
        was = config_mod.load_settings
        config_mod.load_settings = lambda *_a, **_k: SimpleNamespace(app_env=AppEnv(env))  # type: ignore[assignment]
        try:
            return secure_cookies(fake)  # type: ignore[arg-type]
        except InsecureTransportError as exc:
            return str(exc)
        finally:
            config_mod.load_settings = was  # type: ignore[assignment]

    def test_https_is_always_secure(self) -> None:
        assert self.ask("live", "https") is True
        assert self.ask("dev", "https") is True

    def test_dev_may_run_plain(self) -> None:
        """⛔ 무조건 붙이면 로컬 개발에서 쿠키가 안 실려 로그인이 안 된다."""
        assert self.ask("dev", "http") is False
        assert self.ask("dev", "") is False

    @pytest.mark.parametrize("env", ["paper", "live"])
    def test_deployed_plain_http_is_refused(self, env: str) -> None:
        got = self.ask(env, "http")
        assert isinstance(got, str)
        assert "평문 HTTP" in got

    @pytest.mark.parametrize("env", ["paper", "live"])
    def test_a_proxy_that_forgets_the_header_is_caught(self, env: str) -> None:
        """⚠️ 앞단이 `X-Forwarded-Proto` 를 안 넘기는 **오설정**도 여기서 걸린다.

        그때 로그인이 깨지는 것이 조용히 취약한 것보다 낫다.
        """
        assert isinstance(self.ask(env, ""), str)


class TestTheCookieName:
    def test_it_is_stable(self) -> None:
        """이름이 바뀌면 돌던 세션이 전부 로그아웃된다 — 바꿀 때 알고 바꾼다."""
        assert COOKIE == "updown_session"


class TestMoneyReadsOnRealAccounts:
    """실계좌에서는 **읽기조차** 거래 권한을 요구한다.

    사용자 확정 2026-08-30: *"승인 전까지는 read 만 있는데 실거래 콘솔이 보이는 건
    옳지 않다."*

    🔴 **경로가 아니라 환경이 문을 정한다.** `/exchange/*` 와 `/walkforward/live/*` 는
    지금 테스트넷을 가리키지만, 실주문이 열리면(P2-8) **같은 경로가 진짜 돈을 가리킨다** —
    코드는 한 줄도 안 바뀌는데 뜻이 바뀐다. 그래서 문을 미리 걸어 둔다.
    """

    @pytest.mark.parametrize("path", ["/exchange/state", "/walkforward/live/abc", "/rebalancer"])
    def test_testnet_still_lets_viewers_read(self, path: str) -> None:
        """⛔ 지금 조이면 **열람자의 페이퍼 콘솔이 같이 죽는다**.

        열람자에게 페이퍼 콘솔과 리포트를 주기로 한 것이 사용자 결정이고, 그 화면이
        바로 이 경로들을 읽는다. 그래서 테스트넷에서는 아무것도 바뀌지 않아야 한다.
        """
        assert need_for("GET", path, live=False) is Need.READ

    @pytest.mark.parametrize("path", ["/exchange/state", "/walkforward/live/abc", "/rebalancer"])
    def test_live_requires_trade_even_to_look(self, path: str) -> None:
        assert need_for("GET", path, live=True) is Need.TRADE

    @pytest.mark.parametrize("path", ["/exchange/state", "/walkforward/live/abc"])
    def test_viewer_is_shut_out_on_live(self, path: str) -> None:
        """등급까지 이어서 본다 — 판정만 바뀌고 통과 규칙이 안 따라오면 소용없다."""
        need = need_for("GET", path, live=True)
        assert allows(Role.VIEWER, need) is False
        assert allows(Role.PENDING, need) is False
        assert allows(Role.TRADER, need) is True
        assert allows(Role.ADMIN, need) is True

    def test_reports_stay_open_on_live(self) -> None:
        """⚠️ 리포트는 실계좌에서도 열람자가 본다 — 성적은 돈이 아니다."""
        assert need_for("GET", "/report/latest", live=True) is Need.READ

    def test_login_paths_are_never_touched(self) -> None:
        """⛔ 실계좌라고 로그인까지 막으면 아무도 못 들어온다."""
        for path in PUBLIC_PATHS:
            assert need_for("GET", path, live=True) is Need.PUBLIC

    def test_default_is_the_permissive_one(self) -> None:
        """`live` 기본값이 거짓이어야 기존 호출부가 안 바뀐다 — 바뀌면 조용히 막힌다."""
        assert need_for("GET", "/exchange/state") is Need.READ

    def test_admin_paths_still_win(self) -> None:
        """관리자 경로는 실계좌에서도 관리자다 — 거래 권한으로 내려가면 안 된다."""
        assert need_for("GET", "/auth/users", live=True) is Need.ADMIN

    def test_the_guarded_prefixes_are_the_money_ones(self) -> None:
        """목록이 조용히 비면 이 시험 전체가 통과하면서 아무것도 안 지킨다."""
        assert set(MONEY_READ_PREFIXES) == {"/exchange", "/walkforward/live", "/rebalancer"}


class TestGuestIsReadOnlyAndDemoOnly:
    """T221 — 게스트(구글 없이 들어온 익명)는 **읽기만**, 그리고 실계좌 서버는 그 쪽지를 거른다."""

    def test_guest_reads_but_never_trades_or_admins(self) -> None:
        assert allows(Role.GUEST, Need.READ)
        assert not allows(Role.GUEST, Need.TRADE)
        assert not allows(Role.GUEST, Need.ADMIN)

    def test_guest_email_is_recognisable(self) -> None:
        from updown.common.security.roles import GUEST_EMAIL, is_guest_email

        assert is_guest_email(GUEST_EMAIL)
        assert is_guest_email("Anyone@DEMO ")
        assert not is_guest_email("someone@gmail.com")

    def test_guest_entry_is_public_but_carries_no_money(self) -> None:
        # 공개 경로 목록에 있어야 단추 하나로 들어온다. 응답에 계좌 값은 없다(auth.guest 참조).
        assert "/auth/guest" in PUBLIC_PATHS
