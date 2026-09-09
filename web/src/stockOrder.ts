/**
 * 주식 주문 창의 산수 — 순수 (T250).
 *
 * 코인 차트 주문은 "예산이 곧 수량" 이지만 주식은 **정수 주**를 사람이 정한다. 화면은 주수 x 진입가 = 필요 현금을
 * 보여 주고, 서버는 그 값을 예산으로 받아 같은 사이징 경로로 정확히 그 주수를 낸다.
 */

import type { Plan } from "./proposal";

/** 필요 현금 = 주수 x 진입가. 잘못된 입력은 0. */
export function cashNeeded(shares: number, entry: number): number {
  if (!Number.isFinite(shares) || !Number.isFinite(entry) || shares <= 0 || entry <= 0) return 0;
  return shares * entry;
}

/** 이 현금으로 살 수 있는 최대 정수 주수. */
export function maxShares(cash: number, entry: number): number {
  if (!Number.isFinite(cash) || !Number.isFinite(entry) || cash <= 0 || entry <= 0) return 0;
  return Math.floor(cash / entry);
}

/**
 * 서버 제안이 없을 때의 초안 — 마지막 종가 기준 롱.
 *
 * 손절 2% 아래 · 1차 익절 2% 위 · 목표 4% 위. 제안이 아니라 **끌어서 고칠 출발점**이다 — 숫자에 뜻은 없다.
 */
export function defaultDraft(close: number): Plan | null {
  if (!Number.isFinite(close) || close <= 0) return null;
  const round = (v: number) => Number(v.toFixed(2));
  return {
    long: true,
    entry: round(close),
    stop: round(close * 0.98),
    first: round(close * 1.02),
    target: round(close * 1.04),
  };
}

/**
 * 주문 단추를 막는 이유들 — 서버도 다시 막지만 누르기 전에 아는 것이 낫다.
 *
 * @param shares 주수.
 * @param entry 진입가.
 * @param cash 가용 현금 (모르면 null — 현금 검사는 서버에 맡긴다).
 * @param marketState 장 상태 (`open` · `closed` · `unknown`). 24시간 장은 `open` 으로 넘긴다.
 * @param allowed 이 시장에서 거래할 권한 (T242).
 */
export function orderBlockers(args: {
  shares: number;
  entry: number;
  cash: number | null;
  marketState: "open" | "closed" | "unknown";
  allowed: boolean;
}): string[] {
  const out: string[] = [];
  if (!args.allowed) out.push("이 시장에서 거래할 권한이 없다");
  if (!Number.isInteger(args.shares) || args.shares <= 0) out.push("주수는 1 이상의 정수");
  const need = cashNeeded(args.shares, args.entry);
  if (args.cash !== null && need > args.cash)
    out.push(`현금 부족 — 필요 ${need.toFixed(2)} · 가용 ${args.cash.toFixed(2)}`);
  if (args.marketState !== "open") out.push("장이 열려 있지 않다 — 예약 주문 없음");
  return out;
}
