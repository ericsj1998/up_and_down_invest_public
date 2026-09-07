"""주기 캔들 수집 (P0-8-6 · spec §4.2, §12.1, §7).

## 최신 봉 하나만 받지 않는다

일시 장애로 한 번 실행이 빠지면 그 봉이 **영구히 구멍으로 남는다.** 그래서 매번
`gap_lookback_bars` 만큼 되돌아가 겹쳐 받는다 — upsert 라 중복이 무해하고, 그 겹침이
지난 구멍을 자동으로 메운다.

이것이 "갭 메움"의 전부다. 별도 복구 배치가 필요 없고, **정상 경로가 곧 복구 경로**다.

## 오래 멈춰 있었다면 백필의 일이다

engine 이 며칠 멈춰 있었다면 그 구멍은 주기 수집이 감당할 크기가 아니다. 상한 없이 두면
기동 직후 잡 하나가 수만 봉을 받으며 rate limit 을 독점하고, 그 사이 다른 시간축 수집이
밀린다.

`max_catchup_bars` 를 넘는 구멍은 **경고를 남기고 백필 CLI 로 넘긴다.** 조용히 일부만 받고
성공으로 보고하지 않는다 (spec §7) — 그러면 커버리지에 구멍이 있는데 로그는 정상이다.

## 미완성 봉과 재개 건너뛰기

`run_backfill` 을 재사용하되 **`resume=False`** 다. 재개 건너뛰기는 "이미 채워진 창은 다시
받지 않는다"인데, 주기 수집의 목적은 **최근 구간을 다시 받는 것** 이므로 그 최적화가
정면으로 방해한다.

미완성 봉 제외는 그대로 적용된다 — 봉마감 직후에 돌아도 거래소가 마지막 봉을 아직
확정하지 않았을 수 있다.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from updown.common.domain.instrument import Market, Timeframe
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import BrokerAdapter
from updown.marketdata.ingest.backfill import BackfillScope, run_backfill
from updown.marketdata.ingest.integrity import IntegrityThresholds, inspect_candles
from updown.marketdata.ingest.repository import CandleRepository, InstrumentNotFoundError
from updown.marketdata.ingest.timeframes import interval, last_closed_ts
from updown.marketdata.ingest.universe import UnknownSymbolError, to_instrument

_logger = get_logger("marketdata.ingest.collector")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """한 종목 x TF 의 수집 결과.

    Attributes:
        symbol: 마켓 코드.
        timeframe: 시간축.
        stored: 적재를 시도한 봉 수.
        window_start: 실제 요청한 구간 시작.
        window_end: 실제 요청한 구간 끝 (마감 경계).
        catchup_truncated: 구멍이 상한을 넘어 잘렸는가. True 면 **백필이 필요하다**.
        issues_recorded: 새로 적재한 무결성 이슈 수.
        skipped_reason: 수집하지 않은 사유. 수집했으면 None.
    """

    symbol: str
    timeframe: Timeframe
    stored: int
    window_start: datetime | None
    window_end: datetime | None
    catchup_truncated: bool = False
    issues_recorded: int = 0
    skipped_reason: str | None = None

    @property
    def needs_backfill(self) -> bool:
        """백필 CLI 개입이 필요한 상태인가."""
        return self.catchup_truncated


@dataclass(frozen=True, slots=True)
class CollectionRun:
    """한 번의 주기 수집 실행 (시간축 1개, 유니버스 전체).

    Attributes:
        timeframe: 시간축.
        results: 종목별 결과.
        failures: `(symbol, 오류 메시지)` 목록.
    """

    timeframe: Timeframe
    results: tuple[CollectionResult, ...] = field(default_factory=tuple)
    failures: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def stored(self) -> int:
        """총 적재 시도 봉 수."""
        return sum(result.stored for result in self.results)

    @property
    def needs_backfill(self) -> list[str]:
        """백필이 필요한 종목 목록."""
        return [result.symbol for result in self.results if result.needs_backfill]


def resolve_window(
    last_stored: datetime | None,
    now: datetime,
    timeframe: Timeframe,
    scope: BackfillScope,
) -> tuple[datetime, datetime, bool]:
    """이번 실행이 받을 구간을 정한다.

    Args:
        last_stored: 이미 적재된 마지막 봉 시각. 없으면 None.
        now: 현재 시각 (UTC aware). **인자로 받는다** (원칙 P1).
        timeframe: 시간축.
        scope: 수집 범위 설정.

    Returns:
        `(start, end, catchup_truncated)`. `end` 는 마감 경계다.

    Note:
        세 경우다.

        1. **적재 이력 없음** → 최근 `gap_lookback_bars` 만 받는다. 앵커부터 받지 않는다 —
           그것은 백필의 일이고, 주기 수집이 그걸 하면 기동 직후 rate limit 을 독점한다
        2. **정상** → `last_stored` 에서 `gap_lookback_bars` 만큼 되돌아간 지점부터.
           겹쳐 받는 것이 갭 메움의 수단이다
        3. **구멍이 상한 초과** → 상한만큼만 받고 `catchup_truncated=True` 로 알린다.
           나머지는 백필 CLI 소관이다
    """
    step = interval(timeframe)
    end = last_closed_ts(now, timeframe)
    lookback_start = end - step * (scope.gap_lookback_bars - 1)

    if last_stored is None:
        return lookback_start, end, False

    # 적재 이력이 최근이면 `lookback_start` 와 같고, 구멍이 있으면 그보다 과거가 된다.
    # `min` 이 "겹쳐 받기"와 "따라잡기"를 한 식으로 처리한다.
    desired = min(last_stored - step * (scope.gap_lookback_bars - 1), lookback_start)
    span_bars = int((end - desired) / step) + 1
    if span_bars <= scope.max_catchup_bars:
        return desired, end, False

    capped = end - step * (scope.max_catchup_bars - 1)
    return capped, end, True


async def collect_one(
    adapter: BrokerAdapter,
    repository: CandleRepository,
    scope: BackfillScope,
    thresholds: IntegrityThresholds,
    symbol: str,
    timeframe: Timeframe,
    *,
    now: datetime | None = None,
) -> CollectionResult:
    """한 종목 x TF 를 수집하고 무결성을 검사한다.

    Args:
        adapter: 시세 어댑터.
        repository: 캔들 저장소.
        scope: 수집 범위 설정.
        thresholds: 무결성 임계값.
        symbol: 마켓 코드.
        timeframe: 시간축.
        now: 기준 시각. None 이면 현재.

    Returns:
        수집 결과.

    Raises:
        InstrumentNotFoundError: 시드되지 않은 종목.

    Note:
        무결성 이슈는 **중복 억제**로 적재한다 (`record_issues(dedupe_open=True)`).
        주기 수집이 5분마다 같은 구간을 검사하므로, 억제 없이는 메울 수 없는 구멍 하나가
        하루 288행을 만든다.
    """
    moment = now or datetime.now(UTC)
    instrument_id, instrument = await repository.resolve_instrument(Market.UPBIT, symbol)

    last_stored = await repository.last_ts(instrument_id, timeframe)
    start, end, truncated = resolve_window(last_stored, moment, timeframe, scope)

    if truncated:
        remedy = (
            f"scripts/runtime/backfill_cli.py --symbol {symbol} "
            f"--timeframe {timeframe.value} --anchor"
        )
        _logger.error(
            "candle_gap_exceeds_catchup_limit",
            payload={
                "symbol": symbol,
                "timeframe": timeframe.value,
                "last_stored": last_stored.isoformat() if last_stored else None,
                "collecting_from": start.isoformat(),
                "max_catchup_bars": scope.max_catchup_bars,
                "action": f"주기 수집으로 메울 수 없는 크기다 — `{remedy}` 실행 필요",
            },
        )

    result = await run_backfill(
        adapter,
        repository,
        instrument,
        instrument_id,
        timeframe,
        start,
        end,
        now=moment,
        # 주기 수집의 목적이 **최근 구간 재조회**이므로 재개 건너뛰기를 끈다.
        resume=False,
    )

    candles = await repository.fetch_candles(instrument, instrument_id, timeframe, start, end)
    report = inspect_candles(candles, thresholds=thresholds)
    recorded = (
        await repository.record_issues(instrument_id, report.issues, dedupe_open=True)
        if not report.is_clean
        else 0
    )

    _logger.info(
        "candle_collected",
        payload={
            "symbol": symbol,
            "timeframe": timeframe.value,
            "range": f"{start.isoformat()}~{end.isoformat()}",
            "stored": result.stored,
            "inspected": report.inspected,
            "issues_total": report.total_issues,
            "issues_recorded": recorded,
        },
    )
    return CollectionResult(
        symbol=symbol,
        timeframe=timeframe,
        stored=result.stored,
        window_start=start,
        window_end=end,
        catchup_truncated=truncated,
        issues_recorded=recorded,
    )


def _collected_here(symbol: str) -> bool:
    """이 수집기(업비트 어댑터)가 맡는 심볼인가.

    Args:
        symbol: 유니버스 심볼.

    Returns:
        업비트 종목이면 True. **등록되지 않은 심볼도 True** — 건너뛰면 조용히 사라지고,
        남겨 두면 `collect_one` 이 종목별 실패로 보고한다 (절대 규칙 #8).
    """
    try:
        return to_instrument(symbol).market is Market.UPBIT
    except UnknownSymbolError:
        return True


async def collect_timeframe(
    adapter: BrokerAdapter,
    repository: CandleRepository,
    scope: BackfillScope,
    thresholds: IntegrityThresholds,
    timeframe: Timeframe,
    *,
    now: datetime | None = None,
) -> CollectionRun:
    """유니버스 전체를 한 시간축에 대해 수집한다 (스케줄러가 부르는 단위).

    Args:
        adapter: 시세 어댑터.
        repository: 캔들 저장소.
        scope: 수집 범위 설정.
        thresholds: 무결성 임계값.
        timeframe: 시간축.
        now: 기준 시각. None 이면 현재.

    Returns:
        실행 결과.

    Note:
        **한 종목이 실패해도 나머지를 계속 수집한다.** BTC 수집이 실패했다고 ETH 를
        건너뛸 이유가 없고, 잡 전체가 죽으면 다음 주기까지 아무것도 안 들어온다.

        다만 **실패를 삼키지는 않는다** — `failures` 로 돌려주고 로그에 남긴다.
        예외를 올리지 않는 이유는 스케줄러가 잡 실패로 처리하면 나머지 종목의 성공까지
        묻히기 때문이다 (spec §7 — 실패 사실은 보고하되 전체를 멈추지 않는다).
    """
    results: list[CollectionResult] = []
    failures: list[tuple[str, str]] = []

    # 🔴 **주기 수집기는 업비트 어댑터 하나로 돈다** — 유니버스의 KRX·NASDAQ·Gate
    #    종목까지 UPBIT 로 조회하면 15분마다 16건씩 "시드 안 됨" 오류가 난다
    #    (2026-08-22 정전 복구 중 발견). 시장은 universe.py 의 매핑이 정한다.
    #    다른 시장은 오류가 아니라 **수집 대상 아님**이고, 한 줄로만 남긴다.
    # ⚠️ **모르는 심볼은 건너뛰지 않는다** — 그것은 "다른 시장"이 아니라 "시드 안 됨"이고,
    #    종목별 실패(`seed_instruments` 안내)로 보고돼야 한다. 여기서 터뜨리면 한 심볼이
    #    잡 전체를 죽인다 — 이 함수의 첫 약속을 깬다 (CI 가 잡았다: 2026-08-22).
    skipped = [name for name in scope.universe if not _collected_here(name)]
    if skipped:
        _logger.info(
            "candle_collection_skipped_non_upbit",
            payload={"timeframe": timeframe.value, "symbols": skipped},
        )

    for symbol in scope.universe:
        if symbol in skipped:
            continue
        try:
            results.append(
                await collect_one(
                    adapter, repository, scope, thresholds, symbol, timeframe, now=now
                )
            )
        except InstrumentNotFoundError as exc:
            failures.append((symbol, str(exc)))
            _logger.error(
                "candle_collection_symbol_not_seeded",
                payload={"symbol": symbol, "timeframe": timeframe.value, "error": str(exc)},
            )
        except Exception as exc:
            failures.append((symbol, f"{type(exc).__name__}: {exc}"))
            _logger.error(
                "candle_collection_failed",
                payload={
                    "symbol": symbol,
                    "timeframe": timeframe.value,
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": "나머지 종목은 계속 수집한다",
                },
            )

    run = CollectionRun(timeframe=timeframe, results=tuple(results), failures=tuple(failures))
    _logger.info(
        "candle_collection_run_finished",
        payload={
            "timeframe": timeframe.value,
            "symbols": len(scope.universe),
            "succeeded": len(results),
            "failed": len(failures),
            "stored": run.stored,
            "needs_backfill": run.needs_backfill,
        },
    )
    return run
