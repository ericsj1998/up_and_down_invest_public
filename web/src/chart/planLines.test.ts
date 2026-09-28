/**
 * 원장 계획 → 차트 가로선 — 추세추종은 목표를 숨기고, 몫이 여럿이면 선 이름에 다리를 붙인다 (T320).
 */
import { describe, expect, it } from "vitest";
import { planLinesOf } from "./planLines";

const PLAN = { entry: "100", stop: "95", first: "110", target: "120" };

describe("planLinesOf", () => {
  it("고정 익절 매매법은 네 선 · 진입 대비 %", () => {
    const got = planLinesOf(PLAN);
    expect(got.map((l) => l.title)).toEqual(["손절 -5.00%", "진입", "1차 +10.00%", "목표 +20.00%"]);
  });

  it("추세추종은 목표 · 1차를 숨기고 손절을 청산이라 부른다", () => {
    const got = planLinesOf({ ...PLAN, full_ride: true });
    expect(got.map((l) => l.title)).toEqual(["청산(트레일)", "진입"]);
  });

  it("🔴 서버가 실제 청산선을 주면 손절은 '손절' · 청산선은 따로 (2026-09-29)", () => {
    const got = planLinesOf({
      ...PLAN,
      full_ride: true,
      exit_line: { label: "SMA20(1d) 마감 청산", price: "103" },
    });
    expect(got.map((l) => l.title)).toEqual(["손절 -5.00%", "진입", "SMA20(1d) 마감 청산 +3.00%"]);
    expect(got.map((l) => l.token)).toEqual(["--loss", "--entry-line", "--ma-line"]);
  });

  it("몫이 여럿이면 선 이름 앞에 다리 이름", () => {
    const got = planLinesOf({ ...PLAN, full_ride: true }, "일봉 채널 ");
    expect(got.map((l) => l.title)).toEqual(["일봉 채널 청산(트레일)", "일봉 채널 진입"]);
    expect(got.map((l) => l.price)).toEqual([95, 100]);
  });
});
