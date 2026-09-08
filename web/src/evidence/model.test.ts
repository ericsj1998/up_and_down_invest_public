import { describe, expect, it } from "vitest";
import { defaultWorld, groupByScenario, mdd, multiple, pct, yearsBetween, type LabRow, type World } from "./model";

function row(scenario: string, seed: number, total: number, mddPct: number, liq = 0): LabRow {
  return {
    scenario,
    mu_pct: -40,
    seed,
    total_pct: total,
    cagr_pct: 0,
    mdd_pct: mddPct,
    liquidations: liq,
    h3y_pct: 0,
    h2y_pct: 0,
    h1y_pct: 0,
    h1m_pct: 0,
    h1d_pct: 0,
  };
}

describe("multiple", () => {
  it("turns a return % into a capital multiple and never hits zero (log axis)", () => {
    expect(multiple(0)).toBe(1);
    expect(multiple(100)).toBe(2);
    expect(multiple(-80)).toBeCloseTo(0.2);
    expect(multiple(-100)).toBeGreaterThan(0);
  });
});

describe("pct / mdd formatting", () => {
  it("signs, groups thousands, and drops decimals above 1,000%", () => {
    expect(pct(6139.2)).toBe("+6,139%");
    expect(pct(-79.33)).toBe("−79.3%");
    expect(pct(0)).toBe("0.0%");
    expect(pct(null)).toBe("—");
  });
  it("MDD is always shown as a drawdown", () => {
    expect(mdd(84)).toBe("−84%");
    expect(mdd(9.14)).toBe("−9.1%");
    expect(mdd(undefined)).toBe("—");
  });
});

describe("groupByScenario", () => {
  it("keeps table order and summarises each drift's five seeds", () => {
    const rows = [row("A", 1, 10, 50), row("A", 2, 30, 60, 1), row("B", 1, -5, 90), row("A", 3, 20, 55)];
    const groups = groupByScenario(rows);
    expect(groups.map((g) => g.scenario)).toEqual(["A", "B"]);
    const [a, b] = groups;
    expect(a?.median_total_pct).toBe(20);
    expect(a?.min_total_pct).toBe(10);
    expect(a?.max_total_pct).toBe(30);
    expect(a?.median_mdd_pct).toBe(55);
    expect(a?.liquidated).toBe(1);
    expect(b?.median_total_pct).toBe(-5);
  });
});

describe("defaultWorld / yearsBetween", () => {
  const w = (id: string, canonical: boolean): World => ({
    id,
    label: id,
    canonical,
    header: [],
    summary: {
      n: 0,
      total_median_pct: 0,
      total_worst_pct: 0,
      total_best_pct: 0,
      total_p5_pct: 0,
      total_p95_pct: 0,
      cvar5_pct: 0,
      mdd_median_pct: 0,
      mdd_worst_pct: 0,
      liquidated_runs: 0,
      liquidations_total: 0,
    },
    rows: [],
  });
  it("prefers the canonical world", () => {
    expect(defaultWorld([w("b15", false), w("b15-90", true)])?.id).toBe("b15-90");
    expect(defaultWorld([w("b15", false)])?.id).toBe("b15");
    expect(defaultWorld([])).toBeNull();
  });
  it("years between dates, null when unreadable", () => {
    expect(yearsBetween("2022-02-03", "2026-08-21")).toBeCloseTo(4.55, 1);
    expect(yearsBetween(null, "2026-08-21")).toBeNull();
    expect(yearsBetween("2026-08-21", "2022-02-03")).toBeNull();
  });
});
