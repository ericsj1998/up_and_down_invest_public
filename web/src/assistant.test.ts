/**
 * 온보딩 위저드 산수 (T247) — 납입 누계(수익 가정 없음) · 단계 · 막는 이유.
 */

import { describe, expect, it } from "vitest";
import { blockers, contributed, nextStep, prevStep } from "./assistant";

describe("contributed — 납입 누계", () => {
  it("시작 + 주기 x 횟수 · 수익률을 곱하지 않는다", () => {
    expect(contributed(1_000_000, 100_000, "month", 2)).toBe(1_000_000 + 100_000 * 24);
    expect(contributed(1_000_000, 0, "week", 5)).toBe(1_000_000);
    expect(contributed(-5, 10, "year", 1)).toBe(10);
  });
});

describe("steps", () => {
  it("동의 → 자본 → 성향 → 설정 → 검토 → 끝", () => {
    expect(nextStep("consent")).toBe("capital");
    expect(nextStep("review")).toBe("done");
    expect(nextStep("done")).toBe("done");
    expect(prevStep("consent")).toBe("consent");
    expect(prevStep("setup")).toBe("profile");
  });
});

describe("blockers", () => {
  it("동의 없이는 못 간다 · 자본 0 · 갈래/성향 · 매매법", () => {
    expect(blockers("consent", {}, false)).toEqual(["동의가 필요하다"]);
    expect(blockers("consent", {}, true)).toEqual([]);
    expect(blockers("capital", { capital: 0 }, true).length).toBe(1);
    expect(blockers("profile", {}, true).length).toBe(2);
    expect(blockers("setup", { playbook: "x" }, true)).toEqual([]);
  });
});
