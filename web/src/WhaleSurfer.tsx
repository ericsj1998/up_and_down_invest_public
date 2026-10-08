/**
 * WhaleSurfer (T442 · 2026-10-08) — 유명 13F 보고자의 보유 지도.
 *
 * 배치(사용자 요구 1 ~ 5 · 기능과 대략 배치만 · 꾸밈은 나중):
 *   왼쪽  인물 목록(사진 자리 · 없으면 머리글자)
 *   가운데 고른 인물 · 분기 고르기 · 원형 그래프(보유 비중) — 마우스를 올리면 "추적 보고자 중 몇 % 가 들고 있나"
 *          · 조각을 누르면 아래 종목 상세
 *   아래   종목 상세 — 누가 들고 있고 누가 늘리고 줄였나(규모) · 그때 샀다면 추정 손익(백테스트 전 = 자리만) · 토스 · 바이낸스 · 게이트 바로가기(없으면 회색)
 *
 * 🔴 13F 는 롱 보유만 · 분기 끝 45일 뒤 공개 — 화면마다 그 말을 단다. 이 화면엔 주문 경로가 없다.
 * 주소로 공유된다: `/whalesurfer?cik=…&cusip=…`.
 */

import type { ApexOptions } from "apexcharts";
import { useEffect, useMemo, useState } from "react";
import Chart from "react-apexcharts";
import { useSearchParams } from "react-router-dom";
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
import { apexBaseOptions } from "./chart/apexBase";
import { Button, Card, CardBody, Typography } from "./mt";
import { ErrorCard } from "./ui";

const TOP_SLICES = 12;

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
  held: "bg-blue-gray-50 text-blue-gray-600 dark:bg-gray-800 dark:text-blue-gray-300",
};

/** 달러를 짧게 — 1.2B · 340M · 12K. */
export function usdCompact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

export function pct(x: number, digits = 1): string {
  return `${(x * 100).toFixed(digits)}%`;
}

/** 사진이 없을 때 쓰는 머리글자 — "Warren Buffett" → WB. */
export function initials(person: string, label: string): string {
  const src = (person || label).trim();
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return parts
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

/** 원형 그래프 조각 — 상위 N 개 + "그 밖". 현물 줄만(Put/Call 은 비중이 아니라 방향 정보라 뺀다). */
export function slicesOf(view: WhaleManagerView | null, reportIdx: number) {
  const rep = view?.reports[reportIdx];
  if (!rep) return { labels: [] as string[], series: [] as number[], cusips: [] as (string | null)[] };
  const spot = rep.holdings.filter((h) => !h.put_call);
  const top = spot.slice(0, TOP_SLICES);
  const rest = spot.slice(TOP_SLICES).reduce((acc, h) => acc + h.weight, 0);
  const labels = top.map((h) => h.issuer);
  const series = top.map((h) => Math.round(h.weight * 10000) / 100);
  const cusips: (string | null)[] = top.map((h) => h.cusip);
  if (rest > 0) {
    labels.push(`그 밖 ${spot.length - top.length}종`);
    series.push(Math.round(rest * 10000) / 100);
    cusips.push(null);
  }
  return { labels, series, cusips };
}

function Avatar({ m, size = "h-9 w-9" }: { m: WhaleManager; size?: string }) {
  if (m.image) {
    return <img src={m.image} alt="" className={`${size} shrink-0 rounded-full object-cover`} />;
  }
  return (
    <span
      className={`${size} grid shrink-0 place-items-center rounded-full bg-blue-gray-100 text-xs font-bold text-blue-gray-700 dark:bg-gray-800 dark:text-blue-gray-200`}
      aria-hidden="true"
    >
      {initials(m.person, m.label)}
    </span>
  );
}

function LinkButton({ label, href }: { label: string; href: string | null }) {
  if (!href) {
    return (
      <Button size="sm" variant="outlined" disabled className="normal-case opacity-50" title="바로가기 주소를 아직 모른다 — T442 §5">
        {label}
      </Button>
    );
  }
  return (
    <a href={href} target="_blank" rel="noreferrer">
      <Button size="sm" variant="outlined" className="normal-case">
        {label} ↗
      </Button>
    </a>
  );
}

function StockDetail({ stock, loading, error }: { stock: WhaleStock | null; loading: boolean; error: string | null }) {
  if (error) return <ErrorCard message={error} title="종목 상세를 못 받았다" />;
  if (loading || !stock) {
    return (
      <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <CardBody className="p-5 text-sm text-blue-gray-500">종목 상세를 모으는 중 — 추적 보고자 전부의 최근 두 분기를 읽는다(처음엔 느리다).</CardBody>
      </Card>
    );
  }
  const c = stock.consensus;
  return (
    <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <CardBody className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <Typography variant="h5" color="blue-gray" className="dark:text-white">
              {stock.issuer ?? stock.cusip}
            </Typography>
            <div className="mt-1 text-xs text-blue-gray-500">
              CUSIP {stock.cusip}
              {stock.ticker ? ` · ${stock.ticker}` : " · 티커 모름(수동 표에 없음)"}
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
          <Stat label="그때 샀다면(추정 손익)" value="백테스트 전" hint={stock.estimate_note} />
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-blue-gray-500">
              <tr>
                <th className="py-2 pr-3">보고자</th>
                <th className="py-2 pr-3">기준 분기</th>
                <th className="py-2 pr-3">변화</th>
                <th className="py-2 pr-3 text-right">주 수</th>
                <th className="py-2 pr-3 text-right">직전 주 수</th>
                <th className="py-2 pr-3 text-right">가치</th>
                <th className="py-2 pr-3 text-right">그 보고서 안 비중</th>
              </tr>
            </thead>
            <tbody>
              {stock.holders.map((h) => (
                <tr key={`${h.cik}-${h.put_call ?? ""}`} className="border-t border-blue-gray-50 dark:border-gray-800">
                  <td className="py-2 pr-3">
                    <span className="font-medium">{h.person}</span>
                    <span className="ml-1 text-xs text-blue-gray-500">{h.label}</span>
                    {h.put_call ? <span className="ml-1 rounded bg-blue-gray-50 px-1 text-[10px] dark:bg-gray-800">{h.put_call}</span> : null}
                  </td>
                  <td className="py-2 pr-3 text-xs text-blue-gray-500">{h.period ?? "—"}</td>
                  <td className="py-2 pr-3">
                    <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${KIND_TONE[h.kind]}`}>{KIND_LABEL[h.kind]}</span>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">{h.shares.toLocaleString()}</td>
                  <td className="py-2 pr-3 text-right font-mono text-blue-gray-500">{h.prev_shares.toLocaleString()}</td>
                  <td className="py-2 pr-3 text-right font-mono">{usdCompact(h.value_usd)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{pct(h.weight)}</td>
                </tr>
              ))}
              {stock.holders.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-3 text-blue-gray-500">
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
        <div className="mt-3 text-xs text-blue-gray-500">{stock.disclaimer}</div>
      </CardBody>
    </Card>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-blue-gray-50 p-3 dark:border-gray-800" title={hint}>
      <div className="text-[11px] uppercase text-blue-gray-500">{label}</div>
      <div className="mt-1 text-base font-semibold text-blue-gray-900 dark:text-white">{value}</div>
    </div>
  );
}

export function WhaleSurferPage() {
  const [params, setParams] = useSearchParams();
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

  const select = (next: { cik?: string | null; cusip?: string | null }) => {
    const q = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) q.set(k, v);
      else q.delete(k);
    }
    setParams(q, { replace: true });
  };

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slices, consensus, consensusError]);

  return (
    <div className="grid gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
      {/* 왼쪽 — 인물 목록 */}
      <aside className="xl:sticky xl:top-4 xl:self-start">
        <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <CardBody className="p-3">
            <Typography variant="small" color="blue-gray" className="mb-2 px-1 font-black uppercase opacity-75 dark:text-blue-gray-200">
              추적 보고자 {managers.length}
            </Typography>
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
                          ? "bg-gray-900 text-white dark:bg-blue-gray-100 dark:text-gray-900"
                          : "text-blue-gray-700 hover:bg-blue-gray-50 dark:text-blue-gray-200 dark:hover:bg-gray-800"
                      }`}
                    >
                      <Avatar m={m} />
                      <span className="min-w-0">
                        <span className="block truncate font-semibold">{m.person}</span>
                        <span className={`block truncate text-[11px] ${on ? "opacity-80" : "text-blue-gray-500"}`}>{m.label}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </CardBody>
        </Card>
      </aside>

      {/* 가운데 · 아래 */}
      <div className="flex min-w-0 flex-col gap-4">
        <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <CardBody className="p-5">
            {current ? (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <Avatar m={current} size="h-12 w-12" />
                  <div>
                    <Typography variant="h5" color="blue-gray" className="dark:text-white">
                      {current.person}
                    </Typography>
                    <div className="text-xs text-blue-gray-500">
                      {current.label} · CIK {current.cik}
                      {report ? ` · ${report.entity}` : ""}
                    </div>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <label className="text-xs text-blue-gray-500" htmlFor="ws-period">
                    기준 분기
                  </label>
                  <select
                    id="ws-period"
                    className="rounded-lg border border-blue-gray-200 bg-white px-2 py-1 text-sm dark:border-gray-700 dark:bg-gray-800"
                    value={reportIdx}
                    onChange={(e) => setReportIdx(Number(e.target.value))}
                    disabled={!view}
                  >
                    {(view?.reports ?? []).map((r, i) => (
                      <option key={r.accession} value={i}>
                        {r.period ?? r.filed} (접수 {r.filed})
                      </option>
                    ))}
                  </select>
                  {report ? (
                    <span className="text-xs text-blue-gray-500">
                      보유 {report.n}줄 · 합계 {usdCompact(report.total_value_usd)}
                    </span>
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="text-sm text-blue-gray-500">왼쪽에서 보고자를 고른다.</div>
            )}
            {viewError ? (
              <div className="mt-3">
                <ErrorCard message={viewError} title="보고를 못 받았다" />
              </div>
            ) : null}
            {cik && !view && !viewError ? <div className="mt-3 text-sm text-blue-gray-500">EDGAR 에서 13F 를 읽는 중…</div> : null}

            {slices.series.length ? (
              <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
                <Chart type="donut" series={slices.series} options={options} height={380} />
                <div>
                  <div className="mb-2 text-xs font-bold uppercase text-blue-gray-500">직전 분기 대비 변화 (가치 순)</div>
                  <ul className="flex max-h-[22rem] flex-col gap-1 overflow-y-auto text-sm">
                    {(report?.changes ?? [])
                      .filter((c) => c.kind !== "held")
                      .slice(0, 30)
                      .map((c) => (
                        <li key={`${c.cusip}-${c.put_call ?? ""}`}>
                          <button
                            type="button"
                            onClick={() => select({ cusip: c.cusip })}
                            className="flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-blue-gray-50 dark:hover:bg-gray-800"
                          >
                            <span className="min-w-0 truncate">
                              {c.issuer}
                              {c.put_call ? <span className="ml-1 text-[10px] text-blue-gray-500">{c.put_call}</span> : null}
                            </span>
                            <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${KIND_TONE[c.kind]}`}>
                              {KIND_LABEL[c.kind]} {c.kind === "exited" ? "" : pct(c.weight)}
                            </span>
                          </button>
                        </li>
                      ))}
                    {report && report.changes.every((c) => c.kind === "held") ? (
                      <li className="px-2 text-blue-gray-500">직전 분기와 같거나 비교할 직전 분기가 없다.</li>
                    ) : null}
                  </ul>
                </div>
              </div>
            ) : null}
            <div className="mt-3 text-xs text-blue-gray-500">
              {disclaimer}
              {consensusError ? ` · 합의 자료 실패: ${consensusError}` : consensus ? ` · 합의 자료 ${consensus.managers}/${consensus.tracked} 보고자` : " · 합의 자료 모으는 중"}
            </div>
          </CardBody>
        </Card>

        {cusip ? <StockDetail stock={stock} loading={stockLoading} error={stockError} /> : null}
      </div>
    </div>
  );
}
