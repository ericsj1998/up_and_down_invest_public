/**
 * 근거 세미 창 — 도구 결과를 JSON 이 아니라 **카드·표**로 (T257 F2 · 사용자 2026-09-10 "사용자 친화적인 대시보드").
 *
 * 규칙은 단순하다: 최상위 값 중 숫자·문자·불리언은 이름표 카드, 객체 배열은 표(열 = 키 합집합 · 8열까지), 객체는 접는
 * 절, 그 밖의 배열은 칩. 키 이름은 아는 것만 한국어로 바꾸고 모르는 키는 그대로 보인다(조용히 숨기지 않는다).
 * 맨 아래 "원문" 폴드에 JSON 이 그대로 있다.
 */

import { useState } from "react";
import type { ChatEvidence } from "./chat";

export const LABELS: Record<string, string> = {
  symbol: "종목",
  market: "시장",
  name: "이름",
  group: "갈래",
  candidates: "후보",
  confidence: "확신",
  tradable_now: "지금 거래 가능",
  frames: "축",
  last: "현재가",
  change_pct: "등락(%)",
  sma20: "SMA20",
  sma50: "SMA50",
  sma200: "SMA200",
  to_sma200_pct: "SMA200 이격(%)",
  rsi14: "RSI(14)",
  to_high_pct: "고점 대비(%)",
  to_low_pct: "저점 대비(%)",
  bars: "봉 수",
  trend: "추세",
  levels: "레벨",
  plan: "계획",
  note: "메모",
  error: "오류",
  balance: "잔고",
  positions: "포지션",
  position: "포지션",
  stops: "조건부 주문",
  orders: "미결 주문",
  runs: "판",
  funds: "펀드",
  total: "총액",
  available: "가용",
  broker: "브로커",
  order_margin: "주문 증거금",
  account_position_margin: "포지션 증거금",
  key: "키",
  playbook: "매매법",
  leverage: "배율",
  alive: "살아 있음",
  label: "이름",
  score: "점수",
  per: "PER",
  pbr: "PBR",
  psr: "PSR",
  fcf_yield: "FCF 수익률",
  debt_to_equity: "부채비율",
  flags: "깃발",
  recommended: "추천",
  entry: "진입",
  stop: "손절",
  first: "1차",
  target: "목표",
  rr: "손익비",
  ok: "통과",
  blocked: "막힘",
  reasons: "근거",
  budget: "예산",
  currency: "통화",
  tier: "성향",
  tier_label: "성향",
  chosen: "선택",
  alternatives: "대안",
  min_unit: "최소 단위",
  value_candidates: "저평가 후보",
  by_group: "갈래별",
  by_tier: "등급별",
  share_pct: "비중(%)",
  margin: "예산",
  ai_margin: "AI 예산",
  ai_share_pct: "AI 비중(%)",
  warnings: "경고",
  totals_by_market: "시장별 총액",
  rows: "행",
  reason_hits: "근거별 적중",
  n: "표본",
  wins: "적중",
  hit_rate: "적중률(%)",
  days: "일수",
  to_high_52w_pct: "52주 고점 대비(%)",
  to_low_52w_pct: "52주 저점 대비(%)",
  years: "기간(년)",
  total_pct: "총수익(%)",
  cagr_pct: "연환산(%)",
  mdd_pct: "MDD(%)",
  calmar: "칼마",
  trades_count: "매매 수",
  liquidations: "청산",
  risk_tier_label: "등급",
  windows: "창",
  query: "질문",
};

export function labelOf(key: string): string {
  return LABELS[key] ?? key;
}

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

function isObject(v: Json): v is { [k: string]: Json } {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** 도구 결과 문자열 → JSON. 깨졌으면 null (원문 폴드만 보인다). */
export function parseEvidence(text: string | undefined): Json | null {
  if (!text) return null;
  try {
    return JSON.parse(text) as Json;
  } catch {
    return null;
  }
}

function scalar(v: Json): string {
  if (v === null) return "—";
  if (typeof v === "boolean") return v ? "예" : "아니오";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

/** 객체 배열 → 열 목록 (키 합집합 · 8열까지). 시험 대상. */
export function columnsOf(rows: Json[]): string[] {
  const seen: string[] = [];
  for (const r of rows) {
    if (!isObject(r)) continue;
    for (const k of Object.keys(r)) if (!seen.includes(k)) seen.push(k);
  }
  return seen.slice(0, 8);
}

function Table({ rows }: { rows: Json[] }) {
  const cols = columnsOf(rows);
  const objects = rows.filter(isObject);
  const tail = rows.filter((r) => !isObject(r));
  if (!cols.length) return <Chips items={rows} />;
  return (
    <div className="table-wrap">
      <table className="text-xs">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{labelOf(c)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {objects.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} className={typeof r[c] === "number" ? "num" : ""}>
                  {isObject(r[c] ?? null) || Array.isArray(r[c]) ? JSON.stringify(r[c]).slice(0, 60) : scalar(r[c] ?? null)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {tail.length ? <p className="faint mt-1">{tail.map(scalar).join(" · ")}</p> : null}
    </div>
  );
}

function Chips({ items }: { items: Json[] }) {
  if (!items.length) return <span className="faint">없음</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((it, i) => (
        <span key={i} className="chip">
          {isObject(it) || Array.isArray(it) ? JSON.stringify(it).slice(0, 80) : scalar(it)}
        </span>
      ))}
    </span>
  );
}

function Section({ name, value, depth }: { name: string; value: Json; depth: number }) {
  const [open, setOpen] = useState(depth < 1);
  if (isObject(value)) {
    const keys = Object.keys(value);
    return (
      <div className="mt-2">
        <button type="button" className="flex w-full items-center gap-1 text-left font-medium" onClick={() => setOpen((was) => !was)}>
          <span className="faint">{open ? "▾" : "▸"}</span> {labelOf(name)} <span className="faint">({keys.length})</span>
        </button>
        {open ? (
          <div className="ml-2 border-l border-blue-gray-100 pl-2 dark:border-gray-800">
            <Body value={value} depth={depth + 1} />
          </div>
        ) : null}
      </div>
    );
  }
  if (Array.isArray(value)) {
    return (
      <div className="mt-2">
        <div className="font-medium">
          {labelOf(name)} <span className="faint">({value.length})</span>
        </div>
        {value.some(isObject) ? <Table rows={value} /> : <Chips items={value} />}
      </div>
    );
  }
  return null;
}

function Body({ value, depth }: { value: { [k: string]: Json }; depth: number }) {
  const entries = Object.entries(value);
  const scalars = entries.filter(([, v]) => !isObject(v) && !Array.isArray(v));
  const rest = entries.filter(([, v]) => isObject(v) || Array.isArray(v));
  return (
    <>
      {scalars.length ? (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          {scalars.map(([k, v]) => (
            <div key={k} className="min-w-0">
              <div className="faint truncate" title={k}>
                {labelOf(k)}
              </div>
              <div className={`break-words ${typeof v === "number" ? "num" : ""} ${k === "error" ? "loss" : ""}`}>{scalar(v)}</div>
            </div>
          ))}
        </div>
      ) : null}
      {rest.map(([k, v]) => (
        <Section key={k} name={k} value={v} depth={depth} />
      ))}
    </>
  );
}

export function EvidenceView({ evidence }: { evidence: ChatEvidence }) {
  const [raw, setRaw] = useState(false);
  const parsed = parseEvidence(evidence.result);
  const args = Object.entries(evidence.arguments ?? {});
  return (
    <div className="space-y-2 text-xs">
      {args.length ? (
        <div>
          <div className="faint">입력</div>
          <div className="flex flex-wrap gap-1">
            {args.map(([k, v]) => (
              <span key={k} className="chip">
                {labelOf(k)}: {typeof v === "string" ? v : JSON.stringify(v)}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div className="faint">입력 없음</div>
      )}
      {!evidence.ok ? (
        <div>
          <div className="loss">실패</div>
          <p className="break-words">{evidence.error}</p>
        </div>
      ) : parsed && isObject(parsed) ? (
        <div>
          <div className="faint">출력</div>
          <Body value={parsed} depth={0} />
        </div>
      ) : (
        <div>
          <div className="faint">출력 (요약)</div>
          <p className="break-words">{evidence.digest || "없음"}</p>
        </div>
      )}
      {evidence.result ? (
        <div>
          <button type="button" className="faint underline" onClick={() => setRaw((was) => !was)}>
            {raw ? "원문 닫기" : "원문 보기"}
          </button>
          {raw ? <pre className="mono mt-1 whitespace-pre-wrap break-all rounded bg-blue-gray-50 p-2 dark:bg-gray-800">{evidence.result}</pre> : null}
        </div>
      ) : null}
    </div>
  );
}
