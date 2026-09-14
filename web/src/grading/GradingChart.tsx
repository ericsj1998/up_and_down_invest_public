/**
 * 채점 차트(dev · T281) — 봉 + 지표(BB 20/2 · BB 4/4 · EMA) + 엔진 매매(진입 ▲▼ · 진입→청산 선 · 청산 ●) + 수기 포지션 + 클릭·끌기.
 *
 * `PriceChart.tsx`(끝난 자료 · 읽기 전용)와 다른 점:
 *   1. 봉 배열이 **재생 커서에 따라 바뀐다** — 마지막 봉은 만드는 중 봉이고 지표도 그 봉을 포함해 다시 계산된다.
 *   2. 매매는 **진입→청산 선**(승 초록 · 패 빨강)으로 그린다 — 연구 그림(`_plot_n.py`)과 같은 표기 (사용자 2026-09-14:
 *      화살표+글자가 너무 지저분). 글자는 고른(또는 마우스 올린) 매매에만 `TL -0.40% trend_end` 로.
 *   3. 마우스를 올린 봉에 진입한 매매를 `onHover` 로 알린다 — 아래 표가 그 행으로 간다.
 *   4. 수기 포지션: 클릭으로 놓고(`onPlace`), 고른 포지션은 진입·손절·목표 선을 끌고(`onDrag`), **상자 안을 꾹 누른 채 끌면
 *      통째로 옮긴다**(`onMove` · 가격·시각 함께). 끄는 동안 차트 스크롤·확대를 잠근다 — 잠그지 않으면 선이 아니라
 *      차트가 끌린다(첫 판 실측 2026-09-14: 상태가 바뀔 때마다 잠금이 풀려 차트가 따라 움직였다 → 잠금은 ref 로만).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  LineSeries,
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
import { snap, type TradeMark } from "../chart/trades";
import { ZonesPrimitive, type Rect } from "../chart/ZonesPrimitive";
import { positionZones, tag, type UserPosition } from "./grading";

export type DragKey = "entry" | "stop" | "target";

type Props = {
  bars: Ohlc[];
  step: number;
  specs: readonly IndicatorSpec[];
  trades: TradeMark[];
  focusId: string | null;
  hoverId: string | null;
  onHover: (id: string | null) => void;
  positions: UserPosition[];
  /** 선택한 수기 포지션 — 선을 끌 수 있고 축 라벨이 붙는다. */
  activeId: string | null;
  /** 클릭이 "놓기" 로 해석되는가 (롱/숏 단추를 누른 뒤). */
  placing: boolean;
  onPlace: (price: number, time: number) => void;
  onDrag: (key: DragKey, price: number) => void;
  /** 상자 통째로 옮기기 — 가격 차이 · 시각 차이(초). */
  onMove: (dPrice: number, dTime: number) => void;
  onPick: (positionId: string) => void;
  height?: number;
  lookAt?: { from: number; to: number } | null;
  follow?: boolean;
};

export function GradingChart({
  bars,
  step,
  specs,
  trades,
  focusId,
  hoverId,
  onHover,
  positions,
  activeId,
  placing,
  onPlace,
  onDrag,
  onMove,
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
  const segLines = useRef<ISeriesApi<"Line">[]>([]);
  const draftLines = useRef<IPriceLine[]>([]);
  const [ready, setReady] = useState(0);
  // 최신 값을 ref 로 — 차트 구독·마우스 리스너는 한 번만 걸고, 상태가 바뀌어도 잠금이 풀리지 않게.
  const latest = useRef({ placing, onPlace, onPick, onDrag, onMove, onHover, positions, activeId, trades, step });
  latest.current = { placing, onPlace, onPick, onDrag, onMove, onHover, positions, activeId, trades, step };

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
    segLines.current = [];
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
    // 마우스가 올라간 봉에 진입한 매매 → 아래 표로.
    const hover = (param: MouseEventParams<Time>) => {
      const { trades: rows, step: st, onHover: tell } = latest.current;
      const time = typeof param.time === "number" ? param.time : null;
      if (time === null) {
        tell(null);
        return;
      }
      const at = rows.find((t) => snap(t.openedTs, st) === time) ?? null;
      tell(at?.id ?? null);
    };
    made.subscribeCrosshairMove(hover);

    // ── 끌기: 선(진입·손절·목표) 또는 상자 통째로. 잠금은 down/up 에서만 — 상태 변화와 무관.
    const box = holder.current;
    let held: DragKey | "move" | null = null;
    let last: { price: number; logical: number } | null = null;
    const priceAt = (event: MouseEvent): number | null => {
      const rect = box.getBoundingClientRect();
      const got = candles.coordinateToPrice(event.clientY - rect.top);
      return got === null ? null : Number(got);
    };
    const logicalAt = (event: MouseEvent): number | null => {
      const rect = box.getBoundingClientRect();
      const got = made.timeScale().coordinateToLogical(event.clientX - rect.left);
      return got === null ? null : Number(got);
    };
    const down = (event: MouseEvent) => {
      const { positions: drawn, activeId: id, placing: armed } = latest.current;
      if (armed) return;
      const active = drawn.find((p) => p.id === id) ?? null;
      if (active === null) return;
      const price = priceAt(event);
      const logical = logicalAt(event);
      if (price === null || logical === null) return;
      const rect = box.getBoundingClientRect();
      const near = Math.abs(Number(candles.coordinateToPrice(0) ?? 0) - Number(candles.coordinateToPrice(rect.height * 0.02) ?? 0));
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
      if (best !== null) {
        held = best[0];
      } else {
        // 상자 안(시각·가격 모두)이면 통째로 옮긴다.
        const t = made.timeScale().coordinateToTime(event.clientX - rect.left);
        const time = typeof t === "number" ? t : null;
        const lo = Math.min(active.entry, active.stop, active.target);
        const hi = Math.max(active.entry, active.stop, active.target);
        if (time === null || time < active.from || time > active.to || price < lo || price > hi) return;
        held = "move";
      }
      last = { price, logical };
      made.applyOptions({ handleScroll: false, handleScale: false });
      event.preventDefault();
    };
    const moveTo = (event: MouseEvent) => {
      if (held === null || last === null) return;
      const price = priceAt(event);
      const logical = logicalAt(event);
      if (price === null || logical === null) return;
      if (held === "move") {
        const dt = (logical - last.logical) * latest.current.step;
        // 봉 하나 이상 움직였을 때만 시각을 옮긴다 — 가격은 매번.
        const whole = Math.trunc(dt / latest.current.step) * latest.current.step;
        latest.current.onMove(price - last.price, whole);
        last = { price, logical: whole === 0 ? last.logical : logical };
      } else {
        latest.current.onDrag(held, price);
      }
    };
    const up = () => {
      if (held === null) return;
      held = null;
      last = null;
      made.applyOptions({ handleScroll: true, handleScale: true });
    };
    box.addEventListener("mousedown", down);
    window.addEventListener("mousemove", moveTo);
    window.addEventListener("mouseup", up);

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
      made.unsubscribeCrosshairMove(hover);
      box.removeEventListener("mousedown", down);
      window.removeEventListener("mousemove", moveTo);
      window.removeEventListener("mouseup", up);
      badges.current = null;
      zones.current = null;
      series.current = null;
      chart.current = null;
      overlays.current = [];
      segLines.current = [];
      draftLines.current = [];
      made.remove();
    };
  }, [height, step]);

  // ── 봉 ──
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

  // ── 지표 ──
  const overlaySeries = useMemo(() => computeOverlays(buildEnabled(specs), bars), [specs, bars]);
  useEffect(() => {
    const made = chart.current;
    if (made === null) return;
    overlays.current = applyOverlays(made, overlays.current, overlaySeries);
  }, [overlaySeries, ready]);

  // ── 엔진 매매: 진입 ▲▼(작게) · 진입→청산 선 · 청산 ● · 글자는 고른/올린 것에만 ──
  const focus = useMemo(() => trades.find((t) => t.id === focusId) ?? null, [trades, focusId]);
  useEffect(() => {
    const plugin = badges.current;
    const made = chart.current;
    if (plugin === null || made === null) return;
    const c = palette();
    for (const line of segLines.current) {
      try {
        made.removeSeries(line);
      } catch {
        // 이미 지워진 차트
      }
    }
    segLines.current = [];
    const rows: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const strong = t.id === focusId || t.id === hoverId;
      const win = (t.pnl ?? 0) >= 0;
      const color = t.reason === "open" ? c.entry : win ? c.up : c.down;
      const label = strong ? `${tag(t.leg ?? "", t.side)} ${(t.pnl ?? 0).toFixed(2)}% ${t.reason}` : "";
      rows.push({
        time: snap(t.openedTs, step) as Time,
        position: t.side === 1 ? "belowBar" : "aboveBar",
        shape: t.side === 1 ? "arrowUp" : "arrowDown",
        color: t.side === 1 ? c.up : c.down,
        text: label,
        size: strong ? 2 : 1,
      });
      const t0 = snap(t.openedTs, step);
      const t1 = Math.max(snap(t.closedTs, step), t0 + step);
      if (t.reason !== "open") {
        rows.push({ time: t1 as Time, position: t.side === 1 ? "aboveBar" : "belowBar", shape: "circle", color, text: "", size: strong ? 1.5 : 0.8 });
      }
      const seg = made.addSeries(LineSeries, {
        color,
        lineWidth: strong ? 3 : 2,
        lineStyle: t.reason === "open" ? LineStyle.Dotted : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      seg.setData([
        { time: t0 as Time, value: t.entry },
        { time: t1 as Time, value: t.exit },
      ]);
      segLines.current.push(seg);
    }
    rows.sort((a, b) => Number(a.time) - Number(b.time));
    plugin.setMarkers(rows);
  }, [trades, step, focusId, hoverId, ready]);

  // ── 영역: 고른 엔진 매매의 손절·손익 + 수기 포지션 전부 ──
  const active = useMemo(() => positions.find((p) => p.id === activeId) ?? null, [positions, activeId]);
  useEffect(() => {
    const rug = zones.current;
    if (rug === null) return;
    const c = palette();
    const rects: Rect[] = [];
    if (focus !== null) {
      const from = snap(focus.openedTs, step);
      const to = Math.max(snap(focus.closedTs, step) + step, from + step);
      if (focus.stop !== focus.entry) {
        rects.push({ from, to, low: Math.min(focus.entry, focus.stop), high: Math.max(focus.entry, focus.stop), color: wash(c.down, 0.12) });
      }
      const win = (focus.exit - focus.entry) * focus.side > 0;
      rects.push({ from, to, low: Math.min(focus.entry, focus.exit), high: Math.max(focus.entry, focus.exit), color: wash(win ? c.up : c.down, 0.16) });
    }
    for (const p of positions) {
      const strong = p.id === activeId;
      for (const z of positionZones(p)) {
        rects.push({ from: z.from, to: z.to, low: z.low, high: z.high, color: wash(z.tone === "gain" ? c.up : c.down, strong ? 0.3 : 0.16) });
      }
    }
    rug.set(rects);
  }, [focus, positions, activeId, step, ready]);

  // ── 고른 수기 포지션의 끄는 선 ──
  useEffect(() => {
    const drawn = series.current;
    if (drawn === null) return;
    for (const line of draftLines.current) drawn.removePriceLine(line);
    draftLines.current = [];
    if (active === null) return;
    const c = palette();
    const rows: [string, number, string][] = [
      ["손절 ⇕", active.stop, c.down],
      [`${active.side === 1 ? "롱" : "숏"} 진입 ⇕ (상자 안 끌면 이동)`, active.entry, c.entry],
      ["목표 ⇕", active.target, c.up],
    ];
    draftLines.current = rows.map(([title, price, color]) =>
      drawn.createPriceLine({ price, color, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title }),
    );
  }, [active, ready]);

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
      <div ref={holder} style={{ width: "100%", height, cursor: placing ? "crosshair" : active ? "move" : "default" }} />
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-blue-gray-500 dark:text-blue-gray-300">
        {shown.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-4" style={{ background: s.color }} aria-hidden="true" />
            {s.label}
          </span>
        ))}
        <span className="text-blue-gray-400">
          · ▲▼ 진입 · 선 = 진입→청산(초록 승 · 빨강 패 · 점선 = 아직 열림) · ● 청산 · 지표는 보이는 봉으로 계산(라이브 밴드)
        </span>
      </div>
    </div>
  );
}
