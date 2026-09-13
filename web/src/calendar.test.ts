import { describe, expect, it } from "vitest";
import { hourLabel, isoDay, monthCells } from "./Calendar";

describe("달력 격자 (T276)", () => {
  it("일요일부터 7의 배수로 채우고 이웃 달은 표시한다", () => {
    // 2026-09-01 은 화요일 → 앞에 일·월 두 칸이 8월.
    const cells = monthCells(2026, 8);
    expect(cells.length % 7).toBe(0);
    expect(cells[0]).toEqual({ iso: "2026-08-30", day: 30, inMonth: false });
    expect(cells[2]).toEqual({ iso: "2026-09-01", day: 1, inMonth: true });
    expect(cells.filter((c) => c.inMonth)).toHaveLength(30);
  });

  it("여섯째 줄이 통째로 다음 달이면 자른다", () => {
    // 2026-02 는 일요일 시작 · 28일 → 4줄로 끝난다.
    expect(monthCells(2026, 1)).toHaveLength(28);
    // 2026-08 은 토요일 시작 · 31일 → 6줄이 필요하다.
    expect(monthCells(2026, 7)).toHaveLength(42);
  });

  it("날짜 문자열은 시간대 변환 없이 숫자만 붙인다", () => {
    expect(isoDay(2026, 0, 5)).toBe("2026-01-05");
  });

  it("Finnhub 시각 표기는 아는 것만 옮기고 모르는 것은 그대로", () => {
    expect(hourLabel("amc")).toBe("장 마감 후");
    expect(hourLabel("bmo")).toBe("장 전");
    expect(hourLabel("")).toBe("");
    expect(hourLabel("xyz")).toBe("xyz");
  });
});
