/**
 * WhaleSurfer 화면 (T442 · 2026-10-08 · 2026-10-09 본체에서 분리) — 유명 13F 보고자의 보유 지도.
 *
 * 배치(사용자 요구 1 ~ 5 · 기능과 대략 배치만 · 꾸밈은 나중):
 *   왼쪽   인물 목록(위키백과 사진 · 없으면 머리글자)
 *   가운데 고른 인물 · 분기 고르기 · 원형 그래프(보유 비중) — 마우스를 올리면 "추적 보고자 중 몇 % 가 들고 있나"
 *          · 조각을 누르면 아래 종목 상세
 *   아래   종목 상세 — 누가 들고 있고 누가 늘리고 줄였나(규모) · 그때 샀다면(T443 실현값) ·
 *          토스 · 바이낸스 · 게이트 바로가기(없으면 회색)
 *
 * 🔴 13F 는 롱 보유만 · 분기 끝 45일 뒤 공개 — 화면마다 그 말을 단다. 이 화면엔 주문 경로가 없다.
 * 주소로 공유된다: `?cik=…&cusip=…`.
 */

import type { ApexOptions } from "apexcharts";
import { useEffect, useMemo, useState } from "react";
import Chart from "react-apexcharts";
import {
  whaleConsensus,
  whaleManager,
  whaleManagers,
  whaleStock,
  type WhaleChangeKind,
  type WhaleConsensus,
  type WhaleManager,
  type WhaleManagerView,
  type WhaleStock,
} from "./api";
import { apexBaseOptions } from "./apexBase";
import { initials, pct, signedPct, slicesOf, usdCompact } from "./format";
import { useQueryParams } from "./params";
import { Card, ErrorCard, LinkButton, Stat } from "./ui";

const KIND_LABEL: Record<WhaleChangeKind, string> = {
  new: "신규",
  added: "늘림",
  reduced: "줄임",
  exited: "전량 매도",
  held: "유지",
};

const KIND_TONE: Record<WhaleChangeKind, string> = {
  new: "bg-gain-wash text-gain",
  added: "bg-gain-wash text-gain",
  reduced: "bg-loss-wash text-loss",
  exited: "bg-loss-wash text-loss",
  held: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
};

function tone(x: number | null | undefined): string {
  if (x === null || x === undefined) return "text-gray-500";
  return x > 0 ? "text-gain" : x < 0 ? "text-loss" : "";
}

function Avatar({ m, size = "h-9 w-9" }: { m: WhaleManager; size?: string }) {
  if (m.image) {
    return <img src={m.image} alt="" className={`${size} shrink-0 rounded-full object-cover`} />;
  }
  return (
    <span
      className={`${size} grid shrink-0 place-items-center rounded-full bg-gray-200 text-xs font-bold text-gray-700 dark:bg-gray-800 dark:text-gray-200`}
      aria-hidden="true"
    >
      {initials(m.person, m.label)}
    </span>
  );
}

function StockDetail({ stock, loading, error }: { stock: WhaleStock | null; loading: boolean; error: string | null }) {
  if (error) return <ErrorCard message={error} title="종목 상세를 못 받았다" />;
  if (loading || !stock) {
    return (
      <Card className="p-5 text-sm text-gray-500">
        종목 상세를 모으는 중 — 추적 보고자 전부의 최근 두 분기를 읽는다(처음엔 느리다).
      </Card>
    );
  }
  const c = stock.consensus;
  const e = stock.estimate;
  const estimate = e
    ? e.mean_q === null
      ? `사건 ${e.events} · 다음 공시 전`
      : `평균 ${signedPct(e.mean_q)} · 중앙 ${signedPct(e.median_q)}`
    : "사건 없음";
  const estimateHint = e && e.win_q !== null ? `공시 ${e.with_q}건 · 오른 비율 ${pct(e.win_q, 0)} · ${stock.estimate_note}` : stock.estimate_note;
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">{stock.issuer ?? stock.cusip}</h2>
          <div className="mt-1 text-xs text-gray-500">
            CUSIP {stock.cusip}
            {stock.ticker ? ` · ${stock.ticker}` : " · 티커 모름"}
            {stock.ticker_source ? ` (${stock.ticker_source})` : ""}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <LinkButton label="토스" href={stock.links.toss} />
          <LinkButton label="바이낸스" href={stock.links.binance} />
          <LinkButton label="Gate" href={stock.links.gate} />
        </div>
      </div>
      {stock.links_note ? <div className="mt-2 text-xs text-loss">{stock.links_note}</div> : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-4">
        <Stat label="들고 있는 보고자" value={c ? `${c.holders}명 · ${pct(c.share, 0)}` : "0명"} />
        <Stat label="직전 분기에 늘림 · 신규" value={c ? `${c.added} · ${c.new}` : "—"} />
        <Stat label="줄임 · 전량 매도" value={c ? `${c.reduced} · ${c.exited}` : "—"} />
        <Stat label="공시 뒤 따라 샀다면(분기)" value={estimate} hint={estimateHint} />
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-gray-500">
            <tr>
              <th className="py-2 pr-3">보고자</th>
              <th className="py-2 pr-3">기준 분기</th>
              <th className="py-2 pr-3">변화</th>
              <th className="py-2 pr-3 text-right">주 수</th>
              <th className="py-2 pr-3 text-right">직전 주 수</th>
              <th className="py-2 pr-3 text-right">가치</th>
              <th className="py-2 pr-3 text-right">그 보고서 안 비중</th>
              <th className="py-2 pr-3 text-right" title="이 공시(신규 · 늘림) 다음 거래일 시가에 샀다면 — 지금까지 · 비용 0.10% 뒤">
                공시 뒤 따라 샀다면
              </th>
            </tr>
          </thead>
          <tbody>
            {stock.holders.map((h) => (
              <tr key={`${h.cik}-${h.put_call ?? ""}`} className="border-t border-gray-100 dark:border-gray-800">
                <td className="py-2 pr-3">
                  <span className="font-medium">{h.person}</span>
                  <span className="ml-1 text-xs text-gray-500">{h.label}</span>
                  {h.put_call ? <span className="ml-1 rounded bg-gray-100 px-1 text-[10px] dark:bg-gray-800">{h.put_call}</span> : null}
                </td>
                <td className="py-2 pr-3 text-xs text-gray-500">
                  {h.period ?? "—"}
                  <span className="block text-[10px]">공시 {h.filed}</span>
                </td>
                <td className="py-2 pr-3">
                  <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${KIND_TONE[h.kind]}`}>{KIND_LABEL[h.kind]}</span>
                </td>
                <td className="py-2 pr-3 text-right font-mono">{h.shares.toLocaleString()}</td>
                <td className="py-2 pr-3 text-right font-mono text-gray-500">{h.prev_shares.toLocaleString()}</td>
                <td className="py-2 pr-3 text-right font-mono">{usdCompact(h.value_usd)}</td>
                <td className="py-2 pr-3 text-right font-mono">{pct(h.weight)}</td>
                <td className={`py-2 pr-3 text-right font-mono ${tone(h.since_filing?.ret_now)}`}>
                  {h.since_filing ? (
                    <span title={`진입 ${h.since_filing.entry_date} · ${h.since_filing.entry} · 다음 공시까지 ${signedPct(h.since_filing.ret_q)}`}>
                      {signedPct(h.since_filing.ret_now)}
                    </span>
                  ) : (
                    <span className="text-gray-400">—</span>
                  )}
                </td>
              </tr>
            ))}
            {stock.holders.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-3 text-gray-500">
                  추적 보고자 중 이 종목을 든 곳이 없다.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {stock.failures.length ? (
        <div className="mt-3 text-xs text-loss">
          못 읽은 보고자 {stock.failures.length}: {stock.failures.map((f) => `${f.label}(${f.reason})`).join(" · ")}
        </div>
      ) : null}
      <div className="mt-3 text-xs text-gray-500">{stock.disclaimer}</div>
    </Card>
  );
}

export function WhaleSurferPage() {
  const [params, select] = useQueryParams();
  const cik = params.get("cik");
  const cusip = params.get("cusip");

  const [managers, setManagers] = useState<WhaleManager[]>([]);
  const [disclaimer, setDisclaimer] = useState("");
  const [view, setView] = useState<WhaleManagerView | null>(null);
  const [viewError, setViewError] = useState<string | null>(null);
  const [reportIdx, setReportIdx] = useState(0);
  const [consensus, setConsensus] = useState<WhaleConsensus | null>(null);
  const [consensusError, setConsensusError] = useState<string | null>(null);
  const [stock, setStock] = useState<WhaleStock | null>(null);
  const [stockLoading, setStockLoading] = useState(false);
  const [stockError, setStockError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  useEffect(() => {
    whaleManagers()
      .then((r) => {
        setManagers(r.managers);
        setDisclaimer(r.disclaimer);
        if (!cik && r.managers[0]) select({ cik: r.managers[0].cik });
      })
      .catch((e: Error) => setListError(e.message));
    // 합의(누가 들고 있나)는 느리다 — 목록과 따로, 한 번만.
    whaleConsensus()
      .then(setConsensus)
      .catch((e: Error) => setConsensusError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!cik) return;
    setView(null);
    setViewError(null);
    setReportIdx(0);
    whaleManager(cik)
      .then(setView)
      .catch((e: Error) => setViewError(e.message));
  }, [cik]);

  useEffect(() => {
    if (!cusip) {
      setStock(null);
      return;
    }
    setStock(null);
    setStockError(null);
    setStockLoading(true);
    whaleStock(cusip)
      .then(setStock)
      .catch((e: Error) => setStockError(e.message))
      .finally(() => setStockLoading(false));
  }, [cusip]);

  const slices = useMemo(() => slicesOf(view, reportIdx), [view, reportIdx]);
  const report = view?.reports[reportIdx] ?? null;
  const current = managers.find((m) => m.cik === cik) ?? null;

  const options: ApexOptions = useMemo(() => {
    const base = apexBaseOptions();
    return {
      ...base,
      labels: slices.labels,
      legend: { position: "bottom", fontSize: "11px" },
      stroke: { width: 1 },
      dataLabels: { enabled: true, formatter: (v: number) => `${v.toFixed(0)}%` },
      chart: {
        ...base.chart,
        events: {
          dataPointSelection: (_e: unknown, _ctx: unknown, cfg: { dataPointIndex: number }) => {
            const c = slices.cusips[cfg.dataPointIndex];
            if (c) select({ cusip: c });
          },
        },
      },
      tooltip: {
        custom: ({ seriesIndex }: { seriesIndex: number }) => {
          const label = slices.labels[seriesIndex] ?? "";
          const w = slices.series[seriesIndex] ?? 0;
          const c = slices.cusips[seriesIndex];
          const slot = c && consensus ? consensus.by_cusip[c] : undefined;
          const who = slot
            ? `추적 보고자 ${consensus?.managers ?? 0}명 중 ${slot.holders}명(${pct(slot.share, 0)}) 보유 · 늘림 ${slot.added} · 줄임 ${slot.reduced}`
            : consensusError
              ? "합의 자료 실패"
              : consensus
                ? c
                  ? "추적 보고자 중 보유 0명"
                  : ""
                : "합의 자료 모으는 중…";
          return `<div style="padding:8px 10px;font-size:12px"><b>${label}</b><br/>이 보고서 안 비중 ${w.toFixed(2)}%<br/>${who}<br/><span style="opacity:.7">누르면 종목 상세</span></div>`;
        },
      },
    };
  }, [slices, consensus, consensusError, select]);

  return (
    <div className="grid gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
      {/* 왼쪽 — 인물 목록 */}
      <aside className="xl:sticky xl:top-4 xl:self-start">
        <Card className="p-3">
          <div className="mb-2 px-1 text-xs font-black uppercase text-gray-500">추적 보고자 {managers.length}</div>
          {listError ? <ErrorCard message={listError} title="목록을 못 받았다" /> : null}
          <ul className="flex max-h-[70vh] flex-col gap-1 overflow-y-auto">
            {managers.map((m) => {
              const on = m.cik === cik;
              return (
                <li key={m.cik}>
                  <button
                    type="button"
                    onClick={() => select({ cik: m.cik, cusip: null })}
                    className={`flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left text-sm transition-colors ${
                      on
                        ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                        : "text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-800"
                    }`}
                  >
                    <Avatar m={m} />
                    <span className="min-w-0">
                      <span className="block truncate font-semibold">{m.person}</span>
                      <span className={`block truncate text-[11px] ${on ? "opacity-80" : "text-gray-500"}`}>{m.label}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </Card>
      </aside>

      {/* 가운데 · 아래 */}
      <div className="flex min-w-0 flex-col gap-4">
        <Card className="p-5">
          {current ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <Avatar m={current} size="h-12 w-12" />
                <div>
                  <h2 className="text-xl font-bold">{current.person}</h2>
                  <div className="text-xs text-gray-500">
                    {current.label} · CIK {current.cik}
                    {report ? ` · ${report.entity}` : ""}
                  </div>
                  {current.image && current.image_credit ? (
                    <div className="text-[10px] text-gray-400">
                      사진:{" "}
                      {current.image_page ? (
                        <a href={current.image_page} target="_blank" rel="noreferrer" className="underline">
                          {current.image_credit}
                        </a>
                      ) : (
                        current.image_credit
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <label className="text-xs text-gray-500" htmlFor="ws-period">
                  기준 분기
                </label>
                <select
                  id="ws-period"
                  className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
                  value={reportIdx}
                  onChange={(e) => setReportIdx(Number(e.target.value))}
                  disabled={!view}
                >
                  {(view?.reports ?? []).map((r, i) => (
                    <option key={r.accession} value={i}>
                      {r.period ?? r.filed} (공시 {r.filed})
                    </option>
                  ))}
                </select>
                {report ? (
                  <span className="text-xs text-gray-500">
                    보유 {report.n}줄 · 합계 {usdCompact(report.total_value_usd)}
                  </span>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="text-sm text-gray-500">왼쪽에서 보고자를 고른다.</div>
          )}
          {viewError ? (
            <div className="mt-3">
              <ErrorCard message={viewError} title="보고를 못 받았다" />
            </div>
          ) : null}
          {cik && !view && !viewError ? <div className="mt-3 text-sm text-gray-500">EDGAR 에서 13F 를 읽는 중…</div> : null}

          {slices.series.length ? (
            <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
              <Chart type="donut" series={slices.series} options={options} height={380} />
              <div>
                <div className="mb-2 text-xs font-bold uppercase text-gray-500">직전 분기 대비 변화 (가치 순)</div>
                <ul className="flex max-h-[22rem] flex-col gap-1 overflow-y-auto text-sm">
                  {(report?.changes ?? [])
                    .filter((c) => c.kind !== "held")
                    .slice(0, 30)
                    .map((c) => (
                      <li key={`${c.cusip}-${c.put_call ?? ""}`}>
                        <button
                          type="button"
                          onClick={() => select({ cusip: c.cusip })}
                          className="flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-800"
                        >
                          <span className="min-w-0 truncate">
                            {c.issuer}
                            {c.put_call ? <span className="ml-1 text-[10px] text-gray-500">{c.put_call}</span> : null}
                          </span>
                          <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${KIND_TONE[c.kind]}`}>
                            {KIND_LABEL[c.kind]} {c.kind === "exited" ? "" : pct(c.weight)}
                          </span>
                        </button>
                      </li>
                    ))}
                  {report && report.changes.every((c) => c.kind === "held") ? (
                    <li className="px-2 text-gray-500">직전 분기와 같거나 비교할 직전 분기가 없다.</li>
                  ) : null}
                </ul>
              </div>
            </div>
          ) : null}
          <div className="mt-3 text-xs text-gray-500">
            {disclaimer}
            {consensusError
              ? ` · 합의 자료 실패: ${consensusError}`
              : consensus
                ? ` · 합의 자료 ${consensus.managers}/${consensus.tracked} 보고자`
                : " · 합의 자료 모으는 중"}
          </div>
        </Card>

        {cusip ? <StockDetail stock={stock} loading={stockLoading} error={stockError} /> : null}
      </div>
    </div>
  );
}
