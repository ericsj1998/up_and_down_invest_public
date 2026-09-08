/**
 * 종목 순위 정렬 — **누를 때마다 방향이 바뀐다** (사용자 신고 2026-08-20).
 *
 * 🔴 사용자 신고: *"클릭해도 내림차순 정렬이 안되네?"*
 *
 * 방향이 내림차순으로 **고정**돼 있었다. 이미 그 열로 정렬된 상태에서 다시 눌러도
 * 아무 일이 안 일어났고, 눌렀는데 화면이 안 변하면 사람은 **정렬이 안 되는 것**으로
 * 읽는다.
 */

import { describe, expect, it } from "vitest";
import { ordered } from "./Ranking";
import type { Rank } from "./api";

/** 실측 값 (2026-08-20 23:48). */
const ROWS = [
  { symbol: "ETH_USDT", recent_pct: 0.79, turnover: 8798e6 },
  { symbol: "XRP_USDT", recent_pct: 2.59, turnover: 200e6 },
  { symbol: "BTC_USDT", recent_pct: 0.88, turnover: 7154e6 },
  { symbol: "SNDK_USDT", recent_pct: 3.42, turnover: 2271e6 },
] as Rank[];

const names = (rows: Rank[]) => rows.map((row) => row.symbol.split("_")[0]);

describe("ordered — 열 하나로 줄을 세운다", () => {
  it("내림차순 — 큰 값이 위", () => {
    expect(names(ordered(ROWS, "recent_pct", true))).toEqual([
      "SNDK",
      "XRP",
      "BTC",
      "ETH",
    ]);
  });

  it("🔴 오름차순 — 다시 누르면 뒤집힌다", () => {
    // 이것이 없어서 "클릭해도 정렬이 안 된다" 로 보였다.
    expect(names(ordered(ROWS, "recent_pct", false))).toEqual([
      "ETH",
      "BTC",
      "XRP",
      "SNDK",
    ]);
  });

  it("⭐ 거래대금과 최근 변동성은 순서가 반대다 — 열을 나눈 이유", () => {
    // 거래대금 1·2위(ETH·BTC)가 최근 1시간으로는 꼴찌 둘이다.
    expect(names(ordered(ROWS, "turnover", true)).slice(0, 2)).toEqual([
      "ETH",
      "BTC",
    ]);
    expect(names(ordered(ROWS, "recent_pct", true)).slice(-2)).toEqual([
      "BTC",
      "ETH",
    ]);
  });

  it("⛔ 못 읽은 값은 **방향과 무관하게** 아래로 간다", () => {
    // 0 으로 치면 오름차순에서 맨 위로 올라와 제일 좋은 것처럼 보인다.
    const withGap = [
      { symbol: "A_USDT", recent_pct: 1 },
      { symbol: "B_USDT", recent_pct: null },
      { symbol: "C_USDT" },
      { symbol: "D_USDT", recent_pct: 5 },
    ] as Rank[];
    expect(names(ordered(withGap, "recent_pct", true)).slice(0, 2)).toEqual(["D", "A"]);
    expect(names(ordered(withGap, "recent_pct", false)).slice(0, 2)).toEqual(["A", "D"]);
    // 빈 값 둘은 어느 방향이든 뒤에 남는다.
    expect(names(ordered(withGap, "recent_pct", true)).slice(2).sort()).toEqual(["B", "C"]);
    expect(names(ordered(withGap, "recent_pct", false)).slice(2).sort()).toEqual(["B", "C"]);
  });

  it("원본을 안 건드린다", () => {
    const before = names(ROWS);
    ordered(ROWS, "recent_pct", true);
    expect(names(ROWS)).toEqual(before);
  });
});
