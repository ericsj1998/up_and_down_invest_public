import { describe, expect, it } from "vitest";
import type { ForecastTrade } from "../api";
import { forecastLines } from "../ForecastNotes";
import { forecastShapes, futureBars, priceRange } from "./forecast";

const T = "2026-11-11T12:00:00+00:00";
const at = (h: number) => new Date(Date.parse(T) + h * 3600_000).toISOString();

const trade: ForecastTrade = {
  trade_id: "a",
  leg: "시험 롱",
  ok: true,
  basis: { at: T, hold_h: 8, on_grid: true, cur_r: 0.4, entry_ref: 100, stop_pct: 5, stop_source: "initial", btc_known: true },
  band: { q25: 98, q50: 102, q75: 106, q90: 110, note: null },
  fan: {
    origin: { at: T, price: 102 },
    qs: [0.1, 0.25, 0.5, 0.75, 0.9],
    points: [
      { at: at(12), p: [96, 99, 102, 105, 108] },
      { at: at(24), p: [94, 98, 102, 106, 110] },
      { at: at(48), p: [90, 96, 102, 108, 114] },
    ],
  },
  branch: {
    p: [0.2, 0.3, 0.5],
    lines: [
      { name: "유리", p: 0.5, points: [{ at: T, price: 102 }, { at: at(48), price: 110 }] },
      { name: "불리", p: 0.2, points: [{ at: T, price: 102 }, { at: at(48), price: 95 }] },
    ],
  },
};

describe("forecastShapes", () => {
  it("부채꼴은 기준점에서 시작해 10 ~ 90 · 25 ~ 75 로 벌어진다", () => {
    const [s] = forecastShapes([trade]);
    expect(s).toBeDefined();
    expect(s!.outer[0]).toEqual({ ts: Date.parse(T) / 1000, lo: 102, hi: 102 });
    expect(s!.outer[3]).toEqual({ ts: Date.parse(at(48)) / 1000, lo: 90, hi: 114 });
    expect(s!.inner[3]).toEqual({ ts: Date.parse(at(48)) / 1000, lo: 96, hi: 108 });
    expect(s!.band).toEqual({ low: 98, mid: 102, high: 106, tail: 110 });
    expect(s!.branches.map((b) => b.name)).toEqual(["유리", "불리"]);
  });

  it("못 그리는 매매는 뺀다", () => {
    expect(forecastShapes([{ trade_id: "b", leg: "x", ok: false, reason: "창 밖" }])).toEqual([]);
  });

  it("가격 축 범위는 부채꼴 · 띠 · 꼬리 · 갈래를 다 담는다", () => {
    expect(priceRange(forecastShapes([trade]))).toEqual({ min: 90, max: 114 });
    expect(priceRange([])).toBeNull();
  });

  it("미래 여백은 가장 먼 점까지 봉 수로", () => {
    const shapes = forecastShapes([trade]);
    expect(futureBars(shapes, Date.parse(T) / 1000, 3600)).toBe(48);
    expect(futureBars(shapes, Date.parse(T) / 1000, 4 * 3600)).toBe(12);
    expect(futureBars([], 0, 3600)).toBe(0);
  });
});

describe("forecastLines", () => {
  it("검증 간격이 아니거나 손절을 가정했으면 말한다", () => {
    const lines = forecastLines({
      ...trade,
      basis: { ...trade.basis!, on_grid: false, stop_source: "assumed", btc_known: false },
    });
    expect(lines[0]).toContain("연구 검증 간격 아님");
    expect(lines[0]).toContain("처음 손절 모름");
    expect(lines[0]).toContain("BTC 국면 모름");
    expect(lines.some((l) => l.includes("유리 50%"))).toBe(true);
  });

  it("못 그린 까닭을 적는다", () => {
    expect(forecastLines({ trade_id: "b", leg: "시험 숏", ok: false, reason: "손절이 본전 이상" })).toEqual([
      "시험 숏: 예측 안 그림 — 손절이 본전 이상",
    ]);
  });
});
