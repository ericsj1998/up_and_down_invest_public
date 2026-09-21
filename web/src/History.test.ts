/**
 * 체결 이력이 **누구의 무엇인가** (사용자 요구 2026-08-20).
 *
 * 🔴 사용자 지적: *"지금 체결 이력이 너무 보기 힘들어."*
 */

import { describe, expect, it } from "vitest";
import { costBite, shownPct, sideOf, unrealizedOf, verdictOf } from "./History";
import { tradeOf } from "./ui";

describe("sideOf — 롱인가 숏인가", () => {
  it("진입은 부호 그대로다", () => {
    expect(sideOf(4, false)).toBe("롱");
    expect(sideOf(-4, false)).toBe("숏");
  });

  it("🔴 줄이는 주문은 반대로 읽는다", () => {
    // 롱을 닫는 것은 **매도**이고, 매수로 닫히는 것은 **숏**이다.
    // 예전 화면은 매수/매도를 그대로 보여 줘서 손절 발동이 전부 "매수" 로 보였다.
    expect(sideOf(85, true)).toBe("숏");
    expect(sideOf(-85, true)).toBe("롱");
  });

  it("실측 한 줄 — ETH 손절 발동", () => {
    // 08-20 20:50 · 매수 85 · ao- · 숏 포지션이 손절로 닫힌 것이다.
    expect(sideOf(85, true)).toBe("숏");
  });
});

describe("tradeOf — 계획을 잇는 열쇠", () => {
  it("옛 형식은 매매 id 가 12자다", () => {
    expect(tradeOf("t-72bb3b4690f3-cl-0")).toBe("72bb3b4690f3");
  });

  it("🔴 새 형식은 8자로 잘린다 — 주문 이름이 30자 제한이라", () => {
    expect(tradeOf("t-82e456-72bb3b46-cl-1")).toBe("72bb3b46");
  });

  it("⭐ 조건부 발동은 **이름 그대로가 열쇠**다 (2026-08-20)", () => {
    // 🔴 `ao-{id}` 에는 우리 매매 id 가 없다. 그래서 서버가 **조건부 주문 id** 로
    //    매매를 되찾아 이 이름으로도 계획을 걸어 준다 — 화면은 추정하지 않고 묻기만 한다.
    //
    //    예전에는 여기서 "" 를 내서, 손절이 나면 화면이 "RUN 미상 · 배율 — · 수익률 —"
    //    로 떴다. 원장은 그 매매를 알고 있었는데 화면만 못 이었다.
    expect(tradeOf("ao-2090406159629418496")).toBe("ao-2090406159629418496");
  });

  it("⛔ 우리 이름이 아닌 나머지는 지어내지 않는다", () => {
    // 남의 계획을 이 주문에 붙이면 화면이 없는 사실을 말한다.
    expect(tradeOf("")).toBe("");
    expect(tradeOf("esc-1787228823")).toBe("");
  });

  it("손으로 닫은 것과 정리 주문도 읽는다", () => {
    expect(tradeOf("t-cclose-1787195761")).toBe("cclose");
    expect(tradeOf("t-tidy7fb81612")).toBe("");
  });
});


describe("shownPct — 옆 칸과 같은 것을 잰다", () => {
  it("🔴 서버가 준 값을 읽기만 한다 — 화면이 다시 계산하지 않는다", () => {
    // 사용자 신고: 화면이 원장 진입가로 재고 옆 칸은 거래소 실현이라 부호가 갈렸다.
    expect(shownPct({ gain_pct: "-0.5959", move_pct: "0.0702" })).toEqual({
      net: -0.5959,
      move: 0.0702,
    });
  });

  it("⛔ 없으면 지어내지 않는다 — 0 은 본전으로 읽힌다", () => {
    expect(shownPct({})).toBeNull();
    expect(shownPct({ gain_pct: "" })).toBeNull();
    expect(shownPct({ gain_pct: "n/a" })).toBeNull();
  });

  it("가격 변동만 못 읽어도 순수익률은 살린다", () => {
    expect(shownPct({ gain_pct: "-0.5959" })).toEqual({ net: -0.5959, move: null });
  });
});

describe("costBite — 수수료가 얼마나 먹었나", () => {
  it("🔴 실측 한 줄 — 가격으로는 이겼는데 수수료로 졌다", () => {
    // ETH 숏 85계약 · 진입 2316.1753 → 청산 2314.55 · 20배
    const said = costBite({ net: -0.5959, move: 0.0702 }, "20");
    expect(said).toContain("+0.070%");
    expect(said).toContain("+1.40%");
    // ⭐ 20배에서 왕복 수수료가 증거금의 2%p 다 — 이 숫자가 모순의 답이다.
    expect(said).toContain("2.00%p");
  });

  it("배율을 못 읽으면 곱하기를 지어내지 않는다", () => {
    expect(costBite({ net: -0.6, move: 0.07 }, "")).not.toContain("배율");
    expect(costBite({ net: -0.6, move: null }, "20")).not.toContain("배율");
  });
});

describe("unrealizedOf — 안 닫힌 진입의 미실현 손익률", () => {
  const pos = (u: string, m: string) =>
    ({ symbol: "BTC_USDT", unrealised_pnl: u, margin: m }) as Record<
      string,
      string
    > & { symbol: string };

  it("미실현/증거금 을 % 로 준다", () => {
    const r = unrealizedOf(pos("12", "100"));
    expect(r?.pct).toBeCloseTo(12);
    expect(r?.usdt).toBe(12);
  });

  it("손실도 부호 그대로", () => {
    expect(unrealizedOf(pos("-8.16", "100"))?.pct).toBeCloseTo(-8.16);
  });

  it("🔴 포지션이 없으면 null (지어내지 않는다)", () => {
    expect(unrealizedOf(undefined)).toBeNull();
  });

  it("🔴 증거금이 0/빈값이면 null — 0 으로 나누지 않는다", () => {
    expect(unrealizedOf(pos("12", "0"))).toBeNull();
    expect(unrealizedOf(pos("12", ""))).toBeNull();
  });
});

describe("verdictOf — 결말은 매매의 결과다 (주문의 수명이 아니다)", () => {
  // 🔴 전에는 이 칸이 finish_as(체결·취소)를 보여 줬다. 손절로 닫힌 주문도 "체결" 이라
  //    결과가 화면에 없었다 (사용자 요구 2026-09-21에 갈랐다).
  const base = { net: null, pnl: undefined, outcome: undefined, reduceOnly: false, tradeOpen: false };

  it("수익률로 익절·손절을 가른다", () => {
    expect(verdictOf({ ...base, net: 8.95 }).label).toBe("익절");
    expect(verdictOf({ ...base, net: 8.95 }).tone).toBe("gain");
    expect(verdictOf({ ...base, net: -3.2 }).label).toBe("손절");
    expect(verdictOf({ ...base, net: -3.2 }).tone).toBe("loss");
  });

  it("보합 띠(±0.5%) 안이면 어느 쪽도 아니다", () => {
    expect(verdictOf({ ...base, net: 0.5 }).label).toBe("보합");
    expect(verdictOf({ ...base, net: -0.5 }).label).toBe("보합");
    expect(verdictOf({ ...base, net: 0 }).label).toBe("보합");
    expect(verdictOf({ ...base, net: 0.5 }).tone).toBe("");
    // 띠 **밖**은 가른다 — 경계가 흐리면 표가 거짓말한다
    expect(verdictOf({ ...base, net: 0.51 }).label).toBe("익절");
    expect(verdictOf({ ...base, net: -0.51 }).label).toBe("손절");
  });

  it("원장이 취소면 손익을 지어내지 않는다", () => {
    expect(verdictOf({ ...base, outcome: "취소", net: -9 }).label).toBe("취소");
  });

  it("수익률이 없고 USDT 만 있으면 부호만 말한다 (보합은 % 가 있어야 잰다)", () => {
    expect(verdictOf({ ...base, pnl: "-0.6596" }).label).toBe("손절");
    expect(verdictOf({ ...base, pnl: "0.4708" }).label).toBe("익절");
    expect(verdictOf({ ...base, pnl: "-0.6596" }).why).toContain("보합은 안 가린다");
  });

  it("아직 안 닫힌 진입 줄은 보유중이다 — 미실현으로 익절이라 적지 않는다", () => {
    expect(verdictOf({ ...base, tradeOpen: true }).label).toBe("보유중");
    expect(verdictOf({ ...base, tradeOpen: true }).tone).toBe("");
  });

  it("근거가 없으면 빈칸이다", () => {
    expect(verdictOf(base).label).toBe("—");
  });
});
