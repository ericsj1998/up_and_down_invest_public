"""운영 테이블 — 감사 로그·알림·백테스트 (spec §9, §1.2.1, §4.12, §4.14)."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import Base, JsonDict, enum_column
from updown.common.db.models.enums import LogLevel, NotificationStatus


class AppSetting(Base):
    """앱 전체가 공유하는 설정 하나 (T21 ⑦).

    Note:
        🔴 **판마다 다르면 안 되는 값이 있다.** 재충전 상한이 그렇다 — 금고는 하나인데
        판마다 다른 상한을 쓰면 *"이 금고가 얼마나 탔나"* 를 사람이 못 따라간다.

        🔴 **재시작을 넘어야 한다.** 리스크 한도를 메모리에 두면 리로드 한 번에 사라지고,
        사라진 줄 모른 채 계속 돈다 — 이 프로젝트가 오늘 하루 걷어낸 것이 그 모양이다.

        ⚠️ **값은 문자열이다.** Decimal 도 불리언도 여기서는 글자이고, 뜻은 읽는 쪽이
        정한다. 칸을 타입마다 만들면 설정 하나 늘 때마다 마이그레이션이 필요하다.

        ⛔ 여기에 시크릿을 넣지 않는다 (절대 규칙 #1). 이 표는 화면에서 읽고 쓴다.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class StockPaperAccount(Base):
    """주식 페이퍼 계좌 — 시장 하나에 문서 하나 (T240 · 2026-09-09).

    Note:
        토스에는 테스트넷이 없어 체결·잔고를 우리가 모의한다(`execution/stock_paper.py`).
        토스 실주문이 당분간 범위 밖이라 이 계좌가 곧 주식 운영 계좌다 — 프로세스 메모리나
        파일에 두면 재시작·볼륨 정리에 사라지고, 사라지면 감사가 원장과 안 맞는다며 판을 멈춘다.

        ⚠️ 값은 통째 JSON 이다(현금·포지션·대기 주문·조건부·마감·장부). 열로 펼치면 어댑터의
        모양이 바뀔 때마다 마이그레이션이 필요한데, 이 모양은 실브로커 응답을 흉내내는 것이라
        브로커 쪽 사정으로 바뀐다.
    """

    __tablename__ = "stock_paper_accounts"

    market: Mapped[str] = mapped_column(primary_key=True)
    state: Mapped[JsonDict]
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class EventLog(Base):
    """감사 이벤트 로그 (spec §9 `event_logs`, §4.14) — **append-only**.

    Note:
        **UPDATE/DELETE 권한을 애플리케이션 롤에서 회수한다** (0003 마이그레이션,
        spec §1.2.1·§8). 애플리케이션 코드의 선의(수정 API 를 안 만드는 것)에
        의존하지 않겠다는 결정이다. 시도하면 런타임 권한 오류가 난다
        (절대 규칙 #8-2).

        `trace_id` 인덱스가 손실 귀속 추적의 진입점이다 —
        `trace_id → proposal_id → order_id → position_id` 체인 (spec §4.14).

        **적재 실패가 리스크 감소 행동을 막지 않는다** (spec §1.2.1, 절대 규칙 #8-1).
        손절·청산·스탑 상향·주문 취소는 로그가 실패해도 집행하고 폴백 파일에 남긴다.
        구현은 P0-6.
    """

    __tablename__ = "event_logs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(index=True)
    actor: Mapped[str | None] = mapped_column(index=True, default=None)
    """이 흐름을 **시작한 사람** (구글 이메일) — 2026-08-30 외부 공개 준비.

    🔴 `trace_id` 는 한 흐름을 잇지만 그 흐름을 **누가 시작했는지**는 안 말해 준다.
    사람이 여럿 들어오는 순간 *"이 주문 누가 냈지"* 를 답할 수 있어야 한다.

    ⛔ **NULL 일 수 있다** — 엔진의 스케줄 잡은 사람이 시작한 것이 아니다. 거기에 값을
    채우면 "누가 했는지 아는 척" 이 된다.

    ⚠️ 계정 표로 외래키를 걸지 않는다. 감사 기록은 **그때의 사실**이고, 나중에 계정을
    지운다고 기록에서 사람이 사라지면 안 된다 (규칙 8-2 의 정신).
    """
    module: Mapped[str]
    level: Mapped[LogLevel] = mapped_column(enum_column(LogLevel, "level"))
    event_type: Mapped[str] = mapped_column(index=True)
    payload_json: Mapped[JsonDict]
    ts: Mapped[datetime] = mapped_column(server_default=sa.func.now(), index=True)


class Notification(Base):
    """알림 발송 기록 (spec §9 `notifications`, §4.12).

    Note:
        `FAILED` 행이 재시도 큐의 입력이다. 심각도별 채널 라우팅은 발송 계층의
        책임이며(손절 집행 = 즉시 푸시+메일, 일간 리포트 = 메일만), 여기는
        결과만 남긴다.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    type: Mapped[str]
    channel: Mapped[str]
    payload: Mapped[JsonDict]
    status: Mapped[NotificationStatus] = mapped_column(
        enum_column(NotificationStatus, "status"), default=NotificationStatus.PENDING
    )
    ts: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class BacktestRun(Base):
    """백테스트 실행 기록 (spec §9 `backtest_runs`, §4.11).

    Note:
        `config_json` 에 소스/검증 기간과 파라미터가 통째로 남아야 walk-forward
        결과를 재현할 수 있다 (spec §4.11, §12.9). 재현 불가능한 백테스트는
        전략 채택 근거가 될 수 없다.
    """

    __tablename__ = "backtest_runs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    config_json: Mapped[JsonDict]
    report_json: Mapped[JsonDict]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
