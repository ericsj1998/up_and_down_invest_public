"""인증 문이 **실제로 막는가** (2026-08-30 배포 준비).

## 왜 따로 시험하나

`tests/test_auth_rules.py` 는 판정(진리표)을 본다. 그런데 판정이 맞아도 **문에 안
걸려 있으면** 아무것도 안 막힌다 — 그리고 그 상태는 조용하다. 실제로 미들웨어를
붙인 뒤 기존 시험 2,685건이 **전부 그대로 통과**했다. 라우트 함수를 직접 부르기
때문이다. 즉 기존 시험은 이 문을 한 번도 안 지난다.

⇒ 여기서는 **ASGI 스택을 통과시켜** 상태 코드를 본다.

## 그리고 라우트 표를 훑는다

새 엔드포인트를 만들 때 인증을 깜빡하는 것이 가장 흔한 구멍이다. 그래서 앱에 실제로
등록된 경로를 전부 꺼내 **공개 목록에 없으면 인증을 요구하는지** 확인한다.
"""

# starlette 의 `TestClient` 는 httpx 를 타입 없이 감싼다 — 응답 객체가 통째로 Unknown 이라
# 우리 코드와 무관한 잡음이 40건 난다. **이 파일에서만** 그 종류를 끈다.
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
# 라우트 함수는 데코레이터가 앱에 등록한다 — 이름으로 부르지 않는 것이 정상이다.
# pyright: reportUnusedFunction=false
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from updown.apps.api import auth as mod
from updown.common.security.roles import PUBLIC_PATHS, Need, Role, need_for

_UNAUTHORIZED = 401
_FORBIDDEN = 403
_OK = 200


def app_with_guard() -> FastAPI:
    """문만 붙인 최소 앱 — 라우트 몇 개로 등급별 응답을 본다."""
    made = FastAPI()
    made.middleware("http")(mod.guard)

    @made.get("/health")
    async def health() -> dict[str, str]:
        return {"ok": "1"}

    @made.get("/walkforward/sessions")
    async def listing() -> dict[str, str]:
        return {"rows": "0"}

    @made.post("/walkforward/live")
    async def start() -> dict[str, str]:
        return {"started": "1"}

    @made.post("/auth/users/x@y.com/role")
    async def role() -> dict[str, str]:
        return {"ok": "1"}

    @made.get("/admin/logs")
    async def logs_listing() -> dict[str, str]:
        return {"files": "0"}

    @made.post("/무언가/새기능")
    async def brand_new() -> dict[str, str]:
        return {"ok": "1"}

    @made.get("/exchange/state")
    async def venue() -> dict[str, str]:
        return {"balance": "1000"}

    return made


@pytest.fixture
def client() -> TestClient:
    return TestClient(app_with_guard())


def as_role(monkeypatch: pytest.MonkeyPatch, role: Role | None, *, fresh: bool = True) -> None:
    """쿠키 해석을 대신한다 — 여기서 보려는 것은 **문**이지 구글이 아니다."""

    async def fake(_request: Any) -> mod.Caller | None:
        if role is None:
            return None
        return mod.Caller(email="a@b.com", role=role, fresh=fresh)

    monkeypatch.setattr(mod, "caller_of", fake)


class TestItActuallyBlocks:
    def test_health_is_open_to_everyone(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 이게 막히면 컨테이너가 unhealthy 로 죽는다."""
        as_role(monkeypatch, None)
        assert client.get("/health").status_code == _OK

    def test_a_stranger_cannot_read(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_role(monkeypatch, None)
        assert client.get("/walkforward/sessions").status_code == _UNAUTHORIZED

    def test_a_stranger_cannot_start_a_run(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 이것이 이 작업의 이유다 — URL 을 아는 사람이 판을 띄우던 상태."""
        as_role(monkeypatch, None)
        assert client.post("/walkforward/live").status_code == _UNAUTHORIZED

    def test_pending_may_read_but_not_trade(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_role(monkeypatch, Role.PENDING)
        assert client.get("/walkforward/sessions").status_code == _OK
        assert client.post("/walkforward/live").status_code == _FORBIDDEN

    def test_viewer_still_cannot_trade(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 승인만으로는 주문이 안 나간다 (사용자 확정 2026-08-30)."""
        as_role(monkeypatch, Role.VIEWER)
        assert client.get("/walkforward/sessions").status_code == _OK
        assert client.post("/walkforward/live").status_code == _FORBIDDEN

    def test_trader_may_trade_but_not_manage_people(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_role(monkeypatch, Role.TRADER)
        assert client.post("/walkforward/live").status_code == _OK
        assert client.post("/auth/users/x@y.com/role").status_code == _FORBIDDEN

    def test_admin_may_do_everything(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_role(monkeypatch, Role.ADMIN)
        assert client.post("/auth/users/x@y.com/role").status_code == _OK

    def test_a_brand_new_endpoint_is_closed_without_anyone_remembering(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 이 시험이 설계의 전부다 — 아무 조치도 안 한 새 POST 가 막힌다."""
        as_role(monkeypatch, Role.VIEWER)
        assert client.post("/무언가/새기능").status_code == _FORBIDDEN


class TestRecentAuthOnOrders:
    """요구 ③ — 거래소로 주문이 나가는 경로는 **최근 인증**까지 본다."""

    def test_a_stale_session_cannot_send_orders(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_role(monkeypatch, Role.ADMIN, fresh=False)
        got = client.post("/walkforward/live")
        assert got.status_code == _UNAUTHORIZED
        # 화면이 어디로 보낼지 알아야 한다 — 막기만 하면 사람이 갇힌다.
        assert got.json()["reauth"] == "/auth/login?force=1"

    def test_a_fresh_session_passes(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_role(monkeypatch, Role.ADMIN, fresh=True)
        assert client.post("/walkforward/live").status_code == _OK

    def test_reading_never_needs_reauth(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 조회까지 5분마다 재인증을 요구하면 사람이 로그인만 하다 만다."""
        as_role(monkeypatch, Role.ADMIN, fresh=False)
        assert client.get("/walkforward/sessions").status_code == _OK

    def test_non_exchange_writes_do_not_need_reauth(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """사용자 관리는 거래소로 주문이 안 나간다 — 재인증까지 요구하지 않는다."""
        as_role(monkeypatch, Role.ADMIN, fresh=False)
        assert client.post("/auth/users/x@y.com/role").status_code == _OK


class TestTheRealAppHasNoOpenDoors:
    """🔴 앱에 **실제로 등록된** 경로를 훑는다 — 깜빡한 것을 사람이 아니라 시험이 찾는다."""

    def test_every_route_is_covered(self) -> None:
        from updown.apps.api.main import create_app

        # ⚠️ lifespan 을 안 돌리므로 DB 없이 라우트 표만 읽는다.
        made = create_app()
        public = {item.rstrip("/") for item in PUBLIC_PATHS}
        opened: list[str] = []
        for route in made.routes:
            path = str(getattr(route, "path", ""))
            methods: set[str] = {str(m) for m in (getattr(route, "methods", None) or {"GET"})}
            for method in methods:
                if need_for(method, path) is Need.PUBLIC and path.rstrip("/") not in public:
                    opened.append(f"{method} {path}")
        assert not opened, f"인증 없이 열려 있다: {opened}"

    def test_the_guard_is_registered_on_the_real_app(self) -> None:
        """판정이 맞아도 **문에 안 걸려 있으면** 아무것도 안 막힌다."""
        from pathlib import Path

        import updown.apps.api.main as main_mod

        source = Path(main_mod.__file__).read_text(encoding="utf-8")
        assert 'app.middleware("http")(auth_guard)' in source

    def test_the_guard_runs_before_the_handlers(self) -> None:
        """🔴 `trace_id` **뒤에** 등록해야 인증이 바깥에서 먼저 돈다."""
        from pathlib import Path

        import updown.apps.api.main as main_mod

        source = Path(main_mod.__file__).read_text(encoding="utf-8")
        assert source.index("trace_id_middleware)") < source.index("(auth_guard)")


class TestRealMoneyReads:
    """실계좌에서는 **읽기도** 막힌다 — 그리고 그 전환은 환경이 정한다.

    🔴 판정(`need_for(live=True)`)이 맞아도 **미들웨어가 환경을 안 넘기면** 아무것도
    안 막힌다. `test_auth_rules.py` 는 진리표만 보므로 그 배선은 여기서 본다 —
    이 파일이 존재하는 이유 그대로다.
    """

    def _env(self, monkeypatch: pytest.MonkeyPatch, *, live: bool) -> None:
        monkeypatch.setattr(mod, "on_real_money", lambda: live)

    def test_viewer_reads_the_venue_on_testnet(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 여기서 막으면 열람자의 **페이퍼 콘솔이 죽는다** (사용자 확정)."""
        self._env(monkeypatch, live=False)
        as_role(monkeypatch, Role.VIEWER)
        assert client.get("/exchange/state").status_code == _OK

    def test_viewer_is_shut_out_on_real_money(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 사용자 확정 2026-08-30: *"실거래 콘솔이 보이는 건 옳지 않다."*"""
        self._env(monkeypatch, live=True)
        as_role(monkeypatch, Role.VIEWER)
        assert client.get("/exchange/state").status_code == _FORBIDDEN

    def test_pending_is_shut_out_on_real_money(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._env(monkeypatch, live=True)
        as_role(monkeypatch, Role.PENDING)
        assert client.get("/exchange/state").status_code == _FORBIDDEN

    def test_trader_still_reads_on_real_money(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⚠️ 조이는 것이 목적이지 잠그는 것이 목적이 아니다 — 맡긴 사람은 봐야 한다."""
        self._env(monkeypatch, live=True)
        as_role(monkeypatch, Role.TRADER)
        assert client.get("/exchange/state").status_code == _OK

    def test_stale_login_still_reads_on_real_money(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 **재인증은 읽기에 안 건다** — 걸면 콘솔이 5분마다 죽는다.

        `/exchange/` 는 재인증 경로(`TRADE_PATHS`)이고, 실계좌에서는 조회도
        `Need.TRADE` 로 올라간다. 둘을 그대로 곱하면 화면이 몇 초에 한 번씩 당기는
        `/exchange/state` 가 **5분 뒤부터 전부 401** 이 되어 콘솔을 못 쓴다.

        ⚠️ 재인증의 뜻은 *"방금 사람이 거기 있었나"* 이고 그 질문은 **돈이 움직일 때**
        필요하다. 보는 것은 되돌릴 수 없는 일이 아니고, 등급 문이 이미 열람자를 막았다.
        """
        self._env(monkeypatch, live=True)
        as_role(monkeypatch, Role.TRADER, fresh=False)
        assert client.get("/exchange/state").status_code == _OK

    def test_stale_login_still_cannot_order_on_real_money(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 그 완화가 **주문까지 풀면** 안 된다 — 요구 ③ 은 그대로 서 있어야 한다."""
        self._env(monkeypatch, live=True)
        as_role(monkeypatch, Role.TRADER, fresh=False)
        assert client.post("/walkforward/live").status_code == _UNAUTHORIZED

    def test_reports_stay_open_on_real_money(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⚠️ 성적은 돈이 아니다 — 열람자가 실계좌에서도 리포트를 본다."""
        self._env(monkeypatch, live=True)
        as_role(monkeypatch, Role.VIEWER)
        assert client.get("/walkforward/sessions").status_code == _OK


class TestTheEnvironmentSwitch:
    def test_unreadable_settings_lock_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """⛔ 설정을 못 읽으면 **조인다** — 관대하면 오설정이 곧 구멍이다 (규칙 #8)."""
        import updown.common.config as config

        def boom() -> object:
            raise RuntimeError("설정 없음")

        monkeypatch.setattr(config, "load_settings", boom)
        assert mod.on_real_money() is True


class TestSignupNotice:
    """새 가입자 알림 본문 (사용자 2026-09-08 "게스트가 발생하면 이메일")."""

    def test_body_names_who_when_and_the_hold_deadline(self) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)
        body = mod.signup_body(
            "someone@example.com", "홍길동", now=now, hold_after=timedelta(days=30)
        )
        assert "someone@example.com" in body and "홍길동" in body
        assert "2026-09-08T03:00:00+00:00" in body
        assert "2026-10-08T03:00:00+00:00" in body  # 보류 예정 = 가입 + 유예
        assert "유예 30일 0시간" in body

    def test_missing_name_is_spelled_out(self) -> None:
        from datetime import UTC, datetime, timedelta

        body = mod.signup_body(
            "x@example.com",
            "",
            now=datetime(2026, 9, 8, tzinfo=UTC),
            hold_after=timedelta(hours=36),
        )
        assert "(없음)" in body and "유예 1일 12시간" in body
