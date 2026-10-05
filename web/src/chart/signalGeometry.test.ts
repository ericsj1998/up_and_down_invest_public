/**
 * 신호 때 탐지기가 본 선 → 차트 선분 — 판정 축보다 긴 봉엔 안 그리고, 차트 범위로 자르고, 다시 잰 값이
 * 다르면 점선 (T378).
 */
import { describe, expect, it } from "vitest";
import type { SignalGeometry } from "../chartTypes";
import { signalLineSpecs, signalNote } from "./signalGeometry";

function secondsOf(frame: string): number {
  return { "1h": 3600, "4h": 14400, "1d": 86400 }[frame] ?? 0;
}

const DAY = 86400;
const T0 = Date.parse("2026-09-01T00:00:00Z") / 1000;
const iso = (s: number): string => new Date(s * 1000).toISOString();

const CHANNEL: SignalGeometry = {
  trade_id: "t1",
  playbook: "private_strategy@0.1.0",
  kind: "channel",
  frame: "1d",
  signal_ts: iso(T0 + 20 * DAY),
  lines: [
    { role: "upper", t1: iso(T0), p1: 110, t2: iso(T0 + 20 * DAY), p2: 130 },
    { role: "lower", t1: iso(T0), p1: 90, t2: iso(T0 + 20 * DAY), p2: 110 },
  ],
  points: [],
  note: "일봉 채널 20일 · 번갈아 닿기 6 · 안쪽 90%",
  matched: true,
  open: true,
};

describe("signalLineSpecs", () => {
  it("일봉 채널을 4h 차트에 두 선분으로", () => {
    const got = signalLineSpecs([CHANNEL], "4h", T0, T0 + 30 * DAY, secondsOf);
    expect(got.map((l) => l.role)).toEqual(["upper", "lower"]);
    expect(got[0]?.points).toEqual([
      { time: T0, value: 110 },
      { time: T0 + 20 * DAY, value: 130 },
    ]);
    expect(got[0]?.width).toBe(2);
    expect(got[0]?.dashed).toBe(false);
  });

  it("🔴 판정 축보다 긴 봉의 차트엔 안 그린다(4h 삼각 → 일봉 차트)", () => {
    const tri: SignalGeometry = { ...CHANNEL, kind: "triangle", frame: "4h" };
    expect(signalLineSpecs([tri], "1d", T0, T0 + 30 * DAY, secondsOf)).toEqual([]);
    expect(signalLineSpecs([tri], "1h", T0, T0 + 30 * DAY, secondsOf)).toHaveLength(2);
  });

  it("차트 첫 봉 앞은 잘라 보간한다", () => {
    const got = signalLineSpecs([CHANNEL], "1d", T0 + 10 * DAY, T0 + 30 * DAY, secondsOf);
    expect(got[0]?.points[0]).toEqual({ time: T0 + 10 * DAY, value: 120 });
  });

  it("차트 범위 밖이면 없다", () => {
    expect(signalLineSpecs([CHANNEL], "1d", T0 + 40 * DAY, T0 + 60 * DAY, secondsOf)).toEqual([]);
  });

  it("다시 잰 값이 다르면 점선 · 닫힌 매매는 얇게", () => {
    const got = signalLineSpecs(
      [{ ...CHANNEL, matched: false, open: false }],
      "1d",
      T0,
      T0 + 30 * DAY,
      secondsOf,
    );
    expect(got[0]?.dashed).toBe(true);
    expect(got[0]?.width).toBe(1);
  });
});

describe("signalNote", () => {
  it("신호봉 시각 · 상태 · 설명 · 경고", () => {
    expect(signalNote(CHANNEL)).toBe(
      "일봉 채널 신호 2026-09-21 00:00 UTC 봉 · 보유 중 · 일봉 채널 20일 · 번갈아 닿기 6 · 안쪽 90%",
    );
    expect(signalNote({ ...CHANNEL, matched: false })).toContain("⚠️");
  });
});
