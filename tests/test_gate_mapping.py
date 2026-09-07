"""Gate.io 선물 응답 매핑 (`marketdata/gate/mapping.py`).

여기서 지키려는 성질은 하나다.

    **조용히 틀리지 않는다.**

이 매핑에는 예외를 던지지 않고 틀리는 경로가 하나 있다 — **거래량 단위**다.
`v` 는 계약 수이고 BTC 수량이 아니라, 승수(0.0001)를 안 곱하면 값이 **1만 배**가 된다.
거래량 기준선이 그 값을 쓰므로 판정 전체가 어긋나는데 아무 오류도 나지 않는다.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.marketdata.gate.mapping import (
    CANDLE_LIMIT,
    SETTLE,
    GateMappingError,
    base_volume,
    interval_of,
    interval_seconds,
    parse_seconds,
    quote_volume,
    to_candle,
    to_contract,
    to_multiplier,
)

BTC = Instrument(
    market=Market.GATE,
    symbol="BTC_USDT",
    name="BTC 무기한",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
UPBIT_BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

MULT = Decimal("0.0001")

# 실측 응답 (2026-08-17 · /futures/usdt/candlesticks?contract=BTC_USDT&interval=15m).
REAL = {
    "o": "63354.2",
    "v": 2132098,
    "t": 1786957200,
    "c": "63397.9",
    "l": "63354.1",
    "h": "63406.1",
    "sum": "13514078.543110002",
}


class TestVolumeUnit:
    """🔴 계약 수를 BTC 수량으로 옮긴다 — 이걸 놓치면 1만 배 틀린다."""

    def test_contracts_become_base_quantity(self) -> None:
        """`v: 2132098` 계약 = 213.2098 BTC."""
        assert base_volume(REAL, MULT) == Decimal("213.2098")

    def test_the_raw_field_is_not_the_answer(self) -> None:
        """⚠️ 그대로 쓰면 1만 배다. 이 차이가 조용히 지나가면 안 된다."""
        assert base_volume(REAL, MULT) * 10000 == Decimal(REAL["v"])

    def test_quote_volume_is_a_different_number(self) -> None:
        """`sum` 은 USDT 거래대금이다 — 계약 수와 섞으면 통화 단위가 흔들린다."""
        assert quote_volume(REAL) == Decimal("13514078.543110002")
        assert quote_volume(REAL) != base_volume(REAL, MULT)

    @pytest.mark.parametrize("bad", [Decimal(0), Decimal(-1), Decimal("-0.0001")])
    def test_a_useless_multiplier_is_refused(self, bad: Decimal) -> None:
        """⛔ 0 이하 승수는 거래량을 0 이나 음수로 만든다 — 기본값으로 넘기지 않는다."""
        with pytest.raises(GateMappingError, match="승수"):
            base_volume(REAL, bad)

    def test_a_missing_field_raises(self) -> None:
        """규격이 바뀌면 조용히 0 이 되는 대신 터진다 (절대 규칙 #8)."""
        with pytest.raises(GateMappingError, match="계약 수"):
            base_volume({"sum": "1"}, MULT)


class TestMultiplierComesFromTheApi:
    """승수는 **읽는다**. 박아 두면 종목을 늘릴 때 조용히 틀린다."""

    def test_it_is_read_from_the_contract_spec(self) -> None:
        assert to_multiplier({"quanto_multiplier": "0.0001"}) == MULT

    @pytest.mark.parametrize("bad", [{"quanto_multiplier": "0"}, {"quanto_multiplier": "-1"}])
    def test_a_useless_value_raises(self, bad: dict[str, str]) -> None:
        with pytest.raises(GateMappingError):
            to_multiplier(bad)

    def test_a_missing_spec_raises(self) -> None:
        """⛔ 1 로 되돌리지 않는다 — 모르는 값을 1 로 채우면 1만 배 틀린다."""
        with pytest.raises(GateMappingError, match="quanto_multiplier"):
            to_multiplier({"name": "BTC_USDT"})


class TestTime:
    """🔴 저장은 UTC 다 (절대 규칙 #7)."""

    def test_seconds_become_aware_utc(self) -> None:
        moment = parse_seconds(1786957200, "t")
        assert moment.tzinfo is not None
        assert moment == datetime(2026, 8, 17, 9, 0, tzinfo=UTC)

    @pytest.mark.parametrize("raw", [1786957200, "1786957200", 1786957200.0, "1786957200.0"])
    def test_every_documented_shape_is_read(self, raw: object) -> None:
        """Gate 문서가 int·float·문자열 넷 다 온다고 못박고 있다."""
        assert parse_seconds(raw, "t") == datetime(2026, 8, 17, 9, 0, tzinfo=UTC)

    def test_garbage_raises(self) -> None:
        with pytest.raises(GateMappingError, match="시각"):
            parse_seconds("어제", "t")


class TestContract:
    """업비트 표기를 받으면 **다른 상품**을 거래하게 된다."""

    def test_a_gate_symbol_passes(self) -> None:
        assert to_contract(BTC) == "BTC_USDT"

    def test_an_upbit_instrument_is_refused(self) -> None:
        """🔴 KRW 현물을 USDT 무기한으로 착각하는 경로를 막는다."""
        with pytest.raises(GateMappingError, match="다른 상품"):
            to_contract(UPBIT_BTC)

    def test_an_upbit_style_symbol_is_refused(self) -> None:
        """시장은 GATE 인데 심볼이 업비트 형식인 경우 — 설정 오타다."""
        odd = Instrument(
            market=Market.GATE,
            symbol="KRW-BTC",
            name="오타",
            asset_type=AssetType.COIN,
            currency=Currency.USD,
        )
        with pytest.raises(GateMappingError, match="형식"):
            to_contract(odd)


class TestInterval:
    def test_every_current_frame_maps(self) -> None:
        """🔴 지금 있는 시간축은 **전부** 매핑돼야 한다.

        빠진 것이 있으면 그 시간축으로 조회할 때 터지는데, 그 지점이 여기서 멀다.
        `Timeframe` 에 값을 더할 때 이 표를 잊는 것이 정확히 그 사고다.
        """
        for frame in Timeframe:
            assert interval_of(frame)
            assert interval_seconds(frame) > 0

    def test_seconds_match_the_frame(self) -> None:
        assert interval_of(Timeframe.M15) == "15m"
        assert interval_seconds(Timeframe.M15) == 900
        assert interval_seconds(Timeframe.D1) == 86400

    def test_the_guard_exists_for_frames_gate_lacks(self) -> None:
        """⛔ `timeframe.value` 를 그대로 보내지 않는 이유.

        Gate 가 지원하는 것은 `10s·1m·5m·15m·30m·1h·4h·8h·1d·7d·30d` 이고 우리
        열거형과 교집합이 아니다. 지금은 우연히 다섯 개가 다 겹치지만, **주봉(`1w`)이
        P3 에서 들어오면 Gate 에는 `7d` 만 있다** — 그때 조용히 400 이 나는 대신
        여기서 터져야 한다.

        열거형에 없는 값으로 가드가 실제로 도는지 확인한다.
        """
        with pytest.raises(GateMappingError, match="지원"):
            interval_of("1w")  # type: ignore[arg-type]


class TestCandle:
    def test_a_real_response_becomes_a_candle(self) -> None:
        """실측 응답 그대로 넣어 본다."""
        candle = to_candle(REAL, BTC, Timeframe.M15, MULT)
        assert candle.ts == datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        assert candle.open == Decimal("63354.2")
        assert candle.high == Decimal("63406.1")
        assert candle.low == Decimal("63354.1")
        assert candle.close == Decimal("63397.9")
        assert candle.volume == Decimal("213.2098")

    def test_prices_keep_full_precision(self) -> None:
        """`float` 을 거치면 손절가가 조용히 어긋난다."""
        candle = to_candle({**REAL, "c": "63397.912345678"}, BTC, Timeframe.M15, MULT)
        assert candle.close == Decimal("63397.912345678")

    def test_a_broken_payload_raises(self) -> None:
        with pytest.raises(GateMappingError):
            to_candle({k: v for k, v in REAL.items() if k != "h"}, BTC, Timeframe.M15, MULT)


class TestConstants:
    def test_the_futures_limit_is_not_the_spot_limit(self) -> None:
        """선물 2000 · 현물 1000. 같은 값으로 두면 한쪽이 조용히 잘린다."""
        assert CANDLE_LIMIT == 2000

    def test_settle_is_usdt(self) -> None:
        assert SETTLE == "usdt"
