"""견본 매매법 근거 — 이동평균 교차(`sample_ma_cross`)를 적재된 캔들 위에서 봉인 걸음으로 돌려 요약을 쓴다.

    uv run python scripts/build/sample_evidence.py                 # 6종 · 전체 구간 → config/evidence/sample_backtest.json
    uv run python scripts/build/sample_evidence.py --symbols BTC_USDT --out /tmp/x.json

왜 있나 (사용자 2026-09-08): 감사 권한이 없는 사람(게스트·대기·열람자)에게 백테스트 리포트가 가려진 칸만 보여
"애매한 결과" 였다. 공개 저장소에 그대로 나가는 견본 매매법의 결과는 감출 것이 없으므로, 그 사람들에게는 **이것을
기본으로 보여 주고** 노란 카드로 "감사 권한이 없어 표준 매매법만 보인다" 고 알린다. 공개본의 근거 화면도 이것으로 찬다.

무엇을 돌리나: 라이브·모의와 **같은** `Session`/`SealedFeed` (연구용 펀드 엔진이 아니다). 4h 판정 · D1 국면 · 워밍업
600봉 뒤부터 끝까지 봉인. 원장이 준 값(청산 매매 · 승/패 · 실현 · 최대 낙폭 · 펀딩 모형)을 그대로 적는다 — 새 계산은
자본 곡선을 청산 시각마다 찍는 것뿐이다.

⛔ 엣지 주장 아님 · 측정 없음. 교과서 규칙이 이 플랫폼에서 어떻게 도는지 보여 주는 견본이다.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from updown.analysis.detectors.registry import SetupRegistry
from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import Market, Timeframe
from updown.marketdata.ingest.repository import CandleRepository
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "config" / "evidence" / "sample_backtest.json"
PLAYBOOK_ID = "sample_ma_cross"
SYMBOLS = ("BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "DOGE_USDT", "ADA_USDT")
WARMUP_BARS = 600
SEED = Decimal(10_000)
POINTS = 400
REGISTRY = SetupRegistry.from_plugins(load_rules())
"""한 번만 만든다 — 없으면 Session 이 걸음마다 다시 만든다."""


def _dsn() -> str:
    """접속 문자열 — `UPDOWN_DB_DSN`, 없으면 `.env.dev` 의 DATABASE_URL (localhost:5433)."""
    dsn = os.environ.get("UPDOWN_DB_DSN")
    if dsn:
        return dsn
    env = ROOT / ".env.dev"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit(".env.dev 에 DATABASE_URL 이 없다")


def _downsample(points: list[tuple[str, float]], limit: int) -> list[tuple[str, float]]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    picked = [points[int(i * step)] for i in range(limit)]
    if picked[-1] != points[-1]:
        picked.append(points[-1])
    return picked


async def _run_symbol(
    repo: CandleRepository, symbol: str, market: Market, *, days: int | None = None
) -> dict[str, object]:
    instrument_id, instrument = await repo.resolve_instrument(market, symbol)
    lo = datetime(2000, 1, 1, tzinfo=UTC)
    hi = datetime(2100, 1, 1, tzinfo=UTC)
    h4 = await repo.fetch_candles(instrument, instrument_id, Timeframe.H4, lo, hi)
    d1 = await repo.fetch_candles(instrument, instrument_id, Timeframe.D1, lo, hi)
    # D1 이 4h 보다 짧게 적재된 종목(ADA 실측: 하루 뒤짐)은 봉인 끝을 D1 끝에 맞춘다 — SealedFeed 는 모자라면 터진다.
    d1_edge = d1[-1].ts + timedelta(days=1)
    h4 = [bar for bar in h4 if bar.ts <= d1_edge]
    if len(h4) <= WARMUP_BARS + 10:
        raise SystemExit(
            f"{symbol}: 4h 봉이 {len(h4)}개 — 워밍업 {WARMUP_BARS} 뒤에 남는 것이 없다"
        )
    book = {item.playbook_id: item for item in load_playbooks()}[PLAYBOOK_ID]
    first = WARMUP_BARS
    if days is not None:
        # 최근 N일만 봉인 — 시간을 재거나 짧은 견본을 만들 때. 워밍업은 그 앞 600봉.
        first = max(WARMUP_BARS, len(h4) - days * 6)
        h4 = h4[first - WARMUP_BARS :]
        first = WARMUP_BARS
    seal = Seal(start=h4[first].ts, end=h4[-1].ts)
    session = Session(
        instrument=instrument,
        playbooks=(book,),
        feed=SealedFeed({Timeframe.H4: h4, Timeframe.D1: d1}, seal),
        ledger=Ledger(seed_cash=SEED),
        # 걸음 축 = 판정 축(4h). 5m 봉이 없어 봉 안 경로를 못 본다 — 같은 봉 진입·익절은 낙관으로 읽는다 (기억: intrabar).
        step_frame=Timeframe.H4,
        registry=REGISTRY,
    )
    curve: list[tuple[str, float]] = [(seal.start.isoformat(), float(SEED))]
    seen_closed = 0
    started = time.monotonic()
    for _ in range(400_000):
        if session.finished:
            break
        session.step()
        closed = session.ledger.closed
        if len(closed) > seen_closed:
            seen_closed = len(closed)
            curve.append(
                (
                    closed[-1].closed_at.isoformat()
                    if closed[-1].closed_at
                    else seal.end.isoformat(),
                    float(session.ledger.equity),
                )
            )
    ledger = session.ledger
    closed = ledger.closed
    equity = ledger.equity
    total_pct = float((equity / SEED - 1) * 100)
    years = max((seal.end - seal.start).days / 365.25, 1e-9)
    cagr = (float(equity / SEED) ** (1 / years) - 1) * 100 if equity > 0 else -100.0
    losses = len(closed) - ledger.wins
    print(
        f"  {symbol}: 4h {len(h4)} · 봉인 {seal.start:%Y-%m-%d}~{seal.end:%Y-%m-%d} · 매매 {len(closed)} "
        f"(승 {ledger.wins}/패 {losses}) · 손익 {total_pct:+.1f}% · MDD {float(ledger.max_drawdown_pct):.1f}% "
        f"· {time.monotonic() - started:.0f}s",
        file=sys.stderr,
    )
    return {
        "symbol": symbol,
        "market": market.value,
        "bars_4h": len(h4),
        "start": seal.start.isoformat(),
        "end": seal.end.isoformat(),
        "years": round(years, 2),
        "trades": len(closed),
        "wins": ledger.wins,
        "losses": losses,
        "win_rate_pct": round(ledger.wins / len(closed) * 100, 1) if closed else None,
        "total_pct": round(total_pct, 2),
        "cagr_pct": round(cagr, 2),
        "mdd_pct": round(float(ledger.max_drawdown_pct), 2),
        "funding_settlements": int(session.funnel.get("funding:settlements", 0)),
        "equity": _downsample(curve, POINTS),
    }


async def _main(
    symbols: tuple[str, ...], out: Path, market: Market, days: int | None = None
) -> int:
    engine = create_engine(_dsn())
    try:
        repo = CandleRepository(create_session_factory(engine))
        # 종목 하나가 터져도 앞의 결과를 잃지 않도록 종목마다 부분 파일에 적어 둔다 (6종 ≈ 20분).
        partial = out.with_suffix(".partial.json")
        done: dict[str, dict[str, object]] = {}
        if partial.exists():
            cached = json.loads(partial.read_text(encoding="utf-8"))
            if cached.get("days") == days:
                done = dict(cached.get("rows", {}))
        rows: list[dict[str, object]] = []
        for symbol in symbols:
            if symbol not in done:
                done[symbol] = await _run_symbol(repo, symbol, market, days=days)
                partial.write_text(
                    json.dumps({"days": days, "rows": done}, ensure_ascii=False), encoding="utf-8"
                )
            rows.append(done[symbol])
    finally:
        await engine.dispose()
    book = {item.playbook_id: item for item in load_playbooks()}[PLAYBOOK_ID]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "playbook": f"{book.playbook_id}@{book.version}",
        "label": book.label,
        "engine": "Session/SealedFeed — 라이브·모의 라이브와 같은 코드 · 4h 판정 · D1 국면 · 워밍업 600봉 뒤 전 구간 봉인 · 걸음 4h(5m 없음 → 봉 안 경로 없음 · 같은 봉 진입·익절은 낙관)",
        "seed_cash": float(SEED),
        "rules": [
            "견본 매매법이다 — 엣지 주장이 아니고 측정으로 고른 값도 아니다 (SMA20/50 교차 · 손절 2xATR · 목표 2R).",
            "수익률엔 MDD 를 병기한다 · 레버리지 1 (λ=1) · 수수료·슬리피지는 config/costs.yml · 펀딩은 8h 모형.",
            "실측 봉 한 경로의 값이지 기댓값이 아니다 — 합성 미래·표본 검정은 없다.",
        ],
        "symbols": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    partial.unlink(missing_ok=True)
    print(f"wrote {out} ({len(rows)} symbols)", file=sys.stderr)
    return 0


def main() -> int:
    """인자를 읽고 돈다."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--symbols", default=",".join(SYMBOLS), help="쉼표로 종목들")
    parser.add_argument("--market", default="GATE")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--days", type=int, default=None, help="최근 N일만 (시간 재기 · 짧은 견본)")
    args = parser.parse_args()
    symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
    return asyncio.run(_main(symbols, Path(args.out), Market(str(args.market)), args.days))


if __name__ == "__main__":
    sys.exit(main())
