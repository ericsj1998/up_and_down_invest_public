import { describe, expect, it } from "vitest";
import type { Ohlc } from "../chart/indicators";
import {
  aggregate,
  exportPayload,
  gradeSummary,
  movePosition,
  newPosition,
  nextCursor,
  positionZones,
  rewardRisk,
  startCursor,
  tag,
  toMark,
  visibleBars,
  visibleTrades,
  type GradingTrade,
} from "./grading";

const H = 3600;
const Q = 900;

function bar(time: number, o: number, h: number, l: number, c: number): Ohlc {
  return { time, open: o, high: h, low: l, close: c };
}

// 기준봉 3개(0h · 1h · 2h) + 하위 15m 봉 12개
const bars = [bar(0, 10, 12, 9, 11), bar(H, 11, 13, 10, 12), bar(2 * H, 12, 14, 11, 13)];
const subs: Ohlc[] = [];
for (let i = 0; i < 12; i += 1) subs.push(bar(i * Q, 10 + i * 0.25, 10.5 + i * 0.25, 9.5 + i * 0.25, 10.25 + i * 0.25));

const trade: GradingTrade = {
  id: "t1",
  symbol: "KRW-BTC",
  kind: "fade",
  side: 1,
  entry: 100,
  exit: 102,
  stop: 0,
  opened_ts: H + Q,
  closed_ts: 2 * H + 2 * Q,
  pnl: 1.9,
  gross_pct: 2,
  reason: "opp_band",
  bars_held: 1,
};

describe("aggregate", () => {
  it("하위 봉을 하나로 — 시가는 첫 봉 · 종가는 마지막 봉 · 고저는 극값", () => {
    const got = aggregate(subs.slice(0, 4), 0);
    expect(got).toEqual({ time: 0, open: 10, high: 10.5 + 0.75, low: 9.5, close: 10.25 + 0.75 });
    expect(aggregate([], 0)).toBeNull();
  });
});

describe("visibleBars", () => {
  it("커서 없음 → 전부", () => {
    expect(visibleBars(bars, subs, null, H, Q)).toHaveLength(3);
  });
  it("커서가 1h 의 두 번째 15m → 마감된 0h 봉 + 15m 둘을 합친 만드는 중 1h 봉", () => {
    const got = visibleBars(bars, subs, H + Q, H, Q);
    expect(got).toHaveLength(2);
    expect(got[0]).toEqual(bars[0]);
    expect(got[1]?.time).toBe(H);
    expect(got[1]?.open).toBe(subs[4]?.open);
    expect(got[1]?.close).toBe(subs[5]?.close);
  });
  it("커서가 시간 끝(마지막 15m) → 그 기준봉은 마감된 것으로", () => {
    const got = visibleBars(bars, subs, H + 3 * Q, H, Q);
    expect(got).toHaveLength(2);
    expect(got[1]).toEqual(bars[1]);
  });
  it("하위 봉이 없으면 마감된 기준봉만", () => {
    expect(visibleBars(bars, [], H + Q, H, Q)).toEqual([bars[0]]);
  });
});

describe("visibleTrades", () => {
  it("진입이 커서 뒤면 숨기고, 청산이 커서 뒤면 열린 채로", () => {
    // 진입 = 15m 봉의 마감 시각(H+Q). 커서 H-Q 는 마감이 H 라 아직 전, 커서 H 는 마감이 H+Q 라 그 순간 보인다.
    expect(visibleTrades([trade], H - Q, Q)).toHaveLength(0);
    expect(visibleTrades([trade], H, Q)).toHaveLength(1);
    const open = visibleTrades([trade], H + 2 * Q, Q);
    expect(open).toHaveLength(1);
    expect(open[0]?.reason).toBe("open");
    expect(open[0]?.closedTs).toBe(H + 3 * Q);
    const done = visibleTrades([trade], 2 * H + 2 * Q, Q);
    expect(done[0]?.reason).toBe("opp_band");
    expect(visibleTrades([trade], null, Q)[0]?.exit).toBe(102);
  });
  it("손절 0 인 매매는 진입가를 손절로 둔다(영역 폭 0)", () => {
    expect(toMark(trade).stop).toBe(100);
  });
});

describe("cursor", () => {
  it("시작은 warm 개 뒤 첫 하위 봉 · 다음은 다음 하위 봉 · 끝이면 null", () => {
    expect(startCursor(bars, subs, 1, H)).toBe(2 * H);
    expect(nextCursor(subs, bars, 2 * H, H)).toBe(2 * H + Q);
    expect(nextCursor(subs, bars, 11 * Q, H)).toBeNull();
    expect(nextCursor([], bars, 0, H)).toBe(H);
  });
});

describe("positions", () => {
  it("새 포지션 — 손절 1% · 목표 2% · 기간 24봉 · 손익비 2", () => {
    const p = newPosition(1, 100, H + Q, H, "p1");
    expect(p.stop).toBe(99);
    expect(p.target).toBe(102);
    expect(p.from).toBe(H);
    expect(p.to).toBe(H + 24 * H);
    expect(rewardRisk(p)).toBe(2);
    const zones = positionZones(p);
    expect(zones[0]).toMatchObject({ low: 99, high: 100, tone: "loss" });
    expect(zones[1]).toMatchObject({ low: 100, high: 102, tone: "gain" });
    const s = newPosition(-1, 100, 0, H, "p2");
    expect(s.stop).toBe(101);
    expect(s.target).toBe(98);
  });
});

describe("tag · move", () => {
  it("꼬리표와 옮기기(시간은 봉 단위로 맞춤)", () => {
    expect(tag("trend", 1)).toBe("TL");
    expect(tag("fade", -1)).toBe("RS");
    const p = newPosition(1, 100, H, H, "p");
    const moved = movePosition(p, 2, 1.4 * H, H);
    expect(moved.entry).toBe(102);
    expect(moved.stop).toBe(101);
    expect(moved.from).toBe(2 * H);
    expect(moved.to).toBe(p.to + H);
  });
});

describe("export · summary", () => {
  it("내보내기에 O/X 가 매매 옆에 붙고, 집계는 다리 종류별", () => {
    const marks = { grades: { t1: "O" as const }, positions: [], notes: "", saved_at: null };
    const out = exportPayload({ file: "r.json", config: "c", symbol: "KRW-BTC", market: "UPBIT", timeframe: "1h", bars, trades: [trade], marks });
    expect((out["trades"] as { grade: string }[])[0]?.grade).toBe("O");
    expect(gradeSummary([trade, { ...trade, id: "t2", kind: "trend" }], marks.grades)).toEqual([
      { kind: "fade", o: 1, x: 0, none: 0 },
      { kind: "trend", o: 0, x: 0, none: 1 },
    ]);
  });
});
