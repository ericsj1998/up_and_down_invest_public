/**
 * 채점 화면(dev · T281)의 서버 계약 — `/admin/grading/*`. 관리자만 통과한다(서버가 막는다).
 */
import { request } from "../api";
import type { Ohlc } from "../chart/indicators";
import type { GradingTrade, Marks, Summary } from "./grading";

export interface RunSummary {
  file: string;
  dir: string;
  venue: string;
  generated_at: string;
  cost: string;
  symbols: string[];
  configs: { name: string; trades: number }[];
  /** 주력(★) — 서버 env `UPDOWN_GRADING_MAIN` 글롭에 맞는 파일. 목록 맨 위. */
  main?: boolean;
}

export function gradingRuns(): Promise<{ dirs: string[]; runs: RunSummary[] }> {
  return request("/admin/grading/runs");
}

export function gradingTrades(
  file: string,
  config: string,
  symbol: string,
): Promise<{
  file: string;
  config: string;
  symbol: string;
  venue: string;
  market: string | null;
  trades: GradingTrade[];
  summary: { config: Summary; symbol: Summary };
}> {
  const q = new URLSearchParams({ file, config, symbol });
  return request(`/admin/grading/trades?${q.toString()}`);
}

export interface CandleRow extends Ohlc {
  volume: number;
}

export function gradingCandles(
  market: string,
  symbol: string,
  timeframe: string,
  start: number,
  end: number,
): Promise<{ market: string; symbol: string; timeframe: string; candles: CandleRow[] }> {
  const q = new URLSearchParams({ market, symbol, timeframe, start: String(start), end: String(end) });
  // 봉 수천 개 — DB 에 없으면 브로커에서 받으므로 시한을 넉넉히.
  return request(`/admin/grading/candles?${q.toString()}`, undefined, 60_000);
}

export function gradingMarks(file: string, config: string, symbol: string): Promise<Marks> {
  const q = new URLSearchParams({ file, config, symbol });
  return request(`/admin/grading/marks?${q.toString()}`);
}

export function saveGradingMarks(file: string, config: string, symbol: string, marks: Marks): Promise<Marks> {
  const q = new URLSearchParams({ file, config, symbol });
  return request(`/admin/grading/marks?${q.toString()}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ grades: marks.grades, positions: marks.positions, notes: marks.notes }),
  });
}
