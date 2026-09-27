/**
 * 펀드 종목 묶음 · 순서 · 히트맵 색 (사용자 2026-09-27).
 *
 * 🔴 못 박는 것: 한 종목은 **한 묶음에만** 나온다(다리가 겹쳐도) · 묶음 안은 거래대금 내림차순 · 값 없는 종목은 뒤.
 */

import { describe, expect, it } from "vitest";
import { groupedOrder, heatColor, legGroups, tickOf, type FundLegInfo } from "./fundLayout";

const leg = (name: string, symbols: string[]): FundLegInfo => ({
  playbook: name,
  name,
  symbols,
  slots: 6,
  exposure: "1",
  isolated: false,
});

const CORE = ["BTC_USDT", "ETH_USDT"];
const ALT = ["GALA_USDT", "SAND_USDT"];
const MACD = ["ZEC_USDT", "UNI_USDT"];
const LEGS = [
  leg("돌파 롱", CORE),
  leg("삼각 숏", [...CORE, ...ALT]),
  leg("MACD 숏", MACD),
  leg("MACD 롱", [...CORE, ...ALT, ...MACD]),
];
const ALL = [...MACD, ...ALT, ...CORE];

describe("legGroups", () => {
  it("다리 조합으로 가른다 — 핵심 · 삼각 알트 · MACD 알트 순", () => {
    const got = legGroups(LEGS, ALL);
    expect(got.map((g) => g.label)).toEqual([
      "돌파 롱 · 삼각 숏 · MACD 롱",
      "삼각 숏 · MACD 롱",
      "MACD 숏 · MACD 롱",
    ]);
  });

  it("🔴 한 종목은 한 묶음에만", () => {
    const order = groupedOrder(legGroups(LEGS, ALL));
    expect(order).toHaveLength(ALL.length);
    expect(new Set(order).size).toBe(ALL.length);
  });

  it("묶음 안은 거래대금 내림차순 · 값 없으면 뒤 · 같으면 이름", () => {
    const ticks = { ETH_USDT: { turnover: 900 }, BTC_USDT: { turnover: 100 }, GALA_USDT: { turnover: null } };
    const got = legGroups(LEGS, ALL, ticks);
    expect(got[0]?.symbols).toEqual(["ETH_USDT", "BTC_USDT"]);
    expect(got[1]?.symbols).toEqual(["SAND_USDT", "GALA_USDT"].sort()); // 둘 다 값 없음 → 이름 순
  });

  it("다리 없는 펀드는 한 묶음", () => {
    const got = legGroups([], ["B_USDT", "A_USDT"]);
    expect(got).toEqual([{ key: "all", label: "종목", symbols: ["A_USDT", "B_USDT"] }]);
  });

  it("순위 표 이름이 밑줄 없이 와도 맞춘다", () => {
    expect(tickOf({ BTCUSDT: { change: 1 } }, "BTC_USDT")?.change).toBe(1);
  });
});

describe("heatColor", () => {
  it("오르면 초록 · 내리면 빨강 · ±8% 에서 꽉 찬다", () => {
    expect(heatColor(8).bg).toBe("rgba(15,123,108,0.85)");
    expect(heatColor(-20).bg).toBe("rgba(180,66,58,0.85)");
    expect(heatColor(0.1).fg).toBe("inherit");
    expect(heatColor(8).fg).toBe("#fff");
  });

  it("값 없으면 무채색 — 0 으로 꾸미지 않는다", () => {
    expect(heatColor(null).bg).toBe("rgba(96,125,139,0.12)");
  });
});
