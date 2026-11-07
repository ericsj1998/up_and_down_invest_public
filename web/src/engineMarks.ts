/**
 * 엔진 다리 몫 → 차트 매매 상자 (사용자 2026-11-07 "어디에 어떻게 들어갔는지도 안 나오고").
 *
 * 판 화면처럼 진입 세로줄 · 상자 · 진입가 · 손절선을 그린다. 한 포지션 안에 몫이 여럿이라 몫마다 상자 하나다.
 * 유령 몫은 돈이 안 들어갔으니 안 그린다. 옛 닫힌 몫은 진입 시각이 없어 안 그린다 — 시각을 지어내지 않는다.
 * 손익 % 는 판 상자와 같이 증거금 대비(가격 변동 x 배율) · 배율을 모르면 비운다.
 */
import type { TradeMark } from "./chart/trades";
import type { FundEngineStatus } from "./api";

const REASON: Record<string, string> = {
  grid_sell: "계단 매도",
  stop: "재난 손절",
  breakout_exit: "돌파 청산",
  bounce_exit: "되돌림 청산",
};

function seconds(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  return Number.isFinite(at) ? Math.floor(at / 1000) : null;
}

function pct(
  entry: number,
  exit: number,
  leverage: number | null,
): number | null {
  if (leverage === null || !(entry > 0)) return null;
  return ((exit - entry) / entry) * 100 * leverage;
}

export function engineMarks(
  status: FundEngineStatus,
  symbol: string,
  name: string,
  candles: readonly { ts: string; close: number | string }[],
): TradeMark[] {
  const last = candles[candles.length - 1];
  if (last === undefined) return [];
  const lastTs = seconds(last.ts);
  const now = Number(last.close);
  if (lastTs === null || !Number.isFinite(now)) return [];
  const lev = Number(status.leverage);
  const leverage = Number.isFinite(lev) && lev > 0 ? lev : null;
  const out: TradeMark[] = [];
  const open = (
    id: string,
    kind: string | undefined,
    fillRaw: string | null | undefined,
    stopRaw: string | null | undefined,
    opened: string | null | undefined,
  ) => {
    const entry = Number(fillRaw);
    const openedTs = seconds(opened);
    if (!(entry > 0) || openedTs === null) return;
    const stop = Number(stopRaw);
    out.push({
      id,
      symbol,
      side: 1,
      entry,
      exit: now,
      stop: stop > 0 ? stop : entry,
      openedTs,
      closedTs: Math.max(lastTs, openedTs),
      pnl: pct(entry, now, leverage),
      reason: "보유중",
      leg: `${name} ${kind ?? ""}`.trim(),
      open: true,
    });
  };
  for (const u of status.units) {
    if (!u.ghost && u.contracts > 0)
      open(u.key, u.name, u.fill, u.stop, u.opened);
  }
  const buy = status.buy;
  if (buy?.filled)
    open(`buy-${buy.slot}`, buy.name, buy.fill, buy.stop, buy.filled_at);
  (status.closed ?? []).forEach((c, index) => {
    const openedTs = seconds(c.opened);
    const closedTs = seconds(c.at);
    const entry = Number(c.fill);
    const exit = Number(c.exit);
    if (openedTs === null || closedTs === null || !(entry > 0) || !(exit > 0))
      return;
    out.push({
      id: `${c.unit}:${index}`,
      symbol,
      side: 1,
      entry,
      exit,
      stop: entry,
      openedTs,
      closedTs,
      pnl: pct(entry, exit, leverage),
      reason: REASON[c.how] ?? c.how,
      leg: `${name} ${c.kind === "brk" ? "돌파" : c.kind === "bnc" ? "되돌림" : "계단"}`,
    });
  });
  return out;
}
