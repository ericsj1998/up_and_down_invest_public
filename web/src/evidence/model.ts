/**
 * 근거 화면의 자료형과 순수 계산 (T222).
 *
 * 🔴 숫자는 `/api/evidence/bundle` 에서 온다 (감사 권한이 없으면 손익이 null 로 온다 · 2026-09-06) — `scripts/build/evidence_bundle.py` 가 기록된 산출물을 옮겨 만든 정적
 *    파일이다. 여기서 하는 계산은 **그리기 위한 변형**(묶기 · 배수 변환 · 서식)뿐이고 새 통계를 만들지 않는다.
 */

export interface Coverage {
  symbols?: string[];
  bars?: number;
  bars_4h?: number;
  years_4h_max?: number;
  since?: string | null;
  until?: string | null;
  futures?: number;
  years?: number;
  drifts?: string[];
  seeds?: number[];
  starts_after?: string | null;
  file?: string;
  per_symbol?: Array<Record<string, string | number>>;
}

export interface Dataset {
  id: string;
  kind: "real" | "synthetic";
  market: string;
  label: string;
  timeframe: string;
  coverage: Coverage | null;
  coverage_1d?: Coverage | null;
  where: string;
  used_for: string[];
  method?: string;
  block_rule_canonical?: string;
  block_rule_stress?: string;
  paths_persisted?: boolean;
}

export interface Strategy {
  id: string;
  version: string;
  label: string | null;
  listed: boolean;
  leverage: number | null;
  timeframe: string | null;
  bundle: string[] | null;
  backtest_note: string | null;
  market_groups: string[] | null;
}

export interface LabRow {
  scenario: string;
  mu_pct: number | null;
  seed: number;
  /** 감사 권한이 없으면 수익률 계열은 null 이다 (MDD·청산은 남는다). */
  total_pct: number | null;
  cagr_pct: number | null;
  mdd_pct: number;
  liquidations: number;
  h3y_pct: number | null;
  h2y_pct: number | null;
  h1y_pct: number | null;
  h1m_pct: number | null;
  h1d_pct: number | null;
}

export interface LabSummary {
  n: number;
  total_median_pct: number | null;
  total_worst_pct: number | null;
  total_best_pct: number | null;
  total_p5_pct: number | null;
  total_p95_pct: number | null;
  cvar5_pct: number | null;
  mdd_median_pct: number;
  mdd_worst_pct: number;
  liquidated_runs: number;
  liquidations_total: number;
}

export interface World {
  id: string;
  label: string;
  canonical: boolean;
  header: string[];
  summary: LabSummary;
  rows: LabRow[];
  /** 원문 파일명 — 상세 세트(`/evidence/synthetic` 의 basis)와 같으면 청산 확률·종목별 분해가 붙는다. */
  source?: string;
}

export interface MdTable {
  heading: string;
  columns: string[];
  rows: string[][];
  /** 서버가 행을 가렸다 (감사 권한 없음 — 표 문장에 수익률이 있다). */
  redacted?: boolean;
}

export interface RealResult {
  id: string;
  strategy: string;
  playbook: string;
  venue: string;
  data: string;
  years: number | null;
  total_pct: number | null;
  cagr_pct?: number | null;
  mdd_pct: number | null;
  calmar?: number | null;
  trades?: number | null;
  liquidations?: number | null;
  lam: string | null;
  note: string;
  sources: string[];
}

export interface Bundle {
  /** 서버가 손익을 가렸다 (감사 권한 없음). */
  redacted?: boolean;
  generated_at: string;
  rules: string[];
  datasets: Dataset[];
  strategies: Strategy[];
  synthetic: { worlds: World[] };
  results: {
    real: RealResult[];
    venues: MdTable;
    matrix: MdTable;
    xcheck_d2: MdTable;
    accidents: MdTable;
    source: string;
  };
  live_match: { status: string; needs: string[] };
}

/** 수익률 % → 자본 배수. 로그 축에 올리려면 양수여야 한다 — -100% 는 0 이라 바닥을 아주 작은 값으로 막는다. */
export function multiple(totalPct: number): number {
  return Math.max(1 + totalPct / 100, 0.001);
}

/** 부호·천 단위 구분 붙인 %. 소수 자릿수는 크기에 따라 — 1,000% 넘으면 정수, 아니면 한 자리. */
export function pct(value: number | null | undefined, digits?: number): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const d = digits ?? (Math.abs(value) >= 1000 ? 0 : 1);
  const body = Math.abs(value).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${body}%`;
}

/** MDD 는 늘 낙폭 — 부호 없이 "−xx%" 로 적는다 (병기 규칙). */
export function mdd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `−${Math.abs(value).toFixed(value >= 10 ? 0 : 1)}%`;
}

export interface ScenarioGroup {
  scenario: string;
  mu_pct: number | null;
  rows: LabRow[];
  median_total_pct: number | null;
  min_total_pct: number | null;
  max_total_pct: number | null;
  median_mdd_pct: number;
  liquidated: number;
}

function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  const hi = s[mid] ?? 0;
  const lo = s[mid - 1] ?? hi;
  return s.length % 2 ? hi : (lo + hi) / 2;
}

/** 시나리오(드리프트)별로 씨앗 5개를 묶는다 — 표의 등장 순서를 지킨다 (드리프트 오름차순으로 적혀 있다). */
export function groupByScenario(rows: LabRow[]): ScenarioGroup[] {
  const order: string[] = [];
  const by = new Map<string, LabRow[]>();
  for (const r of rows) {
    if (!by.has(r.scenario)) {
      by.set(r.scenario, []);
      order.push(r.scenario);
    }
    by.get(r.scenario)?.push(r);
  }
  return order.map((scenario) => {
    const group = by.get(scenario) ?? [];
    // 감사 권한이 없으면 total 이 null 로 온다 — 그때 요약도 null (0 으로 꾸미지 않는다).
    const totals = group.map((r) => r.total_pct).filter((v): v is number => v !== null);
    return {
      scenario,
      mu_pct: group[0]?.mu_pct ?? null,
      rows: group,
      median_total_pct: totals.length ? median(totals) : null,
      min_total_pct: totals.length ? Math.min(...totals) : null,
      max_total_pct: totals.length ? Math.max(...totals) : null,
      median_mdd_pct: median(group.map((r) => r.mdd_pct)),
      liquidated: group.filter((r) => r.liquidations > 0).length,
    };
  });
}

/** 세상 목록에서 기준(canonical) 을 먼저 — 없으면 첫 것. */
export function defaultWorld(worlds: World[]): World | null {
  return worlds.find((w) => w.canonical) ?? worlds[0] ?? null;
}

// ── 라이브 vs 45미래 (`GET /evidence/live_match` · T222 2단계) ─────────────────────

export interface LiveFeatures {
  cum_pct: number;
  vol_pct: number;
  mdd_pct: number;
}

export interface LiveMatchRow {
  rank: number;
  scenario: string;
  seed: number;
  mu_pct: number;
  distance: number;
  head: LiveFeatures;
  outcome_total_pct: number;
  outcome_mdd_pct: number;
  outcome_liquidations: number;
}

export interface LiveMatch {
  status: "ok" | "insufficient" | "no_runs";
  bars?: number;
  min_bars: number;
  sufficient?: boolean;
  live?: LiveFeatures;
  nearest?: LiveMatchRow[];
  all?: LiveMatchRow[];
  scale?: LiveFeatures;
  since?: string;
  until?: string;
  symbols?: string[];
  paths_meta?: { futures: number; bars: number; block: string; basis: string; generated: string };
  chart?: { live: number[]; nearest: { label: string; values: number[] }[] };
  caveat: string;
  elapsed_days?: number;
  reason?: string;
}

/** 로그 지수 → % (그리기용). */
export function logToPct(v: number): number {
  return (Math.exp(v) - 1) * 100;
}

/** 날짜 문자열(YYYY-MM-DD) 둘 사이의 년수. 못 읽으면 null. */
export function yearsBetween(since: string | null | undefined, until: string | null | undefined): number | null {
  if (!since || !until) return null;
  const a = Date.parse(since);
  const b = Date.parse(until);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return null;
  return (b - a) / (365.25 * 86_400_000);
}
