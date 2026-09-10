/**
 * 펀드 상세(T261) 순수 조각 — 서버 일봉 → 차트 봉, 등락 색.
 */

import type { FundMember } from "./api";
import type { Ohlc } from "./chart/indicators";

/** 서버 봉(문자열 가격) → 차트 봉. 숫자가 아닌 줄은 버린다. */
export function toOhlc(bars: FundMember["bars"]): Ohlc[] {
  const out: Ohlc[] = [];
  for (const b of bars) {
    const o = Number(b.open);
    const h = Number(b.high);
    const l = Number(b.low);
    const c = Number(b.close);
    if (![o, h, l, c].every(Number.isFinite)) continue;
    out.push({ time: b.time, open: o, high: h, low: l, close: c });
  }
  return out;
}

/** 등락 문장 — `+1.25%` · 값이 없으면 `—`. */
export function changeText(pct: string | null | undefined): string {
  if (pct === null || pct === undefined) return "—";
  const v = Number(pct);
  if (!Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** 등락 색 클래스 — 화면 전체가 한 값(.gain/.loss)을 쓴다. */
export function changeTone(pct: string | null | undefined): "gain" | "loss" | "" {
  const v = Number(pct);
  if (!Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "gain" : "loss";
}
