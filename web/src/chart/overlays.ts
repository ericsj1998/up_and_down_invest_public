/**
 * 지표 선을 캔버스에 올린다 — `Indicator.compute` 의 결과를 lightweight-charts 시리즈로.
 *
 * 지표는 **무엇을** 그릴지만 말하고(`OverlaySeries`), 여기는 **어떻게** 올리는지만 안다. 콘솔 차트(`Chart.tsx`)와
 * 리포트 차트(`PriceChart.tsx`)가 같은 함수를 쓰므로 "모든 차트" 가 같은 지표를 같은 모양으로 그린다.
 */
import { LineSeries, LineStyle, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import type { Indicator, Ohlc, OverlaySeries } from "./indicators/types";

export type OverlayHandle = { key: string; series: ISeriesApi<"Line"> };

/** 지표 전부를 계산해 선 묶음으로. */
export function computeOverlays(indicators: readonly Indicator[], bars: readonly Ohlc[]): OverlaySeries[] {
  const out: OverlaySeries[] = [];
  for (const ind of indicators) out.push(...ind.compute(bars));
  return out;
}

/**
 * 이전에 올린 선을 걷고 새 선을 올린다. 돌려주는 손잡이를 다음 호출에 넘긴다.
 *
 * ⚠️ 시리즈를 매번 새로 만든다 — 지표 수가 한 자리라 비용이 없고, 키가 바뀌는 경우(설정 변경)를 따로 다루지 않아
 *    단순하다. 봉이 초 단위로 바뀌는 콘솔에서는 봉 배열이 바뀔 때만 부른다(만드는 중 봉은 안 본다).
 */
export function applyOverlays(
  chart: IChartApi,
  held: readonly OverlayHandle[],
  overlays: readonly OverlaySeries[],
): OverlayHandle[] {
  for (const h of held) {
    try {
      chart.removeSeries(h.series);
    } catch {
      // 차트가 이미 지워졌으면 걷을 것도 없다.
    }
  }
  const next: OverlayHandle[] = [];
  for (const o of overlays) {
    if (o.points.length === 0) continue;
    const series = chart.addSeries(LineSeries, {
      color: o.color,
      lineWidth: o.width,
      lineStyle: o.dashed ? LineStyle.Dashed : LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    series.setData(o.points.map((p) => ({ time: p.time as Time, value: p.value })));
    next.push({ key: o.key, series });
  }
  return next;
}

/** 범례용 — 같은 지표의 상·중·하는 하나로 묶는다. */
export function legend(overlays: readonly OverlaySeries[]): { label: string; color: string }[] {
  const seen = new Map<string, string>();
  for (const o of overlays) {
    const base = o.label.replace(/ (상단|중심|하단)$/, "");
    if (!seen.has(base)) seen.set(base, o.color);
  }
  return [...seen.entries()].map(([label, color]) => ({ label, color }));
}
