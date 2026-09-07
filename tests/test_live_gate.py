"""라이브 이중 게이트 (P0-5-4 · spec §12.4 · plan D-12).

두 층을 따로 검증한다:

1. **판정** (`decide_routing`) — 진리표. 순수 함수라 DB·어댑터가 필요 없다
2. **획득** (`OrderGateway.resolve_adapter`) — 실주문은 **여전히 어떤 조건에서도
   반환되지 않는다**. 페이퍼는 T13 에서 열렸다 (2026-08-17)

🔴 **페이퍼가 열려도 실주문은 막혀 있다** — 그것이 두 예외를 나눈 이유이고,
   `test_injecting_paper_does_not_open_live` 가 그 분리를 지킨다.
"""

from typing import cast

import pytest

from updown.common.config import AppEnv
from updown.common.security.live_gate import (
    RoutingTarget,
    UserLiveToggle,
    decide_routing,
)
from updown.execution.gateway import (
    LiveOrderBlockedError,
    OrderGateway,
    OrderGatewayError,
    PaperAdapterUnavailableError,
)
from updown.marketdata.adapter import BrokerAdapter

ON = UserLiveToggle(user_id="u1", live_enabled=True)
OFF = UserLiveToggle(user_id="u1", live_enabled=False)


# ---------------------------------------------------------------------------
# 1. 판정 진리표 (spec §12.4)
# ---------------------------------------------------------------------------

TRUTH_TABLE = [
    (AppEnv.DEV, ON, RoutingTarget.PAPER),
    (AppEnv.DEV, OFF, RoutingTarget.PAPER),
    (AppEnv.PAPER, ON, RoutingTarget.PAPER),
    (AppEnv.PAPER, OFF, RoutingTarget.PAPER),
    (AppEnv.LIVE, OFF, RoutingTarget.PAPER),
    (AppEnv.LIVE, ON, RoutingTarget.LIVE),
]


@pytest.mark.parametrize(("env", "toggle", "expected"), TRUTH_TABLE)
def test_routing_truth_table(env: AppEnv, toggle: UserLiveToggle, expected: RoutingTarget) -> None:
    """`환경 == live` AND `토글 ON` 일 때만 LIVE 다."""
    assert decide_routing(env, toggle) is expected


def test_live_requires_both_conditions() -> None:
    """AND 조건임을 명시적으로 고정한다 — 한쪽만으로는 LIVE 가 되지 않는다."""
    live_cases = [(env, tog) for env, tog, target in TRUTH_TABLE if target is RoutingTarget.LIVE]
    assert live_cases == [(AppEnv.LIVE, ON)], "LIVE 로 가는 조합은 단 하나여야 한다"


# ---------------------------------------------------------------------------
# 2. 획득 — 실주문은 계속 차단 · 페이퍼는 주입하면 열린다 (T13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("env", "toggle", "_expected"), TRUTH_TABLE)
def test_a_bare_gateway_returns_nothing(
    env: AppEnv, toggle: UserLiveToggle, _expected: RoutingTarget
) -> None:
    """아무것도 주입하지 않은 게이트는 진리표 전 케이스에서 예외다.

    🔴 **기본값이 차단이다.** 어댑터를 넘기지 않으면 주문 경로가 없다 — 설정을
    빠뜨렸을 때 조용히 열리는 대신 터진다 (절대 규칙 #8).

    ⚠️ 예전에는 이 테스트가 *"Phase 0~1 에는 어떤 조건에서도 반환하지 않는다"* 였다.
    페이퍼가 열린 뒤로 그 주장은 틀렸고, 지금 지키는 것은 **기본값이 차단**이라는
    성질이다. 예외 종류는 아래 테스트들이 구분한다.
    """
    gateway = OrderGateway(env)
    with pytest.raises(OrderGatewayError):
        gateway.resolve_adapter(toggle)


def test_live_path_raises_live_blocked_not_paper_unavailable() -> None:
    """LIVE 판정은 **실주문 차단** 예외여야 한다.

    두 예외를 나눈 이유가 여기다. P2-1 에서 PaperAdapter 를 붙일 때
    `PaperAdapterUnavailableError` 만 사라지고 이쪽은 남아야 한다 — 하나로 뭉쳤다면
    그때 실주문까지 함께 열릴 수 있다.
    """
    gateway = OrderGateway(AppEnv.LIVE)
    with pytest.raises(LiveOrderBlockedError, match="실주문이 차단"):
        gateway.resolve_adapter(ON)


@pytest.mark.parametrize(
    ("env", "toggle"),
    [(env, tog) for env, tog, target in TRUTH_TABLE if target is RoutingTarget.PAPER],
)
def test_paper_path_raises_when_no_adapter_is_injected(env: AppEnv, toggle: UserLiveToggle) -> None:
    """PAPER 판정인데 어댑터를 안 넘겼으면 **폴백 대상 부재** 예외다.

    ⛔ 게이트가 자격증명을 읽어 어댑터를 스스로 만들지 않는다. 만들게 하면 이 파일이
    키를 아는 파일이 되고, 그 다음에는 라이브 키도 읽을 수 있게 된다.
    """
    gateway = OrderGateway(env)
    with pytest.raises(PaperAdapterUnavailableError, match="주입되지 않았다"):
        gateway.resolve_adapter(toggle)


@pytest.mark.parametrize(
    ("env", "toggle"),
    [(env, tog) for env, tog, target in TRUTH_TABLE if target is RoutingTarget.PAPER],
)
def test_paper_path_returns_the_injected_adapter(env: AppEnv, toggle: UserLiveToggle) -> None:
    """🔴 **페이퍼 경로가 열렸다** (T13 · 2026-08-17).

    넘긴 어댑터가 그대로 나온다. 게이트가 고르거나 바꾸지 않는다 — 고르게 하면 어느
    어댑터가 나올지 호출부가 모르고, 그 모름이 라이브로 새는 경로가 된다.
    """
    marker = object()
    gateway = OrderGateway(env, paper=cast("BrokerAdapter", marker))
    assert gateway.resolve_adapter(toggle) is marker


def test_injecting_paper_does_not_open_live() -> None:
    """🔴 **이 테스트가 이 변경의 요점이다.**

    페이퍼 어댑터를 넘겼어도 LIVE 판정은 **여전히 막혀 있어야** 한다. 두 예외를 나눈
    이유가 이것이고, 하나로 뭉쳤다면 페이퍼를 여는 순간 실주문까지 열렸을 것이다.

    ⛔ 페이퍼 어댑터가 실주문 경로의 폴백으로 쓰이지도 않는다 — 그러면 "라이브인데
    조용히 페이크머니로 거래" 가 되고, 그 성적을 실적으로 적게 된다.
    """
    gateway = OrderGateway(AppEnv.LIVE, paper=cast("BrokerAdapter", object()))
    with pytest.raises(LiveOrderBlockedError, match="실주문이 차단"):
        gateway.resolve_adapter(ON)


def test_gateway_env_is_immutable_after_construction() -> None:
    """게이트의 환경을 런타임에 바꿀 수 없다 — 바꿀 수 있으면 게이트가 아니다."""
    gateway = OrderGateway(AppEnv.DEV)
    assert gateway.env is AppEnv.DEV
    with pytest.raises(AttributeError):
        gateway.env = AppEnv.LIVE  # type: ignore[misc]
