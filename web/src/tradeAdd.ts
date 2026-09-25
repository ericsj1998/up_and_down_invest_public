import type { TradeAdd } from "./api";

/**
 * 닫힌 매매의 손익 % — 불타기 추가분까지 (T308).
 *
 * `gain_pct` 는 처음 크기만 센 값이고 분모가 `margin_used`(이 매매가 쓴 자리 예산)다.
 * 추가분은 같은 자리 안에서 더 실은 것이라 **같은 분모**로 더한다 — 그래야 `margin x %` 가
 * 거래소 실현 금액과 같다. 분모를 모르면(옛 행 · 백테스트) 추가분을 %로 바꿀 수 없어
 * `gain_pct` 그대로 둔다(지어내지 않는다).
 */
export function closedPct(
  gainPct: number | null,
  marginUsed: string | null | undefined,
  add: TradeAdd | null | undefined,
): number | null {
  if (gainPct === null) return null;
  const extra = add ? Number(add.pnl) : 0;
  const margin = marginUsed === null || marginUsed === undefined ? Number.NaN : Number(marginUsed);
  if (!extra || !Number.isFinite(extra) || !Number.isFinite(margin) || margin <= 0) {
    return gainPct;
  }
  return gainPct + (extra / margin) * 100;
}

/** 불타기 한 줄 — 딱지 · 표에 붙인다. 없으면 null. */
export function addLabel(add: TradeAdd | null | undefined): string | null {
  if (!add) return null;
  if (add.contracts > 0) {
    const pnl = Number(add.pnl);
    const tail = Number.isFinite(pnl) && pnl !== 0 ? ` · 추가분 ${pnl > 0 ? "+" : ""}${pnl.toFixed(2)} USDT` : "";
    return `불타기 +${add.contracts}계약 @${add.fill ?? add.price ?? "?"}${tail}`;
  }
  if (add.held) return `불타기 버림 (${add.held})`;
  return null;
}
