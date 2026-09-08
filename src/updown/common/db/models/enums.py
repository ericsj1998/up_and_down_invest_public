"""§9 컬럼이 값 집합을 규정하지만 도메인 타입에는 아직 대응이 없는 열거형.

도메인에 이미 있는 것(`Bucket`, `Side`, `OrderKind`, `ProposalStatus` …)은 여기 두지
않고 `common.domain` 것을 그대로 쓴다. 같은 개념을 두 번 정의하면 언젠가 갈라진다.

여기 있는 것들은 소비자가 Phase 2~3 에 있어 `docs/platform/interfaces_v1.md` §3 기준으로
도메인 타입 고정을 미룬 것들이다 — 그래도 **DB 제약은 지금 걸어야** 잘못된 값이
쌓이지 않는다.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """사용자 역할 (spec §4.1, §9 `users.role`).

    Note:
        `TRIAL` 은 PaperAdapter 전용이다 — 실주문은 `MEMBER` + 본인 키 등록자만
        가능하다 (spec §8).
    """

    ADMIN = "admin"
    MEMBER = "member"
    TRIAL = "trial"


class AnalysisReportType(StrEnum):
    """분석 리포트 종류 (spec §9 `analysis_reports.type`)."""

    TECH = "tech"
    FUND = "fund"


class QualityIssueType(StrEnum):
    """캔들 무결성 위반 종류 (spec §12.1, §7 / plan D-9).

    Attributes:
        OHLC_VIOLATION: `high < max(open, close)` 등 논리 위반. 임계값 없는 오류.
        MISSING_BARS: 결측 구간. **candles 에 행이 없어** 구간 테이블이 필요한
            결정적 이유다 (plan D-14).
        PRICE_SPIKE: 가격 스파이크. 초기 임계값 ±30%, 느슨하게 시작해 조정한다.
        VOLUME_SPIKE: 거래량 스파이크. 초기 임계값 최근 20봉 평균 대비 x50.
        NON_POSITIVE_PRICE: 가격 0 이하. 임계값 없는 오류.
        NEGATIVE_VOLUME: 거래량 음수. 임계값 없는 오류.
    """

    OHLC_VIOLATION = "ohlc_violation"
    MISSING_BARS = "missing_bars"
    PRICE_SPIKE = "price_spike"
    VOLUME_SPIKE = "volume_spike"
    NON_POSITIVE_PRICE = "non_positive_price"
    NEGATIVE_VOLUME = "negative_volume"


class QualityIssueStatus(StrEnum):
    """무결성 위반 처리 상태 (spec §9 `candle_quality_issues.status`).

    Note:
        `OPEN` 구간은 분석 차단 대상이다 (spec §7). 다만 **Phase 0 에서는 기록만
        하고 차단하지 않는다** — 임계값이 느슨해 오탐이 섞일 수 있고, 처음부터
        차단하면 오탐이 백필을 막는다 (plan D-9).
    """

    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class PositionStatus(StrEnum):
    """포지션 상태 (spec §9 `positions.status`).

    Note:
        `TRANSITIONED` 는 유형 B 조건 트리거 전환에만 쓴다. 손절 트리거 전환(유형 A)은
        금지이며 그 경로는 `CLOSED` 후 **별개 트레이드로 재진입**한다 (spec §4.8).
    """

    OPEN = "open"
    CLOSED = "closed"
    TRANSITIONED = "transitioned"


class AllocationReason(StrEnum):
    """비중 원장 기입 사유 (spec §9 `allocation_ledger.reason`).

    Note:
        `DEPOSIT`/`WITHDRAW` 기록이 시간가중수익률(TWR) 계산의 재료다 — 입출금
        구간을 나누지 않으면 입금 시 성과가 부풀어 보인다 (spec §4.18).
    """

    TRADE = "trade"
    TRANSITION = "transition"
    REBALANCE = "rebalance"
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"


class LogLevel(StrEnum):
    """이벤트 로그 레벨 (spec §9 `event_logs.level`)."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationStatus(StrEnum):
    """알림 발송 상태 (spec §9 `notifications.status`, §4.12).

    Note:
        `FAILED` 는 재시도 큐의 입력이다 (spec §4.12).
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
