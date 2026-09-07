/**
 * 자본 곡선 차트 — 펀드 · 시장 지수를 **배수(×)** 로 로그 축에 겹친다. 청산 시각에 표식.
 *
 * +386,925% 와 −40% 를 한 축에 놓으려면 로그가 필요하고, 로그 축은 양수만 받는다 — 그래서 % 가 아니라 배수다
 * (×1 = 원금). 툴팁·축 라벨은 배수로 적는다.
 */
import { useEffect, useRef } from "react";
import {
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { onThemeChange, palette } from "./theme";

export interface EquityLine {
  label: string;
  color: string;
  /** 배수 — 1 이 원금. */
  values: number[];
  width?: 1 | 2;
  dashed?: boolean;
}

type Props = {
  t: number[];
  lines: EquityLine[];
  marks?: { time: number; label: string }[];
  height?: number;
};

export function EquityChart({ t, lines, marks, height = 280 }: Props) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (holder.current === null) return;
    const c = palette();
    const made = createChart(holder.current, {
      height,
      autoSize: true,
      layout: { background: { color: c.bg }, textColor: c.text, attributionLogo: false },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { mode: PriceScaleMode.Logarithmic },
      timeScale: { timeVisible: false },
      crosshair: { mode: 0 },
      localization: { priceFormatter: (v: number) => `×${v >= 10 ? v.toFixed(0) : v.toFixed(2)}` },
    });
    chart.current = made;
    let first: ReturnType<IChartApi["addSeries"]> | null = null;
    for (const line of lines) {
      const s = made.addSeries(LineSeries, {
        color: line.color,
        lineWidth: line.width ?? 2,
        lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        title: line.label,
        priceFormat: { type: "custom", formatter: (v: number) => `×${v >= 10 ? v.toFixed(0) : v.toFixed(2)}`, minMove: 0.0001 },
      });
      const data = t
        .map((time, i) => ({ time: time as Time, value: line.values[i] ?? Number.NaN }))
        .filter((p) => Number.isFinite(p.value) && p.value > 0);
      s.setData(data);
      if (first === null) first = s;
    }
    if (first !== null && marks && marks.length) {
      const rows: SeriesMarker<Time>[] = marks
        .map((m) => ({
          time: nearest(t, m.time) as Time,
          position: "aboveBar" as const,
          shape: "square" as const,
          color: c.down,
          text: m.label,
          size: 1,
        }))
        .sort((a, b) => (a.time as number) - (b.time as number));
      createSeriesMarkers(first, rows);
    }
    made.timeScale().fitContent();
    const off = onThemeChange(() => {
      const p = palette();
      made.applyOptions({
        layout: { background: { color: p.bg }, textColor: p.text },
        grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      });
    });
    return () => {
      off();
      chart.current = null;
      made.remove();
    };
  }, [t, lines, marks, height]);

  return <div ref={holder} style={{ width: "100%", height }} />;
}

/** 축소된 격자에서 가장 가까운 점 시각 — 마커는 자료 점 위에만 붙는다. */
function nearest(t: number[], time: number): number {
  if (t.length === 0) return time;
  let lo = 0;
  let hi = t.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((t[mid] ?? 0) < time) lo = mid + 1;
    else hi = mid;
  }
  const a = t[lo] ?? time;
  const b = t[Math.max(0, lo - 1)] ?? a;
  return Math.abs(a - time) <= Math.abs(b - time) ? a : b;
}
