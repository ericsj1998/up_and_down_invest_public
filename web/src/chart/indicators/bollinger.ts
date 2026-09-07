/**
 * 볼린저 밴드 — 중심 SMA(길이) ± k · 표준편차. 표시용 (types.ts 머리말).
 *
 * 표준편차는 **모집단**(n 으로 나눔)이다 — TradingView 기본과 같다. 사용자 기본 둘: 20/2(관례) · 4/4(짧고 넓게).
 */
import { sma, toPoints } from "./movingAverage";
import type { Indicator, Ohlc, OverlaySeries } from "./types";

export interface Bands {
  mid: (number | null)[];
  upper: (number | null)[];
  lower: (number | null)[];
}

export function bollinger(values: readonly number[], length: number, k: number): Bands {
  const mid = sma(values, length);
  const upper: (number | null)[] = new Array<number | null>(values.length).fill(null);
  const lower: (number | null)[] = new Array<number | null>(values.length).fill(null);
  if (length <= 0) return { mid, upper, lower };
  for (let i = length - 1; i < values.length; i++) {
    const m = mid[i];
    if (m === null || m === undefined) continue;
    let acc = 0;
    for (let j = i - length + 1; j <= i; j++) {
      const d = (values[j] ?? m) - m;
      acc += d * d;
    }
    const sd = Math.sqrt(acc / length);
    upper[i] = m + k * sd;
    lower[i] = m - k * sd;
  }
  return { mid, upper, lower };
}

export interface BollingerOptions {
  id: string;
  length: number;
  k: number;
  color: string;
}

export class BollingerBands implements Indicator {
  readonly id: string;
  readonly label: string;
  private readonly opts: BollingerOptions;

  constructor(opts: BollingerOptions) {
    this.opts = opts;
    this.id = opts.id;
    this.label = `BB ${opts.length}/${opts.k}`;
  }

  compute(bars: readonly Ohlc[]): OverlaySeries[] {
    const { mid, upper, lower } = bollinger(
      bars.map((b) => b.close),
      this.opts.length,
      this.opts.k,
    );
    const c = this.opts.color;
    return [
      { key: `${this.id}:upper`, label: `${this.label} 상단`, color: c, width: 1, points: toPoints(bars, upper) },
      { key: `${this.id}:mid`, label: `${this.label} 중심`, color: c, width: 1, dashed: true, points: toPoints(bars, mid) },
      { key: `${this.id}:lower`, label: `${this.label} 하단`, color: c, width: 1, points: toPoints(bars, lower) },
    ];
  }
}
