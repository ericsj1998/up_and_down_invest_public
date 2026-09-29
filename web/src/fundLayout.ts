/**
 * 펀드 종목의 **묶음 · 순서 · 히트맵 색** (사용자 2026-09-27: *"각 매매법마다로 그루핑되어야 할 것 같고, 매매법
 * 내에서도 중요 6종과 같이 정렬에 대한 기준이 있어야"* · *"펀드 내 종목들의 히트맵도"*).
 *
 * 묶음 규칙 — 종목을 **그 종목을 굴리는 다리들의 조합**으로 가른다. 다리는 종목이 겹치므로(MACD 롱은 40종 전부)
 * 다리 하나로 가르면 한 종목이 여러 묶음에 나온다. 조합으로 가르면 겹치지 않는다:
 *
 *   혼합 2.1.0 → 돌파 롱 · 삼각 숏 · MACD 롱(핵심 6) / 삼각 숏 · MACD 롱(알트 12) / MACD 숏 · MACD 롱(알트 22)
 *
 * 묶음 순서 = 조합의 가장 앞 다리(선언 순서) → 다리가 많은 쪽. 묶음 안 순서 = **24시간 거래대금 내림차순**
 * (종목 순위 탭과 같은 자 · 돈이 많이 도는 종목이 위 · 값 없으면 뒤) → 종목 이름.
 *
 * ⚠️ 화면(`FundPanel.tsx`)이 아니라 여기에 두는 이유: 묶음과 순서는 **규칙**이라 시험이 있어야 한다.
 */

import type { FundLeg, FundPreview } from "./api";

export type FundLegInfo = {
  playbook: string;
  name: string;
  symbols: string[];
  slots: number;
  exposure: string;
  isolated: boolean;
  /** 다리 손익(실현 + 미실현 · USDT)과 펀드 대비 % — 서버 `leg_pnl`. 옛 서버면 없다. */
  pnl?: string;
  pct?: string;
};

export type SymbolGroupView = {
  key: string;
  /** 묶음 이름 — 다리 이름을 선언 순서로 잇는다(`돌파 롱 · 삼각 숏 · MACD 롱`). */
  label: string;
  symbols: string[];
};

/** 종목별 시세 한 줄 — 종목 순위 API(`/exchange/ranking`)의 부분집합. */
export type Tick = { change?: number | null; turnover?: number | null };

/** 순위 표의 종목 이름과 펀드 종목 이름을 맞춘다 — `BTC_USDT` · `BTCUSDT` 둘 다 받는다. */
export function tickOf(ticks: Record<string, Tick>, symbol: string): Tick | undefined {
  return ticks[symbol] ?? ticks[symbol.replace("_", "")];
}

/**
 * 묶음 안 순서의 첫 열쇠 — **지금 상태** (사용자 2026-09-27).
 *
 * > *"이득 미실현 포지션, 이득 실현, 손해 미실현 포지션, 손해 실현, 현금 순으로 정렬해 달라"* ·
 * > *"예비 신호가 난 종목은 매매법 내에서 가장 상단으로"*
 *
 *   0 진입 가능(깜빡임) · 1 보유 · 미실현 ≥ 0 · 2 현금 · 실현 > 0 · 3 보유 · 미실현 < 0 · 4 현금 · 실현 < 0 · 5 현금
 *
 * 실현은 표가 그리는 값과 같다 — 거래소와 갈린 판은 거래소 실측(`verified_realized`)이 있으면 그것.
 * 보유 중인데 미실현을 아직 못 읽었으면 1(이득 쪽)에 둔다 — 모르는 것을 손실로 끌어내리지 않는다.
 */
export function positionRank(v: FundLeg | undefined): number {
  if (!v) return 5;
  if (v.preview && v.holding !== true) return 0;
  if (v.holding === true) {
    const u = Number(v.unrealized);
    return Number.isFinite(u) && u < 0 ? 3 : 1;
  }
  const diverged = v.reconciled === false || v.accounting_ok === false;
  const r = Number(diverged && v.verified_realized != null ? v.verified_realized : v.realized);
  if (Number.isFinite(r) && r > 0) return 2;
  if (Number.isFinite(r) && r < 0) return 4;
  return 5;
}

export function legGroups(
  legs: readonly FundLegInfo[],
  symbols: readonly string[],
  ticks: Record<string, Tick> = {},
  rank: (symbol: string) => number = () => 0,
): SymbolGroupView[] {
  const byKey = new Map<string, { cover: number[]; symbols: string[] }>();
  for (const sym of symbols) {
    const cover = legs.flatMap((leg, i) => (leg.symbols.includes(sym) ? [i] : []));
    const key = legs.length === 0 ? "all" : cover.length === 0 ? "none" : cover.join(",");
    const slot = byKey.get(key) ?? { cover, symbols: [] };
    slot.symbols.push(sym);
    byKey.set(key, slot);
  }
  const turnover = (s: string) => tickOf(ticks, s)?.turnover ?? null;
  const ordered = [...byKey.entries()].sort(([, a], [, b]) => {
    const first = (a.cover[0] ?? Number.MAX_SAFE_INTEGER) - (b.cover[0] ?? Number.MAX_SAFE_INTEGER);
    return first !== 0 ? first : b.cover.length - a.cover.length;
  });
  return ordered.map(([key, g]) => ({
    key,
    label:
      key === "all" ? "종목" : key === "none" ? "다리 없음" : g.cover.map((i) => legs[i]?.name ?? "?").join(" · "),
    symbols: [...g.symbols].sort((a, b) => {
      const ra = rank(a);
      const rb = rank(b);
      if (ra !== rb) return ra - rb;
      const ta = turnover(a);
      const tb = turnover(b);
      if (ta !== tb) {
        if (ta === null) return 1;
        if (tb === null) return -1;
        return tb - ta;
      }
      return a.localeCompare(b);
    }),
  }));
}

/** 묶음들을 이은 종목 순서 — 표 · 차트 카드가 같은 순서를 쓴다. */
export function groupedOrder(groups: readonly SymbolGroupView[]): string[] {
  return groups.flatMap((g) => g.symbols);
}

/** 히트맵 칸 색 — 24시간 등락 ±8% 에서 꽉 찬다 · 값 없으면 무채색 (손익 색 토큰과 같은 초록 · 빨강). */
export function heatColor(change: number | null | undefined): { bg: string; fg: string } {
  if (change === null || change === undefined || !Number.isFinite(change)) {
    return { bg: "rgba(96,125,139,0.12)", fg: "inherit" };
  }
  const strength = Math.min(1, Math.abs(change) / 8);
  const alpha = 0.12 + 0.73 * strength;
  const rgb = change >= 0 ? "15,123,108" : "180,66,58";
  return { bg: `rgba(${rgb},${alpha.toFixed(2)})`, fg: alpha > 0.5 ? "#fff" : "inherit" };
}

/**
 * 진입 가능성 한 줄 (깜빡임 설명 · 사용자 2026-09-27).
 *
 * `signal` 은 **마감 전 예비 신호**다 — 형성 중 봉이 지금 값으로 닫히면 같은 탐지기가 후보를 낸다.
 * 펀드 문(자리 · 명목 상한 · 브레이크) 전이라 진입이 약속된 것은 아니다. 그 말을 툴팁에 그대로 싣는다.
 */
export function previewText(p: FundPreview, legName?: string): string {
  const leg = legName || p.name || p.leg.split("@")[0] || "";
  if (p.kind === "waiting") {
    return `진입 주문 대기 · ${p.side} @ ${trimNum(p.entry)} · 손절 ${trimNum(p.stop)} · ${leg}`;
  }
  return (
    `마감 전 예비 신호 · ${p.side}${p.frame ? ` · ${p.frame} 봉` : ""} · ${leg} — ` +
    "봉이 지금 값으로 닫히면 진입 후보가 된다(펀드 자리 · 상한 전이라 약속은 아니다)"
  );
}

/** 표 · 칸에 들어갈 짧은 말. */
export function previewShort(p: FundPreview): string {
  // ⭐ 2026-09-30 사용자 "예비 신호에도 다리 이름" — 어느 다리의 신호인지 같이 적는다.
  const leg = p.name || p.leg.split("@")[0] || "";
  const head = p.kind === "waiting" ? `진입 주문 · ${p.side}` : `예비 신호 · ${p.side}`;
  return leg ? `${head} · ${leg}` : head;
}

function trimNum(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumSignificantDigits: 6 }) : "—";
}


/**
 * 묶음 한 줄의 손익 — 표 줄들의 실현 + 미실현 합 · 그 줄들 몫(예산 + 손익) 합 대비 %.
 *
 * % 의 분모는 **그 줄들이 책임지는 돈**(몫 합)이다 — 줄마다의 % 와 같은 자.
 */
export function groupPnl(
  symbols: readonly string[],
  rows: Record<string, FundLeg | undefined>,
): { amount: number; pct: number | null } {
  let amount = 0;
  let base = 0;
  for (const sym of symbols) {
    const v = rows[sym];
    if (!v) continue;
    const diverged = v.reconciled === false || v.accounting_ok === false;
    const r = Number(diverged && v.verified_realized != null ? v.verified_realized : v.realized);
    const u = v.holding ? Number(v.unrealized) : 0;
    amount += (Number.isFinite(r) ? r : 0) + (Number.isFinite(u) ? u : 0);
    const e = Number(v.equity);
    base += Number.isFinite(e) ? e : 0;
  }
  return { amount, pct: base > 0 ? (amount / base) * 100 : null };
}

/** 정각 가드 구간(초) — 정각 앞뒤로 이만큼은 리밸런싱을 미룬다. */
export const HOUR_GUARD_S = 30;

/**
 * 지금이 **매시 정각 ± 30초** 안인가 (사용자 2026-09-27).
 *
 * > *"매 시 정각부터 전후 30초 사이에는 안내문과 타이머(정각까지 남은 시간)를 출력해 주고, 그 시간이 끝나면
 * > 리밸런싱 하는 걸로 해줘."*
 *
 * 봉 마감 진입은 정각 뒤 15 ~ 20초에 크기를 정하고 약 2.4초 뒤 체결된다 — 그 사이 예산이 바뀌면 진입이 옛 예산으로
 * 나가고 실측 노출만 새 예산으로 적힌다(무해하지만 헷갈린다). 이 구간을 피한다.
 *
 * @returns 구간 밖이면 null · 안이면 `{toHour, until}` — 정각까지 남은 ms(지났으면 0) · 구간 끝(ms 시각).
 */
export function hourGuard(nowMs: number): { toHour: number; until: number } | null {
  const hour = 3_600_000;
  const guard = HOUR_GUARD_S * 1000;
  const prev = Math.floor(nowMs / hour) * hour;
  const next = prev + hour;
  if (nowMs - prev < guard) return { toHour: 0, until: prev + guard };
  if (next - nowMs <= guard) return { toHour: next - nowMs, until: next + guard };
  return null;
}
