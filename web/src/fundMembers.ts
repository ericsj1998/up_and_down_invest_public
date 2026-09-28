/**
 * 펀드 상세(T261) 순수 조각 — 서버 일봉 → 차트 봉, 등락 색.
 */

import type { FundMember } from "./api";
import type { Ohlc } from "./chart/indicators";
import type { TradeMark } from "./chart/trades";

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
 * 카드 **테두리 색** — 지금 이익인가 손해인가, 아니면 **못 믿는 값인가** (사용자 요구 2026-09-21).
 *
 * 🔴 **갈림이 손익보다 먼저다.** 원장과 거래소가 어긋난 종목(`reconciled` · `accounting_ok`
 * 거짓)은 손익이 **미확정**이라 초록/빨강으로 단언하면 안 된다 — 그 수가 맞는지를 아직
 * 모르는 상태다. 노랑은 "틀렸다" 가 아니라 **"못 믿는다"** 는 뜻이다.
 *
 * ⚠️ 갈림은 **들고 있지 않아도** 칠한다. 포지션이 닫힌 뒤에 회계가 갈린 경우가 실제로 있었고
 * (2026-09-01), 그때 카드가 아무 색도 없으면 사람은 그 종목이 멀쩡한 줄 안다.
 *
 * 🔴 **들고 있는 종목만 손익 색을 준다.** 포지션이 없으면 "보고 있는" 손익이 없다 — 지난
 * 실현으로 칠하면 지금 아무 일도 안 하는 카드가 초록으로 빛나 눈이 거기로 간다.
 */
export function cardTone(m: {
  holding?: boolean;
  unrealized?: string;
  reconciled?: boolean;
  accounting_ok?: boolean;
}): "gain" | "loss" | "warn" | "" {
  if (m.reconciled === false || m.accounting_ok === false) return "warn";
  if (m.holding !== true) return "";
  return changeTone(m.unrealized);
}

/**
 * 미실현을 **그 줄이 책임지는 돈 대비 %** 로 — 금액 옆에 붙인다 (손익은 금액 + % 병기).
 *
 * 🔴 분모는 **종목 몫**(`equity` = 배정 예산 + 정산 뒤 실현)이다 (09-06 규칙 "그 줄이 책임지는 돈" ·
 * 2026-09-29 사용자 "규칙대로 판 예산 대비로"). 예전엔 증거금 대비(배율 반영)라 같은 손익이 표(몫 대비)와
 * 카드(증거금 대비)에서 6배 다른 % 로 보였다. 몫 줄(T320)도 같은 종목 몫으로 나눈다 — 몫 % 의 합 = 종목 %.
 *
 * @param unrealized 미실현(USDT) — 없거나 null 이면 null.
 * @param base 그 줄의 몫(USDT) — 0 이하 · 못 읽으면 null.
 */
export function unrealizedPct(
  unrealized: string | null | undefined,
  base: string | null | undefined,
): number | null {
  if (unrealized === null || unrealized === undefined) return null;
  const usdt = Number(unrealized);
  const money = Number(base);
  if (!Number.isFinite(usdt) || !Number.isFinite(money) || money <= 0) return null;
  return (usdt / money) * 100;
}

/**
 * 열린 포지션 → 차트 상자들. 몫이 여럿이면(T320) **몫마다 하나** — 진입가 · 손절 · 손익이 몫마다 다르다.
 *
 * ⚠️ 진입 시각이나 지금가가 없으면 **안 그린다** — 상자의 한쪽 끝을 지어내지 않는다.
 * 그때는 가로선도 안 나오는데, 없는 자리에 선을 긋는 것보다 낫다.
 *
 * @param m 펀드 종목 한 줄.
 * @returns 상자들 — 없으면 빈 목록.
 */
export function memberMarks(m: FundMember): TradeMark[] {
  const last = Number(m.last);
  if (!Number.isFinite(last)) return [];
  // 마지막 마감 봉까지 — 마감 봉만 그리는 카드라 "지금" 은 마지막 봉이다.
  const end = (openedTs: number) =>
    m.bars.length > 0 ? (m.bars[m.bars.length - 1]?.time ?? openedTs) : openedTs;
  const box = (
    id: string,
    side: string,
    entryRaw: string,
    stopRaw: string,
    opened: string | null | undefined,
    pnl: number | null,
  ): TradeMark | null => {
    const entry = Number(entryRaw);
    const stop = Number(stopRaw);
    const openedTs = opened ? Math.floor(Date.parse(opened) / 1000) : 0;
    if (![entry, stop].every(Number.isFinite) || !(openedTs > 0)) return null;
    return {
      id,
      symbol: m.symbol,
      side: side === "숏" ? -1 : 1,
      entry,
      exit: last,
      stop,
      openedTs,
      closedTs: end(openedTs),
      pnl,
      reason: "보유중",
      open: true,
    };
  };
  if (m.shares && m.shares.length > 1) {
    return m.shares.flatMap((s) => {
      const one = box(
        `${m.handle || m.symbol}:${s.leg}`,
        s.side,
        s.entry,
        s.stop,
        s.opened_at,
        unrealizedPct(s.unrealized, m.equity),
      );
      return one ? [{ ...one, leg: s.name }] : [];
    });
  }
  const pos = m.position;
  if (!pos) return [];
  const one = box(
    m.handle || m.symbol,
    pos.side,
    pos.entry,
    pos.stop,
    pos.opened_at,
    unrealizedPct(m.unrealized, m.equity),
  );
  return one ? [one] : [];
}
