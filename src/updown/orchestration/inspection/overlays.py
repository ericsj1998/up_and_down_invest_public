"""차트 **겹칩선** — 화면 하나가 아니라 **차트 전부**가 쓴다 (2026-08-30).

## 왜 여기로 나왔나

사용자 2026-08-30: *"결국 어느 정도는 다른 형태로 차트를 구성할 수밖에 없는 건 알지만,
내가 말하는건 공유할 수 있는 부분들까지도 하드코딩해서 사용할 필요는 없다는 거지.
재사용성 측면에서 말야."*

이평선·ADX 를 그리는 코드가 `apps/api/walkforward.py` 안에 있었다. 그래서 RUN 상세는
이평선을 그리고 **차트 주문은 못 그렸다** — 같은 봉을 보는 두 화면이 다른 것을 보여
줬고, 화면에는 그 이유가 어디에도 안 적혀 있었다.

## 🔴 여기 들어올 수 있는 것 · 없는 것

    들어온다   `FrameView` **만** 있으면 그릴 수 있는 것 — 봉이 곧 답인 것들
    못 들어온다 플레이북·세션·게이트를 알아야 하는 것 (`slope_layer`·`vol_layer`·
               `stance_layer`·`adx_gate_layer`)

⚠️ 후자를 억지로 끌고 오면 이 모듈이 **판을 알아야** 하고, 그러면 분석 화면이 판 없이
못 그린다. 경계는 *"무엇을 알아야 그릴 수 있나"* 다.

## ⛔ 여기서 지표를 다시 계산하지 않는다

세션이 쓰는 `sma()`·`adx()` 를 그대로 부른다 (절대 규칙 #9). 화면이 다른 값을 그리면
*"왜 저기서 청산됐지"* 가 영영 안 풀린다.
"""

from __future__ import annotations

from updown.analysis.indicators.adx import adx
from updown.analysis.indicators.ma import sma
from updown.orchestration.inspection.snapshot import FrameView, Layer

MA_OVERLAY_FLAG = "overlay.trail_ma"
"""이평선 레이어 이름 — 화면이 이 이름으로 찾는다."""

ADX_OVERLAY_FLAG = "overlay.adx"
"""ADX 레이어 이름."""

DEFAULT_MA_PERIOD = 200
"""판이 없을 때 그릴 이동평균 기간.

🔴 **지어낸 값이 아니다** — `config/playbooks.yml` 의 모든 `trail_ma` 가 200 이다.
분석 화면에는 플레이북이 없으므로(사람이 아직 아무 판도 안 골랐다) 실제로 도는 판들이
쓰는 값을 그린다.

⚠️ 판이 있는 화면(RUN 상세)은 **그 판의 값**을 쓴다 — 여기 기본값으로 떨어지면
화면이 그 판과 다른 선을 그린다.
"""


def ma_layer(view: FrameView, period: int = DEFAULT_MA_PERIOD) -> Layer:
    """트레일 청산선(SMA) — 봉별 `{ts, price}` 점.

    Args:
        view: 그릴 축의 화면.
        period: 이동평균 기간. 판이 있으면 **그 판의 `trail_ma`** 를 넘긴다.

    Returns:
        `overlay.trail_ma` 레이어. 봉이 `period` 미만이면 점이 없고 note 가 그 사실을 말한다.

    Note:
        🔴 세션이 트레일에 쓰는 `sma()` 를 그대로 쓴다 — 화면과 판정이 다른 값을 그리면
        *"왜 저기서 청산됐지"* 가 안 풀린다. 앞 `period-1` 봉은 값이 `None` 이라 건너뛴다.
    """
    series = sma([candle.close for candle in view.candles], period)
    points = tuple(
        {"ts": candle.ts.isoformat(), "price": str(value)}
        for candle, value in zip(view.candles, series, strict=True)
        if value is not None
    )
    note = f"SMA{period} — 트레일 청산선(상향만)" if points else f"SMA{period} — 봉 부족"
    return Layer(MA_OVERLAY_FLAG, points, note=note)


def adx_overlay(view: FrameView) -> Layer:
    """추세강도(ADX) — 봉별 `{ts, value}` 점.

    Args:
        view: 그릴 축의 화면.

    Returns:
        `overlay.adx` 레이어. 워밍업이 모자라면 점이 없고 note 가 그 사실을 말한다.

    Note:
        🔴 **세션이 쓰는 그 `adx()` 를 그대로 쓴다** (규칙 #9 · 클라 재구현 금지).

        ⚠️ 앞쪽 봉은 `None` 이라 건너뛴다. **0 으로 채우지 않는다** — 0 은 "추세가
        없다" 는 뜻이고, 여기서 진짜 뜻은 "아직 못 잰다" 다 (규칙 #8).
    """
    series = adx(
        [candle.high for candle in view.candles],
        [candle.low for candle in view.candles],
        [candle.close for candle in view.candles],
    )
    points = tuple(
        {"ts": candle.ts.isoformat(), "value": str(value)}
        for candle, value in zip(view.candles, series, strict=True)
        if value is not None
    )
    note = "ADX(14) — 추세 강도. 방향은 말하지 않는다" if points else "ADX — 봉 부족"
    return Layer(ADX_OVERLAY_FLAG, points, note=note)
