/**
 * 분석 도형 읽기 — **못 읽은 것을 지어내지 않는다**.
 *
 * 🔴 이 화면의 목적은 *"빠르게 분석"* 이다. 그런데 없는 띠를 그리거나 뒤집힌 띠를
 * 칠하면 빠르게 **틀린 결론**에 간다 — 그래서 버리는 규칙을 시험으로 못 박는다.
 */

import { describe, expect, it } from "vitest";
import {
  GROUPS,
  pickSegments,
  readSegments,
  readTrend,
  trendText,
} from "./analysis";

describe("readTrend — 지금 추세", () => {
  it("판단과 마디 수를 읽는다", () => {
    expect(readTrend([{ trend: "UPTREND", legs: 4 }])).toEqual({ trend: "UPTREND", legs: 4 });
  });

  it("없으면 null — 칩을 안 그린다", () => {
    expect(readTrend([])).toBeNull();
    expect(readTrend([{ legs: 3 }])).toBeNull();
  });
});

describe("trendText — 사람 말", () => {
  it("아는 것은 번역한다", () => {
    expect(trendText("UPTREND")).toBe("상승");
    expect(trendText("DOWNTREND")).toBe("하락");
    expect(trendText("RANGE")).toBe("횡보");
  });

  it("🔴 모르는 값은 **그대로** 보여 준다", () => {
    // ⛔ 지어내면 서버가 새 상태를 더했을 때 화면이 조용히 옛 어휘로 말한다.
    expect(trendText("NEW_STATE")).toBe("NEW_STATE");
  });
});

describe("GROUPS — 사람이 쓰는 말로 묶는다", () => {
  it("사용자가 고른 셋이 있다", () => {
    expect(GROUPS.map((item) => item.id)).toEqual(["trend", "range_box", "levels"]);
  });

  it("묶음마다 플래그가 하나 이상", () => {
    for (const group of GROUPS) expect(group.flags.length).toBeGreaterThan(0);
  });

  it("⚠️ 추세는 판단·이탈·**선**이 따로 온다 — 한 묶음에 여러 플래그", () => {
    const trend = GROUPS.find((item) => item.id === "trend");
    expect(trend?.flags).toContain("trend.structure");
    expect(trend?.flags).toContain("trend.break");
    // 선만 켜면 "그래서 지금 뭔가" 가 없고, 판단만 켜면 근거가 안 보인다.
    expect(trend?.flags).toContain("structure.swing_trendline");
  });

  it("⛔ **구 추세선은 안 켠다** — 400봉에 40개+ 나오는 과탐지가 확인됐다 (축 J)", () => {
    for (const group of GROUPS) {
      expect(group.flags).not.toContain("structure.trendline");
    }
  });
});

describe("readSegments — 추세선은 **봉 번호**로 온다", () => {
  const raw = {
    x1: 10,
    anchor_x2: 40,
    x2: 60,
    y1: "100",
    y_anchor2: "120",
    y2: "133",
    kind: "low",
    touches: 4,
  };

  it("접점 구간과 연장분을 갈라서 읽는다", () => {
    const got = readSegments([raw]);
    expect(got).toHaveLength(1);
    // ⚠️ 앞은 시장이 닿은 곳, 뒤는 우리가 이어 그은 것 — 화면이 굵기로 가른다.
    expect(got[0]?.anchorX).toBe(40);
    expect(got[0]?.x2).toBe(60);
  });

  it("숫자가 아니면 버린다 — 0 으로 채우면 화면 왼쪽 끝에 선이 생긴다", () => {
    expect(readSegments([{ ...raw, y1: "없음" }])).toEqual([]);
    expect(readSegments([{ ...raw, x1: null }])).toEqual([]);
  });

  it("🔴 뒤로 가는 선은 버린다 — 그릴 수 없다", () => {
    expect(readSegments([{ ...raw, x1: 60, x2: 10 }])).toEqual([]);
  });

  it("연장분이 없어도 된다 — 접점에서 끝나는 선", () => {
    const got = readSegments([{ ...raw, x2: 40 }]);
    expect(got).toHaveLength(1);
    expect(got[0]?.x2).toBe(got[0]?.anchorX);
  });
});

describe("pickSegments — 과탐지를 화면에서 다시 겪지 않는다", () => {
  const seg = (touches: number) => ({
    x1: 0,
    anchorX: 10,
    x2: 20,
    y1: 1,
    yAnchor: 2,
    y2: 3,
    kind: "low",
    touches,
  });

  it("🔴 400봉에 선 40개가 나온 적이 있다 (축 J) — 넷만 남긴다", () => {
    const many = Array.from({ length: 40 }, (_, i) => seg(i));
    expect(pickSegments(many)).toHaveLength(4);
  });

  it("닿은 횟수가 많은 것이 남는다", () => {
    expect(pickSegments([seg(2), seg(9), seg(5)], 1).at(0)?.touches).toBe(9);
  });

  it("원본을 안 건드린다", () => {
    const rows = [seg(1), seg(9)];
    pickSegments(rows);
    expect(rows.at(0)?.touches).toBe(1);
  });
});
