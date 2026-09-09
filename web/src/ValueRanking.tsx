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
import { fundamentals, valueRanking, type FundamentalsView, type ValueRow } from "./api";
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

/** 펼침 — 한 종목의 표 전체 (지표 · 백분위 · 출처 링크). */
function Detail({ symbol, market }: { symbol: string; market: string }) {
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

export function ValueRanking({ market }: { market: string }) {
  const [rows, setRows] = useState<ValueRow[]>([]);
  const [label, setLabel] = useState("저평가 후보");
  const [recommended, setRecommended] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const pull = () => {
      valueRanking(market)
        .then((body) => {
          if (!alive) return;
          setRows(body.rows);
          setLabel(body.label);
          setRecommended(body.recommended);
          setNote(body.note);
          setError("");
        })
        .catch((exc: unknown) => alive && setError(String(exc)));
    };
    pull();
    // 공시는 분기마다, 종가는 하루 한 번 — 서버도 10분 캐시라 자주 물을 이유가 없다.
    const timer = setInterval(pull, 300_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [market]);

  const scored = rows.filter((r) => r.score !== null && r.score !== undefined).length;
  const summary = error
    ? "읽지 못했다"
    : rows.length
      ? `${rows.length}종목 · 점수 ${scored}개 · ${rows[0]?.symbol ?? ""} 부터`
      : "아직 안 읽었다";
  const title = recommended ? "추천" : label;

  return (
    <Fold name={title} summary={summary} keep="value-ranking" initialShut>
      <p className="card-hint">
        {market} 종목을 <b>재무제표 대비 싼 순</b>으로 — 자기 5년 백분위(쌀수록 100)의 평균에서 부채 깃발마다 감점.
        {note ? ` ${note}` : ""}
      </p>
      {error ? <ErrorCard message={error} /> : null}
      <div className="table-wrap">
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
                />
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="faint">{DISCLAIMER_TEXT}</p>
    </Fold>
  );
}

function RowPair({
  row,
  market,
  shown,
  onToggle,
}: {
  row: ValueRow;
  market: string;
  shown: boolean;
  onToggle: () => void;
}) {
  const tone = scoreTone(row.score);
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
          </span>
        </td>
        <td className="num">{num(row.price ?? null, 2)}</td>
        <td className="num">
          {row.score === null || row.score === undefined ? (
            <span className="chip" title={row.why}>
              {row.has_facts ? "점수 없음" : "재무 없음"}
            </span>
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
            disabled={!row.price}
            title={row.price ? "주식 주문 창에 이 종목을 채운다" : "시세가 없다"}
            onClick={(e) => {
              e.stopPropagation();
              requestStockOrder(row.symbol, market);
            }}
          >
            주문
          </button>
        </td>
      </tr>
      {shown ? (
        <tr className="why-row">
          <td colSpan={11}>
            <Detail symbol={row.symbol} market={market} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
