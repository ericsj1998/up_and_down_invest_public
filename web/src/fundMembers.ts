/**
 * 펀드 상세(T261) 순수 조각 — 서버 일봉 → 차트 봉, 등락 색.
 */

import type { FundMember } from "./api";
import type { Ohlc } from "./chart/indicators";

/** 서버 봉(문자열 가격) → 차트 봉. 숫자가 아닌 줄은 버린다. */
export function toOhlc(bars: FundMember["bars"]): Ohlc[] {
  const out: Ohlc[] = [];
  for (const b of bars) {
    const o = Number(b.open);
    const h = Number(b.high);
    const l = Number(b.low);
    const c = Number(b.close);
    if (![o, h, l, c].every(Number.isFinite)) continue;
    out.push({ time: b.time, open: o, high: h, low: l, close: c });
  }
  return out;
}

/** 등락 문장 — `+1.25%` · 값이 없으면 `—`. */
export function changeText(pct: string | null | undefined): string {
  if (pct === null || pct === undefined) return "—";
  const v = Number(pct);
  if (!Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** 등락 색 클래스 — 화면 전체가 한 값(.gain/.loss)을 쓴다. */
export function changeTone(pct: string | null | undefined): "gain" | "loss" | "" {
  const v = Number(pct);
  if (!Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "gain" : "loss";
}

/**
 * 카드 **테두리 색** — 지금 이익인가 손해인가 (사용자 요구 2026-09-21).
 *
 * 🔴 **들고 있는 종목만 색을 준다.** 포지션이 없으면 "보고 있는" 손익이 없다 — 지난 실현으로
 * 칠하면 지금 아무 일도 안 하는 카드가 초록으로 빛나 눈이 거기로 간다.
 *
 * ⚠️ 원장과 거래소가 갈린 종목(`reconciled` · `accounting_ok` 거짓)은 손익이 **미확정**이라
 * 색을 안 준다 — 확정되지 않은 수를 색으로 단언하지 않는다.
 */
export function cardTone(m: {
  holding?: boolean;
  unrealized?: string;
  reconciled?: boolean;
  accounting_ok?: boolean;
}): "gain" | "loss" | "" {
  if (m.holding !== true) return "";
  if (m.reconciled === false || m.accounting_ok === false) return "";
  return changeTone(m.unrealized);
}

/**
 * 미실현을 **증거금 대비 %** 로 — 금액 옆에 붙인다 (손익은 금액 + % 병기).
 *
 * ⚠️ 분모는 **거래소가 이 포지션에 잡은 증거금**(`margin`)이다. 몫(`equity` = 예산 + 실현)이
 * 아니다 — 둘은 다른 돈이고, 섞으면 같은 손익이 카드마다 다른 % 로 보인다.
 */
export function unrealizedPct(m: { unrealized?: string; margin?: string }): number | null {
  const usdt = Number(m.unrealized);
  const base = Number(m.margin);
  if (!Number.isFinite(usdt) || !Number.isFinite(base) || base <= 0) return null;
  return (usdt / base) * 100;
}
