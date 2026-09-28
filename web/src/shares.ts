/**
 * 한 종목 포지션을 나눠 쓰는 **몫**들의 손익 (T320).
 *
 * Gate 는 종목당 포지션이 하나라 거래소는 미실현 · 증거금을 **포지션 전체** 숫자 하나로만 준다.
 * 같은 방향 다리 둘(예: 돌파 롱 2계약 @100 · 일봉 채널 3계약 @120)이 그 포지션을 나눠 쓰면, 그 숫자를
 * 그대로 두 상자에 달면 두 매매가 같은 % 를 말한다 — 한쪽은 이기고 한쪽은 지는데도.
 *
 * ⇒ 몫마다 `계약 x (표시가 - 몫 평단) x 승수` 로 나눈다(서버 `split_unrealized` 와 같은 식). 합이 거래소
 *   미실현과 정확히 같도록 남는 차이(수수료 반올림 · 평단 차)는 계약 비로 얹는다. 증거금은 몫의 진입
 *   명목 비로 나눈다(격리 증거금은 진입 명목에 비례한다).
 *
 * ⛔ 계약 수를 모르는 몫이 있거나 거래소 값(표시가 · 명목 · 계약)을 못 읽으면 **null** — 부르는 쪽이
 *   원장과 같은 식(`openPct`)으로 떨어진다. 추정해 나누지 않는다.
 */

/** 몫 하나 — 원장 매매 기록에서 온다. */
export type ShareInput = {
  id: string;
  side: 1 | -1;
  entry: number;
  /** 진입 체결 계약 수. 0 이하 = 모른다. */
  contracts: number;
  /** 불타기로 더 산 계약 (T308). */
  addContracts?: number;
  /** 불타기 체결 평단 — 없으면 진입가로 본다. */
  addFill?: number | null;
};

/** 몫 하나의 미실현 — 금액 · 증거금 · 증거금 대비 %. */
export type ShareGain = { usdt: number; margin: number; pct: number | null };

function sum(values: readonly number[]): number {
  return values.reduce((a, b) => a + b, 0);
}

/**
 * 거래소 포지션 하나를 몫마다 나눈다.
 *
 * @param shares 그 종목의 열린 몫들.
 * @param position 거래소 포지션 조회(`size` · `mark_price` · `value` · `unrealised_pnl` · `margin`).
 * @returns 몫 id → 미실현. 못 나누면 null.
 */
export function splitPosition(
  shares: readonly ShareInput[],
  position: Record<string, string> | null | undefined,
): Map<string, ShareGain> | null {
  if (!position || shares.length === 0) return null;
  const total = Number(position["unrealised_pnl"]);
  const margin = Number(position["margin"]);
  const mark = Number(position["mark_price"]);
  const value = Math.abs(Number(position["value"]));
  const size = Math.abs(Number(position["size"]));
  if (![total, margin, mark, value, size].every(Number.isFinite)) return null;
  if (mark <= 0 || value <= 0 || size <= 0) return null;
  if (shares.some((s) => !(s.contracts > 0) || !Number.isFinite(s.entry))) return null;
  const multiplier = value / (size * mark);
  const held = shares.map((s) => s.contracts + (s.addContracts ?? 0));
  const cost = shares.map(
    (s) => s.contracts * s.entry + (s.addContracts ?? 0) * (s.addFill ?? s.entry),
  );
  const raw = shares.map((s, i) => ((held[i] ?? 0) * mark - (cost[i] ?? 0)) * multiplier * s.side);
  const heldSum = sum(held);
  const costSum = sum(cost);
  if (heldSum <= 0 || costSum <= 0) return null;
  const gap = total - sum(raw);
  const out = new Map<string, ShareGain>();
  shares.forEach((s, i) => {
    const usdt = (raw[i] ?? 0) + (gap * (held[i] ?? 0)) / heldSum;
    const part = (margin * (cost[i] ?? 0)) / costSum;
    out.set(s.id, { usdt, margin: part, pct: part > 0 ? (usdt / part) * 100 : null });
  });
  return out;
}
