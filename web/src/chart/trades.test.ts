import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS } from "./indicators/settings";
import { favorable, snap, toTradeMark, tradeLevels, tradeMarkers, tradeZones, type TradeMark } from "./trades";

const long: TradeMark = {
  id: "a",
  symbol: "BTCUSDT",
  side: 1,
  entry: 100,
  exit: 120,
  stop: 90,
  openedTs: 14_400 * 10 + 900,
  closedTs: 14_400 * 13,
  pnl: 20,
  reason: "soft",
};
const liqShort: TradeMark = {
  id: "b",
  symbol: "ADAUSDT",
  side: -1,
  entry: 0.0447,
  exit: 0.0519,
  stop: 0.0476,
  openedTs: 14_400 * 20,
  closedTs: 14_400 * 20,
  pnl: -4767,
  reason: "liq",
};
const marks = DEFAULT_SETTINGS.marks;

describe("snap", () => {
  it("봉 시작으로 내린다", () => {
    expect(snap(14_400 * 10 + 900, 14_400)).toBe(14_400 * 10);
  });
});

describe("tradeMarkers", () => {
  it("롱은 아래 ↑ 진입 · 위 청산, 숏은 반대. 청산(liq)은 네모", () => {
    const got = tradeMarkers([long, liqShort], 14_400, marks, "b");
    const entry = got.find((m) => m.text.startsWith("롱 진입"));
    expect(entry).toMatchObject({ position: "belowBar", shape: "arrowUp", time: 14_400 * 10, size: 1 });
    const liq = got.find((m) => m.text.startsWith("강제청산"));
    expect(liq).toMatchObject({ position: "belowBar", shape: "square", tone: "loss", size: 2 });
    expect(got.map((m) => m.time)).toEqual([...got.map((m) => m.time)].sort((a, b) => a - b));
  });
  it("표기를 끄면 안 나온다 · 매매가 많으면 글자는 선택한 것만", () => {
    expect(tradeMarkers([long], 14_400, { ...marks, entry: false, exit: false }, null)).toEqual([]);
    const many = Array.from({ length: 50 }, (_, i) => ({ ...long, id: `t${i}`, openedTs: i * 14_400, closedTs: i * 14_400 + 14_400 }));
    const got = tradeMarkers(many, 14_400, marks, "t3");
    expect(got.filter((m) => m.text !== "")).toHaveLength(2);
  });
});

describe("tradeLevels / tradeZones", () => {
  it("선 셋: 손절(점선) · 진입 · 청산", () => {
    const got = tradeLevels(long, marks);
    expect(got.map((l) => [l.tone, l.dashed])).toEqual([
      ["loss", true],
      ["entry", false],
      ["gain", false],
    ]);
    expect(tradeLevels(long, { ...marks, lines: false })).toEqual([]);
  });
  it("유리한 롱: 붉은(진입↔손절) + 초록(진입↔청산)", () => {
    expect(favorable(long)).toBe(true);
    const z = tradeZones(long, 14_400, marks);
    expect(z).toHaveLength(2);
    expect(z[0]).toMatchObject({ low: 90, high: 100, tone: "loss", from: 14_400 * 10, to: 14_400 * 14 });
    expect(z[1]).toMatchObject({ low: 100, high: 120, tone: "gain" });
  });
  it("손절선을 지나 끝난 숏(강제청산): 붉은 + 짙은 붉은(손절↔청산) · 같은 봉이면 한 봉 폭", () => {
    const z = tradeZones(liqShort, 14_400, marks);
    expect(z).toHaveLength(2);
    expect(z[1]).toMatchObject({ low: 0.0476, high: 0.0519, tone: "loss", alpha: 0.32 });
    expect((z[0]?.to ?? 0) - (z[0]?.from ?? 0)).toBe(14_400);
  });
});

describe("toTradeMark", () => {
  it("서버 행을 옮긴다 — side 부호로 롱/숏, reason 없으면 liq", () => {
    const got = toTradeMark(
      { symbol: "X", side: -1, entry: 1, exit: 2, stop: 1.5, opened_ts: 5, closed_ts: 6, pnl: -1 },
      3,
    );
    expect(got).toMatchObject({ id: "X:5:3", side: -1, reason: "liq" });
  });
});
