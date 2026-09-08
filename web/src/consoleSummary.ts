/**
 * 콘솔 상단 카드용 — **계좌 전체** 포지션 합 (2026-09-06 · 사용자 신고).
 *
 * 🔴 전에는 카드 셋(포지션 · 잡혀 있는 증거금 · 미실현)이 `body.position`, 즉 **고른 종목 하나**(기본 BTC_USDT)의
 *    포지션만 그렸다. 6종 펀드에서 ETH 에만 포지션이 있으면 "포지션 없음 · — · —" 이 뜨는데 아래 표에는 ETH 롱이
 *    있다 — 같은 화면이 모순된 두 말을 했다. 카드는 계좌 단위라야 한다. 종목별은 아래 목록이 맡는다.
 *
 * 숫자는 서버가 준 문자열을 그대로 더한다. 못 읽은 값(빈 문자열·NaN)은 0 이 아니라 **건너뛰고** 개수에서 뺀다.
 *
 * 🔴 **% 의 분모 규칙 (사용자 확정 2026-09-06)**: `%` 는 **그 줄이 책임지는 돈** 대비다 — 상단 카드 = 계좌 총액 ·
 *    펀드 패널 = 펀드 잔고 · RUN 표·펀드 종목 표 = 그 판 예산 · 체결 이력의 "수익률 (증거금)" 만 매매 단위 RR 통계용
 *    예외이고 이름표를 단다. 같은 0.30 USDT 가 증거금 대비 +7.3% 와 계좌 대비 +0.1% 로 갈려 보이던 것을 끊는다.
 */

export type PositionRow = Record<string, string | undefined> & { symbol: string };

export interface PositionsSummary {
  /** 포지션이 있는 종목 수. */
  count: number;
  /** 계약 수 합 (절댓값). */
  contracts: number;
  /** 잡혀 있는 증거금 합 (USDT). 값을 못 읽은 행은 뺀다 — 그 수는 `unpriced`. */
  margin: number;
  /** 미실현 손익 합 (USDT). % 는 여기서 내지 않는다 — 분모는 화면 층이 정한다(상단 카드 = 계좌 총액). */
  pnl: number;
  /** 증거금·손익을 못 읽은 행 수 (0 이 아니면 합이 부분합이다). */
  unpriced: number;
  /** 한 줄 요약 — `ETH_USDT 롱 1 @2,448.58 · 6x` 순. */
  lines: string[];
}

function asNumber(value: string | undefined): number | null {
  if (value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fmt(value: number, digits: number): string {
  return value.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 계좌 전체 포지션 합. 빈 목록이면 count 0. */
export function positionsSummary(rows: readonly PositionRow[]): PositionsSummary {
  let contracts = 0;
  let margin = 0;
  let pnl = 0;
  let unpriced = 0;
  const lines: string[] = [];
  for (const row of rows) {
    const size = asNumber(row["size"]) ?? 0;
    if (size === 0) continue; // 크기 0 은 포지션이 아니다 (거래소가 빈 스냅샷을 줄 때가 있다)
    contracts += Math.abs(size);
    const m = asNumber(row["margin"]);
    const p = asNumber(row["unrealised_pnl"]);
    if (m === null || p === null) unpriced += 1;
    margin += m ?? 0;
    pnl += p ?? 0;
    const entry = asNumber(row["entry_price"]);
    const lev = row["leverage"] ? ` · ${row["leverage"]}x` : "";
    lines.push(
      `${row.symbol} ${size > 0 ? "롱" : "숏"} ${fmt(Math.abs(size), 0)}${entry === null ? "" : ` @${fmt(entry, 2)}`}${lev}`,
    );
  }
  const count = lines.length;
  return {
    count,
    contracts,
    margin,
    pnl,
    unpriced,
    lines,
  };
}
