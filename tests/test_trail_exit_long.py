"""`trail_exit_pct_long`(683차 R15 후보) — 선언 키 · 되돌림 판정 순수 함수 · 기본값 동결."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from updown.analysis.playbook.select import _KNOWN_KEYS, load_playbooks
from updown.orchestration.walkforward.session import trail_exit_hit

T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)


def _rows(highs: list[str], closes: list[str]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(ts=T0 + k * H, high=Decimal(h), close=Decimal(c))
        for k, (h, c) in enumerate(zip(highs, closes, strict=True))
    ]


class TestTrailExit:
    def test_key_is_declarable_and_frozen_by_default(self) -> None:
        assert "trail_exit_pct_long" in _KNOWN_KEYS
        # 실계좌 매매법은 이 키를 안 쓴다 — 기본 None 이면 청산 가지가 없는 것과 같다
        assert all(book.trail_exit_pct_long is None for book in load_playbooks())

    def test_hits_when_close_retraces_from_peak(self) -> None:
        # 진입 100 · 고가 120 까지 오른 뒤 종가 101 (= 120 x 0.85 = 102 아래) → 나간다
        rows = _rows(["105", "120", "104"], ["104", "118", "101"])
        assert trail_exit_hit(Decimal(100), T0, rows, Decimal(15)) is True

    def test_no_hit_inside_band(self) -> None:
        rows = _rows(["105", "120", "110"], ["104", "118", "103"])
        assert trail_exit_hit(Decimal(100), T0, rows, Decimal(15)) is False

    def test_peak_ignores_bars_before_open(self) -> None:
        # 체결 전 봉의 고가 150 은 최고가에 안 들어간다 → 최고가 105 · 종가 100 은 15% 안
        rows = _rows(["150", "105", "102"], ["140", "104", "100"])
        assert trail_exit_hit(Decimal(100), T0 + H, rows, Decimal(15)) is False

    def test_peak_floor_is_entry(self) -> None:
        # 오른 적이 없어도 최고가는 진입가 — 종가가 진입가의 85% 아래면 나간다
        # (손절이 먼저 오는 게 보통)
        rows = _rows(["99", "95"], ["96", "84"])
        assert trail_exit_hit(Decimal(100), T0, rows, Decimal(15)) is True
