import { describe, expect, it } from "vitest";
import { bollinger, BollingerBands } from "./bollinger";
import { ema, MovingAverage, sma } from "./movingAverage";
import { build, buildEnabled, DEFAULT_SPECS, describe as describeSpec, specId } from "./registry";
import type { Ohlc } from "./types";

const bars = (closes: number[]): Ohlc[] =>
  closes.map((c, i) => ({ time: 1_700_000_000 + i * 3600, open: c, high: c + 1, low: c - 1, close: c }));

describe("sma", () => {
  it("길이 미만은 null, 그 뒤는 창 평균", () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });
  it("길이 0 이하면 전부 null", () => {
    expect(sma([1, 2, 3], 0)).toEqual([null, null, null]);
  });
});

describe("ema", () => {
  it("첫 값은 SMA 씨앗, 이후 α=2/(n+1)", () => {
    const got = ema([1, 2, 3, 4, 10], 3);
    expect(got.slice(0, 2)).toEqual([null, null]);
    expect(got[2]).toBe(2);
    expect(got[3]).toBeCloseTo(2 + (4 - 2) * 0.5, 10);
    expect(got[4]).toBeCloseTo(3 + (10 - 3) * 0.5, 10);
  });
  it("표본이 길이보다 짧으면 전부 null", () => {
    expect(ema([1, 2], 3)).toEqual([null, null]);
  });
});

describe("bollinger", () => {
  it("중심 = SMA · 상하 = ±k·모집단 표준편차", () => {
    // 창 [2,4,4,4,5,5,7,9] → 평균 5 · 모집단 sd 2
    const v = [2, 4, 4, 4, 5, 5, 7, 9];
    const b = bollinger(v, 8, 2);
    expect(b.mid[7]).toBe(5);
    expect(b.upper[7]).toBeCloseTo(9, 10);
    expect(b.lower[7]).toBeCloseTo(1, 10);
    expect(b.upper[6]).toBeNull();
  });
  it("4/4 는 짧고 넓다 — 상단이 20/2 보다 멀리 벌어지는 것은 k 때문", () => {
    const v = [10, 11, 12, 13];
    const b = bollinger(v, 4, 4);
    // 평균 11.5 · sd = sqrt(1.25)
    expect(b.mid[3]).toBe(11.5);
    expect(b.upper[3]).toBeCloseTo(11.5 + 4 * Math.sqrt(1.25), 10);
  });
});

describe("Indicator 객체", () => {
  it("MovingAverage 는 선 하나, BollingerBands 는 선 셋(상·중·하) — 점은 null 을 뺀다", () => {
    const data = bars([1, 2, 3, 4, 5]);
    const ma = new MovingAverage({ id: "sma3", kind: "sma", length: 3, color: "#000" }).compute(data);
    expect(ma).toHaveLength(1);
    expect(ma[0]?.points.map((p) => p.value)).toEqual([2, 3, 4]);
    expect(ma[0]?.points[0]?.time).toBe(1_700_000_000 + 2 * 3600);
    const bb = new BollingerBands({ id: "bb3-2", length: 3, k: 2, color: "#000" }).compute(data);
    expect(bb.map((s) => s.key)).toEqual(["bb3-2:upper", "bb3-2:mid", "bb3-2:lower"]);
    expect(bb[1]?.dashed).toBe(true);
  });
});

describe("registry", () => {
  it("기본 설정은 이평 셋 + BB 20/2 · 4/4 이고 켜진 것만 지표가 된다", () => {
    expect(DEFAULT_SPECS.map((s) => s.id)).toEqual(["sma20", "sma50", "sma200", "bb20-2", "bb4-4"]);
    const built = buildEnabled(DEFAULT_SPECS);
    expect(built.map((i) => i.label)).toEqual(["SMA 20", "BB 20/2"]);
  });
  it("모르는 타입·잘못된 길이는 null — 저장된 옛 설정이 화면을 죽이지 않는다", () => {
    expect(build({ id: "x", type: "rsi" as never, length: 14, enabled: true, color: "#000" })).toBeNull();
    expect(build({ id: "x", type: "sma", length: 0, enabled: true, color: "#000" })).toBeNull();
  });
  it("id 는 종류·길이·k 로 정해진다", () => {
    expect(specId("bb", 20, 2)).toBe("bb20-2");
    expect(specId("ema", 9)).toBe("ema9");
    expect(describeSpec({ id: "bb4-4", type: "bb", length: 4, k: 4, enabled: true, color: "" })).toBe("BB 4/4");
  });
});
