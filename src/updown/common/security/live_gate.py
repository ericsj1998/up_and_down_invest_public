"""라이브 이중 게이트 판정 (spec §12.4, plan D-12).

> 실주문 조건: **`환경 == live` AND `사용자별 live 토글 ON`** — 하나라도 아니면 페이퍼.

판정을 **순수 함수**로 뺀 이유가 둘 있다:

1. 진리표 4케이스를 DB·어댑터 없이 테스트할 수 있다
2. 판정과 획득을 분리하면, Phase 0~1 처럼 "판정은 되지만 획득은 불가능한" 상태를
   정확히 표현할 수 있다 (plan D-12)

이 모듈은 어댑터를 알지 못한다. 알면 `common` 이 `marketdata` 를 의존하게 되어
계층이 뒤집힌다.
"""

from dataclasses import dataclass
from enum import StrEnum

from updown.common.config import AppEnv


class RoutingTarget(StrEnum):
    """주문이 향할 곳 (spec §12.4).

    Attributes:
        PAPER: 가상 체결 (spec §4.19 PaperAdapter).
        LIVE: 실주문.
    """

    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class UserLiveToggle:
    """사용자별 live 토글 (spec §12.4 이중 게이트의 두 번째 조건).

    Attributes:
        user_id: 사용자 식별자.
        live_enabled: 사용자가 실거래를 켰는가.

    Note:
        전체 `User` 모델(§4.1, P3-8)을 기다리지 않고 최소 형태로 둔다. 게이트가
        필요한 것은 "누가"와 "켰는가" 둘뿐이며, 인증 모델이 생긴 뒤에도 이 계약은
        바뀌지 않는다.

        `trial` 역할은 실주문 자체가 금지이므로(spec §8) 이 토글이 ON 이 될 수 없다 —
        그 판정은 권한 계층의 몫이고, 게이트는 최종 확인선이다.
    """

    user_id: str
    live_enabled: bool


def decide_routing(env: AppEnv, toggle: UserLiveToggle) -> RoutingTarget:
    """이중 게이트를 판정한다 (spec §12.4).

    진리표:

    | 환경 | 토글 | 결과 |
    |------|------|------|
    | dev | ON | PAPER |
    | dev | OFF | PAPER |
    | paper | ON | PAPER |
    | paper | OFF | PAPER |
    | live | OFF | PAPER |
    | **live** | **ON** | **LIVE** |

    Args:
        env: 실행 환경.
        toggle: 사용자 live 토글.

    Returns:
        주문이 향할 곳.

    Note:
        **AND 조건이라 실수로 LIVE 가 되기 어렵다.** 둘 중 하나라도 빠지면 페이퍼로
        떨어진다 — 개발 중 실주문 사고를 구조적으로 차단하는 것이 목적이다 (spec §12.4).

        판정이 LIVE 라는 것은 "실주문을 해도 되는 조건"이라는 뜻이며, **실주문이
        가능하다는 뜻이 아니다.** Phase 0~1 에서는 획득 단계에서 막힌다
        (`execution.gateway.OrderGateway`).
    """
    if env is AppEnv.LIVE and toggle.live_enabled:
        return RoutingTarget.LIVE
    return RoutingTarget.PAPER
