import { describe, expect, it } from "vitest";
import type { TradeAdd } from "./api";
import { addLabel, closedPct } from "./tradeAdd";

const add = (over: Partial<TradeAdd> = {}): TradeAdd => ({
  at: "2026-09-25T13:00:00+00:00",
  price: "132.1",
  frac: "0.5",
  exposure: "2.5",
  held: null,
  contracts: 3,
  fill: "132.4",
  pnl: "-4.25",
  ...over,
});

describe("closedPct — 닫힌 매매 손익에 불타기 추가분을 같은 분모로 더한다", () => {
  it("자리 예산 69.55 · 처음 크기 +10% · 추가분 +6.955 USDT → +20%", () => {
    expect(closedPct(10, "69.55", add({ pnl: "6.955" }))).toBeCloseTo(20);
  });
  it("추가가 없거나 분모를 모르면 gain_pct 그대로", () => {
    expect(closedPct(10, "69.55", null)).toBe(10);
    expect(closedPct(10, null, add({ pnl: "5" }))).toBe(10);
    expect(closedPct(null, "69.55", add())).toBeNull();
  });
});

describe("addLabel — 불타기 한 줄", () => {
  it("채워졌으면 계약 · 체결가 · 추가분", () => {
    expect(addLabel(add())).toBe("불타기 +3계약 @132.4 · 추가분 -4.25 USDT");
  });
  it("버렸으면 사유", () => {
    expect(addLabel(add({ contracts: 0, held: "margin", pnl: "0" }))).toBe("불타기 버림 (margin)");
  });
  it("없으면 null", () => {
    expect(addLabel(null)).toBeNull();
  });
});
