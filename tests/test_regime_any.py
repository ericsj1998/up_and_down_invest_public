"""`PlaybookRegime.ANY` — 국면을 안 쓰는 다리는 횡보 · 박스 없음 봉에서도 돈다
(515차 §304-1 · 2026-09-29).

`[RANGE, UPTREND, DOWNTREND]` 는 "모든 국면" 이 아니었다: RANGE 는 횡보 **그리고** 유효 박스라,
횡보인데 박스가 없는 봉은 어느 국면도 아니어서 탐지기가 불리지도 않았다(MACD 숏 379건 중 3건 ·
AR 2022-05-05 +25% 포함).
연구 원장에는 국면 층이 없으므로 ANY 가 연구와 같은 동작이다.
"""

from __future__ import annotations

from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import PlaybookRegime
from updown.common.domain.reports import TrendDirection

LEGS = (
    "private_strategy",
    "private_strategy",
    "private_strategy",
    "private_strategy",
    "private_strategy",
    "private_strategy",
    "private_strategy",
)


def test_three_regimes_are_not_every_regime() -> None:
    """옛 선언의 빈틈 — 횡보인데 박스가 없으면 셋 중 어느 것도 참이 아니다."""
    old = (PlaybookRegime.RANGE, PlaybookRegime.UPTREND, PlaybookRegime.DOWNTREND)
    assert not any(r.matches(TrendDirection.SIDEWAYS, has_box=False) for r in old)
    assert any(r.matches(TrendDirection.SIDEWAYS, has_box=True) for r in old)


def test_any_matches_every_state_including_warmup() -> None:
    for trend in (*TrendDirection, None):
        for has_box in (True, False):
            assert PlaybookRegime.ANY.matches(trend, has_box=has_box)


def test_live_legs_and_measurement_books_declare_any() -> None:
    """실계좌 다리 다섯과 412 · 515차 측정 판이 같은 문을 쓴다.

    하나라도 빠지면 그 다리만 신호를 버린다.
    """
    books = {item.playbook_id: item for item in load_playbooks()}
    for name in LEGS:
        assert books[name].regimes == (PlaybookRegime.ANY,), name
