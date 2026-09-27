/**
 * 도는 RUN 목록의 **순서** (사용자 요구 2026-09-21: *"포지션이 잡히고, 이득이 높은 순에서
 * 낮은 순으로 내림차순 정렬해줘."* → 2026-09-27: 실현 + · 미실현 + · 실현 − · 미실현 − · 진입 대기).
 *
 * 🔴 서버는 `started_at` 내림차순으로만 준다 (`walkforward.py` 의 `sessions`). T291 의 18종은
 * 23:43:58 ~ 23:44:35 사이 **38초 안에 전부** 만들어져서 그 순서는 사실상 무작위였고, 화면
 * 맨 위는 늘 아무 일도 안 하는 판이었다.
 *
 * ⚠️ 화면(`Runs.tsx`)이 아니라 여기에 두는 이유: 정렬은 **규칙**이라 시험이 있어야 한다.
 * 컴포넌트 안에 인라인으로 두면 다음 사람이 한 줄 고칠 때 근거가 안 남는다.
 */

/** 정렬에 필요한 것만 — 화면 행(`Summary`)의 부분집합이라 그대로 넘겨도 된다. */
export type Sortable = {
  session_id: string;
  symbol: string;
  trades: number;
  closed: number;
  return_pct: number;
};

/** 판별로 따로 오는 건강 폴링 — 거래소 포지션과 미실현이 여기 있다. */
export type Beat = {
  exchange?: { position?: Record<string, string> | null } | null;
} | null | undefined;

/**
 * 그 판이 **지금 들고 있나**.
 *
 * 근거 둘을 OR 로 본다 — 거래소 포지션(참말)과 원장의 열린 매매(`trades > closed`).
 * 건강 폴링이 아직 안 온 판도 원장으로 잡히고, 원장이 늦은 판도 거래소로 잡힌다.
 */
export function holdsNow(row: Sortable, beat: Beat): boolean {
  if (beat?.exchange?.position) return true;
  return row.trades > row.closed;
}

/**
 * 그 판이 **지금 버는 돈** (미실현 USDT).
 *
 * ⚠️ 값이 없으면 0 이다 — 없는 것을 위로 올리지 않는다. 첫 폴링이 오면 순서가 한 번 바뀐다.
 */
export function livePnl(beat: Beat): number {
  const raw = beat?.exchange?.position?.["unrealised_pnl"];
  const value = raw === undefined || raw === "" ? Number.NaN : Number(raw);
  return Number.isFinite(value) ? value : 0;
}

/** 정렬 묶음 — 숫자가 작을수록 위다 (사용자 요구 2026-09-27). */
export const GROUP = {
  REALIZED_UP: 0,
  LIVE_UP: 1,
  REALIZED_DOWN: 2,
  LIVE_DOWN: 3,
  WAITING: 4,
} as const;

/**
 * 그 판이 어느 묶음인가 — **지금 상태**로 가른다.
 *
 * 들고 있는 판은 미실현으로(지금 버는 돈이 그 판의 이야기다), 안 들고 있는 판은 실현 손익률로,
 * 둘 다 없으면(실현 0 · 보유 없음) 진입 대기 현금이다.
 *
 * ⚠️ 들고 있는데 미실현이 아직 안 온 판(`livePnl` = 0)은 미실현 양수 쪽에 둔다 — 첫 폴링이 오면
 * 제자리를 찾는다. 모르는 것을 손실로 끌어내리지 않는다.
 */
export function groupOf(row: Sortable, beat: Beat): number {
  if (holdsNow(row, beat)) return livePnl(beat) >= 0 ? GROUP.LIVE_UP : GROUP.LIVE_DOWN;
  if (row.return_pct > 0) return GROUP.REALIZED_UP;
  if (row.return_pct < 0) return GROUP.REALIZED_DOWN;
  return GROUP.WAITING;
}

/**
 * 도는 RUN 을 정렬한다 — **원본을 안 건드린다**.
 *
 * 🔴 사용자 요구(2026-09-27): *"실현손익 양수, 미실현 손익 양수, 실현손익 음수, 미실현 손익 음수,
 * 진입대기(현금) 순으로 정렬해줘."* 전에는 보유 판이 무조건 먼저라 손해 본 보유 판이 실현 이익 판보다
 * 위에 있었고, 진입 대기 판과 실현 손실 판이 섞여 맨 아래로 갔다.
 *
 * 키 (앞이 먼저):
 *   ① 묶음(`groupOf`)        — 실현 + · 미실현 + · 실현 − · 미실현 − · 진입 대기
 *   ② 묶음 안 손익 내림차순  — 미실현 묶음은 미실현 USDT · 실현 묶음은 실현 손익률(위가 좋은 쪽)
 *   ③ 종목 이름              — 동점일 때(막 만든 판은 전부 0) **무엇을 기준으로 줄 세울지**
 *
 * ⚠️ ③ 이 없어도 줄이 흔들리지는 **않는다** — `Array.sort` 는 안정 정렬(ES2019)이라 동점이면
 * 입력 순서가 보존되고, 입력은 서버의 `started_at` 내림차순이라 그 자체가 고정이다.
 * ③ 을 두는 이유는 그 순서가 **서버의 정렬에 딸려 가지 않게** 하려는 것이다: 서버가 정렬을
 * 바꾸거나 판을 다시 만들면 화면 순서도 같이 바뀌는데, 그때 "만든 순서" 는 사람에게 아무 뜻이 없다
 * (T291 의 18종은 38초 안에 전부 만들어졌다). 알파벳은 최소한 찾을 수 있는 순서다.
 *
 * 🔴 **실제로 자리가 바뀌는 것은 미실현이다.** 가격을 따라 10초마다 달라지므로 보유 판은
 * 미실현 양수 · 음수 묶음을 오갈 수 있다 — 그것은 요구한 정렬의 결과이지 결함이 아니다.
 */
export function orderAlive<T extends Sortable>(
  rows: readonly T[],
  beats: Record<string, Beat>,
): T[] {
  return rows.slice().sort((a, b) => {
    const ga = groupOf(a, beats[a.session_id]);
    const gb = groupOf(b, beats[b.session_id]);
    if (ga !== gb) return ga - gb;
    const live = ga === GROUP.LIVE_UP || ga === GROUP.LIVE_DOWN;
    const diff = live
      ? livePnl(beats[b.session_id]) - livePnl(beats[a.session_id])
      : b.return_pct - a.return_pct;
    if (diff !== 0) return diff;
    return a.symbol.localeCompare(b.symbol);
  });
}
