/**
 * 보유 매매의 **예측 그림** — 청산 띠 · 꼬리선 · 부채꼴 · 갈래 선 (T820 · T825 · 표시 전용).
 *
 * 값은 서버가 연구 모형으로 낸 가격 · 시각 그대로다(`/walkforward/forecast`) — 화면은 값을 만들지 않는다.
 *
 * ```
 *   띠      진입 뒤 청산 자리의 25 ~ 75% (옅은 면) + 가운데 점선 · 꼬리선(90%)은 통과 다리만
 *   부채꼴  기준 시각 → 12 · 24 · 48 시간 뒤 · 10 ~ 90% (아주 옅게) · 25 ~ 75% (조금 진하게)
 *   갈래    유리 · 옆 · 불리 세 갈래의 가운데 길(점선) + 끝에 확률
 * ```
 *
 * 🔴 주문 · 청산과 무관하다 — 사람이 읽는 그림이다. 좌표 계산은 순수 함수(`forecastShapes`)로 빼고
 * 캔버스는 `ForecastPrimitive` 가 만진다. 미래 시각은 마지막 봉 뒤로 봉 간격만큼 이어 붙여 잰다.
 */
import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import type { ForecastTrade } from "../api";

export type FanSlice = { ts: number; lo: number; hi: number };

export type ForecastShape = {
  id: string;
  /** 기준 시각(초) — 띠 · 부채꼴이 여기서 시작한다. */
  from: number;
  /** 부채꼴 끝(초) — 띠도 여기까지 깐다. */
  to: number;
  band: { low: number; mid: number; high: number; tail: number | null };
  outer: FanSlice[];
  inner: FanSlice[];
  branches: { name: string; p: number; pts: { ts: number; price: number }[] }[];
};

const sec = (iso: string): number => Math.floor(Date.parse(iso) / 1000);

/** 예측 응답 → 그릴 도형(그릴 수 있는 매매만). */
export function forecastShapes(trades: readonly ForecastTrade[]): ForecastShape[] {
  const out: ForecastShape[] = [];
  for (const t of trades) {
    if (!t.ok || t.band === undefined || t.fan === undefined) continue;
    const origin = t.fan.origin;
    const from = sec(origin.at);
    const pts = t.fan.points;
    const last = pts[pts.length - 1];
    const to = last === undefined ? from : sec(last.at);
    const start: FanSlice = { ts: from, lo: origin.price, hi: origin.price };
    // 분위 칸: [10, 25, 50, 75, 90] — 서버가 qs 로 함께 준다.
    const idx = (q: number): number => t.fan?.qs.findIndex((v) => Math.abs(v - q) < 1e-9) ?? -1;
    const at = (row: number[], q: number): number => row[idx(q)] ?? Number.NaN;
    const slice = (lo: number, hi: number) =>
      pts.map((p) => ({ ts: sec(p.at), lo: Math.min(at(p.p, lo), at(p.p, hi)), hi: Math.max(at(p.p, lo), at(p.p, hi)) }));
    out.push({
      id: t.trade_id,
      from,
      to,
      band: { low: Math.min(t.band.q25, t.band.q75), mid: t.band.q50, high: Math.max(t.band.q25, t.band.q75), tail: t.band.q90 },
      outer: [start, ...slice(0.1, 0.9)],
      inner: [start, ...slice(0.25, 0.75)],
      branches: (t.branch?.lines ?? []).map((l) => ({
        name: l.name,
        p: l.p,
        pts: l.points.map((p) => ({ ts: sec(p.at), price: p.price })),
      })),
    });
  }
  return out;
}

/** 예측이 그리는 가격의 위 · 아래 끝 — 가격 축이 이 범위를 담아야 선이 잘리지 않는다. */
export function priceRange(shapes: readonly ForecastShape[]): { min: number; max: number } | null {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  const take = (v: number | null) => {
    if (v === null || !Number.isFinite(v)) return;
    min = Math.min(min, v);
    max = Math.max(max, v);
  };
  for (const s of shapes) {
    take(s.band.low);
    take(s.band.high);
    take(s.band.tail);
    for (const f of s.outer) {
      take(f.lo);
      take(f.hi);
    }
    for (const b of s.branches) for (const p of b.pts) take(p.price);
  }
  return Number.isFinite(min) && Number.isFinite(max) ? { min, max } : null;
}

/** 부채꼴 · 갈래가 마지막 봉 뒤로 몇 봉을 차지하나 — 오른쪽 여백을 이만큼 연다. */
export function futureBars(shapes: readonly ForecastShape[], lastTs: number, step: number): number {
  if (step <= 0) return 0;
  let far = lastTs;
  for (const s of shapes) {
    far = Math.max(far, s.to);
    for (const b of s.branches) for (const p of b.pts) far = Math.max(far, p.ts);
  }
  return Math.max(0, Math.ceil((far - lastTs) / step));
}

export type ForecastPalette = { fan: string; gain: string; loss: string; flat: string; paper: string; text: string };

/** 갈래 이름 → 색 열쇠. */
function toneOf(name: string): "gain" | "loss" | "flat" {
  return name === "유리" ? "gain" : name === "불리" ? "loss" : "flat";
}

function alpha(color: string, a: number): string {
  if (color.startsWith("#") && color.length === 7) {
    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }
  return color;
}

type Scope = {
  context: CanvasRenderingContext2D;
  bitmapSize: { width: number; height: number };
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
};

export class ForecastPrimitive implements ISeriesPrimitive<Time> {
  private shapes: ForecastShape[] = [];
  private step = 3600;
  private colors: ForecastPalette = {
    fan: "#4f6bed",
    gain: "#0f7b6c",
    loss: "#b4423a",
    flat: "#78716c",
    paper: "#ffffff",
    text: "#44403c",
  };
  private series: ISeriesApi<"Candlestick"> | null = null;
  private chart: IChartApi | null = null;
  private refresh: (() => void) | null = null;

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.chart = param.chart;
    this.refresh = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.chart = null;
    this.refresh = null;
  }

  set(shapes: ForecastShape[], step: number, colors: ForecastPalette): void {
    this.shapes = shapes;
    this.step = step;
    this.colors = colors;
    this.refresh?.();
  }

  /**
   * 가격 축이 예측 범위를 담게 한다 — 안 그러면 봉 범위로만 축이 잡혀 부채꼴 · 갈래가 잘린다
   * (2026-11-11 화면 확인에서 잡힘).
   */
  autoscaleInfo() {
    const r = priceRange(this.shapes);
    return r === null ? null : { priceRange: { minValue: r.min, maxValue: r.max } };
  }

  /** 시각(초) → x. 마지막 봉 뒤는 봉 간격으로 이어 붙인 논리 자리로 잰다. */
  private x(ts: number): number | null {
    const chart = this.chart;
    const series = this.series;
    if (chart === null || series === null) return null;
    const scale = chart.timeScale();
    const data = series.data();
    const last = data[data.length - 1];
    if (last === undefined) return null;
    const lastTs = last.time as number;
    if (ts > lastTs) {
      return scale.logicalToCoordinate((data.length - 1 + (ts - lastTs) / this.step) as Logical);
    }
    const got = scale.timeToCoordinate(ts as Time);
    if (got !== null) return got;
    const index = scale.timeToIndex(ts as Time, true);
    return index === null ? null : scale.logicalToCoordinate(index as unknown as Logical);
  }

  paneViews() {
    const owner = this;
    const view = (order: "bottom" | "top", paint: (scope: Scope) => void) => ({
      zOrder: () => order,
      renderer: () => ({
        draw(target: { useBitmapCoordinateSpace: (fn: (scope: Scope) => void) => void }) {
          if (owner.series === null || owner.shapes.length === 0) return;
          target.useBitmapCoordinateSpace((scope) => paint.call(owner, scope));
        },
      }),
    });
    return [view("bottom", owner.paintAreas), view("top", owner.paintLines)];
  }

  private y(price: number): number | null {
    const v = this.series?.priceToCoordinate(price);
    return v === undefined || v === null ? null : v;
  }

  private polygon(scope: Scope, slices: FanSlice[], fill: string): void {
    const { context, horizontalPixelRatio: hx, verticalPixelRatio: vy } = scope;
    const top: [number, number][] = [];
    const bottom: [number, number][] = [];
    for (const s of slices) {
      const x = this.x(s.ts);
      const yh = this.y(s.hi);
      const yl = this.y(s.lo);
      if (x === null || yh === null || yl === null) return;
      top.push([x * hx, yh * vy]);
      bottom.push([x * hx, yl * vy]);
    }
    if (top.length < 2) return;
    context.beginPath();
    top.forEach(([x, y], i) => (i === 0 ? context.moveTo(x, y) : context.lineTo(x, y)));
    for (let i = bottom.length - 1; i >= 0; i -= 1) {
      const [x, y] = bottom[i]!;
      context.lineTo(x, y);
    }
    context.closePath();
    context.fillStyle = fill;
    context.fill();
  }

  /** 아래 층 — 띠 면 · 부채꼴 면(봉을 가리지 않게 옅게). */
  private paintAreas(scope: Scope): void {
    const { context, horizontalPixelRatio: hx, verticalPixelRatio: vy } = scope;
    for (const s of this.shapes) {
      const x0 = this.x(s.from);
      const x1 = this.x(s.to);
      const yh = this.y(s.band.high);
      const yl = this.y(s.band.low);
      if (x0 !== null && x1 !== null && yh !== null && yl !== null && x1 > x0) {
        context.fillStyle = alpha(this.colors.fan, 0.08);
        context.fillRect(x0 * hx, yh * vy, (x1 - x0) * hx, Math.max(1, (yl - yh) * vy));
      }
      this.polygon(scope, s.outer, alpha(this.colors.fan, 0.1));
      this.polygon(scope, s.inner, alpha(this.colors.fan, 0.18));
    }
  }

  private dashed(scope: Scope, pts: [number, number][], color: string, width: number, dash: number[]): void {
    const { context, horizontalPixelRatio: hx } = scope;
    if (pts.length < 2) return;
    context.save();
    context.strokeStyle = color;
    context.lineWidth = Math.max(1, width * hx);
    context.setLineDash(dash.map((d) => d * hx));
    context.beginPath();
    pts.forEach(([x, y], i) => (i === 0 ? context.moveTo(x, y) : context.lineTo(x, y)));
    context.stroke();
    context.restore();
  }

  /**
   * 글자 하나 — 종이 바탕을 깔고, 이미 놓인 글자와 겹치면 아래로 비킨다.
   *
   * ⚠️ 글자는 선 끝 **안쪽**에 둔다 — 바깥(오른쪽)에 두면 가격 축에 잘린다(2026-11-11 화면 확인).
   */
  private label(
    scope: Scope,
    placed: { x0: number; x1: number; y0: number; y1: number }[],
    text: string,
    color: string,
    at: [number, number],
    align: "left" | "right",
  ): void {
    const { context, horizontalPixelRatio: hx, verticalPixelRatio: vy } = scope;
    const font = Math.round(11 * vy);
    context.save();
    context.font = `${font}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    context.textBaseline = "middle";
    const w = context.measureText(text).width;
    const pad = 3 * hx;
    const x = align === "right" ? at[0] - w - pad * 2 : at[0] + pad * 2;
    let y = at[1] - font * 0.9;
    const box = () => ({ x0: x - pad, x1: x + w + pad, y0: y - font * 0.7, y1: y + font * 0.7 });
    for (let guard = 0; guard < 8 && placed.some((r) => {
      const b = box();
      return b.x0 < r.x1 && b.x1 > r.x0 && b.y0 < r.y1 && b.y1 > r.y0;
    }); guard += 1) {
      y += font * 1.5;
    }
    const b = box();
    placed.push(b);
    context.fillStyle = alpha(this.colors.paper, 0.85);
    context.fillRect(b.x0, b.y0, b.x1 - b.x0, b.y1 - b.y0);
    context.fillStyle = color;
    context.fillText(text, x, y);
    context.restore();
  }

  /** 위 층 — 띠 가운데 · 꼬리선 · 갈래 선 · 확률 글자. */
  private paintLines(scope: Scope): void {
    const { horizontalPixelRatio: hx, verticalPixelRatio: vy } = scope;
    const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
    for (const s of this.shapes) {
      const x0 = this.x(s.from);
      const x1 = this.x(s.to);
      if (x0 === null || x1 === null) continue;
      const line = (price: number | null, color: string, dash: number[], text: string) => {
        if (price === null) return;
        const y = this.y(price);
        if (y === null) return;
        this.dashed(scope, [[x0 * hx, y * vy], [x1 * hx, y * vy]], color, 1, dash);
        this.label(scope, placed, text, color, [x0 * hx, y * vy], "left");
      };
      line(s.band.mid, this.colors.fan, [4, 3], "청산 가운데");
      line(s.band.tail, this.colors.fan, [1, 3], "꼬리 90%");
      for (const b of s.branches) {
        const pts: [number, number][] = [];
        for (const p of b.pts) {
          const x = this.x(p.ts);
          const y = this.y(p.price);
          if (x === null || y === null) continue;
          pts.push([x * hx, y * vy]);
        }
        const color = this.colors[toneOf(b.name)];
        this.dashed(scope, pts, color, 1.5, [6, 4]);
        const end = pts[pts.length - 1];
        if (end !== undefined) this.label(scope, placed, `${b.name} ${Math.round(b.p * 100)}%`, color, end, "right");
      }
    }
  }
}
