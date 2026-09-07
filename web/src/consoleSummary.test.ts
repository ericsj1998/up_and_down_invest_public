import { describe, expect, it } from "vitest";
import { positionsSummary } from "./consoleSummary";

describe("positionsSummary — 카드는 계좌 전체를 말한다", () => {
  it("실측 재현: BTC 기준으로 보면 없음이지만 ETH 롱 1 이 있다 (2026-09-06)", () => {
    const s = positionsSummary([
      { symbol: "ETH_USDT", size: "1", margin: "4.10", unrealised_pnl: "0.30", entry_price: "2448.58", leverage: "6" },
    ]);
    expect(s.count).toBe(1);
    expect(s.contracts).toBe(1);
    expect(s.margin).toBeCloseTo(4.1);
    expect(s.pnl).toBeCloseTo(0.3);
    expect(s.lines[0]).toBe("ETH_USDT 롱 1 @2,448.58 · 6x");
  });

  it("여러 종목을 더하고 숏은 절댓값으로 센다", () => {
    const s = positionsSummary([
      { symbol: "ETH_USDT", size: "2", margin: "10", unrealised_pnl: "1" },
      { symbol: "XRP_USDT", size: "-30", margin: "5", unrealised_pnl: "-0.5" },
    ]);
    expect(s.count).toBe(2);
    expect(s.contracts).toBe(32);
    expect(s.margin).toBe(15);
    expect(s.pnl).toBeCloseTo(0.5);
    expect(s.lines[1]).toBe("XRP_USDT 숏 30");
  });

  it("크기 0 스냅샷은 포지션이 아니고, 못 읽은 값은 0 으로 꾸미지 않고 unpriced 로 센다", () => {
    const s = positionsSummary([
      { symbol: "BTC_USDT", size: "0", margin: "0", unrealised_pnl: "0" },
      { symbol: "ADA_USDT", size: "5", margin: "", unrealised_pnl: "abc" },
    ]);
    expect(s.count).toBe(1);
    expect(s.unpriced).toBe(1);
    expect(s.margin).toBe(0);
  });

  it("빈 목록", () => {
    const s = positionsSummary([]);
    expect(s.count).toBe(0);
    expect(s.lines).toEqual([]);
  });
});
