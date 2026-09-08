"""백필 CLI (P0-8-2·3·5 · spec §4.2, §12.1).

실행 예:
    # 정규 수집 — 고정 앵커부터 전 조합 (P0-8-7 DoD). 오래 걸리므로 백그라운드 권장
    uv run python scripts/runtime/backfill_cli.py --full

    # 한 조합만 고정 앵커부터
    uv run python scripts/runtime/backfill_cli.py --symbol KRW-BTC --timeframe 5m --anchor

    # 임시 확인 — 최근 1일 5m (288봉 기대, P0-8-2)
    uv run python scripts/runtime/backfill_cli.py --symbol KRW-BTC --timeframe 5m --days 1

    # 무결성 검사만 (적재 없이)
    uv run python scripts/runtime/backfill_cli.py --symbol KRW-BTC --timeframe 5m \\
        --days 7 --check-only

    # 커버리지 (DoD 1)
    uv run python scripts/runtime/backfill_cli.py --coverage

`--timeframe` 값은 `Timeframe` 열거형 문자열과 **같다** (`5m/15m/1h/4h/1d`) — 어긋나면
"문서상 되는데 CLI 는 안 되는" 상태가 된다 (plan D-8 주석).

> ⚠️ **정규 수집은 `--anchor` / `--full` 이다.** `--days` 는 실행 시점마다 시작점이 밀려
> 커버리지가 재현되지 않으므로 임시 확인에만 쓴다 (D-15).

재실행이 안전하다. 이미 채워진 창은 건너뛴다 (P0-8-3).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# 재정리(2026-09-06): scripts/ 하위 폴더끼리 import — 자기 폴더 · scripts/ · runtime/ · research/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _progress import ProgressRecorder

from updown.common.config import load_settings
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import Instrument, Timeframe
from updown.common.logging.setup import configure_logging, get_logger
from updown.marketdata.ingest.backfill import (
    DEFAULT_SCOPE_PATH,
    BackfillScope,
    days_ago,
    run_backfill,
)
from updown.marketdata.ingest.coverage_check import CoverageVerdict, verdict_for
from updown.marketdata.ingest.integrity import (
    DEFAULT_CONFIG_PATH,
    IntegrityReport,
    IntegrityThresholds,
    inspect_candles,
)
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.sessions import (
    ALWAYS_OPEN_MARKETS,
    SessionCalendar,
    SessionCalendarError,
)
from updown.marketdata.ingest.timeframes import expected_bar_count, last_closed_ts
from updown.marketdata.ingest.universe import to_instrument
from updown.marketdata.provider import MarketDataProvider

_logger = get_logger("scripts.backfill_cli")

DEFAULT_REPORT_DIR = Path("logs/integrity")
"""무결성 리포트 저장 위치. `logs/` 는 gitignore 대상이다 (종목·수량 정보, spec §8)."""

DENSITY_REFERENCES = (Timeframe.M5, Timeframe.M15)
"""밀도 비교의 기준 후보 — **촘촘한 순**이다.

같은 종목의 더 촘촘한 시간축을 기준으로 삼으면 휴장일 달력을 모델링하지 않고도
"이 시간축만 성기다"를 잡을 수 있다. 다른 종목과 비교하면 상장일·휴장일이 달라
밀도가 의미를 잃는다.
"""


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 정의."""
    parser = argparse.ArgumentParser(description="캔들 백필 + 무결성 검사 (P0-8)")
    parser.add_argument("--symbol", help="업비트 마켓 코드 (예: KRW-BTC)")
    parser.add_argument(
        "--timeframe",
        choices=[tf.value for tf in Timeframe],
        help="시간축. Timeframe 열거형과 같은 문자열이다",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="며칠 전부터 받을지 (기본 1). **임시 확인용** — 실행 시점마다 하한이 밀린다",
    )
    parser.add_argument(
        "--anchor",
        action="store_true",
        help="config/backfill.yml 의 **고정** history_anchor 부터 받는다 (정규 수집, D-15)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="유니버스 x 시간축 전체를 고정 앵커부터 받고 커버리지까지 출력한다 (P0-8-7 DoD)",
    )
    parser.add_argument("--start", help="시작 시각 ISO8601 UTC. 주면 --anchor/--days 를 무시한다")
    parser.add_argument("--end", help="끝 시각 ISO8601 UTC. 기본은 현재")
    parser.add_argument(
        "--scope", type=Path, default=DEFAULT_SCOPE_PATH, help="수집 범위 YAML (앵커·유니버스)"
    )
    parser.add_argument(
        "--check-only", action="store_true", help="적재하지 않고 이미 있는 캔들만 검사한다"
    )
    parser.add_argument(
        "--no-resume", action="store_true", help="이미 채워진 창도 다시 받는다 (강제 재수집)"
    )
    parser.add_argument("--coverage", action="store_true", help="커버리지만 출력하고 종료 (DoD 1)")
    parser.add_argument(
        "--thresholds", type=Path, default=DEFAULT_CONFIG_PATH, help="무결성 임계값 YAML"
    )
    parser.add_argument(
        "--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="무결성 리포트 저장 위치"
    )
    return parser


def parse_moment(raw: str) -> datetime:
    """ISO8601 문자열을 UTC aware datetime 으로 만든다.

    Args:
        raw: `2026-08-01T00:00:00Z` 또는 오프셋 있는 ISO8601.

    Returns:
        UTC aware datetime.

    Raises:
        SystemExit: 파싱 불가 또는 타임존 표기 없음.

    Note:
        **타임존 없는 입력을 거부한다.** UTC 로 가정하면 사용자가 KST 로 적었을 때 9시간
        어긋난 구간을 조용히 받는다 (spec §12.3, 절대 규칙 #7).
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"시각을 해석할 수 없다: {raw!r} ({exc})") from exc
    if parsed.tzinfo is None:
        raise SystemExit(
            f"타임존을 명시하라: {raw!r} → '2026-08-01T00:00:00Z' 처럼 (spec §12.3). "
            "UTC 로 가정하면 KST 로 적은 값이 9시간 어긋난 채 조용히 통과한다"
        )
    return parsed.astimezone(UTC)


def print_report(report: IntegrityReport, symbol: str, timeframe: Timeframe) -> None:
    """무결성 리포트를 사람이 읽는 형태로 출력한다."""
    print(f"\n=== 무결성 검사 · {symbol} {timeframe.value} ===")
    print(f"  검사 봉 수: {report.inspected}")
    truncated_note = " (표시는 일부만 — 상한 초과)" if report.truncated else ""
    print(f"  위반: {report.total_issues}건{truncated_note}")

    if report.is_clean:
        print("  ✅ 0건 — DoD 2 충족")
        return

    counts: dict[str, int] = {}
    for issue in report.issues:
        counts[issue.issue_type.value] = counts.get(issue.issue_type.value, 0) + 1
    print("  종류별:")
    for issue_type, count in sorted(counts.items()):
        print(f"    {issue_type}: {count}")

    print("  상위 10건:")
    for issue in report.issues[:10]:
        span = (
            issue.ts_start.isoformat()
            if issue.ts_start == issue.ts_end
            else f"{issue.ts_start.isoformat()}~{issue.ts_end.isoformat()}"
        )
        detail = json.dumps(issue.detail, ensure_ascii=False)
        print(f"    [{issue.issue_type.value}] {span} {detail}")

    print(
        "\n  ⚠️ Phase 0 은 기록만 하고 분석을 차단하지 않는다 (D-9).\n"
        "     오탐이면 config/candle_integrity.yml 을 조정하고 "
        "docs/rules/candle_integrity_rules.md 에 근거를 남긴다."
    )


def save_report(
    report: IntegrityReport, symbol: str, timeframe: Timeframe, report_dir: Path
) -> Path:
    """리포트를 JSON 파일로 저장한다.

    Args:
        report: 검사 결과.
        symbol: 종목 코드.
        timeframe: 시간축.
        report_dir: 저장 디렉터리.

    Returns:
        저장된 파일 경로.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"{symbol}_{timeframe.value}_{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "timeframe": timeframe.value,
                "inspected": report.inspected,
                "total_issues": report.total_issues,
                "truncated": report.truncated,
                "issues": [
                    {
                        "issue_type": issue.issue_type.value,
                        "ts_start": issue.ts_start.isoformat(),
                        "ts_end": issue.ts_end.isoformat(),
                        "detail": issue.detail,
                    }
                    for issue in report.issues
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=repr,
        ),
        encoding="utf-8",
    )
    return path


async def show_coverage(repository: CandleRepository) -> None:
    """커버리지를 출력한다 (P0-8-7 DoD 1)."""
    rows = await repository.coverage()
    if not rows:
        print("적재된 캔들이 없다.")
        return

    print(f"\n{'종목':<10} {'TF':<5} {'봉 수':>9}  {'first_ts':<26} {'last_ts':<26}")
    print("-" * 82)
    for row in rows:
        first = row.first_ts.isoformat() if row.first_ts else "-"
        last = row.last_ts.isoformat() if row.last_ts else "-"
        print(f"{row.symbol:<10} {row.timeframe.value:<5} {row.bars:>9,}  {first:<26} {last:<26}")


async def resolve_sessions(
    repository: CandleRepository,
    instrument: Instrument,
    instrument_id: int,
    start: datetime,
    end: datetime,
) -> SessionCalendar:
    """이 종목의 거래일 달력을 만든다.

    Args:
        repository: 저장소.
        instrument: 대상 종목.
        instrument_id: 종목 PK.
        start: 구간 시작.
        end: 구간 끝.

    Returns:
        24시간 장이면 `always()`, 주식이면 적재된 일봉에서 만든 달력.

    Raises:
        SessionCalendarError: 주식인데 일봉이 적재돼 있지 않은 경우.

    Note:
        **일봉을 네트워크가 아니라 DB 에서 읽는다.** 결측 검사는 순수 함수여야 하고
        (integrity 모듈 docstring), 검사할 때마다 브로커를 부르면 같은 데이터에 대해
        다른 결과가 나올 수 있다 — 원칙 P1 위반이다.

        일봉이 없으면 조용히 24시간 장으로 넘어가지 않고 예외를 던진다. 그 경우
        **1d 백필을 먼저 돌리라**는 것이 정확한 답이고, 야간을 전부 결측으로 보고하는
        리포트는 답이 아니다 (절대 규칙 #8).
    """
    if instrument.market in ALWAYS_OPEN_MARKETS:
        return SessionCalendar.always()
    daily = await repository.fetch_candles(instrument, instrument_id, Timeframe.D1, start, end)
    if not daily:
        raise SessionCalendarError(
            f"{instrument.symbol} 의 일봉이 적재돼 있지 않아 거래일 달력을 만들 수 없다. "
            f"먼저 `--timeframe 1d` 로 백필하라 — 일봉이 곧 거래일 달력이다 (P1 §1-0j)"
        )
    return SessionCalendar.from_daily(daily)


async def verify_coverage(
    repository: CandleRepository,
    instrument: Instrument,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
) -> CoverageVerdict:
    """요청 구간 대비 **실제 적재**를 판정한다 (§1-0t).

    Args:
        repository: 저장소.
        instrument: 도메인 종목.
        timeframe: 검사할 시간축.
        start: 요청 시작.
        end: 요청 끝.

    Returns:
        판정. 밀도 비교용 기준 시간축이 없으면 경계만 본다.

    Note:
        밀도의 기준을 **같은 종목의 더 촘촘한 시간축**에서 찾는다 (15m → 5m → 1m 순).
        다른 종목과 비교하면 휴장일·상장일이 달라 밀도가 의미를 잃는다.
    """
    rows = {
        item.timeframe: item
        for item in await repository.coverage()
        if item.symbol == instrument.symbol
    }
    own = rows.get(timeframe)
    reference = next(
        (tf for tf in DENSITY_REFERENCES if tf != timeframe and tf in rows),
        None,
    )
    return verdict_for(
        symbol=instrument.symbol,
        timeframe=timeframe,
        requested_start=start,
        requested_end=end,
        stored_start=own.first_ts if own else None,
        stored_end=own.last_ts if own else None,
        bars=own.bars if own else 0,
        reference_bars=rows[reference].bars if reference else None,
        reference=reference,
    )


async def run_one(
    repository: CandleRepository,
    args: argparse.Namespace,
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    now: datetime,
    thresholds: IntegrityThresholds,
    on_window: Callable[[int, int], None] | None = None,
) -> bool:
    """한 종목 x TF 를 백필하고 검사한다.

    Args:
        repository: 저장소.
        args: CLI 인자.
        symbol: 마켓 코드.
        timeframe: 시간축.
        start: 시작 (UTC).
        end: 끝 (UTC).
        now: 마감 판정 기준 시각.
        thresholds: 무결성 임계값.

    Returns:
        무결성 위반이 없으면 True.
    """
    # 시장은 유니버스 표가 정한다 — 심볼 형식으로 추측하지 않는다 (`universe.py`).
    market = to_instrument(symbol).market
    instrument_id, instrument = await repository.resolve_instrument(market, symbol)

    if not args.check_only:
        # 미마감 봉은 적재하지 않으므로 기대 수도 **마감 경계 기준**으로 보여 준다.
        # `end` 그대로 세면 헤더(368)와 결과(367)가 갈라져 "1봉 빠졌나" 로 읽힌다.
        closed_end = min(end, last_closed_ts(now, timeframe))
        expected = expected_bar_count(start, closed_end, timeframe)
        print(
            f"\n백필: {symbol} {timeframe.value} "
            f"{start.isoformat()} ~ {closed_end.isoformat()} (마감 기준 기대 {expected:,}봉)"
        )
        # 어댑터는 `MarketDataProvider` 로 얻는다 — 조회 전용 획득 지점 (절대 규칙 #0).
        # `scripts/` 는 정적 검사 범위 밖이지만, 검사가 못 보는 곳에서 규약을 흐리면
        # 그 규약이 "검사에 걸리지 않으면 된다"로 퇴화한다.
        async with MarketDataProvider() as provider:
            result = await run_backfill(
                provider.adapter_for(market),
                repository,
                instrument,
                instrument_id,
                timeframe,
                start,
                end,
                now=now,
                resume=not args.no_resume,
                on_window=on_window,
            )
        print(
            f"  창 {result.windows}개 (건너뜀 {result.skipped_windows}) · "
            f"수신 {result.fetched:,} · 적재 {result.stored:,} · "
            f"미완성 제외 {result.dropped_unclosed} · 기대 {result.expected:,}"
        )

    candles = await repository.fetch_candles(instrument, instrument_id, timeframe, start, end)

    # 주식은 매일 밤·주말에 정상적으로 구멍이 난다. 거래일 달력을 주지 않으면 그 구멍이
    # 전부 결측으로 잡혀 리포트가 무의미해진다 (P1 §1-0j).
    #
    # 달력의 재료는 **같은 종목·같은 구간의 일봉**이다. 일봉을 먼저 적재해야 하므로
    # `timeframes` 순서에 1d 가 앞서야 하는 것은 아니고(여기서 직접 읽는다), 1d 백필이
    # 한 번은 돌아 있어야 한다.
    sessions = await resolve_sessions(repository, instrument, instrument_id, start, end)
    report = inspect_candles(candles, thresholds=thresholds, sessions=sessions)
    print_report(report, symbol, timeframe)

    if not report.is_clean:
        recorded = await repository.record_issues(instrument_id, report.issues)
        path = save_report(report, symbol, timeframe, args.report_dir)
        print(f"  candle_quality_issues 적재: {recorded}건")
        print(f"  리포트 저장: {path}")

    # 🔴 **요청 구간을 실제로 채웠는지**는 위 검사가 답하지 않는다 (§1-0t).
    #    `inspect_candles` 는 **받아 온 봉들 안의** 구멍을 보고, 이것은 **못 받아 온
    #    구간**을 본다. 후자가 없어서 AAPL 1h 이 46봉만 받고도 "완료" 로 보고됐다.
    verdict = await verify_coverage(repository, instrument, timeframe, start, end)
    print(f"\n=== 적재 정합성 · {symbol} {timeframe.value} ===")
    print(verdict.render())
    return report.is_clean and verdict.ok


def resolve_range(
    args: argparse.Namespace, scope: BackfillScope, now: datetime
) -> tuple[datetime, datetime]:
    """시작·끝 시각을 정한다.

    Args:
        args: CLI 인자.
        scope: 수집 범위 설정.
        now: 현재 시각.

    Returns:
        `(start, end)`.

    Note:
        우선순위는 `--start` > `--anchor`/`--full` > `--days` 다.

        **`--days` 는 임시 확인용이다** — 실행 시점마다 하한이 밀려 커버리지가 재현되지
        않는다 (D-15). 정규 수집은 고정 앵커를 쓴다.
    """
    end = parse_moment(args.end) if args.end else now
    if args.start:
        return parse_moment(args.start), end
    if args.anchor or args.full:
        return scope.history_anchor, end
    return days_ago(end, days=args.days), end


async def main() -> None:
    """백필·검사·커버리지를 수행한다."""
    args = build_parser().parse_args()
    settings = load_settings()
    configure_logging(settings.log_level)

    engine = create_engine(settings.database_url)
    repository = CandleRepository(create_session_factory(engine))

    try:
        if args.coverage:
            await show_coverage(repository)
            return

        scope = BackfillScope.load(args.scope)
        thresholds = IntegrityThresholds.load(args.thresholds)
        now = datetime.now(UTC)
        start, end = resolve_range(args, scope, now)

        if args.full:
            targets = [(symbol, tf) for symbol in scope.universe for tf in scope.timeframes]
            print(
                f"전체 수집 (P0-8-7): {len(targets)}조합 · 고정 앵커 "
                f"{scope.history_anchor.isoformat()} ~ 현재"
            )
        else:
            if not args.symbol or not args.timeframe:
                raise SystemExit("--symbol 과 --timeframe 이 필요하다 (또는 --full / --coverage)")
            targets = [(args.symbol, Timeframe(args.timeframe))]

        # 조합 단위 진행률 — `--full` 은 종목 x 시간축이라 수십 분이 걸린다.
        #
        # 봉 단위가 아니라 **조합 단위**로 센다. 백필은 브로커 페이지네이션이라 남은 봉
        # 수를 미리 알 수 없고, 알 수 있는 척하면 ETA 가 거짓말이 된다 (`make progress`).
        clean = True
        with ProgressRecorder("backfill", len(targets), unit="조합") as recorder:
            for index, (symbol, timeframe) in enumerate(targets):
                # 🔴 창마다 상태 파일을 다시 쓴다. 조합 하나가 수 분 걸리는데(주식 분봉은
                # 거래일마다 페이지가 끊겨 특히 느리다) 조합 단위로만 찍으면 그 사이가
                # 통째로 무신호라 프론트가 **멈춤과 구분하지 못한다** — 실제로 매번
                # '멈춤 의심'이 떴다.
                #
                # `done` 은 그대로 두고 파일만 갱신한다. 창 진행을 완료 수에 섞으면
                # 조합 단위 퍼센트가 거짓이 된다.
                def touch(current: int, total: int, done: int = index) -> None:
                    recorder.note = f"창 {current}/{total}"
                    recorder.update(done, force=True)

                clean &= await run_one(
                    repository, args, symbol, timeframe, start, end, now, thresholds, touch
                )
                recorder.note = ""
                recorder.update(index + 1, force=True)

        if args.full:
            print("\n########## 커버리지 (DoD 1) ##########")
            await show_coverage(repository)
            if clean:
                print("\n✅ 전 조합 위반 0건 — DoD 2 충족")
            else:
                print(
                    "\n⚠️ 위반이 있다 — 위 리포트를 확인한다.\n"
                    "   거래소 자체에 없는 봉(재수집으로도 안 메워짐)이면 status='ignored' 로\n"
                    "   내려야 한다. 그러지 않으면 차단 승격 후 해당 구간 분석이 영구 차단된다\n"
                    "   (docs/rules/candle_integrity_rules.md §5)."
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
