/**
 * 숫자 · 이름 모양 바꾸기 — 순수 함수(시험 `format.test.ts`).
 */

import type { WhaleManagerView } from "./api";

export const TOP_SLICES = 12;

/** 달러를 짧게 — 1.2B · 340M · 12K. */
export function usdCompact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

/** 0 ~ 1 비율 → "12.3%". */
export function pct(x: number, digits = 1): string {
  return `${(x * 100).toFixed(digits)}%`;
}

/** 이미 % 인 값 → "+3.25%" · 없으면 "—". */
export function signedPct(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${x > 0 ? "+" : ""}${x.toFixed(digits)}%`;
}

/** 사진이 없을 때 쓰는 머리글자 — "Warren Buffett" → WB. */
export function initials(person: string, label: string): string {
  const src = (person || label).trim();
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return parts
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

/** 원형 그래프 조각 — 상위 N 개 + "그 밖". 현물 줄만(Put/Call 은 비중이 아니라 방향 정보라 뺀다). */
export function slicesOf(view: WhaleManagerView | null, reportIdx: number) {
  const rep = view?.reports[reportIdx];
  if (!rep) return { labels: [] as string[], series: [] as number[], cusips: [] as (string | null)[] };
  const spot = rep.holdings.filter((h) => !h.put_call);
  const top = spot.slice(0, TOP_SLICES);
  const rest = spot.slice(TOP_SLICES).reduce((acc, h) => acc + h.weight, 0);
  const labels = top.map((h) => h.issuer);
  const series = top.map((h) => Math.round(h.weight * 10000) / 100);
  const cusips: (string | null)[] = top.map((h) => h.cusip);
  if (rest > 0) {
    labels.push(`그 밖 ${spot.length - top.length}종`);
    series.push(Math.round(rest * 10000) / 100);
    cusips.push(null);
  }
  return { labels, series, cusips };
}
