"""골든 캔들 픽스처 로더 (P1-1-1).

픽스처 JSON 이 **입력의 SSoT** 다. 구간 정의는 `scripts/data/export_golden_fixtures.py` 가
갖고 있지만, 테스트는 그 스크립트를 보지 않고 굳어진 파일만 본다 — 그래야 테스트가
DB 나 스크립트 상태에 영향받지 않는다 (원칙 P1).

가격을 `Decimal(str)` 로 읽는다. float 를 거치면 이진 오차가 들어와 스냅샷이 플랫폼마다
달라진다.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "candles"
"""캔들 픽스처 디렉터리."""

GOLDEN_DIR = Path(__file__).parent / "golden" / "structures"
"""구조물 골든 스냅샷 디렉터리."""

EXPECTED_FIXTURES: tuple[str, ...] = (
    "btc_1h_downtrend",
    "btc_1h_reversal",
    "btc_1h_uptrend",
    "btc_5m_downtrend",
    "btc_5m_exchange_gap",
    "btc_5m_range",
    "btc_5m_uptrend",
)
"""기대 픽스처 이름 (D1-2 확정값, 정렬 고정).

⚠️ 여기에 못박아 두는 이유: 픽스처가 조용히 사라지면 골든 테스트는 **아무것도 검증하지
않으면서 통과**한다. 파일 목록과 이 표가 일치하는지 테스트가 검사한다.
"""


@dataclass(frozen=True, slots=True)
class CandleFixture:
    """로드된 픽스처.

    Attributes:
        name: 픽스처 이름.
        instrument: 종목.
        timeframe: 시간축.
        shape: 구간 성격 라벨 (`uptrend`/`downtrend`/`reversal`/`range`/`gap`).
        note: 선정 근거.
        candles: `ts` 오름차순 캔들.
    """

    name: str
    instrument: Instrument
    timeframe: Timeframe
    shape: str
    note: str
    candles: tuple[Candle, ...]


def load_fixture(name: str) -> CandleFixture:
    """픽스처 하나를 읽는다.

    Args:
        name: 픽스처 이름 (확장자 없음).

    Returns:
        로드된 픽스처.

    Raises:
        FileNotFoundError: 파일이 없는 경우. 스크립트로 내보내야 한다.
        AssertionError: 파일에 담긴 봉 수가 `bar_count` 와 다른 경우 — 파일이 손으로
            수정됐다는 신호다.
    """
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 가 없다 — `uv run python scripts/data/export_golden_fixtures.py` 로 내보내라"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    timeframe = Timeframe(raw["timeframe"])
    instrument = Instrument(
        market=Market.UPBIT,
        symbol=raw["symbol"],
        name=raw["symbol"],
        asset_type=AssetType.COIN,
        currency=Currency.KRW,
    )
    candles = tuple(
        Candle(
            instrument=instrument,
            timeframe=timeframe,
            ts=datetime.fromisoformat(item["ts"]),
            open=Decimal(item["open"]),
            high=Decimal(item["high"]),
            low=Decimal(item["low"]),
            close=Decimal(item["close"]),
            volume=Decimal(item["volume"]),
        )
        for item in raw["candles"]
    )
    assert len(candles) == raw["bar_count"], (
        f"{name}: 파일의 봉 수({len(candles)})가 bar_count({raw['bar_count']})와 다르다 — "
        f"손으로 고친 흔적이다. 스크립트로 다시 내보내라"
    )
    return CandleFixture(
        name=raw["name"],
        instrument=instrument,
        timeframe=timeframe,
        shape=raw["shape"],
        note=raw["note"],
        candles=candles,
    )


def load_all() -> list[CandleFixture]:
    """모든 픽스처를 이름 순으로 읽는다.

    Returns:
        픽스처 목록.
    """
    return [load_fixture(name) for name in EXPECTED_FIXTURES]
