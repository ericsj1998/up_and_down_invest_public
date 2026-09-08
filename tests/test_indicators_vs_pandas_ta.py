# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false
# pyright: reportPrivateImportUsage=false, reportCallIssue=false
# pyright: reportAttributeAccessIssue=false, reportInvalidTypeArguments=false
#
# pandas / pandas-ta 는 타입 스텁을 제공하지 않는다. **정답지 전용 dev 의존성**이며
# 런타임 코드는 이들을 import 하지 않으므로(test_indicators.py 가 강제) 여기서만
# 좁게 억제한다. 다른 검사는 그대로 돈다.
"""지표 정답지 대조 (P1-2-7 · P1-2 DoD 1 · spec §2.1).

## 느슨한 허용 오차로 덮지 않는다

"대략 비슷하다"는 검증은 공식이 틀렸어도 통과한다. 그래서 이 테스트는 각 지표마다
**차이의 원인을 하나로 특정**하고, 그 원인을 제거하면 **float64 epsilon 수준으로
일치**함을 증명한다.

| 지표 | 정답지와의 차이 | 증명 방식 |
|------|----------------|-----------|
| SMA | **없음** | 직접 비교 → 상대오차 0 |
| EMA | **없음** (시드 관행 동일) | 직접 비교 → 5e-16 |
| ATR | pandas-ta 가 `TR[0] = high-low` 로 채운다 | TR[0]을 같이 채워 비교 → **9.5e-16** |
| RSI | pandas-ta 는 **첫값 시드**, 우리는 **단순평균 시드** | 변형으로 비교 → **9.9e-16** |

즉 **평활 공식·산술은 정답지와 동일**하고, 남은 것은 문서화된 관행 선택 두 개다.

## 우리 선택이 왜 옳은가

- **ATR**: True Range 는 정의상 **직전 종가**를 쓴다. 첫 봉에는 직전 종가가 없으므로
  TR 이 존재하지 않는다. `high-low` 로 채우면 ATR 을 한 봉 일찍 얻는 대신 **불완전한
  TR** 을 평균에 섞는다. ATR 은 손절폭이 되므로(§6.1) 한 봉 늦더라도 정확한 쪽을 택한다
- **RSI**: Wilder 원문과 차트 플랫폼 관행이 **단순평균 시드**다. 첫값 시드는 라이브러리
  구현 편의이며 초반 수십 봉에서 값이 크게 다르다 (실측: index 14 에서 145% 차이)

두 선택 모두 수렴하므로 후반부는 어느 쪽이든 사실상 같다 — 그것도 아래에서 측정한다.
"""

from decimal import Decimal

import pandas as pd
import pandas_ta as ta
import pytest

from fixture_loader import CandleFixture, load_fixture
from updown.analysis.indicators.atr import STANDARD_PERIOD, atr, true_range
from updown.analysis.indicators.ma import STANDARD_PERIODS, ema, sma
from updown.analysis.indicators.rsi import rsi
from updown.analysis.indicators.series import wilder_average
from updown.analysis.indicators.volume import volume_ratio

REL_TOL_SAME_ARITHMETIC = 1e-12
""""같은 산술" 판정 허용 오차 (상대).

float64 epsilon 은 2.2e-16 이다. Decimal → float 변환과 누적을 감안해도 1e-12 은
**넉넉하면서 여전히 엄격**하다 — 공식이 다르면 이 문턱을 절대 통과하지 못한다.
"""

REL_TOL_AFTER_CONVERGENCE = 1e-2
"""시드 차이가 남긴 오차의 상한 (상대), 워밍업 후 `5*period` 봉 시점.

Wilder 평활의 감쇠가 `(1 - 1/period)^n` 이므로 period=14, n=70 이면 `(13/14)^70 ≈ 6.3e-3`
이다. 1e-2 은 그 이론값 바로 위이며, 임의로 고른 값이 아니다.
"""

_FIXTURES = ("btc_1h_uptrend", "btc_5m_downtrend", "btc_5m_range")
"""대조에 쓸 픽스처 — 상승·하락·횡보를 모두 포함한다."""


def _frame(fixture: CandleFixture) -> pd.DataFrame:
    """픽스처를 pandas-ta 입력으로 바꾼다."""
    return pd.DataFrame(
        {
            "high": [float(candle.high) for candle in fixture.candles],
            "low": [float(candle.low) for candle in fixture.candles],
            "close": [float(candle.close) for candle in fixture.candles],
            "volume": [float(candle.volume) for candle in fixture.candles],
        }
    )


def _worst_relative(
    mine: list[Decimal | None] | list[float | None],
    theirs: "pd.Series[float]",
    from_index: int = 0,
) -> tuple[float, int]:
    """두 시리즈의 최대 상대오차와 그 위치.

    Args:
        mine: 우리 구현 결과. `None` 위치는 건너뛴다.
        theirs: 정답지. NaN 위치는 건너뛴다.
        from_index: 이 인덱스부터 비교한다.

    Returns:
        `(최대 상대오차, 위치)`. 비교 가능한 지점이 없으면 `(0.0, -1)`.

    Raises:
        AssertionError: 비교 가능한 지점이 하나도 없는 경우 — 빈 비교로 통과하는
            테스트를 막는다.
    """
    assert len(mine) == len(theirs), f"길이가 다르다: {len(mine)} vs {len(theirs)}"
    worst, worst_at, compared = 0.0, -1, 0
    for index in range(from_index, len(mine)):
        value, other = mine[index], theirs.iloc[index]
        if value is None or pd.isna(other):
            continue
        compared += 1
        reference = float(other)
        relative = abs(float(value) - reference) / abs(reference) if reference else 0.0
        if relative > worst:
            worst, worst_at = relative, index
    assert compared > 0, "비교 가능한 지점이 없다 — 이 테스트는 아무것도 검증하지 않았다"
    return worst, worst_at


@pytest.mark.parametrize("name", _FIXTURES)
@pytest.mark.parametrize("period", STANDARD_PERIODS)
def test_sma_matches_reference_exactly(name: str, period: int) -> None:
    """SMA 는 정답지와 같은 산술이다 — 차이가 없다."""
    fixture = load_fixture(name)
    closes = [candle.close for candle in fixture.candles]
    worst, at = _worst_relative(
        sma(closes, period), ta.sma(_frame(fixture)["close"], length=period)
    )
    assert worst < REL_TOL_SAME_ARITHMETIC, f"{name} SMA{period}: 상대오차 {worst:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
@pytest.mark.parametrize("period", STANDARD_PERIODS)
def test_ema_matches_reference_exactly(name: str, period: int) -> None:
    """EMA 는 시드 관행까지 정답지와 같다 (둘 다 단순평균 시드)."""
    fixture = load_fixture(name)
    closes = [candle.close for candle in fixture.candles]
    worst, at = _worst_relative(
        ema(closes, period), ta.ema(_frame(fixture)["close"], length=period)
    )
    assert worst < REL_TOL_SAME_ARITHMETIC, f"{name} EMA{period}: 상대오차 {worst:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_atr_smoothing_is_identical_only_first_bar_tr_differs(name: str) -> None:
    """ATR 평활은 정답지와 동일하다 — 차이는 `TR[0]` 처리 하나뿐이다.

    Note:
        pandas-ta 가 채우는 방식(`TR[0] = high-low`)으로 우리 `wilder_average` 를 돌리면
        정답지와 epsilon 수준으로 일치한다. 즉 공식이 아니라 **정의 해석**만 다르다.
    """
    fixture = load_fixture(name)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    close = [candle.close for candle in fixture.candles]

    filled: list[Decimal | None] = list(true_range(high, low, close))
    filled[0] = high[0] - low[0]
    frame = _frame(fixture)
    reference = ta.atr(frame["high"], frame["low"], frame["close"], length=STANDARD_PERIOD)

    worst, at = _worst_relative(wilder_average(filled, STANDARD_PERIOD), reference)
    assert worst < REL_TOL_SAME_ARITHMETIC, (
        f"{name}: TR[0] 을 정답지와 같게 채웠는데도 상대오차 {worst:.3e} @ {at} — "
        f"평활 공식이 다르다는 뜻이다"
    )


@pytest.mark.parametrize("name", _FIXTURES)
def test_our_atr_starts_one_bar_later_by_definition(name: str) -> None:
    """우리 ATR 은 정답지보다 **한 봉 늦게** 시작한다 — 의도된 것이다.

    Note:
        True Range 는 직전 종가를 쓰므로 첫 봉에 존재하지 않는다. pandas-ta 는
        `high-low` 로 채워 한 봉 일찍 값을 내지만 불완전한 TR 을 평균에 섞는다.
        ATR 은 손절폭이 되므로(§6.1) 정확한 쪽을 택했다.
    """
    fixture = load_fixture(name)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    close = [candle.close for candle in fixture.candles]
    frame = _frame(fixture)

    ours = atr(high, low, close)
    theirs = ta.atr(frame["high"], frame["low"], frame["close"], length=STANDARD_PERIOD)

    our_first = next(index for index, value in enumerate(ours) if value is not None)
    their_first = int(theirs.notna().idxmax())
    assert our_first == STANDARD_PERIOD, f"{name}: 우리 첫 값이 index {our_first} 다 (14 여야 한다)"
    assert our_first == their_first + 1, (
        f"{name}: 우리 {our_first} / 정답지 {their_first} — 한 봉 차이가 아니라면 "
        f"TR 정의가 바뀐 것이다"
    )


@pytest.mark.parametrize("name", _FIXTURES)
def test_atr_converges_to_reference(name: str) -> None:
    """시드 차이는 수렴한다 — 워밍업 후 `5*period` 봉부터는 사실상 같다."""
    fixture = load_fixture(name)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    close = [candle.close for candle in fixture.candles]
    frame = _frame(fixture)
    reference = ta.atr(frame["high"], frame["low"], frame["close"], length=STANDARD_PERIOD)

    worst, at = _worst_relative(atr(high, low, close), reference, from_index=STANDARD_PERIOD * 6)
    assert worst < REL_TOL_AFTER_CONVERGENCE, f"{name} ATR: 상대오차 {worst:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_rsi_formula_is_identical_only_the_seed_differs(name: str) -> None:
    """RSI 공식은 정답지와 동일하다 — 차이는 Wilder 시드 방식 하나뿐이다.

    Note:
        pandas-ta 방식(첫값 시드)으로 계산한 변형이 정답지와 epsilon 수준으로 일치한다.
        우리는 Wilder 원문·차트 플랫폼 관행인 **단순평균 시드**를 쓴다.
    """
    fixture = load_fixture(name)
    close = [candle.close for candle in fixture.candles]
    variant = _rsi_with_first_value_seed(close, STANDARD_PERIOD)
    reference = ta.rsi(_frame(fixture)["close"], length=STANDARD_PERIOD)

    worst, at = _worst_relative(variant, reference)
    assert worst < REL_TOL_SAME_ARITHMETIC, (
        f"{name}: 시드를 정답지와 같게 맞췄는데도 상대오차 {worst:.3e} @ {at} — "
        f"RSI 공식이 다르다는 뜻이다"
    )


@pytest.mark.parametrize("name", _FIXTURES)
def test_rsi_converges_to_reference(name: str) -> None:
    """RSI 시드 차이도 수렴한다."""
    fixture = load_fixture(name)
    close = [candle.close for candle in fixture.candles]
    reference = ta.rsi(_frame(fixture)["close"], length=STANDARD_PERIOD)
    worst, at = _worst_relative(rsi(close), reference, from_index=STANDARD_PERIOD * 6)
    assert worst < REL_TOL_AFTER_CONVERGENCE, f"{name} RSI: 상대오차 {worst:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_volume_ratio_excludes_the_current_bar(name: str) -> None:
    """거래량 배수의 분모는 **직전** N봉이다.

    Note:
        pandas-ta 에 대응 지표가 없어 pandas 로 정답지를 만든다. `shift(1)` 이 곧
        "자기 자신을 분모에서 뺀다"는 요구의 표현이다 — 이것이 없으면 폭발한 거래량이
        자기 분모를 끌어올려 배수가 축소된다 (`volume` 모듈 docstring).
    """
    fixture = load_fixture(name)
    volumes = [candle.volume for candle in fixture.candles]
    frame = _frame(fixture)
    baseline = frame["volume"].rolling(20).mean().shift(1)
    reference = frame["volume"] / baseline

    worst, at = _worst_relative(volume_ratio(volumes), reference)
    assert worst < REL_TOL_SAME_ARITHMETIC, f"{name} 거래량 배수: 상대오차 {worst:.3e} @ {at}"


def _rsi_with_first_value_seed(close: list[Decimal], period: int) -> list[float | None]:
    """pandas-ta 방식(첫값 시드) RSI — **대조 전용**이며 프로덕션 경로가 아니다.

    Args:
        close: 종가 열.
        period: 기간.

    Returns:
        RSI 시리즈.

    Note:
        이 함수를 `rsi.py` 에 두지 않는 이유: 두 시드 방식을 동시에 제공하면 어느 것이
        우리 기준인지 흐려지고, 설정으로 고를 수 있게 되면 백테스트 재현성이 갈라진다.
        정답지 해부용으로 테스트에만 둔다.
    """
    alpha = 1.0 / period
    gains: list[float | None] = [None] * len(close)
    losses: list[float | None] = [None] * len(close)
    for index in range(1, len(close)):
        delta = float(close[index] - close[index - 1])
        gains[index] = max(delta, 0.0)
        losses[index] = max(-delta, 0.0)

    def smooth(values: list[float | None]) -> list[float | None]:
        out: list[float | None] = [None] * len(values)
        current: float | None = None
        seen = 0
        for index, value in enumerate(values):
            if value is None:
                continue
            current = value if current is None else current + alpha * (value - current)
            seen += 1
            if seen >= period:
                out[index] = current
        return out

    up, down = smooth(gains), smooth(losses)
    result: list[float | None] = [None] * len(close)
    for index in range(len(close)):
        gain, loss = up[index], down[index]
        if gain is None or loss is None:
            continue
        result[index] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    return result
