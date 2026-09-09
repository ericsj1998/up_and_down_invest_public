"""T236 — 거래소 청산 행에서 실제 수수료와 명목 대비 비율을 읽는다."""

from __future__ import annotations

from decimal import Decimal

from updown.orchestration.walkforward.live_runner import fee_from_close


class TestFeeFromClose:
    def test_long_uses_long_price_and_max_size(self) -> None:
        row: dict[str, object] = {
            "pnl_fee": "-0.0468",
            "max_size": "184",
            "long_price": "0.08926",
            "side": "long",
        }
        found = fee_from_close(row, Decimal("10"))
        assert found is not None
        fee, ratio = found
        assert fee == Decimal("0.0468")
        # 명목 = 184 x 0.08926 x 10 = 164.2384 → 0.0468 / 164.2384
        assert ratio == Decimal("0.0468") / Decimal("164.2384")

    def test_short_uses_short_price(self) -> None:
        row: dict[str, object] = {
            "pnl_fee": "-1.5",
            "max_size": "-3",
            "short_price": "1000",
            "side": "short",
        }
        found = fee_from_close(row, Decimal("1"))
        assert found is not None
        assert found[1] == Decimal("1.5") / Decimal("3000")

    def test_missing_fields_do_not_invent(self) -> None:
        assert fee_from_close({"pnl": "0.25"}, Decimal("1")) is None
        assert (
            fee_from_close(
                {"pnl_fee": "-1", "max_size": "0", "long_price": "10", "side": "long"}, Decimal("1")
            )
            is None
        )
