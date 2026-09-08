"""셋업 계약 — `TradeSetup` 과 그 구성 요소 (spec §4.3 `setups[]`, §4.3.1).

`TechnicalReport`(§4.3)가 `TradeSetup` 을 담고 `TradeProposal`(§4.5)이 `raw_reports` 로
`TechnicalReport` 를 담으므로, 셋 다 한 모듈에 두면 import 가 순환한다.
셋업 계약을 최하단으로 분리해 `setup → reports → proposal` 단방향을 만든다.

**"숫자 없는 매수 추천 금지"** (spec §4.3.1) — `TradeSetup` 은 분할 진입 계획·손절·익절
사다리·스탑 정책 힌트까지 갖춘 **완결된 계획 템플릿**이어야 한다.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.evidence import Evidence
from updown.common.domain.instrument import Timeframe


class EntryTrigger(StrEnum):
    """진입 트리거 방식 (spec §4.3 `entry_trigger`).

    Attributes:
        TOUCH: 가격 터치 즉시 진입.
        CLOSE_CONFIRM: 봉마감 확인 후 진입.
        RETEST_CONFIRM: 돌파를 **지킨** 뒤 리테스트 확인 시 진입 — 추세 지속 베팅
            (spec §4.3.2).
        RECLAIM_CONFIRM: 돌파에 **실패**해 레벨 안쪽으로 복귀 봉마감할 때 진입 — 반전
            베팅 (spec §4.3.2, §6.5 채널 상단 복귀).

    Note:
        집행 방식이 갈리는 지점이다 (spec §4.10). TOUCH 는 적응형 폴링의 1~3초 구간을
        요구하고, CLOSE_CONFIRM 은 봉마감 주기로 충분하다 (spec §4.2).

        `RETEST_CONFIRM` 은 spec **v1.9** §4.3.2 가 신설한 **공통** 진입 트리거다 —
        컵앤핸들 넥라인(§6.7)·IFVG S/R Flip(§6.6)이 같은 판정을 쓴다.
        판정 로직은 `structures/` 공용 모듈이 소유하고 셋업은 기준 레벨만 제공한다.

        `RECLAIM_CONFIRM` 은 spec **v2.4** 가 신설했다. 돌파 이후 시장이 하는 일은 둘 중
        하나이고 **둘 다 유효한 표준 셋업**이며, 갈리는 기준은 **종가가 레벨 위냐
        아래냐 — 부등호 하나**다. 판정은 같은 모듈이 모드만 바꿔 수행한다.

        ⚠️ 같은 "안쪽 복귀" 사건이라도 **포지션을 보유 중이면 청산 신호**이며 그것은
        `decision` 의 소관이다 (spec §6.5, 절대 규칙 #4). 여기 열거형은 **진입** 트리거만
        기술한다.
    """

    TOUCH = "TOUCH"
    CLOSE_CONFIRM = "CLOSE_CONFIRM"
    RETEST_CONFIRM = "RETEST_CONFIRM"
    RECLAIM_CONFIRM = "RECLAIM_CONFIRM"


class TpFollowUpAction(StrEnum):
    """익절 체결 후 자동 수행할 조치 (spec §4.3 `tp_ladder[].then`).

    Attributes:
        MOVE_STOP_TO_BREAKEVEN: 스탑을 본절(평단)로 상향 — **반익반본** (spec §6.9).
            1차 익절 체결 시 이후 최악의 경우가 본전이 된다.
    """

    MOVE_STOP_TO_BREAKEVEN = "MOVE_STOP_TO_BREAKEVEN"


@dataclass(frozen=True, slots=True)
class EntryLeg:
    """분할 진입 계획의 한 레그 (spec §4.3 `entry_plan[]`, §6.9 3분할 진입).

    Attributes:
        price: 이 레그의 진입가.
        ratio: 총 수량 대비 비율. 3분할이면 상단 0.25 / 중단 0.25 / 하단 0.50.

    Note:
        `ratio` 가 `float` 이 아니라 `Decimal` 인 이유는 이 값이 **수량으로 환산**되기
        때문이다. 리스크·손절 계산은 개별 레그가 아니라 항상 **계획 평단**
        (`TradeSetup.avg_entry`) 기준이다 (spec §4.6, §6.9).
    """

    price: Decimal
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class TakeProfitStep:
    """익절 사다리의 한 단계 (spec §4.3 `tp_ladder[]`).

    Attributes:
        price: 익절가.
        ratio: 이 단계에서 청산할 비율.
        then: 체결 후 자동 수행할 조치. 없으면 None.
    """

    price: Decimal
    ratio: Decimal
    then: TpFollowUpAction | None


@dataclass(frozen=True, slots=True)
class StopPolicyHint:
    """분석이 제시하는 스탑 정책 **의견** (spec §4.3 `stop_policy_hint`).

    Attributes:
        never_lower: 스탑 불변 원칙 적용 여부. 실질적으로 항상 True 이며 (spec §6.9),
            필드로 남기는 것은 셋업 JSON 이 자기 완결적이기 위해서다.
        trailing: 트레일링 스탑을 권하는가. 의견 없음이면 None.

    Note:
        **파라미터가 아니라 의견이다.** 트레일링 배수 같은 수치는 RiskManager 소유이며
        (spec §6.9), 확정값은 `ApprovedOrder.stop_policy` 에 담긴다. 분석은 제안만 한다
        (절대 규칙 #4).
    """

    never_lower: bool
    trailing: bool | None


@dataclass(frozen=True, slots=True)
class StopCandidate:
    """손절 **후보** 하나 — 분석이 싣는 **사실**이다 (spec §6.1, P1-7).

    Attributes:
        price: 이 후보의 손절가.
        timeframe: 이 후보를 만든 구조물의 시간축. **축 ②(발견 봉 vs 상위 봉) 판정의
            키**다 (`Phase01` §P1-8-0b).
        source: 출처 설명. 예: `패턴 폐기점(감쌈 양봉 아래꼬리)`, `1h 스윙 저점`.

    Note:
        **후보는 제안이 아니라 사실이다.** "이 구조물이 여기 있다"는 관측이며, 그중
        무엇을 쓸지 고르는 것은 RiskManager 다 (§5.1, 절대 규칙 #4). 분석이 후보를
        여럿 싣는 것은 **표현력**이 늘어난 것이고 권한이 늘어난 것이 아니다.

        `source` 가 열거형이 아닌 이유는 `setup_type` 과 같다 — 셋업은 플러그인이고
        새 출처가 생길 때마다 공용 타입을 고쳐야 하면 "탐지 파일 1개 + 레지스트리
        1줄"(§4.3.1)이 깨진다.

        ⚠️ **ATR 기반 손절은 여기 들어오지 않는다.** 그것은 구조 관측이 아니라
        RiskManager 자신의 정책값이며(§6.1 "손절폭 산정의 표준"), decision 계층이
        후보 목록과 **병행해 더 보수적인 쪽을 고른다**.
    """

    price: Decimal
    timeframe: Timeframe
    source: str


@dataclass(frozen=True, slots=True)
class TradeSetup:
    """탐지된 매매 셋업 — 완결된 계획 템플릿 (spec §4.3 `setups[]`).

    Attributes:
        setup_type: 셋업 종류. 예: `ORDER_BLOCK`, `FVG`, `CUP_HANDLE`, `TRENDLINE_RETEST`.
        rule_version: 룰 버전 `rule_id@version`. 예: `order_block@1.2`.
            백테스트 성과 귀속 단위다 (spec §4.3.1, §4.11).
        entry_trigger: 진입 트리거 방식.
        entry_plan: 분할 진입 계획.
        avg_entry: **계획 평단** — 리스크·수량 계산의 기준 (spec §4.6, §6.9).
        stop_loss: 손절 **1순위 제안**. 확정은 RiskManager 다 (spec §5.1, 절대 규칙 #4).
        stop_candidates: 손절 후보 전체 — 시간축 라벨이 붙은 **관측 사실**이다.
            `stop_loss` 는 **반드시 이 중 하나**여야 한다 (`__post_init__` 이 강제).
        tp_ladder: 익절 사다리.
        stop_policy_hint: 스탑 정책 의견.
        rr_ratio: 손익비 제안값.
        confidence: 신뢰도 0.0~1.0. 초기엔 룰 가중치 합, 이후 백테스트 통계로 보정
            (spec §4.3 비고).
        evidence: 근거 목록.

    Note:
        `setup_type` 이 열거형이 아니라 `str` 인 것은 의도다. 셋업은 플러그인이고
        "탐지 파일 1개 + 레지스트리 1줄"로 추가되어야 하는데 (spec §4.3.1), 열거형이면
        플러그인을 추가할 때마다 공용 타입을 고쳐야 한다. 값의 권위는 레지스트리에 있다.
        (구조물의 `StructureType` 이 열거형인 것은 spec §9 가 DB 컬럼 값을 못박기 때문이다.)
    """

    setup_type: str
    rule_version: str
    entry_trigger: EntryTrigger
    entry_plan: tuple[EntryLeg, ...]
    avg_entry: Decimal
    stop_loss: Decimal
    tp_ladder: tuple[TakeProfitStep, ...]
    stop_policy_hint: StopPolicyHint
    rr_ratio: Decimal
    confidence: float
    evidence: tuple[Evidence, ...]
    stop_candidates: tuple[StopCandidate, ...] = ()
    size_mult: Decimal = Decimal(1)
    """탐지기가 낸 **크기 승수** — 세션이 노출에 곱한다 (T81 · 기본 1 = 무변화).

    변동성 타게팅처럼 *"이 자리는 평소보다 작게/크게"* 를 **분석이** 판단해야
    하는 경우가 있다. 수량 자체는 여전히 decision 이 정하고(절대 규칙 #4)
    여기 값은 그 계산에 들어가는 **입력**이다.

    ⚠️ 손절·익절과 달리 이 값은 **위험을 키울 수도** 있다. 그래서 탐지기 쪽에
    클램프를 두고, 여기서는 0 이하를 금지한다 (`__post_init__`).
    """
    hold_level: Decimal | None = None
    """**뚫린 레벨** — 돌파 셋업만 채운다 (T44).

    롱이면 뚫린 저항의 윗변(= 새 지지), 숏이면 뚫린 지지의 아랫변(= 새 저항). 청산이
    캔들 색 대신 이 값을 볼 수 있게 셋업이 들고 간다. 비어 있으면 그 셋업은 레벨 보유
    청산의 대상이 아니다 — 박스권 왕복이 그렇다.
    """
    entry_lifetime_bars: int | None = None
    """걸어 둔 진입 다리가 **몇 봉까지 살아 있나** (T43 리테스트 대기).

    🔴 세션의 기본 수명은 *"계획이 살아 있는 동안"* 인데, 돌파는 한 봉짜리 사건이라
    다음 봉에 계획이 사라지면 지정가가 바로 거둬진다 — 리테스트를 기다릴 수 없다.
    돌파 셋업이 이 값을 주면 그 봉 수만큼은 계획이 없어도 표를 둔다. 비어 있으면 지금과
    같다 (동결 버전 무변화).
    """

    def __post_init__(self) -> None:
        """`stop_loss` 가 후보 중 하나임을 강제한다.

        Raises:
            ValueError: 후보를 실었는데 `stop_loss` 가 그중에 없는 경우.

        Note:
            두 필드가 어긋나면 **어느 쪽이 진짜인지 알 수 없다.** 불변식으로 묶어 두면
            RiskManager 가 후보 목록만 보고 판단해도 되고, 축 ②(발견 봉 vs 상위 봉)
            측정에서 "1순위가 어느 시간축이었나"를 되짚을 수 있다.

            빈 후보를 허용하는 것은 **하위호환**을 위해서다 — 계약 테스트가 부분집합
            검사라(P0-3) 필드 추가는 기존 코드를 깨지 않아야 한다. 다만 후보가 비면
            축 ② 측정이 불가능하므로, 활성 탐지기는 반드시 채운다.
        """
        if self.size_mult <= 0:
            raise ValueError(
                f"size_mult 는 양수여야 한다 (받은 값: {self.size_mult}) - "
                "0 이하는 수량을 지우거나 방향을 뒤집는다"
            )
        if not self.stop_candidates:
            return
        prices = {candidate.price for candidate in self.stop_candidates}
        if self.stop_loss not in prices:
            raise ValueError(
                f"{self.rule_version}: stop_loss {self.stop_loss} 가 후보에 없다 "
                f"(후보: {sorted(prices)}) — 두 필드가 어긋나면 어느 쪽이 진짜인지 "
                f"알 수 없다"
            )
