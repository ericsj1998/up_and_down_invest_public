/**
 * 매매 상자 — 무엇이 그려지고, 마우스가 어느 매매 위인가 (사용자 요구 2026-09-21).
 *
 * 🔴 여기서 못 박는 것: **손절 매매에 초록 상자가 없다.** 익절 기준이 없어서가 아니라
 * 유리하게 끝난 구간이 없어서다 (사용자 질문에 대한 답이 시험으로 남는다).
 */

import { describe, expect, it } from "vitest";
import { boxesOf, edgesOf, hitTest, pnlText, tagsOf } from "./tradeBoxes";
import type { TradeMark } from "./trades";

const STEP = 3600;

const mark = (over: Partial<TradeMark>): TradeMark => ({
  id: "t1",
  symbol: "BTC_USDT",
  side: 1,
  entry: 100,
  exit: 110,
  stop: 95,
  openedTs: 3600,
  closedTs: 7200,
  pnl: 10,
  reason: "목표 익절",
  ...over,
});

describe("boxesOf — 무슨 상자가 그려지나", () => {
  it("이긴 매매는 위험(붉은) + 이익(초록) 둘이다", () => {
    const got = boxesOf(mark({}), STEP);
    expect(got.map((b) => b.kind)).toEqual(["risk", "gain"]);
    // 붉은 = 진입↔손절 · 초록 = 진입↔청산
    expect([got[0]!.low, got[0]!.high]).toEqual([95, 100]);
    expect([got[1]!.low, got[1]!.high]).toEqual([100, 110]);
  });

  it("🔴 손절 매매에는 초록 상자가 없다 — 유리하게 끝난 구간이 없어서다", () => {
    const got = boxesOf(mark({ exit: 95, pnl: -20, reason: "손절" }), STEP);
    expect(got.map((b) => b.kind)).toEqual(["risk"]);
  });

  it("손절선을 지나 끝나면 그 몫이 짙은 붉은으로 따로 그려진다 (갭·강제청산)", () => {
    const got = boxesOf(mark({ exit: 90, pnl: -40, reason: "강제청산" }), STEP);
    expect(got.map((b) => b.kind)).toEqual(["risk", "beyond"]);
    expect([got[1]!.low, got[1]!.high]).toEqual([90, 95]);
  });

  it("숏은 방향이 뒤집힌다 — 내려가면 이익이다", () => {
    const short = mark({ side: -1, entry: 100, exit: 90, stop: 105, pnl: 10 });
    const got = boxesOf(short, STEP);
    expect(got.map((b) => b.kind)).toEqual(["risk", "gain"]);
    expect([got[1]!.low, got[1]!.high]).toEqual([90, 100]);
  });

  it("열린 매매는 open 표식을 달고 나온다 (점선 테두리로 그려진다)", () => {
    const got = boxesOf(mark({ open: true }), STEP);
    expect(got.every((b) => b.open)).toBe(true);
  });

  it("같은 봉에서 열리고 닫혀도 상자는 최소 한 봉 폭이다", () => {
    const got = boxesOf(mark({ openedTs: 3600, closedTs: 3600 }), STEP);
    expect(got[0]!.to - got[0]!.from).toBe(STEP);
  });
});

describe("edgesOf — 세로 경계 둘 (점 대신)", () => {
  it("시작은 진입 · 끝은 결말이다", () => {
    const got = edgesOf(mark({}), STEP);
    // 결말 옆에 손익이 붙는다 — "익절" 만으로는 얼마나 벌었는지 못 읽는다.
    expect(got.map((e) => e.label)).toEqual(["롱 진입", "익절 +10.00%"]);
    expect(got.map((e) => e.side)).toEqual(["open", "close"]);
  });

  it("보합 띠 안이면 익절도 손절도 아니라고 적는다", () => {
    expect(edgesOf(mark({ pnl: 0.3 }), STEP)[1]!.label).toBe("보합 +0.30%");
    expect(edgesOf(mark({ pnl: -0.3 }), STEP)[1]!.label).toBe("보합 -0.30%");
    expect(edgesOf(mark({ pnl: -0.3 }), STEP)[1]!.tone).toBe("flat");
  });

  it("손절은 붉은 색조다", () => {
    const got = edgesOf(mark({ pnl: -9 }), STEP);
    expect(got[1]!.label).toBe("손절 -9.00%");
    expect(got[1]!.tone).toBe("loss");
  });

  it("🔴 아직 안 닫혔으면 '보유중' 이다 — 지금 이익이라고 '익절' 이라 적지 않는다", () => {
    const got = edgesOf(mark({ open: true, pnl: 12 }), STEP);
    expect(got[1]!.label).toBe("보유중 +12.00%");
  });

  it("숏은 진입 딱지가 '숏 진입' 이다", () => {
    expect(edgesOf(mark({ side: -1 }), STEP)[0]!.label).toBe("숏 진입");
  });
});

describe("tagsOf — 호버하면 상자 선 좌측에 뜨는 가격", () => {
  it("진입 · 손절 · 청산 셋이다", () => {
    const got = tagsOf(mark({}));
    expect(got.map((t) => t.price)).toEqual([100, 95, 110]);
    // 🔴 이름이 겹치면 안 된다 — 계획 손절선과 실제 청산가가 둘 다 '손절' 이던 결함을 막는다.
    expect(got[0]!.text).toContain("진입");
    expect(got[1]!.text).toContain("손절선");
    expect(got[2]!.text).toContain("청산");
  });

  it("🔴 열린 매매의 셋째는 청산가가 아니라 '지금' 이다", () => {
    const got = tagsOf(mark({ open: true, exit: 107 }));
    expect(got[2]!.text.startsWith("지금")).toBe(true);
  });
});

describe("hitTest — 마우스가 어느 매매 위인가", () => {
  const boxes = [
    ...boxesOf(mark({ id: "a", openedTs: 3600, closedTs: 7200 }), STEP),
    ...boxesOf(
      mark({ id: "b", openedTs: 7200, closedTs: 10_800, entry: 200, exit: 210, stop: 190 }),
      STEP,
    ),
  ];

  it("상자 안이면 그 매매다", () => {
    expect(hitTest(boxes, 5000, 97)).toBe("a");
    expect(hitTest(boxes, 9000, 205)).toBe("b");
  });

  it("상자 밖이면 null 이다 — 아무거나 고르지 않는다", () => {
    expect(hitTest(boxes, 5000, 500)).toBeNull();
    expect(hitTest(boxes, 100_000, 97)).toBeNull();
  });

  it("경계는 포함이다 (딱 진입가·손절가 위에 올려도 잡힌다)", () => {
    expect(hitTest(boxes, 3600, 100)).toBe("a");
    expect(hitTest(boxes, 3600, 95)).toBe("a");
  });

  it("겹치면 나중 것 — 눈에 보이는 것(위에 그려진 것)과 맞아야 한다", () => {
    const overlap = [
      ...boxesOf(mark({ id: "old", openedTs: 3600, closedTs: 10_800 }), STEP),
      ...boxesOf(mark({ id: "new", openedTs: 3600, closedTs: 10_800 }), STEP),
    ];
    expect(hitTest(overlap, 5000, 97)).toBe("new");
  });
});

describe("pnlText — 손익은 금액 + % 병기 (사용자 요구)", () => {
  it("증거금이 있으면 금액과 % 를 같이 적는다", () => {
    // gain_pct 는 이미 **그 매매가 건 돈 대비** % 다 — 곱하면 금액이 나온다.
    expect(pnlText(mark({ pnl: -8, margin: 62.25 }))).toBe("-4.98 USDT · -8.00%");
    expect(pnlText(mark({ pnl: 12.5, margin: 62.25 }))).toBe("+7.78 USDT · +12.50%");
  });

  it("🔴 증거금이 없으면 % 만 적는다 — 다른 돈을 끌어다 곱하지 않는다", () => {
    expect(pnlText(mark({ pnl: -8, margin: null }))).toBe("-8.00%");
    expect(pnlText(mark({ pnl: -8 }))).toBe("-8.00%");
    expect(pnlText(mark({ pnl: -8, margin: 0 }))).toBe("-8.00%");
  });

  it("손익을 모르면 아무것도 안 적는다", () => {
    expect(pnlText(mark({ pnl: null, margin: 62.25 }))).toBe("");
  });
});

describe("🔴 '손절' 이 두 번 뜨던 결함 (사용자 신고 2026-09-21)", () => {
  it("계획 손절선과 실제 청산가는 이름이 다르다 — 그 차이가 미끄러짐이다", () => {
    const stopped = mark({ stop: 2.157, exit: 2.283, pnl: -9, reason: "손절" });
    const labels = tagsOf(stopped).map((t) => t.text.split(" ")[0]);
    expect(labels).toEqual(["진입", "손절선", "청산"]);
    // 같은 이름이 둘이면 안 된다
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("결말은 세로줄이 말한다 — 가격 딱지가 또 적지 않는다", () => {
    const texts = tagsOf(mark({ pnl: -9 })).map((t) => t.text);
    expect(texts.some((t) => t.startsWith("손절 "))).toBe(false);
  });
});

describe("결말 세로줄에 손익이 같이 붙는다", () => {
  it("손절도 -0.6% 와 -12% 는 다른 사건이다", () => {
    const small = edgesOf(mark({ pnl: -0.6, margin: 62.25 }), STEP)[1]!;
    const big = edgesOf(mark({ pnl: -12, margin: 62.25 }), STEP)[1]!;
    expect(small.label).toBe("손절 -0.37 USDT · -0.60%");
    expect(big.label).toBe("손절 -7.47 USDT · -12.00%");
  });

  it("손익을 모르면 결말만 적는다", () => {
    expect(edgesOf(mark({ pnl: null }), STEP)[1]!.label).toBe("청산");
  });
});
