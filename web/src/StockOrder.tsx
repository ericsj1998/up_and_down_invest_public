/**
 * 주식 주문 창 — 차트를 보고 사람이 주식을 산다 (T250 · 2026-09-09).
 *
 * 코인 차트 주문(`custom` 판 · 09-05 에 탭은 지웠고 서버 경로는 남았다)을 **주식 능력표**로 다시 쓴 것이다:
 *
 *     정수 주        예산이 아니라 주수를 받는다 — 필요 현금 = 주수 x 진입가, 서버는 그 값을 예산으로 받아 같은 사이징 경로를 탄다
 *     배율 없음      배율 칸이 없다 · 서버는 1 을 강제한다
 *     숏 없음        매수만 — 방향 칩이 없다
 *     장중만         장 시간 배지(`MarketHours`) · 닫혀 있으면 단추가 막히고 "다음 개장" 을 말한다 (예약 주문 없음)
 *     권한           T242 `markets.foreign.trade` 없으면 🔒
 *
 * 차트·끌기·계획 산수·확정 미리보기(`/analysis/validate`)는 코인과 **같은 부품**이다 — 차이는 시장의 성질뿐이다.
 * 판의 주체는 사람이고 러너는 집행·감시·기록만 한다. 팝업 없음.
 */

import { useCallback, useEffect, useState } from "react";
import { GROUPS, pickSegments, readSegments } from "./analysis";
import {
  analysisFrame,
  consoleBalances,
  marketStatus,
  orderCustom,
  validatePlan,
} from "./api";
import type { AnalysisFrame, MarketInfo, Who } from "./api";
import { Chart } from "./Chart";
import { Live } from "./Live";
import {
  MIN_STOP_PCT,
  blockers,
  move,
  recompute,
  warnings,
  type Plan,
} from "./proposal";
import { BrokerMark } from "./shell/BrokerMark";
import { MarketHours } from "./shell/MarketHours";
import { brokerOfName, marketTradeAllowed } from "./shell/marketGroup";
import {
  symbolForMarket,
  cashNeeded,
  defaultDraft,
  maxShares,
  orderBlockers,
} from "./stockOrder";
import { ErrorCard, frameSeconds, num, useFold } from "./ui";
import { useAnalysisForming } from "./useForming";
import { pick, useStream } from "./useStream";

/** 처음 그리는 축 — 응답의 `frames` 가 오면 갈아 끼운다 (거래소가 아는 사실). */
const FRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];
/** 손절 감시를 보는 축 그대로 둘 수 있는 축 — 느린 축을 볼 때는 1분으로 내린다. */
const FAST = ["1m", "5m", "15m", "1h"];
const SLOT = "stock-order-groups";
/** 저평가 후보 카드의 "주문" 단추가 쏘는 사건 — 종목·시장을 이 창에 채운다. */
export const ORDER_EVENT = "updown:stock-order";

export function beatSeconds(frame: string): number {
  return Math.min(60, Math.max(5, frameSeconds(frame)));
}

function remembered(): string[] {
  try {
    const kept: unknown = JSON.parse(localStorage.getItem(SLOT) ?? "null");
    if (!Array.isArray(kept)) return ["trend"];
    return kept.filter((item): item is string => typeof item === "string");
  } catch {
    return ["trend"];
  }
}

/** 카드에서 이 창으로 — 팝업 없이 같은 화면 안에서 이어진다. */
/** 카드·분석 화면이 넘기는 것 — 계획과 축은 선택 (T273 "이 계획으로 주문"). */
export type OrderRequest = {
  symbol: string;
  market: string;
  plan?: {
    long: boolean;
    entry: number;
    stop: number;
    first: number;
    target: number;
  };
  frame?: string;
};

export function requestStockOrder(
  symbol: string,
  market: string,
  extra: Omit<OrderRequest, "symbol" | "market"> = {},
): void {
  window.dispatchEvent(
    new CustomEvent<OrderRequest>(ORDER_EVENT, {
      detail: { symbol, market, ...extra },
    }),
  );
}

const STASH = "updown:stock-order:stash";

/** 다른 화면(AI 차트 분석 주문)에서 넘길 때 — 이 창이 아직 안 떠 있으니 두고 간다. 콘솔이 뜨면 집어 간다. */
export function stashStockOrder(req: OrderRequest): void {
  try {
    sessionStorage.setItem(STASH, JSON.stringify(req));
  } catch {
    /* 저장이 막힌 브라우저 — 콘솔에서 직접 고른다 */
  }
}

function takeStash(): OrderRequest | null {
  try {
    const raw = sessionStorage.getItem(STASH);
    if (!raw) return null;
    sessionStorage.removeItem(STASH);
    const kept: unknown = JSON.parse(raw);
    if (!kept || typeof kept !== "object") return null;
    const row = kept as Partial<OrderRequest>;
    return typeof row.symbol === "string" && typeof row.market === "string"
      ? (row as OrderRequest)
      : null;
  } catch {
    return null;
  }
}

export function StockOrder({
  markets,
  who,
}: {
  markets: MarketInfo[];
  who: Who | null;
}) {
  const [open, toggle] = useFold("stock-order", true);
  const [market, setMarket] = useState(markets[0]?.name ?? "NASDAQ");
  const [symbol, setSymbol] = useState("AAPL");
  const [frame, setFrame] = useState("1h");
  const [served, setServed] = useState<string[]>(FRAMES);
  const [on, setOn] = useState<string[]>(remembered);
  const [body, setBody] = useState<AnalysisFrame | null>(null);
  const [busy, setBusy] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  const [draft, setDraft] = useState<Plan | null>(null);
  const [shares, setShares] = useState(1);
  const [cash, setCash] = useState<number | null>(null);
  const [state, setState] = useState<"open" | "closed" | "unknown">("unknown");
  const [nextOpen, setNextOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [placed, setPlaced] = useState<{
    key: string;
    moved: boolean;
    stop: string;
  } | null>(null);
  const [risk, setRisk] = useState<Awaited<
    ReturnType<typeof validatePlan>
  > | null>(null);

  const allowed = marketTradeAllowed(who, "stock");
  const info = markets.find((m) => m.name === market);
  const alwaysOpen = info?.always_open === true;

  useEffect(() => {
    try {
      localStorage.setItem(SLOT, JSON.stringify(on));
    } catch {
      // 기억만 못 한다.
    }
  }, [on]);

  // 카드의 "주문" 단추 → 종목·시장을 채우고 접혀 있으면 편다.
  useEffect(() => {
    const onEvent = (event: Event) => {
      const detail = (event as CustomEvent<OrderRequest>).detail;
      if (!detail) return;
      setSymbol(detail.symbol);
      if (markets.some((m) => m.name === detail.market))
        setMarket(detail.market);
      if (detail.frame) setFrame(detail.frame);
      // ⭐ T273 — 분석 화면의 계획을 그대로 초안으로. 사람이 보고 고친 뒤 보낸다(팝업 없음 · 재인증 문은 서버).
      if (detail.plan) setDraft(detail.plan);
      if (!open) toggle();
    };
    window.addEventListener(ORDER_EVENT, onEvent);
    // ⭐ 다른 화면이 두고 간 요청 — 이 창이 뜨는 순간 한 번 집어 간다 (AI 차트 분석 주문 → 콘솔).
    const stashed = takeStash();
    if (stashed)
      onEvent(new CustomEvent<OrderRequest>(ORDER_EVENT, { detail: stashed }));
    return () => window.removeEventListener(ORDER_EVENT, onEvent);
  }, [markets, open, toggle]);

  // 가용 현금 · 장 상태 — 둘 다 자주 안 바뀐다.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const pull = () => {
      consoleBalances()
        .then((body) => {
          if (!alive) return;
          const found = body.balances[market];
          setCash(found ? Number(found.available) : null);
        })
        .catch(() => alive && setCash(null));
      if (alwaysOpen) {
        setState("open");
        return;
      }
      marketStatus(market)
        .then((got) => {
          if (!alive) return;
          setState(got.state);
          setNextOpen(got.next_open);
        })
        .catch(() => alive && setState("unknown"));
    };
    pull();
    const timer = setInterval(pull, 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [open, market, alwaysOpen]);

  const pull = useCallback(
    (fresh = true) => {
      const flags = GROUPS.filter((item) => on.includes(item.id)).flatMap(
        (item) => item.flags,
      );
      if (fresh) setBusy(true);
      setError("");
      analysisFrame({
        symbol,
        market,
        flags: flags.length ? flags : ["trend.structure"],
        timeframe: frame,
      })
        .then((first) => {
          setBody(first);
          if (first.frames && first.frames.length > 0) setServed(first.frames);
          if (fresh) {
            const last = first.candles[first.candles.length - 1];
            // ⚠️ 서버 제안이 없는 것이 흔하다 — 주문 창은 그래도 낼 수 있어야 하므로 마지막 종가로 초안을 만든다.
            //    주식은 롱만이라 서버 제안이 숏이면 버린다.
            setDraft(
              first.plan !== null && first.plan.long
                ? {
                    long: true,
                    entry: Number(first.plan.entry),
                    stop: Number(first.plan.stop),
                    first: Number(first.plan.first),
                    target: Number(first.plan.target),
                  }
                : defaultDraft(last ? Number(last.close) : NaN),
            );
          }
          if (first.candles.length === 0)
            setError(
              `${symbol} 의 ${frame} 봉이 없다 — 적재된 축인지 확인한다`,
            );
        })
        .catch((exc: unknown) => {
          if (fresh) setBody(null);
          setError(String(exc));
        })
        .finally(() => {
          if (fresh) setBusy(false);
        });
    },
    [market, symbol, frame, on],
  );

  useEffect(() => {
    if (open) pull(true);
  }, [open, pull]);

  useEffect(() => {
    if (!open) return;
    const beat = beatSeconds(frame);
    const tick = () => {
      if (document.visibilityState === "visible") pull(false);
    };
    const timer = window.setInterval(tick, beat * 1000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [open, frame, pull]);

  // 확정 미리보기 — 주문 경로와 같은 함수(`decision/risk/manual.confirm`)가 본다. 배율은 1.
  useEffect(() => {
    if (!draft) {
      setRisk(null);
      return;
    }
    let alive = true;
    validatePlan({ ...draft, leverage: 1, market, short: false })
      .then((got) => alive && setRisk(got))
      .catch(() => alive && setRisk(null));
    return () => {
      alive = false;
    };
  }, [draft, market]);

  const order = useCallback(() => {
    if (draft === null) return;
    setSending(true);
    setError("");
    const flags = GROUPS.filter((item) => on.includes(item.id)).flatMap(
      (item) => item.flags,
    );
    orderCustom({
      symbol,
      market,
      margin: cashNeeded(shares, draft.entry),
      shares,
      leverage: 1,
      short: false,
      entry: draft.entry,
      stop: draft.stop,
      first: draft.first,
      target: draft.target,
      flags,
      timeframe: frame,
      price_frame: FAST.includes(frame) ? frame : "1m",
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
  }, [draft, symbol, market, shares, on, frame]);

  const layer = (flag: string) =>
    body?.layers.find((item) => item.flag === flag);
  const zones = (body?.levels ?? []).map((item) => ({
    low: Number(item.low),
    high: Number(item.high),
    kind: item.support ? "support" : "resistance",
  }));
  const segments = pickSegments(
    readSegments(
      (layer("structure.swing_trendline")?.shapes ?? []) as Record<
        string,
        unknown
      >[],
    ),
  );
  const streamed = useStream(open ? symbol : "", market, frame);
  const polled = useAnalysisForming(open ? symbol : "", market, frame);
  const forming = pick(streamed, polled);
  const cost = Number(body?.round_trip_pct ?? 0);
  const math = draft ? recompute(draft, cost) : null;
  const planBlocks = draft ? blockers(draft, cost) : [];
  const careful = draft ? warnings(draft, cost) : [];
  const gate = draft
    ? orderBlockers({
        shares,
        entry: draft.entry,
        cash,
        marketState: state,
        allowed,
      })
    : ["계획이 없다"];
  const stuck = [...planBlocks, ...gate];
  const summary = `${symbol} · ${frame} · ${state === "open" ? "장중" : "장 마감"}`;

  return (
    <section className="fold">
      <button
        type="button"
        className="fold-head"
        onClick={toggle}
        aria-expanded={open}
      >
        <span className="fold-mark">{open ? "▾" : "▸"}</span>
        <span className="card-name">주식 주문</span>
        {open ? null : <span className="faint">{summary}</span>}
        {!allowed ? (
          <span
            className="chip"
            title="이 시장에서 거래할 권한이 없다 (관리자에게)"
          >
            🔒
          </span>
        ) : null}
      </button>
      {open ? (
        <>
          <p className="card-hint">
            차트를 보고 <b>사람이</b> 산다 — 정수 주 · 배율 없음 · 매수만 ·
            장중만. 판(RUN)이 하나 생기고 러너는 집행·감시·기록만 한다.
            손절·익절 선은 차트에서 <b>끌어서</b> 고칠 수 있다.
          </p>
          <div className="row">
            <BrokerMark broker={brokerOfName(markets, market)} />
            <select
              value={market}
              onChange={(e) => {
                const next = e.target.value;
                setMarket(next);
                setSymbol((was) => symbolForMarket(next, was));
              }}
            >
              {markets.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.trim().toUpperCase())}
              placeholder="AAPL"
              style={{ width: 100 }}
            />
            {FRAMES.map((item) => {
              const has = served.includes(item);
              return (
                <button
                  key={item}
                  className={item === frame ? "btn small primary" : "btn small"}
                  disabled={!has}
                  title={
                    has
                      ? `${item} 봉으로 본다`
                      : `${market} 는 ${item} 봉을 주지 않는다`
                  }
                  onClick={() => setFrame(item)}
                >
                  {item}
                </button>
              );
            })}
            <button
              className="btn small"
              onClick={() => pull(true)}
              disabled={busy}
            >
              {busy ? "보는 중…" : "다시 본다"}
            </button>
            <Live on={liveOn} onToggle={() => setLiveOn((was) => !was)} />
            <MarketHours market={market} alwaysOpen={alwaysOpen} />
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            {GROUPS.map((item) => (
              <button
                key={item.id}
                className={on.includes(item.id) ? "chip gain" : "chip"}
                title={item.hint}
                style={{ cursor: "pointer", font: "inherit" }}
                onClick={() =>
                  setOn((was) =>
                    was.includes(item.id)
                      ? was.filter((one) => one !== item.id)
                      : [...was, item.id],
                  )
                }
              >
                {on.includes(item.id) ? "● " : "○ "}
                {item.label}
              </button>
            ))}
          </div>

          {error ? <ErrorCard message={error} /> : null}

          {placed ? (
            <div className="row" style={{ marginTop: 8 }}>
              <span className="chip gain">판을 띄우고 매수 주문을 냈다</span>
              <a className="btn small" href={`#/runs/${placed.key}`}>
                판 {placed.key} 로 간다
              </a>
              {placed.moved ? (
                <span className="chip loss">
                  손절이 {placed.stop} 로 당겨졌다
                </span>
              ) : (
                <span className="chip faint">손절은 낸 값 그대로다</span>
              )}
            </div>
          ) : null}

          {body && draft ? (
            <div className="card" style={{ marginTop: 8 }}>
              <div className="row">
                <b>계획</b>
                <span className="chip gain">매수</span>
                {body.plan && body.plan.long ? (
                  <span
                    className="chip faint"
                    title="서버가 레벨에서 잡았다 — 사람이 고칠 수 있다"
                  >
                    {body.plan.why}
                  </span>
                ) : (
                  <span className="chip faint">
                    서버 제안 없음 — 마지막 종가 기준 초안
                  </span>
                )}
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                {(
                  [
                    ["entry", "진입"],
                    ["stop", "손절"],
                    ["first", "1차 익절"],
                    ["target", "익절"],
                  ] as const
                ).map(([key, label]) => (
                  <label
                    key={key}
                    style={{ display: "flex", gap: 4, alignItems: "center" }}
                  >
                    <span className="faint">{label}</span>
                    <input
                      className="mono"
                      type="number"
                      value={draft[key]}
                      step="0.01"
                      style={{ width: 110 }}
                      onChange={(event) => {
                        const got = Number(event.target.value);
                        if (Number.isFinite(got))
                          setDraft((was) => (was ? move(was, key, got) : was));
                      }}
                    />
                  </label>
                ))}
                <label
                  style={{ display: "flex", gap: 4, alignItems: "center" }}
                  title="정수 주. 현금 한도 안에서"
                >
                  <span className="faint">주수</span>
                  <input
                    className="mono"
                    type="number"
                    min={1}
                    step={1}
                    value={shares}
                    style={{ width: 80 }}
                    onChange={(e) =>
                      setShares(
                        Math.max(1, Math.floor(Number(e.target.value) || 1)),
                      )
                    }
                  />
                </label>
                <span
                  className="chip"
                  title="주수 x 진입가 / 0.99(러너 여유) — 서버는 이 값을 판 예산으로 받는다"
                >
                  필요 예산 {num(cashNeeded(shares, draft.entry), 2)}
                </span>
                <span className="chip faint" title="페이퍼 계좌 가용 현금">
                  가용 {cash === null ? "—" : num(cash, 2)}
                  {cash !== null
                    ? ` · 최대 ${maxShares(cash, draft.entry)}주`
                    : ""}
                </span>
              </div>
              {math ? (
                <div className="row" style={{ marginTop: 8 }}>
                  <span className="chip">RR {math.rr.toFixed(2)}</span>
                  <span
                    className={math.needPct >= 100 ? "chip loss" : "chip"}
                    title="P > (1+c)/(1+RR)"
                  >
                    필요 승률 {math.needPct.toFixed(1)}%
                  </span>
                  <span
                    className={
                      math.stopPct < MIN_STOP_PCT ? "chip loss" : "chip"
                    }
                  >
                    손절폭 {math.stopPct.toFixed(2)}%
                  </span>
                  <span className="chip faint">
                    왕복 비용 {cost.toFixed(3)}%
                  </span>
                  {risk ? (
                    <span
                      className={risk.moved ? "chip loss" : "chip"}
                      title="RiskManager 가 확정한 손절가"
                    >
                      확정 손절 {num(Number(risk.stop), 2)}
                      {risk.moved ? " (당겨졌다)" : ""}
                    </span>
                  ) : null}
                </div>
              ) : null}
              {stuck.length > 0 ? (
                <div className="row" style={{ marginTop: 8 }}>
                  {stuck.map((item) => (
                    <span key={item} className="chip loss">
                      ⛔ {item}
                    </span>
                  ))}
                </div>
              ) : null}
              {careful.length > 0 ? (
                <div className="row" style={{ marginTop: 8 }}>
                  {careful.map((item) => (
                    <span key={item} className="chip faint">
                      ⚠️ {item}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="row" style={{ marginTop: 8 }}>
                <button
                  className="btn primary"
                  disabled={stuck.length > 0 || sending}
                  onClick={order}
                  title={
                    stuck.length > 0
                      ? stuck.join(" · ")
                      : "이 값 그대로 판을 띄우고 산다"
                  }
                >
                  {sending ? "보내는 중…" : `${shares}주 산다`}
                </button>
                {state !== "open" && nextOpen ? (
                  <span className="faint">
                    장이 열려 있지 않다 — 다음 개장{" "}
                    {new Date(nextOpen).toLocaleString("ko-KR")} · 예약 주문
                    없음
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}

          {body ? (
            <Chart
              frame={body}
              entryFrame={frame}
              forming={forming}
              active={false}
              follow={liveOn}
              zones={zones}
              segments={segments}
              draft={draft}
              onDrag={(which, price) =>
                setDraft((was) => (was ? move(was, which, price) : was))
              }
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}
