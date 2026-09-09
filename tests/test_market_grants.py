"""T242 — 시장별 권한: 정책 · 유효 권한 · 내장 기본값 · Caller.market · 관문 403."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from updown.apps.api import auth as mod
from updown.common.domain.instrument import Market
from updown.common.security.caps import Cap, market_policy_of
from updown.common.security.markets import (
    BUILTIN_MARKET_POLICIES,
    DEFAULT_MARKET_POLICY,
    MarketGrant,
    MarketPolicy,
    effective_market_grant,
    group_of,
)
from updown.common.security.roles import Role


class TestPolicy:
    def test_group_of_markets(self) -> None:
        assert group_of(Market.GATE) == "coin" and group_of(Market.BINANCE) == "coin"
        assert group_of(Market.KRX) == "domestic"
        assert group_of(Market.NASDAQ) == "foreign" and group_of(Market.NYSE) == "foreign"

    def test_json_round_trip_and_unknown_groups_dropped(self) -> None:
        made = MarketPolicy.from_json({"view": "*", "backtest": ["coin", "moon"], "trade": []})
        assert made.allows("view", "foreign") and made.allows("backtest", "coin")
        assert not made.allows("backtest", "foreign") and not made.allows("trade", "coin")
        assert made.to_json() == {"view": "*", "backtest": ["coin"], "trade": []}
        assert MarketPolicy.from_json("garbage") == MarketPolicy()

    def test_builtin_defaults_follow_the_draft(self) -> None:
        guest = BUILTIN_MARKET_POLICIES["guest"]
        assert guest.allows("view", "foreign") and guest.allows("trade", "coin")
        assert not guest.allows("trade", "foreign"), "게스트는 주식 거래 없음"
        viewer = BUILTIN_MARKET_POLICIES["viewer"]
        assert viewer.allows("view", "domestic") and not viewer.allows("trade", "coin")
        assert BUILTIN_MARKET_POLICIES["trader"].allows("trade", "foreign")
        assert not DEFAULT_MARKET_POLICY.allows("trade", "coin")


class TestEffectiveGrant:
    def test_row_overrides_policy(self) -> None:
        row = MarketGrant("foreign", view=True, backtest=True, trade=False)
        got = effective_market_grant(
            "foreign", policy=BUILTIN_MARKET_POLICIES["trader"], row=row, audit=False
        )
        assert got.backtest and not got.trade

    def test_audit_opens_view_and_backtest_only(self) -> None:
        got = effective_market_grant("coin", policy=MarketPolicy(), row=None, audit=True)
        assert got.view and got.backtest and not got.trade

    def test_view_is_required_and_group_must_be_known(self) -> None:
        with pytest.raises(ValueError, match="보기"):
            MarketGrant("coin", view=False, backtest=True, trade=False).validate()
        with pytest.raises(ValueError, match="갈래"):
            MarketGrant("moon", view=True, backtest=False, trade=False).validate()


class TestCaller:
    def test_role_only_caller_uses_the_builtin_policy(self) -> None:
        guest = mod.Caller(email="g@example.com", role=Role.GUEST, fresh=True)
        assert guest.market("coin").trade and not guest.market("foreign").trade
        assert guest.market("foreign").view
        viewer = mod.Caller(email="v@example.com", role=Role.VIEWER, fresh=True)
        assert not viewer.market("coin").trade

    def test_rows_and_audit_compose(self) -> None:
        who = mod.Caller(
            email="t@example.com",
            role=Role.TRADER,
            fresh=True,
            caps=frozenset({Cap.AUDIT}),
            market_rows={"foreign": MarketGrant("foreign", view=True, backtest=False, trade=False)},
        )
        got = who.market("foreign")
        assert got.backtest and not got.trade

    def test_market_policy_of_falls_back(self) -> None:
        assert market_policy_of(None, Role.GUEST) == BUILTIN_MARKET_POLICIES["guest"]
        assert not market_policy_of(None, Role.PENDING).allows("trade", "coin")


class TestGate:
    def test_require_market_trade_blocks_and_passes(self) -> None:
        guest = mod.Caller(email="g@example.com", role=Role.GUEST, fresh=True)
        request = SimpleNamespace(state=SimpleNamespace(caller=guest))
        mod.require_market_trade(request, Market.GATE)  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as caught:
            mod.require_market_trade(request, Market.NASDAQ)  # type: ignore[arg-type]
        assert caught.value.status_code == 403
        detail = cast("dict[str, Any]", caught.value.detail)
        assert detail["code"] == "market_forbidden"
        bypass = SimpleNamespace(state=SimpleNamespace())
        mod.require_market_trade(bypass, Market.NASDAQ)  # type: ignore[arg-type]  # 시험 우회는 통과
