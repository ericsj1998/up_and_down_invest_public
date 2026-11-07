import { describe, expect, it } from "vitest";
import type { FundEngineStatus } from "./api";
import { edgesOf } from "./chart/tradeBoxes";
import { engineMarks } from "./engineMarks";

const CANDLES = [
  { ts: "2026-11-05T00:00:00+00:00", close: 27500 },
  { ts: "2026-11-06T00:00:00+00:00", close: 27000 },
];

const BASE: FundEngineStatus = {
  symbol: "X",
  last_day: "2026-11-06",
  zone: true,
  units: [],
  buy: null,
  contracts: 0,
  realized: "0",
  fees: "0",
  findings: [],
  unrealized: "",
  leverage: "3",
};

describe("engineMarks", () => {
  it("낮에 찬 계약도 상자가 된다 — 진입 딱지에 종류 · 체결가 · 손절이 있다", () => {
    const got = engineMarks(
      {
        ...BASE,
        buy: {
          slot: 1,
          level: "27474",
          contracts: 29,
          filled: 29,
          fill: "27132.96",
          stop: "18993.07",
          filled_at: "2026-11-06T21:01:30+00:00",
          name: "계단",
        },
      },
      "X",
      "다리",
      CANDLES,
    );
    expect(got).toHaveLength(1);
    const mark = got[0]!;
    expect(mark.entry).toBeCloseTo(27132.96);
    expect(mark.stop).toBeCloseTo(18993.07);
    expect(mark.open).toBe(true);
    expect(mark.pnl).toBeCloseTo(((27000 - 27132.96) / 27132.96) * 300);
    expect(edgesOf(mark, 86400)[0]!.label).toBe("다리 계단 롱 진입");
  });

  it("유령 몫 · 시각 없는 줄은 안 그린다", () => {
    const got = engineMarks(
      {
        ...BASE,
        units: [
          {
            key: "c1",
            kind: "cyc",
            contracts: 0,
            fill: "26000",
            ghost: true,
            stop: null,
            opened: "2026-10-20T20:00:00+00:00",
          },
        ],
        closed: [
          {
            unit: "c0",
            kind: "cyc",
            fill: "26000",
            exit: "26520",
            pnl: "1",
            how: "grid_sell",
            at: "2026-10-30T20:00:00+00:00",
          },
        ],
      },
      "X",
      "다리",
      CANDLES,
    );
    expect(got).toEqual([]);
  });

  it("닫힌 몫은 청산 이름 · 손익으로 그린다", () => {
    const got = engineMarks(
      {
        ...BASE,
        closed: [
          {
            unit: "c0",
            kind: "cyc",
            fill: "26000",
            exit: "26520",
            pnl: "1",
            how: "grid_sell",
            opened: "2026-10-20T20:00:00+00:00",
            at: "2026-10-30T20:00:00+00:00",
          },
        ],
      },
      "X",
      "다리",
      CANDLES,
    );
    expect(got[0]!.reason).toBe("계단 매도");
    expect(got[0]!.pnl).toBeCloseTo(6);
    expect(got[0]!.open).toBeUndefined();
  });

  it("봉이 없으면 안 그린다 · 배율을 모르면 % 를 비운다", () => {
    expect(engineMarks(BASE, "X", "다리", [])).toEqual([]);
    const got = engineMarks(
      {
        ...BASE,
        leverage: undefined,
        units: [
          {
            key: "c1",
            kind: "cyc",
            name: "계단",
            contracts: 3,
            fill: "26000",
            ghost: false,
            stop: "18200",
            opened: "2026-10-20T20:00:00+00:00",
          },
        ],
      },
      "X",
      "다리",
      CANDLES,
    );
    expect(got[0]!.pnl).toBeNull();
  });
});
