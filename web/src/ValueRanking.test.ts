/**
 * 저평가 후보 카드 — 백분위 말·점수 색 (T244).
 */

import { describe, expect, it } from "vitest";
import { pctLabel, scoreTone } from "./ValueRanking";
import { DISCLAIMER_TEXT, DISCLAIMER_VERSION } from "./shell/disclaimer";

describe("pctLabel — 자기 5년 백분위를 말로", () => {
  it("배수류는 하위, 수익률류는 상위", () => {
    expect(pctLabel(12.4, false)).toBe("5년 하위 12%");
    expect(pctLabel(82, true)).toBe("5년 상위 18%");
  });
  it("없거나 방향 없는 지표는 빈 문자열", () => {
    expect(pctLabel(null, false)).toBe("");
    expect(pctLabel(50, null)).toBe("");
  });
});

describe("scoreTone", () => {
  it("60 이상 gain · 40 미만 loss · 사이·없음은 빈 값", () => {
    expect(scoreTone(75)).toBe("gain");
    expect(scoreTone(20)).toBe("loss");
    expect(scoreTone(50)).toBe("");
    expect(scoreTone(null)).toBe("");
  });
});

describe("면책 문구", () => {
  it("문장과 버전이 있다 — T247 동의가 같은 것을 쓴다", () => {
    expect(DISCLAIMER_TEXT).toContain("투자 권유가 아닙니다");
    expect(DISCLAIMER_VERSION).toMatch(/^\d{4}-\d{2}-\d{2}\.\d+$/);
  });
});
