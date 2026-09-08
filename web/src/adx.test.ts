/**
 * 추세강도 문턱 읽기 — **부등호 하나가 판정을 반박한다**.
 *
 * 🔴 진입은 `>=`, 약화 청산은 `<=`, 본대 인계는 `>=` 다. 셋을 한 방향으로 쓰면 화면이
 * *"충족"* 이라고 적는데 판정은 안 들어가는 상태가 생기고, 그 어긋남은 조용하다 —
 * 사람은 화면을 믿으므로 알고리즘이 고장 났다고 읽는다.
 */

import { describe, expect, it } from "vitest";
import {
  type Gate,
  gateText,
  gateTone,
  readGates,
  readSlope,
  readVol,
  statusOf,
} from "./adx";

const LONG_IN: Gate = {
  key: "long_adx",
  label: "롱 진입",
  value: 35,
  kind: "in",
  owner: "sample_ma_cross",
};
const LONG_OUT: Gate = {
  key: "adx_exit_long",
  label: "롱 청산",
  value: 31,
  kind: "out",
  owner: "sample_ma_cross",
};
const HANDOVER: Gate = {
  key: "adx_exit_above_long",
  label: "본대 인계",
  value: 35,
  kind: "up",
  owner: "sample_carry",
};

describe("readGates — 서버 도형을 문턱으로", () => {
  it("숫자와 종류가 온전한 것만 읽는다", () => {
    const got = readGates([
      {
        key: "long_adx",
        label: "롱 진입",
        value: "35",
        kind: "in",
        owner: "본대",
      },
      // ⚠️ 값이 숫자가 아니면 **버린다** — 0 으로 채우면 "문턱이 0" 으로 그려진다.
      {
        key: "broken",
        label: "깨짐",
        value: "없음",
        kind: "in",
        owner: "본대",
      },
      // ⚠️ 모르는 종류도 버린다 — 부등호를 못 정하면 그릴 수 없다.
      {
        key: "weird",
        label: "이상",
        value: "10",
        kind: "sideways",
        owner: "본대",
      },
    ]);
    expect(got).toHaveLength(1);
    expect(got.at(0)?.value).toBe(35);
  });

  it("진입 문이 먼저, 그 안에서는 높은 문턱부터", () => {
    const got = readGates([
      {
        key: "adx_exit_short",
        label: "숏 청산",
        value: "16",
        kind: "out",
        owner: "본대",
      },
      {
        key: "short_adx",
        label: "숏 진입",
        value: "20",
        kind: "in",
        owner: "본대",
      },
      {
        key: "long_adx",
        label: "롱 진입",
        value: "35",
        kind: "in",
        owner: "본대",
      },
    ]);
    expect(got.map((item) => item.key)).toEqual([
      "long_adx",
      "short_adx",
      "adx_exit_short",
    ]);
  });

  it("빈 목록도 빈 목록이다 — 지어내지 않는다", () => {
    expect(readGates([])).toEqual([]);
  });
});

describe("statusOf — 부등호가 판정과 같아야 한다", () => {
  it("진입 문은 이상(>=)에서 열린다 — 경계 포함", () => {
    expect(statusOf(35, LONG_IN).met).toBe(true);
    expect(statusOf(34.9, LONG_IN).met).toBe(false);
  });

  it("약화 청산은 이하(<=)에서 걸린다 — 경계 포함", () => {
    // 🔴 세션은 `power <= adx_gate` 로 나간다. `<` 로 쓰면 딱 31 일 때 화면만 안 걸린다.
    expect(statusOf(31, LONG_OUT).met).toBe(true);
    expect(statusOf(31.1, LONG_OUT).met).toBe(false);
  });

  it("본대 인계는 방향이 반대다 — 강해지면 나간다", () => {
    expect(statusOf(35, HANDOVER).met).toBe(true);
    expect(statusOf(30, HANDOVER).met).toBe(false);
  });

  it("남은 거리는 절대값이다", () => {
    expect(statusOf(34.9, LONG_IN).gap).toBeCloseTo(0.1);
    expect(statusOf(40, LONG_IN).gap).toBeCloseTo(5);
  });
});

describe("gateText — 사람이 읽는 한 줄", () => {
  it("못 미치면 얼마나 모자란지 적는다", () => {
    expect(gateText(34.9, LONG_IN)).toBe("롱 진입 ≥35 · 0.1 모자람");
  });

  it("충족이면 그렇게만 적는다 — 예언하지 않는다", () => {
    // ⛔ "곧 들어간다" 를 만들지 않는다 (원칙 P4: 분석 ≠ 결정).
    expect(gateText(36, LONG_IN)).toBe("롱 진입 ≥35 · 충족");
  });

  it("청산 문은 부등호가 뒤집힌다", () => {
    expect(gateText(30, LONG_OUT)).toBe("롱 청산 ≤31 · 충족");
  });
});

describe("gateTone — 같은 '충족' 이라도 좋고 나쁨이 갈린다", () => {
  it("진입이 열린 것은 좋은 소식", () => {
    expect(gateTone(LONG_IN, true)).toBe("good");
  });

  it("청산·인계가 걸린 것은 나쁜 소식", () => {
    expect(gateTone(LONG_OUT, true)).toBe("bad");
    expect(gateTone(HANDOVER, true)).toBe("bad");
  });

  it("안 걸린 문은 회색 — 아직 아무 일도 없다", () => {
    expect(gateTone(LONG_IN, false)).toBe("idle");
    expect(gateTone(LONG_OUT, false)).toBe("idle");
  });
});

describe("readSlope — SMA 선이 내려가고 있나", () => {
  it("봉 수와 변화율을 읽는다", () => {
    const got = readSlope([
      { bars: "6", now: "100", before: "101", pct: "-0.99" },
    ]);
    expect(got).toEqual({ bars: 6, pct: -0.99 });
  });

  it("도형이 없으면 null — 칩을 안 그린다", () => {
    expect(readSlope([])).toBeNull();
  });

  it("숫자가 아니면 null — 0 으로 채우면 '기울기 없음' 이 된다", () => {
    // ⚠️ 0 은 "평평하다" 는 뜻이고, 그러면 숏 조건이 아슬아슬해 보인다.
    //    진짜 뜻은 "못 쟀다" 다 (규칙 #8).
    expect(readSlope([{ bars: "6", pct: "없음" }])).toBeNull();
  });
});

describe("readVol — 얼마나 크게 걸까", () => {
  it("변동성·되돌아보기·승수를 읽는다", () => {
    const got = readVol([
      { vol: "1.41", target: "1.0", lookback: "120", mult: "0.71" },
    ]);
    expect(got).toEqual({ vol: 1.41, lookback: 120, mult: 0.71 });
  });

  it("🔴 되돌아보기를 같이 들고 온다", () => {
    // 짧은 ATR 사이징과 **다른 것**이 이 규칙의 요점이다 (T81 §8-D: 짧게 재면
    // 강추세에서 수량이 줄어 OOS 0/5). 승수만 보면 어느 쪽인지 구별할 수 없다.
    expect(
      readVol([{ vol: "1.0", lookback: "120", mult: "1.0" }])?.lookback,
    ).toBe(120);
  });

  it("변동성을 못 쟀으면 null — 1 배로 적지 않는다", () => {
    // ⛔ 탐지기는 그때 아예 안 간다. 화면이 1 을 적으면 "평소대로 들어간다" 는 거짓말이다.
    expect(readVol([{ vol: "없음", mult: "없음" }])).toBeNull();
    expect(readVol([])).toBeNull();
  });
});
