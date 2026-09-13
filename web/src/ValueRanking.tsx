/**
 * 저평가 후보 — 시장의 종목을 **재무 대비 싼 순**으로 (T244 · 2026-09-09).
 *
 * 🔴 "추천" 이라는 말은 서버의 `recommended` 가 참일 때만 쓴다 — T243 점수가 수익을 가르는지는 OOS 판정 뒤
 *    사용자가 켠다 (규칙 #12). 그 전까지 이 카드는 "저평가 후보" 로 시작한다.
 *
 * ⛔ **재무 없는 종목은 뒤에 선다** — 0점으로 그리면 "가장 비싼 회사" 로 읽힌다 (서버가 이미 그렇게 세운다 ·
 *    화면은 순서를 바꾸지 않는다).
 *
 * 행을 누르면 그 자리에서 근거가 펼쳐진다 (지표 17 · 5년 백분위 · 출처 공시 링크). 팝업 없음.
 * 주문 창(T250)은 아직 없다 — 그때 행 안에서 이어진다.
 */

import { useEffect, useState } from "react";
import { fundamentals, fundamentalsRefresh, valueScreen, type FundamentalsView, type ValueRow, type ValueScreenView } from "./api";
import { BrokerMark } from "./shell/BrokerMark";
import { DISCLAIMER_TEXT } from "./shell/disclaimer";
import { requestStockOrder } from "./StockOrder";
import { ErrorCard, Fold, num, pct } from "./ui";

/** 줄에 싣는 지표 — 서버 `SHOWN_METRICS` 와 같은 순서. */
const COLUMNS = [
  { key: "per", label: "PER", cheaperWhenHigh: false },
  { key: "pbr", label: "PBR", cheaperWhenHigh: false },
  { key: "fcf_yield", label: "FCF 수익률", cheaperWhenHigh: true, percent: true },
  { key: "debt_to_equity", label: "부채비율", cheaperWhenHigh: null },
] as const;

/**
 * 자기 5년 백분위를 말로 — 배수류는 "하위 p%", 수익률류는 "상위 (100-p)%".
 *
 * @param percentile 0~100. 없으면 빈 문자열.
 * @param cheaperWhenHigh 높을수록 싼 지표인가. null 이면 방향 없음(부채) — 백분위를 안 붙인다.
 */
export function pctLabel(
  percentile: number | null | undefined,
  cheaperWhenHigh: boolean | null,
): string {
  if (percentile === null || percentile === undefined || cheaperWhenHigh === null) return "";
  return cheaperWhenHigh
    ? `5년 상위 ${Math.round(100 - percentile)}%`
    : `5년 하위 ${Math.round(percentile)}%`;
}

/** 점수 칩 색 — 60 넘으면 싼 편, 40 아래면 비싼 편. 없으면 회색. */
export function scoreTone(score: number | null | undefined): "gain" | "loss" | "" {
  if (score === null || score === undefined) return "";
  if (score >= 60) return "gain";
  if (score < 40) return "loss";
  return "";
}

function cell(row: ValueRow, key: (typeof COLUMNS)[number]["key"], percent: boolean): string {
  const got = row.metrics?.[key];
  if (!got || got.value === null || got.value === undefined) return "—";
  return percent ? pct(got.value) : `${num(got.value, 1)}x`;
}

/** 펼침 — 한 종목의 표 전체 (지표 · 백분위 · 출처 링크). 달력의 실적 행(T276)도 이것을 쓴다 — 두 곳이 갈리지 않게. */
export function FundamentalsDetail({ symbol, market }: { symbol: string; market: string }) {
  const [view, setView] = useState<FundamentalsView | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    fundamentals(symbol, market)
      .then((body) => alive && setView(body))
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [symbol, market]);
  if (error) return <ErrorCard message={error} />;
  if (!view) return <p className="faint">읽는 중…</p>;
  const groups: Array<[string, string]> = [
    ["price", "가격이 싼가"],
    ["debt", "빚 위험"],
    ["earning", "벌고 있나"],
    ["dilution", "희석"],
  ];
  return (
    <div>
      {view.notes.length ? <p className="faint">{view.notes.join(" · ")}</p> : null}
      {groups.map(([group, title]) => (
        <div key={group}>
          <p className="faint">
            <b>{title}</b>
          </p>
          <ul>
            {view.metrics
              .filter((m) => m.group === group)
              .map((m) => (
                <li key={m.key}>
                  {m.label}{" "}
                  <span className="mono">
                    {m.value === null ? "—" : m.unit === "%" ? pct(m.value) : `${num(m.value, 2)}x`}
                  </span>
                  {m.percentile !== null ? (
                    <span className="faint"> · {pctLabel(m.percentile, m.higher_is_cheaper)}</span>
                  ) : null}
                  {m.note ? <span className="faint"> · {m.note}</span> : null}
                  {m.sources.length ? (
                    <span className="faint">
                      {" "}
                      · 출처{" "}
                      {m.sources.slice(0, 3).map((s) =>
                        s.url ? (
                          <a key={s.accession} href={s.url} target="_blank" rel="noreferrer" className="mono">
                            {s.form ?? s.accession}{" "}
                          </a>
                        ) : (
                          <span key={s.accession} className="mono">
                            {s.accession}{" "}
                          </span>
                        ),
                      )}
                    </span>
                  ) : null}
                </li>
              ))}
          </ul>
        </div>
      ))}
      <p className="faint">
        점수 {view.score.score === null ? "없음" : num(view.score.score, 0)}
        {view.score.note ? ` — ${view.score.note}` : ""} · 백분위 표본 {view.history_points}개월 ·
        기준 {view.as_of.slice(0, 10)}
      </p>
    </div>
  );
}

const SORT_LABEL: Record<string, string> = {
  score: "저평가 점수",
  per: "PER",
  pbr: "PBR",
  psr: "PSR",
  fcf_yield: "FCF 수익률",
  debt_to_equity: "부채비율",
  momentum_60d: "60일 모멘텀",
  market_cap: "시가총액",
};

/** 범위 칩 — 시장이 둘 이상이면 `전체`(합산) · `SP 500`(후보 503 전부)을 앞에 둔다 (사용자 요청 2026-09-10). */
export function scopeOptions(markets: string[]): Array<{ label: string; value: string }> {
  const own = markets.map((m) => ({ label: m, value: m }));
  if (markets.length < 2) return own;
  return [
    { label: "전체", value: "ALL" },
    { label: "SP 500", value: "SP500" },
    ...own,
  ];
}

export function ValueRanking({ markets }: { markets: string[] }) {
  // 🔴 재무가 있는 시장이 **둘 이상**이다(NASDAQ · NYSE). 첫 시장만 보이면 NYSE 62종(ORCL · JPM · V …)이
  //    적재돼 있어도 화면에 영영 안 뜬다 (사용자 신고 2026-09-10 "ORCL 이 왜 없나"). 범위는 칩으로 고른다.
  const scopes = scopeOptions(markets);
  const [market, setMarket] = useState(scopes[0]?.value ?? "");
  useEffect(() => {
    if (!scopes.some((s) => s.value === market)) setMarket(scopes[0]?.value ?? "");
  }, [scopes, market]);
  const [view, setView] = useState<ValueScreenView | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  // ⭐ 필터·정렬·쪽은 서버가 처리한다(T255) — 창은 한 쪽 크기만큼만 자란다.
  const [sort, setSort] = useState("score");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [minScore, setMinScore] = useState<string>("");
  const [noFlags, setNoFlags] = useState(false);
  const [hasFacts, setHasFacts] = useState(false);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const pull = () => {
      valueScreen(market, {
        sort,
        order,
        min_score: minScore.trim() === "" ? null : Number(minScore),
        no_flags: noFlags,
        has_facts: hasFacts,
        q,
        page,
        size,
      })
        .then((body) => {
          if (!alive) return;
          setView(body);
          setError("");
        })
        .catch((exc: unknown) => alive && setError(String(exc)));
    };
    pull();
    // 공시는 분기마다, 종가는 하루 한 번 — 서버도 캐시라 자주 물을 이유가 없다.
    const timer = setInterval(pull, 300_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [market, sort, order, minScore, noFlags, hasFacts, q, page, size, tick]);

  const rows = view?.rows ?? [];
  const scored = rows.filter((r) => r.score !== null && r.score !== undefined).length;
  const summary = error
    ? "읽지 못했다"
    : view
      ? `${view.total}종목 · 이 쪽 ${rows.length} · 점수 ${scored}개`
      : "아직 안 읽었다";
  const title = view?.recommended ? "추천" : (view?.label ?? "저평가 후보");
  const pickSort = (key: string) => {
    setSort(key);
    // 배수(PER 등)는 낮을수록 싸다 — 기본 오름차순. 점수·수익률·모멘텀·시총은 내림차순.
    setOrder(["per", "pbr", "psr", "debt_to_equity"].includes(key) ? "asc" : "desc");
    setPage(1);
  };

  return (
    <Fold name={title} summary={summary} keep="value-ranking" initialShut>
      <p className="card-hint">
        {scopes.find((s) => s.value === market)?.label ?? market} 종목을 <b>재무제표 대비 싼 순</b>으로 — 자기 5년 백분위(쌀수록 100)의 평균에서 부채 깃발마다 감점.
        {view?.note ? ` ${view.note}` : ""} 1단계(지금 값 · frames)는 점수 없이 값만 보이고, "이력 받기" 로 2단계(5년 백분위 · 점수)가 된다.
      </p>
      {scopes.length > 1 ? (
        <div className="row" style={{ gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
          {scopes.map((s) => (
            <button
              key={s.value}
              type="button"
              className={`chip${market === s.value ? " live" : ""}`}
              title={
                s.value === "ALL"
                  ? "필터 없음 — 아는 종목 전부(적재분 + S&P 500 후보). 적재 안 된 종목은 1단계(지금 값), 처음엔 몇 분 준비"
                  : s.value === "SP500"
                    ? "S&P 500 목록에 든 종목만"
                    : `${s.label} 상장 종목만`
              }
              onClick={() => {
                setMarket(s.value);
                setPage(1);
                setOpen(null);
              }}
            >
              {s.label}
            </button>
          ))}
          {view?.pending ? <span className="faint text-xs">1단계 {view.pending}종 준비 중…</span> : null}
        </div>
      ) : null}
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select value={sort} onChange={(e) => pickSort(e.target.value)} title="정렬 기준">
          {(view?.sorts ?? Object.keys(SORT_LABEL)).map((k) => (
            <option key={k} value={k}>
              {SORT_LABEL[k] ?? k}
            </option>
          ))}
        </select>
        <button type="button" className="btn small" onClick={() => setOrder((was) => (was === "asc" ? "desc" : "asc"))} title="오름/내림">
          {order === "asc" ? "오름차순" : "내림차순"}
        </button>
        <input
          className="mono"
          style={{ width: 80 }}
          placeholder="최소 점수"
          value={minScore}
          onChange={(e) => {
            setMinScore(e.target.value);
            setPage(1);
          }}
        />
        <label className="faint text-xs">
          <input type="checkbox" checked={noFlags} onChange={(e) => { setNoFlags(e.target.checked); setPage(1); }} /> 부채 깃발 제외
        </label>
        <label className="faint text-xs">
          <input type="checkbox" checked={hasFacts} onChange={(e) => { setHasFacts(e.target.checked); setPage(1); }} /> 이력 있음만
        </label>
        <input
          style={{ width: 110 }}
          placeholder="종목 검색"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <select value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(1); }} title="쪽 크기">
          {[10, 20, 50].map((n) => (
            <option key={n} value={n}>
              {n}줄
            </option>
          ))}
        </select>
      </div>
      {error ? <ErrorCard message={error} /> : null}
      <div className="table-wrap" style={{ maxHeight: "28rem", overflowY: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>종목</th>
              <th className="num">가격</th>
              <th className="num">점수</th>
              {COLUMNS.map((col) => (
                <th key={col.key} className="num">
                  {col.label}
                </th>
              ))}
              <th className="num">60일</th>
              <th>부채 깃발</th>
              <th>최근 공시</th>
              <th>왜 이 자리</th>
              <th>주문</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const shown = open === row.symbol;
              return (
                <RowPair
                  key={row.symbol}
                  row={row}
                  market={market}
                  shown={shown}
                  onToggle={() => setOpen(shown ? null : row.symbol)}
                  onPromoted={() => setTick((t) => t + 1)}
                />
              );
            })}
            {rows.length === 0 && view ? (
              <tr>
                <td colSpan={11} className="faint">
                  조건에 맞는 종목이 없다.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {view && view.pages > 1 ? (
        <div className="row" style={{ gap: 8, alignItems: "center", justifyContent: "flex-end" }}>
          <button type="button" className="btn small" disabled={view.page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            이전
          </button>
          <span className="faint text-xs">
            {view.page} / {view.pages} 쪽 · {view.total}종목
          </span>
          <button type="button" className="btn small" disabled={view.page >= view.pages} onClick={() => setPage((p) => p + 1)}>
            다음
          </button>
        </div>
      ) : null}
      <p className="faint">{DISCLAIMER_TEXT}</p>
    </Fold>
  );
}

function RowPair({
  row,
  market,
  shown,
  onToggle,
  onPromoted,
}: {
  row: ValueRow & { stage?: string };
  market: string;
  shown: boolean;
  onToggle: () => void;
  /** 1단계 줄의 "이력 받기" 가 끝나면 표를 다시 읽는다. */
  onPromoted?: () => void;
}) {
  const tone = scoreTone(row.score);
  const [busy, setBusy] = useState(false);
  const [promoteError, setPromoteError] = useState("");
  // ⭐ 전체·SP 500 범위에서는 행마다 시장이 다르다 — 서버가 준 것을 쓰고, 없으면(AMEX) 주문·이력 받기를 막는다.
  const rowMarket = row.market ?? (["ALL", "SP500"].includes(market) ? null : market);
  const promote = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!rowMarket) return;
    setBusy(true);
    setPromoteError("");
    fundamentalsRefresh(row.symbol, rowMarket)
      .then(() => onPromoted?.())
      .catch((exc: unknown) => setPromoteError(String(exc)))
      .finally(() => setBusy(false));
  };
  return (
    <>
      <tr
        onClick={row.has_facts ? onToggle : undefined}
        style={row.has_facts ? { cursor: "pointer" } : undefined}
        title={row.has_facts ? "누르면 근거가 펼쳐진다" : "공시를 아직 안 받았다"}
      >
        <td className="mono" style={{ minWidth: "8rem" }}>
          <span className="inline-flex items-center gap-1">
            <BrokerMark broker={row.broker ?? undefined} />
            {row.symbol}
            {row.name ? <span className="faint text-xs">{row.name}</span> : null}
            {["ALL", "SP500"].includes(market) && rowMarket ? (
              <span className="faint text-xs">{rowMarket}</span>
            ) : null}
          </span>
        </td>
        <td className="num">{num(row.price ?? null, 2)}</td>
        <td className="num">
          {row.score === null || row.score === undefined ? (
            row.stage === "quick" ? (
              <span className="inline-flex items-center gap-1">
                <span className="chip" title="1단계 — frames 지금 값 · 백분위·점수 없음">
                  1단계
                </span>
                <button type="button" className="btn small" disabled={busy || !rowMarket} onClick={promote} title={rowMarket ? "companyfacts 이력을 받아 2단계(5년 백분위 · 점수)로 올린다" : "시장을 모르는 종목이다(AMEX 등)"}>
                  {busy ? "받는 중…" : "이력 받기"}
                </button>
                {promoteError ? <span className="loss text-xs">{promoteError}</span> : null}
              </span>
            ) : (
              <span className="chip" title={row.why}>
                {row.has_facts ? "점수 없음" : "재무 없음"}
              </span>
            )
          ) : (
            <span className={`chip ${tone}`}>{num(row.score, 0)}</span>
          )}
        </td>
        {COLUMNS.map((col) => (
          <td key={col.key} className="num" title={pctLabel(row.metrics?.[col.key]?.percentile, col.cheaperWhenHigh)}>
            {cell(row, col.key, "percent" in col && col.percent === true)}
            {col.cheaperWhenHigh === null ? null : (
              <span className="faint"> {pctLabel(row.metrics?.[col.key]?.percentile, col.cheaperWhenHigh)}</span>
            )}
          </td>
        ))}
        <td
          className={`num ${
            row.momentum_60d === null || row.momentum_60d === undefined
              ? ""
              : row.momentum_60d >= 0
                ? "gain"
                : "loss"
          }`}
        >
          {row.momentum_60d === null || row.momentum_60d === undefined ? "—" : pct(row.momentum_60d * 100)}
        </td>
        <td>
          {row.flags.length ? (
            row.flags.map((f) => (
              <span key={f} className="chip loss">
                {f}
              </span>
            ))
          ) : row.has_facts ? (
            <span className="chip gain">없음</span>
          ) : (
            "—"
          )}
        </td>
        <td>
          {row.latest_filing ? (
            row.latest_filing.url ? (
              <a href={row.latest_filing.url} target="_blank" rel="noreferrer" className="mono">
                {row.latest_filing.form} {row.latest_filing.filed_at.slice(0, 10)}
              </a>
            ) : (
              <span className="mono">
                {row.latest_filing.form} {row.latest_filing.filed_at.slice(0, 10)}
              </span>
            )
          ) : (
            "—"
          )}
        </td>
        <td className="faint">{row.why}</td>
        <td>
          {/* 카드 → 주문 창 (T250). 행 클릭은 근거 펼침이라 단추는 전파를 막는다. */}
          <button
            type="button"
            className="btn small"
            disabled={!row.price || !rowMarket}
            title={!rowMarket ? "시장을 모르는 종목이다(AMEX 등)" : row.price ? "주식 주문 창에 이 종목을 채운다" : "시세가 없다"}
            onClick={(e) => {
              e.stopPropagation();
              if (rowMarket) requestStockOrder(row.symbol, rowMarket);
            }}
          >
            주문
          </button>
        </td>
      </tr>
      {shown ? (
        <tr className="why-row">
          <td colSpan={11}>
            <FundamentalsDetail symbol={row.symbol} market={rowMarket ?? market} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
