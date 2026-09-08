"""예시 매매법(이동평균 교차) — 공개 저장소가 매매법 없이도 끝까지 돌기 위한 표본 (T224).

막아야 하는 실패:
1. 교차가 **막 일어난 봉**에서만 낸다 — 이미 위에 있던 봉에서 또 내면 봉마다 같은 자리를 되풀이한다.
2. 손절은 늘 있고 진입 - 2xATR 이다. 목표는 그 거리의 2배.
3. 숏은 설정으로만 열린다 (예시는 롱만).
4. 룰 설정·entry point·레지스트리가 짝을 이룬다 — 공개 저장소의 유일한 탐지기가 등록돼 있어야 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.detectors.registry import SetupRegistry, discovered_detectors
from updown.analysis.detectors.rules import load_rules
from updown.analysis.detectors.sample_ma_cross import RULE_ID, ma_cross_setup
from updown.analysis.indicators.atr import atr
from updown.analysis.playbook.select import load_playbooks
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
COST = Decimal("0.0015")


def _candle(close: float, i: int, prev: float) -> Candle:
    value, opened = Decimal(str(close)), Decimal(str(prev))
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H4,
        ts=START + timedelta(hours=4 * i),
        open=opened,
        high=max(value, opened) + Decimal("0.5"),
        low=min(value, opened) - Decimal("0.5"),
        close=value,
        volume=Decimal(1),
    )


def _window(closes: list[float]) -> list[Candle]:
    return [_candle(close, i, closes[i - 1] if i else close) for i, close in enumerate(closes)]


def _setup(window: list[Candle], *, allow_short: bool = False):
    return ma_cross_setup(
        window,
        Timeframe.H4,
        COST,
        fast=20,
        slow=50,
        sl_atr=Decimal("2.0"),
        rr=Decimal("2.0"),
        allow_short=allow_short,
    )


FLAT = [100.0] * 70


class TestItFiresOnlyOnTheCrossingBar:
    def test_fresh_upward_cross_is_a_long(self) -> None:
        window = _window([*FLAT, 130.0])
        made = _setup(window)
        assert made is not None
        assert made.avg_entry == Decimal("130.0")
        span = atr([c.high for c in window], [c.low for c in window], [c.close for c in window])[-1]
        assert span is not None
        assert made.stop_loss == Decimal("130.0") - 2 * span
        assert made.tp_ladder[0].price == Decimal("130.0") + 4 * span
        assert made.stop_loss < made.avg_entry < made.tp_ladder[0].price

    def test_no_cross_means_nothing(self) -> None:
        assert _setup(_window(FLAT)) is None

    def test_a_stale_cross_is_not_repeated(self) -> None:
        """두 번째 봉에서는 직전 봉이 이미 위였다 — 다시 내지 않는다."""
        assert _setup(_window([*FLAT, 130.0, 131.0])) is None

    def test_short_only_when_allowed(self) -> None:
        window = _window([*FLAT, 70.0])
        assert _setup(window) is None
        made = _setup(window, allow_short=True)
        assert made is not None
        assert made.stop_loss > made.avg_entry > made.tp_ladder[0].price

    def test_warmup_is_respected(self) -> None:
        assert _setup(_window([*[100.0] * 30, 130.0])) is None


class TestItIsWiredLikeAnyOtherRule:
    def test_registered_through_the_entry_point(self) -> None:
        assert RULE_ID in {rule_id for rule_id, _factory in discovered_detectors()}

    def test_config_and_registry_agree(self) -> None:
        registry = SetupRegistry.from_plugins(load_rules())
        assert RULE_ID in registry.available()

    def test_declared_as_a_playbook(self) -> None:
        books = {book.playbook_id: book for book in load_playbooks()}
        assert RULE_ID in books
        assert books[RULE_ID].setups == (RULE_ID,)
        # 실제 매매법이 함께 선언된 트리에서는 예시가 기본이 되면 안 된다.
        # 공개본(예시 + 차트 주문만)에서는 예시가 유일한 후보라 recommended 다 — 그 경우만 허용.
        others = [b for b in books.values() if b.playbook_id not in (RULE_ID, "custom")]
        if others:
            assert books[RULE_ID].recommended is False, (
                "예시가 기본이 되면 안 된다 (전략 패키지가 앞이다)"
            )
