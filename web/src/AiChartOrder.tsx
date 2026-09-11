/**
 * AI 차트 분석 주문 (T273 · 사용자 2026-09-11 · 이름 확정) — 종목 · 갈래(단기/스윙/장투) → 갈래의 진입 축 차트 + 구조(전고/전저 ·
 * 지지/저항 · 오더블록 · 추세) + 재무(주식) + VIX + 롱/숏 계획(진입·익절·손절 · 현재가 대비 거리).
 *
 * ⛔ 여기서 주문은 나가지 않는다. "이 계획으로 주문" 은 주식 주문 창(`StockOrder`)에 초안으로 넘긴다 — 사람이 보고
 *    고친 뒤 보내고, 서버가 재인증·RiskManager 로 다시 확정한다. AI 참가자 비교(2단계) · 채점(3단계)은 다음 조각.
 *
 * 숫자는 전부 서버가 준 문자열이다 — 화면은 계산하지 않는다(규칙 #2 정신 · 대시보드와 같은 원칙).
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  analysisFrame,
  chartOrderAnalyzeJob,
  chartOrderBuckets,
  chartOrderResolve,
  chartOrderRun,
  chartOrderRuns,
  chartOrderScoreboard,
  consoleBalances,
  exchangeMarkets,
  orderCustom,
  symbols as coinSymbols,
  validatePlan,
  valueScreen,
} from "./api";
import type {
  AnalysisFrame,
  ChartAnalysis,
  ChartBucket,
  ChartParticipant,
  ChartPlanSide,
  ValueScreenView,
  ChartRun,
  Choice,
  MarketInfo,
  ScoreRow,
  Who,
} from "./api";
import { Chart } from "./Chart";
import { Live } from "./Live";
import { useJobEvents } from "./chat/useJobEvents";
import { BrokerMark } from "./shell/BrokerMark";
import {
  brokerOfName,
  pickMarkets,
  useMarketGroup,
  type MarketGroup,
} from "./shell/marketGroup";
import { requestStockOrder, stashStockOrder } from "./StockOrder";
import { ErrorCard, useFold } from "./ui";

/** 마지막 선택 — 묶음(코인/주식)마다 따로 기억한다. 주식에서 고른 것이 코인 화면에 뜨면 안 된다. */
function slotFor(group: MarketGroup): string {
  return `ai-chart-order:last:${group}`;
}

function remembered(group: MarketGroup): {
  symbol: string;
  market: string;
  bucket: string;
} | null {
  try {
    const raw = localStorage.getItem(slotFor(group));
    return raw
      ? (JSON.parse(raw) as { symbol: string; market: string; bucket: string })
      : null;
  } catch {
    return null;
  }
}

/**
 * 기본 종목: 코인은 BTC, 주식은 **AAPL·NASDAQ** (사용자 2026-09-11 "시작 종목 그냥 애플로" — 전엔 SPY·NYSE 였는데
 * SPY 는 재무가 없어 저평가 후보 목록에도 안 떠 고르개가 비어 보였다). 시장은 NASDAQ 이 있으면 NASDAQ, 아니면 첫 시장.
 */
function defaultsFor(
  group: MarketGroup,
  markets: MarketInfo[],
): { symbol: string; market: string } {
  if (group === "stock") {
    const nasdaq = markets.find((m) => m.name === "NASDAQ");
    return {
      symbol: "AAPL",
      market: nasdaq?.name ?? markets[0]?.name ?? "NASDAQ",
    };
  }
  return { symbol: "BTC_USDT", market: markets[0]?.name ?? "GATE" };
}

/** 점수(0~100) — 소수 한 자리. 서버 값 그대로 두면 `94.16666666666667` 처럼 나온다 (사용자 2026-09-11). */
function scoreText(raw: number | null | undefined): string {
  if (raw === null || raw === undefined || !Number.isFinite(raw)) return "—";
  return raw.toFixed(1);
}

function pctText(raw: string | undefined): string {
  if (raw === undefined) return "—";
  const n = Number(raw);
  if (!Number.isFinite(n)) return raw;
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

/** 서버 문자열을 그대로 두되 소수 꼬리만 자른다(최대 `max` 자리 · 끝 0 제거) — 값을 다시 계산하지 않는다. */
function numText(raw: string | null | undefined, max = 6): string {
  if (raw === null || raw === undefined || raw === "") return "—";
  const m = /^(-?\d+)(?:\.(\d+))?$/.exec(raw);
  if (!m) return raw;
  const whole = m[1] ?? raw;
  const frac = (m[2] ?? "").slice(0, max).replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : whole;
}

function PlanCard({
  plan,
  label,
  onOrder,
  dec = 6,
  reason,
}: {
  plan: ChartPlanSide | null;
  label: string;
  onOrder?: () => void;
  /** 소수 자릿수 — 주식 2 · 코인 6. */
  dec?: number;
  /** 계획이 없을 때 서버가 말한 이유 — 원래 없는 자리인지 오류인지 (2026-09-11). */
  reason?: string | null;
}) {
  if (plan === null) {
    return (
      <div className="card" style={{ flex: 1, minWidth: 260 }}>
        <b>{label} · 후보 없음</b>
        <p className="faint text-xs">
          {reason ??
            "구조에서 후보가 안 나왔다 — 그 방향의 지지/저항이 없거나 이 시장은 그 방향이 없다."}
        </p>
      </div>
    );
  }
  const d = plan.distance;
  return (
    <div className="card" style={{ flex: 1, minWidth: 260 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>
          {label} {plan.ok ? "" : "· 막힘"}
        </b>
        {plan.ok && onOrder ? (
          <button
            type="button"
            className="btn small primary"
            onClick={onOrder}
            title="주식 주문 창에 초안으로 넘긴다 — 보내기 전에 고칠 수 있다"
          >
            이 계획으로 주문
          </button>
        ) : null}
      </div>
      <ul className="text-sm">
        <li>
          진입 {numText(plan.entry, dec)} · 손절 {numText(plan.stop, dec)}
          {plan.stop_moved ? " (RiskManager 가 옮김)" : ""} · 1차{" "}
          {numText(plan.first, dec)} · 목표 {numText(plan.target, dec)}
        </li>
        {d ? (
          <li>
            현재가 대비 진입 {pctText(d.to_entry_pct)} · 손절{" "}
            {pctText(d.to_stop_pct)} · 목표 {pctText(d.to_target_pct)} · 리스크{" "}
            {d.risk_pct}% · 보상 {d.reward_pct}%
          </li>
        ) : null}
        <li className="faint text-xs">
          1차 손익비 {numText(plan.rr, 2)} · 필요 승률{" "}
          {plan.need_pct ? `${Number(plan.need_pct).toFixed(1)}%` : "—"} ·
          손절폭 {plan.stop_pct ? `${Number(plan.stop_pct).toFixed(2)}%` : "—"}
        </li>
        <li className="faint text-xs">근거: {plan.basis}</li>
        {plan.blocked.length ? (
          {/* 색은 토큰으로 — 박아 두면 다크에서 어두운 바탕에 어두운 빨강이 된다 (2026-09-12). */}
          <li className="text-xs" style={{ color: "var(--loss)" }}>
            막은 이유: {plan.blocked.join(" · ")}
          </li>
        ) : null}
        {plan.warnings?.length ? (
          <li className="faint text-xs">주의: {plan.warnings.join(" · ")}</li>
        ) : null}
      </ul>
    </div>
  );
}

/** 실험 엔진의 판정 값(`follow_through.Outcome`) — 익절 먼저 · 손절 먼저 · 기한 만료 청산(손익 있음) · 미결(봉 부족). */
const OUTCOME_LABEL: Record<string, string> = {
  FOLLOWED: "익절 먼저",
  NOT_FOLLOWED: "손절 먼저",
  EXPIRED: "기한 만료 청산",
  UNRESOLVED: "미결(봉 부족)",
};

function judgementText(j: ChartParticipant["judgement"]): string {
  if (j === null || j === undefined) return "미판정";
  if (!j.entered) return "진입 안 됨";
  const label = j.outcome ? (OUTCOME_LABEL[j.outcome] ?? j.outcome) : "—";
  return `${label} · 순 R ${j.net_r ?? "—"} · 진입까지 ${j.bars_to_entry ?? "—"}봉`;
}

/** 참가자 표 — 우리-구조 · 우리-알고리즘 · 기준선 · AI 단독 · AI+근거. 값은 서버 문자열 그대로. */
function ParticipantsTable({ rows }: { rows: ChartParticipant[] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="table text-xs">
        <thead>
          <tr>
            <th>참가자</th>
            <th>입장</th>
            <th>진입</th>
            <th>손절</th>
            <th>1차</th>
            <th>목표</th>
            <th>확신</th>
            <th>근거·판정</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.participant}>
              <td>
                <b>{r.participant}</b> <span className="faint">{r.kind}</span>
              </td>
              <td>
                {r.stance === "PROPOSED"
                  ? "제안"
                  : r.stance === "ABSTAINED"
                    ? "관망"
                    : "실패"}
              </td>
              <td>{r.entry ?? "—"}</td>
              <td>{r.stop ?? "—"}</td>
              <td>{r.first ?? "—"}</td>
              <td>{r.target ?? "—"}</td>
              <td>{r.conviction ?? "—"}</td>
              <td className="faint">
                {r.detail}
                {r.judgement !== undefined ? (
                  <div>{judgementText(r.judgement)}</div>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * 코인 주문 — 계획을 **그대로** 차트 주문 경로(`live_custom`)로 보낸다. 팝업 없이 행 안 확인 패널:
 * 예산(USDT)·배율을 적고 RiskManager 미리보기(`validatePlan`)를 본 뒤 "주문". 재인증은 서버가 요구한다(401 → 로그인).
 */
function CoinOrderPanel({
  symbol,
  market,
  frame,
  plan,
  onClose,
}: {
  symbol: string;
  market: string;
  frame: string;
  plan: ChartPlanSide;
  onClose: () => void;
}) {
  const [margin, setMargin] = useState(50);
  const [leverage, setLeverage] = useState(1);
  const [available, setAvailable] = useState<number | null>(null);
  const [risk, setRisk] = useState<Awaited<
    ReturnType<typeof validatePlan>
  > | null>(null);
  const [sending, setSending] = useState(false);
  const [placed, setPlaced] = useState<{
    key: string;
    moved: boolean;
    stop: string;
  } | null>(null);
  const [error, setError] = useState("");
  const short = plan.side === "short";
  const draft = {
    entry: Number(plan.entry),
    stop: Number(plan.stop),
    first: Number(plan.first),
    target: Number(plan.target),
  };

  useEffect(() => {
    consoleBalances()
      .then((body) =>
        setAvailable(
          body.balances[market]
            ? Number(body.balances[market].available)
            : null,
        ),
      )
      .catch(() => setAvailable(null));
  }, [market]);

  useEffect(() => {
    let alive = true;
    validatePlan({ ...draft, leverage, market, short })
      .then((got) => alive && setRisk(got))
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leverage, market, short, plan.entry, plan.stop, plan.first, plan.target]);

  const send = () => {
    setSending(true);
    setError("");
    orderCustom({
      symbol,
      market,
      margin,
      leverage,
      short,
      ...draft,
      flags: [],
      timeframe: frame,
      price_frame: "1m",
    })
      .then((got) =>
        setPlaced({
          key: got.session_id,
          moved: got.confirm.moved,
          stop: got.confirm.stop,
        }),
      )
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setSending(false));
  };

  const blocked = risk && !risk.ok ? risk.reasons : [];
  const over = available !== null && margin > available;
  return (
    <div className="card" style={{ marginTop: 8, borderColor: "#0f7b6c" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>
          {short ? "숏" : "롱"} 주문 — {symbol} · {market} · 판정 축 {frame}
        </b>
        <button type="button" className="btn small" onClick={onClose}>
          닫기
        </button>
      </div>
      <p className="faint text-xs">
        진입 {plan.entry} · 손절 {risk ? risk.stop : plan.stop}
        {risk?.moved ? " (RiskManager 가 옮김)" : ""} · 1차 {plan.first} · 목표{" "}
        {plan.target}. 판(RUN)이 하나 생기고 러너가 진입 대기 → 체결 →
        손절/익절을 맡는다.
      </p>
      <div
        className="row"
        style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}
      >
        <label className="field">
          <span className="faint text-xs">
            예산 (USDT)
            {available !== null ? ` · 가용 ${available.toFixed(2)}` : ""}
          </span>
          <input
            type="number"
            min={1}
            value={margin}
            onChange={(e) => setMargin(Number(e.target.value) || 0)}
            style={{ width: 110 }}
          />
        </label>
        <label className="field">
          <span className="faint text-xs">배율</span>
          <input
            type="number"
            min={1}
            max={20}
            value={leverage}
            onChange={(e) => setLeverage(Number(e.target.value) || 1)}
            style={{ width: 70 }}
          />
        </label>
        <button
          type="button"
          className="btn small primary"
          disabled={
            sending || Boolean(placed) || !risk?.ok || over || margin <= 0
          }
          onClick={send}
          title={over ? "가용 잔고를 넘는다" : undefined}
        >
          {sending ? "보내는 중…" : placed ? "보냈다" : "주문"}
        </button>
      </div>
      {risk ? (
        <p className="faint text-xs">
          손익비 {risk.rr} · 필요 승률 {Number(risk.need_pct).toFixed(1)}% ·
          손절폭 {Number(risk.stop_pct).toFixed(2)}%
          {risk.ok && risk.reasons.length
            ? ` · 주의: ${risk.reasons.join(" · ")}`
            : ""}
        </p>
      ) : null}
      {blocked.length ? (
        <ErrorCard message={`막힘: ${blocked.join(" · ")}`} />
      ) : null}
      {over ? (
        <p className="text-xs" style={{ color: "var(--loss)" }}>
          예산이 가용 잔고보다 크다.
        </p>
      ) : null}
      {error ? <ErrorCard message={error} /> : null}
      {placed ? (
        <p className="text-xs">
          판 <b>{placed.key}</b> 가 떴다
          {placed.moved ? ` · 손절은 ${placed.stop} 로 확정됐다` : ""} — 콘솔의
          판 목록에서 본다.
        </p>
      ) : null}
    </div>
  );
}

export function AiChartOrder({
  markets,
  who,
  page = false,
  group: groupProp,
}: {
  markets: MarketInfo[];
  who: Who | null;
  page?: boolean;
  /** 사이드바 토글의 묶음 — 화면 모드에서 넘긴다. 없으면 첫 시장의 묶음. */
  group?: MarketGroup;
}) {
  const [open, toggle] = useFold("ai-chart-order", false);
  const navigate = useNavigate();
  // ⭐ 묶음(코인/주식)은 사이드바의 토글을 따른다 — 콘솔과 같은 쿠키. 기억한 것이 없으면 기본 종목(BTC · SPY).
  const groupOf = (name: string): MarketGroup =>
    markets.find((m) => m.name === name)?.group ?? "coin";
  const group: MarketGroup = groupProp ?? groupOf(markets[0]?.name ?? "");
  const last = remembered(group);
  const fallback = defaultsFor(group, markets);
  const [market, setMarket] = useState(
    last?.market && markets.some((m) => m.name === last.market)
      ? last.market
      : fallback.market,
  );
  const [symbol, setSymbol] = useState(last?.symbol ?? fallback.symbol);
  const [bucket, setBucket] = useState(last?.bucket ?? "swing");
  // 종목 고르개 — 코인은 띄울 수 있는 목록(`/exchange/symbols`), 주식은 저평가 유니버스의 이름표(datalist).
  const [choices, setChoices] = useState<Choice[]>([]);
  const [names, setNames] = useState<{ symbol: string; name: string }[]>([]);
  // ⭐ 저평가 후보(점수순)를 이 화면에도 띄운다 (사용자 2026-09-11 "저평가 후보가 저쪽에도 떠야 — 필수") — 클릭하면
  //    그 종목으로 바로 분석. 서버 쪽 크기 상한(50)만큼 받고 처음엔 15개, "더" 로 전부.
  const [cands, setCands] = useState<ValueScreenView["rows"]>([]);
  const [moreCands, setMoreCands] = useState(false);
  const [buckets, setBuckets] = useState<ChartBucket[]>([]);
  const [body, setBody] = useState<ChartAnalysis | null>(null);
  // ⭐ 라이브(최신 봉 추종)는 **기본 꺼짐** (사용자 2026-09-11 "우측 고정이어서 불편 — 라이브를 눌렀을 때만").
  //    분석 화면은 과거 구조를 훑는 자리라 콘솔과 반대다. 켜면 콘솔과 같은 규칙(오른쪽 끝 고정).
  const [liveOn, setLiveOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const autoRun = useRef<number | null>(null);
  const [error, setError] = useState("");
  const info = markets.find((m) => m.name === market);
  const stock = info?.group === "stock";
  // 카드 숫자의 소수 자릿수 — 주식은 센트(2) · 코인은 6 (사용자 2026-09-11 "소수점이 너무 길다").
  const dec = stock ? 2 : 6;
  // 2단계 — AI 비교 작업 · 3단계 — 이력·채점
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJobEvents(jobId);
  // ⭐ 구조 읽기도 작업이다 — 단계마다 진행 줄이 온다 (사용자 2026-09-11 "무슨 작업을 하는지 다 띄워").
  const [analyzeId, setAnalyzeId] = useState<string | null>(null);
  const analyzeJob = useJobEvents(analyzeId);
  const [showLog, setShowLog] = useState(false);
  const [participants, setParticipants] = useState<ChartParticipant[] | null>(
    null,
  );
  const [runId, setRunId] = useState<string | null>(null);
  const [history, setHistory] = useState<ChartRun[] | null>(null);
  const [resolveId, setResolveId] = useState<string | null>(null);
  const resolveJob = useJobEvents(resolveId);
  const guest = Boolean(who?.guest);

  useEffect(() => {
    if (!job.done) return;
    setJobId(null);
    if (job.error) {
      setError(job.error);
      return;
    }
    const got = job.result as {
      analysis?: ChartAnalysis;
      participants?: ChartParticipant[];
      run_id?: string;
    } | null;
    if (got?.analysis) setBody(got.analysis);
    setParticipants(got?.participants ?? []);
    setRunId(got?.run_id ?? null);
  }, [job.done, job.error, job.result]);

  useEffect(() => {
    if (!analyzeJob.done) return;
    setAnalyzeId(null);
    setBusy(false);
    if (analyzeJob.error) {
      setError(analyzeJob.error);
      return;
    }
    const got = analyzeJob.result as ChartAnalysis | null;
    if (!got) return;
    setBody(got);
    try {
      localStorage.setItem(
        slotFor(group),
        JSON.stringify({ symbol: got.symbol, market: got.market, bucket }),
      );
    } catch {
      // 기억만 못 한다.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analyzeJob.done, analyzeJob.error, analyzeJob.result]);

  const loadHistory = (sym: string, mkt: string) => {
    chartOrderRuns({ symbol: sym, market: mkt, limit: 10 })
      .then((got) => setHistory(got.runs))
      .catch((exc: unknown) => setError(String(exc)));
  };

  useEffect(() => {
    if (!resolveJob.done) return;
    setResolveId(null);
    if (resolveJob.error) setError(resolveJob.error);
    if (body) loadHistory(body.symbol, body.market);
  }, [resolveJob.done, resolveJob.error, body]);

  const [reusedNote, setReusedNote] = useState("");
  const [scores, setScores] = useState<{
    rows: ScoreRow[];
    min_sample: number;
    cycles: number;
    judged: number;
  } | null>(null);

  const compare = (side?: string) => {
    if (!symbol.trim() || jobId) return;
    setError("");
    setParticipants(null);
    setReusedNote("");
    chartOrderRun({ symbol: symbol.trim().toUpperCase(), market, bucket, side })
      .then((got) => {
        if (got.job_id) {
          setJobId(got.job_id);
          return;
        }
        // 10분 안의 지난 회차 — 모델을 안 불렀다. 참가자 표만 그대로.
        setParticipants(got.participants ?? []);
        setRunId(got.run_id ?? null);
        setReusedNote(got.note ?? "");
      })
      .catch((exc: unknown) => setError(String(exc)));
  };

  const loadScores = () => {
    chartOrderScoreboard({ market })
      .then(setScores)
      .catch((exc: unknown) => setError(String(exc)));
  };

  const resolveNow = () => {
    if (resolveId) return;
    chartOrderResolve()
      .then((got) => setResolveId(got.job_id))
      .catch((exc: unknown) => setError(String(exc)));
  };

  useEffect(() => {
    if ((!open && !page) || buckets.length) return;
    chartOrderBuckets()
      .then((got) => setBuckets(got.buckets))
      .catch((exc: unknown) => setError(String(exc)));
  }, [open, page, buckets.length]);

  // 종목 목록 — 코인은 한 번, 주식은 시장마다. 실패해도 손으로 칠 수 있으니 조용히 넘긴다.
  useEffect(() => {
    if (!open && !page) return;
    if (!stock) {
      coinSymbols()
        .then((got) => setChoices(got.rows))
        .catch(() => setChoices([]));
      return;
    }
    let alive = true;
    valueScreen(market, { size: 50, sort: "score", has_facts: true })
      .then((got) => {
        if (!alive) return;
        setCands(got.rows);
        setNames(
          got.rows.map((r) => ({ symbol: r.symbol, name: r.name ?? "" })),
        );
      })
      .catch(() => {
        if (!alive) return;
        setCands([]);
        setNames([]);
      });
    return () => {
      alive = false;
    };
  }, [open, page, stock, market]);

  // ⭐ "차트 보기" 와 "분석" 은 **별개** (사용자 2026-09-11). 차트 보기는 봉과 겹칩선만 — DB 에 있으면 1초 —
  //    종목을 고르는 순간 이것이 뜬다. 분석(구조·지지/저항·계획·재무·VIX)은 단추를 눌러야 돈다.
  const [chart, setChart] = useState<AnalysisFrame | null>(null);
  const [chartBusy, setChartBusy] = useState(false);
  const [chartOf, setChartOf] = useState<{
    symbol: string;
    market: string;
  } | null>(null);
  const view = (pick?: { symbol: string; market: string }) => {
    const chosen = (pick?.symbol ?? symbol).trim().toUpperCase();
    const where = pick?.market ?? market;
    if (!chosen) return;
    if (pick) {
      setSymbol(pick.symbol);
      setMarket(pick.market);
    }
    const entry = buckets.find((b) => b.key === bucket)?.entry ?? "1h";
    setChartBusy(true);
    setError("");
    // 다른 종목의 옛 분석은 내린다 — 지금 보는 봉과 다른 계획이 화면에 남으면 거짓말이다.
    if (body && (body.symbol !== chosen || body.market !== where))
      setBody(null);
    analysisFrame({
      symbol: chosen,
      market: where,
      flags: ["trend.structure"],
      timeframe: entry,
    })
      .then((got) => {
        setChart(got);
        setChartOf({ symbol: chosen, market: where });
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setChartBusy(false));
  };

  // `pick` 은 후보 칩에서 바로 분석할 때 — 상태 갱신을 기다리지 않고 그 종목·시장으로 간다.
  const run = (pick?: { symbol: string; market: string }) => {
    const chosen = (pick?.symbol ?? symbol).trim().toUpperCase();
    const where = pick?.market ?? market;
    if (!chosen) return;
    if (pick) {
      setSymbol(pick.symbol);
      setMarket(pick.market);
    }
    setBusy(true);
    setError("");
    chartOrderAnalyzeJob({ symbol: chosen, market: where, bucket })
      .then((got) => setAnalyzeId(got.job_id))
      .catch((exc: unknown) => {
        setError(String(exc));
        setBusy(false);
      });
  };

  // ⭐ 화면으로 들어오면 기본 종목(또는 마지막 종목)의 차트가 **바로** 떠 있어야 한다 (사용자 2026-09-11) — 첫 렌더에 한 번.
  useEffect(() => {
    if (page && buckets.length) view();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, buckets.length]);

  const order = (plan: ChartPlanSide) => {
    if (!body || !plan.entry || !plan.stop || !plan.first || !plan.target)
      return;
    const req = {
      symbol: body.symbol,
      market: body.market,
      frame: body.bucket.entry,
      plan: {
        long: plan.side === "long",
        entry: Number(plan.entry),
        stop: Number(plan.stop),
        first: Number(plan.first),
        target: Number(plan.target),
      },
    };
    if (page) {
      // 제 화면에서는 주식 주문 창이 안 떠 있다 — 두고 콘솔로 간다. 콘솔의 주문 창이 집어 간다.
      stashStockOrder(req);
      navigate("/console");
      return;
    }
    requestStockOrder(req.symbol, req.market, {
      frame: req.frame,
      plan: req.plan,
    });
  };

  const s = body?.structure;
  const [shown, setShown] = useState<"long" | "short">("long");
  const [coinOrder, setCoinOrder] = useState<ChartPlanSide | null>(null);
  const chosen = body
    ? shown === "long"
      ? (body.plans.long ?? body.plans.short)
      : (body.plans.short ?? body.plans.long)
    : null;
  const chartPlan =
    chosen && chosen.ok
      ? {
          entry: chosen.entry,
          stop: chosen.stop,
          first: chosen.first,
          target: chosen.target,
        }
      : null;
  // ⭐ 계산에 쓴 근거를 차트에 전부 얹는다 — 손절은 붉은 박스 · 익절은 초록 박스(진입~손절 / 진입~목표) ·
  //    아래 첫 지지·위 첫 저항 띠 · 전고/전저 점. 숫자는 서버 것 그대로.
  const zones: { low: number; high: number; kind: string }[] = [];
  if (chosen && chosen.ok && chosen.entry && chosen.stop && chosen.target) {
    const e = Number(chosen.entry);
    const st = Number(chosen.stop);
    const tg = Number(chosen.target);
    zones.push({
      low: Math.min(e, st),
      high: Math.max(e, st),
      kind: "resistance",
    }); // 붉은 = 손절 구간
    zones.push({
      low: Math.min(e, tg),
      high: Math.max(e, tg),
      kind: "support",
    }); // 초록 = 익절 구간
  }
  if (s?.nearest_support)
    zones.push({
      low: Number(s.nearest_support.low),
      high: Number(s.nearest_support.high),
      kind: "support",
    });
  if (s?.nearest_resistance)
    zones.push({
      low: Number(s.nearest_resistance.low),
      high: Number(s.nearest_resistance.high),
      kind: "resistance",
    });
  const marks: {
    at: string;
    label: string;
    tone: "entry" | "gain" | "loss";
  }[] = [];
  if (s?.swings.swing_high)
    marks.push({
      at: s.swings.swing_high.ts,
      label: `전고 ${s.swings.swing_high.price}`,
      tone: "loss",
    });
  if (s?.swings.swing_low)
    marks.push({
      at: s.swings.swing_low.ts,
      label: `전저 ${s.swings.swing_low.price}`,
      tone: "gain",
    });
  // ⭐ 전고/전저는 **선**으로도 (사용자 2026-09-11 "트레이딩뷰에서 선으로 그려져야지") — 점은 자리, 선은 값.
  const lines: {
    price: number;
    label: string;
    tone: "entry" | "gain" | "loss";
  }[] = [];
  if (s?.swings.swing_high)
    lines.push({
      price: Number(s.swings.swing_high.price),
      label: `전고 ${numText(String(s.swings.swing_high.price), dec)}`,
      tone: "loss",
    });
  if (s?.swings.swing_low)
    lines.push({
      price: Number(s.swings.swing_low.price),
      label: `전저 ${numText(String(s.swings.swing_low.price), dec)}`,
      tone: "gain",
    });
  // `/fundamentals/snapshot` 은 점수를 `score: {score, cheapness, flags}` 로 감싼다 — 그 안을 읽는다.
  const valRaw = body?.valuation as
    | {
        score?:
          number | { score?: number; cheapness?: number; flags?: string[] };
      }
    | null
    | undefined;
  const val =
    valRaw && typeof valRaw.score === "object" && valRaw.score !== null
      ? valRaw.score
      : null;
  const ex = body?.extremes as
    | {
        to_high_52w_pct?: number | null;
        to_low_52w_pct?: number | null;
        to_sma200_pct?: number | null;
        rsi14?: number | null;
      }
    | undefined;

  return (
    <section className={page ? "" : "fold"}>
      {page ? (
        <h1>AI 차트 분석 주문</h1>
      ) : (
        <button
          type="button"
          className="fold-head"
          onClick={toggle}
          aria-expanded={open}
        >
          <span className="fold-mark">{open ? "▾" : "▸"}</span>
          <span className="card-name">AI 차트 분석 주문</span>
          {open ? null : (
            <span className="faint">
              {body
                ? `${body.symbol} · ${body.bucket.label}`
                : "종목 · 갈래 → 구조 · 롱/숏 계획"}
            </span>
          )}
        </button>
      )}
      {open || page ? (
        <div>
          <p className="card-hint">
            종목과 갈래(단기 / 스윙 / 장투)를 고르면 <b>그 갈래의 축</b>으로
            구조를 읽고 롱/숏 계획을 낸다. 손절·익절은 RiskManager 가 확정한
            값이고, 주문은 <b>사람이</b> 주식 주문 창에서 보고 보낸다. 예측은
            없다.
          </p>
          <div
            className="row"
            style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}
          >
            <select
              value={market}
              onChange={(e) => setMarket(e.target.value)}
              title="시장"
            >
              {markets.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
            <BrokerMark broker={brokerOfName(markets, market)} />
            {!stock && choices.length ? (
              <select
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                title="종목 — 띄울 수 있는 코인 목록"
              >
                {choices.some((c) => c.symbol === symbol) ? null : (
                  <option value={symbol}>{symbol}</option>
                )}
                {choices.map((c) => (
                  <option key={c.symbol} value={c.symbol}>
                    {c.label}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <input
                  value={symbol}
                  list={stock ? "ai-chart-order-names" : undefined}
                  onChange={(e) => {
                    const typed = e.target.value;
                    setSymbol(typed);
                    // ⭐ 목록에서 고르면(또는 코드를 다 치면) **바로 읽는다** — 고르고 나서 단추를 또 눌러야
                    //    하는 것이 "로딩이 안 된다" 로 보였다(사용자 2026-09-11). 반 초 뒤 그때의 값이
                    //    아는 종목이면 실행 — 치는 중간(GOOG→GOOGL)에 두 번 읽지 않게.
                    const code = typed.trim().toUpperCase();
                    if (autoRun.current !== null)
                      window.clearTimeout(autoRun.current);
                    if (stock && names.some((n) => n.symbol === code)) {
                      autoRun.current = window.setTimeout(() => {
                        autoRun.current = null;
                        view({ symbol: code, market });
                      }, 500);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") run();
                  }}
                  placeholder={stock ? "SPY" : "BTC_USDT"}
                  style={{ width: 160 }}
                  title={
                    stock
                      ? "종목 코드 — 치면 유니버스 이름표가 뜬다"
                      : "종목 코드 — 코인은 BTC_USDT 꼴"
                  }
                />
                {stock ? (
                  <datalist id="ai-chart-order-names">
                    {names.map((n) => (
                      <option key={n.symbol} value={n.symbol}>
                        {n.name}
                      </option>
                    ))}
                  </datalist>
                ) : null}
              </>
            )}
            <div className="row" style={{ gap: 4 }}>
              {buckets.map((b) => (
                <button
                  key={b.key}
                  type="button"
                  className={`chip${bucket === b.key ? " gain" : ""}`}
                  onClick={() => setBucket(b.key)}
                  title={`진입 ${b.entry} · 맥락 ${b.context.join("/")} · 진입 유효 ${b.valid_bars}봉 · 목표 ${b.rr}R`}
                >
                  {b.label} <span className="faint">{b.entry}</span>
                </button>
              ))}
            </div>
            <button
              type="button"
              className="btn small primary"
              disabled={chartBusy || !symbol.trim()}
              onClick={() => view()}
              title="봉과 겹칩선만 — 종목을 고르면 자동으로 뜬다"
            >
              {chartBusy ? "차트 읽는 중…" : "차트 보기"}
            </button>
            <button
              type="button"
              className="btn small"
              disabled={busy || !symbol.trim()}
              onClick={() => run()}
              title="구조(지지/저항·전고/전저) · 재무 · VIX · 롱/숏 계획 — 규칙 엔진 · AI 아님"
            >
              {busy ? "분석 중…" : "분석"}
            </button>
            <button
              type="button"
              className="btn small"
              disabled={Boolean(jobId) || !symbol.trim() || guest}
              onClick={() => compare()}
              title={
                guest
                  ? "게스트는 AI 비교를 돌릴 수 없다(토큰)"
                  : "같은 스냅샷을 AI 단독 · AI+우리 근거 · 우리-구조 셋으로 기록한다 — 모델 호출 2회"
              }
            >
              {jobId ? "AI 비교 중…" : "AI 비교"}
            </button>
            <button
              type="button"
              className="btn small"
              disabled={!symbol.trim()}
              onClick={() => loadHistory(symbol.trim().toUpperCase(), market)}
              title="이 종목의 지난 AI 비교와 판정"
            >
              이력
            </button>
            <button
              type="button"
              className="btn small"
              onClick={loadScores}
              title="참가자 x 갈래 x 시장 — 판정된 회차만 · 표본 30 미만은 회색"
            >
              성적표
            </button>
          </div>
          {stock && cands.length ? (
            <div
              className="row"
              style={{
                gap: 4,
                flexWrap: "wrap",
                marginTop: 6,
                alignItems: "center",
              }}
            >
              <span
                className="faint text-xs"
                title="저평가 후보 화면과 같은 점수 · 이력 있는 종목만 · 누르면 그 종목으로 분석"
              >
                저평가 후보 {market} · 점수순
              </span>
              {(moreCands ? cands : cands.slice(0, 15)).map((r) => (
                <button
                  key={`${r.market ?? market}:${r.symbol}`}
                  type="button"
                  className={`chip${r.symbol === symbol.trim().toUpperCase() ? " gain" : ""}`}
                  disabled={busy}
                  title={`${r.name ?? ""} · 점수 ${scoreText(r.score)} · 싼 정도 ${scoreText(r.cheapness)} · ${numText(r.price, dec)}${r.flags?.length ? ` · 깃발 ${r.flags.join(",")}` : ""}`}
                  onClick={() =>
                    view({ symbol: r.symbol, market: r.market ?? market })
                  }
                >
                  {r.symbol} <span className="faint">{scoreText(r.score)}</span>
                </button>
              ))}
              {cands.length > 15 ? (
                <button
                  type="button"
                  className="btn small"
                  onClick={() => setMoreCands((was) => !was)}
                >
                  {moreCands ? "접기" : `더 (${cands.length})`}
                </button>
              ) : null}
            </div>
          ) : null}
          {reusedNote ? <p className="faint text-xs">{reusedNote}</p> : null}
          {jobId ? (
            <p className="faint text-xs" style={{ marginTop: 6 }}>
              {job.lines.length ? (
                <ul
                  style={{
                    fontFamily: "monospace",
                    margin: 0,
                    paddingLeft: 16,
                  }}
                >
                  {job.lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              ) : (
                "AI 비교를 띄우는 중"
              )}
            </p>
          ) : null}
          {analyzeJob.lines.length > 0 && (busy || showLog) ? (
            <ul
              className="faint text-xs"
              style={{
                fontFamily: "monospace",
                margin: "4px 0 0",
                paddingLeft: 16,
              }}
            >
              {analyzeJob.lines.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          ) : null}
          {!busy && analyzeJob.lines.length > 0 ? (
            <button
              type="button"
              className="btn small"
              onClick={() => setShowLog((was) => !was)}
              title="방금 분석이 단계마다 무엇을 얼마나 했는지"
            >
              {showLog
                ? "진행 기록 접기"
                : `진행 기록 (${analyzeJob.lines.length})`}
            </button>
          ) : null}
          {error ? <ErrorCard message={error} /> : null}
          {!body && chart && chartOf ? (
            <>
              <div className="row" style={{ gap: 6, alignItems: "center" }}>
                <span className="faint text-xs">
                  {chartOf.symbol} · {chartOf.market} · {chart.timeframe} 봉{" "}
                  {chart.candles.length}개 — 구조·지지/저항·계획은 <b>분석</b>{" "}
                  을 누르면 이 차트 위에 얹힌다.
                </span>
                <Live on={liveOn} onToggle={() => setLiveOn((was) => !was)} />
              </div>
              <Chart frame={chart} follow={liveOn} />
            </>
          ) : null}
          {body && s ? (
            <>
              <p className="faint text-xs" style={{ marginTop: 6 }}>
                {body.symbol} · {body.market} · {body.bucket.label}(진입{" "}
                {body.bucket.entry} · 맥락 {body.bucket.context.join("/")}) ·{" "}
                {body.note}
              </p>
              <div className="row" style={{ gap: 6, alignItems: "center" }}>
                <span className="faint text-xs">차트에 그릴 계획</span>
                <button
                  type="button"
                  className={`chip${shown === "long" ? " gain" : ""}`}
                  disabled={!body.plans.long}
                  onClick={() => setShown("long")}
                >
                  롱
                </button>
                <button
                  type="button"
                  className={`chip${shown === "short" ? " gain" : ""}`}
                  disabled={!body.plans.short}
                  onClick={() => setShown("short")}
                >
                  숏
                </button>
                <span className="faint text-xs">
                  붉은 박스 = 진입~손절 · 초록 박스 = 진입~목표 · 띠 = 첫
                  지지/저항 · 점 = 전고/전저 · 선·구간 = 켜진 규칙의
                  레벨·오더블록·추세선
                </span>
                <Live on={liveOn} onToggle={() => setLiveOn((was) => !was)} />
              </div>
              <Chart
                frame={body.frame}
                plan={chartPlan}
                zones={zones}
                marks={marks}
                lines={lines}
                follow={liveOn}
              />
              {body.evidence?.length ? (
                <div className="card" style={{ marginTop: 8 }}>
                  <b>사용한 근거</b>
                  <ul className="text-xs" style={{ margin: "4px 0 0" }}>
                    {body.evidence.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div
                className="row"
                style={{ flexWrap: "wrap", gap: 8, alignItems: "stretch" }}
              >
                <div className="card" style={{ flex: 1, minWidth: 260 }}>
                  <b>구조 · {body.bucket.entry}</b>
                  <ul className="text-sm">
                    <li>
                      현재가 {numText(s.last, dec)} · ATR{" "}
                      {s.atr ? numText(s.atr, dec) : "—"}
                    </li>
                    <li>
                      전고{" "}
                      {s.swings.swing_high
                        ? `${numText(String(s.swings.swing_high.price), dec)} (${pctText(String(s.swings.swing_high.away_pct ?? ""))})`
                        : "—"}{" "}
                      · 전저{" "}
                      {s.swings.swing_low
                        ? `${numText(String(s.swings.swing_low.price), dec)} (${pctText(String(s.swings.swing_low.away_pct ?? ""))})`
                        : "—"}
                    </li>
                    <li>
                      아래 첫 지지{" "}
                      {s.nearest_support
                        ? `${s.nearest_support.low}~${s.nearest_support.high} (접점 ${s.nearest_support.touches})`
                        : "없음"}{" "}
                      · 위 첫 저항{" "}
                      {s.nearest_resistance
                        ? `${s.nearest_resistance.low}~${s.nearest_resistance.high} (접점 ${s.nearest_resistance.touches})`
                        : "없음"}
                    </li>
                    <li className="faint text-xs">
                      레벨 원본 {s.levels_raw}개 · 규칙 계획선{" "}
                      {s.rule_plan
                        ? `${s.rule_plan.long ? "롱" : "숏"} ${s.rule_plan.entry}/${s.rule_plan.stop}/${s.rule_plan.target}`
                        : "없음(비용을 못 갚는 자리)"}
                    </li>
                    {ex ? (
                      <li className="faint text-xs">
                        52주 고가 대비{" "}
                        {pctText(String(ex.to_high_52w_pct ?? ""))} · 저가 대비{" "}
                        {pctText(String(ex.to_low_52w_pct ?? ""))} · SMA200 이격{" "}
                        {pctText(String(ex.to_sma200_pct ?? ""))} · RSI{" "}
                        {ex.rsi14 ?? "—"}
                      </li>
                    ) : null}
                  </ul>
                </div>
                <div className="card" style={{ flex: 1, minWidth: 260 }}>
                  <b>재무 · 거시</b>
                  <ul className="text-sm">
                    {val ? (
                      <li>
                        저평가 점수 {val.score ?? "—"} · 싼 정도{" "}
                        {val.cheapness ?? "—"}
                        {val.flags?.length
                          ? ` · 깃발 ${val.flags.join(", ")}`
                          : ""}
                      </li>
                    ) : (
                      <li className="faint">
                        {body.valuation_note || "재무 없음"}
                      </li>
                    )}
                    <li>
                      VIX {body.vix?.value ?? "—"}{" "}
                      {body.vix?.band ? `· ${body.vix.band}` : ""}
                      {body.vix?.note ? (
                        <span className="faint text-xs">
                          {" "}
                          — {body.vix.note}
                        </span>
                      ) : null}
                    </li>
                    <li className="faint text-xs">
                      재무·VIX 는 방아쇠가 아니라 맥락이다 — 급락을 예측하지
                      않는다.
                    </li>
                  </ul>
                </div>
              </div>
              <div
                className="row"
                style={{
                  flexWrap: "wrap",
                  gap: 8,
                  alignItems: "stretch",
                  marginTop: 8,
                }}
              >
                <PlanCard
                  plan={body.plans.long}
                  label="롱"
                  dec={dec}
                  reason={body.plan_reasons?.long}
                  onOrder={
                    who && !guest && body.plans.long
                      ? () =>
                          stock
                            ? order(body.plans.long as ChartPlanSide)
                            : setCoinOrder(body.plans.long)
                      : undefined
                  }
                />
                <PlanCard
                  plan={body.plans.short}
                  label="숏"
                  dec={dec}
                  reason={body.plan_reasons?.short}
                  onOrder={
                    who && !guest && body.plans.short
                      ? () =>
                          stock
                            ? order(body.plans.short as ChartPlanSide)
                            : setCoinOrder(body.plans.short)
                      : undefined
                  }
                />
              </div>
              {coinOrder && !stock ? (
                <CoinOrderPanel
                  symbol={body.symbol}
                  market={body.market}
                  frame={body.bucket.entry}
                  plan={coinOrder}
                  onClose={() => setCoinOrder(null)}
                />
              ) : null}
              {participants ? (
                <div className="card" style={{ marginTop: 8 }}>
                  <b>세 참가자 비교</b>
                  <p className="faint text-xs">
                    같은 봉을 보고 낸 계획 — AI 계획은 표시·기록·채점 전용이고
                    주문이 되지 않는다. 채점은 익절·손절에 실제로 닿은 봉으로
                    한다
                    {runId ? ` · 회차 ${runId}` : ""}.
                  </p>
                  <ParticipantsTable rows={participants} />
                </div>
              ) : null}
              <p className="faint text-xs">
                분석 id {body.analysis_id} — 기록돼 나중에 익절/손절에 실제로
                닿았는지로 채점한다.
              </p>
            </>
          ) : null}
          {scores ? (
            <div className="card" style={{ marginTop: 8 }}>
              <b>성적표</b>
              <p className="faint text-xs">
                판정된 회차 {scores.judged} / 기록 {scores.cycles} · 익절률 =
                익절 먼저 ÷ (익절+손절+만료) · 표본 {scores.min_sample} 미만은
                회색(믿지 말라는 표시). 제안했지만 진입가에 안 닿은 것은 "진입
                안 됨" 으로 따로 센다.
              </p>
              {scores.rows.length === 0 ? (
                <p className="faint text-xs">
                  아직 판정된 회차가 없다 — 회차가 익으면(진입 유효 봉 + 보유
                  기한) 한 시간마다 자동 채점된다.
                </p>
              ) : (
                <div style={{ overflowX: "auto" }}>
                  <table className="table text-xs">
                    <thead>
                      <tr>
                        <th>참가자</th>
                        <th>갈래</th>
                        <th>시장</th>
                        <th>제안</th>
                        <th>관망</th>
                        <th>진입 안 됨</th>
                        <th>체결</th>
                        <th>익절 먼저</th>
                        <th>손절 먼저</th>
                        <th>만료</th>
                        <th>익절률</th>
                        <th>평균 순 R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scores.rows.map((r) => (
                        <tr
                          key={`${r.participant}|${r.bucket}|${r.market}`}
                          className={r.grey ? "faint" : ""}
                          title={
                            r.grey
                              ? `표본 ${r.entered} < ${scores.min_sample}`
                              : undefined
                          }
                        >
                          <td>
                            <b>{r.participant}</b>
                          </td>
                          <td>{r.bucket}</td>
                          <td>{r.market}</td>
                          <td>{r.proposed}</td>
                          <td>{r.abstained}</td>
                          <td>{r.no_entry}</td>
                          <td>{r.entered}</td>
                          <td>{r.followed}</td>
                          <td>{r.not_followed}</td>
                          <td>{r.expired}</td>
                          <td>
                            {r.follow_pct === null ? "—" : `${r.follow_pct}%`}
                          </td>
                          <td>{r.avg_net_r ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : null}
          {history ? (
            <div className="card" style={{ marginTop: 8 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <b>이력 · 판정</b>
                <button
                  type="button"
                  className="btn small"
                  disabled={Boolean(resolveId) || guest}
                  onClick={resolveNow}
                  title="익은 회차를 채점한다(원장 전체)"
                >
                  {resolveId ? "채점 중…" : "채점 실행"}
                </button>
              </div>
              {history.length === 0 ? (
                <p className="faint text-xs">
                  아직 없다 — "AI 비교" 를 누르면 여기 쌓인다.
                </p>
              ) : null}
              {history.map((h) => (
                <div key={h.run_id} style={{ marginTop: 6 }}>
                  <div className="text-xs">
                    <b>{h.run_id}</b> · 현재가 {h.entry} · 기한 {h.hold_bars}봉
                    ·{" "}
                    {h.judged
                      ? "판정됨"
                      : `판정 가능 ${h.matures_at.slice(0, 16).replace("T", " ")}Z 이후`}
                  </div>
                  <ParticipantsTable rows={h.participants} />
                </div>
              ))}
              {resolveId ? (
                <p className="faint text-xs">
                  {resolveJob.lines[resolveJob.lines.length - 1] ?? "채점 중"}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/**
 * 사이드바 화면 `/chart-order` (사용자 2026-09-11 "좌측 사이드바 · 거래 콘솔과 AI 투자 어시스턴트 사이").
 * 시장 목록은 콘솔과 같은 곳(`/exchange/markets`)에서 받고, 코인·주식 구분 없이 연결된 시장 전부를 고를 수 있다.
 */
export function AiChartOrderPage({ who }: { who: Who | null }) {
  const [all, setAll] = useState<MarketInfo[] | null>(null);
  const [error, setError] = useState("");
  // ⭐ 사이드바의 코인/주식 토글을 따른다 (사용자 2026-09-11 "주식 상태면 주식 · 코인 상태면 코인").
  const [group] = useMarketGroup();
  useEffect(() => {
    let alive = true;
    exchangeMarkets()
      .then(
        (r) =>
          alive &&
          setAll(
            r.all ??
              r.markets.map((name) => ({ name, ready: true, scoped: true })),
          ),
      )
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, []);
  if (error)
    return <ErrorCard title="시장 목록을 받지 못했다" message={error} />;
  if (all === null) return <p className="faint">시장 목록을 받는 중…</p>;
  // 분석은 조회만이라 `ready`(조회 어댑터가 있다)면 된다 — `scoped`(판을 띄울 수 있다)까지는 안 본다.
  //   서버는 NYSE 를 판 범위에 안 넣었지만 토스가 SPY 봉을 주므로 여기선 고를 수 있어야 한다.
  const markets = pickMarkets(
    all.filter((m) => m.ready),
    group,
  );
  if (markets.length === 0)
    return (
      <>
        <h1>AI 차트 분석 주문</h1>
        <p className="notice warn">
          이 API 에 연결된 {group === "stock" ? "주식" : "코인"} 시장이 없다 —
          사이드바에서 묶음을 바꾸거나, 관리자가 시장을 연결하면 여기서 종목을
          고를 수 있다.
        </p>
      </>
    );
  // key=group — 묶음이 바뀌면 상태(종목·시장·결과)를 새로 시작한다.
  return (
    <AiChartOrder key={group} markets={markets} who={who} page group={group} />
  );
}
