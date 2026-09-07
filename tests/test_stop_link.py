"""손절이 나면 **어느 매매였는지** 화면이 알아야 한다 (사용자 신고 2026-08-20).

Note:
    사용자 신고 — 같은 손절인데 두 화면이 다른 말을 했다:

    ```
    RUN 상세   81201e · 손절 · 숏 · -24.51% · 166계약
    콘솔       RUN 미상 · 배율 — · 수익률 —
               "원장에서 이 주문의 계획을 못 찾았다"
    ```

    🔴 **조건부가 발동하면 Gate 가 `ao-{id}` 라는 이름으로 주문을 만든다.** 그 이름에는
    우리 매매 id 가 없다 — 이름으로는 어느 방법으로도 못 잇는다.

    ⭐ 이을 열쇠는 **조건부 주문 id** 뿐이고, 그것은 우리가 손절을 걸 때 거래소가
    돌려준 값이다. `stops_for` 가 이미 주고 있었는데 **버리고 있었다** (실측:
    `wf_orders` 의 손절 행에서 `exchange_order_id` 가 비어 있었다 — 익절 행에는 있었다).

    ⛔ **시각·종목으로 추정하지 않는다.** 3분 차이로 남의 손익이 붙은 적이 있다.
"""

from __future__ import annotations

import inspect
import pathlib

from updown.orchestration.walkforward.live_runner import LiveRunner


class TestTheStopIdIsKept:
    """① 거래소가 준 id 를 버리지 않는다."""

    @staticmethod
    def _arm() -> str:
        return inspect.getsource(LiveRunner._arm_stop)  # pyright: ignore[reportPrivateUsage]

    def test_arm_stop_captures_it(self) -> None:
        assert "self._stop_id = made or self._stop_id" in self._arm()

    def test_a_none_does_not_erase_it(self) -> None:
        """⚠️ 이미 맞게 걸려 있으면 None 이 온다 — 그때 덮으면 열쇠를 잃는다."""
        assert "or self._stop_id" in self._arm()

    def test_the_retry_keeps_it_too(self) -> None:
        """⭐ 1틱 띄워 다시 걸면 **id 가 바뀐다** — 새 값을 잡아야 한다."""
        assert "again_id or self._stop_id" in self._arm()

    def test_it_is_written_to_the_ledger(self) -> None:
        """🔴 메모리에만 두면 재시작에 사라지고, 그 뒤의 손절은 영영 못 잇는다."""
        # ⚠️ 본체는 `_guard_stop_once` 다 (2026-08-31 · 겹침 방어 분리).
        source = inspect.getsource(
            LiveRunner._guard_stop_once  # pyright: ignore[reportPrivateUsage]
        )
        cut = source.index('status="placed"')
        assert "order_id=self._stop_id" in source[cut : cut + 200]


class TestTheConsoleResolvesIt:
    """② `ao-{id}` 를 매매로 되돌린다."""

    @staticmethod
    def _side() -> str:
        from updown.apps.api import exchange

        return inspect.getsource(exchange._ledger_side)  # pyright: ignore[reportPrivateUsage]

    def test_the_store_looks_up_by_order_id(self) -> None:
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.stop_owners)
        assert "WalkforwardOrder.exchange_order_id.in_(wanted)" in source

    def test_the_console_strips_the_prefix(self) -> None:
        """⭐ `ao-` 세 글자를 떼면 그것이 조건부 주문 id 다."""
        source = self._side()
        assert "stop_owners" in source
        assert "text[3:]" in source

    def test_it_also_keys_the_plan_by_the_order_name(self) -> None:
        """⚠️ 화면은 `ao-` 에서 매매 id 를 못 뽑는다 — 서버가 그 이름으로도 걸어 준다."""
        assert "plans[text] = found" in self._side()

    def test_the_screen_asks_by_that_name(self) -> None:
        """🔴 두 규칙이 갈리면 상세가 안 붙고, 그 실패는 조용하다."""
        source = pathlib.Path("web/src/ui.tsx").read_text(encoding="utf-8")
        cut = source.index("export function tradeOf")
        assert 'startsWith("ao-")) return text' in source[cut : cut + 600]


def test_it_never_guesses_by_time() -> None:
    """⛔ 3분 차이로 남의 손익이 붙은 적이 있다 — id 는 하나뿐이다."""
    from updown.orchestration.walkforward.store import RunStore

    source = inspect.getsource(RunStore.stop_owners)
    for name in ("time", "created_at", "abs("):
        assert name not in source, f"시각으로 추정한다: {name}"
