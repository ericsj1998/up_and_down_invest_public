import { describe, expect, it } from "vitest";
import { axisPrecision } from "./precision";

describe("axisPrecision", () => {
  it("큰 값은 지금처럼 소수 한 자리", () => {
    expect(axisPrecision(95000)).toBe(1);
  });
  it("10 ~ 1000 은 두 자리 · 1 ~ 10 은 세 자리", () => {
    expect(axisPrecision(150)).toBe(2);
    expect(axisPrecision(2.5)).toBe(3);
  });
  it("1 미만은 유효 숫자 네 자리", () => {
    expect(axisPrecision(0.5)).toBe(4);
    expect(axisPrecision(0.0123)).toBe(5);
    expect(axisPrecision(0.00001234)).toBe(8);
  });
  it("값이 없거나 0 이하면 기본 한 자리", () => {
    expect(axisPrecision(undefined)).toBe(1);
    expect(axisPrecision(0)).toBe(1);
  });
});
