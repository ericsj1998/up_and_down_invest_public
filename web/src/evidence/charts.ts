/**
 * 차트 상세 API (`/evidence/synthetic/*` · `/evidence/backtest/*`) — 자료형 · 호출 · 그리기용 변환.
 *
 * 서버는 저장된 배열을 잘라 줄 뿐이다(계산 없음). 여기서 하는 변환도 모양 바꾸기(열 → 봉 객체 · 배수)뿐이다.
 */
import { request } from "../api";
import type { Ohlc } from "../chart/indicators";
import { toTradeMark, type TradeMark } from "../chart/trades";

export interface Columns {
  t: number[];
  o: number[];
  h: number[];
  l: number[];
  c: number[];
}

export interface TradeRow {
  symbol: string;
  leg?: string;
  side: number;
  entry: number;
  exit: number;
  qty?: number;
  stop: number;
  opened_ts: number;
  closed_ts: number;
  /** 매매 손익. 감사 권한이 없으면 서버가 null 로 보낸다. */
  pnl: number | null;
  fees?: number;
  funding?: number;
  reason?: string;
  risk_mult?: number;
}

export interface Stats {
  by_reason?: Record<string, number>;
  trades_by_symbol?: Record<string, number>;
}

export interface SyntheticRow extends Stats {
  k: number;
  scenario: string;
  seed: number;
  total_pct: number | null;
  mdd_pct: number | null;
  liquidations: number | null;
  trades?: number;
  liquidations_by_symbol?: Record<string, number>;
}

export interface LiquidationSummary {
  futures: number;
  futures_hit: number;
  liquidations: number;
  trades: number;
  /** 청산 / 전체 매매 (%). */
  pct: number;
  by_symbol: {
    symbol: string;
    liquidations: number;
    trades: number;
    pct: number;
    futures: number;
  }[];
  by_scenario: {
    scenario: string;
    liquidations: number;
    futures_hit: number;
    futures: number;
  }[];
}

export interface SyntheticList {
  futures: SyntheticRow[];
  summary?: LiquidationSummary;
  symbols: string[];
  daily: { days: number; ts0: number };
  block: string | null;
  basis: string | null;
  generated: string | null;
  caveat: string;
}

export interface SyntheticDetail {
  /** 서버가 손익을 가렸다 (감사 권한 없음) — 수익률·펀드 자본 곡선·매매별 손익이 null. */
  redacted?: boolean;
  k: number;
  scenario: string;
  seed: number;
  mu_pct: number;
  total_pct: number | null;
  mdd_pct: number;
  liquidations: number;
  symbols: string[];
  equity: {
    t: number[];
    fund: number[] | null;
    market_log: number[];
    step: number;
  };
  liquidation_trades: (TradeRow & { future: number })[];
  windows: { symbol: string; closed_ts: number; bars: number; step: number }[];
  stats: Stats;
  daily: { days: number; ts0: number; step: number };
  caveat: string;
}

export interface CandlesResponse {
  symbol: string;
  frame: string;
  closed_ts?: number;
  candles: Columns;
  trades: TradeRow[];
  total_bars?: number;
}

export interface BacktestSummary {
  id: string;
  label?: string;
  playbook?: string;
  symbols?: string[];
  frame?: string;
  years?: number;
  total_pct?: number;
  cagr_pct?: number;
  mdd_pct?: number;
  calmar?: number;
  trades_count?: number;
  liquidations?: number;
  by_reason?: Record<string, number>;
  trades_by_symbol?: Record<string, number>;
  documented?: {
    note: string;
    total_pct: number;
    mdd_pct: number;
    years: number;
    trades_count: number;
  };
  generated?: string;
  missing?: boolean;
  reason?: string;
  /** 서버가 이 항목의 손익을 가렸다 — 그 매매법의 백테스트 권한이 없다 (T230). */
  redacted?: boolean;
}

export interface BacktestDetail extends BacktestSummary {
  /** 서버가 손익을 가렸다 (감사 권한 없음) — 수익률·자본 곡선·매매별 손익이 null. */
  redacted?: boolean;
  equity: { t: number[]; value: number[] | null };
  trades: TradeRow[];
}

export const syntheticList = () =>
  request<SyntheticList>("/evidence/synthetic", undefined, 60_000);
export const syntheticDetail = (k: number) =>
  request<SyntheticDetail>(`/evidence/synthetic/${k}`, undefined, 60_000);
export const syntheticCandles = (k: number, symbol: string, window?: number) =>
  request<CandlesResponse>(
    `/evidence/synthetic/${k}/candles?symbol=${encodeURIComponent(symbol)}${window ? `&window=${window}` : ""}`,
    undefined,
    60_000,
  );
export const backtestList = () =>
  request<{ backtests: BacktestSummary[] }>(
    "/evidence/backtest",
    undefined,
    60_000,
  );
export const backtestDetail = (id: string) =>
  request<BacktestDetail>(`/evidence/backtest/${id}`, undefined, 90_000);
export const backtestCandles = (id: string, symbol: string) =>
  request<CandlesResponse>(
    `/evidence/backtest/${id}/candles?symbol=${encodeURIComponent(symbol)}`,
    undefined,
    90_000,
  );

/** 열 → 봉 객체. */
export function toOhlc(c: Columns): Ohlc[] {
  const out: Ohlc[] = [];
  for (let i = 0; i < c.t.length; i++) {
    const t = c.t[i];
    const o = c.o[i];
    const h = c.h[i];
    const l = c.l[i];
    const cl = c.c[i];
    if (
      t === undefined ||
      o === undefined ||
      h === undefined ||
      l === undefined ||
      cl === undefined
    )
      continue;
    out.push({ time: t, open: o, high: h, low: l, close: cl });
  }
  return out;
}

export function toTradeMarks(rows: readonly TradeRow[]): TradeMark[] {
  return rows.map((r, i) => toTradeMark(r, i));
}

/** 자본 → 배수 (첫 값 기준). 첫 값이 0 이하면 그대로. */
export function toMultiple(values: readonly number[]): number[] {
  const base = values[0] ?? 1;
  if (!(base > 0)) return [...values];
  return values.map((v) => v / base);
}

/** 로그 지수 → 배수. */
export function logToMultiple(values: readonly number[]): number[] {
  return values.map((v) => Math.exp(v));
}

export function fmtTs(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ");
}

export function holdLabel(openedTs: number, closedTs: number): string {
  const h = Math.max(0, closedTs - openedTs) / 3600;
  if (h < 1) return "같은 봉";
  if (h < 48) return `${h.toFixed(0)}시간`;
  return `${(h / 24).toFixed(1)}일`;
}
