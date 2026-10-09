/**
 * WhaleSurfer API 클라이언트 (T442 · 2026-10-09 본체 화면에서 분리).
 *
 * 단독 앱 `whalesurfer.api.app` 을 `/api` 로 부른다(dev 는 vite 프록시가 8010 으로 넘긴다).
 * 분석 · 안내만 — 주문 경로가 없다.
 */

const BASE = "/api";
const TIMEOUT_MS = 20_000;

/** 본문까지 담아 던진다 — 상태 코드만 던지면 서버가 적은 이유가 사라진다. */
export async function request<T>(path: string, timeoutMs: number = TIMEOUT_MS): Promise<T> {
  const control = new AbortController();
  const timer = window.setTimeout(() => control.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, { signal: control.signal });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return (await res.json()) as T;
  } catch (exc) {
    if (exc instanceof DOMException && exc.name === "AbortError") {
      throw new Error(`${timeoutMs / 1000}초 안에 응답이 없다 — WhaleSurfer API(8010)를 확인한다`);
    }
    throw exc;
  } finally {
    window.clearTimeout(timer);
  }
}

export type WhaleManager = {
  cik: string;
  label: string;
  person: string;
  image: string | null;
  /** 사진 출처 — 위키미디어 공용은 저작자 · 라이선스 표시가 필수라 화면에 단다. 수동 적재면 "수동 적재". */
  image_credit: string | null;
  image_page: string | null;
  note: string | null;
};

export type WhaleHolding = {
  cusip: string;
  issuer: string;
  title: string;
  value_usd: number;
  shares: number;
  sh_prn: string;
  put_call: string | null;
  /** 보고서 안 비중(0 ~ 1). */
  weight: number;
};

export type WhaleChangeKind = "new" | "added" | "reduced" | "exited" | "held";

export type WhaleChange = {
  cusip: string;
  issuer: string;
  put_call: string | null;
  kind: WhaleChangeKind;
  shares: number;
  prev_shares: number;
  value_usd: number;
  weight: number;
};

export type WhaleReport = {
  accession: string;
  form: string;
  filed: string;
  period: string | null;
  entity: string;
  total_value_usd: number;
  n: number;
  holdings: WhaleHolding[];
  changes: WhaleChange[];
};

export type WhaleManagerView = { manager: WhaleManager; reports: WhaleReport[]; disclaimer: string };

export type WhaleConsensusSlot = {
  cusip: string;
  issuer: string;
  holders: number;
  new: number;
  added: number;
  reduced: number;
  exited: number;
  held: number;
  value_usd: number;
  /** 추적 보고자 중 들고 있는 비율(0 ~ 1). */
  share: number;
};

export type WhaleConsensus = {
  at: string;
  managers: number;
  tracked: number;
  failures: { cik: string; label: string; reason: string }[];
  by_cusip: Record<string, WhaleConsensusSlot>;
};

/** 그 공시 뒤에 따라 샀다면 — T443 사건 실현값(%) · 비용 0.10% 뒤. */
export type WhaleSince = {
  ticker: string;
  entry: number;
  entry_date: string;
  /** 다음 공시일까지(분기) — 아직 다음 공시 전이면 null. */
  ret_q: number | null;
  /** 백테스트를 돌린 날의 마지막 종가까지. */
  ret_now: number | null;
  kind: WhaleChangeKind;
};

export type WhaleHolder = WhaleChange & {
  cik: string;
  label: string;
  person: string;
  period: string | null;
  filed: string;
  since_filing: WhaleSince | null;
};

/** 이 종목을 누군가 사거나 늘린 공시 뒤에 따라 샀다면 — 사건 전부의 분기 수익 요약. */
export type WhaleEstimate = {
  events: number;
  with_q: number;
  mean_q: number | null;
  median_q: number | null;
  win_q: number | null;
};

export type WhaleStock = {
  cusip: string;
  issuer: string | null;
  ticker: string | null;
  ticker_source: string | null;
  consensus: WhaleConsensusSlot | null;
  holders: WhaleHolder[];
  links: { toss: string | null; binance: string | null; gate: string | null };
  links_note: string | null;
  estimate: WhaleEstimate | null;
  estimate_note: string;
  failures: { cik: string; label: string; reason: string }[];
  disclaimer: string;
};

export function whaleManagers(): Promise<{ managers: WhaleManager[]; count: number; disclaimer: string }> {
  return request("/whalesurfer/managers");
}

export function whaleManager(cik: string, n = 4): Promise<WhaleManagerView> {
  // 보고 한 건 = EDGAR 요청 둘 · 큰 기관(르네상스 등)은 정보표가 수 MB 라 넉넉히.
  return request(`/whalesurfer/managers/${encodeURIComponent(cik)}?n=${n}`, 90_000);
}

export function whaleConsensus(): Promise<WhaleConsensus> {
  // 처음엔 보고자 x 2분기 x 요청 2 라 느리다(초당 10 요청 스로틀). 서버가 6시간 기억한다.
  return request("/whalesurfer/consensus", 180_000);
}

export function whaleStock(cusip: string): Promise<WhaleStock> {
  return request(`/whalesurfer/stocks/${encodeURIComponent(cusip)}`, 180_000);
}
