/**
 * 몫 손익 나누기 (T320) — 거래소 포지션 하나의 미실현을 몫마다.
 *
 * 지키는 것: 한쪽이 이기고 한쪽이 지면 부호가 갈린다 · 합은 거래소 미실현 · 증거금은 진입 명목 비 ·
 * 모르는 계약 · 거래소 값이 있으면 나누지 않는다(null).
 */
import { describe, expect, it } from "vitest";
import { splitPosition, type ShareInput } from "./shares";

const A: ShareInput = { id: "a", side: 1, entry: 100, contracts: 2 };
const B: ShareInput = { id: "b", side: 1, entry: 120, contracts: 3 };
// 5계약 · 표시가 110 · 승수 1 → 명목 550 · 미실현 = 2x10 + 3x(-10) = -10
const POSITION = {
  size: "5",
  mark_price: "110",
  value: "550",
  unrealised_pnl: "-10",
  margin: "56",
};

describe("splitPosition", () => {
  it("몫마다 자기 평단으로 — 한쪽은 이기고 한쪽은 진다", () => {
    const got = splitPosition([A, B], POSITION);
    expect(got?.get("a")?.usdt).toBeCloseTo(20, 9);
    expect(got?.get("b")?.usdt).toBeCloseTo(-30, 9);
  });

  it("증거금은 진입 명목 비 · % 는 그 몫 증거금 대비", () => {
    const got = splitPosition([A, B], POSITION);
    // 진입 명목 200 : 360
    expect(got?.get("a")?.margin).toBeCloseTo(20, 9);
    expect(got?.get("b")?.margin).toBeCloseTo(36, 9);
    expect(got?.get("a")?.pct).toBeCloseTo(100, 9);
  });

  it("합은 거래소 미실현과 같다 — 남는 차이는 계약 비로", () => {
    const got = splitPosition([A, B], { ...POSITION, unrealised_pnl: "-10.5" });
    const total = (got?.get("a")?.usdt ?? 0) + (got?.get("b")?.usdt ?? 0);
    expect(total).toBeCloseTo(-10.5, 9);
  });

  it("불타기 계약은 그 몫에 붙는다", () => {
    const got = splitPosition([{ ...A, addContracts: 1, addFill: 105 }, B], {
      ...POSITION,
      size: "6",
      value: "660",
      unrealised_pnl: "-5",
    });
    // A: 3x110 - (200 + 105) = 25 · B: -30 → 합 -5
    expect(got?.get("a")?.usdt).toBeCloseTo(25, 9);
  });

  it("계약 수를 모르는 몫이 있으면 나누지 않는다", () => {
    expect(splitPosition([A, { ...B, contracts: 0 }], POSITION)).toBeNull();
  });

  it("거래소 값을 못 읽으면 나누지 않는다", () => {
    expect(splitPosition([A, B], null)).toBeNull();
    expect(splitPosition([A, B], { ...POSITION, mark_price: "" })).toBeNull();
  });
});
