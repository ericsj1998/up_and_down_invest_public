# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false
# pyright: reportPrivateImportUsage=false, reportCallIssue=false
# pyright: reportAttributeAccessIssue=false, reportInvalidTypeArguments=false, reportIndexIssue=false
#
# pandas / pandas-ta 는 타입 스텁이 없다. **정답지 전용 dev 의존성**이며 런타임
# 코드는 import 하지 않는다 — 여기서만 좁게 억제한다 (기존 대조 시험과 같은 구조).
"""새 지표 셋의 정답지 대조 — MACD · 볼린저 · 스토캐스틱 (T153).

기존 `test_indicators_vs_pandas_ta.py` 와 같은 규율을 따른다: *"대략 비슷하다"* 로
덮지 않고, 차이가 나면 **원인을 하나로 특정**한다.

| 지표 | 정답지와의 차이 | 기대 |
|---|---|---|
| MACD | 없음 (둘 다 EMA 기반, 시드 관행 동일) | epsilon |
| 볼린저 | 없음 (둘 다 모집단 편차 `ddof=0`) | epsilon |
| 스토캐스틱 | 없음 (둘 다 느린 스토캐스틱) | epsilon |

🔴 셋 중 하나라도 어긋나면 **정의가 다른 것**이고, 그러면 그 지표로 낸 Tier 표는
남의 것과 비교가 안 된다.
"""

from decimal import Decimal

import pandas as pd
import pandas_ta as ta
import pytest

from fixture_loader import CandleFixture, load_fixture
from updown.analysis.indicators.bands import bollinger
from updown.analysis.indicators.cci import cci
from updown.analysis.indicators.channels import donchian, keltner
from updown.analysis.indicators.macd import macd
from updown.analysis.indicators.stochastic import stochastic, williams

REL_TOL = 1e-12
"""float64 epsilon 은 2.2e-16 이다. 공식이 다르면 이 문턱을 절대 못 넘는다."""

_FIXTURES = ("btc_1h_uptrend", "btc_5m_downtrend", "btc_5m_range")


def _frame(fixture: CandleFixture) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "high": [float(candle.high) for candle in fixture.candles],
            "low": [float(candle.low) for candle in fixture.candles],
            "close": [float(candle.close) for candle in fixture.candles],
            "volume": [float(candle.volume) for candle in fixture.candles],
        }
    )


def _worst(
    mine: list[Decimal | None], theirs: "pd.Series[float]", *, floor: float = 1e-9
) -> tuple[float, int]:
    """최대 상대오차와 그 위치. 비교 지점이 없으면 실패시킨다.

    Note:
        🔴 `floor` 는 **0 근처에서 상대오차가 폭발하는 것**을 막는다. MACD 선은 두
        EMA 의 차라 교차 부근에서 0 에 가까워지고, 그때 상대오차의 분모가 0 으로
        가면 float 잡음이 3e-12 처럼 보인다 (실측). 절대 크기가 뜻을 갖는 지표는
        **가격 규모**를 바닥으로 준다 — 그래도 기간이 틀리면 오차가 1e-3 수준이라
        여전히 잡힌다.
    """
    worst = 0.0
    worst_at = -1
    compared = 0
    for index, value in enumerate(mine):
        if value is None or index >= len(theirs):
            continue
        reference = float(theirs.iloc[index])
        if reference != reference:  # NaN
            continue
        compared += 1
        scale = max(abs(reference), floor)
        error = abs(float(value) - reference) / scale
        if error > worst:
            worst, worst_at = error, index
    assert compared > 0, "비교 가능한 지점이 없다 — 이 시험은 아무것도 검증하지 않았다"
    return worst, worst_at


@pytest.mark.parametrize("name", _FIXTURES)
def test_macd_matches_reference(name: str) -> None:
    """MACD 선·시그널·히스토그램 셋 다 정답지와 같은 산술이다."""
    fixture = load_fixture(name)
    close = [candle.close for candle in fixture.candles]
    mine = macd(close)
    theirs = ta.macd(_frame(fixture)["close"], fast=12, slow=26, signal=9)
    # MACD 는 가격 단위이고 교차 부근에서 0 을 지난다 — 바닥을 가격 규모로 준다.
    level = float(sorted(close)[len(close) // 2])

    line, worst_at = _worst(mine.line, theirs["MACD_12_26_9"], floor=level)
    assert line < REL_TOL, f"{name} MACD 선: {line:.3e} @ {worst_at}"
    signal, worst_at = _worst(mine.signal, theirs["MACDs_12_26_9"], floor=level)
    assert signal < REL_TOL, f"{name} 시그널: {signal:.3e} @ {worst_at}"
    hist, worst_at = _worst(mine.histogram, theirs["MACDh_12_26_9"], floor=level)
    assert hist < REL_TOL, f"{name} 히스토그램: {hist:.3e} @ {worst_at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_bollinger_matches_reference(name: str) -> None:
    """볼린저 상·중·하단이 정답지와 같다 — **모집단 편차**(ddof=0)까지 같다."""
    fixture = load_fixture(name)
    close = [candle.close for candle in fixture.candles]
    mine = bollinger(close, period=20, multiple=Decimal(2))
    theirs = ta.bbands(_frame(fixture)["close"], length=20, std=2.0, ddof=0)

    middle, at = _worst(mine.middle, theirs["BBM_20_2.0_2.0"])
    assert middle < REL_TOL, f"{name} 중심선: {middle:.3e} @ {at}"
    upper, at = _worst(mine.upper, theirs["BBU_20_2.0_2.0"])
    assert upper < REL_TOL, f"{name} 상단: {upper:.3e} @ {at}"
    lower, at = _worst(mine.lower, theirs["BBL_20_2.0_2.0"])
    assert lower < REL_TOL, f"{name} 하단: {lower:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_bollinger_percent_b_matches_reference(name: str) -> None:
    """%B 도 같다 — 상단 돌파·하단 이탈을 한 값으로 쓰는 근거다."""
    fixture = load_fixture(name)
    close = [candle.close for candle in fixture.candles]
    mine = bollinger(close, period=20, multiple=Decimal(2))
    theirs = ta.bbands(_frame(fixture)["close"], length=20, std=2.0, ddof=0)
    worst, at = _worst(mine.position, theirs["BBP_20_2.0_2.0"])
    assert worst < REL_TOL, f"{name} %B: {worst:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_stochastic_matches_reference(name: str) -> None:
    """느린 스토캐스틱 %K·%D 가 정답지와 같다.

    Note:
        🔴 여기가 어긋나면 *"빠른 스토캐스틱을 쓰고 있다"* 는 뜻이고, 그러면 같은
        교차가 다른 시점에 난다.
    """
    fixture = load_fixture(name)
    frame = _frame(fixture)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    close = [candle.close for candle in fixture.candles]
    mine = stochastic(high, low, close, period=14, smooth_k=3, smooth_d=3)
    theirs = ta.stoch(frame["high"], frame["low"], frame["close"], k=14, d=3, smooth_k=3)

    k, at = _worst(mine.k, theirs["STOCHk_14_3_3"])
    assert k < REL_TOL, f"{name} %K: {k:.3e} @ {at}"
    d, at = _worst(mine.d, theirs["STOCHd_14_3_3"])
    assert d < REL_TOL, f"{name} %D: {d:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_cci_matches_the_textbook_definition(name: str) -> None:
    """🔴 **정답지를 못 쓴다 — pandas-ta 의 CCI 가 이 환경에서 고장났다.**

    실측 (2026-08-30 · btc_5m_range · index 25):

        우리 값          -26.0300
        MAD 정의로       -26.0300   ← 손으로 계산한 값과 정확히 같다
        표준편차로       -20.0828   ← 흔한 오구현
        pandas-ta    96,186,174   ← 🔴 CCI 는 보통 ±300 이다

    pandas-ta 의 `cci` 는 `Series.mad()` 에 기대는데 그 메서드가 pandas 2.0 에서
    사라졌다. 즉 **정답지가 틀렸고 우리가 맞다.**

    ⇒ 대조 상대를 라이브러리가 아니라 **정의 자체**로 바꾼다. 이 시험이 없으면
      나중에 누군가 *"정답지와 다르네"* 하며 맞는 쪽을 고칠 수 있다.
    """
    fixture = load_fixture(name)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    close = [candle.close for candle in fixture.candles]
    mine = cci(high, low, close, period=20).value

    typical = [float(h + low_ + c) / 3 for h, low_, c in zip(high, low, close, strict=True)]
    checked = 0
    for index in range(19, len(typical)):
        ours = mine[index]
        if ours is None:
            continue
        window = typical[index - 19 : index + 1]
        mean = sum(window) / 20
        deviation = sum(abs(one - mean) for one in window) / 20
        if deviation == 0:
            continue
        checked += 1
        expected = (typical[index] - mean) / (0.015 * deviation)
        assert float(ours) == pytest.approx(expected, rel=1e-9), index
    assert checked > 50


def test_the_reference_cci_is_the_broken_one() -> None:
    """⚠️ 위 시험의 전제를 잠근다 — 정답지가 고쳐지면 여기서 알려 준다.

    Note:
        pandas-ta 가 고쳐져 정상값을 내기 시작하면 이 시험이 깨진다. 그때 위
        시험을 다시 정답지 대조로 되돌리면 된다.
    """
    fixture = load_fixture("btc_5m_range")
    frame = _frame(fixture)
    theirs = ta.cci(frame["high"], frame["low"], frame["close"], length=20)
    worst = max(abs(float(one)) for one in theirs.dropna())
    assert worst > 1000, f"정답지가 정상 범위로 돌아왔다 ({worst:.1f}) — 대조를 복원하라"


@pytest.mark.parametrize("name", _FIXTURES)
def test_williams_matches_reference(name: str) -> None:
    """Williams %R — 스토캐스틱에서 파생했는데도 정답지와 같다."""
    fixture = load_fixture(name)
    frame = _frame(fixture)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    close = [candle.close for candle in fixture.candles]
    mine = williams(high, low, close, period=14)
    theirs = ta.willr(frame["high"], frame["low"], frame["close"], length=14)
    worst, at = _worst(mine, theirs, floor=100.0)
    assert worst < REL_TOL, f"{name} %R: {worst:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_keltner_matches_reference(name: str) -> None:
    """켈트너 — EMA 중심 · ATR 배수. 볼린저와 **다른 지표**임을 여기서 잠근다."""
    fixture = load_fixture(name)
    frame = _frame(fixture)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    close = [candle.close for candle in fixture.candles]
    mine = keltner(high, low, close, period=20, multiple=Decimal(2))
    theirs = ta.kc(frame["high"], frame["low"], frame["close"], length=20, scalar=2)
    middle, at = _worst(mine.middle, theirs["KCBe_20_2"])
    assert middle < REL_TOL, f"{name} 켈트너 중심: {middle:.3e} @ {at}"


@pytest.mark.parametrize("name", _FIXTURES)
def test_donchian_excludes_the_current_bar(name: str) -> None:
    """🔴 정답지는 **현재 봉을 포함**한다 — 우리는 일부러 뺀다.

    포함하면 신고가가 자기 자신 때문에 신고가가 되어 조건이 항상 참이고, 신호가
    매 봉 발생한다. 그래서 여기서는 *같다* 가 아니라 **한 봉 밀려 있음**을 잠근다.
    """
    fixture = load_fixture(name)
    frame = _frame(fixture)
    high = [candle.high for candle in fixture.candles]
    low = [candle.low for candle in fixture.candles]
    mine = donchian(high, low, period=20)
    theirs = ta.donchian(frame["high"], frame["low"], lower_length=20, upper_length=20)

    checked = 0
    for index in range(25, len(high) - 1):
        ours = mine.upper[index]
        if ours is None:
            continue
        # 우리 창 [i-20, i-1] = 정답지의 i-1 시점 창 [i-20, i-1]
        reference = float(theirs["DCU_20_20"].iloc[index - 1])
        if reference != reference:
            continue
        checked += 1
        assert float(ours) == pytest.approx(reference, rel=1e-12), index
    assert checked > 10
