"""지정가 진입 거절이 **원인을 가를 값**을 남기는가 (`_entry_forensics`).

## 왜 이 시험이 있나

2026-08-29 실측: Binance 에서 post-only 거절이 18건 났는데, 남은 것이 *"거절됐다"* 뿐이라
원인을 사후에 좁히지 못했다 — `wf_orders.raw_json` 은 `{}` 였고 컨테이너 로그는 재기동에
날아갔다. 같은 시점 캔들로 탐지기를 다시 돌려 보니:

    탐지기가 낸 지정가   80,216.23   (= 종가 x 0.997)
    거래소로 나간 지정가  80,457.60   (= 종가 그대로)

**0.30% 물러섬이 중간에서 사라졌다.** post-only 는 호가를 넘으면 거부하므로, 종가에 그대로
걸면 진입이 동전 던지기가 된다.

## 무엇을 고정하는가

`plan_pct`(원장의 계획 평단)와 `leg_pct`(실제로 나간 지정가)를 **따로** 재는 것.

    plan_pct ≈ -0.30 · leg_pct = 0.00   → 손실 지점은 **사다리**
    plan_pct = 0.00  · leg_pct = 0.00   → 손실 지점은 **탐지기 위쪽**

하나만 재면 이 구분이 안 되고, 구분이 안 되면 다음 거절에서도 똑같이 못 좁힌다. 그래서
*"값이 남는다"* 가 아니라 **"두 값이 따로 남는다"** 를 시험한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from updown.orchestration.walkforward.live_filler import Want


class _Rows:
    """`_feed.observed()` 대역 — 마지막 봉의 종가만 쓴다."""

    entry = None
    """러너의 `entry` 프로퍼티가 읽어 가는 칸 (`LiveRunner.entry` → `self._feed.entry`)."""

    def __init__(self, close: Decimal | None) -> None:
        self._close = close

    def observed(self, _timeframe: object) -> list[object]:
        if self._close is None:
            return []
        return [type("Bar", (), {"close": self._close})()]


class _Waiting:
    """원장의 대기 매매 대역 — 계획 평단만 쓴다."""

    def __init__(self, entry: Decimal) -> None:
        self.entry = entry


def _forensics(*, sent: Decimal, plan: Decimal, close: Decimal | None) -> dict[str, object]:
    """러너를 통째로 세우지 않고 진단 함수만 떼어 부른다."""
    from updown.orchestration.walkforward.live_runner import LiveRunner

    runner = object.__new__(LiveRunner)
    runner._feed = _Rows(close)  # type: ignore[attr-defined]
    want = Want(ticket="t:0", price=sent, ratio=Decimal(1), long=True)
    return LiveRunner._entry_forensics(runner, want, _Waiting(plan))  # type: ignore[arg-type]


def test_offset_preserved_shows_matching_widths() -> None:
    """정상: 원장도 다리도 종가에서 -0.30% 물러나 있다."""
    close = Decimal("80457.60")
    back = close * Decimal("0.997")
    got = _forensics(sent=back, plan=back, close=close)
    assert got["plan_pct"] == "-0.3000"
    assert got["leg_pct"] == "-0.3000"


def test_offset_lost_in_the_ladder_splits_the_widths() -> None:
    """🔴 2026-08-29 의 모양 — 원장은 물러났는데 나간 값은 종가다.

    이 갈림이 안 보이면 *"거절됐다"* 만 남고 원인은 다음에도 못 좁힌다.
    """
    close = Decimal("80457.60")
    got = _forensics(sent=close, plan=close * Decimal("0.997"), close=close)
    assert got["plan_pct"] == "-0.3000"
    assert got["leg_pct"] == "+0.0000"
    assert got["plan_pct"] != got["leg_pct"]


def test_offset_lost_above_the_detector_zeroes_both() -> None:
    """다른 원인: 원장부터 이미 종가라 사다리는 무죄다."""
    close = Decimal("2497.96")
    got = _forensics(sent=close, plan=close, close=close)
    assert got["plan_pct"] == "+0.0000"
    assert got["leg_pct"] == "+0.0000"


@pytest.mark.parametrize("close", [None, Decimal(0)])
def test_missing_close_still_records_the_prices(close: Decimal | None) -> None:
    """⛔ 진단이 주문 경로를 멈추면 규칙 #8-1 위반이다.

    비율은 못 내더라도 **보낸 값과 계획 값은 남는다** — 그것만으로도 다음 진단의 절반이다.
    """
    got = _forensics(sent=Decimal("100"), plan=Decimal("99.7"), close=close)
    assert got["sent"] == "100"
    assert got["plan"] == "99.7"
    assert "plan_pct" not in got
