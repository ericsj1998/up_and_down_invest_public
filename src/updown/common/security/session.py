"""로그인 세션 쪽지 — **서명해서 쿠키에 담는다** (2026-08-30 배포 준비).

## 왜 라이브러리를 안 쓰나

JWT 라이브러리를 새로 넣지 않는다. 필요한 것은 *"우리가 발급한 것이 맞나"* 하나이고,
그건 표준 라이브러리 `hmac` 한 줄이다. 의존성이 늘면 갱신할 것이 늘고, 인증 의존성의
취약점은 **조용히 치명적**이다.

⚠️ 서명(signed)이지 암호화(encrypted)가 아니다 — 담은 값은 누구나 **읽을 수 있다**.
   그래서 이메일·등급만 담고 **비밀은 담지 않는다.**

## 🔴 재인증 시각을 같이 담는 이유

사용자 확정: *"실제 거래소에 주문이 왔다 갔다 할 때 보안 확인"* = **구글 재인증**.

세션이 살아 있는 것과 *방금 사람이 거기 있었다*는 다른 사실이다. 노트북을 열어 둔 채
자리를 비우면 세션은 멀쩡하다. 그래서 마지막 인증 시각(`auth_at`)을 쪽지에 박고,
위험한 동작은 **그 시각이 최근인지**를 따로 본다.

## 쿠키 규약 (여기서 만들고 `apps/api/auth.py` 가 굽는다)

    httpOnly   JS 가 못 읽는다 — XSS 로 세션이 새는 길을 끊는다
    Secure     HTTPS 로만 — 외부 노출이 목적이므로 필수다
    SameSite   Lax — 남의 사이트에서 온 POST 에 쿠키를 안 싣는다 (CSRF 1차 방어)
"""

from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass
from hashlib import sha256

COOKIE = "updown_session"
"""세션 쿠키 이름."""

MAX_AGE_S = 60 * 60 * 12
"""세션 수명(초) — 12시간. 자고 일어나면 다시 로그인한다."""

FRESH_S = 60 * 60
"""**재인증이 최근인가**의 기준(초) — 1시간 (사용자 확정 2026-09-03: 5분은 너무 짧다).

🔴 이 값이 요구 ③ 의 전부다. 주문·판 생성 같은 위험 동작은 마지막 구글 인증이
이 시간 안이어야 한다. 길면 방어가 없는 것과 같고, 짧으면 사람이 로그인만 하다 만다.
5분으로 시작했다가 보안 리뷰로 게이트 경로가 늘어난 날(수동 매매·세션 삭제까지)
실사용에서 로그인 반복이 과해져 1시간으로 올렸다 — 세션 수명(12시간)의 1/12 이라
"자리 비운 노트북" 위협 모델은 유지된다.
"""


class BadTokenError(ValueError):
    """쪽지가 우리 것이 아니거나 상했다.

    Note:
        ⛔ **이유를 나누지 않는다** (서명 불일치 / 만료 / 깨짐). 공격자에게 어느 쪽이
        틀렸는지 알려 주는 것은 그 자체가 정보다.
    """


@dataclass(frozen=True, slots=True)
class Session:
    """쪽지에 담기는 것 — **비밀 없음**.

    Attributes:
        email: 구글 계정.
        issued_at: 쪽지를 만든 시각 (epoch 초).
        auth_at: **마지막으로 구글 인증을 통과한** 시각 (epoch 초).
    """

    email: str
    issued_at: float
    auth_at: float

    def fresh(self, now: float, within: float = FRESH_S) -> bool:
        """방금 사람이 거기 있었나 (요구 ③).

        Args:
            now: 현재 epoch 초.
            within: 허용 나이(초).

        Returns:
            최근 인증이면 참.

        Note:
            ⚠️ **미래 시각을 참으로 세지 않는다.** 쪽지는 서명돼 있어 조작은 못 하지만,
            서버 시계가 흔들리면(이 프로젝트는 그 사고를 겪었다) 미래 값이 생길 수 있고
            그러면 재인증이 영원히 통과한다.
        """
        age = now - self.auth_at
        return 0 <= age <= within


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(session: Session, secret: str) -> str:
    """쪽지를 만들어 서명한다.

    Args:
        session: 담을 내용.
        secret: 서버 비밀키.

    Returns:
        `본문.서명` 문자열.

    Raises:
        ValueError: 비밀키가 비었을 때. ⛔ 빈 키로 서명하면 **누구나 위조**할 수 있고,
            그 상태가 조용히 도는 것이 최악이다 (절대 규칙 #8).
    """
    if not secret:
        raise ValueError("세션 비밀키가 비어 있다 — 빈 키로 서명하면 누구나 위조한다")
    body = _b64(
        json.dumps(
            {"email": session.email, "iat": session.issued_at, "aat": session.auth_at},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    return f"{body}.{_b64(hmac.new(secret.encode(), body.encode(), sha256).digest())}"


def read(token: str, secret: str, *, now: float, max_age: float = MAX_AGE_S) -> Session:
    """쪽지를 검사하고 편다.

    Args:
        token: 쿠키에서 꺼낸 값.
        secret: 서버 비밀키.
        now: 현재 epoch 초.
        max_age: 세션 수명(초).

    Returns:
        담겨 있던 내용.

    Raises:
        BadTokenError: 우리 것이 아니거나 상했거나 만료됐다.

    Note:
        🔴 **`compare_digest` 로 비교한다.** `==` 는 다른 첫 바이트에서 바로 끝나므로,
        걸린 시간으로 서명을 한 바이트씩 알아낼 수 있다 (타이밍 공격).

        🔴 **서명을 먼저 검사하고 그 다음에 푼다.** 순서를 바꾸면 서명도 안 맞는
        입력으로 JSON 파서를 먼저 때리게 된다.
    """
    if not secret:
        raise BadTokenError("세션 비밀키가 없다")
    body, _, sign = token.partition(".")
    if not body or not sign:
        raise BadTokenError("쪽지 모양이 아니다")
    want = _b64(hmac.new(secret.encode(), body.encode(), sha256).digest())
    if not hmac.compare_digest(sign, want):
        raise BadTokenError("서명이 다르다")
    try:
        found = json.loads(_unb64(body))
        made = Session(
            email=str(found["email"]),
            issued_at=float(found["iat"]),
            auth_at=float(found["aat"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise BadTokenError("쪽지 내용을 못 읽었다") from exc
    if not 0 <= now - made.issued_at <= max_age:
        raise BadTokenError("만료됐다")
    return made
