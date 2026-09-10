/**
 * 펀드 상세 조각(T261) — 봉 변환 · 등락 문장 · 색.
 */

import { describe, expect, it } from "vitest";
import { changeText, changeTone, toOhlc } from "./fundMembers";

describe("toOhlc", () => {
  it("문자열 가격을 숫자 봉으로 · 깨진 줄은 버린다", () => {
    const got = toOhlc([
      { time: 1, open: "1.5", high: "2", low: "1", close: "1.8", volume: "10" },
      { time: 2, open: "x", high: "2", low: "1", close: "1.8", volume: "10" },
    ]);
    expect(got).toEqual([{ time: 1, open: 1.5, high: 2, low: 1, close: 1.8 }]);
  });
});

describe("changeText · changeTone", () => {
  it("부호와 두 자리 · 없으면 —", () => {
    expect(changeText("1.2345")).toBe("+1.23%");
    expect(changeText("-0.5")).toBe("-0.50%");
    expect(changeText(null)).toBe("—");
    expect(changeTone("1")).toBe("gain");
    expect(changeTone("-1")).toBe("loss");
    expect(changeTone("0")).toBe("");
    expect(changeTone(undefined)).toBe("");
  });
});
