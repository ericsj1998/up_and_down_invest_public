"""쇼케이스(포트폴리오 데모) — 켜짐 조건 · 거래 요청 거절 · 실계좌에선 절대 안 켜짐 (2026-10-02)."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from updown.apps.api import auth, showcase


def test_off_by_default() -> None:
    assert showcase.enabled({}) is False
    assert showcase.enabled({"UPDOWN_SHOWCASE": "0"}) is False


def test_on_for_paper() -> None:
    assert showcase.enabled({"UPDOWN_SHOWCASE": "1", "LIVE_ORDERS": "0"}) is True
    assert showcase.enabled({"UPDOWN_SHOWCASE": " 1 "}) is True


def test_never_on_real_money() -> None:
    """실계좌 api 에 잘못 붙어도 거래가 멈추지 않는다."""
    assert showcase.enabled({"UPDOWN_SHOWCASE": "1", "LIVE_ORDERS": "1"}) is False
    assert showcase.enabled({"UPDOWN_SHOWCASE": "1", "STOCK_LIVE_ORDERS": "1"}) is False


def test_live_compose_caps_the_demo_and_turns_it_on() -> None:
    """데모 컨테이너에 CPU 천장 · 낮은 몫 · 쇼케이스 플래그가 있고, 실계좌 api 슬롯엔 없다."""
    root = Path(__file__).resolve().parents[1]
    raw: Any = yaml.safe_load((root / "docker/compose.live.yml").read_text(encoding="utf-8"))
    services = cast("dict[str, dict[str, Any]]", raw["services"])
    demo = services["api_demo"]
    assert demo["environment"]["UPDOWN_SHOWCASE"] == "1"
    assert float(demo["deploy"]["resources"]["limits"]["cpus"]) <= 0.25
    assert int(demo["cpu_shares"]) <= 256
    for slot in ("api", "api_b"):
        env = cast("dict[str, Any]", (services.get(slot) or {}).get("environment") or {})
        assert "UPDOWN_SHOWCASE" not in env, slot


def test_guard_refuses_trade_writes_in_showcase(monkeypatch: pytest.MonkeyPatch) -> None:
    """문(auth)의 거래 분기가 쇼케이스면 403 + 안내 — 리더 없이 판을 띄우지 않는다."""
    src = inspect.getsource(auth)
    i = src.index("if showcase.enabled():")
    j = src.index('gate = getattr(request.app.state, "trading_leader", None)')
    assert i < j, "쇼케이스 거절이 리더 검사보다 먼저여야 한다"
    assert "showcase.MESSAGE" in src[i:j] and "403" in src[i:j]
    monkeypatch.setenv("UPDOWN_SHOWCASE", "1")
    monkeypatch.setenv("LIVE_ORDERS", "0")
    assert showcase.enabled() is True


def test_showcase_runs_the_fund_but_skips_the_extras() -> None:
    """리더는 잡고(펀드가 돈다) 부가 루프는 쇼케이스 분기 뒤에만 있다."""
    from updown.apps.api import main

    src = inspect.getsource(main)
    assert "if redis is not None and not showcase.enabled()" not in src, "리더를 잡아야 펀드가 돈다"
    cut = src.index("if showcase.enabled():\n            from updown.apps.api.report import")
    before, after = src[:cut], src[cut:]
    for must in (
        "autostart_live()",
        "restore_funds()",
        "rebalance_loop()",
        "watch_forever()",
        "reconcile_loop()",
    ):
        assert must in before, must
    for extra in (
        "preview_loop()",
        "notify_loop()",
        "daily_report_loop()",
        "chart_order_resolve_loop()",
    ):
        assert extra in after and extra not in before, extra
