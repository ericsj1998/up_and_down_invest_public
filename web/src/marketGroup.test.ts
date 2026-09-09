// T245 — 시장 전환: 쿠키 해석 · 묶음 거르기 · 옛 서버 응답(그룹 없음)은 코인.
import { describe, expect, it } from "vitest";
import type { MarketInfo } from "./api";
import { brokerOfName, groupOfName, marketGroupCookie, pickMarkets } from "./shell/marketGroup";

const ALL: MarketInfo[] = [
  { name: "GATE", ready: true, scoped: true, group: "coin", broker: "gate" },
  { name: "BINANCE", ready: true, scoped: true, group: "coin", broker: "binance" },
  { name: "NASDAQ", ready: true, scoped: true, group: "stock", broker: "toss" },
  { name: "KRX", ready: false, scoped: false, group: "stock", broker: "toss" },
];

describe("marketGroupCookie", () => {
  it("없으면 코인 · stock 일 때만 주식", () => {
    expect(marketGroupCookie("")).toBe("coin");
    expect(marketGroupCookie("updown_mode=demo; updown_market=stock")).toBe("stock");
    expect(marketGroupCookie("updown_market=coin")).toBe("coin");
    expect(marketGroupCookie("updown_market=weird")).toBe("coin");
  });
});

describe("pickMarkets", () => {
  it("고른 묶음의 시장만 남긴다", () => {
    expect(pickMarkets(ALL, "coin").map((m) => m.name)).toEqual(["GATE", "BINANCE"]);
    expect(pickMarkets(ALL, "stock").map((m) => m.name)).toEqual(["NASDAQ", "KRX"]);
  });
  it("group 이 없는 옛 응답은 전부 코인으로 본다 — 코인 화면이 안 바뀐다", () => {
    const old: MarketInfo[] = [{ name: "GATE", ready: true, scoped: true }];
    expect(pickMarkets(old, "coin")).toHaveLength(1);
    expect(pickMarkets(old, "stock")).toHaveLength(0);
  });
});

describe("groupOfName · brokerOfName", () => {
  it("서버가 말한 값 그대로 · 모르면 코인 / undefined", () => {
    expect(groupOfName(ALL, "NASDAQ")).toBe("stock");
    expect(groupOfName(ALL, "MOON")).toBe("coin");
    expect(brokerOfName(ALL, "NASDAQ")).toBe("toss");
    expect(brokerOfName(ALL, "MOON")).toBeUndefined();
  });
});
