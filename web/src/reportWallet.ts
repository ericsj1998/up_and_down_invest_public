/**
 * 리포트 지갑 그래프의 **층 나누기** (사용자 2026-09-27: *"내가 사비로 금액을 추가했을 때는 색을 회색으로 …
 * 내가 벌어서 얻은 수익과 내가 넣은 돈과, 잃은 돈이 그래프에서 한번에 보여야 해."*).
 *
 * 점마다 지갑 잔고(`balance`)와 그때까지 넣은 돈(`principal`)을 받아 세 층으로 쌓는다:
 *
 *   회색 `kept`   = min(잔고, 넣은 돈)       — 넣은 돈 중 남아 있는 몫
 *   빨강 `lost`   = max(넣은 돈 − 잔고, 0)   — 넣은 돈 중 잃은 몫 (회색 위에 얹으면 꼭대기 = 넣은 돈)
 *   초록 `earned` = max(잔고 − 넣은 돈, 0)   — 넣은 돈 위로 번 몫 (회색 위에 얹으면 꼭대기 = 잔고)
 *
 * 한 점에서 빨강 · 초록 중 하나는 늘 0 이다. 입금은 잔고와 넣은 돈을 같이 올리므로 회색만 커진다.
 *
 * ⚠️ 화면(`ReportDashboard.tsx`)이 아니라 여기에 두는 이유: 층 산식은 **규칙**이라 시험이 있어야 한다.
 */

export type WalletRow = { at: string; balance: string; principal: string };

export type WalletLayers = {
  x: number[];
  kept: number[];
  lost: number[];
  earned: number[];
  balance: number[];
  principal: number[];
  /** y 축 바닥 — 회색이 화면을 다 먹지 않게 가장 낮은 회색 꼭대기의 90% 를 10 단위로 내림(0 아래로는 안 간다). */
  floor: number;
};

/** 문자열 금액 → 수. 못 읽으면 null (0 으로 꾸미지 않는다 · 규칙 #8). */
function num(value: string): number | null {
  const parsed = Number(value);
  return value !== "" && Number.isFinite(parsed) ? parsed : null;
}

export function walletLayers(points: readonly WalletRow[]): WalletLayers {
  const out: WalletLayers = {
    x: [],
    kept: [],
    lost: [],
    earned: [],
    balance: [],
    principal: [],
    floor: 0,
  };
  for (const p of points) {
    const bal = num(p.balance);
    const put = num(p.principal);
    const at = Date.parse(p.at);
    if (bal === null || put === null || !Number.isFinite(at)) continue;
    out.x.push(at);
    out.balance.push(bal);
    out.principal.push(put);
    out.kept.push(Math.min(bal, put));
    out.lost.push(Math.max(put - bal, 0));
    out.earned.push(Math.max(bal - put, 0));
  }
  if (out.kept.length) {
    const low = Math.min(...out.kept);
    out.floor = Math.max(0, Math.floor((low * 0.9) / 10) * 10);
  }
  return out;
}

/**
 * 이 기간 손익 = 잔고 변화 − 넣은 돈 변화 (입금 · 출금은 손익이 아니다).
 *
 * 머리 줄의 "지금까지 번 돈"(잔고 − 넣은 돈 전부)은 기간을 바꿔도 같다 — 24시간을 골라도 같은 +40 이 떠서
 * 기간 손익으로 읽혔다(사용자 2026-09-27). 기간 몫은 따로 보여 준다.
 *
 * @returns 점이 없으면 null.
 */
export function windowGain(layers: Pick<WalletLayers, "balance" | "principal">): number | null {
  const n = layers.balance.length;
  if (n === 0 || layers.principal.length !== n) return null;
  const bal = (layers.balance[n - 1] ?? 0) - (layers.balance[0] ?? 0);
  const put = (layers.principal[n - 1] ?? 0) - (layers.principal[0] ?? 0);
  return Math.round((bal - put) * 100) / 100;
}
