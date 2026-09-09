"""계정 — 구글로 들어온 사람과 그 등급 (2026-08-30 배포 준비).

사용자 요구: *"외부에서 URL 로 접속할 수 있게 하는 게 목표기 때문에 위험할 수 있잖아."*

## 왜 표가 필요한가

세션 쪽지(`common/security/session.py`)에는 **이메일만** 담는다. 등급을 쪽지에 담으면
관리자가 등급을 내려도 **그 사람의 쪽지가 만료될 때까지 옛 권한이 살아 있다** —
권한 회수가 12시간 뒤에 듣는 셈이다.

⇒ 쪽지는 *"누구인가"* 만 말하고, *"무엇을 할 수 있나"* 는 **매 요청 이 표에서 읽는다.**
  등급을 내리면 다음 요청부터 바로 듣는다.

## ⛔ 비밀번호가 없다

구글이 사람을 확인하고 우리는 그 결과만 받는다. 해시도 재설정 절차도 없다 —
저장하지 않은 것은 샐 수 없다.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import Base, JsonDict, JsonList, enum_column
from updown.common.security.roles import Role


class Account(Base):
    """구글로 로그인한 사람 하나.

    Note:
        🔴 **이메일이 열쇠다** (구글 `sub` 가 아니라). 관리자가 승인할 때 보는 것도,
        설정에 적는 첫 관리자도 이메일이다 — 열쇠를 `sub` 로 두면 사람이 화면에서
        고른 것과 코드가 다루는 것이 달라진다.

        ⚠️ 대신 **소문자로 저장한다.** 구글은 대소문자를 구분하지 않는데 우리가
        구분하면 같은 사람이 두 계정이 되고, 하나만 승인된 상태가 생긴다.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    """구글 계정 — **소문자로 정규화해서** 넣는다."""

    name: Mapped[str] = mapped_column(default="")
    """구글이 준 표시 이름 — 승인 화면에서 사람을 알아보는 데 쓴다."""

    picture: Mapped[str] = mapped_column(default="")
    """프로필 사진 URL (표시용). 없으면 빈 문자열."""

    role: Mapped[Role] = mapped_column(
        enum_column(Role, "role"), default=Role.PENDING, server_default=Role.PENDING.value
    )
    """등급. 🔴 **기본은 승인 대기**다 — 새로 들어온 사람이 아무 권한도 안 갖게."""

    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    approved_by: Mapped[str] = mapped_column(default="")
    """누가 승인했나 (이메일). 🔴 권한을 준 사람을 기록하지 않으면 나중에 못 따진다."""

    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)
    """마지막 로그인. **안 쓰는 계정을 찾는 근거**다 — 권한은 쌓이기만 하면 위험하다."""

    audit: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    """감사 권한 — 백테스트·합성 미래의 **최종 손익·연차별 손익**을 본다 (사용자 2026-09-06).

    등급과 직교한다: 관리자는 늘 참이고, 그 외는 관리자가 따로 준다 (`roles.may_audit`).
    """

    blocked: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    """차단. 🔴 **삭제와 다르다** — 지우면 같은 이메일로 다시 가입해 `PENDING` 이 되고,
    승인 목록에 또 떠서 실수로 승인될 수 있다. 차단은 그 길을 막는다.
    """
    demo_trade: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    """열람자에게 **데모(테스트넷) 주문만** 허용 — 실계좌 서버에서는 효과 없음 (2026-09-07)."""
    hold_released_until: Mapped[datetime | None] = mapped_column(default=None)
    """관리자가 보류를 풀어 준 기한 — 이 시각 전에는 하루가 지나도 보류되지 않는다 (2026-09-07)."""
    note: Mapped[str] = mapped_column(default="", server_default="")
    """관리자 메모 — 누구인지 · 왜 승인/차단했는지. 본인은 못 본다."""
    contacted_at: Mapped[datetime | None] = mapped_column(default=None)
    """마지막 관리자 문의 시각 — 연타 간격(`standing.CONTACT_COOLDOWN`)의 기준."""

    role_collection: Mapped[str] = mapped_column(default="", server_default="")
    """권한 묶음 이름 (`role_collections.name`). 빈 문자열 = 승인 대기 (사용자 2026-09-07).

    ⚠️ FK 를 걸지 않는다 — 묶음을 지우면 계정이 함께 막히는 대신 등급 기본으로 떨어지게
    (`caps.effective_caps`). 지우기 전에 사용 중인지는 창구가 검사한다.
    """
    extra_caps: Mapped[str] = mapped_column(default="", server_default="")
    """묶음 밖에 개별로 더 준 기능 — 쉼표 목록 (`caps.parse_caps`). 옛 플래그 둘의 후임."""


class RoleCollection(Base):
    """권한 묶음 하나 — 관리자가 화면에서 만들고 고친다 (사용자 2026-09-07).

    Note:
        내장 여섯 개(`caps.BUILTIN_COLLECTIONS`)는 `builtin=True` 라 지울 수 없다. 안의 기능은
        고칠 수 있되 `super_admin` 에서 `manage_roles` 를 빼는 것은 창구가 거절한다.
    """

    __tablename__ = "role_collections"

    name: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(default="", server_default="")
    caps: Mapped[str] = mapped_column(default="", server_default="")
    builtin: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )
    updated_by: Mapped[str] = mapped_column(default="", server_default="")
    playbook_policy: Mapped[JsonDict | None] = mapped_column(default=None)
    """매매법 기본 정책 — 칸(view·backtest·trade)마다 `"*"` 또는 id 목록 (T230 · 0115).

    NULL 이면 내장값(`security.playbooks.BUILTIN_POLICIES`).
    """
    market_policy: Mapped[JsonDict | None] = mapped_column(default=None)
    """시장 기본 정책 — 칸(view·backtest·trade)마다 `"*"` 또는 갈래 목록 (T242 · 0119).

    NULL 이면 내장값(`security.markets.BUILTIN_MARKET_POLICIES`).
    """


class PlaybookGrantRow(Base):
    """사람별 매매법 권한 덮어쓰기 — 행이 있으면 그 매매법은 이 행이 정한다 (T230 · 0115).

    Note:
        이력은 여기 없다 — 변경마다 `event_logs` 에 `permission_changed` 가 남는다 (규칙 8-2).
        행을 지우면 묶음 기본값으로 돌아간다.
    """

    __tablename__ = "playbook_grants"

    email: Mapped[str] = mapped_column(primary_key=True)
    playbook_id: Mapped[str] = mapped_column(primary_key=True)
    view: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    backtest: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    trade: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    granted_by: Mapped[str] = mapped_column(default="", server_default="")
    granted_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )
    note: Mapped[str] = mapped_column(default="", server_default="")


class MarketGrantRow(Base):
    """사람별 시장 권한 덮어쓰기 — 행이 있으면 그 갈래는 이 행이 정한다 (T242 · 0119).

    Note:
        갈래는 `coin` · `domestic` · `foreign` 셋. 매매법 덮어쓰기(`PlaybookGrantRow`)와 같은 모양.
    """

    __tablename__ = "market_grants"

    email: Mapped[str] = mapped_column(primary_key=True)
    market_group: Mapped[str] = mapped_column(primary_key=True)
    view: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    backtest: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    trade: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    granted_by: Mapped[str] = mapped_column(default="", server_default="")
    granted_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )
    note: Mapped[str] = mapped_column(default="", server_default="")


class AssistantDraft(Base):
    """온보딩 위저드 초안 — 사람마다 한 행 (T247 · 0121).

    Note:
        답은 JSONB 한 칸(자본 · 납입 · 갈래 · 성향 · 매매법) — 단계가 늘 때마다 마이그레이션하지
        않는다. 동의는 문구 **버전**과 시각으로 남고, 같은 트랜잭션에 `event_logs.consent_given` 이
        들어간다 —
        어떤 문장에 동의했는지가 기록의 뜻이다. 게스트(공유 계정)는 행을 만들지 않는다.
    """

    __tablename__ = "assistant_drafts"

    email: Mapped[str] = mapped_column(primary_key=True)
    step: Mapped[str] = mapped_column(default="consent", server_default="consent")
    answers: Mapped[JsonDict] = mapped_column(default=dict, server_default=sa.text("'{}'::jsonb"))
    consent_version: Mapped[str | None] = mapped_column(default=None)
    consent_at: Mapped[datetime | None] = mapped_column(default=None)
    fund_id: Mapped[str | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class ChatThread(Base):
    """AI 채팅 대화 하나 — 사람마다 여럿 (T248 · 0122).

    Note:
        메시지는 JSONB 목록(역할 · 본문 · 도구 호출 · 근거 · 제안). 도구 호출 자체의 감사 기록은
        `event_logs.ai_chat_turn` 에 따로 남는다(추가만). 게스트(공유 계정)는 행을 만들지 않는다.
    """

    __tablename__ = "chat_threads"

    id: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(default="", server_default="")
    model: Mapped[str] = mapped_column(default="", server_default="")
    messages: Mapped[JsonList] = mapped_column(default=list, server_default=sa.text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class AccountContact(Base):
    """보류·대기 중인 사람이 보낸 관리자 문의 한 건 (사용자 2026-09-07).

    Note:
        메일이 못 가도(SMTP 미설정) 여기에는 남는다 — 관리자 화면이 이 표를 읽는다.
        `handled_at` 이 차면 처리된 것이다. 지우지 않는다 — 누가 언제 문을 두드렸는지는 기록이다.
    """

    __tablename__ = "account_contacts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(index=True)
    message: Mapped[str] = mapped_column(default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    mailed: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    """관리자 메일이 실제로 나갔나 — 안 갔으면 화면에서 그 사실을 본다."""
    handled_at: Mapped[datetime | None] = mapped_column(default=None)
    handled_by: Mapped[str] = mapped_column(default="", server_default="")
    """누가 처리했나 (관리자 이메일)."""
