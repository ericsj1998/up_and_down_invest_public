/**
 * 매매 표기 — 진입 타점 · 롱/숏 · 익절/청산 위치 · 진입 시각·가격 · 손절선 · 손익 영역 (사용자 요구 2026-09-06).
 *
 * 순수 함수다: 매매 목록 + 설정 → 마커 · 가로선 · 영역. 캔버스는 `PriceChart.tsx` 가 만진다.
 *
 * 영역은 **선택한 매매 하나**에만 칠한다. 수천 매매에 전부 칠하면 차트가 안 보인다 — 마커는 전부, 영역·선은 하나.
 *   - 붉은 영역: 진입가 ↔ 손절선 (그 매매가 감수한 위험)
 *   - 초록 영역: 진입가 ↔ 청산가 (유리하게 끝났을 때 — 익절 구간)
 *   - 짙은 붉은 영역: 손절선 ↔ 청산가 (손절선을 **지나서** 끝났을 때 = 청산(liq)·갭. "어디서 당했나" 가 이 띠다)
 */
import type { MarkSettings } from "./indicators/settings";

export interface TradeMark {
  id: string;
  symbol: string;
  /** 1 = 롱 · -1 = 숏. */
  side: 1 | -1;
  entry: number;
  exit: number;
  stop: number;
  openedTs: number;
  closedTs: number;
  /** 매매 손익 — 감사 권한이 없으면 null (그때 색은 중립). */
  pnl: number | null;
  /** 도구의 청산 사유 — hard_sl · liq · tp · soft … */
  reason: string;
  leg?: string;
  /**
   * **아직 안 닫혔다** (라이브 · 2026-09-21).
   *
   * 참이면 `exit` 는 청산가가 아니라 **지금가**이고 `closedTs` 는 마지막 봉이다 —
   * 화면은 그것을 "청산" 이라 부르지 않고 "지금 / 보유중" 이라 적는다.
   */
  open?: boolean;
  /**
   * **이 매매가 실제로 건 돈** (USDT · 증거금).
   *
   * `pnl` 이 이 돈 대비 % 이므로, 둘을 곱하면 손익 **금액**이 나온다. 없으면(백테스트 ·
   * 단독 판 · 옛 행) 화면은 % 만 적는다 — 다른 돈을 끌어다 곱하지 않는다.
   */
  margin?: number | null;
  /** 불타기 한 줄(T308) — `불타기 +3계약 @132.4 · 추가분 -4.25 USDT` · 없으면 null. */
  add?: string | null;
}

export const REASON_LABEL: Record<string, string> = {
  liq: "강제청산",
  hard_sl: "손절",
  soft: "약화 청산",
  soft_maker: "약화 청산(지정가)",
  soft_late: "약화 청산(지연)",
  tp: "익절",
  half: "절반 익절",
  timeout: "시간 만료",
  eos: "기간 끝",
};

export function reasonLabel(reason: string): string {
  return REASON_LABEL[reason] ?? reason;
}

export function sideLabel(side: 1 | -1): string {
  return side === 1 ? "롱" : "숏";
}

/** 시각을 봉 시작으로 내린다 — 봉에 안 걸린 마커는 그려지지 않는다. */
export function snap(ts: number, step: number): number {
  return step > 0 ? ts - (ts % step) : ts;
}

/** 손절선 대비 청산가가 얼마나 더 갔나 (%). 양수면 손절선을 지나 더 나쁘게 끝났다 — 청산(liq)의 크기. */
export function beyondStopPct(t: TradeMark): number {
  if (!(t.stop > 0)) return 0;
  return ((t.exit - t.stop) / t.stop) * -t.side * 100;
}

/** 유리하게 끝났나 (수수료 전 · 가격만). */
export function favorable(t: TradeMark): boolean {
  return (t.exit - t.entry) * t.side > 0;
}

export function fmtPrice(v: number): string {
  const digits = v >= 1000 ? 0 : v >= 10 ? 2 : v >= 1 ? 3 : 5;
  return v.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export interface Marker {
  time: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  tone: "entry" | "gain" | "loss";
  text: string;
  size: number;
}

/**
 * 마커 — 진입은 화살표(롱 ↑ 아래 · 숏 ↓ 위), 청산은 원(손익 색). 선택한 매매는 크게.
 * 같은 봉에 여럿이면 라이브러리가 겹쳐 그린다 — 글자는 선택한 것에만 남겨 덜 지저분하게.
 */
export function tradeMarkers(
  trades: readonly TradeMark[],
  step: number,
  marks: MarkSettings,
  focusId: string | null,
): Marker[] {
  const out: Marker[] = [];
  const many = trades.length > 40;
  for (const t of trades) {
    const focused = t.id === focusId;
    const labels = marks.labels && (focused || !many);
    if (marks.entry) {
      out.push({
        time: snap(t.openedTs, step),
        position: t.side === 1 ? "belowBar" : "aboveBar",
        shape: t.side === 1 ? "arrowUp" : "arrowDown",
        tone: "entry",
        text: labels ? `${sideLabel(t.side)} 진입 ${fmtPrice(t.entry)}` : "",
        size: focused ? 2 : 1,
      });
    }
    if (marks.exit) {
      const win = (t.pnl ?? 0) >= 0;
      out.push({
        time: snap(t.closedTs, step),
        position: t.side === 1 ? "aboveBar" : "belowBar",
        shape: t.reason === "liq" ? "square" : "circle",
        tone: win ? "gain" : "loss",
        text: labels ? `${reasonLabel(t.reason)} ${fmtPrice(t.exit)}` : "",
        size: focused ? 2 : 1,
      });
    }
  }
  out.sort((a, b) => a.time - b.time);
  return out;
}

export interface Level {
  price: number;
  title: string;
  tone: "entry" | "gain" | "loss";
  dashed: boolean;
}

/** 선택한 매매의 가로선 — 손절 · 진입 · 청산. */
export function tradeLevels(t: TradeMark, marks: MarkSettings): Level[] {
  if (!marks.lines) return [];
  const out: Level[] = [
    { price: t.stop, title: `손절 ${fmtPrice(t.stop)}`, tone: "loss", dashed: true },
    { price: t.entry, title: `${sideLabel(t.side)} 진입 ${fmtPrice(t.entry)}`, tone: "entry", dashed: false },
    { price: t.exit, title: `${reasonLabel(t.reason)} ${fmtPrice(t.exit)}`, tone: (t.pnl ?? 0) >= 0 ? "gain" : "loss", dashed: false },
  ];
  return out;
}

export interface Zone {
  from: number;
  to: number;
  low: number;
  high: number;
  tone: "gain" | "loss";
  alpha: number;
}

/** 선택한 매매의 영역 — 시간은 [진입, 청산] (최소 한 봉). */
export function tradeZones(t: TradeMark, step: number, marks: MarkSettings): Zone[] {
  if (!marks.zones) return [];
  const from = snap(t.openedTs, step);
  const to = Math.max(snap(t.closedTs, step) + step, from + step);
  const zones: Zone[] = [
    { from, to, low: Math.min(t.entry, t.stop), high: Math.max(t.entry, t.stop), tone: "loss", alpha: 0.14 },
  ];
  if (favorable(t)) {
    zones.push({ from, to, low: Math.min(t.entry, t.exit), high: Math.max(t.entry, t.exit), tone: "gain", alpha: 0.16 });
  } else if ((t.exit - t.stop) * t.side < 0) {
    // 손절선을 지나 끝났다 — 청산(liq)·갭. 이 띠가 "어디서 당했나" 다.
    zones.push({ from, to, low: Math.min(t.stop, t.exit), high: Math.max(t.stop, t.exit), tone: "loss", alpha: 0.32 });
  }
  return zones;
}

/** 서버 매매 행(공통 모양) → TradeMark. */
export function toTradeMark(
  r: {
    symbol: string;
    side: number;
    entry: number;
    exit: number;
    stop: number;
    opened_ts: number;
    closed_ts: number;
    pnl: number | null;
    reason?: string;
    leg?: string;
  },
  index: number,
): TradeMark {
  return {
    id: `${r.symbol}:${r.opened_ts}:${index}`,
    symbol: r.symbol,
    side: r.side >= 0 ? 1 : -1,
    entry: r.entry,
    exit: r.exit,
    stop: r.stop,
    openedTs: r.opened_ts,
    closedTs: r.closed_ts,
    pnl: r.pnl,
    reason: r.reason ?? "liq",
    leg: r.leg,
  };
}
