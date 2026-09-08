/**
 * 지표 레지스트리 — **어떤 지표가 있는지** 아는 유일한 곳 (types.ts 머리말).
 *
 * 설정(`IndicatorSpec`)은 저장되는 값이라 평평한 데이터다. 그것을 `Indicator` 객체로 바꾸는 일이 여기 있다.
 * 새 지표를 더하려면: 파일 1개(compute) + 아래 `build` 의 분기 1줄 + `TYPES` 에 이름 1줄.
 */
import { BollingerBands } from "./bollinger";
import { MovingAverage } from "./movingAverage";
import type { Indicator } from "./types";

export type IndicatorType = "sma" | "ema" | "bb";

/** 저장되는 지표 설정 하나 — 평평한 값만 (localStorage 에 그대로 간다). */
export interface IndicatorSpec {
  id: string;
  type: IndicatorType;
  length: number;
  /** 볼린저의 표준편차 배수 — 이평은 없다. */
  k?: number;
  enabled: boolean;
  color: string;
}

export const TYPES: Record<IndicatorType, { label: string; hasK: boolean }> = {
  sma: { label: "단순 이동평균 (SMA)", hasK: false },
  ema: { label: "지수 이동평균 (EMA)", hasK: false },
  bb: { label: "볼린저 밴드 (BB)", hasK: true },
};

/** 지표 색 — 캔들(초록/빨강)·계획선(황금/빨강/초록)과 겹치지 않는 것들. */
export const INDICATOR_PALETTE = ["#8957e5", "#0e7490", "#b8860b", "#607d8b", "#c65a2e", "#2e7d32", "#5b3fd1"];

/** 사용자 기본 — 이평 시리즈(20 · 50 · 200) + 볼린저 20/2 · 4/4. 20/2 만 켜 둔다 (겹치면 봉이 안 보인다). */
export const DEFAULT_SPECS: IndicatorSpec[] = [
  { id: "sma20", type: "sma", length: 20, enabled: true, color: "#8957e5" },
  { id: "sma50", type: "sma", length: 50, enabled: false, color: "#0e7490" },
  { id: "sma200", type: "sma", length: 200, enabled: false, color: "#607d8b" },
  { id: "bb20-2", type: "bb", length: 20, k: 2, enabled: true, color: "#b8860b" },
  { id: "bb4-4", type: "bb", length: 4, k: 4, enabled: false, color: "#c65a2e" },
];

export function describe(spec: IndicatorSpec): string {
  if (spec.type === "bb") return `BB ${spec.length}/${spec.k ?? 2}`;
  return `${spec.type.toUpperCase()} ${spec.length}`;
}

/** 설정 하나 → 지표 객체. 모르는 타입은 `null` (저장된 옛 설정이 깨져 있어도 화면은 산다). */
export function build(spec: IndicatorSpec): Indicator | null {
  if (!(spec.length > 0)) return null;
  switch (spec.type) {
    case "sma":
    case "ema":
      return new MovingAverage({ id: spec.id, kind: spec.type, length: spec.length, color: spec.color });
    case "bb":
      return new BollingerBands({ id: spec.id, length: spec.length, k: spec.k ?? 2, color: spec.color });
    default:
      return null;
  }
}

/** 켜진 설정만 지표로. */
export function buildEnabled(specs: readonly IndicatorSpec[]): Indicator[] {
  const out: Indicator[] = [];
  for (const spec of specs) {
    if (!spec.enabled) continue;
    const made = build(spec);
    if (made) out.push(made);
  }
  return out;
}

/** 새 설정의 id — 같은 종류·길이·k 면 같은 id 라 중복이 안 쌓인다. */
export function specId(type: IndicatorType, length: number, k?: number): string {
  return type === "bb" ? `bb${length}-${k ?? 2}` : `${type}${length}`;
}
