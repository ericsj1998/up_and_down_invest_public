"""이메일 HTML 렌더 — 값이 들어가고, 못 읽은 값은 '—' 로 나온다 (T55).

막아야 하는 실패:
1. 🔴 못 읽은 금액을 0 으로 그리면 "다 잃었다"로 읽힌다 → None 은 '—'.
2. 🔴 판 이메일은 그 판만 담아야 한다 — 다른 판 종목이 새면 안 된다.
"""

from __future__ import annotations

from decimal import Decimal

from updown.orchestration.report.html import (
    AccountSummary,
    RunSection,
    TradeRow,
    render_console_email,
    render_run_email,
)

_ACC = AccountSummary(
    total=Decimal("5317.64"),
    available=Decimal("6.44"),
    locked=Decimal("5311.20"),
    realized=Decimal("33.31"),
    unrealized=Decimal("-13.46"),
    vault=Decimal("128.40"),
    win_rate=Decimal("57"),
    trades=7,
    runs=2,
    diverged=False,
    note="일치",
)


def _section(symbol: str, gain: Decimal | None) -> RunSection:
    return RunSection(
        symbol=symbol,
        playbook="박스 0.70",
        period_label="08-23 · 15m",
        gain_pct=gain,
        equity_before=Decimal("1000"),
        takes=2,
        stops=0,
        chart_b64="QUJD",  # 아무 base64
        trades=[
            TradeRow(
                direction="롱",
                entry=Decimal("76600"),
                exit=Decimal("77750"),
                gain_pct=Decimal("1.85"),
                outcome="목표 익절",
                leverage=Decimal("3"),
                when="08-23 02:00",
            )
        ],
    )


def test_console_has_totals_and_run() -> None:
    html = render_console_email(_ACC, [_section("BTC_USDT", Decimal("3.27"))], when="지금")
    assert "5,317.64" in html  # 계좌 전체 금액
    assert "128.40" in html  # 금고
    assert "BTC_USDT" in html and "목표 익절" in html
    assert html.startswith("<!doctype html>")


def test_none_amount_renders_dash() -> None:
    # 🔴 못 읽은 총액은 0 이 아니라 "—".
    acc = AccountSummary(
        total=None,
        available=None,
        locked=None,
        realized=None,
        unrealized=None,
        vault=None,
        win_rate=None,
        trades=0,
        runs=0,
        diverged=False,
        note="",
    )
    html = render_console_email(acc, [], when="지금")
    assert "—" in html and "0.00" not in html.split("계좌 전체 금액")[1][:80]


def test_run_email_is_single_run() -> None:
    html = render_run_email(_section("ETH_USDT", Decimal("-2.25")), when="지금")
    assert "ETH_USDT" in html and "BTC_USDT" not in html
    assert "-2.25%" in html
