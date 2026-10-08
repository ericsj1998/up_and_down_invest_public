/**
 * 펀드 종목 묶음 · 순서 · 히트맵 색 (사용자 2026-09-27).
 *
 * 🔴 못 박는 것: 한 종목은 **한 묶음에만** 나온다(다리가 겹쳐도) · 묶음 안은 거래대금 내림차순 · 값 없는 종목은 뒤.
 */

import { describe, expect, it } from "vitest";
import type { FundLeg } from "./api";
import {
  brakeChip,
  groupedOrder,
  groupPnl,
  heatColor,
  hourGuard,
  legGroups,
  positionRank,
  previewShort,
  previewText,
  tickOf,
  type FundLegInfo,
} from "./fundLayout";

const leg = (name: string, symbols: string[]): FundLegInfo => ({
  playbook: name,
  name,
  symbols,
  slots: 6,
  exposure: "1",
  isolated: false,
});

const CORE = ["BTC_USDT", "ETH_USDT"];
const ALT = ["GALA_USDT", "SAND_USDT"];
const MACD = ["ZEC_USDT", "UNI_USDT"];
const LEGS = [
  leg("돌파 롱", CORE),
  leg("삼각 숏", [...CORE, ...ALT]),
  leg("MACD 숏", MACD),
  leg("MACD 롱", [...CORE, ...ALT, ...MACD]),
];
const ALL = [...MACD, ...ALT, ...CORE];

describe("legGroups", () => {
  it("다리 조합으로 가른다 — 핵심 · 삼각 알트 · MACD 알트 순", () => {
    const got = legGroups(LEGS, ALL);
    expect(got.map((g) => g.label)).toEqual([
      "돌파 롱 · 삼각 숏 · MACD 롱",
      "삼각 숏 · MACD 롱",
      "MACD 숏 · MACD 롱",
    ]);
  });

  it("🔴 한 종목은 한 묶음에만", () => {
    const order = groupedOrder(legGroups(LEGS, ALL));
    expect(order).toHaveLength(ALL.length);
    expect(new Set(order).size).toBe(ALL.length);
  });

  it("묶음 안은 거래대금 내림차순 · 값 없으면 뒤 · 같으면 이름", () => {
    const ticks = { ETH_USDT: { turnover: 900 }, BTC_USDT: { turnover: 100 }, GALA_USDT: { turnover: null } };
    const got = legGroups(LEGS, ALL, ticks);
    expect(got[0]?.symbols).toEqual(["ETH_USDT", "BTC_USDT"]);
    expect(got[1]?.symbols).toEqual(["SAND_USDT", "GALA_USDT"].sort()); // 둘 다 값 없음 → 이름 순
  });

  it("다리 없는 펀드는 한 묶음", () => {
    const got = legGroups([], ["B_USDT", "A_USDT"]);
    expect(got).toEqual([{ key: "all", label: "종목", symbols: ["A_USDT", "B_USDT"] }]);
  });

  it("순위 표 이름이 밑줄 없이 와도 맞춘다", () => {
    expect(tickOf({ BTCUSDT: { change: 1 } }, "BTC_USDT")?.change).toBe(1);
  });
});

describe("heatColor", () => {
  it("오르면 초록 · 내리면 빨강 · ±8% 에서 꽉 찬다", () => {
    expect(heatColor(8).bg).toBe("rgba(15,123,108,0.85)");
    expect(heatColor(-20).bg).toBe("rgba(180,66,58,0.85)");
    expect(heatColor(0.1).fg).toBe("inherit");
    expect(heatColor(8).fg).toBe("#fff");
  });

  it("값 없으면 무채색 — 0 으로 꾸미지 않는다", () => {
    expect(heatColor(null).bg).toBe("rgba(96,125,139,0.12)");
  });
});

describe("previewText · previewShort", () => {
  const signal = {
    kind: "signal" as const,
    side: "롱",
    leg: "private_strategy@1.1.0",
    frame: "1h",
    entry: "1.2345",
    stop: "1.2",
  };

  it("예비 신호는 약속이 아니라고 말한다 · 다리 이름을 쓴다", () => {
    const text = previewText(signal, "돌파 롱");
    expect(text).toContain("마감 전 예비 신호 · 롱 · 1h 봉 · 돌파 롱");
    expect(text).toContain("약속은 아니다");
    expect(previewText(signal)).toContain("private_strategy");
    // 2026-09-30 사용자 "예비 신호에도 다리 이름" — 다리 id 가 붙는다.
    expect(previewShort(signal)).toBe("예비 신호 · 롱 · private_strategy");
  });

  it("걸어 둔 진입 주문은 가격 · 손절을 싣는다", () => {
    const waiting = { ...signal, kind: "waiting" as const, side: "숏", frame: "" };
    expect(previewText(waiting)).toContain("진입 주문 대기 · 숏 @ 1.2345 · 손절 1.2");
    expect(previewShort(waiting)).toBe("진입 주문 · 숏 · private_strategy");
  });
});


describe("positionRank · 묶음 안 순서", () => {
  const v = (x: Partial<FundLeg>): FundLeg => ({ handle: "h", ...x });
  const preview = { kind: "signal" as const, side: "롱", leg: "a@1", frame: "1h", entry: "1", stop: "0.9" };

  it("🔴 진입 가능 → 이득 보유 → 이득 실현 → 손해 보유 → 손해 실현 → 현금 (사용자 2026-09-27)", () => {
    expect(positionRank(v({ preview }))).toBe(0);
    expect(positionRank(v({ holding: true, unrealized: "0.06" }))).toBe(1);
    expect(positionRank(v({ realized: "3" }))).toBe(2);
    expect(positionRank(v({ holding: true, unrealized: "-1" }))).toBe(3);
    expect(positionRank(v({ realized: "-2" }))).toBe(4);
    expect(positionRank(v({ realized: "0" }))).toBe(5);
    // 보유 중인데 미실현을 못 읽었으면 이득 쪽 — 모르는 것을 손실로 끌어내리지 않는다
    expect(positionRank(v({ holding: true }))).toBe(1);
    // 거래소와 갈린 판은 실측 실현으로
    expect(positionRank(v({ realized: "5", accounting_ok: false, verified_realized: "-4" }))).toBe(4);
  });

  it("묶음 안: 순위가 거래대금보다 먼저", () => {
    const legs: FundLegInfo[] = [
      { playbook: "a", name: "A", symbols: ["BTC_USDT", "SOL_USDT", "XRP_USDT"], slots: 6, exposure: "1", isolated: false },
    ];
    const ticks = { BTC_USDT: { turnover: 900 }, SOL_USDT: { turnover: 100 }, XRP_USDT: { turnover: 500 } };
    const rank = (s: string) => (s === "SOL_USDT" ? 1 : 5);
    const [g] = legGroups(legs, ["BTC_USDT", "SOL_USDT", "XRP_USDT"], ticks, rank);
    expect(g?.symbols).toEqual(["SOL_USDT", "BTC_USDT", "XRP_USDT"]);
  });
});

describe("groupPnl", () => {
  it("실현 + 보유 미실현 · 몫 합 대비 %", () => {
    const rows: Record<string, FundLeg> = {
      A: { handle: "a", equity: "100", realized: "2", holding: true, unrealized: "3" },
      B: { handle: "b", equity: "100", realized: "-1", unrealized: "9" }, // 보유 아님 — 미실현 안 셈
    };
    const got = groupPnl(["A", "B"], rows);
    expect(got.amount).toBeCloseTo(4);
    expect(got.pct).toBeCloseTo(2);
    expect(groupPnl([], rows).pct).toBeNull();
  });
});

describe("hourGuard — 매시 정각 ± 30초", () => {
  const at = (h: number, m: number, s: number) => Date.UTC(2026, 8, 27, h, m, s);
  it("정각 30초 전부터 정각 30초 뒤까지", () => {
    expect(hourGuard(at(9, 59, 29))).toBeNull();
    expect(hourGuard(at(9, 59, 30))).toEqual({ toHour: 30_000, until: at(10, 0, 30) });
    expect(hourGuard(at(10, 0, 0))).toEqual({ toHour: 0, until: at(10, 0, 30) });
    expect(hourGuard(at(10, 0, 29))).toEqual({ toHour: 0, until: at(10, 0, 30) });
    expect(hourGuard(at(10, 0, 30))).toBeNull();
    expect(hourGuard(at(10, 30, 0))).toBeNull();
  });
});

describe("brakeChip — 지금 브레이크가 걸려 있나 (2026-10-09)", () => {
  const leg = (name: string, at: string | null, engaged = false, isolated = false) => ({
    playbook: name,
    name,
    at,
    scale: at === null ? null : "0.25",
    engaged,
    isolated,
  });
  it("옛 서버(칸 없음)면 칩을 안 그린다 — 거짓 '안 걸림' 금지", () => {
    expect(brakeChip(undefined)).toBeNull();
    expect(brakeChip(null)).toBeNull();
  });
  it("안 걸림 — 매매법 낙폭과 문턱을 적는다", () => {
    const got = brakeChip({
      source: "own_pnl",
      drawdown_pct: "0.00",
      engaged: false,
      scale: "1",
      recover_pct: null,
      legs: [leg("돌파 롱", "0.10"), leg("삼각 숏", null), leg("MACD 롱", "0.10", false, true)],
    });
    expect(got?.tone).toBe("ok");
    expect(got?.text).toBe("브레이크 안 걸림 · 매매법 낙폭 0%");
    expect(got?.title).toContain("문턱 10%");
    expect(got?.title).toContain("x0.25");
    expect(got?.title).toContain("수동 매매");
  });
  it("걸림 — 배수 · 낙폭 · 걸린 다리 · 회복 필요 수익률", () => {
    const got = brakeChip({
      source: "own_pnl",
      drawdown_pct: "24.36",
      engaged: true,
      scale: "0.25",
      recover_pct: "32.21",
      legs: [leg("돌파 롱", "0.10", true), leg("MACD 롱", "0.10", false, true)],
    });
    expect(got?.tone).toBe("on");
    expect(got?.text).toBe("🔴 브레이크 x0.25 · 매매법 낙폭 24.4%");
    expect(got?.title).toContain("걸린 다리 돌파 롱");
    expect(got?.title).not.toContain("MACD 롱 ·");
    expect(got?.title).toContain("+32.2%");
  });
  it("브레이크 선언이 없는 펀드", () => {
    const got = brakeChip({
      source: "fund",
      drawdown_pct: "41.6",
      engaged: false,
      scale: "1",
      recover_pct: "71.2",
      legs: [leg("삼각 숏", null)],
    });
    expect(got?.tone).toBe("none");
    expect(got?.text).toBe("브레이크 없음");
  });
});
