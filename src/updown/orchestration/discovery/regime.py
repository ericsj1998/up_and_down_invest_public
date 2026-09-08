"""**국면 태깅** — 상승 / 하락 / 횡보 (T159 §1-G).

## ⛔ 성과를 보고 경계를 조정하지 않는다

지시서 §1-G 가 그렇게 못 박았다. 그래서 규칙을 **먼저 적고 그대로 쓴다**:

    기준선   BTC 일봉 종가의 200일 단순이동평균
    상승     종가가 선 위 **그리고** 선이 오르는 중
    하락     종가가 선 아래 **그리고** 선이 내리는 중
    횡보     나머지 전부

## ⭐ 왜 BTC 하나인가 — **날짜만의 함수**여야 한다

종목별로 국면을 매기면 같은 날이 종목마다 다른 국면이 되고, 누산기가 종목을
날짜로 합쳐 놓았으므로 **사후에 쪼갤 수 없다**.

BTC 를 시장 대용으로 쓰면 국면이 **날짜만의 함수**가 되어, 이미 모아 둔 일별
집계를 그대로 국면별로 가를 수 있다. 코인 포트폴리오에서 BTC 를 시장 대용으로
삼는 것은 흔한 선택이고, 무엇보다 **이 스캔을 다시 안 돌아도 된다**.

⚠️ 대가: 알트가 BTC 와 다른 국면일 때를 못 가른다. 그것을 재려면 종목별 국면으로
스캔을 다시 돌아야 한다 — 지금은 안 한다.

## 🔴 인과적이다

200일선은 **그날까지의** 종가만 쓰고, 기울기는 **지난 20일**을 본다. 앞을 안 본다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

__all__ = ["SLOPE_DAYS", "WINDOW", "Regime", "classify", "from_closes", "load_market"]

WINDOW = 200
"""기준선의 길이(일). 가장 널리 쓰이는 값이고 **고르지 않았다** — 지시서 권장이다."""

SLOPE_DAYS = 20
"""기울기를 볼 구간(일). 선이 오르는 중인지 내리는 중인지만 가른다."""


class Regime(StrEnum):
    """시장 국면.

    Attributes:
        BULL: 상승 — 종가가 200일선 위이고 선이 오르는 중.
        BEAR: 하락 — 종가가 선 아래이고 선이 내리는 중.
        RANGE: 횡보 — 나머지.
        UNKNOWN: 200일이 아직 안 찼다.

    Note:
        🔴 `UNKNOWN` 을 따로 둔다. 워밍업 구간을 횡보로 뭉개면 초기 구간이 통째로
        한 국면에 들어가고, *"횡보에서 잘 벌었다"* 가 사실은 *"2020년에 잘 벌었다"*
        가 된다.
    """

    BULL = "상승"
    BEAR = "하락"
    RANGE = "횡보"
    UNKNOWN = "미상"


def classify(closes: Sequence[float]) -> list[Regime]:
    """일봉 종가 열을 국면 열로 바꾼다.

    Args:
        closes: 날짜 오름차순 종가.

    Returns:
        같은 길이의 국면 열.

    Note:
        ⭐ 이동평균을 **누적합으로** 굴린다. 매 날짜마다 200개를 다시 더하면
        2,400일에서 48만 번이 되고, 그 자체가 느린 것은 아니지만 같은 실수를
        지표에서 두 번 했다 (`macd_hist` · 위생 리포트 O(n²)).
    """
    out: list[Regime] = []
    running = 0.0
    line: list[float] = []
    for index, price in enumerate(closes):
        running += price
        if index >= WINDOW:
            running -= closes[index - WINDOW]
        if index < WINDOW - 1:
            line.append(float("nan"))
            out.append(Regime.UNKNOWN)
            continue
        here = running / WINDOW
        line.append(here)
        before = line[index - SLOPE_DAYS] if index >= WINDOW - 1 + SLOPE_DAYS else float("nan")
        if before != before:  # NaN — 기울기를 볼 만큼 선이 안 쌓였다
            out.append(Regime.UNKNOWN)
            continue
        rising = here > before
        if price > here and rising:
            out.append(Regime.BULL)
        elif price < here and not rising:
            out.append(Regime.BEAR)
        else:
            out.append(Regime.RANGE)
    return out


def from_closes(days: Sequence[str], closes: Sequence[float]) -> dict[str, Regime]:
    """날짜 문자열 → 국면.

    Args:
        days: `YYYY-MM-DD` 오름차순.
        closes: 같은 길이의 종가.

    Returns:
        날짜 → 국면.

    Raises:
        ValueError: 길이가 다른 경우 — 짝이 어긋나면 국면이 통째로 밀린다.
    """
    if len(days) != len(closes):
        raise ValueError(f"날짜 {len(days)}개와 종가 {len(closes)}개의 길이가 다르다")
    return dict(zip(days, classify(closes), strict=True))


def load_market(path: Path) -> dict[str, Regime]:
    """1분봉 파일에서 일봉을 접어 국면을 만든다.

    Args:
        path: `data/discovery/1m/BTCUSDT.json` 같은 파일.

    Returns:
        날짜 → 국면.

    Note:
        ⚠️ **그날의 마지막 1분봉 종가**를 일봉 종가로 쓴다. UTC 자정 경계이며,
        누산기가 쓰는 날짜 키와 같은 기준이라 짝이 맞는다.
    """
    rows = json.loads(path.read_text(encoding="utf-8"))
    last: dict[str, float] = {}
    for one in rows:
        day = datetime.fromtimestamp(int(one[0]) / 1000, tz=UTC).strftime("%Y-%m-%d")
        last[day] = float(one[4])
    days = sorted(last)
    return from_closes(days, [last[day] for day in days])
