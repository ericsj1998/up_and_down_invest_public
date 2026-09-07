"""익이 나야 반익이다 (사용자 확정 2026-08-20 · 후보 B).

Note:
    🔴 반익은 **지금 값에 절반을 파는 것**이다. 진입가 근처에서 팔면 그 절반의 실현은
    `0 - 비용` 이라 **확정 손실**이다 — 리스크 관리가 아니라 거래소에 수수료를 내고
    크기를 줄이는 일이다.

    실측 (2026-08-20 밤): 끝난 매매 30건 중 **24건이 진입 직후 신호 반익**이었고
    전부 졌다. 수수료만 -153 이 나갔다.

    ⭐ 그리고 이 규칙이 **본절 문제까지 같이 푼다.** 이익일 때만 반익하면 그 순간 가격이
    진입가보다 유리한 쪽에 있으므로, 본절 손절(= 진입가)이 현재가의 올바른 쪽에 서고
    거래소가 받는다. 어젯밤 `stop_guard_failed` 33회 · `panic_close` 10회 · 무방비
    포지션 3개가 전부 그 반대였다.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from updown.orchestration.walkforward.session import Session


class TestTheGateExists:
    def test_the_half_is_withheld_below_cost(self) -> None:
        """🔴 문턱은 **실측 왕복 비용**이다 — 자의적인 숫자가 아니다."""
        source = inspect.getsource(Session._settle)  # pyright: ignore[reportPrivateUsage]
        assert "move < held.cost_pct" in source

    def test_it_only_gates_the_half_not_the_exit(self) -> None:
        """⛔ **전량 청산은 안 막는다.** 반대 자리가 떴으면 나가는 것이 맞고,
        나가는 것은 리스크를 줄이는 행동이다 (spec §1.2.1).
        """
        source = inspect.getsource(Session._settle)  # pyright: ignore[reportPrivateUsage]
        # `flipped`(전량) 처리가 반익 문턱보다 **위**에 있어야 한다.
        assert source.index("if flipped:") < source.index("move < held.cost_pct")

    def test_the_stop_is_left_alone_when_withheld(self) -> None:
        """🔴 덜지 않을 때 손절을 건드리면 **익도 안 났는데 본절**이 된다.

        고치려는 병이 정확히 그것이다.
        """
        source = inspect.getsource(Session._settle)  # pyright: ignore[reportPrivateUsage]
        cut = source.index("move < held.cost_pct")
        # ⚠️ **막는 가지만** 잘라 본다 — 넉넉히 자르면 그 아래 정상 반익 가지의
        #    `planned_stop=held.entry` 가 딸려 들어와 시험이 헛것을 잡는다.
        branch = source[cut : source.index("self._open = replace(", cut)]
        assert "half_withheld" in branch
        assert "return ()" in branch
        assert "planned_stop" not in branch, "막는 가지에서 손절을 건드린다"

    def test_the_half_still_uses_the_price_we_saw(self) -> None:
        """⚠️ 사고 ③ 을 되돌리지 않는다 — 반익가는 계획가가 아니라 **지금 값**이다."""
        source = inspect.getsource(Session._settle)  # pyright: ignore[reportPrivateUsage]
        assert "half_price=bar.close" in source


class TestItIsCounted:
    def test_the_session_counts_what_it_withheld(self) -> None:
        """🔴 **새 규칙이 값을 만들면 그 값의 분포를 싣는다** (§1-0s 관측 규약).

        이 숫자가 없으면 *"규칙이 안 돌았다"* 와 *"돌았는데 조용하다"* 를 못 가른다.
        """
        import dataclasses

        found = {item.name: item.default for item in dataclasses.fields(Session)}
        assert found["half_withheld"] == 0

    def test_the_screen_can_see_it(self) -> None:
        """⛔ 세션만 세고 화면이 못 보면 없는 것과 같다."""
        from updown.apps.api import walkforward as api

        source = inspect.getsource(api.live_health)
        assert "half_withheld" in source
        # ⚠️ 함께 안 실리던 것들도 같이 낸다 — 셋은 서로 다른 사건이다.
        for name in ("stale_plans", "expired", "guarded"):
            assert name in source, f"{name} 이 화면에 안 간다"


class TestDirectionMath:
    """부호를 손으로 다시 확인한다 — 방향이 틀리면 숏에서 정반대로 돈다."""

    def test_a_long_needs_the_price_above_entry(self) -> None:
        entry, cost = Decimal(100), Decimal("0.00157")
        # 롱: (현재 - 진입)/진입 * (+1)
        assert (Decimal("100.5") - entry) * 1 / entry >= cost
        assert (Decimal("100.05") - entry) * 1 / entry < cost

    def test_a_short_needs_the_price_below_entry(self) -> None:
        entry, cost = Decimal(100), Decimal("0.00157")
        # 숏: (현재 - 진입)/진입 * (-1) — 내려가야 이익이다.
        assert (Decimal("99.5") - entry) * -1 / entry >= cost
        assert (Decimal("99.95") - entry) * -1 / entry < cost

    def test_exactly_at_entry_is_never_enough(self) -> None:
        """⭐ 24건이 여기 있었다 — 진입가 근처에서 덜면 그 절반은 비용만큼 진다."""
        entry, cost = Decimal(100), Decimal("0.00157")
        for sign in (1, -1):
            assert (entry - entry) * sign / entry < cost
