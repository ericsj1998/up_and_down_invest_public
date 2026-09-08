/**
 * 계획 제안 읽기·고치기 — **사람이 손대는 값들** (차트 주문 2·3단계).
 *
 * 사용자 요구 2026-08-30: *"적합한 진입점, 손절점, 1차 익절점, 익절점을 제안해줌.
 * 사용자는 그게 마음에 들면 그대로 래버리지 달아서 주문 오더하는거고, 아니라면 손으로
 * 수정 (숫자 입력도 가능하고, 손으로 가로선을 끌어서 지정도 가능) 해서 오더."*
 *
 * ## 🔴 고쳐도 산수는 다시 돈다
 *
 * 사람이 손절을 끌어 올리면 RR 이 오르고 필요 승률이 내린다 — **그 값을 즉시 다시
 * 보여 준다.** 안 보여 주면 사람이 "손절을 좁히면 좋아진다" 는 착각으로 끌게 되고,
 * 그것이 T173 에서 잡은 바로 그 함정이다 (손절폭 0.40% · RR 3.05 · 노이즈에 죽는다).
 *
 * ⛔ **여기서 주문을 만들지 않는다.** 이 파일은 값의 앞뒤가 맞는지만 본다. 집행값의
 * SSoT 는 RiskManager 이고(절대 규칙 #4), 서버가 다시 검증한다.
 */

/** 계획 한 장 — 서버가 제안했고 사람이 고칠 수 있다. */
export interface Plan {
  long: boolean;
  entry: number;
  stop: number;
  first: number;
  target: number;
}

/** 고친 계획의 산수 — 화면이 그대로 보여 준다. */
export interface Math {
  /** 1차까지의 손익비. */
  rr: number;
  /** 이기려면 필요한 승률 (%). 100 을 넘으면 산술적으로 불가능하다. */
  needPct: number;
  /** 손절 거리 (%). */
  stopPct: number;
}

export const MIN_STOP_PCT = 0.5;
/**
 * 손절 거리 하한 (%) — 라이브 1.3.0 이 쓰는 그 값.
 *
 * 🔴 T173 실측: 이 아래인 계획이 BTC 1h 에서 **83%** 였고, 그 계획들의 RR 3.05 는
 * 종이 위의 것이었다. 지지 바로 밑에 손절을 붙이면 RR 은 얼마든지 커지는데 **그
 * 계획은 거래소 노이즈 한 번에 죽는다.**
 *
 * ⚠️ **막지는 않는다** (2026-08-30) — 차트 주문의 주체는 사람이고 좁은 손절이
 * 의도일 수 있다 (스캘핑). 하지만 **경고를 약하게 읽으면 안 된다.**
 *
 * 🔴 **비용 산수는 이 함정을 안 잡는다.** 잡을 거라고 생각했는데 실제로 계산해 보니
 * 아니었다 — 목표를 그대로 두고 손절만 당기면 RR 이 비용보다 빨리 커져서 필요
 * 승률이 **내려간다**:
 *
 *     손절 2.0% · RR 3   →  필요 승률 26.96%
 *     손절 0.5% · RR 12  →  필요 승률 10.11%
 *     손절 0.2% · RR 30  →  필요 승률  5.76%   ← 숫자는 계속 좋아진다
 *
 * 산수가 잡는 것은 **손절과 목표가 같이 좁을 때**다 (스캘핑 · 5m 이 죽은 모양):
 *
 *     손절 2.0% · RR 2   →  35.95%
 *     손절 0.2% · RR 2   →  59.50%
 *     손절 0.1% · RR 2   →  85.67%
 *
 * ⇒ *"목표는 멀리 두고 손절만 바짝"* 은 **오직 이 하한만이** 말해 준다. 이것을
 *   지우면 그 함정을 막는 것이 아무것도 없다.
 */

/**
 * 고친 값으로 산수를 다시 돌린다.
 *
 * @param plan 지금 값 (사람이 고친 뒤일 수 있다).
 * @param roundTripPct 왕복 비용 (%) — **가격 대비**다.
 *
 * 🔴 **비용은 R 단위로 환산해서 넣는다** (2026-08-30 에 고쳤다). `P > (1+c)/(1+RR)`
 * 의 `c` 는 가격 대비 비용이 아니라 **손절폭으로 나눈** 값이다:
 *
 *     손절 2.0% · RR 2 · 비용 0.157%  →  틀린 값 33.4%   맞는 값 36.0%
 *     손절 0.2% · RR 2 · 비용 0.157%  →  틀린 값 33.4%   맞는 값 **59.5%**
 *
 * 가격 대비로 넣으면 **손절이 좁을수록 크게 틀리고**, 정확히 위험한 쪽으로 틀린다.
 * 5m 을 폐기한 계산이 그것이었다 (필요 승률 100.7~100.9%) — 가격 대비로 재면 그
 * 5m 조차 "33% 면 된다" 고 나온다. 서버(`analysis/plan.py`)와 같은 식이다.
 *
 * 🪞 **롱·숏을 한 벌로 다룬다.** 부호 하나로 갈리는 것을 두 벌로 쓰면 한쪽만 고쳐진다.
 */
export function recompute(plan: Plan, roundTripPct: number): Math | null {
  const sign = plan.long ? 1 : -1;
  const risk = (plan.entry - plan.stop) * sign;
  const reward = (plan.first - plan.entry) * sign;
  if (!(plan.entry > 0) || risk <= 0 || reward <= 0) return null;
  const rr = reward / risk;
  const stopPct = (risk / plan.entry) * 100;
  const costInR = roundTripPct / stopPct;
  return { rr, needPct: ((1 + costInR) / (1 + rr)) * 100, stopPct };
}

/**
 * 이 계획을 **주문으로 낼 수 있나** — 못 내면 이유를 돌려준다.
 *
 * @param plan 지금 값.
 * @param roundTripPct 왕복 비용 (%).
 * @returns 막는 이유들. 빈 배열이면 통과.
 *
 * 🔴 **화면이 먼저 막는 것은 편의다.** 진짜 문은 서버에 있고 RiskManager 가 확정한다
 * (절대 규칙 #4) — 여기서 통과했다고 주문이 나가는 것이 아니다.
 *
 * ⚠️ 그래도 여기서 말하는 이유: 단추를 눌러 거절을 받는 것보다 **끄는 동안** 아는
 * 것이 낫다. 사람은 끌면서 배운다.
 */
export function blockers(plan: Plan, roundTripPct: number): string[] {
  const out: string[] = [];
  const side = plan.long ? "롱" : "숏";
  if (!(plan.entry > 0)) out.push("진입가가 없다");
  if (plan.long ? plan.stop >= plan.entry : plan.stop <= plan.entry) {
    out.push(`손절이 진입 ${plan.long ? "위" : "아래"}에 있다 — ${side}이 아니다`);
  }
  if (plan.long ? plan.first <= plan.entry : plan.first >= plan.entry) {
    out.push(`1차 익절이 진입 ${plan.long ? "아래" : "위"}에 있다`);
  }
  if (plan.long ? plan.target < plan.first : plan.target > plan.first) {
    out.push("최종 익절이 1차보다 앞이다");
  }
  const math = recompute(plan, roundTripPct);
  if (math === null) return out;
  if (math.needPct >= 100) {
    out.push("필요 승률이 100% 를 넘는다 — 산술적으로 이길 수 없다");
  }
  return out;
}

/**
 * 말은 하되 **막지는 않는** 것들 — 판단은 사람 것이다.
 *
 * @param plan 지금 값.
 * @param roundTripPct 왕복 비용 (%).
 * @returns 경고들.
 *
 * 🔴 **`blockers` 와 나누는 기준은 "판단이냐 사실이냐"** 다. 필요 승률 100% 초과는
 * 산수라 막고, 좁은 손절은 의도일 수 있어 말만 한다 (사용자 확정 2026-08-30:
 * *"그냥 사람이 정하는대로 다 들어가는 거야"*).
 *
 * ⚠️ 다만 손절폭 경고는 **약한 경고가 아니다** — 비용 산수가 못 잡는 함정을 잡는
 * 유일한 자리다 (`MIN_STOP_PCT` 의 표를 본다).
 */
export function warnings(plan: Plan, roundTripPct: number): string[] {
  const math = recompute(plan, roundTripPct);
  if (math === null) return [];
  const out: string[] = [];
  if (math.stopPct < MIN_STOP_PCT) {
    out.push(
      `손절폭 ${math.stopPct.toFixed(2)}% 가 하한 ${MIN_STOP_PCT}% 안이다 — 노이즈에 죽는다`,
    );
  }
  if (math.needPct >= 50) {
    out.push(`필요 승률 ${math.needPct.toFixed(1)}% — 절반 넘게 맞아야 본전이다`);
  }
  return out;
}

/**
 * 끄는 중인 선을 새 값으로 — **다른 선을 밀지 않는다**.
 *
 * @param plan 지금 값.
 * @param which 어느 선인가.
 * @param price 새 값.
 *
 * ⛔ **자동으로 순서를 맞추지 않는다.** 손절을 진입 위로 끌면 그대로 두고 `blockers`
 * 가 말한다 — 화면이 몰래 고치면 사람이 무엇을 만졌는지 잃는다.
 */
export function move(plan: Plan, which: keyof Plan, price: number): Plan {
  if (which === "long") return plan;
  return { ...plan, [which]: price };
}
