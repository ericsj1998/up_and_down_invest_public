/**
 * 이동평균 — 단순(SMA) · 지수(EMA). 표시용 (types.ts 머리말).
 *
 * 값이 모자란 앞부분(길이 미만)은 점을 내지 않는다 — 0 이나 부분 평균을 그리면 선이 시작에서 거짓말한다.
 */
import type { Indicator, Ohlc, OverlaySeries, Point } from "./types";

export type MaKind = "sma" | "ema";

/** 단순 이동평균 — 길이 미만 자리는 `null`. */
export function sma(values: readonly number[], length: number): (number | null)[] {
  const out: (number | null)[] = new Array<number | null>(values.length).fill(null);
  if (length <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i] ?? 0;
    if (i >= length) sum -= values[i - length] ?? 0;
    if (i >= length - 1) out[i] = sum / length;
  }
  return out;
}

/**
 * 지수 이동평균 — 첫 값은 앞 `length` 개의 단순 평균으로 씨앗을 놓는다 (TradingView 와 같은 관례).
 * 배수 α = 2 / (length + 1).
 */
export function ema(values: readonly number[], length: number): (number | null)[] {
  const out: (number | null)[] = new Array<number | null>(values.length).fill(null);
  if (length <= 0 || values.length < length) return out;
  const alpha = 2 / (length + 1);
  let seed = 0;
  for (let i = 0; i < length; i++) seed += values[i] ?? 0;
  let prev = seed / length;
  out[length - 1] = prev;
  for (let i = length; i < values.length; i++) {
    prev = (values[i] ?? prev) * alpha + prev * (1 - alpha);
    out[i] = prev;
  }
  return out;
}

/** `null` 을 뺀 점 목록 — 시리즈는 빈 자리를 건너뛰어 그린다. */
export function toPoints(bars: readonly Ohlc[], values: readonly (number | null)[]): Point[] {
  const out: Point[] = [];
  for (let i = 0; i < bars.length; i++) {
    const v = values[i];
    const bar = bars[i];
    if (v !== null && v !== undefined && bar && Number.isFinite(v)) out.push({ time: bar.time, value: v });
  }
  return out;
}

export interface MovingAverageOptions {
  id: string;
  kind: MaKind;
  length: number;
  color: string;
}

export class MovingAverage implements Indicator {
  readonly id: string;
  readonly label: string;
  private readonly opts: MovingAverageOptions;

  constructor(opts: MovingAverageOptions) {
    this.opts = opts;
    this.id = opts.id;
    this.label = `${opts.kind.toUpperCase()} ${opts.length}`;
  }

  compute(bars: readonly Ohlc[]): OverlaySeries[] {
    const closes = bars.map((b) => b.close);
    const values = this.opts.kind === "ema" ? ema(closes, this.opts.length) : sma(closes, this.opts.length);
    return [{ key: this.id, label: this.label, color: this.opts.color, width: 1, points: toPoints(bars, values) }];
  }
}
