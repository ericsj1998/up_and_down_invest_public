/**
 * 온보딩 위저드의 산수 — 순수 (T247).
 *
 * "예상" 은 없다. 여기서 계산하는 것은 **납입 누계**(수익 가정 없음)와 단계 진행 조건뿐이다.
 * 과거 창 실측은 서버(`/assistant/preview`)가 저장소에서 낸다.
 */

export const STEPS = ["consent", "capital", "profile", "setup", "review", "done"] as const;
export type Step = (typeof STEPS)[number];

export type Cadence = "week" | "month" | "year";

export type Answers = {
  capital?: number;
  contribution?: number;
  cadence?: Cadence;
  years?: number;
  group?: "coin" | "domestic" | "foreign";
  tier?: "safe" | "balanced" | "aggressive";
  playbook?: string;
  label?: string;
};

const PER_YEAR: Record<Cadence, number> = { week: 52, month: 12, year: 1 };

/** 납입 누계 — 시작 자본 + 주기 납입 x 횟수. 수익률을 곱하지 않는다 (예상 금지). */
export function contributed(capital: number, contribution: number, cadence: Cadence, years: number): number {
  const base = Number.isFinite(capital) && capital > 0 ? capital : 0;
  const each = Number.isFinite(contribution) && contribution > 0 ? contribution : 0;
  const span = Number.isFinite(years) && years > 0 ? years : 0;
  return base + each * PER_YEAR[cadence] * span;
}

/** 다음 단계. 끝이면 그대로. */
export function nextStep(step: Step): Step {
  const index = STEPS.indexOf(step);
  return STEPS[Math.min(index + 1, STEPS.length - 1)] ?? step;
}

export function prevStep(step: Step): Step {
  const index = STEPS.indexOf(step);
  return STEPS[Math.max(index - 1, 0)] ?? step;
}

/** 이 단계에서 다음으로 갈 수 있나 — 막는 이유. 비면 갈 수 있다. */
export function blockers(step: Step, answers: Answers, consented: boolean): string[] {
  const out: string[] = [];
  if (step === "consent" && !consented) out.push("동의가 필요하다");
  if (step === "capital" && !(answers.capital && answers.capital > 0)) out.push("시작 금액은 0 보다 커야 한다");
  if (step === "profile") {
    if (!answers.group) out.push("종목 갈래를 고른다");
    if (!answers.tier) out.push("성향을 고른다");
  }
  if (step === "setup" && !answers.playbook) out.push("매매법을 고른다");
  return out;
}

/** 브라우저에 두는 초안 (게스트 · 서버가 `persisted: false` 라 할 때). */
export const LOCAL_SLOT = "assistant-draft";

export function readLocal(): { step: Step; answers: Answers; consented: boolean } | null {
  try {
    const raw = localStorage.getItem(LOCAL_SLOT);
    if (!raw) return null;
    const got: unknown = JSON.parse(raw);
    if (!got || typeof got !== "object") return null;
    const rec = got as { step?: unknown; answers?: unknown; consented?: unknown };
    const step = (STEPS as readonly string[]).includes(String(rec.step)) ? (rec.step as Step) : "consent";
    return {
      step,
      answers: rec.answers && typeof rec.answers === "object" ? (rec.answers as Answers) : {},
      consented: rec.consented === true,
    };
  } catch {
    return null;
  }
}

export function writeLocal(value: { step: Step; answers: Answers; consented: boolean }): void {
  try {
    localStorage.setItem(LOCAL_SLOT, JSON.stringify(value));
  } catch {
    // 기억만 못 한다.
  }
}
