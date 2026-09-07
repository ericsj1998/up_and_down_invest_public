"""체결 줄에 **누구의 손익**이 붙나 (사용자 신고 2026-08-20).

Note:
    사용자 신고: *"이거는 실현 손익이 마이너스인데, 어떻게 돈을 번거야???"*

    같은 줄에 **+1.40%** 와 **-9.1037 USDT** 가 나란히 있었다. 두 가지가 겹쳐 있었다.

    🔴 **① 남의 손익이 붙었다.** 청산 기록을 **시각 근접(±5분)** 으로 골랐는데, 양쪽에
    주문 이름(`text`)이라는 정확한 열쇠가 있었다:

    ```
    주문 20:51:10  t-2a69a9715081-cl-0   붙은 손익 -9.1037   🔴
    청산 20:51:10  t-2a69a9715081-cl-0   진짜 손익 -0.5866
    청산 20:48:00  t-2904cb3cd6dd-cl-0        손익 -9.1037   ← 여기서 왔다
    ```

    🔴 **② 두 칸이 다른 것을 재고 있었다.** 수익률은 **원장 진입가**로, 손익은 **거래소
    실현**으로 냈다. 둘 다 사실이었다 — 가격으로는 이겼고 수수료로 졌다:

    ```
    가격 변동   +0.0702%  x 20배 = +1.40%          수수료 전
    거래소 실현            -0.5866 USDT = -0.60%   수수료 포함
    ```

    ⚠️ 그 차이 **2.00%p** 가 왕복 수수료다. 20배에서는 그것이 승부를 가른다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from updown.apps.api.exchange import (
    _apply_returns,  # pyright: ignore[reportPrivateUsage]
    _returns,  # pyright: ignore[reportPrivateUsage]
    _with_money,  # pyright: ignore[reportPrivateUsage]
)

LOT = Decimal("0.01")
"""ETH 계약 승수."""

# 🔴 실측 그대로 — 두 청산이 3분 차이로 붙어 있다.
CLOSES = [
    {
        "time": "1787215870",
        "text": "t-2a69a9715081-cl-0",
        "side": "short",
        "max_size": "-85",
        "long_price": "2314.55",
        "short_price": "2316.175294117647",
        "pnl": "-0.58655825",
    },
    {
        "time": "1787215680",
        "text": "t-2904cb3cd6dd-cl-0",
        "side": "short",
        "max_size": "-85",
        "long_price": "2314.4",
        "short_price": "2306",
        "pnl": "-9.10367",
    },
]

FILL = {
    "id": "78956012",
    "size": "85",
    "left": "0",
    "price": "0",
    "fill_price": "2314.55",
    "text": "t-2a69a9715081-cl-0",
    "status": "finished",
    "finish_as": "filled",
    "is_reduce_only": "True",
    "finish_time": "1787215870",
}


class TestItJoinsByName:
    """① 이름이 정확한 열쇠인데 시각으로 골랐다."""

    def test_the_right_pnl_lands(self) -> None:
        got = _with_money([FILL], CLOSES, LOT)[0]
        assert got["pnl"] == "-0.58655825", "3분 떨어진 남의 매매 결과가 붙었다"

    def test_an_earlier_close_does_not_steal_it(self) -> None:
        """⛔ 근접으로 고르면 순서에 따라 남의 것이 먼저 집힌다."""
        got = _with_money([FILL], list(reversed(CLOSES)), LOT)[0]
        assert got["pnl"] == "-0.58655825"

    def test_a_named_close_is_not_reused_by_time(self) -> None:
        """⚠️ 이름으로 짝지어진 청산은 시각 대조 후보에서 빠진다.

        안 빼면 이름 없는 주문(강제청산 liq-)이 남의 짝을 가로챈다.
        """
        orphan = {**FILL, "id": "999", "text": "", "finish_time": "1787215871"}
        rows = _with_money([FILL, orphan], CLOSES, LOT)
        assert rows[0]["pnl"] == "-0.58655825"
        # 남은 것은 20:48 짜리뿐이다 — 그것이 붙되 **추정이라고 밝힌다.**
        assert rows[1]["pnl"] == "-9.10367"
        assert rows[1]["pnl_guessed"] == "true"

    def test_entries_never_get_a_pnl(self) -> None:
        """🔴 진입은 포지션을 여는 것이라 그 순간 번 돈이 없다."""
        entry = {**FILL, "is_reduce_only": "False", "text": "t-82e456-2a69a971-en-0"}
        assert "pnl" not in _with_money([entry], CLOSES, LOT)[0]


class TestBothColumnsMeasureTheSameThing:
    """② 수익률과 손익이 **같은 근거**에서 나와야 한다."""

    def test_the_percent_is_net_of_fees(self) -> None:
        """🔴 실측 검산 — 손익 -0.5866 / 증거금 98.44 = -0.60%."""
        rows = _with_money([FILL], CLOSES, LOT)
        _apply_returns(rows, {"2a69a9715081": {"leverage": "20"}})
        assert Decimal(rows[0]["gain_pct"]) == Decimal("-0.5959")

    def test_the_leverage_comes_from_the_ledger(self) -> None:
        """🔴 사용자 요구: *"레버리지도 RUN 이 지워져도 남게"*.

        leverage_of 는 메모리에 살아 있는 판만 본다 — 지우면 빈칸이고, 그러면
        증거금을 몰라 수익률도 못 낸다.
        """
        rows = _with_money([FILL], CLOSES, LOT)
        assert not rows[0]["leverage"], "지운 판이라 메모리에는 없다"
        _apply_returns(rows, {"2a69a9715081": {"leverage": "20"}})
        assert rows[0]["leverage"] == "20"
        assert "gain_pct" in rows[0]

    def test_without_the_ledger_it_stays_blank(self) -> None:
        """⛔ 배율을 모르면 지어내지 않는다 — 1배로 치면 20배 작게 보인다."""
        rows = _with_money([FILL], CLOSES, LOT)
        _apply_returns(rows, {})
        assert "gain_pct" not in rows[0]
        # ⭐ 가격 변동은 배율과 무관하므로 그것만은 남는다.
        assert rows[0]["move_pct"] == "0.0702"

    def test_it_keeps_the_gross_move_too(self) -> None:
        """⭐ 둘의 차이가 곧 **비용이 먹은 몫**이다 — 버리면 그것을 잃는다."""
        got = _returns(CLOSES[0], LOT)
        move = Decimal(got["move_pct"])
        assert move == Decimal("0.0702")
        # 가격으로는 이겼다. 수수료로 졌다.
        assert move > 0
        rows = _with_money([FILL], CLOSES, LOT)
        _apply_returns(rows, {"2a69a9715081": {"leverage": "20"}})
        assert Decimal(rows[0]["gain_pct"]) < 0
        # ⚠️ 20배에서 왕복 수수료가 증거금의 2%p 다.
        bite = move * 20 - Decimal(rows[0]["gain_pct"])
        assert Decimal("1.9") < bite < Decimal("2.1")

    def test_a_long_uses_the_other_entry(self) -> None:
        """⭐ 차익의 분자는 방향과 무관하고, 다른 것은 **무엇이 진입가인가**뿐이다."""
        long_side = {**CLOSES[0], "side": "long", "pnl": "1.0"}
        got = _returns(long_side, LOT)
        # 롱은 long_price 에 사서 short_price 에 판다 — 값이 올랐으니 이익이다.
        assert Decimal(got["move_pct"]) > 0

    def test_it_refuses_to_invent(self) -> None:
        """⛔ 배율·승수·가격 중 하나라도 없으면 안 낸다."""
        assert _returns(CLOSES[0], Decimal(0)) == {}
        assert _returns({**CLOSES[0], "short_price": "0"}, LOT) == {}
        # 손익이 없으면 수익률을 못 낸다 — 지어내지 않는다.
        rows = _with_money([{**FILL, "text": "t-x"}], [{**CLOSES[0], "pnl": ""}], LOT)
        _apply_returns(rows, {})
        assert "gain_pct" not in rows[0]


class TestTheStopFillGetsItsPercentToo:
    """③ 손절 발동 줄만 수익률이 빈칸이었다 (사용자 신고 2026-08-20).

    실측 — 같은 줄에 **배율은 있는데 수익률만 없었다**:

    ```
    ao-2090441265442193408   pnl -25.068  notional 3057  gain **없음**
    t-6adb7ca9ce0b-cl-0      pnl  -8.276  notional 1269  gain -13.05
    ```

    🔴 `trade_of_text("ao-...")` 는 빈 문자열이다 — Gate 가 만든 이름이라 매매 id 가
    없다. 그래서 `_ledger_side` 가 조건부 주문 id 로 되찾아 **그 이름으로도** 계획을
    걸어 두는데, `_apply_returns` 가 그 이름을 안 봤다.

    ⚠️ 화면(`tradeOf`)은 `ao-` 를 그대로 열쇠로 쓴다 — **두 규칙이 갈렸고 조용히
    실패했다.**
    """

    STOP_FILL: ClassVar[dict[str, str]] = {
        "id": "1",
        "size": "250",
        "left": "0",
        "price": "0",
        "fill_price": "1.2316",
        "text": "ao-2090441265442193408",
        "status": "finished",
        "finish_as": "filled",
        "is_reduce_only": "True",
        "finish_time": "1787217000",
    }
    STOP_CLOSE: ClassVar[dict[str, str]] = {
        "time": "1787217000",
        "text": "ao-2090441265442193408",
        "side": "short",
        "max_size": "-250",
        "long_price": "1.2316",
        "short_price": "1.2228",
        "pnl": "-25.068",
    }

    def test_it_finds_the_plan_by_the_order_name(self) -> None:
        rows = _with_money([self.STOP_FILL], [self.STOP_CLOSE], Decimal(10))
        # ⭐ `_ledger_side` 가 걸어 주는 모양 그대로 — 조건부 주문 이름이 열쇠다.
        _apply_returns(rows, {"ao-2090441265442193408": {"leverage": "20"}})
        assert rows[0]["leverage"] == "20"
        assert "gain_pct" in rows[0], "손절 발동만 수익률이 빈칸이 된다"

    def test_the_number_is_right(self) -> None:
        """🔴 손익 -25.068 / 증거금(3057/20 = 152.85) = -16.4%."""
        rows = _with_money([self.STOP_FILL], [self.STOP_CLOSE], Decimal(10))
        _apply_returns(rows, {"ao-2090441265442193408": {"leverage": "20"}})
        got = Decimal(rows[0]["gain_pct"])
        assert Decimal("-17") < got < Decimal("-16")

    def test_it_still_reads_our_own_names(self) -> None:
        """⛔ `ao-` 를 챙기다 우리 이름을 놓치면 안 된다."""
        ours = {**self.STOP_FILL, "text": "t-6adb7ca9ce0b-cl-0"}
        close = {**self.STOP_CLOSE, "text": "t-6adb7ca9ce0b-cl-0"}
        rows = _with_money([ours], [close], Decimal(10))
        _apply_returns(rows, {"6adb7ca9ce0b": {"leverage": "20"}})
        assert rows[0]["leverage"] == "20"
        assert "gain_pct" in rows[0]

    def test_without_a_plan_it_stays_blank(self) -> None:
        """⛔ 배율을 모르면 지어내지 않는다 — 규격 이전 조건부는 이을 열쇠가 없다."""
        rows = _with_money([self.STOP_FILL], [self.STOP_CLOSE], Decimal(10))
        _apply_returns(rows, {})
        assert "gain_pct" not in rows[0]
        # ⭐ 가격 변동은 배율과 무관하므로 남는다.
        assert rows[0]["move_pct"]
