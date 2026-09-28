/**
 * 원장 계획 → 차트 가로선 (순수 · `Chart` 가 그린다 · 시험이 부른다).
 */

import type { Plan } from "../chartTypes";

export const PLAN_LINES = [
  { key: "stop", label: "손절", token: "--loss", fallback: "#b4423a" },
  { key: "entry", label: "진입", token: "--entry-line", fallback: "#b8860b" },
  { key: "first", label: "1차", token: "--gain", fallback: "#0f7b6c" },
  { key: "target", label: "목표", token: "--gain", fallback: "#0f7b6c" },
] as const;

/** 계획선 한 줄 — 가격 · 색 토큰 · 이름. */
export type PlanLine = { price: number; token: string; fallback: string; title: string };

/**
 * 계획 하나 → 그릴 가로선들 (순수 · 시험이 부른다).
 *
 * 🔴 **추세추종(full_ride)은 고정 익절이 없다** (사용자 지적 2026-08-24). 목표·1차선은 진입+100R
 * 자리표시자라, 그리면 "29배 목표" 처럼 거짓말한다 — 숨긴다. 그리고 손절선이 곧 청산선이다(봉마다
 * SMA 로 상향 트레일) — 이름을 "청산" 으로 바꿔 실제 동작을 말한다.
 *
 * ⭐ 진입 대비 % 와 손익비를 이름에 — 트레이딩뷰 포지션 도구처럼 선만 보고 읽힌다(사용자 2026-09-11).
 *
 * @param plan 계획.
 * @param prefix 이름 앞에 붙일 말 — 몫이 여럿일 때 다리 이름(T320). 없으면 빈 문자열.
 * @returns 그릴 선들. 값이 없는 선은 뺀다.
 */
export function planLinesOf(plan: Plan, prefix = ""): PlanLine[] {
  const ride = plan.full_ride === true;
  const entryPrice = Number(plan.entry);
  const pctOf = (price: number): string => {
    if (!Number.isFinite(entryPrice) || entryPrice === 0) return "";
    const pct = ((price - entryPrice) / entryPrice) * 100;
    return ` ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
  };
  const rr = (plan as { rr?: string }).rr;
  const out: PlanLine[] = [];
  for (const spec of PLAN_LINES) {
    if (ride && (spec.key === "first" || spec.key === "target")) continue;
    const raw = plan[spec.key];
    if (raw === undefined || raw === null) continue;
    const title =
      ride && spec.key === "stop"
        ? "청산(트레일)"
        : spec.key === "entry"
          ? spec.label
          : `${spec.label}${pctOf(Number(raw))}${spec.key === "target" && rr ? ` · 손익비 ${rr}` : ""}`;
    out.push({
      price: Number(raw),
      token: spec.token,
      fallback: spec.fallback,
      title: `${prefix}${title}`,
    });
  }
  return out;
}
