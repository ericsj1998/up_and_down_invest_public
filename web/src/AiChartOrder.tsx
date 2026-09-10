/**
 * AI 차트 주문 (T273 1단계 · 사용자 2026-09-11) — 종목 · 갈래(단기/스윙/장투) → 갈래의 진입 축 차트 + 구조(전고/전저 ·
 * 지지/저항 · 오더블록 · 추세) + 재무(주식) + VIX + 롱/숏 계획(진입·익절·손절 · 현재가 대비 거리).
 *
 * ⛔ 여기서 주문은 나가지 않는다. "이 계획으로 주문" 은 주식 주문 창(`StockOrder`)에 초안으로 넘긴다 — 사람이 보고
 *    고친 뒤 보내고, 서버가 재인증·RiskManager 로 다시 확정한다. AI 참가자 비교(2단계) · 채점(3단계)은 다음 조각.
 *
 * 숫자는 전부 서버가 준 문자열이다 — 화면은 계산하지 않는다(규칙 #2 정신 · 대시보드와 같은 원칙).
 */
import { useEffect, useState } from "react";
import { chartOrderAnalyze, chartOrderBuckets } from "./api";
import type { ChartAnalysis, ChartBucket, ChartPlanSide, MarketInfo, Who } from "./api";
import { Chart } from "./Chart";
import { BrokerMark } from "./shell/BrokerMark";
import { brokerOfName } from "./shell/marketGroup";
import { requestStockOrder } from "./StockOrder";
import { ErrorCard, useFold } from "./ui";

const SLOT = "ai-chart-order:last";

function remembered(): { symbol: string; market: string; bucket: string } | null {
  try {
    const raw = localStorage.getItem(SLOT);
    return raw ? (JSON.parse(raw) as { symbol: string; market: string; bucket: string }) : null;
  } catch {
    return null;
  }
}

function pctText(raw: string | undefined): string {
  if (raw === undefined) return "—";
  const n = Number(raw);
  if (!Number.isFinite(n)) return raw;
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function PlanCard({ plan, label, onOrder }: { plan: ChartPlanSide | null; label: string; onOrder?: () => void }) {
  if (plan === null) {
    return (
      <div className="card" style={{ flex: 1, minWidth: 260 }}>
        <b>{label}</b>
        <p className="faint text-xs">구조에서 후보가 안 나왔다 — 그 방향의 지지/저항이 없거나 이 시장은 그 방향이 없다.</p>
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
          <button type="button" className="btn small primary" onClick={onOrder} title="주식 주문 창에 초안으로 넘긴다 — 보내기 전에 고칠 수 있다">
            이 계획으로 주문
          </button>
        ) : null}
      </div>
      <ul className="text-sm">
        <li>
          진입 {plan.entry} · 손절 {plan.stop}
          {plan.stop_moved ? " (RiskManager 가 옮김)" : ""} · 1차 {plan.first} · 목표 {plan.target}
        </li>
        {d ? (
          <li>
            현재가 대비 진입 {pctText(d.to_entry_pct)} · 손절 {pctText(d.to_stop_pct)} · 목표 {pctText(d.to_target_pct)} · 리스크 {d.risk_pct}% ·
            보상 {d.reward_pct}%
          </li>
        ) : null}
        <li className="faint text-xs">
          1차 손익비 {plan.rr ?? "—"} · 필요 승률 {plan.need_pct ? `${Number(plan.need_pct).toFixed(1)}%` : "—"} · 손절폭{" "}
          {plan.stop_pct ? `${Number(plan.stop_pct).toFixed(2)}%` : "—"}
        </li>
        <li className="faint text-xs">근거: {plan.basis}</li>
        {plan.blocked.length ? <li className="text-xs" style={{ color: "#b91c1c" }}>막은 이유: {plan.blocked.join(" · ")}</li> : null}
        {plan.warnings?.length ? <li className="faint text-xs">주의: {plan.warnings.join(" · ")}</li> : null}
      </ul>
    </div>
  );
}

export function AiChartOrder({ markets, who }: { markets: MarketInfo[]; who: Who | null }) {
  const [open, toggle] = useFold("ai-chart-order", false);
  const last = remembered();
  const [market, setMarket] = useState(last?.market ?? markets[0]?.name ?? "NASDAQ");
  const [symbol, setSymbol] = useState(last?.symbol ?? "AAPL");
  const [bucket, setBucket] = useState(last?.bucket ?? "swing");
  const [buckets, setBuckets] = useState<ChartBucket[]>([]);
  const [body, setBody] = useState<ChartAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const info = markets.find((m) => m.name === market);
  const stock = info?.group === "stock";

  useEffect(() => {
    if (!open || buckets.length) return;
    chartOrderBuckets()
      .then((got) => setBuckets(got.buckets))
      .catch((exc: unknown) => setError(String(exc)));
  }, [open, buckets.length]);

  const run = () => {
    if (!symbol.trim()) return;
    setBusy(true);
    setError("");
    chartOrderAnalyze({ symbol: symbol.trim().toUpperCase(), market, bucket })
      .then((got) => {
        setBody(got);
        try {
          localStorage.setItem(SLOT, JSON.stringify({ symbol: got.symbol, market: got.market, bucket }));
        } catch {
          // 기억만 못 한다.
        }
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };

  const order = (plan: ChartPlanSide) => {
    if (!body || !plan.entry || !plan.stop || !plan.first || !plan.target) return;
    requestStockOrder(body.symbol, body.market, {
      frame: body.bucket.entry,
      plan: { long: plan.side === "long", entry: Number(plan.entry), stop: Number(plan.stop), first: Number(plan.first), target: Number(plan.target) },
    });
  };

  const s = body?.structure;
  const chosen = body?.plans.long ?? body?.plans.short ?? null;
  const chartPlan = chosen && chosen.ok ? { entry: chosen.entry, stop: chosen.stop, first: chosen.first, target: chosen.target } : null;
  const val = body?.valuation as { score?: number; cheapness?: number; flags?: string[]; per?: unknown } | null | undefined;
  const ex = body?.extremes as { to_high_52w_pct?: number | null; to_low_52w_pct?: number | null; to_sma200_pct?: number | null; rsi14?: number | null } | undefined;

  return (
    <section className="fold">
      <button type="button" className="fold-head" onClick={toggle} aria-expanded={open}>
        <span className="fold-mark">{open ? "▾" : "▸"}</span>
        <span className="card-name">AI 차트 주문</span>
        {open ? null : <span className="faint">{body ? `${body.symbol} · ${body.bucket.label}` : "종목 · 갈래 → 구조 · 롱/숏 계획"}</span>}
      </button>
      {open ? (
        <div>
          <p className="card-hint">
            종목과 갈래(단기 / 스윙 / 장투)를 고르면 <b>그 갈래의 축</b>으로 구조를 읽고 롱/숏 계획을 낸다. 손절·익절은 RiskManager 가
            확정한 값이고, 주문은 <b>사람이</b> 주식 주문 창에서 보고 보낸다. 예측은 없다.
          </p>
          <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            <select value={market} onChange={(e) => setMarket(e.target.value)} title="시장">
              {markets.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
            <BrokerMark broker={brokerOfName(markets, market)} />
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") run();
              }}
              placeholder={stock ? "AAPL" : "BTC_USDT"}
              style={{ width: 140 }}
              title="종목 코드 — 코인은 BTC_USDT 꼴"
            />
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
            <button type="button" className="btn small primary" disabled={busy || !symbol.trim()} onClick={run}>
              {busy ? "읽는 중…" : "분석"}
            </button>
          </div>
          {error ? <ErrorCard message={error} /> : null}
          {body && s ? (
            <>
              <p className="faint text-xs" style={{ marginTop: 6 }}>
                {body.symbol} · {body.market} · {body.bucket.label}(진입 {body.bucket.entry} · 맥락 {body.bucket.context.join("/")}) · {body.note}
              </p>
              <Chart frame={body.frame} plan={chartPlan} />
              <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "stretch" }}>
                <div className="card" style={{ flex: 1, minWidth: 260 }}>
                  <b>구조 · {body.bucket.entry}</b>
                  <ul className="text-sm">
                    <li>현재가 {s.last} · ATR {s.atr || "—"}</li>
                    <li>
                      전고 {s.swings.swing_high ? `${s.swings.swing_high.price} (${pctText(String(s.swings.swing_high.away_pct ?? ""))})` : "—"} · 전저{" "}
                      {s.swings.swing_low ? `${s.swings.swing_low.price} (${pctText(String(s.swings.swing_low.away_pct ?? ""))})` : "—"}
                    </li>
                    <li>
                      아래 첫 지지 {s.nearest_support ? `${s.nearest_support.low}~${s.nearest_support.high} (접점 ${s.nearest_support.touches})` : "없음"} · 위 첫 저항{" "}
                      {s.nearest_resistance ? `${s.nearest_resistance.low}~${s.nearest_resistance.high} (접점 ${s.nearest_resistance.touches})` : "없음"}
                    </li>
                    <li className="faint text-xs">
                      레벨 원본 {s.levels_raw}개 · 규칙 계획선 {s.rule_plan ? `${s.rule_plan.long ? "롱" : "숏"} ${s.rule_plan.entry}/${s.rule_plan.stop}/${s.rule_plan.target}` : "없음(비용을 못 갚는 자리)"}
                    </li>
                    {ex ? (
                      <li className="faint text-xs">
                        52주 고가 대비 {pctText(String(ex.to_high_52w_pct ?? ""))} · 저가 대비 {pctText(String(ex.to_low_52w_pct ?? ""))} · SMA200 이격{" "}
                        {pctText(String(ex.to_sma200_pct ?? ""))} · RSI {ex.rsi14 ?? "—"}
                      </li>
                    ) : null}
                  </ul>
                </div>
                <div className="card" style={{ flex: 1, minWidth: 260 }}>
                  <b>재무 · 거시</b>
                  <ul className="text-sm">
                    {val ? (
                      <li>
                        저평가 점수 {val.score ?? "—"} · 싼 정도 {val.cheapness ?? "—"}
                        {val.flags?.length ? ` · 깃발 ${val.flags.join(", ")}` : ""}
                      </li>
                    ) : (
                      <li className="faint">{body.valuation_note || "재무 없음"}</li>
                    )}
                    <li>
                      VIX {body.vix?.value ?? "—"} {body.vix?.band ? `· ${body.vix.band}` : ""}
                      {body.vix?.note ? <span className="faint text-xs"> — {body.vix.note}</span> : null}
                    </li>
                    <li className="faint text-xs">재무·VIX 는 방아쇠가 아니라 맥락이다 — 급락을 예측하지 않는다.</li>
                  </ul>
                </div>
              </div>
              <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "stretch", marginTop: 8 }}>
                <PlanCard plan={body.plans.long} label="롱" onOrder={stock && who && body.plans.long ? () => order(body.plans.long as ChartPlanSide) : undefined} />
                <PlanCard plan={body.plans.short} label="숏" onOrder={stock && who && body.plans.short ? () => order(body.plans.short as ChartPlanSide) : undefined} />
              </div>
              <p className="faint text-xs">
                분석 id {body.analysis_id} — 기록돼 나중에 익절/손절에 실제로 닿았는지로 채점한다. 코인 주문 연결과 AI 참가자 비교는 다음 단계.
              </p>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
