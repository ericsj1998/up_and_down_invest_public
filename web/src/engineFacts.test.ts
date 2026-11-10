/**
 * 엔진 다리 요약 산수 — 상세 화면 · 펀드 카드 · 첫 화면이 같은 값을 쓴다.
 * 모르는 값은 지어내지 않는다(빈 글 · null).
 */
import { describe, expect, it } from "vitest";
import type { FundEngineStatus } from "./api";
import {
  engineFacts,
  engineLines,
  exitText,
  noteOf,
  noteText,
  untilText,
} from "./engineFacts";

const fmt = (value: number): string => value.toFixed(1);

function unit(
  key: string,
  over: Partial<FundEngineStatus["units"][number]> = {},
): FundEngineStatus["units"][number] {
  return {
    key,
    kind: "k",
    name: "몫",
    contracts: 10,
    fill: "100",
    ghost: false,
    stop: "70",
    opened: "2026-11-01T21:00:00+00:00",
    ...over,
  };
}

function status(over: Partial<FundEngineStatus> = {}): FundEngineStatus {
  return {
    symbol: "IDX_USDT",
    last_day: "2026-11-05",
    zone: true,
    units: [],
    buy: null,
    contracts: 0,
    realized: "0",
    fees: "0",
    findings: [],
    unrealized: "",
    ...over,
  };
}

describe("engineFacts", () => {
  it("평균 진입은 돈이 들어간 계약만 계약 수로 가중한다", () => {
    const facts = engineFacts(
      status({
        units: [
          unit("a", { contracts: 10, fill: "100" }),
          unit("b", { contracts: 30, fill: "96" }),
          unit("g", { contracts: 0, fill: "50", ghost: true }),
        ],
        mark: "98.94",
        margin: "50",
        unrealized: "-2.5",
        step_units: 2,
        max_units: 5,
      }),
    );
    expect(facts.avgEntry).toBeCloseTo(97, 6);
    expect(facts.vsEntryPct).toBeCloseTo(2, 6);
    expect(facts.vsMarginPct).toBeCloseTo(-5, 6);
    expect([facts.stepUnits, facts.maxUnits]).toEqual([2, 5]);
  });

  it("낮에 찬 계단도 평균 진입에 넣고, 다 찬 계단은 다음 계단이 아니다", () => {
    const facts = engineFacts(
      status({
        units: [unit("a", { contracts: 10, fill: "100" })],
        buy: { slot: 2, level: "95", contracts: 10, filled: 10, fill: "94" },
      }),
      97,
    );
    expect(facts.avgEntry).toBeCloseTo(97, 6);
    expect(facts.vsEntryPct).toBeCloseTo(0, 6);
    expect(facts.nextStep).toBeNull();
  });

  it("표시가 · 증거금을 모르면 비율을 지어내지 않는다", () => {
    const facts = engineFacts(status({ units: [unit("a")] }));
    expect(facts.vsEntryPct).toBeNull();
    expect(facts.vsMarginPct).toBeNull();
    expect(facts.nextCloseMs).toBeNull();
  });

  it("걸려 있는 계단은 다음 계단으로 나온다", () => {
    const made = status({
      buy: { slot: 3, level: "92.5", contracts: 10 },
      step_units: 2,
      max_units: 5,
      next_close: "2026-11-10T21:00:00+00:00",
    });
    expect(engineFacts(made).nextStep).toBe(92.5);
    expect(noteOf(made)).toEqual({
      step_units: 2,
      max_units: 5,
      next_step: "92.5",
      next_close: "2026-11-10T21:00:00+00:00",
    });
  });
});

describe("untilText · noteText", () => {
  const close = Date.parse("2026-11-10T21:00:00+00:00");

  it("남은 시간을 큰 단위부터 적는다", () => {
    expect(untilText(null, close)).toBe("—");
    expect(untilText(close, close - 45 * 60_000)).toBe("45분");
    expect(untilText(close, close - (3 * 60 + 12) * 60_000)).toBe("3시간 12분");
    expect(untilText(close, close - (2 * 24 + 5) * 3_600_000)).toBe(
      "2일 5시간",
    );
  });

  it("마감이 지났으면 처리 대기라고 적는다", () => {
    expect(untilText(close, close + 1)).toBe("마감 지남 · 처리 대기");
  });

  it("모르는 칸은 빼고 적는다", () => {
    const now = close - 3_600_000;
    expect(
      noteText(
        {
          step_units: 2,
          max_units: 5,
          next_step: "92.5",
          next_close: "2026-11-10T21:00:00+00:00",
        },
        now,
        fmt,
      ),
    ).toBe("계단 몫 2/5 · 다음 계단 92.5 · 다음 마감까지 1시간 0분");
    expect(noteText({ step_units: 0, max_units: 5 }, now, fmt)).toBe(
      "계단 몫 0/5",
    );
    expect(noteText({ next_step: null, next_close: null }, now, fmt)).toBe("");
  });
});

describe("exitText", () => {
  it("값이 있으면 방식과 함께 적는다", () => {
    expect(exitText("104.2", "limit", fmt)).toBe("매도 지정가 104.2");
    expect(exitText("99", "close_below", fmt)).toBe("종가가 이 아래면 청산 99.0");
    expect(exitText("101", "close_above", fmt)).toBe(
      "종가가 이 위면 청산 101.0",
    );
  });

  it("종가 규칙인데 값을 모르면 다음 마감 뒤라고 적는다", () => {
    expect(exitText(null, "close_below", fmt)).toBe(
      "종가가 이 아래면 청산 — 값은 다음 마감 뒤 표시",
    );
  });

  it("정한 자리가 없거나 걸린 지정가가 없으면 빈 글", () => {
    expect(exitText(null, "limit", fmt)).toBe("");
    expect(exitText("100", "", fmt)).toBe("");
    expect(exitText("100", undefined, fmt)).toBe("");
  });
});

describe("engineLines", () => {
  it("같은 자리의 선은 하나로 합치고 가상 몫 · 값 없는 몫은 안 긋는다", () => {
    const lines = engineLines(
      status({
        units: [
          unit("a", { exit: "104", exit_how: "limit", name: "가" }),
          unit("b", { exit: "99", exit_how: "close_below", name: "나" }),
          unit("c", { exit: "99", exit_how: "close_below", name: "나" }),
          unit("d", { exit: null, exit_how: "close_above" }),
          unit("g", {
            exit: "120",
            exit_how: "limit",
            ghost: true,
            contracts: 0,
          }),
        ],
        buy: { slot: 2, level: "95", contracts: 10 },
      }),
    );
    expect(lines.map((line) => [line.price, line.tone])).toEqual([
      [104, "gain"],
      [99, "loss"],
      [95, "entry"],
    ]);
    expect(lines[0]?.label).toBe("가 매도선");
    expect(lines[2]?.label).toBe("다음 계단(지정가)");
  });

  it("다 찬 계단은 다음 계단선을 안 긋는다", () => {
    expect(
      engineLines(
        status({ buy: { slot: 2, level: "95", contracts: 10, filled: 10 } }),
      ),
    ).toEqual([]);
  });
});
