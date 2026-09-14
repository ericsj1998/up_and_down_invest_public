/**
 * 차트 채점(dev · T281) — 순수 함수. 서버 매매 행 → 표기, 재생 커서 → 보이는 봉(만드는 중 봉 포함), 수기 포지션 → 영역.
 *
 * ⚠️ 여기서 남기는 O/X · 수기 포지션은 **성과 채점이 아니라 규칙을 끌어내는 입력**이다(절대 규칙 #11). 사람이 "나라면
 * 여기" 를 예시로 남기면 그것을 규칙으로 환원해 코드에 넣는다 — 채택은 백테스트 OOS 가 정한다.
 *
 * 재생은 **하위 봉(15m) 단위**로 커서를 옮긴다. 기준봉(1h)은 커서가 든 시간의 하위 봉을 합쳐 "만드는 중" 봉으로 보이고,
 * 지표(볼린저 · 이평)는 그 봉을 포함해 다시 계산된다 — 그래야 라이브 차트처럼 밴드가 늘었다 줄었다 한다.
 */
import type { Ohlc } from "../chart/indicators";
import type { TradeMark } from "../chart/trades";

/** 서버 매매 행 (`GET /admin/grading/trades`). */
export interface GradingTrade {
  id: string;
  symbol: string;
  /** 엔진의 다리 종류 — fade(반전) · trend(추종) · impulse · random. */
  kind: string;
  side: 1 | -1;
  entry: number;
  exit: number;
  stop: number;
  opened_ts: number;
  closed_ts: number;
  /** 순손익(%). */
  pnl: number;
  gross_pct: number;
  reason: string;
  bars_held: number;
}

export type Grade = "O" | "X";

/** 서버 요약(`summarize`) — 설정 전체와 이 종목. */
export interface Summary {
  n: number;
  net_sum: number;
  gross_sum: number;
  win_rate: number;
  avg_net: number;
  exits: Record<string, number>;
  stops: number;
  mdd: number;
  worst: number;
}

/** 다리·방향 짧은 꼬리표 — `TL` 추종 롱 · `RS` 반전 숏 (그림 스크립트와 같은 표기). */
export function tag(kind: string, side: 1 | -1): string {
  const k = kind === "trend" ? "T" : kind === "fade" ? "R" : kind === "impulse" ? "I" : "?";
  return `${k}${side === 1 ? "L" : "S"}`;
}

/** 시각·가격을 옮긴 포지션 — 봉 간격 단위로 시간을 맞춘다. */
export function movePosition(p: UserPosition, dPrice: number, dTime: number, step: number): UserPosition {
  const dt = Math.round(dTime / step) * step;
  return { ...p, entry: p.entry + dPrice, stop: p.stop + dPrice, target: p.target + dPrice, from: p.from + dt, to: p.to + dt };
}

/** 사람이 그린 포지션 — 진입선 가운데 · 손절까지 붉게 · 목표까지 초록. */
export interface UserPosition {
  id: string;
  side: 1 | -1;
  entry: number;
  stop: number;
  target: number;
  /** 시작 시각(epoch 초 · 봉 시작). */
  from: number;
  /** 끝 시각(epoch 초). */
  to: number;
  note: string;
}

export interface Marks {
  grades: Record<string, Grade>;
  positions: UserPosition[];
  notes: string;
  saved_at: string | null;
}

export const EMPTY_MARKS: Marks = { grades: {}, positions: [], notes: "", saved_at: null };

/** 서버 행 → 차트 표기(`TradeMark`). 손절이 없는 매매(0)는 진입가로 둔다 — 영역이 0 폭이라 안 보인다. */
export function toMark(t: GradingTrade): TradeMark {
  return {
    id: t.id,
    symbol: t.symbol,
    side: t.side,
    entry: t.entry,
    exit: t.exit,
    stop: t.stop > 0 ? t.stop : t.entry,
    openedTs: t.opened_ts,
    closedTs: t.closed_ts,
    pnl: t.pnl,
    reason: t.reason,
    leg: t.kind,
  };
}

/** 하위 봉들을 하나로 합친다 — 만드는 중 기준봉. 비면 null. */
export function aggregate(subs: readonly Ohlc[], time: number): Ohlc | null {
  const first = subs[0];
  const last = subs[subs.length - 1];
  if (first === undefined || last === undefined) return null;
  let high = -Infinity;
  let low = Infinity;
  for (const s of subs) {
    if (s.high > high) high = s.high;
    if (s.low < low) low = s.low;
  }
  return { time, open: first.open, high, low, close: last.close };
}

/**
 * 재생 커서까지 보이는 기준봉 — 마감된 기준봉 + 커서 시각까지의 하위 봉을 합친 만드는 중 봉.
 *
 * @param bars 기준봉 전체(오름차순).
 * @param subs 하위 봉 전체(오름차순 · 없으면 빈 배열).
 * @param cursor 커서 = 마지막으로 **마감된 하위 봉의 시작 시각**. null 이면 전부(재생 아님).
 * @param step 기준봉 간격(초).
 * @param subStep 하위 봉 간격(초).
 */
export function visibleBars(
  bars: readonly Ohlc[],
  subs: readonly Ohlc[],
  cursor: number | null,
  step: number,
  subStep: number,
): Ohlc[] {
  if (cursor === null) return [...bars];
  const cutoff = cursor + subStep; // 커서 봉의 마감 시각
  const out: Ohlc[] = [];
  for (const b of bars) {
    if (b.time + step <= cutoff) out.push(b);
    else break;
  }
  const lastClosed = out[out.length - 1];
  const formingStart = lastClosed === undefined ? (bars[0]?.time ?? cutoff) : lastClosed.time + step;
  if (subs.length === 0) return out;
  const inside = subs.filter((s) => s.time >= formingStart && s.time + subStep <= cutoff);
  const forming = aggregate(inside, formingStart);
  if (forming !== null) out.push(forming);
  return out;
}

/** 커서까지 "일어난" 매매 — 진입이 커서 뒤면 숨기고, 청산이 커서 뒤면 아직 열린 것으로(청산 표기 없이). */
export function visibleTrades(trades: readonly GradingTrade[], cursor: number | null, subStep: number): TradeMark[] {
  const out: TradeMark[] = [];
  for (const t of trades) {
    if (cursor !== null && t.opened_ts > cursor + subStep) continue;
    const mark = toMark(t);
    if (cursor !== null && t.closed_ts > cursor + subStep) {
      out.push({ ...mark, closedTs: cursor + subStep, exit: mark.entry, reason: "open" });
    } else {
      out.push(mark);
    }
  }
  return out;
}

/** 재생 커서의 첫 값 — 기준봉 `warm` 개가 마감된 뒤 첫 하위 봉. */
export function startCursor(bars: readonly Ohlc[], subs: readonly Ohlc[], warm: number, step: number): number | null {
  const anchor = bars[Math.min(warm, Math.max(0, bars.length - 1))];
  if (anchor === undefined) return null;
  const first = subs.find((s) => s.time >= anchor.time + step) ?? subs.find((s) => s.time >= anchor.time);
  return first?.time ?? anchor.time;
}

/** 다음 커서 — 하위 봉이 있으면 다음 하위 봉, 없으면 다음 기준봉. 끝이면 null. */
export function nextCursor(subs: readonly Ohlc[], bars: readonly Ohlc[], cursor: number, step: number): number | null {
  if (subs.length > 0) {
    const i = subs.findIndex((s) => s.time > cursor);
    return i < 0 ? null : subs[i]?.time ?? null;
  }
  const j = bars.findIndex((b) => b.time > cursor);
  if (j < 0) return null;
  return (bars[j]?.time ?? cursor + step);
}

/** 이전 커서 — 하위 봉이 있으면 이전 하위 봉, 없으면 이전 기준봉. 처음이면 null (사용자: 역으로 가는 것도). */
export function prevCursor(subs: readonly Ohlc[], bars: readonly Ohlc[], cursor: number): number | null {
  const pool = subs.length > 0 ? subs : bars;
  let found: number | null = null;
  for (const b of pool) {
    if (b.time >= cursor) break;
    found = b.time;
  }
  return found;
}

/** 새 수기 포지션 — 클릭한 가격이 진입, 손절·목표는 1%·2% 로 시작(끌어서 고친다). 기간은 기준봉 24개. */
export function newPosition(side: 1 | -1, entry: number, at: number, step: number, id: string): UserPosition {
  const risk = entry * 0.01;
  return {
    id,
    side,
    entry,
    stop: side === 1 ? entry - risk : entry + risk,
    target: side === 1 ? entry + risk * 2 : entry - risk * 2,
    from: at - (at % step),
    to: at - (at % step) + step * 24,
    note: "",
  };
}

/** 포지션 하나의 손익비 — 목표 거리 ÷ 손절 거리. 손절이 진입과 같으면 null. */
export function rewardRisk(p: UserPosition): number | null {
  const risk = Math.abs(p.entry - p.stop);
  if (!(risk > 0)) return null;
  return Math.abs(p.target - p.entry) / risk;
}

export interface PositionZone {
  from: number;
  to: number;
  low: number;
  high: number;
  tone: "gain" | "loss";
}

/** 수기 포지션의 영역 — 진입↔손절 붉게 · 진입↔목표 초록. */
export function positionZones(p: UserPosition): PositionZone[] {
  return [
    { from: p.from, to: p.to, low: Math.min(p.entry, p.stop), high: Math.max(p.entry, p.stop), tone: "loss" },
    { from: p.from, to: p.to, low: Math.min(p.entry, p.target), high: Math.max(p.entry, p.target), tone: "gain" },
  ];
}

/** 내보내기 JSON — 봉 · 매매 · O/X · 수기 포지션을 한 파일로 (사용자 요구 ②). */
export function exportPayload(input: {
  file: string;
  config: string;
  symbol: string;
  market: string | null;
  timeframe: string;
  bars: readonly Ohlc[];
  trades: readonly GradingTrade[];
  marks: Marks;
}): Record<string, unknown> {
  return {
    exported_at: new Date().toISOString(),
    caveat: "O/X 와 수기 포지션은 규칙을 끌어내는 예시다 — 성과 채점이 아니다 (절대 규칙 #11).",
    file: input.file,
    config: input.config,
    symbol: input.symbol,
    market: input.market,
    timeframe: input.timeframe,
    bars: input.bars,
    trades: input.trades.map((t) => ({ ...t, grade: input.marks.grades[t.id] ?? null })),
    positions: input.marks.positions,
    notes: input.marks.notes,
  };
}

/** O/X 집계 — 다리 종류별. */
export function gradeSummary(trades: readonly GradingTrade[], grades: Record<string, Grade>): { kind: string; o: number; x: number; none: number }[] {
  const by = new Map<string, { o: number; x: number; none: number }>();
  for (const t of trades) {
    const row = by.get(t.kind) ?? { o: 0, x: 0, none: 0 };
    const g = grades[t.id];
    if (g === "O") row.o += 1;
    else if (g === "X") row.x += 1;
    else row.none += 1;
    by.set(t.kind, row);
  }
  return [...by.entries()].map(([kind, row]) => ({ kind, ...row }));
}
