/**
 * 펀드 상세 조각(T261) — 봉 변환 · 등락 문장 · 색.
 */

import { describe, expect, it } from "vitest";
import type { FundMember } from "./api";
import { cardTone, changeText, changeTone, memberMarks, toOhlc, unrealizedPct } from "./fundMembers";

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

describe("memberMarks — 몫마다 상자 (T320)", () => {
  const base: FundMember = {
    handle: "h1",
    symbol: "BTC_USDT",
    last: "110",
    holding: true,
    unrealized: "-10",
    margin: "56",
    bars: [{ time: 1_760_000_000, open: "1", high: "1", low: "1", close: "110", volume: "1" }],
    position: { side: "롱", entry: "100", stop: "95", target: "999", opened_at: "2026-09-01T00:00:00Z" },
  };

  it("몫이 하나면 예전처럼 상자 하나 · 종목 증거금 대비 %", () => {
    const got = memberMarks(base);
    expect(got).toHaveLength(1);
    expect(got[0]?.id).toBe("h1");
    expect(got[0]?.pnl).toBeCloseTo((-10 / 56) * 100, 9);
  });

  it("몫이 여럿이면 몫마다 상자 — 자기 진입 · 손절 · 몫 증거금 대비 % · 다리 이름", () => {
    const got = memberMarks({
      ...base,
      shares: [
        { leg: "a", name: "돌파 롱", side: "롱", entry: "100", stop: "95", contracts: 2,
          opened_at: "2026-09-01T00:00:00Z", unrealized: "20", margin: "20" },
        { leg: "b", name: "일봉 채널", side: "롱", entry: "120", stop: "90", contracts: 3,
          opened_at: "2026-09-02T00:00:00Z", unrealized: "-30", margin: "36" },
      ],
    });
    expect(got.map((t) => t.id)).toEqual(["h1:a", "h1:b"]);
    expect(got.map((t) => t.entry)).toEqual([100, 120]);
    expect(got[0]?.pnl).toBeCloseTo(100, 9);
    expect(got[1]?.leg).toBe("일봉 채널");
  });

  it("몫 미실현을 못 나눴으면 % 는 비운다 — 지어내지 않는다", () => {
    const got = memberMarks({
      ...base,
      shares: [
        { leg: "a", name: "A", side: "롱", entry: "100", stop: "95", contracts: 2,
          opened_at: "2026-09-01T00:00:00Z", unrealized: null, margin: "20" },
        { leg: "b", name: "B", side: "롱", entry: "120", stop: "90", contracts: 3,
          opened_at: "2026-09-02T00:00:00Z", unrealized: null, margin: "36" },
      ],
    });
    expect(got.map((t) => t.pnl)).toEqual([null, null]);
  });
});
