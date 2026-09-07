"""비용 실측 — 호가창 샘플링 → 분포 리포트 → 손익분기 슬리피지 (세션 인계 §5-1).

## 무엇을 답하는가

D1-6 의 슬리피지 **가정**(편도 10bp)이 필요 승률표의 **모든 셀에 분모로** 들어가 있다.
15m 판정이 그 가정에 따라 18%p 갈리므로(수수료만 39.6% vs 가정 57.5%) 가정을 측정으로
바꾸는 것이 판정보다 먼저다.

## 세 가지 함정을 코드로 막는다

**① bid-ask bounce 이중 계산.** 체결 틱으로 "N초 후 가격 이동"을 재면 중앙값이 스프레드와
거의 같게 나온다 — 연속 체결이 매수·매도를 번갈아 때리기 때문이며 어떤 간격으로 재도
그렇다. 그것을 슬리피지로 더하면 스프레드를 두 번 센다. 이 스크립트는 **체결 틱을 아예
쓰지 않는다.** 모든 수치가 호가창의 `mid` 기준이다 (`common/costs.py` 모듈 docstring).

**② 과거 스프레드는 측정 불가.** 업비트 공개 API 는 현재 호가만 준다. 그래서 결과를
"가정이 맞았다"로 쓰지 않고 **손익분기 슬리피지 대비 여유 배수**로 낸다 (§손익분기 절).

**③ 어댑터 직접 생성.** 반드시 `MarketDataProvider.adapter_for()` 를 지난다 (절대 규칙 #0).
진단용으로 `UpbitClient` 를 직접 쓴 스크립트는 저장소에 넣지 않았다.

## 사용법

```bash
# 짧은 표본 (즉시 결과)
uv run python -u scripts/runtime/measure_costs.py --minutes 30 --interval 10

# 시간대별 분포 — 24시간, 백그라운드
setsid nohup uv run python -u scripts/runtime/measure_costs.py \
    --hours 24 --interval 60 --json logs/measurements/costs_24h.json \
    > logs/measure_costs.log 2>&1 < /dev/null &
watch -n 5 make progress

# 샘플링 없이 판정만 (config/costs.yml 의 값을 쓴다)
uv run python scripts/runtime/measure_costs.py --no-sample \
    --viability logs/measurements/axisA_15m_direct_sweep.json
```

## 손익분기 프레이밍

> 측정된 이행률이 P 라면, 그 전략이 견딜 수 있는 **최대 슬리피지는 얼마인가?**

이 역산에는 슬리피지 가정이 들어가지 않는다. 실측치와 비교하면 답이 "가정이 맞았나"가
아니라 **"여유가 몇 배인가"** 가 되고, 그것은 가정값에서 독립이다.
"""

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# 재정리(2026-09-06): scripts/ 하위 폴더끼리 import — 자기 폴더 · scripts/ · runtime/ · research/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _progress import ProgressRecorder

from updown.common.costs import (
    BPS,
    CostMeasurementError,
    MarketCosts,
    SlippageSample,
    breakeven_slippage,
    load_cost_table,
    required_win_rate,
    sample_slippage,
)
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.marketdata.adapter import Capability, OrderBookAdapter
from updown.marketdata.provider import MarketDataProvider

KST = timezone(timedelta(hours=9))
"""표시용 한국 시각.

⭕ **고정 오프셋이 여기서는 정확하다** — 한국은 1988년 이후 서머타임이 없다. 절대 규칙 #7 이
금지하는 것은 **미국 장 시각의 하드코딩**이며(DST 때문에 tz DB 가 필요하다 — C2-3), 저장은
여전히 UTC 다. 여기서 KST 를 쓰는 이유는 "새벽 3시에 스프레드가 벌어지는가"를 사람이 읽을
수 있어야 하기 때문이다.
"""

K_CANDIDATES: tuple[Decimal, ...] = (Decimal("1.5"), Decimal("2.0"), Decimal("2.5"))
"""ATR 손절 배수 후보 — `config/risk.yml` 과 같은 3개다 (축 G-k). 하나를 고르지 않는다."""

WIN_RATE_GRID: tuple[Decimal, ...] = (
    Decimal("0.40"),
    Decimal("0.45"),
    Decimal("0.50"),
    Decimal("0.55"),
    Decimal("0.60"),
)
"""손익분기 슬리피지를 낼 이행률 후보들.

⛔ **예측이 아니다.** 15m x A2 의 실제 이행률은 §5-4 에서 측정한다. 여기서는 "P 가 이 값이면
슬리피지 여유가 얼마"인지를 미리 깔아 두어, 측정치가 나오면 표에서 바로 읽게 한다.
"""

DEFAULT_NOTIONALS = "10000000,50000000"
"""측정할 주문 규모(원). 1천만이 현실 규모이고 5천만은 깊이 여유를 보는 대조군이다.

**첫 번째 금액이 대표값**이다 — 시간대별 리포트와 `config/costs.yml` 블록이 그것을 쓴다.
"""


def _instrument(symbol: str) -> Instrument:
    """업비트 종목 객체 — 이름은 표시용이라 코드에서 유도한다."""
    return Instrument(
        market=Market.UPBIT,
        symbol=symbol,
        name=symbol.removeprefix("KRW-"),
        asset_type=AssetType.COIN,
        currency=Currency.KRW,
    )


def _percentile(values: Sequence[Decimal], q: float) -> Decimal:
    """정렬 후 `q` 분위 값 (선형 보간 없음 — 가장 가까운 순위).

    Note:
        보간하지 않는 이유: 표본이 실제로 관측된 호가창이므로, 존재하지 않는 중간값을
        만들어 내는 것보다 **실제 본 값** 하나를 고르는 편이 해석이 명확하다.
    """
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class Distribution:
    """(종목, 규모) 하나의 슬리피지 분포.

    Attributes:
        symbol: 종목 코드.
        notional: 주문 금액.
        samples: 표본 수.
        incomplete_buy: 매수 방향 깊이가 모자랐던 표본 수.
        incomplete_sell: 매도 방향 깊이가 모자랐던 표본 수 — **청산 리스크**다.
        spread_bps_median: 스프레드 중앙값.
        half_spread_bps_median: 스프레드/2 중앙값 — 금액과 무관한 고정 비용.
        depth_bps_median: 깊이충격 중앙값 (매수·매도 평균).
        one_way_bps_median: 편도 슬리피지 중앙값.
        one_way_bps_p90: 편도 슬리피지 90분위 — **보수적 판정에 쓸 값**.
        one_way_bps_max: 편도 슬리피지 최댓값.
    """

    symbol: str
    notional: Decimal
    samples: int
    incomplete_buy: int
    incomplete_sell: int
    spread_bps_median: Decimal
    half_spread_bps_median: Decimal
    depth_bps_median: Decimal
    one_way_bps_median: Decimal
    one_way_bps_p90: Decimal
    one_way_bps_max: Decimal


def summarize(samples: Sequence[SlippageSample]) -> Distribution:
    """표본들을 분포로 접는다.

    Args:
        samples: 같은 (종목, 규모)의 표본들. 비어 있으면 안 된다.

    Returns:
        분포 요약.

    Raises:
        ValueError: 표본이 비어 있는 경우.

    Note:
        **중앙값과 90분위를 함께 낸다.** 중앙값만 보면 평상시 비용이고, 판정은 나쁜
        구간에서도 성립해야 하므로 90분위가 필요하다. 평균을 쓰지 않는 이유는 급변
        구간의 스프레드가 평균을 끌어올려 평상시를 대표하지 못하기 때문이다.
    """
    if not samples:
        raise ValueError("표본이 비어 있다 — 빈 분포를 만들면 0bp 로 오해된다")
    one_way = [sample.average_one_way_bps for sample in samples]
    depth = [(sample.buy_depth_impact_bps + sample.sell_depth_impact_bps) / 2 for sample in samples]
    return Distribution(
        symbol=samples[0].symbol,
        notional=samples[0].notional,
        samples=len(samples),
        incomplete_buy=sum(1 for sample in samples if not sample.buy_is_complete),
        incomplete_sell=sum(1 for sample in samples if not sample.sell_is_complete),
        spread_bps_median=_percentile([s.spread_bps for s in samples], 0.5),
        half_spread_bps_median=_percentile([s.half_spread_bps for s in samples], 0.5),
        depth_bps_median=_percentile(depth, 0.5),
        one_way_bps_median=_percentile(one_way, 0.5),
        one_way_bps_p90=_percentile(one_way, 0.9),
        one_way_bps_max=max(one_way),
    )


async def collect_samples(
    symbols: Sequence[str],
    notionals: Sequence[Decimal],
    *,
    duration: timedelta,
    interval: float,
    recorder: ProgressRecorder,
) -> tuple[list[SlippageSample], int]:
    """호가창을 주기적으로 받아 표본을 모은다.

    Args:
        symbols: 종목 코드들.
        notionals: 측정할 주문 금액들.
        duration: 총 샘플링 시간.
        interval: 라운드 간격(초).
        recorder: 진행률 기록기.

    Returns:
        `(표본들, 거부된 스냅샷 수)`.

    Raises:
        SystemExit: 조회 어댑터가 호가를 지원하지 않는 경우.

    Note:
        **`MarketDataProvider.adapter_for()` 를 지난다** (절대 규칙 #0). 어댑터를 직접
        생성하면 정적 검사(`tests/test_gateway_bypass.py`)가 잡는다.

        스냅샷 하나가 교차 호가라도 전체를 죽이지 않는다 — 거부 개수를 세어 리포트에
        남긴다. 조용히 버리면 "측정이 잘 됐다"와 "절반이 버려졌다"가 구분되지 않는다
        (절대 규칙 #8).
    """
    instruments = [_instrument(symbol) for symbol in symbols]
    samples: list[SlippageSample] = []
    rejected = 0
    rounds = max(1, int(duration.total_seconds() // interval))
    deadline = time.monotonic() + duration.total_seconds()

    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(Market.UPBIT)
        if Capability.ORDERBOOK not in adapter.capabilities or not isinstance(
            adapter, OrderBookAdapter
        ):
            raise SystemExit(
                "이 조회 어댑터는 호가를 주지 않는다 — 슬리피지를 측정할 수 없다. "
                "가정값으로 진행하지 않는다 (절대 규칙 #8)"
            )

        completed = 0
        while time.monotonic() < deadline:
            started = time.monotonic()
            for instrument in instruments:
                try:
                    book = await adapter.get_orderbook(instrument)
                except Exception as error:  # 장시간 작업을 한 번의 실패로 죽이지 않는다
                    rejected += 1
                    print(f"  ⚠️ {instrument.symbol} 조회 실패: {error}", flush=True)
                    continue
                for notional in notionals:
                    try:
                        samples.append(sample_slippage(book, notional))
                    except CostMeasurementError as error:
                        rejected += 1
                        print(f"  ⚠️ 표본 거부: {error}", flush=True)

            completed += 1
            recorder.update(completed)
            elapsed = time.monotonic() - started
            remaining = min(interval - elapsed, deadline - time.monotonic())
            if remaining > 0:
                await asyncio.sleep(remaining)

    recorder.update(rounds, force=True)
    return samples, rejected


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------


def report_distribution(samples: Sequence[SlippageSample], rejected: int) -> list[Distribution]:
    """분포 리포트를 표준출력에 쓴다.

    Args:
        samples: 모은 표본들.
        rejected: 거부된 스냅샷 수.

    Returns:
        (종목, 규모)별 분포들.
    """
    print("\n" + "=" * 78)
    print("비용 실측 — 호가창 분포 (§5-1)")
    print("=" * 78)
    print(f"  표본 {len(samples):,}건 · 거부 {rejected}건")
    print("  ⚠️ 전부 mid 기준이다 — 체결가끼리 비교하지 않으므로 bounce 이중 계산이 없다")

    groups: dict[tuple[str, Decimal], list[SlippageSample]] = {}
    for sample in samples:
        groups.setdefault((sample.symbol, sample.notional), []).append(sample)

    distributions: list[Distribution] = []
    for key in sorted(groups, key=lambda item: (item[0], item[1])):
        dist = summarize(groups[key])
        distributions.append(dist)
        shortfalls: list[str] = []
        if dist.incomplete_buy:
            shortfalls.append(f"매수 {dist.incomplete_buy}건")
        if dist.incomplete_sell:
            shortfalls.append(f"매도 {dist.incomplete_sell}건 ⚠️ 청산 리스크")
        warn = f" · 30단계로 못 채움: {' / '.join(shortfalls)}" if shortfalls else ""
        print(f"\n  {dist.symbol} · {dist.notional:,.0f}원 — 표본 {dist.samples:,}건{warn}")
        print(
            f"    스프레드 중앙   {dist.spread_bps_median:6.2f}bp"
            f"  (편도 절반 {dist.half_spread_bps_median:5.2f}bp)"
        )
        print(f"    깊이충격 중앙   {dist.depth_bps_median:6.2f}bp")
        print(
            f"    편도 슬리피지   중앙 {dist.one_way_bps_median:6.2f}bp"
            f" · p90 {dist.one_way_bps_p90:6.2f}bp"
            f" · 최대 {dist.one_way_bps_max:6.2f}bp"
        )
    return distributions


def report_hourly(samples: Sequence[SlippageSample], notional: Decimal) -> None:
    """KST 시간대별 편도 슬리피지 중앙값.

    Args:
        samples: 모은 표본들.
        notional: 이 규모의 표본만 본다.

    Note:
        시간대를 보는 이유는 **진입 시각이 비용을 바꾸는가**를 알기 위해서다. 새벽에
        스프레드가 두 배로 벌어진다면 그 시간대를 피하는 것이 룰을 고치는 것보다 싸다.
    """
    picked = [sample for sample in samples if sample.notional == notional]
    if not picked:
        return
    span_hours = len({sample.as_of.astimezone(KST).hour for sample in picked})
    print(f"\n  시간대별 (KST · {notional:,.0f}원) — 관측된 시간대 {span_hours}개")
    if span_hours < 24:
        print("    ⚠️ 24시간을 다 돌지 않았다 — 시간대 결론을 내기에는 부분 표본이다")

    by_hour: dict[int, list[Decimal]] = {}
    for sample in picked:
        by_hour.setdefault(sample.as_of.astimezone(KST).hour, []).append(sample.average_one_way_bps)
    for hour in sorted(by_hour):
        values = by_hour[hour]
        median = _percentile(values, 0.5)
        bar = "#" * min(40, int(median * 4))
        print(f"    {hour:02d}시  {median:6.2f}bp  n={len(values):>4}  {bar}")


@dataclass(frozen=True, slots=True)
class ViabilityRow:
    """측정 리포트에서 읽은 셀 하나.

    Attributes:
        label: 표시용 이름 (`15m/direct_sweep` 등).
        timeframe: 시간축.
        atr_pct: ATR / 가격 중앙값.
        tp_multiple: 익절 거리 / ATR 중앙값.
        entered: 진입 표본 수.
    """

    label: str
    timeframe: str
    atr_pct: Decimal
    tp_multiple: Decimal
    entered: int


def load_viability(path: Path) -> list[ViabilityRow]:
    """`timeframe_viability.py` 가 남긴 JSON 에서 셀들을 읽는다.

    Args:
        path: JSON 경로.

    Returns:
        탐지 표본이 있는 행들.

    Raises:
        SystemExit: 파일 형식이 예상과 다른 경우.

    Note:
        수치를 손으로 옮겨 적지 않는 것이 목적이다. 인계 문서 §2-3 의 표는 사람이 읽는
        사본이고, 판정 산수는 **원본 JSON** 에서 돌아야 베끼기 오류가 없다.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"{path} 를 읽을 수 없다: {error}") from error
    if not isinstance(raw, list):
        raise SystemExit(f"{path} 는 배열이어야 한다 — timeframe_viability.py 의 출력이 맞는가")

    rows: list[ViabilityRow] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        detection = entry.get("detection")
        if not isinstance(detection, dict):
            continue
        tp_raw = detection.get("tp_atr_multiple")
        if tp_raw is None:
            continue
        rows.append(
            ViabilityRow(
                # 파일 이름을 라벨에 넣는다 — BTC 와 ETH 의 15m 행은 `timeframe`·`source` 가
                # 똑같아서 파일을 여러 개 넘기면 어느 종목인지 구분되지 않는다. 종목별
                # 필요 승률이 다르므로(ETH 61.7% vs BTC 57.5%) 구분이 판정에 필요하다.
                label=f"{path.stem} · {entry.get('timeframe')} ({entry.get('source')})",
                timeframe=str(entry.get("timeframe")),
                atr_pct=Decimal(str(entry["atr_over_price"])),
                tp_multiple=Decimal(str(tp_raw)),
                entered=int(detection.get("entered", 0)),
            )
        )
    if not rows:
        raise SystemExit(f"{path} 에 탐지 표본이 있는 행이 없다 — 판정할 대상이 없다")
    return rows


def report_verdict(
    rows: Sequence[ViabilityRow],
    costs: MarketCosts,
    measured_one_way_bps: Decimal | None,
    min_sample: int,
) -> None:
    """실측 비용에서의 필요 승률 + 손익분기 슬리피지를 낸다.

    Args:
        rows: 측정된 셀들.
        costs: 비용 테이블의 해당 시장 구성.
        measured_one_way_bps: 이번 샘플링의 편도 슬리피지 중앙값. 없으면 설정값을 쓴다.
        min_sample: 최소 표본 기준 (spec §12.9).

    Note:
        **두 방향을 함께 낸다.** 필요 승률은 "비용을 갚으려면 몇 %를 맞혀야 하나"이고,
        손익분기 슬리피지는 "몇 %를 맞히면 슬리피지를 얼마까지 견디나"다. 같은 식의
        양변이지만 전자는 비용 가정에 의존하고 후자는 **의존하지 않는다** — 과거
        스프레드를 알 수 없으므로 후자가 최종 근거다.
    """
    one_way_pct = (
        measured_one_way_bps / BPS
        if measured_one_way_bps is not None
        else costs.slippage_pct_one_way
    )
    round_trip = costs.fee_and_tax_round_trip_pct + one_way_pct * 2
    origin = "이번 실측" if measured_one_way_bps is not None else f"설정({costs.slippage_source})"

    print("\n" + "=" * 78)
    print("판정 — 실측 비용에서 성립하는가 (§P1-8-0b Q1)")
    print("=" * 78)
    print(
        f"  수수료·세금 왕복 {costs.fee_and_tax_round_trip_pct * 100:.3f}%"
        f" + 슬리피지 편도 {one_way_pct * 100:.4f}% x2  ⇒  "
        f"**왕복 {round_trip * 100:.3f}%** ({origin})"
    )

    for row in rows:
        short = f"  ⚠️ 표본 미달 ({row.entered} < {min_sample})"
        enough = "" if row.entered >= min_sample else short
        print(f"\n  {row.label} — 진입 {row.entered}건{enough}")
        print(f"    ATR/가격 {row.atr_pct * 100:.3f}% · 익절 {row.tp_multiple:.2f}xATR (실측)")

        cells: list[str] = []
        for k in K_CANDIDATES:
            need = required_win_rate(row.tp_multiple, k, round_trip, row.atr_pct)
            mark = " ⛔" if need > 1 else ""
            cells.append(f"k={k}: {need * 100:5.1f}%{mark}")
        print("    필요 승률       " + " · ".join(cells))

        print("    손익분기 편도 슬리피지 — 이행률 P 가 이 값이면 견딜 수 있는 한도")
        for win_rate in WIN_RATE_GRID:
            parts: list[str] = []
            for k in K_CANDIDATES:
                result = breakeven_slippage(
                    win_rate,
                    row.tp_multiple,
                    k,
                    row.atr_pct,
                    costs.fee_and_tax_round_trip_pct,
                )
                limit_bps = result.slippage_one_way_pct * BPS
                headroom = result.headroom(one_way_pct)
                if not result.is_feasible:
                    parts.append(f"k={k}: ⛔ 수수료만으로 적자")
                elif headroom is None:
                    parts.append(f"k={k}: {limit_bps:.1f}bp")
                else:
                    parts.append(f"k={k}: {limit_bps:6.1f}bp ({headroom:4.1f}배)")
            print(f"      P={win_rate * 100:4.0f}%  " + " · ".join(parts))

    print(
        "\n  ⚠️ 필요 승률은 **낙관값**이다 — 1차 익절 50% 부분청산(§6.3)을 전량으로 계산한다."
        "\n  ⚠️ 배수는 **현재 시장 대비** 여유다. 과거 구간의 스프레드는 측정할 수 없다."
    )


def emit_yaml(dist: Distribution, measured_at: datetime, window: str) -> None:
    """`config/costs.yml` 에 붙일 블록을 출력한다.

    Args:
        dist: 채택할 분포 — **현실 규모에서 가장 비싼 종목**의 것이다. 비용 테이블 한 줄이
            모든 코인에 적용되므로 평균을 쓰면 비싼 종목이 조용히 과소 계상된다.
        measured_at: 측정 시각 (UTC).
        window: 측정 창 설명.

    Note:
        손으로 옮겨 적으면 자릿수를 틀리고 **측정 시각을 빼먹는다.** 출처 없는 비용은
        나중에 근거를 잃으므로(§5.6.2) 생성해서 붙인다.
    """
    one_way = dist.one_way_bps_median / BPS
    print("\n" + "-" * 78)
    print("config/costs.yml 에 붙일 블록 (UPBIT)")
    print("-" * 78)
    print(f"""    slippage_pct_one_way: {one_way:.8f}   # {dist.one_way_bps_median:.2f}bp
    slippage_source: measured
    measured_at: {measured_at.strftime("%Y-%m-%dT%H:%M:%SZ")}
    source: >-
      업비트 /orderbook 30단계 스냅샷 {dist.samples:,}건 ({window}).
      {dist.symbol} {dist.notional:,.0f}원 시장가 기준 편도 중앙값
      = 스프레드/2 {dist.half_spread_bps_median:.2f}bp + 깊이충격 {dist.depth_bps_median:.2f}bp.
      p90 {dist.one_way_bps_p90:.2f}bp · 최대 {dist.one_way_bps_max:.2f}bp.""")


def _payload(
    distributions: Sequence[Distribution], samples: Sequence[SlippageSample], rejected: int
) -> dict[str, object]:
    """JSON 출력 — 문서에 옮길 때 손으로 베끼지 않게 한다."""
    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "sample_count": len(samples),
        "rejected": rejected,
        "distributions": [
            {
                "symbol": dist.symbol,
                "notional": str(dist.notional),
                "samples": dist.samples,
                "incomplete_buy": dist.incomplete_buy,
                "incomplete_sell": dist.incomplete_sell,
                "spread_bps_median": str(dist.spread_bps_median),
                "half_spread_bps_median": str(dist.half_spread_bps_median),
                "depth_bps_median": str(dist.depth_bps_median),
                "one_way_bps_median": str(dist.one_way_bps_median),
                "one_way_bps_p90": str(dist.one_way_bps_p90),
                "one_way_bps_max": str(dist.one_way_bps_max),
            }
            for dist in distributions
        ],
        "samples": [
            {
                "symbol": sample.symbol,
                "as_of": sample.as_of.isoformat(),
                "notional": str(sample.notional),
                "mid": str(sample.mid),
                "spread_bps": str(sample.spread_bps),
                "buy_one_way_bps": str(sample.buy_one_way_bps),
                "sell_one_way_bps": str(sample.sell_one_way_bps),
                "buy_is_complete": sample.buy_is_complete,
                "sell_is_complete": sample.sell_is_complete,
            }
            for sample in samples
        ],
    }


async def main() -> int:
    """CLI 진입점.

    Returns:
        종료 코드.
    """
    parser = argparse.ArgumentParser(description="비용 실측 + 손익분기 슬리피지 (§5-1)")
    parser.add_argument("--symbols", default="KRW-BTC,KRW-ETH")
    parser.add_argument("--notionals", default=DEFAULT_NOTIONALS, help="측정할 주문 금액 (원)")
    parser.add_argument("--minutes", type=float, default=0.0)
    parser.add_argument("--hours", type=float, default=0.0)
    parser.add_argument("--interval", type=float, default=10.0, help="라운드 간격(초)")
    parser.add_argument("--no-sample", action="store_true", help="샘플링 없이 설정값으로 판정")
    parser.add_argument("--viability", type=Path, action="append", help="측정 JSON (반복 가능)")
    parser.add_argument("--min-sample", type=int, default=30, help="최소 표본 (spec §12.9)")
    parser.add_argument("--costs", type=Path, help="비용 테이블 경로")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--job", default="costs", help="진행률 파일 이름")
    args = parser.parse_args()

    symbols = [value.strip() for value in str(args.symbols).split(",") if value.strip()]
    notionals = [
        Decimal(value.strip()) for value in str(args.notionals).split(",") if value.strip()
    ]
    if not symbols or not notionals:
        print("종목과 금액이 각각 하나 이상 필요하다", file=sys.stderr)
        return 2

    duration = timedelta(hours=args.hours or 0) + timedelta(minutes=args.minutes or 0)
    if not args.no_sample and duration.total_seconds() <= 0:
        duration = timedelta(minutes=5)
        print("  기간을 주지 않아 5분으로 잡는다 (--minutes / --hours)")

    samples: list[SlippageSample] = []
    distributions: list[Distribution] = []
    rejected = 0
    measured_one_way: Decimal | None = None
    started_at = datetime.now(UTC)

    if not args.no_sample:
        rounds = max(1, int(duration.total_seconds() // args.interval))
        print(
            f"  샘플링 시작 — {', '.join(symbols)} · {duration} · "
            f"{args.interval:.0f}초 간격 · 약 {rounds:,}라운드",
            flush=True,
        )
        with ProgressRecorder(args.job, rounds, unit="라운드") as recorder:
            samples, rejected = await collect_samples(
                symbols,
                notionals,
                duration=duration,
                interval=args.interval,
                recorder=recorder,
            )
        if not samples:
            print("표본을 하나도 모으지 못했다 — 네트워크와 종목 코드를 확인하라", file=sys.stderr)
            return 1
        distributions = report_distribution(samples, rejected)
        report_hourly(samples, notionals[0])
        # 대표값 = 현실 규모(첫 번째 금액)에서 **가장 비싼 종목**의 중앙값.
        #
        # 종목 간 평균이 아니라 최댓값을 쓴다. 비용 테이블은 한 줄이고 그 한 줄이 모든
        # 코인에 적용되므로, 평균을 쓰면 비싼 종목이 **조용히 과소 계상**된다 — 실측에서
        # ETH 편도가 BTC 의 2~3배였다. 판정은 나쁜 쪽에서 성립해야 보수적이다.
        #
        # ⚠️ 판정(`report_verdict`)과 설정 블록(`emit_yaml`)이 **같은 값**을 써야 한다.
        #    다르면 "리포트에서는 통과했는데 설정에는 다른 수가 들어가는" 상태가 된다.
        primary = [dist for dist in distributions if dist.notional == notionals[0]]
        if primary:
            worst = max(primary, key=lambda dist: dist.one_way_bps_median)
            measured_one_way = worst.one_way_bps_median
            emit_yaml(
                worst,
                started_at,
                f"{started_at.astimezone(KST):%Y-%m-%d %H:%M} KST 부터 {duration}",
            )

    if args.viability:
        try:
            costs = load_cost_table(args.costs).for_market(Market.UPBIT)
        except Exception as error:  # 메시지가 곧 사용자 안내다
            print(f"비용 테이블을 읽을 수 없다: {error}", file=sys.stderr)
            return 2
        rows: list[ViabilityRow] = []
        for path in args.viability:
            rows.extend(load_viability(path))
        report_verdict(rows, costs, measured_one_way, args.min_sample)

    if args.json and samples:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(_payload(distributions, samples, rejected), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"\n  JSON → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
