/**
 * 차트의 **매매 상자** — 그 매매가 어디서 어디까지 살았나 (사용자 요구 2026-09-21).
 *
 * ```
 * 점을 찍지 말고, 상자 시작점에 세로 줄 + "진입", 끝나는 지점에 세로 줄 + "익절/손절/보합".
 * 박스에 마우스 올리면 하이라이팅 되면서 진입가·손절가·익절가가 각 박스 선 좌측에.
 * ```
 *
 * 상자의 **뜻** (`trades.ts` 의 규칙 그대로 — 백테스트 차트와 같은 색이어야 한다):
 *   붉은      진입 ↔ 손절선   그 매매가 감수한 위험
 *   초록      진입 ↔ 청산가   유리하게 끝났을 때 번 구간
 *   짙은 붉은 손절선 ↔ 청산가 손절선을 **지나서** 끝난 몫 (갭 · 강제청산)
 *
 * 🔴 **손절로 끝난 매매에는 초록 상자가 없다.** 익절 기준이 없어서가 아니라 *유리하게 끝난
 * 구간이 없어서*다 — 초록은 목표가가 아니라 **실제 청산가**까지 그린다 (룰 0.3 은 애초에
 * 고정 익절선이 없다 · `full_ride`).
 *
 * 좌표 계산은 순수 함수로 빼 두고(시험이 있다), 캔버스는 `TradeBoxPrimitive` 가 만진다.
 */
import type {
  IChartApi,
  Logical,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import { favorable, fmtPrice, sideLabel, snap, type TradeMark } from "./trades";
import { verdictOfTrade } from "../verdict";

export type BoxKind = "risk" | "gain" | "beyond";

export type TradeBox = {
  id: string;
  from: number;
  to: number;
  low: number;
  high: number;
  kind: BoxKind;
  /** 아직 안 닫힌 매매 — 테두리를 점선으로 그려 "끝난 것" 과 가른다. */
  open: boolean;
};

export type TradeEdge = {
  id: string;
  at: number;
  label: string;
  tone: "entry" | "gain" | "loss" | "flat";
  /** 상자의 어느 쪽 경계인가 — 글자를 안쪽으로 밀어 잘리지 않게 한다. */
  side: "open" | "close";
};

/** 호버했을 때 상자 선 **좌측**에 찍을 가격 딱지. */
export type PriceTag = { price: number; text: string; tone: "entry" | "gain" | "loss" };

const TONE_OF: Record<BoxKind, "gain" | "loss"> = {
  risk: "loss",
  gain: "gain",
  beyond: "loss",
};

const ALPHA: Record<BoxKind, number> = { risk: 0.14, gain: 0.16, beyond: 0.32 };

/**
 * 매매 하나 → 상자들.
 *
 * ⚠️ `trades.ts` 의 `tradeZones` 와 같은 규칙이지만 **id 와 종류를 달고** 나온다 — 호버로
 * 어느 매매인지 되찾아야 해서다. 규칙이 갈리지 않게 경계 조건(유리했나 · 손절선을 지났나)은
 * 거기서 쓰는 `favorable` 을 그대로 부른다.
 */
export function boxesOf(trade: TradeMark, step: number): TradeBox[] {
  const from = snap(trade.openedTs, step);
  const to = Math.max(snap(trade.closedTs, step) + step, from + step);
  const open = trade.open === true;
  const out: TradeBox[] = [
    {
      id: trade.id,
      from,
      to,
      low: Math.min(trade.entry, trade.stop),
      high: Math.max(trade.entry, trade.stop),
      kind: "risk",
      open,
    },
  ];
  if (favorable(trade)) {
    out.push({
      id: trade.id,
      from,
      to,
      low: Math.min(trade.entry, trade.exit),
      high: Math.max(trade.entry, trade.exit),
      kind: "gain",
      open,
    });
  } else if ((trade.exit - trade.stop) * trade.side < 0) {
    out.push({
      id: trade.id,
      from,
      to,
      low: Math.min(trade.stop, trade.exit),
      high: Math.max(trade.stop, trade.exit),
      kind: "beyond",
      open,
    });
  }
  return out;
}

/**
 * 손익을 **금액 + %** 로 (사용자 요구 2026-09-21: *"박스에 손해본 금액과, 진입금액 대비 몇퍼
 * 손해봤는지 나오게 해줘"*).
 *
 * 🔴 `pnl` 은 이미 **진입금액(그 매매가 건 증거금) 대비** % 다 — 배율이 곱해진 값이라
 * 분모가 그 돈이다. 금액은 거기에 `margin` 을 곱하면 나온다.
 *
 * ⚠️ `margin` 이 없으면(백테스트·단독 판·옛 행) **% 만** 적는다. 판 예산 같은 다른 돈을
 * 끌어다 곱하면 그럴듯한 거짓 금액이 된다 — 없는 것은 안 적는다.
 */
export function pnlText(trade: TradeMark): string {
  if (trade.pnl === null) return "";
  const pct = `${trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)}%`;
  const margin = trade.margin;
  if (margin === undefined || margin === null || !(margin > 0)) return pct;
  const usdt = (margin * trade.pnl) / 100;
  return `${usdt >= 0 ? "+" : ""}${usdt.toFixed(2)} USDT · ${pct}`;
}

/** 매매 하나 → 양쪽 세로 경계 (시작 = 진입 · 끝 = 결말 + 손익). */
export function edgesOf(trade: TradeMark, step: number): TradeEdge[] {
  const from = snap(trade.openedTs, step);
  const to = Math.max(snap(trade.closedTs, step) + step, from + step);
  const done = verdictOfTrade(trade.pnl, trade.open === true);
  const money = pnlText(trade);
  return [
    {
      id: trade.id,
      at: from,
      label: `${sideLabel(trade.side)} 진입`,
      tone: "entry",
      side: "open",
    },
    {
      id: trade.id,
      at: to,
      // ⭐ 결말만으로는 "얼마나" 를 못 읽는다 — 손절도 -0.6% 와 -12% 는 다른 사건이다.
      label: money === "" ? done.label : `${done.label} ${money}`,
      tone: done.tone === "gain" ? "gain" : done.tone === "loss" ? "loss" : "flat",
      side: "close",
    },
  ];
}

/**
 * 호버한 매매의 가격 딱지 — **상자 선 좌측**에 찍는다.
 *
 * 🔴 **'손절' 이 두 번 뜨던 것을 고쳤다** (사용자 신고 2026-09-21: *"손절이 왜 2개로 뜨는지
 * 모르겠고"*). 손절로 끝난 매매는 ① 계획 손절선과 ② 실제 청산가가 **둘 다 '손절'** 이라
 * 적혀 있었다. 둘은 다른 값이다 — 계획선은 진입할 때 정한 자리이고 청산가는 실제로 나간
 * 가격이며, 그 차이가 곧 **미끄러짐**이다. 이름을 갈라 그 차이가 보이게 한다:
 *
 * ```
 * 손절선 2.157   진입할 때 정한 자리 (계획)
 * 청산  2.283    실제로 나간 가격
 * ```
 *
 * ⚠️ 결말(익절·손절·보합)은 **상자 끝의 세로줄**이 이미 말한다 — 여기서 또 적으면 중복이다.
 * ⚠️ 아직 열린 매매의 셋째 값은 청산가가 아니라 **지금가**라 이름이 다르다.
 */
export function tagsOf(trade: TradeMark): PriceTag[] {
  const done = verdictOfTrade(trade.pnl, trade.open === true);
  return [
    { price: trade.entry, text: `진입 ${fmtPrice(trade.entry)}`, tone: "entry" },
    { price: trade.stop, text: `손절선 ${fmtPrice(trade.stop)}`, tone: "loss" },
    {
      price: trade.exit,
      text: `${trade.open === true ? "지금" : "청산"} ${fmtPrice(trade.exit)}`,
      tone: done.tone === "loss" ? "loss" : "gain",
    },
  ];
}

/**
 * 마우스가 어느 매매 위인가 — 상자 안이면 그 id.
 *
 * ⚠️ 겹친 상자(붉은 + 초록)는 **같은 매매**라 무엇을 고르든 같다. 서로 다른 매매가 겹치면
 * **나중 것**(최근)을 고른다 — 최근 것이 위에 그려지므로 눈에 보이는 것과 맞는다.
 */
export function hitTest(boxes: readonly TradeBox[], time: number, price: number): string | null {
  for (let i = boxes.length - 1; i >= 0; i -= 1) {
    const box = boxes[i];
    if (box === undefined) continue;
    if (time >= box.from && time <= box.to && price >= box.low && price <= box.high) {
      return box.id;
    }
  }
  return null;
}

type Scope = {
  context: CanvasRenderingContext2D;
  bitmapSize: { width: number; height: number };
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
};

export type Palette = {
  entry: string;
  gain: string;
  loss: string;
  flat: string;
  /** 글자 뒤에 까는 판 — 봉 위에서도 읽히게. */
  paper: string;
};

/** 색을 섞는다 — `theme.ts` 의 `wash` 와 같은 일이지만 이 파일만 쓰는 작은 판이다. */
function washed(color: string, alpha: number): string {
  if (color.startsWith("#") && (color.length === 7 || color.length === 4)) {
    const full =
      color.length === 4
        ? `#${color[1]}${color[1]}${color[2]}${color[2]}${color[3]}${color[3]}`
        : color;
    const r = parseInt(full.slice(1, 3), 16);
    const g = parseInt(full.slice(3, 5), 16);
    const b = parseInt(full.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color;
}

/**
 * 매매 상자 · 세로 경계 · 호버 딱지를 그리는 프리미티브.
 *
 * 🔴 `ZonesPrimitive`(리포트 차트)와 나눠 둔 이유: 저쪽은 **고른 매매 하나**의 영역만 칠하고,
 * 이쪽은 **여럿을 같이** 그리면서 세로줄·글자·호버까지 맡는다. 한 클래스에 넣으면 리포트
 * 차트가 안 쓰는 분기를 지고 간다.
 */
export class TradeBoxPrimitive implements ISeriesPrimitive<Time> {
  private boxes: TradeBox[] = [];
  private edges: TradeEdge[] = [];
  private tags: PriceTag[] = [];
  private hover: string | null = null;
  private colors: Palette = {
    entry: "#b8860b",
    gain: "#0f7b6c",
    loss: "#b4423a",
    flat: "#78716c",
    paper: "#ffffff",
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

  set(boxes: TradeBox[], edges: TradeEdge[], colors: Palette): void {
    this.boxes = boxes;
    this.edges = edges;
    this.colors = colors;
    this.refresh?.();
  }

  /** 호버한 매매와 그 가격 딱지. 바뀐 게 없으면 다시 그리지 않는다 — 크로스헤어는 초당 수십 번 온다. */
  setHover(id: string | null, tags: PriceTag[]): boolean {
    if (this.hover === id) return false;
    this.hover = id;
    this.tags = tags;
    this.refresh?.();
    return true;
  }

  /** 지금 그려진 상자들 — 호버 판정에 쓴다. */
  current(): readonly TradeBox[] {
    return this.boxes;
  }

  /** 시각 → x 좌표. 화면 밖이면 보이는 범위의 어느 쪽인지 보고 끝으로 붙인다. */
  private x(time: number, width: number): number | null {
    const chart = this.chart;
    if (chart === null) return null;
    const scale = chart.timeScale();
    const got = scale.timeToCoordinate(time as Time);
    if (got !== null) return got;
    const index = scale.timeToIndex(time as Time, true);
    if (index !== null) {
      const at = scale.logicalToCoordinate(index as unknown as Logical);
      if (at !== null) return at;
    }
    const visible = scale.getVisibleRange();
    if (visible === null) return null;
    if (time < (visible.from as number)) return 0;
    if (time > (visible.to as number)) return width;
    return null;
  }

  private toneColor(tone: TradeEdge["tone"]): string {
    return tone === "entry"
      ? this.colors.entry
      : tone === "gain"
        ? this.colors.gain
        : tone === "loss"
          ? this.colors.loss
          : this.colors.flat;
  }

  /**
   * 판을 **둘로 나눈다** — 상자는 봉 아래, 글자는 봉 위.
   *
   * 🔴 한 판에 `zOrder: "bottom"` 으로 다 그렸더니 **글자가 봉에 묻혔다**
   * (사용자 신고 2026-09-21: *"텍스트가 봉에 묻히네"*). 깔개는 아래여야 봉을 안 가리고,
   * 딱지는 위여야 읽힌다 — 둘은 같은 층에 있을 수 없다.
   */
  paneViews() {
    const owner = this;
    const view = (order: "bottom" | "top", paint: (scope: Scope, s: ISeriesApi<"Candlestick">) => void) => ({
      zOrder: () => order,
      renderer: () => ({
        draw(target: { useBitmapCoordinateSpace: (fn: (scope: Scope) => void) => void }) {
          const series = owner.series;
          if (series === null) return;
          if (owner.boxes.length === 0 && owner.edges.length === 0) return;
          target.useBitmapCoordinateSpace((scope) => paint.call(owner, scope, series));
        },
      }),
    });
    return [view("bottom", owner.paintBoxes), view("top", owner.paintLabels)];
  }

  private paintBoxes(scope: Scope, series: ISeriesApi<"Candlestick">): void {
    const { context, bitmapSize, horizontalPixelRatio: hx, verticalPixelRatio: vy } = scope;
    const cssWidth = bitmapSize.width / hx;

    // ── 상자 ──
    for (const box of this.boxes) {
      const top = series.priceToCoordinate(box.high);
      const bottom = series.priceToCoordinate(box.low);
      const left = this.x(box.from, cssWidth);
      const right = this.x(box.to, cssWidth);
      if (top === null || bottom === null || left === null || right === null) continue;
      if (right <= left) continue;
      const lit = this.hover === box.id;
      const base = this.colors[TONE_OF[box.kind]];
      const x = left * hx;
      const y = top * vy;
      const w = Math.max(1, (right - left) * hx);
      const h = Math.max(1, (bottom - top) * vy);
      // 호버하면 진하게 — "지금 어느 매매를 보고 있나" 가 즉시 보여야 한다.
      context.fillStyle = washed(base, lit ? ALPHA[box.kind] * 2.1 : ALPHA[box.kind]);
      context.fillRect(x, y, w, h);
      if (lit || box.open) {
        context.save();
        context.strokeStyle = base;
        context.lineWidth = Math.max(1, hx);
        // 아직 안 닫힌 매매는 점선 — 끝난 것과 눈으로 갈린다.
        context.setLineDash(box.open ? [4 * hx, 3 * hx] : []);
        context.strokeRect(x + 0.5, y + 0.5, Math.max(1, w - 1), Math.max(1, h - 1));
        context.restore();
      }
    }
  }

  /** 봉 **위** 층 — 세로 경계선과 모든 글자. */
  private paintLabels(scope: Scope, series: ISeriesApi<"Candlestick">): void {
    const { context, bitmapSize, horizontalPixelRatio: hx, verticalPixelRatio: vy } = scope;
    const cssWidth = bitmapSize.width / hx;

    // ── 세로 경계 + 글자 ── (점 대신 줄 · 사용자 요구 2026-09-21)
    const fontPx = Math.round(11 * vy);
    context.save();
    context.font = `${fontPx}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    context.textBaseline = "top";
    for (const edge of this.edges) {
      const at = this.x(edge.at, cssWidth);
      if (at === null) continue;
      const lit = this.hover === edge.id;
      const color = this.toneColor(edge.tone);
      const x = Math.round(at * hx) + 0.5;
      context.save();
      context.strokeStyle = color;
      context.lineWidth = Math.max(1, (lit ? 2 : 1) * hx);
      context.globalAlpha = lit ? 1 : 0.55;
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, bitmapSize.height);
      context.stroke();
      context.restore();
      // 글자는 상자 **안쪽**으로 민다 — 왼쪽 경계는 오른쪽에, 오른쪽 경계는 왼쪽에.
      const width = context.measureText(edge.label).width;
      const pad = 4 * hx;
      const textX = edge.side === "open" ? x + pad : x - width - pad;
      const textY = 6 * vy;
      context.globalAlpha = lit ? 1 : 0.8;
      context.fillStyle = washed(this.colors.paper, 0.82);
      context.fillRect(textX - pad / 2, textY - pad / 2, width + pad, fontPx + pad);
      context.fillStyle = color;
      context.fillText(edge.label, textX, textY);
      context.globalAlpha = 1;
    }
    context.restore();

    // ── 호버 가격 딱지 — 각 상자 선 **좌측** ──
    if (this.hover !== null && this.tags.length > 0) {
      const owned = this.boxes.filter((b) => b.id === this.hover);
      const left = owned.length > 0 ? this.x(owned[0]!.from, cssWidth) : null;
      if (left !== null) {
        context.save();
        context.font = `${fontPx}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
        context.textBaseline = "middle";
        for (const tag of this.tags) {
          const at = series.priceToCoordinate(tag.price);
          if (at === null) continue;
          const color = this.toneColor(tag.tone);
          const width = context.measureText(tag.text).width;
          const pad = 5 * hx;
          // 상자 왼쪽 경계의 **바깥쪽**에 붙인다. 화면 왼쪽으로 넘치면 안쪽으로 접는다.
          const wanted = left * hx - width - pad * 2;
          const x = wanted < 0 ? left * hx + pad : wanted;
          const y = at * vy;
          context.fillStyle = washed(this.colors.paper, 0.9);
          context.fillRect(x - pad / 2, y - fontPx * 0.75, width + pad, fontPx * 1.5);
          context.strokeStyle = color;
          context.lineWidth = Math.max(1, hx);
          context.strokeRect(x - pad / 2, y - fontPx * 0.75, width + pad, fontPx * 1.5);
          context.fillStyle = color;
          context.fillText(tag.text, x, y);
        }
        context.restore();
      }
    }
  }
}
