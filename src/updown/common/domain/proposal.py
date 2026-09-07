"""제안·승인 계약 (spec §4.5, §4.6, §6.9, §9).

이 모듈이 원칙 P4(분석 ≠ 결정 ≠ 집행)의 경계선이다:

- `TradeProposal` — 분석이 만든 **제안**. 숫자가 있지만 확정이 아니다.
- `ApprovedOrder` — RiskManager 가 만든 **확정**. 집행은 이 값을 바꿀 권한이 없다.
- `Rejection` — 승인되지 않은 이유. 조용히 버리지 않는다 (spec §7).

손절/익절의 SSoT 는 RiskManager 다 (spec §5.1, 절대 규칙 #4).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Bucket, Instrument, Side
from updown.common.domain.reports import TechnicalReport
from updown.common.domain.setup import EntryLeg, Evidence, TakeProfitStep


class ProposalStatus(StrEnum):
    """제안의 처리 상태 (spec §9 `trade_proposals.status`).

    Note:
        spec 이 값 집합을 명시하지 않아 초안으로 둔다. P0-4 에서 DB 제약을 걸 때
        확정한다.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RejectionReason(StrEnum):
    """승인 거부 사유 (spec §5.4, §4.6, §12.2, §4.4 — **초안**).

    Attributes:
        ACTIVE_EXIT_SIGNAL: 활성 청산 신호 존재 (spec §5.4 #1 청산 > 진입 비대칭 원칙).
        COUNTER_TREND: 상위 TF 추세와 역방향 (spec §5.4 #2 추세 게이트).
        REGIME_BLOCKED: 시장 국면 BEAR 차단 (spec §5.4 #3, §4.15).
        RR_BELOW_MINIMUM: 최소 손익비 미달 (spec §4.6).
        ON_WATCHLIST: 관찰 목록 등재 종목 (spec §4.6 브레이커 ①).
        RULE_DISABLED: 해당 룰이 자동 비활성 상태 (spec §4.6 브레이커 ②).
        DAILY_LOSS_LIMIT_REACHED: 일일 누적 손실 한도 도달 — 킬 스위치 (spec §4.6).
        MAX_POSITIONS_REACHED: 최대 동시 포지션 수 초과 (spec §4.6).
        BELOW_MIN_ORDER_AMOUNT: 최소 주문 금액·수량 단위 미달 (spec §12.2).
        THESIS_INVALIDATED: 펀더멘탈 논지 무효화 (spec §4.4).

    Note:
        `BELOW_MIN_ORDER_AMOUNT` 를 브로커 거부로 알게 되면 늦다. RiskManager 단계에서
        먼저 거른다 (spec §12.2).
    """

    ACTIVE_EXIT_SIGNAL = "active_exit_signal"
    COUNTER_TREND = "counter_trend"
    REGIME_BLOCKED = "regime_blocked"
    RR_BELOW_MINIMUM = "rr_below_minimum"
    ON_WATCHLIST = "on_watchlist"
    RULE_DISABLED = "rule_disabled"
    DAILY_LOSS_LIMIT_REACHED = "daily_loss_limit_reached"
    MAX_POSITIONS_REACHED = "max_positions_reached"
    BELOW_MIN_ORDER_AMOUNT = "below_min_order_amount"
    THESIS_INVALIDATED = "thesis_invalidated"


class RiskPresetName(StrEnum):
    """리스크 프리셋 (spec §4.6).

    Note:
        **프리셋은 파라미터를 스케일할 뿐, 안전장치를 비활성화할 수 없다.**
        스탑 불변 원칙(§6.9)·서킷 브레이커·국면 필터(§4.15)는 모든 프리셋에서 동일하게
        적용된다. 공격성은 리스크 %를 더 쓰는 것이지 안전장치를 푸는 것이 아니다.
    """

    CONSERVATIVE = "conservative"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True, slots=True)
class TrailingConfig:
    """트레일링 스탑 설정 (spec §6.9).

    Attributes:
        atr_multiple: 추세 저점에서 떨어뜨릴 ATR 배수.

    Note:
        단타는 기본 활성, 장투는 비활성이다 — 장투에 걸면 휩쏘로 털린다 (spec §6.9).
        활성 여부는 `RiskPolicy.trailing_enabled`, 수치는 여기에 있다.
    """

    atr_multiple: Decimal


@dataclass(frozen=True, slots=True)
class TimeStopConfig:
    """타임 스탑 — 횡보 탈출 설정 (spec §6.9).

    Attributes:
        reduce_tp_after_days: 손절·익절 모두 미도달로 이 일수를 넘기면 익절 목표를
            축소 TP 로 하향한다 (spec 의 N).
        reduced_tp_pct: 축소 TP. 진입가 대비 비율 (예: 0.03 = +3%).
        exit_after_days: 축소 TP 로도 미도달이면 이 일수 뒤 청산한다 (spec 의 M).

    Note:
        **TP 하향은 허용된다.** 스탑 하향 금지와 무관하며 리스크 축소 방향이기 때문이다
        (spec §6.9). 이 구분을 놓치면 절대 규칙 #3 을 잘못 적용하게 된다.
    """

    reduce_tp_after_days: int
    reduced_tp_pct: Decimal
    exit_after_days: int


@dataclass(frozen=True, slots=True)
class StopPolicy:
    """확정된 스탑 관리 정책 (spec §4.6 `ApprovedOrder.stop_policy`, §6.9).

    Attributes:
        never_lower: 손절선 하향 금지. **항상 True** 이며, 필드로 남기는 것은
            `policy_snapshot` 에 정책 전문이 기록되어야 하기 때문이다 (spec §6.9).
        move_stop_to_breakeven_after_first_tp: 반익반본 — 1차 익절 체결 시 스탑을
            본절로 자동 상향 (spec §6.9, §4.10).
        trailing: 트레일링 설정. 비활성이면 None.
        time_stop: 타임 스탑 설정. 미적용이면 None.

    Note:
        집행은 이 정책을 **읽어서 수행**할 뿐 바꾸지 않는다. 스탑 하향 요청은 API
        레벨에서 거부되고 감사 로그에 남는다 (spec §4.10, 절대 규칙 #3).
    """

    never_lower: bool
    move_stop_to_breakeven_after_first_tp: bool
    trailing: TrailingConfig | None
    time_stop: TimeStopConfig | None


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """사용자 리스크 정책 (spec §4.6, §9 `risk_policies`).

    Attributes:
        user_id: 소유 사용자.
        bucket: 적용 버킷. 버킷별로 다른 값을 갖는다 (spec §9 의 `(user_id, bucket)`).
        preset: 이 정책이 파생된 프리셋.
        risk_pct: 1회 거래 허용 리스크 비율. 0.01 이면 계좌의 1%.
        min_rr: 최소 손익비. 미달 시 거부한다.
        daily_loss_limit_pct: 일일 누적 손실 한도. 도달 시 당일 신규 진입 전면 차단
            (킬 스위치, spec §4.6).
        max_positions: 최대 동시 포지션 수.
        trailing_enabled: 트레일링 스탑 활성 여부 (spec §6.9 — 단타 기본 활성 /
            장투 비활성).

    Note:
        **수치를 코드에 박지 않는다.** spec §4.6 의 프리셋 표(보수 0.5%/RR 2.0/3개/-2%,
        표준 1%/RR 1.5~2.0/5개/-3%, 공격 2~3%/RR 1.5/8개/-6%)는 **설정(YAML/DB)의
        초기값**이며 관리자 설정과 백테스트로 조정된다 (spec §4.6, CLAUDE.md 규약 1).
        프리셋 이름만 열거형으로 고정하고 값은 주입받는다.
    """

    user_id: str
    bucket: Bucket
    preset: RiskPresetName
    risk_pct: Decimal
    min_rr: Decimal
    daily_loss_limit_pct: Decimal
    max_positions: int
    trailing_enabled: bool


@dataclass(frozen=True, slots=True)
class RawReports:
    """제안의 원천 리포트 묶음 (spec §4.5 `raw_reports`).

    Attributes:
        technical: 기술적 분석 리포트들. 멀티 타임프레임이면 TF 당 한 건이다.

    Note:
        `fundamental: FundamentalReport | None` 은 P3-1 에서 추가한다 (plan D-11).
        컨테이너를 지금 두는 이유가 그것이다 — 그때 필드 하나만 늘면 되고
        `TradeProposal` 의 시그니처는 바뀌지 않는다.
    """

    technical: tuple[TechnicalReport, ...]


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """Signal Aggregator 가 만든 매매 제안 (spec §4.5, §9 `trade_proposals`).

    Attributes:
        proposal_id: 제안 id.
        trace_id: 요청 단위 상관관계 ID. 손실 귀속 추적의 시작점이다 (spec §4.14).
        instrument: 대상 종목.
        bucket: 전략 버킷.
        side: 방향 (롱 온리이므로 실질적으로 BUY).
        entry: 진입가 제안.
        stop_loss: 손절 제안.
        take_profit: 익절 제안.
        rr_ratio: 손익비.
        score: 종합 점수. **정해진 가중치 공식의 출력**이며 AI 가 만들지 않는다
            (spec §4.5, 절대 규칙 #2).
        evidence: 근거 목록. 카테고리당 1표로 집계된다 (spec §5.5).
        evidence_summary: 근거의 자연어 요약. **LLM 이 생성해도 되는 유일한 필드**이며
            (spec §5.3), OFF 여도 파이프라인은 완전 동작해야 한다 (원칙 P7).
        raw_reports: 원천 리포트.
        valid_until: 제안 유효기한 (UTC).
        status: 처리 상태.

    Note:
        숫자는 전부 계산식 출력이다. AI 는 가격·수량·타이밍을 만들지 않는다
        (spec §5.3, 절대 규칙 #2).
    """

    proposal_id: str
    trace_id: str
    instrument: Instrument
    bucket: Bucket
    side: Side
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    rr_ratio: Decimal
    score: float
    evidence: tuple[Evidence, ...]
    evidence_summary: str | None
    raw_reports: RawReports
    valid_until: datetime
    status: ProposalStatus


@dataclass(frozen=True, slots=True)
class ApprovedOrder:
    """RiskManager 가 확정한 주문 (spec §4.6 출력, §9 `approved_orders`).

    spec §4.6 이 규정한 8필드 — `proposal_id`, `total_qty`, `entry_plan`, `stop_loss`,
    `tp_ladder`, `stop_policy`, `valid_until`, `policy_snapshot` — 에 §9 가 요구하는
    영속화 필드 4개를 더한 것이다.

    Attributes:
        approved_order_id: 승인 주문 id (spec §9).
        proposal_id: 원천 제안 id.
        user_id: 소유 사용자 (spec §9).
        total_qty: 총 주문 수량. 분할 진입이라도 리스크는 **계획 평단** 기준으로
            산정한 뒤 레그별로 배분한다 (spec §4.6, §6.9).
        entry_plan: 분할 진입 계획.
        stop_loss: **확정** 손절가. 이 값이 SSoT 다 (spec §5.1).
        tp_ladder: 익절 사다리.
        stop_policy: 확정된 스탑 관리 정책.
        valid_until: 유효기한 (UTC). **필수**다 — 만료 시 자동 폐기하고 재분석을
            유도한다 (spec §4.10).
        policy_snapshot: 승인 시점의 `RiskPolicy` 전문. 정책이 나중에 바뀌어도
            "이 주문이 어떤 정책으로 승인됐는지"가 남아야 백테스트 재현과 감사가
            가능하다 (spec §4.6).
        idempotency_root: 레그별 멱등키의 **파생 기준값** (spec v1.7 §9
            `approved_orders.idempotency_root`). 실제 멱등키는 주문 단위이며
            `OrderRequest.idempotency_key = f"{idempotency_root}:{leg_index}"` 로 만든다.
            승인 단위 키 하나로 여러 레그를 내보내면 브로커가 중복으로 거부한다.
        status: 처리 상태.

    Note:
        집행은 이 값을 **바꿀 권한이 없다** (spec §4.10 "가격/수량 판단은 하지 않음").
        조정이 필요하면 RiskManager 를 다시 부르는 것이 유일한 경로다.
    """

    approved_order_id: str
    proposal_id: str
    user_id: str
    total_qty: Decimal
    entry_plan: tuple[EntryLeg, ...]
    stop_loss: Decimal
    tp_ladder: tuple[TakeProfitStep, ...]
    stop_policy: StopPolicy
    valid_until: datetime
    policy_snapshot: RiskPolicy
    idempotency_root: str
    status: ProposalStatus


class RevisionTrigger(StrEnum):
    """손절/익절 재확정을 촉발한 사건 (spec §9 `risk_plan_revisions.trigger`, v1.8).

    Attributes:
        FIRST_TP_FILLED: 1차 익절 체결 — 반익반본으로 스탑을 본절로 상향하고
            2차 익절선을 재확정한다 (spec §6.9, §9.1).
        TRAILING: 추세 저점 갱신에 따른 스탑 상향 (spec §6.9).
        TIME_STOP: 횡보 탈출 — 익절 목표를 축소 TP 로 하향 (spec §6.9).
        GAP_OPEN: 시가 갭으로 "계획된 최대 손실" 전제가 깨진 경우 (spec §7).
        TRANSITION: 유형 B 버킷 전환에 따른 재산정 (spec §4.8).
        MANUAL: 사용자 수동 오버라이드 (spec §4.6 수동 모드).
    """

    FIRST_TP_FILLED = "first_tp_filled"
    TRAILING = "trailing"
    TIME_STOP = "time_stop"
    GAP_OPEN = "gap_open"
    TRANSITION = "transition"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class RiskPlanRevision:
    """승인 후 손절/익절의 **재확정** 기록 (spec §9 `risk_plan_revisions`, §9.1).

    익절 사다리는 미리 걸어두는 주문 목록이 아니라 **계획**이다. 1차 익절이 체결되면
    RiskManager 가 시장 방향성을 다시 보고 스탑을 본절로 올리고 2차 익절선을 재확정한
    뒤, 그 값으로 2차 주문을 낸다 (spec §9.1, §6.9).

    Attributes:
        revision_id: 개정 id.
        approved_order_id: 대상 승인 주문.
        position_id: 대상 포지션. 진입 전 개정이면 None.
        revision_no: 개정 순번. `OrderKind.STOP_LOSS` 주문의 `leg_index` 가 이 값이다.
        trigger: 재확정을 촉발한 사건.
        stop_loss: 재확정된 손절가.
        tp_ladder: 재확정된 **남은** 익절 계획.
        reason: 판단 근거.
        trace_id: 로그 ID 체인 (spec §4.14).
        decided_at: 결정 시각 (UTC).

    Note:
        **이 객체를 만들 수 있는 것은 RiskManager 뿐이다** (spec §5.1, 절대 규칙 #4).
        집행이 값을 바꿔야 할 때 `ApprovedOrder` 를 수정하는 것이 아니라, RiskManager 가
        새 개정을 발행하고 집행은 그것을 읽어 주문을 낸다. `ApprovedOrder` 가 frozen 인
        것과 이 타입이 존재하는 것은 같은 설계의 양면이다.

        **`stop_loss` 는 직전 개정보다 낮을 수 없다** (절대 규칙 #3, spec §6.9).
        개정 이력이 곧 그 검증 근거다 — 현재값 스칼라만 들고 있으면 하향 여부를
        판정할 수 없다. 타임 스탑의 TP 하향은 리스크 축소 방향이라 허용된다.
    """

    revision_id: str
    approved_order_id: str
    position_id: str | None
    revision_no: int
    trigger: RevisionTrigger
    stop_loss: Decimal
    tp_ladder: tuple[TakeProfitStep, ...]
    reason: str
    trace_id: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class Rejection:
    """승인 거부 (spec §4.6 출력).

    Attributes:
        proposal_id: 거부된 제안 id.
        reason_code: 거부 사유 코드.
        detail: 사람이 읽는 설명.
        rejected_at: 거부 시각 (UTC).

    Note:
        거부는 조용히 버리는 것이 아니다. spec §4.20 은 거부 사유를 **부적합 카드**로
        사용자에게 보여주고 관심 등록까지 제공하도록 요구한다.
    """

    proposal_id: str
    reason_code: RejectionReason
    detail: str
    rejected_at: datetime
