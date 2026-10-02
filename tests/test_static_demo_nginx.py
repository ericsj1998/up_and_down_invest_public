"""정적 데모(포트폴리오 · 2026-10-02) — nginx 가 데모 행선지를 저장본으로 답하는 규칙을 못 박는다.

데모 API 프로세스 없이: GET 은 저장본 · 만들기는 403 · 게스트는 쿠키 · 로그인은 늘 실계좌 API ·
TLS 앞단에서만 켜짐.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = (ROOT / "web/nginx.conf").read_text(encoding="utf-8")


def test_static_only_behind_tls() -> None:
    """로컬 dev(TLS 없음)의 데모는 지금처럼 진짜 데모 API — 운영(Caddy https)에서만 저장본."""
    assert "map $http_x_forwarded_proto $static_demo_on" in CONF
    assert '"updown_api_demo|1" 1;' in CONF


def test_login_and_callback_go_to_live_api() -> None:
    assert '"~^/api/auth/(login|callback)$" 1;' in CONF
    assert '"~^1\\|1\\|" updown_api;' in CONF
    assert "location = /api/auth/callback" in CONF and "$live_mode_cookie" in CONF


def test_snapshot_location_is_internal_and_refuses_writes() -> None:
    block = CONF[CONF.index("location /__demo/ {") :]
    block = block[: block.index("}\n    location @demo_missing")]
    assert "internal;" in block
    assert "return 403" in block and "GET|HEAD" in block
    assert "try_files ${uri}__${args}.json ${uri}.json @demo_missing;" in block


def test_guest_is_a_cookie_and_me_picks_the_snapshot() -> None:
    assert "updown_demo_guest=1" in CONF
    me = CONF[CONF.index("location = /__demo_me") :]
    assert "/__demo/auth/me.json" in me and "/__demo/auth/me_anon.json" in me


def test_security_headers_survive_location_add_header() -> None:
    """location 에 add_header 가 있으면 server 의 것을 안 물려받는다 — 보안 조각을 같이 include."""
    for loc in ("location = /api/auth/guest", "location = /api/auth/logout", "location /__demo/ {"):
        block = CONF[CONF.index(loc) :]
        block = block[: block.index("\n    }\n")]
        assert "updown_security.conf" in block, loc
    docker = (ROOT / "web/Dockerfile").read_text(encoding="utf-8")
    assert "updown_security.conf" in docker and "updown_proxy.conf" in docker


def test_snapshot_guest_cannot_trade_and_has_no_timer() -> None:
    me_path = ROOT / "web/public/__demo/auth/me.json"
    if not me_path.exists():  # 공개 저장소에는 저장본이 없다(비공개 경로)
        return
    me = json.loads(me_path.read_text(encoding="utf-8"))
    assert me["signed_in"] is True and me["guest"] is True
    assert me["may_trade"] is False and "demo_trade" not in me["caps"]
    assert "fresh_until" not in me


def test_snapshot_is_private() -> None:
    split = (ROOT / "scripts/dev/strategy_split.yml").read_text(encoding="utf-8")
    assert "- web/public/__demo/" in split
