/**
 * 주식 주문 창 산수 (T250) — 정수 주 · 현금 한도 · 장중만 · 권한.
 */

import { describe, expect, it } from "vitest";
import { cashNeeded, defaultDraft, maxShares, orderBlockers } from "./stockOrder";

describe("cashNeeded · maxShares", () => {
  it("주수 x 진입가 · 살 수 있는 최대 정수 주", () => {
    expect(cashNeeded(3, 100.5)).toBe(304.55);
    expect(maxShares(1000, 316.22)).toBe(3);
    expect(maxShares(320, 316.22)).toBe(1);
    expect(maxShares(0, 10)).toBe(0);
    expect(cashNeeded(-1, 10)).toBe(0);
  });
});

describe("defaultDraft", () => {
  it("마지막 종가 기준 롱 초안 — 손절 아래 · 익절 위", () => {
    const draft = defaultDraft(100);
    expect(draft).not.toBeNull();
    expect(draft?.long).toBe(true);
    expect(draft?.stop).toBeLessThan(100);
    expect(draft?.first).toBeGreaterThan(100);
    expect(draft?.target).toBeGreaterThan(draft?.first ?? 0);
    expect(defaultDraft(0)).toBeNull();
  });
});

describe("orderBlockers", () => {
  it("열린 장 · 정수 주 · 현금 충분 · 권한 있으면 비어 있다", () => {
    expect(
      orderBlockers({ shares: 2, entry: 100, cash: 500, marketState: "open", allowed: true }),
    ).toEqual([]);
  });
  it("소수 주 · 현금 부족 · 장 마감 · 권한 없음을 각각 말한다", () => {
    const got = orderBlockers({
      shares: 1.5,
      entry: 100,
      cash: 100,
      marketState: "closed",
      allowed: false,
    });
    expect(got.some((r) => r.includes("정수"))).toBe(true);
    expect(got.some((r) => r.includes("현금 부족"))).toBe(true);
    expect(got.some((r) => r.includes("열려 있지"))).toBe(true);
    expect(got.some((r) => r.includes("권한"))).toBe(true);
  });
  it("현금을 모르면 현금 검사는 서버에 맡긴다", () => {
    expect(
      orderBlockers({ shares: 999, entry: 100, cash: null, marketState: "open", allowed: true }),
    ).toEqual([]);
  });
});
