"""첫 화면 시장 칸 — 판이 들고 있는 문 값 · 선언 → 화면 줄 (2026-11-21).

새로 계산하지 않고 세션 값을 읽는다 · 문턱 · 다리 이름은 선언에서 · 모르면 "모름"(새 진입 보류).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from updown.analysis.playbook.types import (
    EventWindow,
    RefReturnBand,
    RefVolOff,
    WashedRule,
)
from updown.apps.api.market_strip import fear_row, gate_rows, next_release, price_rows
from updown.common.domain.releases import ReleaseSchedule

NOW = datetime(2026, 11, 21, 14, 0, tzinfo=UTC)


def _book(name: str, **rules: object) -> SimpleNamespace:
    base = {
        "short_label": name,
        "playbook_id": name,
        "entry_ref_return_band": None,
        "entry_ref_surge_cap": None,
        "entry_ref_vol_off": None,
        "entry_ref_vol_rank": None,
        "entry_washed": None,
        "entry_event_window": None,
    }
    base.update(rules)
    return SimpleNamespace(**base)


def test_rows_follow_declarations_and_session_values() -> None:
    band = RefReturnBand(bars=360, low=Decimal("-0.15"), high=Decimal("0.15"))
    voff = RefVolOff(days=20, min_pct=Decimal("3.5"))
    washed = WashedRule(
        ref_days=7,
        breadth_ma=20,
        breadth_below=Decimal("0.4"),
        lag_days=3,
        lag_rank_days=30,
        need=2,
    )
    window = EventWindow(hours=Decimal(8), kinds=("cpi",))
    a = _book("다리A", entry_ref_return_band=band, entry_washed=washed)
    b = _book("다리B", entry_ref_vol_off=voff, entry_event_window=window)
    release = NOW + timedelta(hours=3)
    sched = ReleaseSchedule(
        since=NOW - timedelta(days=30), horizon=NOW + timedelta(days=300), times={"cpi": (release,)}
    )
    s1 = SimpleNamespace(
        playbooks=[a],
        ref_return=Decimal("-0.27"),
        ref_washed_ret=Decimal("-0.05"),
        market_breadth=Decimal("0.30"),
        market_lag_ret=Decimal("0.01"),
    )
    s2 = SimpleNamespace(playbooks=[b], ref_vol_sd_pct=Decimal("2.1"), release_schedule=sched)
    rows = {r.key: r for r in gate_rows([s1, s2], NOW)}
    assert set(rows) == {
        "entry_ref_return_band",
        "entry_washed",
        "entry_ref_vol_off",
        "entry_event_window",
    }
    assert rows["entry_ref_return_band"].state == "쉼" and rows["entry_ref_return_band"].legs == (
        "다리A",
    )
    assert rows["entry_ref_return_band"].value == "-27.0%"
    assert rows["entry_ref_vol_off"].state == "열림"
    # BTC 마이너스 · 폭 < 0.4 하나 → 종목 펀딩도 마이너스면 둘 = 쉼
    assert rows["entry_washed"].state == "열림" and "펀딩" in rows["entry_washed"].note
    # 발표 3 시간 앞 = ±8 시간 안
    assert rows["entry_event_window"].state == "쉼"
    assert next_release(sched, NOW, ("cpi",)) == ("cpi", release)


def test_unknown_values_say_unknown_and_undeclared_gates_are_absent() -> None:
    band = RefReturnBand(bars=360, low=Decimal("-0.15"), high=Decimal("0.15"))
    s = SimpleNamespace(playbooks=[_book("다리A", entry_ref_return_band=band)], ref_return=None)
    rows = gate_rows([s], NOW)
    assert [r.key for r in rows] == ["entry_ref_return_band"]
    assert rows[0].state == "모름"
    assert gate_rows([SimpleNamespace(playbooks=[_book("다리C")])], NOW) == []


def test_fear_row_and_prices() -> None:
    closes = {date(2026, 11, 19): Decimal("28.4"), date(2026, 11, 20): Decimal("35.89")}
    row = fear_row({"fear": 0}, 30.0, closes)
    assert row is not None and row.state == "열림" and "35.89(2026-11-20)" in row.value
    assert "다음 마감에 조각 하나" in row.note
    assert fear_row({"fear": 0}, 30.0, {date(2026, 11, 20): Decimal("21")}).state == "쉼"  # type: ignore[union-attr]
    assert fear_row(None, 30.0, None).state == "모름"  # type: ignore[union-attr]
    assert fear_row(None, 0.0, closes) is None  # 꺼진 다리는 줄 없음
    ticks = [
        {"contract": "BTC_USDT", "last": "60500", "change_percentage": "3.1"},
        {"contract": "DOGE_USDT", "last": "0.1", "change_percentage": "1"},
    ]
    assert price_rows(ticks, ("BTC_USDT", "ETH_USDT")) == [
        {"symbol": "BTC_USDT", "last": "60500", "change_pct": "3.1"}
    ]
