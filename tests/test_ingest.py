"""캔들 수집 파이프라인 검증 (P0-8 · spec §12.1, §7 / plan D-9, D-14).

- **순수 코어** (`timeframes`, `integrity`): DB 없이 돈다. 원칙 P1 의 확인이기도 하다
- **`db` 마커**: 실제 PostgreSQL 로 upsert 멱등성·재개·파티션·이슈 적재를 본다
- 브로커는 **가짜 어댑터**로 대체한다 — 백필 로직 검증에 실 API 가 필요하지 않고,
  실 API 로는 "1년치 중간에 죽였다 재개" 같은 시나리오를 재현할 수 없다
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest
import sqlalchemy as sa

from updown.common.db.models.enums import QualityIssueStatus, QualityIssueType
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.ingest.backfill import (
    BackfillResult,
    BackfillScope,
    BackfillScopeError,
    drop_unclosed,
    run_backfill,
)
from updown.marketdata.ingest.collector import collect_timeframe, resolve_window
from updown.marketdata.ingest.integrity import (
    IntegrityConfigError,
    IntegrityThresholds,
    check_ohlc_logic,
    find_missing_ranges,
    inspect_candles,
)
from updown.marketdata.ingest.repository import (
    CandleRepository,
    InstrumentNotFoundError,
)
from updown.marketdata.ingest.timeframes import (
    TimeframeError,
    ceil_to_interval,
    expected_bar_count,
    floor_to_interval,
    interval,
    last_closed_ts,
)
from updown.marketdata.ingest.universe import UnknownSymbolError, resolve_universe

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
ETH = Instrument(
    market=Market.UPBIT,
    symbol="KRW-ETH",
    name="이더리움",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

BASE = datetime(2026, 8, 1, tzinfo=UTC)


def candle(
    minute_offset: int,
    *,
    instrument: Instrument = BTC,
    timeframe: Timeframe = Timeframe.M5,
    close: str = "100",
    volume: str = "1",
    high: str | None = None,
    low: str | None = None,
    open_: str | None = None,
) -> Candle:
    """테스트용 봉 하나."""
    close_value = Decimal(close)
    return Candle(
        instrument=instrument,
        timeframe=timeframe,
        ts=BASE + timedelta(minutes=minute_offset),
        open=Decimal(open_) if open_ else close_value,
        high=Decimal(high) if high else close_value,
        low=Decimal(low) if low else close_value,
        close=close_value,
        volume=Decimal(volume),
    )


def series(
    count: int,
    *,
    step_minutes: int = 5,
    instrument: Instrument = BTC,
    timeframe: Timeframe = Timeframe.M5,
    close: str = "100",
    volume: str = "1",
) -> list[Candle]:
    """연속 봉 목록."""
    return [
        candle(
            i * step_minutes,
            instrument=instrument,
            timeframe=timeframe,
            close=close,
            volume=volume,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# 1. timeframes — 순수 계산
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("timeframe", "seconds"),
    [
        (Timeframe.M5, 300),
        (Timeframe.M15, 900),
        (Timeframe.H1, 3600),
        (Timeframe.H4, 14400),
        (Timeframe.D1, 86400),
    ],
)
def test_interval_covers_every_timeframe(timeframe: Timeframe, seconds: int) -> None:
    """새 Timeframe 을 추가하고 표를 안 채우면 여기서 걸린다."""
    assert interval(timeframe).total_seconds() == seconds


def test_all_timeframes_have_an_interval() -> None:
    """열거형과 표가 어긋나면 런타임에 터진다 — 지금 잡는다."""
    for timeframe in Timeframe:
        assert interval(timeframe).total_seconds() > 0


def test_unknown_timeframe_raises_not_keyerror() -> None:
    """설명 있는 예외여야 한다 — `KeyError: <Timeframe.X>` 는 원인을 안 알려준다."""

    class Fake:
        value = "1w"

    with pytest.raises(TimeframeError, match="정의되지 않았다"):
        interval(Fake())  # pyright: ignore[reportArgumentType]


def utc(*parts: int) -> datetime:
    """UTC aware datetime 축약 — 표 형태 파라미터를 읽을 수 있게 유지한다."""
    return datetime(*parts, tzinfo=UTC)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("moment", "timeframe", "expected"),
    [
        # 4시간봉 경계는 00/04/08/12/16/20 UTC — "자정부터 세기"로는 안 맞는 간격이다
        (utc(2026, 8, 3, 13, 37), Timeframe.H4, utc(2026, 8, 3, 12)),
        (utc(2026, 8, 3, 10, 47), Timeframe.M5, utc(2026, 8, 3, 10, 45)),
        (utc(2026, 8, 3, 10, 47), Timeframe.H1, utc(2026, 8, 3, 10)),
        (utc(2026, 8, 3, 23, 59), Timeframe.D1, utc(2026, 8, 3)),
    ],
)
def test_floor_to_interval(moment: datetime, timeframe: Timeframe, expected: datetime) -> None:
    """봉 경계는 epoch 기준이다 (업비트 실측과 일치 — notes §3)."""
    assert floor_to_interval(moment, timeframe) == expected


def test_floor_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        floor_to_interval(datetime(2026, 8, 3, 10, 0), Timeframe.M5)


def test_last_closed_excludes_the_forming_bar() -> None:
    """지금 만들어지고 있는 봉은 마감된 봉이 아니다 (spec §4.2)."""
    now = datetime(2026, 8, 3, 10, 47, tzinfo=UTC)  # 10:45 봉이 형성 중
    assert last_closed_ts(now, Timeframe.M5) == datetime(2026, 8, 3, 10, 40, tzinfo=UTC)


def test_last_closed_on_exact_boundary() -> None:
    """정확히 경계 시각이면 직전 봉이 마지막 마감 봉이다."""
    now = datetime(2026, 8, 3, 10, 45, tzinfo=UTC)
    assert last_closed_ts(now, Timeframe.M5) == datetime(2026, 8, 3, 10, 40, tzinfo=UTC)


def test_expected_bar_count_matches_the_dod_number() -> None:
    """P0-8-2 확인 문구 — 1일치 5m 은 **288봉**이다."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=1) - timedelta(minutes=5)
    assert expected_bar_count(start, end, Timeframe.M5) == 288


def test_expected_bar_count_is_zero_for_reversed_range() -> None:
    assert expected_bar_count(BASE, BASE - timedelta(hours=1), Timeframe.M5) == 0


def test_expected_count_excludes_the_bar_straddling_start() -> None:
    """시작이 경계에 안 맞으면 **그 봉은 세지 않는다**.

    조회가 `ts >= start` 라 15:00 봉은 결과에 없다. 내림으로 세면 기대 수가 1 커지고,
    그 1 때문에 재개 판정(`stored >= expected`)이 영원히 미달이 되어 같은 창을 매번
    다시 받는다 — 실제로 1일치 백필에서 `expected 288 / stored 287` 로 드러났다.
    """
    start = datetime(2026, 8, 2, 15, 3, 28, tzinfo=UTC)  # 15:00 봉 도중
    end = datetime(2026, 8, 3, 14, 55, tzinfo=UTC)
    assert expected_bar_count(start, end, Timeframe.M5) == 287


def test_ceil_leaves_aligned_moments_alone() -> None:
    aligned = datetime(2026, 8, 3, 10, 45, tzinfo=UTC)
    assert ceil_to_interval(aligned, Timeframe.M5) == aligned


def test_ceil_advances_unaligned_moments() -> None:
    assert ceil_to_interval(datetime(2026, 8, 3, 10, 46, tzinfo=UTC), Timeframe.M5) == datetime(
        2026, 8, 3, 10, 50, tzinfo=UTC
    )


def test_expected_count_is_zero_when_no_boundary_falls_inside() -> None:
    """봉 경계가 하나도 안 들어가는 좁은 구간은 0 이다."""
    start = datetime(2026, 8, 3, 10, 46, tzinfo=UTC)
    end = datetime(2026, 8, 3, 10, 48, tzinfo=UTC)
    assert expected_bar_count(start, end, Timeframe.M5) == 0


# ---------------------------------------------------------------------------
# 1b. 수집 범위 — 고정 앵커 (D-15)
# ---------------------------------------------------------------------------


def test_shipped_scope_file_loads() -> None:
    """저장소에 든 앵커가 실제로 파싱되고 **불변식을 지켜야** 한다.

    Note:
        ⚠️ **날짜 리터럴을 박지 않는다.** D-15 가 요구하는 것은 "앵커가 고정"이지
        "앵커가 특정 날짜"가 아니다. 리터럴을 박으면 정당한 앵커 이동(과거로 내리는 것은
        명시적으로 허용된다)마다 테스트가 깨지고, 그러면 테스트가 규칙이 아니라 **현재
        값을 지키는** 물건이 된다.

        대신 문서가 실제로 요구하는 성질을 검사한다: UTC aware · **월 경계**
        (candles 월 파티션·일봉 경계와 맞아야 한다).
    """
    scope = BackfillScope.load(Path("config/backfill.yml"))
    anchor = scope.history_anchor
    assert anchor.tzinfo is not None, "앵커는 UTC aware 여야 한다 (절대 규칙 #7)"
    assert (anchor.day, anchor.hour, anchor.minute, anchor.second) == (1, 0, 0, 0), (
        f"앵커가 UTC 월 경계가 아니다: {anchor.isoformat()} — "
        "candles 월 파티션·일봉 경계와 어긋난다 (config/backfill.yml 주석)"
    )
    assert scope.universe, "유니버스가 비면 수집할 대상이 없다"
    # ⚠️ **특정 시간축을 박지 않는다** — 위 docstring 과 같은 이유다. 예전에는 `M5` 를
    #    박아 뒀는데, 5m 단독 진입이 산술 반증으로 폐기되고(§1-0i) 목록에서 빠지자
    #    테스트가 깨졌다. 그것은 규칙 위반이 아니라 **정당한 목록 변경**이었다.
    #
    #    실제 불변식은 둘이다:
    #      ① 측정이 **진입하는** 시간축이 있어야 한다 (없으면 백필이 쓸모없다)
    #      ② 주식의 **거래일 달력 재료**인 1d 가 있어야 한다 (§1-0j)
    assert Timeframe.M15 in scope.timeframes, "측정이 진입하는 시간축이 목록에 없다"
    assert Timeframe.D1 in scope.timeframes, "일봉이 없으면 주식 결측 판정이 성립하지 않는다"


def test_anchor_is_a_fixed_instant_not_a_window() -> None:
    """**앵커는 상수다.** 두 번 읽어도 같아야 한다 — 실행 시점에 의존하면 D-15 위반이다."""
    first = BackfillScope.load(Path("config/backfill.yml"))
    second = BackfillScope.load(Path("config/backfill.yml"))
    assert first.history_anchor == second.history_anchor


def test_anchor_is_utc_month_boundary() -> None:
    """월 경계여야 candles 월 파티션·일봉 경계와 정확히 맞는다."""
    anchor = BackfillScope.load(Path("config/backfill.yml")).history_anchor
    assert (anchor.day, anchor.hour, anchor.minute, anchor.second) == (1, 0, 0, 0)
    assert anchor.tzinfo is not None


def test_anchor_covers_at_least_a_year_before_phase0() -> None:
    """tasks.md DoD 는 "1년치"다 — Phase 0 착수(2026-08) 기준으로 1년 이상이어야 한다."""
    anchor = BackfillScope.load(Path("config/backfill.yml")).history_anchor
    phase0_start = datetime(2026, 8, 1, tzinfo=UTC)
    assert phase0_start - anchor >= timedelta(days=365)


def test_every_configured_symbol_resolves_to_an_instrument() -> None:
    """설정의 심볼을 전부 도메인 객체로 만들 수 있어야 한다.

    유니버스 목록은 `config/backfill.yml` 에만 있고 시드 스크립트는 그것을 읽는다 —
    목록이 두 벌이면 갈라지고, 갈라진 순간 "설정에는 있는데 시드에 없는 종목"을 수집하려
    들어 `InstrumentNotFoundError` 가 난다. 이 테스트는 이름 표(`UPBIT_KRW_NAMES` ·
    `STOCK_SPECS`)에 빠진 심볼을 설정에 넣었을 때 **CI 에서** 잡는다.

    Note:
        ⚠️ 예전에는 "전부 `Market.UPBIT` 여야 한다"를 검사했다. 그것은 유니버스가 코인
        전용이던 시절의 **부수적 사실**이었지 이 테스트가 지키려는 불변식이 아니다.
        방향 전환(P1 §1-0j)으로 7종목 3시장이 되면서 그 단정이 틀렸다.

        지금 지키는 불변식은 **"모든 심볼이 조회 가능한 시장의 종목으로 해석된다"** 이다 —
        해석은 되는데 조회 어댑터가 없는 시장이면 백필이 런타임에 죽는다.
    """
    scope = BackfillScope.load(Path("config/backfill.yml"))
    instruments = resolve_universe(scope.universe)
    assert [inst.symbol for inst in instruments] == list(scope.universe)
    assert all(inst.market in RESOLVABLE_MARKETS for inst in instruments), (
        "조회 어댑터가 없는 시장의 종목이 유니버스에 있다 — 백필이 런타임에 죽는다"
    )
    assert all(inst.name and inst.name != inst.symbol for inst in instruments)
    # 통화·자산군은 시장에서 따라오는 값이라 시장별로 일관되어야 한다.
    for inst in instruments:
        if inst.market is Market.UPBIT:
            assert inst.asset_type is AssetType.COIN
            assert inst.currency is Currency.KRW
        elif inst.market is Market.GATE:
            # ⭐ 코인인데 USD 다 — 정산이 USDT 이고 `Currency` 에 USDT 가 없다 (T23).
            #    라이브 경로(`apps/api/exchange.py`)가 이미 같은 선택을 했다.
            assert inst.asset_type is AssetType.COIN
            assert inst.currency is Currency.USD
        else:
            assert inst.asset_type is AssetType.STOCK
            expected = Currency.KRW if inst.market is Market.KRX else Currency.USD
            assert inst.currency is expected, f"{inst.symbol} 의 통화가 시장과 어긋난다"


#: 조회 어댑터가 존재하는 시장 (`MarketDataProvider.adapter_for`).
#:
#: ⭐ 2026-08-21: `GATE` 추가 (T23). `provider.adapter_for(Market.GATE)` 가 이미
#: 어댑터를 돌려주고 있었고, 유니버스에만 없었다.
RESOLVABLE_MARKETS = frozenset({Market.UPBIT, Market.GATE, Market.KRX, Market.NASDAQ, Market.NYSE})


def test_unknown_symbol_is_rejected_not_guessed() -> None:
    """오타가 그대로 종목명이 되어 DB 에 들어가면 나중에 구분할 수 없다 (spec §7)."""
    with pytest.raises(UnknownSymbolError, match="이름을 모르는"):
        resolve_universe(("KRW-BTV",))


def test_non_krw_market_is_rejected() -> None:
    """BTC 마켓은 정산 통화가 달라 Currency 확장이 먼저 필요하다."""
    with pytest.raises(UnknownSymbolError, match="KRW 마켓만"):
        resolve_universe(("BTC-ETH",))


def test_naive_anchor_is_rejected() -> None:
    """타임존 없는 앵커를 UTC 로 가정하면 KST 로 적었을 때 9시간 어긋난 하한이 된다."""
    with pytest.raises(BackfillScopeError, match="타임존"):
        BackfillScope.from_mapping(
            {
                "history_anchor": "2025-08-01T00:00:00",
                "universe": ["KRW-BTC"],
                "timeframes": ["5m"],
            }
        )


@pytest.mark.parametrize(
    "raw",
    [
        {"universe": ["KRW-BTC"], "timeframes": ["5m"]},  # 앵커 없음
        {"history_anchor": "not-a-date", "universe": ["KRW-BTC"], "timeframes": ["5m"]},
        {"history_anchor": "2025-08-01T00:00:00Z", "universe": [], "timeframes": ["5m"]},
        {"history_anchor": "2025-08-01T00:00:00Z", "universe": ["KRW-BTC"], "timeframes": []},
        {"history_anchor": "2025-08-01T00:00:00Z", "universe": ["KRW-BTC"], "timeframes": ["2m"]},
    ],
)
def test_invalid_scope_is_rejected(raw: dict[str, object]) -> None:
    """앵커를 잘못 읽고 도는 것이 곧 "하한이 조용히 바뀐" 상태다 (spec §7)."""
    with pytest.raises(BackfillScopeError):
        BackfillScope.from_mapping(raw)  # pyright: ignore[reportArgumentType]


def test_missing_scope_file_raises() -> None:
    with pytest.raises(BackfillScopeError, match="읽을 수 없다"):
        BackfillScope.load(Path("config/does_not_exist.yml"))


# ---------------------------------------------------------------------------
# 2. 임계값 설정 (D-9)
# ---------------------------------------------------------------------------


def test_shipped_config_file_loads() -> None:
    """저장소에 든 YAML 이 실제로 파싱돼야 한다 — 문서용 파일이 아니다."""
    thresholds = IntegrityThresholds.load(Path("config/candle_integrity.yml"))
    assert thresholds.price_spike_pct == Decimal("0.30")
    assert thresholds.volume_spike_multiple == Decimal(50)
    assert thresholds.volume_spike_window == 20


def test_thresholds_avoid_binary_float_error() -> None:
    """`Decimal(0.3)` 은 오차를 옮긴다 — `str()` 경유여야 한다."""
    thresholds = IntegrityThresholds.from_mapping({"price_spike_pct": 0.3})
    assert thresholds.price_spike_pct == Decimal("0.3")


@pytest.mark.parametrize(
    "raw",
    [
        {"price_spike_pct": 0},
        {"price_spike_pct": -1},
        {"volume_spike_multiple": 1},
        {"volume_spike_window": 0},
        {"max_issues_per_run": 0},
        {"price_spike_pct": "not-a-number"},
    ],
)
def test_invalid_thresholds_are_rejected(raw: dict[str, object]) -> None:
    """무의미한 임계값으로 검사가 조용히 도는 것을 막는다 (spec §7)."""
    with pytest.raises(IntegrityConfigError):
        IntegrityThresholds.from_mapping(raw)  # pyright: ignore[reportArgumentType]


def test_missing_config_file_raises() -> None:
    with pytest.raises(IntegrityConfigError, match="읽을 수 없다"):
        IntegrityThresholds.load(Path("config/does_not_exist.yml"))


# ---------------------------------------------------------------------------
# 3. OHLC 논리 — 임계값 없는 오류
# ---------------------------------------------------------------------------


def test_valid_candle_has_no_violation() -> None:
    assert check_ohlc_logic(candle(0, open_="100", high="110", low="90", close="105")) is None


_OHLC = QualityIssueType.OHLC_VIOLATION
_NON_POSITIVE = QualityIssueType.NON_POSITIVE_PRICE


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # high < low — 데이터 손상
        ({"open_": "100", "high": "90", "low": "95", "close": "100"}, _OHLC),
        # high < close
        ({"open_": "100", "high": "100", "low": "90", "close": "120"}, _OHLC),
        # low > close
        ({"open_": "100", "high": "110", "low": "105", "close": "100"}, _OHLC),
        ({"open_": "0", "high": "110", "low": "90", "close": "100"}, _NON_POSITIVE),
        ({"open_": "-1", "high": "110", "low": "90", "close": "100"}, _NON_POSITIVE),
    ],
)
def test_logic_violations_are_detected(kwargs: dict[str, str], expected: QualityIssueType) -> None:
    """`high < low` 같은 것은 시장 상황이 아니라 데이터 손상이다 (D-9)."""
    assert check_ohlc_logic(candle(0, **kwargs)) is expected  # pyright: ignore[reportArgumentType]


def test_negative_volume_is_detected() -> None:
    bad = candle(0, open_="100", high="110", low="90", close="100", volume="-1")
    assert check_ohlc_logic(bad) is QualityIssueType.NEGATIVE_VOLUME


def test_non_positive_price_wins_over_ohlc() -> None:
    """가격이 0 이하면 OHLC 비교가 무의미하므로 그쪽이 먼저 보고돼야 한다."""
    bad = candle(0, open_="0", high="0", low="0", close="0")
    assert check_ohlc_logic(bad) is QualityIssueType.NON_POSITIVE_PRICE


# ---------------------------------------------------------------------------
# 4. 결측 구간 (D-14)
# ---------------------------------------------------------------------------


def test_no_gaps_in_contiguous_series() -> None:
    assert find_missing_ranges(series(10), Timeframe.M5) == []


def test_consecutive_missing_bars_collapse_into_one_range() -> None:
    """1년치에 하루 구멍이 나면 288개 이슈가 아니라 **1개 구간**이어야 읽을 수 있다."""
    candles = [candle(0), candle(5), candle(30), candle(35)]  # 10·15·20·25분 결측
    gaps = find_missing_ranges(candles, Timeframe.M5)
    assert gaps == [(BASE + timedelta(minutes=10), BASE + timedelta(minutes=25))]


def test_multiple_gaps_are_separate_ranges() -> None:
    """떨어져 있는 구멍은 합쳐지지 않는다 — 붙어 있는 것만 하나로 묶인다."""
    # 0, 10, 20, 35 → 구멍은 (5), (15), (25~30) 세 개다
    candles = [candle(0), candle(10), candle(20), candle(35)]
    assert find_missing_ranges(candles, Timeframe.M5) == [
        (BASE + timedelta(minutes=5), BASE + timedelta(minutes=5)),
        (BASE + timedelta(minutes=15), BASE + timedelta(minutes=15)),
        (BASE + timedelta(minutes=25), BASE + timedelta(minutes=30)),
    ]


def test_edges_are_not_reported_as_missing() -> None:
    """양 끝을 결측으로 세면 진행 중인 백필 경계가 매번 잡힌다 (D-9 오탐 회피)."""
    report = inspect_candles(series(5), thresholds=IntegrityThresholds())
    assert report.is_clean


def test_missing_range_detail_counts_bars() -> None:
    report = inspect_candles(
        [candle(0), candle(30)], thresholds=IntegrityThresholds(volume_spike_window=100)
    )
    missing = [i for i in report.issues if i.issue_type is QualityIssueType.MISSING_BARS]
    assert len(missing) == 1
    assert missing[0].detail["missing_bars"] == 5


def test_missing_check_can_be_disabled() -> None:
    """주식(P3)은 장 운영시간 캘린더 없이 켜면 매일 밤이 결측으로 잡힌다."""
    thresholds = IntegrityThresholds(check_missing_bars=False, volume_spike_window=100)
    report = inspect_candles([candle(0), candle(30)], thresholds=thresholds)
    assert report.is_clean


# ---------------------------------------------------------------------------
# 5. 스파이크
# ---------------------------------------------------------------------------


def test_price_spike_is_detected() -> None:
    candles = [candle(0, close="100"), candle(5, close="140")]  # +40% > 30%
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    spikes = [i for i in report.issues if i.issue_type is QualityIssueType.PRICE_SPIKE]
    assert len(spikes) == 1
    assert spikes[0].detail["threshold_pct"] == "0.30"


def test_price_move_within_threshold_is_clean() -> None:
    candles = [candle(0, close="100"), candle(5, close="120")]  # +20% < 30%
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    assert report.is_clean


def test_volume_spike_needs_a_full_window() -> None:
    """표본 없이 판정하면 백필 구간의 **첫 봉들이 전부** 스파이크로 잡힌다."""
    candles = series(5, volume="1")
    candles.append(candle(25, volume="10000"))
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=20))
    assert not any(i.issue_type is QualityIssueType.VOLUME_SPIKE for i in report.issues)


def test_volume_spike_is_detected_with_enough_history() -> None:
    candles = series(20, volume="1")
    candles.append(candle(100, volume="100"))  # 평균 1 대비 x100 > x50
    report = inspect_candles(candles, thresholds=IntegrityThresholds(check_missing_bars=False))
    spikes = [i for i in report.issues if i.issue_type is QualityIssueType.VOLUME_SPIKE]
    assert len(spikes) == 1
    assert spikes[0].detail["multiple"] == "100.00"


def test_zero_volume_window_does_not_divide_by_zero() -> None:
    """거래 없는 구간을 스파이크로 잡지 않는다 — 진짜 문제면 결측 검사가 잡는다."""
    candles = series(20, volume="0")
    candles.append(candle(100, volume="5"))
    report = inspect_candles(candles, thresholds=IntegrityThresholds(check_missing_bars=False))
    assert not any(i.issue_type is QualityIssueType.VOLUME_SPIKE for i in report.issues)


# ---------------------------------------------------------------------------
# 6. inspect_candles 계약
# ---------------------------------------------------------------------------


def test_empty_input_is_clean() -> None:
    report = inspect_candles([], thresholds=IntegrityThresholds())
    assert report.is_clean
    assert report.inspected == 0


def test_mixed_instruments_are_rejected() -> None:
    """섞이면 A 종목의 봉을 B 의 직전 봉으로 비교한다 — 조용히 틀리는 대신 거부한다."""
    with pytest.raises(ValueError, match="단일 종목"):
        inspect_candles(
            [candle(0, instrument=BTC), candle(5, instrument=ETH)],
            thresholds=IntegrityThresholds(),
        )


def test_mixed_timeframes_are_rejected() -> None:
    with pytest.raises(ValueError, match="단일 종목"):
        inspect_candles(
            [candle(0, timeframe=Timeframe.M5), candle(5, timeframe=Timeframe.H1)],
            thresholds=IntegrityThresholds(),
        )


def test_unsorted_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="오름차순"):
        inspect_candles([candle(5), candle(0)], thresholds=IntegrityThresholds())


def test_duplicate_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="오름차순"):
        inspect_candles([candle(0), candle(0)], thresholds=IntegrityThresholds())


def test_truncation_is_visible() -> None:
    """잘린 것을 조용히 넘기면 리포트가 "몇 건 없다"로 읽힌다 (spec §7)."""
    candles = [candle(i * 5, close=str(100 * (2 ** (i % 2)))) for i in range(40)]
    thresholds = IntegrityThresholds(max_issues_per_run=5, volume_spike_window=100)
    report = inspect_candles(candles, thresholds=thresholds)
    assert report.truncated
    assert len(report.issues) == 5
    assert report.total_issues > 5


def test_report_is_deterministic() -> None:
    """원칙 P1 — 같은 입력이면 같은 출력이다."""
    candles = [candle(0, close="100"), candle(5, close="200"), candle(30, close="210")]
    first = inspect_candles(candles, thresholds=IntegrityThresholds())
    second = inspect_candles(candles, thresholds=IntegrityThresholds())
    assert first == second


# ---------------------------------------------------------------------------
# 7. 미완성 봉 제외
# ---------------------------------------------------------------------------


def test_forming_bar_is_dropped() -> None:
    """미완성 봉을 저장하면 지표가 그것을 진짜 봉으로 읽는다 (spec §4.2)."""
    now = BASE + timedelta(minutes=12)  # 10분 봉이 형성 중
    kept = drop_unclosed([candle(0), candle(5), candle(10)], now, Timeframe.M5)
    assert [c.ts for c in kept] == [BASE, BASE + timedelta(minutes=5)]


def test_nothing_is_dropped_when_all_closed() -> None:
    now = BASE + timedelta(minutes=30)
    assert len(drop_unclosed(series(4), now, Timeframe.M5)) == 4


# ---------------------------------------------------------------------------
# 8. 백필 — 가짜 어댑터
# ---------------------------------------------------------------------------


class FakeAdapter:
    """구간을 요청받으면 그 구간의 봉을 만들어 주는 가짜 브로커.

    Note:
        실 API 로는 "1년치 중간에 죽였다 재개"를 재현할 수 없다. 호출 기록을 남겨
        **재개가 실제로 요청을 줄이는지**도 확인한다.
    """

    def __init__(self, timeframe: Timeframe = Timeframe.M5, *, gap_at: datetime | None = None):
        self.timeframe = timeframe
        self.gap_at = gap_at
        self.calls: list[tuple[datetime, datetime]] = []

    @property
    def capabilities(self) -> frozenset[object]:
        return frozenset()

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls.append((start, end))
        step = interval(timeframe)
        out: list[Candle] = []
        cursor = floor_to_interval(start, timeframe)
        while cursor <= end:
            if cursor != self.gap_at:
                out.append(
                    Candle(
                        instrument=instrument,
                        timeframe=timeframe,
                        ts=cursor,
                        open=Decimal(100),
                        high=Decimal(110),
                        low=Decimal(90),
                        close=Decimal(105),
                        volume=Decimal(1),
                    )
                )
            cursor += step
        return out


class RecordingRepository:
    """적재 내용을 메모리에 담는 저장소 대역 (DB 없이 백필 로직만 본다)."""

    def __init__(self) -> None:
        self.stored: dict[datetime, Candle] = {}

    async def upsert_candles(self, instrument_id: int, candles: list[Candle]) -> int:  # noqa: ARG002
        for c in candles:
            self.stored[c.ts] = c
        return len(candles)

    async def fetch_candles(
        self,
        instrument: Instrument,  # noqa: ARG002
        instrument_id: int,  # noqa: ARG002
        timeframe: Timeframe,  # noqa: ARG002
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        return sorted(
            (c for ts, c in self.stored.items() if start <= ts <= end), key=lambda c: c.ts
        )


async def _backfill(
    adapter: FakeAdapter,
    repository: RecordingRepository,
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    window_bars: int = 100,
    resume: bool = True,
) -> BackfillResult:
    return await run_backfill(
        adapter,  # pyright: ignore[reportArgumentType]
        repository,  # pyright: ignore[reportArgumentType]
        BTC,
        1,
        Timeframe.M5,
        start,
        end,
        now=now,
        window_bars=window_bars,
        resume=resume,
    )


async def test_backfill_stores_the_expected_count() -> None:
    """P0-8-2 확인 — 1일치 5m 은 288봉이다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    start = BASE
    end = BASE + timedelta(days=1) - timedelta(minutes=5)
    result = await _backfill(
        adapter, repository, start=start, end=end, now=end + timedelta(minutes=10)
    )

    assert result.expected == 288
    assert len(repository.stored) == 288


async def test_backfill_splits_into_windows() -> None:
    """어댑터 페이지 상한(200) 때문에 창 분할이 필수다 — 1년치는 526페이지다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    end = BASE + timedelta(days=1) - timedelta(minutes=5)
    result = await _backfill(
        adapter, repository, start=BASE, end=end, now=end + timedelta(minutes=10), window_bars=100
    )
    assert result.windows == 3  # 288봉 / 100
    assert len(adapter.calls) == 3


async def test_windows_do_not_overlap() -> None:
    """겹치면 같은 봉을 두 번 받아 요청 예산을 낭비한다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    end = BASE + timedelta(days=1)
    await _backfill(adapter, repository, start=BASE, end=end, now=end + timedelta(hours=1))
    for (_, prev_end), (next_start, _) in zip(adapter.calls, adapter.calls[1:], strict=False):
        assert next_start > prev_end, "창이 겹친다"


async def test_rerun_is_idempotent_and_skips_windows() -> None:
    """DoD 3·4 — 두 번 돌려도 중복이 없고, 재개가 요청을 줄인다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    end = BASE + timedelta(days=1) - timedelta(minutes=5)
    now = end + timedelta(minutes=10)

    first = await _backfill(adapter, repository, start=BASE, end=end, now=now)
    calls_after_first = len(adapter.calls)
    stored_after_first = len(repository.stored)

    second = await _backfill(adapter, repository, start=BASE, end=end, now=now)

    assert len(repository.stored) == stored_after_first, "중복 행이 생겼다"
    assert second.skipped_windows == first.windows, "재개가 창을 건너뛰지 않았다"
    assert len(adapter.calls) == calls_after_first, "이미 채워진 창을 다시 요청했다"


async def test_no_resume_refetches_everything() -> None:
    """강제 재수집 경로가 실제로 다시 받아야 한다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    end = BASE + timedelta(hours=2)
    now = end + timedelta(minutes=10)
    await _backfill(adapter, repository, start=BASE, end=end, now=now)
    calls = len(adapter.calls)
    await _backfill(adapter, repository, start=BASE, end=end, now=now, resume=False)
    assert len(adapter.calls) > calls


async def test_partial_progress_survives_and_resumes() -> None:
    """DoD 4 — 중간까지 받은 상태에서 재실행하면 구멍 없이 이어진다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    end = BASE + timedelta(days=1) - timedelta(minutes=5)
    now = end + timedelta(minutes=10)

    # 절반만 먼저 받는다 (프로세스가 중간에 죽은 상태를 흉내낸다)
    half = BASE + timedelta(hours=12) - timedelta(minutes=5)
    await _backfill(adapter, repository, start=BASE, end=half, now=now)
    partial = len(repository.stored)
    assert 0 < partial < 288

    await _backfill(adapter, repository, start=BASE, end=end, now=now)

    stored = sorted(repository.stored)
    assert len(stored) == 288
    gaps = [(a, b) for a, b in pairwise(stored) if b - a != timedelta(minutes=5)]
    assert gaps == [], f"재개 후 구멍이 남았다: {gaps}"


async def test_forming_bar_is_not_stored() -> None:
    """`end` 가 미래여도 마감된 봉만 적재한다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    now = BASE + timedelta(minutes=12)
    result = await _backfill(
        adapter, repository, start=BASE, end=BASE + timedelta(hours=1), now=now
    )
    assert max(repository.stored) == BASE + timedelta(minutes=5)
    assert result.dropped_unclosed == 0, "구간이 이미 잘렸으므로 버릴 봉이 없어야 한다"


async def test_nothing_closed_yet_returns_empty_result() -> None:
    """시작 시각이 아직 마감 전이면 아무것도 하지 않고 그 사실을 알린다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    result = await _backfill(
        adapter, repository, start=BASE, end=BASE, now=BASE + timedelta(minutes=1)
    )
    assert result.windows == 0
    assert result.stored == 0
    assert adapter.calls == []


async def test_broker_gap_shows_up_as_an_integrity_issue() -> None:
    """브로커가 봉을 빼먹으면 결측으로 잡혀야 한다 — 백필과 검사가 이어지는 지점."""
    gap = BASE + timedelta(minutes=30)
    adapter, repository = FakeAdapter(gap_at=gap), RecordingRepository()
    end = BASE + timedelta(hours=2)
    await _backfill(adapter, repository, start=BASE, end=end, now=end + timedelta(minutes=10))

    candles = await repository.fetch_candles(BTC, 1, Timeframe.M5, BASE, end)
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    missing = [i for i in report.issues if i.issue_type is QualityIssueType.MISSING_BARS]
    assert len(missing) == 1
    assert missing[0].ts_start == gap


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (BASE + timedelta(hours=1), BASE),  # 역순
    ],
)
async def test_invalid_range_is_rejected(start: datetime, end: datetime) -> None:
    adapter, repository = FakeAdapter(), RecordingRepository()
    with pytest.raises(ValueError, match="늦다"):
        await _backfill(adapter, repository, start=start, end=end, now=BASE + timedelta(days=1))


async def test_naive_range_is_rejected() -> None:
    adapter, repository = FakeAdapter(), RecordingRepository()
    with pytest.raises(ValueError, match="timezone-aware"):
        await _backfill(
            adapter,
            repository,
            start=datetime(2026, 8, 1),
            end=BASE + timedelta(hours=1),
            now=BASE + timedelta(days=1),
        )


async def test_zero_window_is_rejected() -> None:
    """0 이면 창을 만들지 못해 무한 루프가 된다."""
    adapter, repository = FakeAdapter(), RecordingRepository()
    with pytest.raises(ValueError, match="window_bars"):
        await _backfill(
            adapter,
            repository,
            start=BASE,
            end=BASE + timedelta(hours=1),
            now=BASE + timedelta(days=1),
            window_bars=0,
        )


# ---------------------------------------------------------------------------
# 9. 실제 DB — upsert 멱등성 · 파티션 · 이슈 적재
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.db


@pytest.fixture
async def repository(migrated_test_database: str) -> AsyncIterator[CandleRepository]:
    """마이그레이션된 테스트 DB 의 저장소.

    Note:
        **매 테스트 전에 캔들·이슈를 비운다.** 이것 없이는 테스트가 순서에 의존한다 —
        앞선 테스트가 같은 종목·TF·구간에 봉을 남기면 결측 검사가 "구멍이 메워진" 상태를
        보고 통과해 버린다 (실제로 그렇게 깨졌다). `instruments` 는 지우지 않는다:
        `upsert_instrument` 가 멱등하고, id 가 재사용돼도 무해하다.

        raw 연결이 필요한 테스트는 `migrated_test_database` 를 **함께 요청**한다 (세션
        스코프라 같은 DB 를 가리킨다). 전역에 URL 을 보관하는 우회는 쓰지 않는다.
    """
    engine = create_engine(migrated_test_database)
    async with engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM candle_quality_issues"))
        await connection.execute(sa.text("DELETE FROM candles"))
    yield CandleRepository(create_session_factory(engine))
    await engine.dispose()


@pytest.mark.db
async def test_instrument_upsert_is_idempotent(repository: CandleRepository) -> None:
    """id 는 바뀌면 안 된다 — `candles` 가 수천만 번 참조한다."""
    first = await repository.upsert_instrument(BTC)
    second = await repository.upsert_instrument(BTC)
    assert first == second

    resolved_id, resolved = await repository.resolve_instrument(Market.UPBIT, "KRW-BTC")
    assert resolved_id == first
    assert resolved == BTC


@pytest.mark.db
async def test_unknown_instrument_tells_you_what_to_run(repository: CandleRepository) -> None:
    """ "시드를 안 돌렸다"와 "DB 가 죽었다"는 대응이 다르다."""
    with pytest.raises(InstrumentNotFoundError, match="seed_instruments"):
        await repository.resolve_instrument(Market.UPBIT, "KRW-NOPE")


@pytest.mark.db
async def test_candle_upsert_is_idempotent(repository: CandleRepository) -> None:
    """DoD 3 — 두 번 적재해도 중복 행이 0 이다 (PK 가 보장)."""
    instrument_id = await repository.upsert_instrument(BTC)
    candles = series(10)

    await repository.upsert_candles(instrument_id, candles)
    await repository.upsert_candles(instrument_id, candles)

    stored = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    assert len(stored) == 10


@pytest.mark.db
async def test_upsert_updates_changed_values(repository: CandleRepository) -> None:
    """거래소가 집계를 정정하면 갱신돼야 한다 (DO UPDATE 인 이유)."""
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0, close="100")])
    await repository.upsert_candles(instrument_id, [candle(0, close="123")])

    stored = await repository.fetch_candles(BTC, instrument_id, Timeframe.M5, BASE, BASE)
    assert stored[0].close == Decimal("123")


@pytest.mark.db
async def test_upsert_creates_missing_partitions(repository: CandleRepository) -> None:
    """DEFAULT 파티션이 없으므로 이 단계를 건너뛰면 INSERT 가 실패한다 (plan D-4)."""
    instrument_id = await repository.upsert_instrument(BTC)
    far_future = datetime.now(UTC) + timedelta(days=400)
    future_candle = Candle(
        instrument=BTC,
        timeframe=Timeframe.D1,
        ts=floor_to_interval(far_future, Timeframe.D1),
        open=Decimal(1),
        high=Decimal(1),
        low=Decimal(1),
        close=Decimal(1),
        volume=Decimal(1),
    )
    assert await repository.upsert_candles(instrument_id, [future_candle]) == 1


@pytest.mark.db
async def test_ts_survives_the_round_trip_as_utc(repository: CandleRepository) -> None:
    """저장은 UTC 다 (절대 규칙 #7). naive 로 돌아오면 Candle 생성이 터진다."""
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0)])
    stored = await repository.fetch_candles(BTC, instrument_id, Timeframe.M5, BASE, BASE)
    assert stored[0].ts == BASE
    assert stored[0].ts.tzinfo is not None


@pytest.mark.db
async def test_issues_are_recorded_as_ranges(repository: CandleRepository) -> None:
    """P0-8-5 / D-14 — 결측은 구간으로 기록된다."""
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0), candle(30)])

    candles = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    assert not report.is_clean
    assert await repository.record_issues(instrument_id, report.issues) == len(report.issues)


@pytest.mark.db
async def test_coverage_reports_bounds(repository: CandleRepository) -> None:
    """DoD 1 확인 수단 — 종목 x TF 별 min/max/count."""
    instrument_id = await repository.upsert_instrument(ETH)
    await repository.upsert_candles(instrument_id, series(12, instrument=ETH))

    rows = [
        row
        for row in await repository.coverage()
        if row.symbol == "KRW-ETH" and row.timeframe is Timeframe.M5
    ]
    assert len(rows) == 1
    assert rows[0].bars == 12
    assert rows[0].first_ts == BASE
    assert rows[0].last_ts == BASE + timedelta(minutes=55)


@pytest.mark.db
async def test_last_ts_is_none_when_empty(repository: CandleRepository) -> None:
    """주기 수집이 "어디부터 받을지" 판단하는 기준점 (P0-8-6)."""
    instrument_id = await repository.upsert_instrument(BTC)
    assert await repository.last_ts(instrument_id, Timeframe.H4) is None


# ---------------------------------------------------------------------------
# 10. 주기 수집 (P0-8-6)
# ---------------------------------------------------------------------------

SCOPE = BackfillScope(
    history_anchor=datetime(2025, 8, 1, tzinfo=UTC),
    universe=("KRW-BTC", "KRW-ETH"),
    timeframes=(Timeframe.M5,),
    gap_lookback_bars=10,
    max_catchup_bars=50,
)


def test_shipped_scope_has_collection_params() -> None:
    """저장소 설정에 주기 수집 파라미터가 실제로 있어야 한다."""
    scope = BackfillScope.load(Path("config/backfill.yml"))
    assert scope.gap_lookback_bars >= 1
    assert scope.max_catchup_bars >= scope.gap_lookback_bars


def test_zero_lookback_is_rejected() -> None:
    """0 이면 최신 봉만 받아 **일시 장애 구멍이 영구히 남는다.**"""
    with pytest.raises(BackfillScopeError, match="gap_lookback_bars"):
        BackfillScope.from_mapping(
            {
                "history_anchor": "2025-08-01T00:00:00Z",
                "universe": ["KRW-BTC"],
                "timeframes": ["5m"],
                "gap_lookback_bars": 0,
            }
        )


def test_catchup_smaller_than_lookback_is_rejected() -> None:
    """되돌아볼 구간이 상한에 먼저 잘리면 갭 메움이 동작하지 않는다."""
    with pytest.raises(BackfillScopeError, match="max_catchup_bars"):
        BackfillScope.from_mapping(
            {
                "history_anchor": "2025-08-01T00:00:00Z",
                "universe": ["KRW-BTC"],
                "timeframes": ["5m"],
                "gap_lookback_bars": 60,
                "max_catchup_bars": 10,
            }
        )


def test_window_overlaps_when_up_to_date() -> None:
    """최신 상태여도 **겹쳐 받는다** — 그 겹침이 갭 메움의 수단이다."""
    now = BASE + timedelta(minutes=52)  # 50분 봉이 형성 중 → 마감은 45분
    last = BASE + timedelta(minutes=45)
    start, end, truncated = resolve_window(last, now, Timeframe.M5, SCOPE)

    assert end == BASE + timedelta(minutes=45)
    assert start == end - timedelta(minutes=5 * 9), "되돌아가지 않고 최신 봉만 받았다"
    assert truncated is False


def test_window_catches_up_a_gap() -> None:
    """구멍이 있으면 그만큼 과거로 되돌아간다."""
    now = BASE + timedelta(minutes=102)
    last = BASE + timedelta(minutes=20)  # 한참 전에 멈췄다
    start, end, truncated = resolve_window(last, now, Timeframe.M5, SCOPE)

    assert start < last, "구멍을 덮지 못했다"
    assert end == BASE + timedelta(minutes=95)
    assert truncated is False


def test_window_is_capped_and_flagged() -> None:
    """상한을 넘는 구멍은 잘리고 **잘렸다는 사실을 알린다** (spec §7)."""
    now = BASE + timedelta(days=3)
    last = BASE  # 3일 전
    start, end, truncated = resolve_window(last, now, Timeframe.M5, SCOPE)

    assert truncated is True, "상한을 넘었는데 조용히 일부만 받았다"
    span = int((end - start) / timedelta(minutes=5)) + 1
    assert span == SCOPE.max_catchup_bars


def test_window_without_history_takes_only_lookback() -> None:
    """적재 이력이 없어도 앵커부터 받지 않는다 — 그것은 백필의 일이다."""
    now = BASE + timedelta(days=30)
    start, end, truncated = resolve_window(None, now, Timeframe.M5, SCOPE)

    span = int((end - start) / timedelta(minutes=5)) + 1
    assert span == SCOPE.gap_lookback_bars
    assert truncated is False


def test_window_end_is_the_closed_boundary() -> None:
    """미완성 봉을 구간에 넣지 않는다 (spec §4.2)."""
    now = BASE + timedelta(minutes=52)
    _, end, _ = resolve_window(None, now, Timeframe.M5, SCOPE)
    assert end == last_closed_ts(now, Timeframe.M5)


@pytest.mark.db
async def test_collect_fills_a_gap(repository: CandleRepository) -> None:
    """P0-8-6 핵심 — 구멍이 다음 실행에서 **자동으로 메워진다.**"""
    instrument_id = await repository.upsert_instrument(BTC)
    # 구멍 있는 상태를 만든다: 0~15분만 있고 20~45분이 비었다.
    await repository.upsert_candles(instrument_id, series(4))
    now = BASE + timedelta(minutes=52)

    adapter = FakeAdapter()
    run = await collect_timeframe(
        adapter,  # pyright: ignore[reportArgumentType]
        repository,
        BackfillScope(
            history_anchor=BASE,
            universe=("KRW-BTC",),
            timeframes=(Timeframe.M5,),
            gap_lookback_bars=10,
            max_catchup_bars=50,
        ),
        IntegrityThresholds(volume_spike_window=100),
        Timeframe.M5,
        now=now,
    )

    assert run.failures == ()
    stored = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(minutes=45)
    )
    timestamps = [candle.ts for candle in stored]
    gaps = [(a, b) for a, b in pairwise(timestamps) if b - a != timedelta(minutes=5)]
    assert gaps == [], f"수집 후에도 구멍이 남았다: {gaps}"
    assert timestamps[-1] == BASE + timedelta(minutes=45), "마감 봉까지 받지 못했다"


@pytest.mark.db
async def test_collect_does_not_store_the_forming_bar(repository: CandleRepository) -> None:
    """봉마감 직후에 돌아도 형성 중인 봉은 저장하지 않는다."""
    await repository.upsert_instrument(BTC)
    now = BASE + timedelta(minutes=52)  # 50분 봉이 형성 중

    run = await collect_timeframe(
        FakeAdapter(),  # pyright: ignore[reportArgumentType]
        repository,
        BackfillScope(
            history_anchor=BASE,
            universe=("KRW-BTC",),
            timeframes=(Timeframe.M5,),
            gap_lookback_bars=10,
            max_catchup_bars=50,
        ),
        IntegrityThresholds(volume_spike_window=100),
        Timeframe.M5,
        now=now,
    )
    assert run.results[0].window_end == BASE + timedelta(minutes=45)

    instrument_id, _ = await repository.resolve_instrument(Market.UPBIT, "KRW-BTC")
    assert await repository.last_ts(instrument_id, Timeframe.M5) == BASE + timedelta(minutes=45)


@pytest.mark.db
async def test_one_symbol_failure_does_not_stop_the_rest(repository: CandleRepository) -> None:
    """한 종목이 실패했다고 나머지를 건너뛸 이유가 없다 — 다음 주기까지 아무것도 안 들어온다.

    Note:
        시드되지 **않은** 심볼로 실패를 만든다. `repository` 픽스처는 캔들만 비우고
        `instruments` 는 남기므로(멱등하고 무해하다), BTC·ETH 로는 "시드 안 됨"을 재현할 수
        없다 — 앞선 테스트가 이미 넣어 뒀다.
    """
    await repository.upsert_instrument(ETH)
    now = BASE + timedelta(minutes=52)

    run = await collect_timeframe(
        FakeAdapter(),  # pyright: ignore[reportArgumentType]
        repository,
        BackfillScope(
            history_anchor=BASE,
            universe=("KRW-NEVER-SEEDED", "KRW-ETH"),
            timeframes=(Timeframe.M5,),
            gap_lookback_bars=10,
            max_catchup_bars=50,
        ),
        IntegrityThresholds(volume_spike_window=100),
        Timeframe.M5,
        now=now,
    )

    assert [symbol for symbol, _ in run.failures] == ["KRW-NEVER-SEEDED"]
    assert "seed_instruments" in run.failures[0][1], "무엇을 실행하라는지 알려야 한다"
    assert [result.symbol for result in run.results] == ["KRW-ETH"]
    assert run.stored > 0, "실패한 종목 때문에 성공한 종목도 적재되지 않았다"


@pytest.mark.db
async def test_repeated_collection_does_not_pile_up_issues(
    repository: CandleRepository,
) -> None:
    """**메울 수 없는 구멍 하나가 하루 288행을 만들지 않아야 한다.**

    거래소 자체에 없는 봉(§7.1 ETH 1h 3봉)이 실제로 그런 구멍이다. 5분마다 같은 구간을
    검사하므로 중복 억제가 없으면 이슈 테이블이 노이즈로 덮인다.
    """
    instrument_id = await repository.upsert_instrument(BTC)
    gap = BASE + timedelta(minutes=30)
    adapter = FakeAdapter(gap_at=gap)
    scope = BackfillScope(
        history_anchor=BASE,
        universe=("KRW-BTC",),
        timeframes=(Timeframe.M5,),
        gap_lookback_bars=20,
        max_catchup_bars=50,
    )
    now = BASE + timedelta(minutes=52)
    thresholds = IntegrityThresholds(volume_spike_window=100)

    first = await collect_timeframe(adapter, repository, scope, thresholds, Timeframe.M5, now=now)  # pyright: ignore[reportArgumentType]
    second = await collect_timeframe(adapter, repository, scope, thresholds, Timeframe.M5, now=now)  # pyright: ignore[reportArgumentType]

    assert first.results[0].issues_recorded >= 1, "구멍을 잡지 못했다"
    assert second.results[0].issues_recorded == 0, "같은 구간을 두 번 적재했다"

    keys = await repository.open_issue_keys(instrument_id, Timeframe.M5)
    assert len(keys) == first.results[0].issues_recorded


@pytest.mark.db
async def test_manual_check_still_records_duplicates(repository: CandleRepository) -> None:
    """수동 검사(CLI)는 억제하지 않는다 — 임계값 조정 근거로 여러 번 남는 편이 유용하다."""
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0), candle(30)])
    candles = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))

    first = await repository.record_issues(instrument_id, report.issues)
    second = await repository.record_issues(instrument_id, report.issues)
    assert first == second == len(report.issues)


# ---------------------------------------------------------------------------
# 11. 이슈 상태 전이 — resolved / ignored (P0-8-7)
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_open_issues_are_listed_with_instrument(repository: CandleRepository) -> None:
    """판정 CLI 가 종목·구간을 알아야 재수집할 수 있다."""
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0), candle(30)])
    candles = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    await repository.record_issues(instrument_id, report.issues)

    issues = await repository.list_open_issues(timeframe=Timeframe.M5)
    mine = [i for i in issues if i.instrument.symbol == "KRW-BTC"]
    assert mine, "열린 이슈를 못 읽었다"
    assert mine[0].instrument == BTC
    assert mine[0].issue_type is QualityIssueType.MISSING_BARS
    assert mine[0].span_label


@pytest.mark.db
async def test_list_filters_by_type(repository: CandleRepository) -> None:
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0), candle(30)])
    candles = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    await repository.record_issues(instrument_id, report.issues)

    missing = await repository.list_open_issues(issue_type=QualityIssueType.MISSING_BARS)
    spikes = await repository.list_open_issues(issue_type=QualityIssueType.VOLUME_SPIKE)
    assert all(i.issue_type is QualityIssueType.MISSING_BARS for i in missing)
    assert all(i.issue_type is QualityIssueType.VOLUME_SPIKE for i in spikes)


@pytest.mark.db
async def test_ignored_issue_leaves_the_open_set(repository: CandleRepository) -> None:
    """**차단 승격의 핵심** — ignored 는 `status='open'` 조회에서 빠진다 (D-14).

    거래소에 원래 없는 데이터가 해당 구간 분석을 영구 차단하지 않게 하는 장치다.
    """
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0), candle(30)])
    candles = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    await repository.record_issues(instrument_id, report.issues)

    target = (await repository.list_open_issues(issue_type=QualityIssueType.MISSING_BARS))[0]
    changed = await repository.close_issue(
        target.issue_id,
        QualityIssueStatus.IGNORED,
        {"verdict": "ignored", "reason": "거래소 부재"},
    )

    assert changed is True
    remaining = await repository.list_open_issues(issue_type=QualityIssueType.MISSING_BARS)
    assert target.issue_id not in {i.issue_id for i in remaining}
    # 억제 키에서도 빠져야 한다 — 남아 있으면 주기 수집이 같은 구간을 "이미 열려 있다"고
    # 보고 건너뛰어, 나중에 진짜 문제가 생겨도 새 이슈가 쌓이지 않는다.
    keys = await repository.open_issue_keys(instrument_id, Timeframe.M5)
    assert (target.ts_start, target.ts_end, target.issue_type.value) not in keys


@pytest.mark.db
async def test_closing_preserves_the_original_reason(
    repository: CandleRepository, migrated_test_database: str
) -> None:
    """근거를 **덮어쓰지 않고 병합**한다 — 원래 몇 봉 비었는지를 잃으면 감사가 안 된다."""
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0), candle(30)])
    candles = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    await repository.record_issues(instrument_id, report.issues)

    target = (await repository.list_open_issues(issue_type=QualityIssueType.MISSING_BARS))[0]
    original_bars = target.detail["missing_bars"]
    await repository.close_issue(
        target.issue_id,
        QualityIssueStatus.IGNORED,
        {"verdict": "ignored", "reason": "거래소 부재"},
    )

    engine = create_engine(migrated_test_database)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT detail_json, status, resolved_at FROM candle_quality_issues"
                    " WHERE id = :i"
                ),
                {"i": target.issue_id},
            )
        ).one()
    await engine.dispose()

    assert row.status == "ignored"
    assert row.resolved_at is not None
    assert row.detail_json["missing_bars"] == original_bars, "원래 근거가 사라졌다"
    assert row.detail_json["verdict"] == "ignored", "판정 근거가 안 남았다"


@pytest.mark.db
async def test_closing_twice_is_a_no_op(repository: CandleRepository) -> None:
    """이미 닫힌 이슈를 다시 닫으면 False — 판정을 덮어쓰지 않는다."""
    instrument_id = await repository.upsert_instrument(BTC)
    await repository.upsert_candles(instrument_id, [candle(0), candle(30)])
    candles = await repository.fetch_candles(
        BTC, instrument_id, Timeframe.M5, BASE, BASE + timedelta(hours=1)
    )
    report = inspect_candles(candles, thresholds=IntegrityThresholds(volume_spike_window=100))
    await repository.record_issues(instrument_id, report.issues)
    target = (await repository.list_open_issues(issue_type=QualityIssueType.MISSING_BARS))[0]

    evidence = {"verdict": "ignored", "reason": "거래소 부재"}
    assert await repository.close_issue(target.issue_id, QualityIssueStatus.IGNORED, evidence)
    assert not await repository.close_issue(
        target.issue_id, QualityIssueStatus.RESOLVED, {"verdict": "resolved"}
    )


@pytest.mark.db
async def test_cannot_reopen_an_issue(repository: CandleRepository) -> None:
    """`close_issue` 는 닫는 전이만 한다 — OPEN 으로 되돌리는 경로를 만들지 않는다."""
    with pytest.raises(ValueError, match="되돌릴 수 없다"):
        await repository.close_issue(uuid.uuid4(), QualityIssueStatus.OPEN, {})
