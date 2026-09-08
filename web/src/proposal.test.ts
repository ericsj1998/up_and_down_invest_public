/**
 * 계획을 고치면 **산수가 다시 돈다** — 그리고 그 산수가 사람을 막는다.
 *
 * 🔴 T173 에서 잡은 함정: 손절을 지지 바로 밑에 붙이면 RR 은 얼마든지 커지는데
 * **그 계획은 노이즈 한 번에 죽는다.** 실측 BTC 1h 에서 그런 계획이 83% 였고 RR 은
 * 3.05 였다 — 숫자만 보면 좋아 보인다.
 *
 * ## 🔴 이 파일이 두 번 고쳐졌다 (2026-08-30) — 그 과정이 결론보다 중요하다
 *
 * ### ① 비용 단위가 틀렸다
 *
 * `P > (1+c)/(1+RR)` 의 `c` 는 **R 단위**(비용 ÷ 손절폭)인데 가격 대비 비용을 그대로
 * 넣고 있었다. 값이 비슷해 보여서 시험도 통과했다 — 비용 0.157% 는 1 에 비해 거의
 * 0 이라 어떤 단위로 넣든 답이 비슷하다. **손절이 좁아질 때만 갈린다.**
 *
 * ### ② "산수가 좁은 손절을 잡아 준다" 는 **틀린 생각이었다**
 *
 * ①을 고치면서 *"이제 손절을 좁히면 필요 승률이 치솟으니 하한은 경고면 충분하다"* 고
 * 적었는데, 계산해 보니 아니었다:
 *
 *     손절 2.0% · RR 3   →  26.96%
 *     손절 0.2% · RR 30  →   5.76%   ← **목표가 그대로면 오히려 내려간다**
 *
 * RR 이 1/risk 로 커지는데 비용도 1/risk 로 커지고, **reward 가 비용보다 크면 RR 이
 * 이긴다.** 산수가 잡는 것은 손절과 목표가 **같이** 좁을 때뿐이다 (5m 이 죽은 모양).
 *
 * ⇒ 그래서 손절폭 하한은 **지워지면 안 된다** — *"목표는 멀리, 손절만 바짝"* 을
 *   말하는 유일한 자리다. 아래에 그 사실을 잠그는 시험이 있다.
 */

import { describe, expect, it } from "vitest";
import { MIN_STOP_PCT, blockers, move, recompute, warnings, type Plan } from "./proposal";

const PLAN: Plan = { long: true, entry: 100, stop: 98, first: 106, target: 112 };
const COST = 0.157;

describe("recompute — 고친 값으로 다시 잰다", () => {
  it("손익비와 필요 승률과 손절폭을 낸다", () => {
    const got = recompute(PLAN, COST);
    expect(got).not.toBeNull();
    expect(got?.rr).toBeCloseTo(3, 5);
    expect(got?.stopPct).toBeCloseTo(2, 5);
    // 🔴 **R 단위** 비용이다: c = 0.157% / 2% = 0.0785 → (1.0785)/4 = 26.96%.
    //    가격 대비로 넣으면 25.04% 로 **낮게** 나온다 — 그것이 고친 버그다.
    expect(got?.needPct).toBeCloseTo(26.96, 1);
  });

  it("🔴 같은 RR 이라도 **손절이 좁으면 더 많이 맞아야 한다**", () => {
    // 비용이 R 을 통째로 먹기 때문이다. 가격 대비로 재면 이 차이가 **아예 안 보인다**.
    const wide = recompute({ ...PLAN, stop: 98, first: 104 }, COST);
    const tight = recompute({ ...PLAN, stop: 99.8, first: 100.4 }, COST);
    expect(tight!.rr).toBeCloseTo(wide!.rr, 5);
    expect(tight!.needPct).toBeGreaterThan(wide!.needPct + 20);
  });

  it("⚠️ 목표를 그대로 두고 손절만 당기면 필요 승률은 **내려간다**", () => {
    // 🔴 이것이 산수의 한계다 — 그래서 손절폭 하한이 따로 있어야 한다.
    //    이 시험이 깨지면 하한 경고를 지워도 되는지 다시 생각해 볼 수 있다는 뜻이다.
    const tight = move(PLAN, "stop", 99.8);
    expect(recompute(tight, COST)!.needPct).toBeLessThan(recompute(PLAN, COST)!.needPct);
    // ⇒ 그러니 **경고는 반드시 있어야 한다.**
    expect(warnings(tight, COST).some((item) => item.includes("하한"))).toBe(true);
  });

  it("🪞 숏도 같은 식으로 잰다 — 부호 하나로 갈린다", () => {
    const short: Plan = { long: false, entry: 100, stop: 102, first: 94, target: 88 };
    const got = recompute(short, COST);
    expect(got?.rr).toBeCloseTo(3, 5);
    expect(got?.stopPct).toBeCloseTo(2, 5);
    expect(got?.needPct).toBeCloseTo(26.96, 1);
  });

  it("앞뒤가 안 맞으면 null", () => {
    expect(recompute({ ...PLAN, stop: 101 }, COST)).toBeNull();
    expect(recompute({ ...PLAN, first: 99 }, COST)).toBeNull();
    expect(recompute({ ...PLAN, entry: 0 }, COST)).toBeNull();
  });
});

describe("blockers — 낼 수 없는 것만 막는다", () => {
  it("멀쩡한 계획은 안 막는다", () => {
    expect(blockers(PLAN, COST)).toEqual([]);
  });

  it("🔴 좁은 손절은 **막지 않는다** — 판단은 사람 것이다", () => {
    // 사용자 확정 2026-08-30: *"그냥 사람이 정하는대로 다 들어가는 거야."*
    // 스캘핑이면 좁은 손절이 의도일 수 있다. 대신 `warnings` 가 말한다.
    const tight = move(PLAN, "stop", 99.9);
    expect(blockers(tight, COST)).toEqual([]);
    expect(warnings(tight, COST).some((item) => item.includes("하한"))).toBe(true);
  });

  it("손절이 진입 위면 롱이 아니다", () => {
    const why = blockers(move(PLAN, "stop", 101), COST);
    expect(why.some((item) => item.includes("롱이 아니다"))).toBe(true);
  });

  it("1차가 진입 아래면 막는다", () => {
    expect(blockers(move(PLAN, "first", 99), COST)).not.toEqual([]);
  });

  it("최종이 1차보다 앞이면 막는다", () => {
    const why = blockers(move(PLAN, "target", 101), COST);
    expect(why.some((item) => item.includes("최종"))).toBe(true);
  });

  it("⛔ 필요 승률 100% 초과는 산술적으로 불가능하다 — 5m 이 그랬다", () => {
    // 🔴 5m 이 죽은 모양: 손절도 목표도 좁아 비용이 R 을 통째로 먹는다.
    const why = blockers({ long: true, entry: 100, stop: 99.8, first: 100.02, target: 100.1 }, COST);
    expect(why.some((item) => item.includes("산술적으로"))).toBe(true);
  });

  it("🪞 숏도 같은 말을 한다", () => {
    const why = blockers({ long: false, entry: 100, stop: 98, first: 94, target: 88 }, COST);
    expect(why.some((item) => item.includes("숏이 아니다"))).toBe(true);
  });
});

describe("warnings — 말은 하되 막지 않는다", () => {
  it("멀쩡한 계획에는 아무 말도 안 한다", () => {
    expect(warnings(PLAN, COST)).toEqual([]);
  });

  it("경계값은 조용하다 — 하한은 '이 값 미만' 이다", () => {
    const edge = move(PLAN, "stop", 100 - MIN_STOP_PCT);
    expect(warnings(edge, COST).some((item) => item.includes("하한"))).toBe(false);
  });

  it("필요 승률이 절반을 넘으면 말한다", () => {
    // 손절 2% · 1차 익절 1% → RR 0.5 → (1.0785)/1.5 = 71.9%
    const thin = { ...PLAN, first: 101 };
    expect(warnings(thin, COST).some((item) => item.includes("필요 승률"))).toBe(true);
  });
});

describe("move — 몰래 고치지 않는다", () => {
  it("끈 선만 바뀐다", () => {
    const got = move(PLAN, "stop", 97);
    expect(got.stop).toBe(97);
    expect(got.entry).toBe(PLAN.entry);
    expect(got.first).toBe(PLAN.first);
  });

  it("⛔ 순서가 어긋나도 **자동으로 안 맞춘다** — 화면이 몰래 고치면 사람이 잃는다", () => {
    const got = move(PLAN, "stop", 105);
    expect(got.stop).toBe(105);
    // 대신 막는다.
    expect(blockers(got, COST)).not.toEqual([]);
  });

  it("원본을 안 건드린다", () => {
    move(PLAN, "stop", 50);
    expect(PLAN.stop).toBe(98);
  });
});
