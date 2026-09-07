"""체결 이력에 **원장을 붙인다** (사용자 요구 2026-08-20).

Note:
    사용자 지적: *"지금 체결 이력이 너무 보기 힘들어."* 요구가 다섯이었고, 넷은 거래소
    행만으로는 답이 안 나온다:

    ```
    RUN 열에 종목명 · 판을 지워도 남게      → 원장 (거래소 행의 symbol 도 씀)
    실현 손익 단위                          → USDT 다. 화면 문구
    레버리지도 판을 지워도 남게             → 원장 (거래소 행은 늘 빈 문자열이다)
    누르면 진입가·손절가·1차익절가·익절     → 원장에만 있다
    ```

    🔴 **조인 열쇠는 주문 이름에 박은 매매 id 다** (T18 ⑤). 그런데 이름에 30자 제한이
    있어 새 형식에서는 **8자로 잘린다** — 완전 일치로 찾으면 전부 안 맞는다.

    ⭐ **판을 지워도 남는다.** 지우는 것은 `closed_at` 을 찍는 일이라 행이 그대로다.
"""

from __future__ import annotations

import inspect

import pytest

from updown.orchestration.walkforward.live_runner import run_of_text, trade_of_text


class TestTheJoinKey:
    """이름에서 매매 id 를 되뽑는다 — 화면과 **같은 규칙**이어야 한다."""

    @pytest.mark.parametrize(
        ("text", "trade", "run"),
        [
            # 옛 형식 — 판 표식 없이 매매 id 12자.
            ("t-72bb3b4690f3-cl-0", "72bb3b4690f3", ""),
            # 🔴 새 형식 — 30자 제한 때문에 매매 id 가 8자로 잘린다.
            ("t-82e456-72bb3b46-cl-1", "72bb3b46", "82e456"),
            ("t-56f258-da61a01a-cl-1", "da61a01a", "56f258"),
            # ⚠️ 표식 자리가 6자가 아니면 **옛 형식으로 읽는다** — 그 자리가 매매 id 다.
            #    판 표식은 못 읽으므로 빈 문자열이고, 그것이 곧 "판 미상" 이다.
            ("t-4156f258-da61a01a-cl-1", "4156f258", ""),
            # ⛔ Gate 가 만든 이름은 못 읽는다.
            ("ao-2090406159629418496", "", ""),
            ("esc-1787228823", "", ""),
            ("", "", ""),
        ],
    )
    def test_it_reads_both_shapes(self, text: str, trade: str, run: str) -> None:
        assert trade_of_text(text) == trade
        assert run_of_text(text) == run

    def test_the_screen_uses_the_same_rule(self) -> None:
        """🔴 두 규칙이 갈리면 상세가 통째로 안 붙는다 — 그리고 그 실패는 조용하다."""
        source = __import__("pathlib").Path("web/src/ui.tsx").read_text(encoding="utf-8")
        cut = source.index("export function tradeOf")
        branch = source[cut : cut + 500]
        # 파이썬 쪽과 같은 세 갈래를 밟아야 한다.
        assert 'startsWith("t-")' in branch
        assert "parts[1].length === 6" in branch
        assert "return parts[1]" in branch


class TestItMatchesByPrefix:
    """⚠️ 완전 일치로 찾으면 **새 형식이 전부 안 맞는다.**"""

    def test_the_store_uses_like(self) -> None:
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.trace)
        assert '.like(f"{item}%")' in source
        assert "startswith(item)" in source

    def test_it_answers_with_the_key_it_was_given(self) -> None:
        """⭐ 화면은 **자기가 뽑은 앞자리**로 찾는다 — 전체 id 로 돌려주면 못 찾는다."""
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.trace)
        assert "for key, row in found.items()" in source


class TestItSurvivesDeletion:
    """🔴 사용자 요구의 핵심 — *"RUN 을 지워도 종목명은 남아있으면 좋겠네."*"""

    def test_deleting_a_run_only_closes_it(self) -> None:
        """⭐ 행을 지우지 않는다 — 지우면 이력이 영영 이름을 잃는다."""
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.close)
        assert "sa.delete" not in source
        assert "closed_at" in source

    def test_run_tags_include_closed_runs(self) -> None:
        """⛔ 살아 있는 판만 주면 지운 판의 주문이 전부 `RUN 미상` 이 된다."""
        from updown.orchestration.walkforward.store import RunStore

        source = inspect.getsource(RunStore.run_tags)
        assert "closed_at.is_(None)" not in source
        assert '"alive": closed is None' in source, "닫힌 판도 주되 닫혔다고 말한다"


class TestTheConsoleAsksTheLedgerReadOnly:
    """⚠️ 화면이 원장을 고치기 시작하면 *"사실"* 과 *"모형"* 의 경계가 무너진다."""

    def test_it_only_reads(self) -> None:
        from updown.apps.api import exchange

        source = inspect.getsource(exchange._ledger_side)  # pyright: ignore[reportPrivateUsage]
        assert "store.trace" in source
        assert "store.run_tags" in source
        for name in ("save_", "record_", "open(", "close("):
            assert name not in source, f"콘솔이 원장을 고친다: {name}"

    def test_a_missing_store_does_not_break_the_history(self) -> None:
        """⛔ 곁들이는 정보 하나 때문에 *"무슨 일이 있었나"* 가 사라지면 안 된다."""
        from updown.apps.api import exchange

        source = inspect.getsource(exchange._ledger_side)  # pyright: ignore[reportPrivateUsage]
        assert 'return {"plans": {}, "runs": {}}' in source
