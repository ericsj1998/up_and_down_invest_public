import { describe, expect, it } from "vitest";
import type { LitePosition } from "./api";
import { heldText, legNames, liteFacts } from "./liteFacts";

const NEAR: LitePosition = {
  symbol: "NEAR_USDT",
  run: "abc",
  side: "long",
  contracts: "12",
  margin: "11.3",
  notional: "44.4",
  entry: "3.7538",
  mark: "3.7",
  unrealized: "-0.6",
  pct: "-5",
  shares: [
    { name: "다리 A", stop: "3.3895", opened_at: "2026-11-06T15:55:00Z" },
  ],
  exchange_stops: 1,
};

describe("liteFacts", () => {
  it("롱은 배율 · 손절까지 거리 · 손절 시 손익을 낸다", () => {
    const got = liteFacts(NEAR);
    expect(got.leverage).toBeCloseTo(44.4 / 11.3);
    expect(got.stopDistancePct).toBeCloseTo(-8.39, 2);
    // 12 계약 x (3.3895 − 3.7538) = −4.37
    expect(got.stopPnl).toBeCloseTo(-4.37, 2);
    expect(got.stopWarning).toBeNull();
  });

  it("숏은 손절이 위에 있어도 거리 · 손익이 음수다", () => {
    const got = liteFacts({
      ...NEAR,
      side: "short",
      entry: "100",
      mark: "100",
      notional: "100",
      shares: [{ name: "다리 B 숏", stop: "110", opened_at: null }],
    });
    expect(got.stopDistancePct).toBeCloseTo(-10);
    expect(got.stopPnl).toBeCloseTo(-10);
  });

  it("몫이 여럿이면 손절을 한 값으로 적지 않는다", () => {
    const got = liteFacts({
      ...NEAR,
      shares: [
        { name: "돌파 롱", stop: "3", opened_at: null },
        { name: "다리 C", stop: "3.2", opened_at: null },
      ],
    });
    expect(got.stop).toBeNull();
    expect(got.stopPnl).toBeNull();
  });

  it("손절을 모르면 0 으로 꾸미지 않는다", () => {
    const got = liteFacts({
      ...NEAR,
      shares: [{ name: "엔진 다리", stop: "", opened_at: null }],
    });
    expect(got.stop).toBeNull();
    expect(got.stopDistancePct).toBeNull();
  });

  it("거래소 손절이 없거나 쌓였으면 경고 · 못 읽었으면 말하지 않는다", () => {
    expect(liteFacts({ ...NEAR, exchange_stops: 0 }).stopWarning).toContain(
      "없음",
    );
    expect(liteFacts({ ...NEAR, exchange_stops: 100 }).stopWarning).toContain(
      "100건",
    );
    expect(liteFacts({ ...NEAR, exchange_stops: null }).stopWarning).toBeNull();
  });
});

describe("heldText · legNames", () => {
  it("보유 시간을 적고 모르면 빈 값", () => {
    expect(heldText(NEAR, Date.parse("2026-11-06T18:55:00Z"))).toBe("3.0시간");
    expect(heldText({ ...NEAR, shares: [] }, 0)).toBe("");
  });

  it("같은 다리 이름은 한 번만", () => {
    expect(
      legNames({
        ...NEAR,
        shares: [
          { name: "엔진 다리", stop: "", opened_at: null },
          { name: "엔진 다리", stop: "", opened_at: null },
        ],
      }),
    ).toBe("엔진 다리");
  });
});
