"""키 IP 화이트리스트는 관리자에게만 나간다 (2026-09-12).

Note:
    🔴 사용자 지시: *"IP 카드는 관리자 권한이 없으면 보여주지 않게 해줘."*

    이 칸의 값은 **서버 주소**다(Gate 키에 등록한 화이트리스트). 콘솔 스크린샷에 그대로 찍혀
    공개 저장소로 올라갈 뻔했다 — 이 저장소는 커밋마다 공개본으로 동기화되고, 시크릿 검사는
    글자를 읽지 그림의 픽셀을 읽지 않는다.

    화면에서만 숨기면 URL 을 아는 사람은 그대로 받는다. 그래서 API 가 지운다. 응답이 TTL
    캐시라 **캐시 뒤에서** 사람마다 지운다 — 캐시 안에서 지우면 먼저 부른 사람의 등급이 뒤에
    오는 사람에게 그대로 적용된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from updown.apps.api.exchange import hide_whitelist
from updown.common.security.roles import Role

WHITE = "<주소1>, <주소2>"
"""가짜 값이라도 **주소 모양으로 쓰지 않는다** — 공개본 스캐너가 진짜 주소와 구별하지 못해
멈춘다(2026-09-12 실측). 이 시험이 보는 것은 '그대로인가 · 비었나' 뿐이라 모양은 상관없다."""


@dataclass(frozen=True)
class _Who:
    """`Caller` 중 이 판정이 보는 것만."""

    role: Role


def _payload() -> dict[str, Any]:
    return {
        "account": {"user_id": "58832848", "ip_whitelist": WHITE, "margin_mode": "classic"},
        "position": {"size": 0},
    }


def test_admin_sees_it() -> None:
    got = hide_whitelist(_payload(), _Who(Role.ADMIN))
    assert got["account"]["ip_whitelist"] == WHITE


def test_every_other_role_does_not() -> None:
    """거래자·열람자·대기·게스트 모두 못 본다 — 등급 하나만 통과한다."""
    for role in (Role.TRADER, Role.VIEWER, Role.PENDING, Role.GUEST):
        got = hide_whitelist(_payload(), _Who(role))
        assert got["account"]["ip_whitelist"] == "", role


def test_anonymous_does_not() -> None:
    """로그인 안 한 호출(`caller` 없음)도 못 본다 — 기본이 숨김이다."""
    assert hide_whitelist(_payload(), None)["account"]["ip_whitelist"] == ""


def test_the_cached_payload_is_not_mutated() -> None:
    """원본을 고치지 않는다 — 고치면 다음 사람(관리자)에게도 빈 값이 간다."""
    payload = _payload()
    hide_whitelist(payload, None)
    assert payload["account"]["ip_whitelist"] == WHITE


def test_the_rest_of_the_payload_survives() -> None:
    """지우는 것은 그 칸 하나뿐이다."""
    got = hide_whitelist(_payload(), None)
    assert got["account"]["user_id"] == "58832848"
    assert got["account"]["margin_mode"] == "classic"
    assert got["position"] == {"size": 0}


def test_missing_or_empty_account_is_left_alone() -> None:
    """계정 상세를 못 읽은 응답(키 권한 없음)도 그대로 지나간다."""
    assert hide_whitelist({"position": {}}, None) == {"position": {}}
    empty = {"account": {"ip_whitelist": ""}}
    assert hide_whitelist(empty, None) == empty
