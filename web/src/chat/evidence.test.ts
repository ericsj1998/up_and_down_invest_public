/**
 * 근거 창의 순수 조각 (T257).
 */

import { describe, expect, it } from "vitest";
import { columnsOf, labelOf, parseEvidence } from "./evidence";

describe("evidence", () => {
  it("아는 키는 한국어 · 모르는 키는 그대로", () => {
    expect(labelOf("symbol")).toBe("종목");
    expect(labelOf("weird_key")).toBe("weird_key");
  });
  it("객체 배열의 열은 키 합집합 · 8열까지", () => {
    expect(columnsOf([{ a: 1 }, { b: 2, a: 3 }, "x"])).toEqual(["a", "b"]);
    const wide = [Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`k${i}`, i]))];
    expect(columnsOf(wide)).toHaveLength(8);
  });
  it("깨진 JSON 은 null (원문 폴드만 보인다)", () => {
    expect(parseEvidence('{"a": 1}')).toEqual({ a: 1 });
    expect(parseEvidence('{"a": 1')).toBeNull();
    expect(parseEvidence(undefined)).toBeNull();
  });
});
