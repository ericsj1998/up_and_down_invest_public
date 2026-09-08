/**
 * 백테스트를 차트로 — 저장된 백테스트(E1)의 자본 곡선 · 종목 봉 · 매매 전부 (사용자 요구 2026-09-06).
 *
 * 자료는 `/evidence/backtest` · `/evidence/backtest/{id}` · `/evidence/backtest/{id}/candles`. 표의 행을 누르면 그 매매로
 * 차트가 움직이고 손절선·진입선·청산선·영역이 그 매매 것으로 바뀐다. 지표·표기 설정은 `IndicatorPanel`.
 *
 * ⚠️ 이 실행(2026-09-06)의 숫자는 문서(t200_analysis.md)의 E1 과 다르다 — 원재료(8월 15m parquet)가 재수신돼 6.67년으로
 * 늘었다. 화면은 둘을 나란히 두고 섞지 않는다 (ops_issues F7 · #26).
 */
import { useEffect, useMemo, useState } from "react";
import { EquityChart } from "../chart/EquityChart";
import { IndicatorPanel } from "../chart/IndicatorPanel";
import { useChartSettings } from "../chart/indicators";
import { PriceChart } from "../chart/PriceChart";
import {
  fmtPrice,
  reasonLabel,
  sideLabel,
  type TradeMark,
} from "../chart/trades";
import { Card, CardBody, Typography } from "../mt";
import {
  backtestCandles,
  backtestDetail,
  backtestList,
  fmtTs,
  holdLabel,
  toMultiple,
  toOhlc,
  toTradeMarks,
  type BacktestDetail as Detail,
  type BacktestSummary,
  type CandlesResponse,
} from "./charts";
import { mdd, pct } from "./model";
import { AuditNotice, Fact } from "../mtui";

const PAGE = 40;

export function BacktestPanel() {
  const [list, setList] = useState<BacktestSummary[] | null>(null);
  const [id, setId] = useState<string>("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [symbol, setSymbol] = useState("");
  const [candles, setCandles] = useState<CandlesResponse | null>(null);
  const [reason, setReason] = useState<string>("all");
  const [page, setPage] = useState(0);
  const [focus, setFocus] = useState<string | null>(null);
  const [settings] = useChartSettings();

  useEffect(() => {
    let alive = true;
    backtestList()
      .then((got) => {
        if (!alive) return;
        setList(got.backtests);
        // 감사가 없으면(가려진 목록) 공개 항목(견본)부터 — 가려진 E1 을 첫 화면으로 주지 않는다.
        const first =
          (got.redacted
            ? got.backtests.find((b) => !b.missing && b.public)
            : undefined) ?? got.backtests.find((b) => !b.missing);
        if (first) setId(first.id);
      })
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setDetail(null);
    backtestDetail(id)
      .then((got) => {
        if (!alive) return;
        setDetail(got);
        setSymbol(got.symbols?.[0] ?? "");
        setFocus(null);
        setPage(0);
      })
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [id]);

  useEffect(() => {
    if (!id || !symbol) return;
    let alive = true;
    setCandles(null);
    backtestCandles(id, symbol)
      .then((got) => alive && setCandles(got))
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [id, symbol]);

  const bars = useMemo(
    () => (candles ? toOhlc(candles.candles) : []),
    [candles],
  );
  const symbolTrades = useMemo<TradeMark[]>(
    () => (candles ? toTradeMarks(candles.trades) : []),
    [candles],
  );
  const allTrades = useMemo<TradeMark[]>(
    () => (detail ? toTradeMarks(detail.trades) : []),
    [detail],
  );
  const reasons = useMemo(() => {
    const by = new Map<string, number>();
    for (const t of allTrades) by.set(t.reason, (by.get(t.reason) ?? 0) + 1);
    return [...by.entries()].sort((a, b) => b[1] - a[1]);
  }, [allTrades]);
  const rows = useMemo(() => {
    const filtered = allTrades.filter(
      (t) => t.symbol === symbol && (reason === "all" || t.reason === reason),
    );
    // 강제청산·손절이 먼저 보이게 — 사람이 찾는 것은 잘 된 매매가 아니라 잘못된 자리다.
    return filtered.sort((a, b) => a.openedTs - b.openedTs);
  }, [allTrades, symbol, reason]);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE);
  const equityLines = useMemo(
    () =>
      detail && detail.equity.value
        ? [
            {
              label: "자본 (×)",
              color: "#0f7b6c",
              values: toMultiple(detail.equity.value),
              width: 2 as const,
            },
          ]
        : [],
    [detail],
  );
  const liqMarks = useMemo(
    () =>
      allTrades
        .filter((t) => t.reason === "liq")
        .map((t) => ({
          time: t.closedTs,
          label: `청산 ${t.symbol.replace("USDT", "")}`,
        })),
    [allTrades],
  );

  if (error) {
    return (
      <div
        className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss"
        role="alert"
      >
        백테스트 차트 자료를 못 읽었다 — {error}
      </div>
    );
  }
  if (!list) return <p className="faint">백테스트 목록을 읽는 중…</p>;
  if (list.every((b) => b.missing)) {
    return (
      <Card className="border border-dashed border-blue-gray-200 shadow-none dark:border-gray-700 dark:bg-gray-900">
        <CardBody className="p-4 text-sm text-blue-gray-600 dark:text-blue-gray-300">
          저장된 백테스트 차트 자료가 없다 — 연구 PC 에서 `backtest_paths.py` 를
          돌려 `config/evidence/backtest_*.json` 을 만든다.
        </CardBody>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div
        className="flex flex-wrap items-center gap-2"
        role="tablist"
        aria-label="백테스트"
      >
        {list.map((b) => (
          <button
            key={b.id}
            type="button"
            role="tab"
            aria-selected={b.id === id}
            disabled={b.missing}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${
              b.id === id
                ? "border-gray-900 bg-gray-900 text-white dark:border-blue-gray-100 dark:bg-blue-gray-100 dark:text-gray-900"
                : "border-blue-gray-200 text-blue-gray-700 hover:bg-blue-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
            }`}
            onClick={() => setId(b.id)}
          >
            {b.label ?? b.id}
          </button>
        ))}
      </div>

      {!detail ? (
        <p className="faint">
          백테스트 자료(매매{" "}
          {list
            .find((b) => b.id === id)
            ?.trades_count?.toLocaleString("ko-KR") ?? "—"}
          건)를 읽는 중…
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <Fact
              label="구간"
              value={`${detail.years ?? "—"}년 · ${detail.frame ?? ""}`}
            />
            <Fact
              label="전체 수익률"
              value={pct(detail.total_pct)}
              tone={detail.total_pct ?? 0}
            />
            <Fact label="CAGR" value={pct(detail.cagr_pct, 0)} />
            <Fact
              label="MDD"
              value={mdd(detail.mdd_pct)}
              className="text-loss"
            />
            <Fact
              label="매매 · 강제청산"
              value={`${(detail.trades_count ?? 0).toLocaleString("ko-KR")} · ${detail.liquidations ?? 0}건`}
            />
            <Fact label="칼마" value={detail.calmar?.toFixed(2) ?? "—"} />
          </div>
          {detail.redacted ? (
            <AuditNotice what="전체 수익률 · CAGR · 칼마 · 자본 곡선 · 매매별 손익" />
          ) : null}
          {detail.documented ? (
            <p className="rounded-lg bg-blue-gray-50/60 px-3 py-2 text-xs leading-relaxed text-blue-gray-600 dark:bg-gray-800 dark:text-blue-gray-300">
              <b>2026-09-06 재실행값이다.</b> 문서의 E1 은{" "}
              {pct(detail.documented.total_pct)} · MDD{" "}
              {mdd(detail.documented.mdd_pct)} · {detail.documented.years}년 ·
              매매 {detail.documented.trades_count.toLocaleString("ko-KR")} —
              다른 이유: {detail.documented.note}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-1 text-[11px]">
            {reasons.map(([r, n]) => (
              <button
                key={r}
                type="button"
                className={`rounded-md px-2 py-0.5 ${
                  reason === r
                    ? "bg-gray-900 text-white dark:bg-blue-gray-100 dark:text-gray-900"
                    : r === "liq"
                      ? "bg-loss-wash text-loss"
                      : "bg-blue-gray-50 text-blue-gray-700 dark:bg-gray-800 dark:text-blue-gray-200"
                }`}
                onClick={() => {
                  setReason(reason === r ? "all" : r);
                  setPage(0);
                }}
              >
                {reasonLabel(r)} {n.toLocaleString("ko-KR")}
              </button>
            ))}
          </div>

          <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <CardBody className="p-4">
              <div className="mb-1 text-sm font-semibold text-blue-gray-800 dark:text-blue-gray-100">
                자본 곡선 (로그 배수 · 붉은 네모 = 강제청산)
              </div>
              {detail.equity.value ? (
                <EquityChart
                  t={detail.equity.t}
                  lines={equityLines}
                  marks={liqMarks}
                />
              ) : (
                <AuditNotice what="자본 곡선" />
              )}
            </CardBody>
          </Card>

          <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <CardBody className="p-4">
              <div
                className="mb-2 flex flex-wrap items-center gap-1"
                role="tablist"
                aria-label="종목"
              >
                {(detail.symbols ?? []).map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="tab"
                    aria-selected={s === symbol}
                    className={`rounded-md border px-2 py-0.5 text-xs ${
                      s === symbol
                        ? "border-gray-900 bg-gray-900 text-white dark:border-blue-gray-100 dark:bg-blue-gray-100 dark:text-gray-900"
                        : "border-blue-gray-200 text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
                    }`}
                    onClick={() => {
                      setSymbol(s);
                      setFocus(null);
                      setPage(0);
                    }}
                  >
                    {s.replace("USDT", "")}{" "}
                    <span className="text-blue-gray-400">
                      {detail.trades_by_symbol?.[s] ?? ""}
                    </span>
                  </button>
                ))}
              </div>
              {candles ? (
                <PriceChart
                  bars={bars}
                  step={14_400}
                  trades={symbolTrades}
                  focusId={focus}
                  settings={settings}
                  note={`${symbol} · ${candles.frame} · ${bars.length.toLocaleString("ko-KR")}봉 · 매매 ${symbolTrades.length} · BINANCE 실측`}
                />
              ) : (
                <p className="faint">봉을 읽는 중…</p>
              )}
              <p className="mt-1 text-[11px] text-blue-gray-500 dark:text-blue-gray-300">
                아래 표의 행을 누르면 그 매매로 이동한다 —
                손절선·진입선·청산선과 손익 영역은 선택한 매매 것이다. 마우스
                휠로 확대·이동.
              </p>
              <div className="mt-3">
                <IndicatorPanel compact />
              </div>
            </CardBody>
          </Card>

          <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <CardBody className="p-0">
              <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-4">
                <Typography
                  variant="small"
                  className="font-semibold text-blue-gray-800 dark:text-blue-gray-100"
                >
                  매매 {rows.length.toLocaleString("ko-KR")}건 —{" "}
                  {symbol.replace("USDT", "")}
                  {reason !== "all" ? ` · ${reasonLabel(reason)}` : ""}
                </Typography>
                <div className="flex items-center gap-1 text-xs">
                  <button
                    type="button"
                    className="rounded-md border border-blue-gray-200 px-2 py-0.5 disabled:opacity-40 dark:border-gray-700"
                    disabled={page === 0}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                  >
                    ‹
                  </button>
                  <span className="font-mono">
                    {page + 1} / {pages}
                  </span>
                  <button
                    type="button"
                    className="rounded-md border border-blue-gray-200 px-2 py-0.5 disabled:opacity-40 dark:border-gray-700"
                    disabled={page >= pages - 1}
                    onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                  >
                    ›
                  </button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
                      <th className="px-3 py-2">진입 (UTC)</th>
                      <th className="px-3 py-2">다리</th>
                      <th className="px-3 py-2">방향</th>
                      <th className="px-3 py-2 text-right">진입가</th>
                      <th className="px-3 py-2 text-right">손절선</th>
                      <th className="px-3 py-2 text-right">청산가</th>
                      <th className="px-3 py-2">사유</th>
                      <th className="px-3 py-2 text-right">보유</th>
                      <th className="px-3 py-2 text-right">손익 (USDT)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((t) => (
                      <tr
                        key={t.id}
                        className={`cursor-pointer border-b border-blue-gray-50 hover:bg-blue-gray-50/60 dark:border-gray-800 dark:hover:bg-gray-800 ${
                          focus === t.id ? "bg-sky-wash dark:bg-gray-800" : ""
                        }`}
                        onClick={() => setFocus(t.id)}
                      >
                        <td className="px-3 py-1.5 font-mono text-xs">
                          {fmtTs(t.openedTs)}
                        </td>
                        <td className="px-3 py-1.5 font-mono text-xs text-blue-gray-500">
                          {t.leg}
                        </td>
                        <td
                          className={`px-3 py-1.5 text-xs font-semibold ${t.side === 1 ? "text-gain" : "text-loss"}`}
                        >
                          {sideLabel(t.side)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-xs">
                          {fmtPrice(t.entry)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-xs text-loss">
                          {fmtPrice(t.stop)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-xs">
                          {fmtPrice(t.exit)}
                        </td>
                        <td
                          className={`px-3 py-1.5 text-xs ${t.reason === "liq" ? "font-semibold text-loss" : ""}`}
                        >
                          {reasonLabel(t.reason)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-xs">
                          {holdLabel(t.openedTs, t.closedTs)}
                        </td>
                        <td
                          className={`px-3 py-1.5 text-right font-mono text-xs ${t.pnl === null ? "text-blue-gray-400" : t.pnl >= 0 ? "text-gain" : "text-loss"}`}
                        >
                          {t.pnl === null
                            ? "🔒"
                            : `${t.pnl >= 0 ? "+" : ""}${t.pnl.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}
