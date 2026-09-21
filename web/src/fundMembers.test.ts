/**
 * 펀드 상세 조각(T261) — 봉 변환 · 등락 문장 · 색.
 */

import { describe, expect, it } from "vitest";
import { cardTone, changeText, changeTone, toOhlc, unrealizedPct } from "./fundMembers";

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

describe("cardTone — 테두리 색 (사용자 요구 2026-09-21)", () => {
  it("들고 있고 이익이면 초록 · 손해면 붉은", () => {
    expect(cardTone({ holding: true, unrealized: "8.13" })).toBe("gain");
    expect(cardTone({ holding: true, unrealized: "-2.5" })).toBe("loss");
  });

  it("🔴 포지션이 없으면 색이 없다 — 지금 '보고 있는' 손익이 없다", () => {
    expect(cardTone({ holding: false, unrealized: "8.13" })).toBe("");
    expect(cardTone({ unrealized: "8.13" })).toBe("");
  });

  it("🔴 거래소와 갈리면 노랑이다 — 손익보다 먼저이고, 붉은색(졌다)과 다른 뜻이다", () => {
    // 이익이 나 보여도 그 수를 못 믿는 상태다 — 초록으로 단언하지 않는다.
    expect(cardTone({ holding: true, unrealized: "8.13", reconciled: false })).toBe("warn");
    expect(cardTone({ holding: true, unrealized: "8.13", accounting_ok: false })).toBe("warn");
    // 손해로 보여도 마찬가지 — 붉은색은 "졌다" 이고 노랑은 "못 믿는다" 다.
    expect(cardTone({ holding: true, unrealized: "-8.13", reconciled: false })).toBe("warn");
  });

  it("🔴 갈림은 들고 있지 않아도 칠한다 — 닫힌 뒤 회계가 갈린 적이 있다 (2026-09-01)", () => {
    expect(cardTone({ holding: false, accounting_ok: false })).toBe("warn");
    expect(cardTone({ reconciled: false })).toBe("warn");
  });

  it("정확히 0 이면 색이 없다", () => {
    expect(cardTone({ holding: true, unrealized: "0" })).toBe("");
  });
});

describe("unrealizedPct — 미실현을 증거금 대비 % 로", () => {
  it("분모는 거래소가 잡은 증거금이다", () => {
    expect(unrealizedPct({ unrealized: "8.13", margin: "62.15" })).toBeCloseTo(13.08, 2);
    expect(unrealizedPct({ unrealized: "-3.1", margin: "62.15" })).toBeCloseTo(-4.99, 2);
  });

  it("증거금이 없거나 0 이면 못 잰다 — 몫으로 대신 나누지 않는다", () => {
    expect(unrealizedPct({ unrealized: "8.13" })).toBeNull();
    expect(unrealizedPct({ unrealized: "8.13", margin: "0" })).toBeNull();
    expect(unrealizedPct({ margin: "62.15" })).toBeNull();
  });
});
