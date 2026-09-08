"""로그 적재 실패 시의 집행/보류 판정 (spec §1.2.1, plan D-13) ⭐.

**이 모듈이 D-13 정책의 단일 진실 원천이다.** 순수 함수만 두어 DB·파일 없이 테스트한다.

> **최상위 원칙: 손절 집행은 무슨 일이 있어도 막히지 않는다.**
> 서킷 브레이커(§4.6)·데드맨 스위치(§12.6)와 같은 철학 — 방어 행동은 어떤 부품이
> 고장나도 계속된다.
"""

from enum import StrEnum


class ActionRisk(StrEnum):
    """행동이 계좌 리스크를 어느 방향으로 움직이는가 (spec §1.2.1).

    Attributes:
        RISK_REDUCING: 리스크를 **줄이는** 행동 — 손절, 청산, 스탑 상향, 주문 취소,
            서킷 브레이커 발동. 로그가 실패해도 **무조건 집행**한다.
        RISK_INCREASING: 리스크를 **늘리는** 행동 — 신규 진입, 추가 매수, 레그 체결.
            로그가 실패하면 보류한다. 급하지 않으므로 안전한 쪽을 택한다.
        READ_ONLY: 조회·분석. 상태를 바꾸지 않으므로 보류해도 손실이 없다.

    Note:
        분류 기준은 "급한가"가 아니라 **"미루면 손해가 커지는가"** 다. 손절을 미루면
        손실이 자란다. 신규 진입을 미루면 기회비용뿐이다. 이 비대칭이 정책의 근거다.
    """

    RISK_REDUCING = "risk_reducing"
    RISK_INCREASING = "risk_increasing"
    READ_ONLY = "read_only"


#: 분류를 명시하지 않았을 때의 기본값 (spec §1.2.1).
#:
#: **안전한 쪽으로 떨어진다.** 새 행동을 추가하면서 분류를 잊으면 자동으로 보류되며,
#: 무단 집행되지 않는다. 기본값이 `RISK_REDUCING` 이면 반대로 위험한 행동이 조용히
#: 통과하므로, 이 상수의 방향이 정책 전체의 안전성을 결정한다.
DEFAULT_ACTION_RISK = ActionRisk.RISK_INCREASING


def must_proceed_despite_log_failure(risk: ActionRisk | None) -> bool:
    """로그 적재가 실패했을 때 행동을 계속해야 하는가 (spec §1.2.1).

    Args:
        risk: 행동의 리스크 방향. `None` 이면 `DEFAULT_ACTION_RISK`(보류)로 취급한다.

    Returns:
        True 면 **집행해야 한다** — 로그가 없어도 멈추면 안 되는 행동이다.
        False 면 보류할 수 있다.

    Note:
        반환값이 "집행해도 된다"가 아니라 **"집행해야 한다"** 인 것이 중요하다.
        리스크 감소 행동에서 True 를 무시하고 멈추면 그것이 §1.2.1 위반이다.

        호출부는 이 값을 **반드시 확인해야 한다.** 파이썬이 강제할 수 없는 부분이라
        `AuditLogger.record()` 의 반환 타입(`LogAttempt`)을 통해 눈에 띄게 만든다.
    """
    return (risk or DEFAULT_ACTION_RISK) is ActionRisk.RISK_REDUCING


def requires_fallback_persistence(risk: ActionRisk | None) -> bool:
    """로그 적재 실패 시 폴백 파일에 남겨야 하는가 (spec §1.2.1).

    Args:
        risk: 행동의 리스크 방향.

    Returns:
        폴백 기록이 필요하면 True.

    Note:
        **집행되는 행동은 반드시 폴백에 남는다.** 집행했는데 기록이 어디에도 없으면
        감사 추적에 구멍이 생기고, 그것은 손실 귀속(§4.14)을 불가능하게 만든다.

        보류된 행동은 일어나지 않았으므로 폴백이 필요 없다 — 다만 **로그 적재 실패
        자체는** 분류와 무관하게 알림 대상이다 (§4.12, §7 조용한 실패 금지).
    """
    return must_proceed_despite_log_failure(risk)
