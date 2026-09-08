"""지표 순수 로직 + 계약 검증 (P1-2 · P1-2 DoD 2·3).

정답지 대조(`test_indicators_vs_pandas_ta.py`)는 **값이 맞는지**를 본다. 이 파일은
**계약이 지켜지는지**를 본다 — 길이 정렬, 워밍업 경계, `None` 의 의미, 그리고
pandas-ta 가 런타임에 새어 들어오지 않는지.
"""

import tomllib
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

from fixture_loader import load_fixture
from updown.analysis.indicators import atr as atr_module
from updown.analysis.indicators import series as series_module
from updown.analysis.indicators import snapshot
from updown.analysis.indicators.ma import (
    CROSS_PAIRS,
    STANDARD_PERIODS,
    CrossKind,
    MaAlignment,
    alignment,
    ema,
    find_crosses,
    sma,
    sma_naive,
)
from updown.analysis.indicators.rsi import DivergenceKind, find_divergences, rsi
from updown.analysis.indicators.series import SeriesError, from_candles, wilder_average
from updown.analysis.indicators.volume import volume_ratio
from updown.analysis.structures.swing import SwingKind, SwingPoint, prior_swings
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

_INSTRUMENT = Instrument(
    market=Market.UPBIT,
    symbol="KRW-TEST",
    name="테스트",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
_ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)


def make_candles(
    closes: list[int],
    timeframe: Timeframe = Timeframe.H1,
    volumes: list[int] | None = None,
) -> list[Candle]:
    """종가만 지정한 캔들 — 고가·저가는 ±1 로 둔다."""
    step = timedelta(hours=1) if timeframe is Timeframe.H1 else timedelta(minutes=5)
    return [
        Candle(
            instrument=_INSTRUMENT,
            timeframe=timeframe,
            ts=_ORIGIN + step * index,
            open=Decimal(close),
            high=Decimal(close + 1),
            low=Decimal(close - 1),
            close=Decimal(close),
            volume=Decimal(1 if volumes is None else volumes[index]),
        )
        for index, close in enumerate(closes)
    ]


class TestRuntimeDependencyIsolation:
    """P1-2 DoD 2 — pandas-ta 가 런타임 의존성에 없다."""

    @staticmethod
    def _requirements(*path: str) -> list[str]:
        """`pyproject.toml` 의 의존성 목록을 문자열 리스트로 읽는다.

        Args:
            path: 중첩 키 경로 (예: `"project", "dependencies"`).

        Returns:
            소문자 요구사항 문자열들.
        """
        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            node: object = tomllib.load(handle)
        for key in path:
            assert isinstance(node, dict), f"{key} 상위가 매핑이 아니다"
            node = cast(dict[str, object], node)[key]
        assert isinstance(node, list), f"{path} 가 리스트가 아니다"
        return [str(item).lower() for item in cast(list[object], node)]

    def test_reference_libraries_are_dev_only(self) -> None:
        """pandas-ta·pandas·numpy 는 `[project].dependencies` 에 없다."""
        names = " ".join(self._requirements("project", "dependencies"))
        for forbidden in ("pandas-ta", "pandas", "numpy"):
            assert forbidden not in names, (
                f"{forbidden} 가 런타임 의존성에 있다 — 지표 코어는 Decimal + 순수 "
                f"파이썬이며 정답지는 dev 에만 있어야 한다 (P1-2 DoD 2)"
            )

    def test_reference_library_is_declared_in_dev(self) -> None:
        """대조용 pandas-ta 는 dev 그룹에 **있어야** 한다 — 없으면 대조가 안 돈다."""
        dev = self._requirements("dependency-groups", "dev")
        assert any("pandas-ta" in item for item in dev)

    def test_source_tree_does_not_import_pandas_or_numpy(self) -> None:
        """`src/` 어디서도 pandas·numpy 를 import 하지 않는다.

        Note:
            pyproject 만 검사하면 전이 의존으로 깔린 것을 import 하는 경로를 못 잡는다.
            소스를 직접 훑는다 (P0-5 게이트 가드와 같은 발상).
        """
        offenders: list[str] = []
        for path in (REPO_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("import pandas", "import numpy")) or stripped.startswith(
                    ("from pandas", "from numpy")
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}")
        assert not offenders, f"런타임 코드가 pandas/numpy 를 import 한다: {offenders}"


class TestSeriesContract:
    """`series` 모듈의 계약 (입력 검증)."""

    def test_empty_input_is_refused(self) -> None:
        """빈 입력에 빈 결과를 돌려주지 않는다 — 계산 불가는 오류다."""
        with pytest.raises(SeriesError, match="빈 캔들"):
            from_candles([])

    def test_mixed_timeframe_is_refused(self) -> None:
        """시간축이 섞이면 지표가 의미를 잃는다."""
        candles = make_candles([10, 11, 12])
        broken = [
            *candles[:2],
            Candle(
                instrument=_INSTRUMENT,
                timeframe=Timeframe.M5,
                ts=candles[2].ts,
                open=Decimal(12),
                high=Decimal(13),
                low=Decimal(11),
                close=Decimal(12),
                volume=Decimal(1),
            ),
        ]
        with pytest.raises(SeriesError, match="시간축이 섞였다"):
            from_candles(broken)

    def test_unsorted_input_is_refused(self) -> None:
        """조용히 정렬하지 않는다 — 순서가 틀린 지표는 그럴듯해서 더 위험하다."""
        candles = make_candles([10, 11, 12])
        with pytest.raises(SeriesError, match="오름차순"):
            from_candles([candles[0], candles[2], candles[1]])

    def test_missing_bars_are_not_an_error(self) -> None:
        """결측은 오류가 아니다 — 거래소 점검으로 실제로 생긴다."""
        fixture = load_fixture("btc_5m_exchange_gap")
        assert len(from_candles(fixture.candles)) == len(fixture.candles)

    def test_none_in_the_middle_of_a_series_is_refused(self) -> None:
        """중간 `None` 은 결측이 아니라 계산 버그다."""
        with pytest.raises(SeriesError, match="계산 버그"):
            wilder_average([Decimal(1), None, Decimal(2), Decimal(3)], 2)


class TestWarmupBoundaries:
    """P1-2 DoD 3 — 워밍업 구간에서 `None` 이다. 0 이 아니다."""

    def test_output_length_always_matches_input(self) -> None:
        """길이가 어긋나면 봉 번호 좌표계가 깨진다 (`series` 계약 1번)."""
        closes = list(range(100, 160))
        candles = make_candles(closes)
        prices = [candle.close for candle in candles]
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]
        volumes = [candle.volume for candle in candles]
        for produced in (
            sma(prices, 20),
            ema(prices, 20),
            atr_module.atr(highs, lows, prices),
            rsi(prices),
            volume_ratio(volumes),
        ):
            assert len(produced) == len(closes)

    def test_sma_first_value_is_at_period_minus_one(self) -> None:
        """SMA(20) 의 첫 값은 index 19 다."""
        prices = [Decimal(x) for x in range(100, 160)]
        produced = sma(prices, 20)
        assert produced[18] is None
        assert produced[19] is not None

    def test_ema_first_value_is_at_period_minus_one(self) -> None:
        """EMA(20) 도 단순평균 시드라 index 19 다."""
        prices = [Decimal(x) for x in range(100, 160)]
        produced = ema(prices, 20)
        assert produced[18] is None
        assert produced[19] is not None

    def test_atr_first_value_is_at_period_not_period_minus_one(self) -> None:
        """ATR(14) 의 첫 값은 index **14** 다 — `TR[0]` 이 없어 한 칸 밀린다.

        Note:
            13 이 아니라 14 인 것이 핵심이다. 한 칸 차이가 손절폭을 어긋나게 한다.
        """
        candles = make_candles(list(range(100, 140)))
        produced = atr_module.atr(
            [c.high for c in candles], [c.low for c in candles], [c.close for c in candles]
        )
        assert produced[13] is None, "TR[0] 이 없으므로 index 13 에는 값이 없어야 한다"
        assert produced[14] is not None

    def test_true_range_first_bar_is_none(self) -> None:
        """TR 은 직전 종가를 쓰므로 첫 봉에 정의되지 않는다."""
        candles = make_candles([10, 11, 12])
        produced = atr_module.true_range(
            [c.high for c in candles], [c.low for c in candles], [c.close for c in candles]
        )
        assert produced[0] is None
        assert produced[1] is not None

    def test_rsi_first_value_is_at_period(self) -> None:
        """RSI(14) 의 첫 값도 index 14 다 — 변화량이 직전 종가를 쓴다."""
        prices = [Decimal(x) for x in range(100, 140)]
        produced = rsi(prices)
        assert produced[13] is None
        assert produced[14] is not None

    def test_volume_ratio_first_value_is_at_period(self) -> None:
        """거래량 배수는 **직전** 20봉이 필요하므로 index 20 이다 (SMA 와 한 칸 다르다)."""
        volumes = [Decimal(100) for _ in range(30)]
        produced = volume_ratio(volumes)
        assert produced[19] is None
        assert produced[20] is not None

    def test_insufficient_data_yields_all_none_not_zero(self) -> None:
        """데이터가 기간보다 짧으면 전부 `None` 이다 — 0 을 반환하지 않는다."""
        prices = [Decimal(x) for x in range(100, 110)]
        assert sma(prices, 20) == [None] * 10
        assert ema(prices, 20) == [None] * 10


class TestIndicatorValues:
    """값 자체의 성질 (정답지 대조와 별개로 규칙을 확인한다)."""

    def test_sma_of_constant_series_equals_the_constant(self) -> None:
        """상수 시리즈의 평균은 그 상수다 — Decimal 이라 정확히 같다."""
        prices = [Decimal("123.456")] * 30
        assert sma(prices, 20)[25] == Decimal("123.456")

    @pytest.mark.parametrize("period", STANDARD_PERIODS)
    def test_rolling_sum_sma_equals_recomputed_sma(self, period: int) -> None:
        """롤링 합 SMA 가 매번 다시 더한 것과 **정확히** 같다.

        Note:
            부동소수 롤링 합이면 누적 오차로 어긋난다. Decimal 덧셈이 무손실이라
            같은 값이 나오는데, 그 가정에 기대지 않고 실데이터로 확인한다.
        """
        for name in ("btc_1h_uptrend", "btc_5m_range", "btc_5m_exchange_gap"):
            prices = [candle.close for candle in load_fixture(name).candles]
            assert sma(prices, period) == sma_naive(prices, period), (
                f"{name} SMA{period}: 롤링 합과 재계산 결과가 다르다"
            )

    def test_rsi_is_one_hundred_when_there_is_no_decline(self) -> None:
        """하락이 전혀 없으면 RSI 는 100 이다 — 0으로 나누지 않는다."""
        prices = [Decimal(100 + x) for x in range(30)]
        assert rsi(prices)[29] == 100.0

    def test_rsi_stays_within_bounds(self) -> None:
        """실데이터에서 RSI 가 0~100 을 벗어나지 않는다."""
        fixture = load_fixture("btc_5m_range")
        values = rsi([candle.close for candle in fixture.candles])
        assert all(0.0 <= value <= 100.0 for value in values if value is not None)

    def test_volume_ratio_of_flat_volume_is_one(self) -> None:
        """거래량이 일정하면 배수는 1 이다."""
        volumes = [Decimal(500)] * 30
        assert volume_ratio(volumes)[25] == pytest.approx(1.0)

    def test_volume_ratio_is_none_when_baseline_is_zero(self) -> None:
        """직전 구간 거래량이 전부 0 이면 배수가 정의되지 않는다."""
        volumes = [Decimal(0)] * 25 + [Decimal(100)] * 5
        assert volume_ratio(volumes)[24] is None

    def test_atr_is_never_negative(self) -> None:
        """ATR 은 거리의 평균이므로 음수가 될 수 없다."""
        fixture = load_fixture("btc_1h_downtrend")
        values = atr_module.atr(
            [c.high for c in fixture.candles],
            [c.low for c in fixture.candles],
            [c.close for c in fixture.candles],
        )
        assert all(value > 0 for value in values if value is not None)

    def test_results_are_independent_of_ambient_decimal_context(self) -> None:
        """주변 Decimal 컨텍스트가 결과를 바꾸지 않는다 (결정론 — 원칙 P1)."""
        fixture = load_fixture("btc_1h_uptrend")
        closes = [candle.close for candle in fixture.candles]
        baseline = ema(closes, 20)
        with localcontext() as context:
            context.prec = 6
            narrow = ema(closes, 20)
        assert baseline == narrow


class TestAlignmentAndCrosses:
    """정배열/역배열 + 골든/데드크로스 (spec §6.1)."""

    def test_bullish_alignment(self) -> None:
        """단기가 위, 장기가 아래로 완전 정렬이면 정배열이다."""
        assert alignment([Decimal(4), Decimal(3), Decimal(2), Decimal(1)]) is MaAlignment.BULLISH

    def test_bearish_alignment(self) -> None:
        assert alignment([Decimal(1), Decimal(2), Decimal(3), Decimal(4)]) is MaAlignment.BEARISH

    def test_mixed_alignment(self) -> None:
        assert alignment([Decimal(4), Decimal(1), Decimal(3), Decimal(2)]) is MaAlignment.MIXED

    def test_missing_value_yields_none_not_mixed(self) -> None:
        """워밍업 부족을 MIXED 로 보고하면 "보류"와 "실제 혼재"를 구분할 수 없다."""
        assert alignment([Decimal(4), None, Decimal(2), Decimal(1)]) is None

    def test_golden_cross_is_detected_on_the_completing_bar(self) -> None:
        """돌파가 완성된 봉에 크로스를 기록한다."""
        fast = [Decimal(1), Decimal(2), Decimal(4)]
        slow = [Decimal(3), Decimal(3), Decimal(3)]
        ts = [_ORIGIN + timedelta(hours=i) for i in range(3)]
        crosses = find_crosses(fast, slow, ts, 20, 60)
        assert [(c.index, c.kind) for c in crosses] == [(2, CrossKind.GOLDEN)]

    def test_dead_cross_is_detected(self) -> None:
        fast = [Decimal(4), Decimal(4), Decimal(1)]
        slow = [Decimal(3), Decimal(3), Decimal(3)]
        ts = [_ORIGIN + timedelta(hours=i) for i in range(3)]
        crosses = find_crosses(fast, slow, ts, 20, 60)
        assert [(c.index, c.kind) for c in crosses] == [(2, CrossKind.DEAD)]

    def test_touching_without_crossing_is_not_a_cross(self) -> None:
        """아래에서 올라와 **닿기만 하고** 내려가면 크로스가 아니다.

        Note:
            직전 봉만 비교하는 구현은 이것을 데드크로스로 보고한다 — 위로 간 적이
            없는데도. P1-2 개발 중 실제로 그렇게 구현됐고 이 테스트가 잡았다.
        """
        fast = [Decimal(1), Decimal(3), Decimal(1)]
        slow = [Decimal(3), Decimal(3), Decimal(3)]
        ts = [_ORIGIN + timedelta(hours=i) for i in range(3)]
        assert find_crosses(fast, slow, ts, 20, 60) == []

    def test_touching_then_breaking_up_is_a_golden_cross(self) -> None:
        """닿은 뒤 실제로 뚫으면 골든이다 — 억제가 아니라 부호 추적이기 때문이다."""
        fast = [Decimal(1), Decimal(3), Decimal(4)]
        slow = [Decimal(3), Decimal(3), Decimal(3)]
        ts = [_ORIGIN + timedelta(hours=i) for i in range(3)]
        crosses = find_crosses(fast, slow, ts, 20, 60)
        assert [(c.index, c.kind) for c in crosses] == [(2, CrossKind.GOLDEN)]

    def test_crosses_alternate(self) -> None:
        """골든 다음은 반드시 데드다 — 부호가 뒤집힐 때만 기록하므로 구조적으로 보장된다."""
        fixture = load_fixture("btc_5m_range")
        computed = snapshot.compute(fixture.candles)
        for pair in CROSS_PAIRS:
            kinds = [
                cross.kind
                for cross in computed.crosses
                if (cross.fast_period, cross.slow_period) == pair
            ]
            assert all(a is not b for a, b in pairwise(kinds)), (
                f"{pair} 크로스가 연달아 같은 종류로 나왔다: {kinds}"
            )

    def test_warmup_is_not_a_cross(self) -> None:
        """장기선 워밍업이 끝나기 전의 "크로스"는 존재하지 않는 비교다."""
        fast = [Decimal(1), Decimal(4)]
        slow = [None, Decimal(3)]
        ts = [_ORIGIN, _ORIGIN + timedelta(hours=1)]
        assert find_crosses(fast, slow, ts, 20, 60) == []

    def test_mismatched_lengths_are_refused(self) -> None:
        """길이가 어긋나면 인덱스가 봉과 대응하지 않는다."""
        with pytest.raises(SeriesError, match="길이가 다르다"):
            find_crosses([Decimal(1)], [Decimal(1), Decimal(2)], [_ORIGIN], 20, 60)

    def test_confirmed_requires_a_known_volume_ratio(self) -> None:
        """배수를 모르는 것을 "거래량 동반"으로 취급하지 않는다."""
        crosses = find_crosses(
            [Decimal(1), Decimal(4)],
            [Decimal(3), Decimal(3)],
            [_ORIGIN, _ORIGIN + timedelta(hours=1)],
            20,
            60,
        )
        assert crosses[0].volume_ratio is None
        assert crosses[0].confirmed(1.5) is False

    def test_confirmed_compares_against_the_injected_threshold(self) -> None:
        """임계 배수는 인자로 받는다 — 코드에 박지 않는다 (spec §5.6.2)."""
        crosses = find_crosses(
            [Decimal(1), Decimal(4)],
            [Decimal(3), Decimal(3)],
            [_ORIGIN, _ORIGIN + timedelta(hours=1)],
            20,
            60,
            [None, 2.0],
        )
        assert crosses[0].confirmed(1.5) is True
        assert crosses[0].confirmed(2.5) is False


class TestDivergence:
    """RSI 다이버전스 (spec §6.1)."""

    @staticmethod
    def _swing(index: int, price: str, kind: SwingKind) -> SwingPoint:
        return SwingPoint(
            index=index,
            ts=_ORIGIN + timedelta(hours=index),
            price=Decimal(price),
            kind=kind,
        )

    def test_bullish_divergence_needs_lower_price_and_higher_rsi(self) -> None:
        """가격 신저점 + RSI 저점 상승."""
        swings = [self._swing(10, "100", SwingKind.LOW), self._swing(20, "90", SwingKind.LOW)]
        rsi_values: list[float | None] = [None] * 30
        rsi_values[10], rsi_values[20] = 25.0, 35.0
        found = find_divergences(swings, rsi_values)
        assert [item.kind for item in found] == [DivergenceKind.BULLISH]

    def test_lower_price_with_lower_rsi_is_not_divergence(self) -> None:
        """둘이 같이 내려가면 다이버전스가 아니다 — 정상적인 하락이다."""
        swings = [self._swing(10, "100", SwingKind.LOW), self._swing(20, "90", SwingKind.LOW)]
        rsi_values: list[float | None] = [None] * 30
        rsi_values[10], rsi_values[20] = 35.0, 25.0
        assert find_divergences(swings, rsi_values) == []

    def test_bearish_divergence_is_detected_but_is_not_an_entry_signal(self) -> None:
        """하락 다이버전스도 탐지한다 — 청산·회피 근거다 (절대 규칙 #10)."""
        swings = [self._swing(10, "100", SwingKind.HIGH), self._swing(20, "110", SwingKind.HIGH)]
        rsi_values: list[float | None] = [None] * 30
        rsi_values[10], rsi_values[20] = 75.0, 65.0
        found = find_divergences(swings, rsi_values)
        assert [item.kind for item in found] == [DivergenceKind.BEARISH]

    def test_swings_in_the_warmup_window_are_skipped(self) -> None:
        """RSI 가 없는 스윙은 비교 대상이 없다."""
        swings = [self._swing(5, "100", SwingKind.LOW), self._swing(20, "90", SwingKind.LOW)]
        rsi_values: list[float | None] = [None] * 30
        rsi_values[20] = 35.0
        assert find_divergences(swings, rsi_values) == []

    def test_out_of_range_swing_index_is_refused(self) -> None:
        """좌표계가 어긋나면 엉뚱한 봉의 RSI 를 비교한다."""
        with pytest.raises(SeriesError, match="좌표계가 어긋났다"):
            find_divergences([self._swing(99, "100", SwingKind.LOW)], [None] * 10)

    def test_real_data_divergences_use_prior_swings(self) -> None:
        """실데이터에서 다이버전스가 나오고, 전부 교대 정리된 스윙에서 나온다."""
        fixture = load_fixture("btc_5m_range")
        values = rsi([candle.close for candle in fixture.candles])
        swings = prior_swings(fixture.candles, fixture.timeframe)
        found = find_divergences(swings, values)
        assert found, "횡보 구간에서는 다이버전스가 하나 이상 나와야 한다"
        indices = {swing.index for swing in swings}
        for item in found:
            assert item.first_index in indices
            assert item.second_index in indices
            assert item.first_index < item.second_index


class TestSnapshot:
    """시리즈 일괄 계산 + `Indicators` 조립."""

    def test_indicators_snapshot_fills_phase_one_fields(self) -> None:
        """Phase 1 범위 지표는 채우고, §6.2 항목은 None 이다."""
        fixture = load_fixture("btc_1h_uptrend")
        computed = snapshot.compute(fixture.candles)
        last = computed.at(-1)
        assert last.ma20 is not None
        assert last.ma200 is not None
        assert last.ema20 is not None
        assert last.rsi14 is not None
        assert last.atr14 is not None
        assert last.volume_ratio is not None
        assert last.macd is None, "MACD 는 spec §6.2 (Phase 2) 다"
        assert last.bb is None
        assert last.vwap is None

    def test_series_lengths_match_the_candle_count(self) -> None:
        """모든 시리즈가 봉 수와 같은 길이다."""
        fixture = load_fixture("btc_5m_downtrend")
        computed = snapshot.compute(fixture.candles)
        assert computed.length == len(fixture.candles)
        for period in STANDARD_PERIODS:
            assert len(computed.sma[period]) == computed.length
            assert len(computed.ema[period]) == computed.length
        assert len(computed.atr14) == computed.length
        assert len(computed.rsi14) == computed.length
        assert len(computed.volume_ratio) == computed.length

    def test_out_of_range_index_is_refused(self) -> None:
        """조용히 마지막 값을 돌려주지 않는다."""
        fixture = load_fixture("btc_1h_uptrend")
        computed = snapshot.compute(fixture.candles)
        with pytest.raises(SeriesError, match="범위"):
            computed.at(computed.length)

    def test_crosses_until_hides_the_future(self) -> None:
        """미래의 크로스가 보이면 백테스트가 미래를 참조한다 (spec §4.11)."""
        fixture = load_fixture("btc_5m_range")
        computed = snapshot.compute(fixture.candles)
        assert computed.crosses, "이 픽스처에서는 크로스가 하나 이상 나와야 한다"
        cutoff = computed.crosses[0].index
        visible = computed.crosses_until(cutoff)
        assert all(cross.index <= cutoff for cross in visible)
        assert len(visible) < len(computed.crosses)

    def test_divergences_until_hides_the_future(self) -> None:
        """다이버전스도 같은 규칙이다."""
        fixture = load_fixture("btc_5m_range")
        computed = snapshot.compute(fixture.candles)
        assert computed.divergences
        cutoff = computed.divergences[0].second_index
        assert all(item.second_index <= cutoff for item in computed.divergences_until(cutoff))

    def test_cross_pairs_follow_the_spec(self) -> None:
        """spec §6.1 이 지정한 조합만 본다 (20-60, 60-120)."""
        assert CROSS_PAIRS == ((20, 60), (60, 120))
        fixture = load_fixture("btc_5m_range")
        computed = snapshot.compute(fixture.candles)
        found = {(cross.fast_period, cross.slow_period) for cross in computed.crosses}
        assert found <= set(CROSS_PAIRS)

    def test_alignment_at_uses_sma(self) -> None:
        """배열 판정은 SMA 기준이다 (spec §6.1 의 200일선 필터 관행)."""
        fixture = load_fixture("btc_1h_uptrend")
        computed = snapshot.compute(fixture.candles)
        expected = alignment([computed.sma[period][-1] for period in STANDARD_PERIODS])
        assert computed.alignment_at(-1) == expected

    def test_compute_is_deterministic(self) -> None:
        """동일 입력 2회 → 완전 동일 출력 (원칙 P1)."""
        fixture = load_fixture("btc_5m_downtrend")
        assert snapshot.compute(fixture.candles) == snapshot.compute(fixture.candles)

    def test_gap_fixture_computes_without_error(self) -> None:
        """결측 구간을 지표는 건너 계산한다 (`series` 모듈 docstring)."""
        fixture = load_fixture("btc_5m_exchange_gap")
        computed = snapshot.compute(fixture.candles)
        assert computed.at(-1).ma20 is not None

    def test_series_module_is_the_single_validation_point(self) -> None:
        """`compute()` 가 검증을 거치므로 잘못된 입력이 지표까지 가지 않는다."""
        with pytest.raises(SeriesError):
            snapshot.compute([])
        assert series_module.from_candles is from_candles
