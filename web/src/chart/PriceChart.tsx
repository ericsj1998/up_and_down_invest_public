/**
 * 가격 차트 (리포트용) — 봉 + 지표 겹치기 + 매매 표기. TradingView Lightweight Charts.
 *
 * 콘솔의 `Chart.tsx` 는 라이브(만드는 중 봉 · 판정 커서 · 대기 띠)를 위한 것이고, 이것은 **끝난 자료**(합성 미래 ·
 * 백테스트)를 보는 차트다. 둘이 공유하는 것: 지표(`overlays.ts`) · 색(`theme.ts`) · 매매 표기 규칙(`trades.ts`).
 *
 * 표기(사용자 요구 2026-09-06 · 설정으로 켜고 끈다):
 *   1. 진입 타점  2. 롱/숏  3. 익절/청산 위치  4. 진입 시각·가격  5. 손절선
 *   + 선택한 매매의 손절 구간(붉은) · 익절 구간(초록) 영역. 영역·가로선은 **선택한 매매 하나**에만 — 전부 칠하면 안 보인다.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { buildEnabled, type ChartSettings, type Ohlc } from "./indicators";
import { applyOverlays, computeOverlays, legend, type OverlayHandle } from "./overlays";
import { onThemeChange, palette, wash } from "./theme";
import { tradeLevels, tradeMarkers, tradeZones, type TradeMark } from "./trades";
import { ZonesPrimitive } from "./ZonesPrimitive";

type Props = {
  bars: Ohlc[];
  /** 봉 간격(초) — 마커를 봉 시작으로 내리는 데 쓴다. */
  step: number;
  trades?: TradeMark[];
  /** 선택한 매매 — 가로선·영역은 이것에만. 비면 매매가 하나일 때 그것. */
  focusId?: string | null;
  settings: ChartSettings;
  height?: number;
  /** 아래에 적을 한 줄 (자료 출처 · 주의). */
  note?: string;
};

const TONES = { entry: "entry", gain: "up", loss: "down" } as const;

/** 가격 자릿수 — 마지막 종가 기준. 1,000 이상 0 · 10 이상 2 · 1 이상 3 · 그 아래 5 (fmtPrice 와 같은 규칙). */
export function pricePrecision(bars: readonly Ohlc[]): number {
  const v = bars[bars.length - 1]?.close ?? 1;
  return v >= 1000 ? 0 : v >= 10 ? 2 : v >= 1 ? 3 : 5;
}

export function PriceChart({ bars, step, trades, focusId, settings, height = 420, note }: Props) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const zones = useRef<ZonesPrimitive | null>(null);
  const badges = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const overlays = useRef<OverlayHandle[]>([]);
  const levels = useRef<IPriceLine[]>([]);
  const [ready, setReady] = useState(0);

  // ── 판 만들기 (한 번) ──
  useEffect(() => {
    if (holder.current === null) return;
    const c = palette();
    const made = createChart(holder.current, {
      height,
      autoSize: true,
      layout: { background: { color: c.bg }, textColor: c.text, attributionLogo: true },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      timeScale: { timeVisible: step < 86_400, secondsVisible: false, barSpacing: 7, minBarSpacing: 1 },
      crosshair: { mode: 0 },
    });
    const candles = made.addSeries(CandlestickSeries, {
      upColor: c.up,
      downColor: c.down,
      wickUpColor: c.up,
      wickDownColor: c.down,
      borderVisible: false,
    });
    const rug = new ZonesPrimitive();
    candles.attachPrimitive(rug);
    chart.current = made;
    series.current = candles;
    zones.current = rug;
    badges.current = createSeriesMarkers(candles, []);
    overlays.current = [];
    levels.current = [];
    setReady((n) => n + 1);
    const off = onThemeChange(() => {
      const p = palette();
      made.applyOptions({
        layout: { background: { color: p.bg }, textColor: p.text },
        grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      });
      candles.applyOptions({ upColor: p.up, downColor: p.down, wickUpColor: p.up, wickDownColor: p.down });
    });
    return () => {
      off();
      badges.current = null;
      zones.current = null;
      series.current = null;
      chart.current = null;
      overlays.current = [];
      levels.current = [];
      made.remove();
    };
  }, [height, step]);

  // ── 봉 ──
  useEffect(() => {
    const drawn = series.current;
    const made = chart.current;
    if (drawn === null || made === null) return;
    // 축 자릿수는 가격 크기에 맞춘다 — ADA(0.04)를 소수 둘째 자리로 찍으면 손절 0.0476 과 진입 0.0447 이 같은 눈금이 된다.
    const precision = pricePrecision(bars);
    drawn.applyOptions({ priceFormat: { type: "price", precision, minMove: 10 ** -precision } });
    drawn.setData(bars.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })));
    made.timeScale().fitContent();
  }, [bars, ready]);

  // ── 지표 ──
  const overlaySeries = useMemo(
    () => computeOverlays(buildEnabled(settings.indicators), bars),
    [settings.indicators, bars],
  );
  useEffect(() => {
    const made = chart.current;
    if (made === null) return;
    overlays.current = applyOverlays(made, overlays.current, overlaySeries);
  }, [overlaySeries, ready]);

  // ── 매매 표기 ──
  const focus = useMemo<TradeMark | null>(() => {
    const list = trades ?? [];
    if (focusId) return list.find((t) => t.id === focusId) ?? null;
    return list.length === 1 ? (list[0] ?? null) : null;
  }, [trades, focusId]);

  useEffect(() => {
    const plugin = badges.current;
    if (plugin === null) return;
    const c = palette();
    const rows: SeriesMarker<Time>[] = tradeMarkers(trades ?? [], step, settings.marks, focus?.id ?? null).map((m) => ({
      time: m.time as Time,
      position: m.position,
      shape: m.shape,
      color: c[TONES[m.tone]],
      text: m.text,
      size: m.size,
    }));
    plugin.setMarkers(rows);
  }, [trades, step, settings.marks, focus, ready]);

  useEffect(() => {
    const drawn = series.current;
    const rug = zones.current;
    if (drawn === null || rug === null) return;
    for (const line of levels.current) drawn.removePriceLine(line);
    levels.current = [];
    if (focus === null) {
      rug.set([]);
      return;
    }
    const c = palette();
    levels.current = tradeLevels(focus, settings.marks).map((l) =>
      drawn.createPriceLine({
        price: l.price,
        color: c[TONES[l.tone]],
        lineWidth: 1,
        lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
        axisLabelVisible: true,
        title: l.title,
      }),
    );
    rug.set(
      tradeZones(focus, step, settings.marks).map((z) => ({
        from: z.from,
        to: z.to,
        low: z.low,
        high: z.high,
        color: wash(z.tone === "gain" ? c.up : c.down, z.alpha),
      })),
    );
    // 선택한 매매 주변으로 본다 — 앞뒤로 여유를 둬 진입 전 맥락이 보이게.
    const made = chart.current;
    if (made !== null && bars.length > 0) {
      const span = Math.max(focus.closedTs - focus.openedTs, step * 20);
      const from = focus.openedTs - span * 1.5;
      const to = focus.closedTs + span * 1.0;
      const first = bars[0]?.time ?? from;
      const last = bars[bars.length - 1]?.time ?? to;
      made.timeScale().setVisibleRange({
        from: Math.max(first, from) as Time,
        to: Math.min(last, to) as Time,
      });
    }
  }, [focus, step, settings.marks, bars, ready]);

  const shown = legend(overlaySeries);
  return (
    <div>
      <div ref={holder} style={{ width: "100%", height }} />
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-blue-gray-500 dark:text-blue-gray-300">
        {shown.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-4" style={{ background: s.color }} aria-hidden="true" />
            {s.label}
          </span>
        ))}
        {shown.length ? <span className="text-blue-gray-400">· 표시용 지표 (판정값과 다를 수 있다)</span> : null}
        {note ? <span className="ml-auto font-mono text-blue-gray-400">{note}</span> : null}
      </div>
    </div>
  );
}
