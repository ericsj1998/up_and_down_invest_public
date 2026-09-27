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

export type FundLegInfo = {
  playbook: string;
  name: string;
  symbols: string[];
  slots: number;
  exposure: string;
  isolated: boolean;
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

export function legGroups(
  legs: readonly FundLegInfo[],
  symbols: readonly string[],
  ticks: Record<string, Tick> = {},
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
