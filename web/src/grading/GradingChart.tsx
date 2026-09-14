/**
 * 채점 차트(dev · T281) — 봉 + 지표(BB 20/2 · BB 4/4 · EMA) + 엔진 매매 표기 + 사람이 그린 포지션 + 클릭·끌기.
 *
 * `PriceChart.tsx`(끝난 자료 · 읽기 전용)와 다른 점 셋:
 *   1. 봉 배열이 **재생 커서에 따라 바뀐다** — 마지막 봉은 만드는 중 봉이고 지표도 그 봉을 포함해 다시 계산된다.
 *   2. 클릭으로 진입가·시각을 잡고(`onPlace`), 선택한 수기 포지션의 진입·손절·목표 선을 **끌 수 있다**(`onDrag` ·
 *      `Chart.tsx` 의 계획선과 같은 방식 — 라이브러리에 끄는 선이 없어 마우스를 직접 듣는다).
 *   3. 수기 포지션은 전부 영역으로 칠한다(진입↔손절 붉게 · 진입↔목표 초록) — 엔진 매매는 선택한 하나만.
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
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { buildEnabled, type IndicatorSpec, type Ohlc } from "../chart/indicators";
import { applyOverlays, computeOverlays, legend, type OverlayHandle } from "../chart/overlays";
import { pricePrecision } from "../chart/PriceChart";
import { onThemeChange, palette, wash } from "../chart/theme";
import { tradeLevels, tradeMarkers, tradeZones, type TradeMark } from "../chart/trades";
import { ZonesPrimitive, type Rect } from "../chart/ZonesPrimitive";
import { positionZones, type UserPosition } from "./grading";

export type DragKey = "entry" | "stop" | "target";

type Props = {
  bars: Ohlc[];
  step: number;
  specs: readonly IndicatorSpec[];
  trades: TradeMark[];
  focusId: string | null;
  positions: UserPosition[];
  /** 선택한 수기 포지션 — 선을 끌 수 있고 축 라벨이 붙는다. */
  activeId: string | null;
  /** 클릭이 "놓기" 로 해석되는가 (롱/숏 단추를 누른 뒤). */
  placing: boolean;
  onPlace: (price: number, time: number) => void;
  onDrag: (key: DragKey, price: number) => void;
  onPick: (positionId: string) => void;
  height?: number;
  /** 보이는 범위를 이 시각 근처로 — 매매 행을 눌렀을 때. */
  lookAt?: { from: number; to: number } | null;
  /** 재생 중이면 오른쪽 끝을 따라간다. */
  follow?: boolean;
};

const TONES = { entry: "entry", gain: "up", loss: "down" } as const;

export function GradingChart({
  bars,
  step,
  specs,
  trades,
  focusId,
  positions,
  activeId,
  placing,
  onPlace,
  onDrag,
  onPick,
  height = 520,
  lookAt = null,
  follow = false,
}: Props) {
  const holder = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const zones = useRef<ZonesPrimitive | null>(null);
  const badges = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const overlays = useRef<OverlayHandle[]>([]);
  const levels = useRef<IPriceLine[]>([]);
  const draftLines = useRef<IPriceLine[]>([]);
  const held = useRef<DragKey | null>(null);
  const [ready, setReady] = useState(0);
  // 최신 콜백을 ref 로 — 차트 구독은 한 번만 건다.
  const latest = useRef({ placing, onPlace, onPick, positions });
  latest.current = { placing, onPlace, onPick, positions };

  useEffect(() => {
    if (holder.current === null) return;
    const c = palette();
    const made = createChart(holder.current, {
      height,
      autoSize: true,
      layout: { background: { color: c.bg }, textColor: c.text, attributionLogo: true },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      timeScale: { timeVisible: step < 86_400, secondsVisible: false, barSpacing: 8, minBarSpacing: 1, rightOffset: 4 },
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
    draftLines.current = [];
    setReady((n) => n + 1);
    const click = (param: MouseEventParams<Time>) => {
      const { placing: armed, onPlace: place, onPick: pick, positions: drawn } = latest.current;
      if (param.point === undefined) return;
      const price = candles.coordinateToPrice(param.point.y);
      const time = typeof param.time === "number" ? param.time : null;
      if (price === null || time === null) return;
      if (armed) {
        place(Number(price), time);
        return;
      }
      // 놓는 중이 아니면: 포지션 영역 안을 누르면 그것을 고른다.
      const p = Number(price);
      const hit = drawn.find(
        (pos) =>
          time >= pos.from &&
          time <= pos.to &&
          p <= Math.max(pos.entry, pos.stop, pos.target) &&
          p >= Math.min(pos.entry, pos.stop, pos.target),
      );
      if (hit) pick(hit.id);
    };
    made.subscribeClick(click);
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
      made.unsubscribeClick(click);
      badges.current = null;
      zones.current = null;
      series.current = null;
      chart.current = null;
      overlays.current = [];
      levels.current = [];
      draftLines.current = [];
      made.remove();
    };
  }, [height, step]);

  // ── 봉 (재생이면 매 틱 바뀐다 — setData 로 통째로 · 봉 수가 천 단위라 충분히 빠르다) ──
  const fitted = useRef(false);
  useEffect(() => {
    const drawn = series.current;
    const made = chart.current;
    if (drawn === null || made === null) return;
    const precision = pricePrecision(bars);
    drawn.applyOptions({ priceFormat: { type: "price", precision, minMove: 10 ** -precision } });
    drawn.setData(bars.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })));
    if (!fitted.current && bars.length > 0) {
      made.timeScale().fitContent();
      fitted.current = true;
    } else if (follow) {
      made.timeScale().scrollToRealTime();
    }
  }, [bars, ready, follow]);

  // ── 지표 (만드는 중 봉까지 넣어 계산 → 라이브 밴드) ──
  const overlaySeries = useMemo(() => computeOverlays(buildEnabled(specs), bars), [specs, bars]);
  useEffect(() => {
    const made = chart.current;
    if (made === null) return;
    overlays.current = applyOverlays(made, overlays.current, overlaySeries);
  }, [overlaySeries, ready]);

  // ── 엔진 매매 마커 ──
  const focus = useMemo(() => trades.find((t) => t.id === focusId) ?? null, [trades, focusId]);
  useEffect(() => {
    const plugin = badges.current;
    if (plugin === null) return;
    const c = palette();
    const rows: SeriesMarker<Time>[] = tradeMarkers(
      trades,
      step,
      { entry: true, exit: true, lines: true, zones: true, labels: true },
      focus?.id ?? null,
    ).map((m) => ({
      time: m.time as Time,
      position: m.position,
      shape: m.shape,
      color: c[TONES[m.tone]],
      text: m.text,
      size: m.size,
    }));
    plugin.setMarkers(rows);
  }, [trades, step, focus, ready]);

  // ── 영역: 선택한 엔진 매매 + 수기 포지션 전부 · 가로선: 선택한 엔진 매매 ──
  const active = useMemo(() => positions.find((p) => p.id === activeId) ?? null, [positions, activeId]);
  useEffect(() => {
    const drawn = series.current;
    const rug = zones.current;
    if (drawn === null || rug === null) return;
    for (const line of levels.current) drawn.removePriceLine(line);
    levels.current = [];
    const c = palette();
    const marks = { entry: true, exit: true, lines: true, zones: true, labels: true };
    const rects: Rect[] = [];
    if (focus !== null) {
      levels.current = tradeLevels(focus, marks).map((l) =>
        drawn.createPriceLine({
          price: l.price,
          color: c[TONES[l.tone]],
          lineWidth: 1,
          lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid,
          axisLabelVisible: true,
          title: l.title,
        }),
      );
      for (const z of tradeZones(focus, step, marks)) {
        rects.push({ from: z.from, to: z.to, low: z.low, high: z.high, color: wash(z.tone === "gain" ? c.up : c.down, z.alpha) });
      }
    }
    for (const p of positions) {
      const strong = p.id === activeId;
      for (const z of positionZones(p)) {
        rects.push({
          from: z.from,
          to: z.to,
          low: z.low,
          high: z.high,
          color: wash(z.tone === "gain" ? c.up : c.down, strong ? 0.3 : 0.16),
        });
      }
    }
    rug.set(rects);
  }, [focus, positions, activeId, step, ready]);

  // ── 선택한 수기 포지션의 끄는 선 ──
  useEffect(() => {
    const drawn = series.current;
    if (drawn === null) return;
    for (const line of draftLines.current) drawn.removePriceLine(line);
    draftLines.current = [];
    if (active === null) return;
    const c = palette();
    const rows: [string, number, string][] = [
      ["손절 ⇕", active.stop, c.down],
      [`${active.side === 1 ? "롱" : "숏"} 진입 ⇕`, active.entry, c.entry],
      ["목표 ⇕", active.target, c.up],
    ];
    draftLines.current = rows.map(([title, price, color]) =>
      drawn.createPriceLine({ price, color, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title }),
    );
  }, [active, ready]);

  // ── 끌기 (Chart.tsx 계획선과 같은 방식) ──
  useEffect(() => {
    const box = holder.current;
    const made = chart.current;
    const drawn = series.current;
    if (box === null || made === null || drawn === null || active === null) return;
    const priceAt = (event: MouseEvent): number | null => {
      const rect = box.getBoundingClientRect();
      const got = drawn.coordinateToPrice(event.clientY - rect.top);
      return got === null ? null : Number(got);
    };
    const down = (event: MouseEvent) => {
      const price = priceAt(event);
      if (price === null) return;
      const rect = box.getBoundingClientRect();
      const near = Math.abs(Number(drawn.coordinateToPrice(0) ?? 0) - Number(drawn.coordinateToPrice(rect.height * 0.02) ?? 0));
      const rows: [DragKey, number][] = [
        ["entry", active.entry],
        ["stop", active.stop],
        ["target", active.target],
      ];
      let best: [DragKey, number] | null = null;
      for (const row of rows) {
        if (Math.abs(row[1] - price) > near) continue;
        if (best === null || Math.abs(row[1] - price) < Math.abs(best[1] - price)) best = row;
      }
      if (best === null) return;
      held.current = best[0];
      made.applyOptions({ handleScroll: false, handleScale: false });
      event.preventDefault();
    };
    const moveTo = (event: MouseEvent) => {
      if (held.current === null) return;
      const price = priceAt(event);
      if (price !== null) onDrag(held.current, price);
    };
    const up = () => {
      if (held.current === null) return;
      held.current = null;
      made.applyOptions({ handleScroll: true, handleScale: true });
    };
    box.addEventListener("mousedown", down);
    window.addEventListener("mousemove", moveTo);
    window.addEventListener("mouseup", up);
    return () => {
      box.removeEventListener("mousedown", down);
      window.removeEventListener("mousemove", moveTo);
      window.removeEventListener("mouseup", up);
      made.applyOptions({ handleScroll: true, handleScale: true });
    };
  }, [active, onDrag]);

  // ── 보이는 범위 ──
  useEffect(() => {
    const made = chart.current;
    if (made === null || lookAt === null || bars.length === 0) return;
    const first = bars[0]?.time ?? lookAt.from;
    const last = bars[bars.length - 1]?.time ?? lookAt.to;
    made.timeScale().setVisibleRange({ from: Math.max(first, lookAt.from) as Time, to: Math.min(last, lookAt.to) as Time });
  }, [lookAt, bars, ready]);

  const shown = legend(overlaySeries);
  return (
    <div>
      <div ref={holder} style={{ width: "100%", height, cursor: placing ? "crosshair" : "default" }} />
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-blue-gray-500 dark:text-blue-gray-300">
        {shown.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-4" style={{ background: s.color }} aria-hidden="true" />
            {s.label}
          </span>
        ))}
        <span className="text-blue-gray-400">· 지표는 보이는 봉(만드는 중 봉 포함)으로 계산 = 라이브 밴드</span>
      </div>
    </div>
  );
}
