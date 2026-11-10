/**
 * 엔진 다리 요약 산수 — 상세 화면 · 펀드 카드 · 첫 화면이 같은 값을 쓴다 (T822 · 순수).
 *
 * 몫이 어디서 어떻게 팔리는지는 서버가 몫마다 `exit`(값) · `exit_how`(방식)로 준다. 화면은 몫 종류를
 * 풀어 읽지 않고 그 둘만 그린다. 값을 모르면(다음 마감 뒤 정해진다) 지어내지 않고 그렇게 적는다.
 */
import type { EngineNote, FundEngineStatus } from "./api";
import type { MarkTone } from "./Chart";

export type EngineLine = { price: number; label: string; tone: MarkTone };

export type EngineFacts = {
  /** 돈이 들어간 계약의 가중 평균 진입가 — 없으면 null. */
  avgEntry: number | null;
  /** 평균 진입 대비 지금 값 % (배율 안 곱한 가격 변동). */
  vsEntryPct: number | null;
  /** 증거금 대비 미실현 %. */
  vsMarginPct: number | null;
  stepUnits: number | null;
  maxUnits: number | null;
  /** 걸려 있는 다음 계단 값 — 없으면 null. */
  nextStep: number | null;
  nextCloseMs: number | null;
};

function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function ms(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  return Number.isFinite(at) ? at : null;
}

/** 아직 덜 찬 계단 지정가의 값 — 다 찼거나 없으면 null. */
function pendingStep(buy: FundEngineStatus["buy"]): number | null {
  if (!buy) return null;
  if ((buy.filled ?? 0) >= buy.contracts) return null;
  const level = num(buy.level);
  return level !== null && level > 0 ? level : null;
}

/**
 * 엔진 요약에서 한 줄 값들을 뽑는다.
 *
 * @param status 서버 엔진 요약.
 * @param markFallback 요약에 표시가가 없을 때 쓸 값(차트 마지막 종가 · 카드 현재가).
 */
export function engineFacts(
  status: FundEngineStatus,
  markFallback?: number | null,
): EngineFacts {
  let qty = 0;
  let cost = 0;
  for (const u of status.units) {
    const fill = num(u.fill);
    if (u.ghost || !(u.contracts > 0) || fill === null) continue;
    qty += u.contracts;
    cost += fill * u.contracts;
  }
  const buy = status.buy;
  const dayFill = num(buy?.fill) ?? num(buy?.level);
  if (buy?.filled && dayFill !== null) {
    qty += buy.filled;
    cost += dayFill * buy.filled;
  }
  const avgEntry = qty > 0 ? cost / qty : null;
  const mark = num(status.mark) ?? markFallback ?? null;
  const margin = num(status.margin);
  const unrealized = num(status.unrealized);
  return {
    avgEntry,
    vsEntryPct:
      avgEntry !== null && avgEntry > 0 && mark !== null && mark > 0
        ? ((mark - avgEntry) / avgEntry) * 100
        : null,
    vsMarginPct:
      margin !== null && margin > 0 && unrealized !== null
        ? (unrealized / margin) * 100
        : null,
    stepUnits: status.step_units ?? null,
    maxUnits: status.max_units ?? null,
    nextStep: pendingStep(buy),
    nextCloseMs: ms(status.next_close),
  };
}

/** 엔진 요약 → 첫 화면 줄과 같은 꼴의 짧은 요약. */
export function noteOf(status: FundEngineStatus): EngineNote {
  const next = pendingStep(status.buy);
  return {
    ...(status.step_units === undefined
      ? {}
      : { step_units: status.step_units }),
    ...(status.max_units === undefined ? {} : { max_units: status.max_units }),
    next_step: next === null ? null : String(next),
    next_close: status.next_close ?? null,
  };
}

/** 남은 시간 글 — 지났으면 처리 대기라고 적는다. */
export function untilText(targetMs: number | null, nowMs: number): string {
  if (targetMs === null) return "—";
  const left = targetMs - nowMs;
  if (left <= 0) return "마감 지남 · 처리 대기";
  const minutes = Math.floor(left / 60_000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  if (days > 0) return `${days}일 ${hours}시간`;
  if (hours > 0) return `${hours}시간 ${minutes % 60}분`;
  return `${minutes}분`;
}

/**
 * 짧은 요약 글 — "계단 몫 2/5 · 다음 계단 25,480 · 다음 마감까지 3시간 12분".
 * 모르는 칸은 빼고 적는다(전부 모르면 빈 글).
 */
export function noteText(
  note: EngineNote,
  nowMs: number,
  fmt: (value: number) => string,
): string {
  const parts: string[] = [];
  if (note.step_units !== undefined && note.max_units !== undefined)
    parts.push(`계단 몫 ${note.step_units}/${note.max_units}`);
  const next = num(note.next_step);
  if (next !== null && next > 0) parts.push(`다음 계단 ${fmt(next)}`);
  const close = ms(note.next_close);
  if (close !== null) parts.push(`다음 마감까지 ${untilText(close, nowMs)}`);
  return parts.join(" · ");
}

/** 파는 방식의 짧은 이름 — 모르는 방식은 빈 글. */
export function exitHowText(how: string | undefined): string {
  if (how === "limit") return "매도 지정가";
  if (how === "close_below") return "종가가 이 아래면 청산";
  if (how === "close_above") return "종가가 이 위면 청산";
  return "";
}

/**
 * 몫 한 줄에 적는 청산 자리 — 값이 있으면 "방식 값", 종가 규칙인데 값을 모르면 다음 마감 뒤라고 적는다.
 * 정한 자리가 없는 몫 · 걸린 지정가가 없는 몫은 빈 글.
 */
export function exitText(
  exit: string | null | undefined,
  how: string | undefined,
  fmt: (value: number) => string,
): string {
  const label = exitHowText(how);
  if (!label) return "";
  const price = num(exit);
  if (price !== null && price > 0) return `${label} ${fmt(price)}`;
  return how === "limit" ? "" : `${label} — 값은 다음 마감 뒤 표시`;
}

/**
 * 차트에 긋는 규칙선 — 돈이 들어간 몫의 청산 자리와 걸려 있는 다음 계단.
 * 진입가 · 재난 손절은 매매 상자가 이미 그리므로 여기서 다시 안 긋는다. 같은 자리의 선은 하나로 합친다.
 */
export function engineLines(status: FundEngineStatus): EngineLine[] {
  const out: EngineLine[] = [];
  const seen = new Set<string>();
  for (const u of status.units) {
    const price = num(u.exit);
    if (u.ghost || !(u.contracts > 0) || price === null || !(price > 0))
      continue;
    const kind = u.name ?? u.key;
    const drawn: EngineLine | null =
      u.exit_how === "limit"
        ? { price, label: `${kind} 매도선`, tone: "gain" }
        : u.exit_how === "close_below"
          ? { price, label: `${kind} 청산선(종가가 아래면)`, tone: "loss" }
          : u.exit_how === "close_above"
            ? { price, label: `${kind} 청산선(종가가 위면)`, tone: "gain" }
            : null;
    if (drawn === null) continue;
    const key = `${u.exit_how}:${price}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(drawn);
  }
  const next = pendingStep(status.buy);
  if (next !== null)
    out.push({ price: next, label: "다음 계단(지정가)", tone: "entry" });
  return out;
}
