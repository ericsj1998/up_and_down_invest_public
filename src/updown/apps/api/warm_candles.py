"""유니버스 봉 예열 — 밤에 미리 합성해 두어 낮에 첫 클릭이 1~2초가 되게 (2026-09-11).

토스는 1분 원봉만 주므로 15m·1h·4h 는 1분봉 수백 페이지에서 합성한다. 요율 5건/초면
1h 400봉이 40초다(사용자 "30초 너무 길다"). 그 40초를 사람 앞에서 쓰지 않고 **장 마감 뒤
서버가** 저평가 유니버스(NASDAQ·NYSE) 전부를 미리 읽어 DB(`StoredCandles`)에 둔다.
다음날 화면·프록시(`/admin/toss/candles`)는 DB 를 맞는다.

- 축과 창: 판 시작의 걸음 축(5m)은 달력 7일, 차트 분석 주문의 단기(15m)·스윙(1h)은
  `lookback_span`(정규장 기준 400+200봉), AI 비교의 4h 는 그쪽 `_lookback`(달력 400봉).
  일봉은 순위표가 이미 매일 받는다.
- 토스를 직접 부르는 프로세스(실계좌 서버)에서만 돈다 — 프록시 client 인 로컬은 서버 DB 를
  받으면 된다.
- 한 종목·축 실패는 적고 넘어간다. 두 번 돌아도 안전하다(DB 먼저).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from updown.apps.api.analysis import WARMUP_BARS, lookback_span
from updown.apps.api.chart_order import FRAME_BARS
from updown.apps.api.fundamentals import universe_of
from updown.apps.api.quotes import stored_quotes
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.logging.setup import get_logger
from updown.marketdata.provider import MarketDataProvider, market_allowlist, toss_allowed
from updown.orchestration.ai_analysis.service import FETCH_BARS

_logger = get_logger("api.warm_candles")

WARM_MARKETS: tuple[Market, ...] = (Market.NASDAQ, Market.NYSE)
WARM_AT_UTC_HOUR = 21
"""미국 정규장 마감(20:00Z) 한 시간 뒤. KST 06:00 — 아침에 화면을 열기 전에 끝난다."""
PAUSE_BETWEEN_S = 0.2
"""종목 사이 잠깐 — 요율 스로틀이 지키지만 판 시세 조회가 끼어들 틈을 준다."""


def warm_spans() -> list[tuple[Timeframe, Callable[[Market], timedelta]]]:
    """예열할 축과 창 (순수).

    Returns:
        `(축, 시장 → 달력 기간)` 목록 — 5m 은 판 시작(걸음 축), 15m·1h 는 차트 분석 주문 창,
        4h 는 AI 비교 창.
    """
    return [
        # ⭐ 5m 은 **판 시작의 걸음 축**이다 (`STEP_FRAME`). 시드가 800봉을 달력 기준으로 달라
        #   하므로(≈2.8일) 주말을 넘겨도 남게 7일을 채운다 — 분봉 덩어리 하나라 한 번에 받는다.
        #   이것이 없어 2026-09-11 펀드 만들기가 토스를 직접 불렀고 요청 상한에 걸렸다.
        (Timeframe.M5, lambda _m: timedelta(days=7)),
        (Timeframe.M15, lambda m: lookback_span(Timeframe.M15, FRAME_BARS + WARMUP_BARS, m)),
        (Timeframe.H1, lambda m: lookback_span(Timeframe.H1, FRAME_BARS + WARMUP_BARS, m)),
        (Timeframe.H4, lambda _m: timedelta(minutes=240 * FETCH_BARS)),
    ]


def can_warm() -> bool:
    """이 프로세스가 예열을 돌려도 되는가 — 토스를 **직접** 부르는 구성일 때만.

    Returns:
        참이면 예열을 돌린다. 프록시 client(연구 PC)나 토스가 꺼진 구성은 거짓 —
        서버가 채운 DB 를 받으면 되고, 토큰은 client 당 하나뿐이다.
    """
    if not toss_allowed(market_allowlist()):
        return False
    return not MarketDataProvider().toss_via_proxy()


async def warm_universe(
    report: Callable[[str], None] | None = None, *, symbols: dict[Market, list[str]] | None = None
) -> dict[str, Any]:
    """유니버스 종목의 15m·1h·4h 를 한 바퀴 읽어 DB 에 둔다.

    Args:
        report: 진행 줄 콜백(작업 화면).
        symbols: 시장 → 종목. None 이면 `universe_of` 전부.

    Returns:
        `{symbols, frames, fetched, failed, seconds}`.
    """
    started = time.perf_counter()
    targets = symbols if symbols is not None else {m: universe_of(m) for m in WARM_MARKETS}
    total = sum(len(v) for v in targets.values())
    done = frames = fetched = failed = 0
    now = datetime.now(UTC)
    async with MarketDataProvider() as provider:
        for market, names in targets.items():
            adapter = stored_quotes(provider, market, regular_only=False)
            for symbol in names:
                instrument = Instrument(market, symbol, symbol, AssetType.STOCK, Currency.USD)
                for timeframe, span_of in warm_spans():
                    t0 = time.perf_counter()
                    try:
                        rows = await adapter.get_candles(
                            instrument, timeframe, now - span_of(market), now
                        )
                        frames += 1
                        fetched += len(rows)
                    except Exception as exc:  # 한 축 실패가 밤 전체를 멈추지 않는다 — 적는다
                        failed += 1
                        _logger.warning(
                            "warm_candles_failed",
                            payload={
                                "symbol": symbol,
                                "frame": timeframe.value,
                                "detail": str(exc)[:120],
                            },
                        )
                        continue
                    if report is not None:
                        report(
                            f"[{time.perf_counter() - started:6.0f}s] {market.value} {symbol} "
                            f"{timeframe.value} {len(rows)}봉 · {time.perf_counter() - t0:.1f}s"
                        )
                done += 1
                await asyncio.sleep(PAUSE_BETWEEN_S)
    made = {
        "symbols": done,
        "of": total,
        "frames": frames,
        "fetched": fetched,
        "failed": failed,
        "seconds": int(time.perf_counter() - started),
    }
    _logger.info("warm_candles_done", payload=made)
    return made


def seconds_until(hour_utc: int, now: datetime | None = None) -> float:
    """다음 `hour_utc:00Z` 까지 초 (순수).

    Args:
        hour_utc: 목표 시각 (UTC 시 · 0~23).
        now: 기준 시각. None 이면 지금 (UTC).

    Returns:
        기다릴 초. 이미 지난 시각이면 다음 날 같은 시각까지다.
    """
    at = now or datetime.now(UTC)
    target = at.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= at:
        target += timedelta(days=1)
    return (target - at).total_seconds()


async def warm_loop(*, hour_utc: int = WARM_AT_UTC_HOUR) -> None:
    """매일 `hour_utc` 에 한 바퀴 — 토스를 직접 부르는 프로세스만.

    Args:
        hour_utc: 시작 시각(UTC 시).

    Raises:
        asyncio.CancelledError: 종료 신호 — 삼키지 않는다.
    """
    while True:
        await asyncio.sleep(seconds_until(hour_utc))
        if not can_warm():
            _logger.info(
                "warm_candles_skipped", payload={"reason": "이 프로세스는 토스를 직접 안 부른다"}
            )
            continue
        try:
            await warm_universe()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("warm_candles_loop_failed", payload={"error": str(exc)[:200]})
