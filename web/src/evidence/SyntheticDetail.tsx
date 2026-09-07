/**
 * 합성 미래 하나의 상세 — 그 미래가 **어떤 모양**이었고 **어디서 청산을 당했나** (사용자 요구 2026-09-06).
 *
 * 위: 자본 곡선(펀드 vs 시장 지수 · 청산 표식) · 아래: 청산 매매 표("어디서") · 종목 봉 차트(일봉 전체 / 청산 전후 4h 창).
 * 자료는 `/evidence/synthetic/{k}` · `/evidence/synthetic/{k}/candles`. 지표·표기 설정은 `IndicatorPanel` 이 든다.
 */
import { useEffect, useMemo, useState } from "react";
import { EquityChart } from "../chart/EquityChart";
import { IndicatorPanel } from "../chart/IndicatorPanel";
import { useChartSettings } from "../chart/indicators";
import { PriceChart } from "../chart/PriceChart";
import { beyondStopPct, fmtPrice, reasonLabel, sideLabel, type TradeMark } from "../chart/trades";
import { Card, CardBody, Typography } from "../mt";
import {
  fmtTs,
  holdLabel,
  logToMultiple,
  syntheticCandles,
  syntheticDetail,
  syntheticList,
  toMultiple,
  toOhlc,
  toTradeMarks,
  type CandlesResponse,
  type SyntheticDetail as Detail,
  type SyntheticList,
} from "./charts";
import { mdd, pct } from "./model";
import { AuditNotice, Fact } from "../mtui";

/** 고른 미래 — 표에서 왔으면 시나리오·씨앗, 주소(`?future=<k>`)에서 왔으면 번호. */
export type Pick = { scenario: string; seed: number } | { k: number };

type View = { kind: "daily" } | { kind: "window"; closedTs: number };

export function SyntheticDetailPanel({ pick, onClose }: { pick: Pick; onClose: () => void }) {
  const [list, setList] = useState<SyntheticList | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [symbol, setSymbol] = useState<string>("");
  const [view, setView] = useState<View>({ kind: "daily" });
  const [candles, setCandles] = useState<CandlesResponse | null>(null);
  // 봉 읽기 실패는 **차트 칸 안에서만** 말한다 — 패널 전체를 오류로 바꾸면 자본 곡선·청산 표까지 사라진다.
  const [candlesError, setCandlesError] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const [settings] = useChartSettings();

  useEffect(() => {
    let alive = true;
    syntheticList()
      .then((got) => alive && setList(got))
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, []);

  const k = useMemo(() => {
    if ("k" in pick) return list && pick.k >= 0 && pick.k < list.futures.length ? pick.k : null;
    const row = list?.futures.find((f) => f.scenario === pick.scenario && f.seed === pick.seed);
    return row?.k ?? null;
  }, [list, pick]);

  useEffect(() => {
    if (k === null) return;
    let alive = true;
    setDetail(null);
    setCandles(null);
    setCandlesError("");
    // 🔴 미래를 바꾸면 종목·창을 **먼저** 비운다 (실측 2026-09-06: "그 시각의 청산 창이 없다: NEARUSDT @ …").
    //    비우지 않으면 아래 봉 효과가 새 미래 번호 + 옛 미래의 청산 시각으로 요청해 404 가 났고, 그 오류가
    //    패널 전체를 덮었다. 종목이 비면 봉 효과는 아무것도 안 한다.
    setSymbol("");
    setView({ kind: "daily" });
    setFocus(null);
    syntheticDetail(k)
      .then((got) => {
        if (!alive) return;
        setDetail(got);
        // 청산이 있으면 그 종목·그 창부터 — 사용자가 보려는 것이 그것이다.
        const first = got.liquidation_trades[0];
        if (first) {
          setSymbol(first.symbol);
          setView({ kind: "window", closedTs: first.closed_ts });
        } else {
          setSymbol(got.symbols[0] ?? "");
          setView({ kind: "daily" });
        }
        setFocus(null);
      })
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [k]);

  useEffect(() => {
    if (k === null || !symbol) return;
    let alive = true;
    const window = view.kind === "window" ? view.closedTs : undefined;
    setCandlesError("");
    syntheticCandles(k, symbol, window)
      .then((got) => alive && setCandles(got))
      .catch((exc: unknown) => alive && setCandlesError(String(exc)));
    return () => {
      alive = false;
    };
  }, [k, symbol, view]);

  const bars = useMemo(() => (candles ? toOhlc(candles.candles) : []), [candles]);
  const trades = useMemo<TradeMark[]>(() => (candles ? toTradeMarks(candles.trades) : []), [candles]);
  const step = candles?.frame === "1d" ? 86_400 : 14_400;
  // 창을 보고 있으면 그 청산 매매를 선택한다 — 가로선·영역이 그 매매 것이다.
  const focusId = useMemo(() => {
    if (focus) return focus;
    if (view.kind === "window") return trades.find((t) => t.closedTs === view.closedTs)?.id ?? null;
    return trades.length === 1 ? (trades[0]?.id ?? null) : null;
  }, [focus, view, trades]);

  const allLiqs = useMemo<TradeMark[]>(() => (detail ? toTradeMarks(detail.liquidation_trades) : []), [detail]);
  const equityLines = useMemo(() => {
    if (!detail) return [];
    return [
      // 펀드 자본은 손익이라 감사가 아니면 null — 시장 지수만 그린다.
      ...(detail.equity.fund ? [{ label: "펀드 자본 (×)", color: "#0f7b6c", values: toMultiple(detail.equity.fund), width: 2 as const }] : []),
      { label: "시장 지수 (×, 6종 동일가중)", color: "#607d8b", values: logToMultiple(detail.equity.market_log), width: 1 as const, dashed: true },
    ];
  }, [detail]);
  const equityMarks = useMemo(
    () => allLiqs.map((t) => ({ time: t.closedTs, label: `청산 ${t.symbol.replace("USDT", "")}` })),
    [allLiqs],
  );

  if (error) {
    return (
      <div className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss" role="alert">
        미래 상세를 못 읽었다 — {error}
      </div>
    );
  }
  if (list && k === null) {
    return (
      <Card className="border border-dashed border-blue-gray-200 shadow-none dark:border-gray-700 dark:bg-gray-900">
        <CardBody className="p-4 text-sm text-blue-gray-600 dark:text-blue-gray-300">
          이 미래({"k" in pick ? `#${pick.k}` : `${pick.scenario} s${pick.seed}`})는 상세 파일에 없다 — 상세는 2026-09-06 재생성
          세트(b15-90-r2)만 저장돼 있다. 그 탭에서 고르면 차트가 나온다.
          <button type="button" className="ml-2 underline" onClick={onClose}>
            닫기
          </button>
        </CardBody>
      </Card>
    );
  }
  if (!detail) return <p className="faint">미래 상세를 읽는 중…</p>;

  const reasons = Object.entries(detail.stats.by_reason ?? {}).sort((a, b) => b[1] - a[1]);
  const totalTrades = reasons.reduce((acc, [, n]) => acc + n, 0);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <Typography variant="h6" color="blue-gray" className="dark:text-white">
            미래 #{detail.k} · {detail.scenario} · 씨앗 {detail.seed}
          </Typography>
          <p className="mt-0.5 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            합성 자료다 — 실측 봉을 15~90일 블록으로 다시 엮고 드리프트를 얹은 가상의 4.62년. 아래 봉·매매는 그 세상에서 도구가 한 것이다.
          </p>
        </div>
        <button
          type="button"
          className="rounded-lg border border-blue-gray-200 px-3 py-1 text-xs text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
          onClick={onClose}
        >
          닫기
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="전체 수익률" value={pct(detail.total_pct)} tone={detail.total_pct ?? 0} />
        <Fact label="MDD" value={mdd(detail.mdd_pct)} className="text-loss" />
        <Fact label="강제청산" value={`${detail.liquidations}건`} className={detail.liquidations > 0 ? "text-loss" : ""} />
        <Fact label="매매" value={`${totalTrades.toLocaleString("ko-KR")}건`} />
      </div>
      {detail.redacted ? <AuditNotice what="전체 수익률 · 펀드 자본 곡선 · 매매별 손익" /> : null}
      <div className="flex flex-wrap gap-1 text-[11px]">
        {reasons.map(([r, n]) => (
          <span
            key={r}
            className={`rounded-md px-2 py-0.5 ${r === "liq" ? "bg-loss-wash text-loss" : "bg-blue-gray-50 text-blue-gray-700 dark:bg-gray-800 dark:text-blue-gray-200"}`}
          >
            {reasonLabel(r)} {n}
          </span>
        ))}
      </div>

      <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <CardBody className="p-4">
          <div className="mb-1 text-sm font-semibold text-blue-gray-800 dark:text-blue-gray-100">자본 곡선 — 펀드 vs 시장 (로그 배수 · 붉은 네모 = 강제청산)</div>
          <EquityChart t={detail.equity.t} lines={equityLines} marks={equityMarks} />
        </CardBody>
      </Card>

      <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <CardBody className="p-4">
          <div className="mb-2 text-sm font-semibold text-blue-gray-800 dark:text-blue-gray-100">
            청산을 어디서 당했나 — {allLiqs.length ? `${allLiqs.length}건` : "이 미래에는 강제청산이 없다"}
          </div>
          {allLiqs.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
                    <th className="px-2 py-2">종목 · 다리</th>
                    <th className="px-2 py-2">방향</th>
                    <th className="px-2 py-2">진입 (UTC)</th>
                    <th className="px-2 py-2 text-right">진입가</th>
                    <th className="px-2 py-2 text-right">손절선</th>
                    <th className="px-2 py-2">청산 (UTC)</th>
                    <th className="px-2 py-2 text-right">청산가</th>
                    <th className="px-2 py-2 text-right">손절선 넘김</th>
                    <th className="px-2 py-2 text-right">보유</th>
                    <th className="px-2 py-2 text-right">손익</th>
                  </tr>
                </thead>
                <tbody>
                  {allLiqs.map((t) => (
                    <tr
                      key={t.id}
                      className={`cursor-pointer border-b border-blue-gray-50 hover:bg-blue-gray-50/60 dark:border-gray-800 dark:hover:bg-gray-800 ${
                        symbol === t.symbol && view.kind === "window" && view.closedTs === t.closedTs ? "bg-loss-wash/50 dark:bg-gray-800" : ""
                      }`}
                      onClick={() => {
                        setSymbol(t.symbol);
                        setView({ kind: "window", closedTs: t.closedTs });
                        setFocus(null);
                      }}
                    >
                      <td className="px-2 py-1.5 text-xs font-medium text-blue-gray-900 dark:text-white">
                        {t.symbol} <span className="font-mono text-blue-gray-400">{t.leg}</span>
                      </td>
                      <td className={`px-2 py-1.5 text-xs font-semibold ${t.side === 1 ? "text-gain" : "text-loss"}`}>{sideLabel(t.side)}</td>
                      <td className="px-2 py-1.5 font-mono text-xs">{fmtTs(t.openedTs)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-xs">{fmtPrice(t.entry)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-xs text-loss">{fmtPrice(t.stop)}</td>
                      <td className="px-2 py-1.5 font-mono text-xs">{fmtTs(t.closedTs)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-xs">{fmtPrice(t.exit)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-xs text-loss">+{beyondStopPct(t).toFixed(1)}%</td>
                      <td className="px-2 py-1.5 text-right font-mono text-xs">{holdLabel(t.openedTs, t.closedTs)}</td>
                      <td className="px-2 py-1.5 text-right font-mono text-xs text-loss">{t.pnl === null ? "🔒" : t.pnl.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-[11px] leading-relaxed text-blue-gray-500 dark:text-blue-gray-300">
                "손절선 넘김" 은 청산가가 손절선을 지나 얼마나 더 갔나다 — 손절이 걸리기 전에 한 봉 안에서 가격이 그만큼 뛰어 증거금이 먼저
                바닥났다는 뜻이다. 행을 누르면 그 청산 전후 ±45일(4h 봉)이 아래에 나온다. "같은 봉" 은 진입한 봉에서 바로 당한 것이다.
              </p>
            </div>
          ) : (
            <p className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
              손절(hard_sl)·약화 청산으로만 나갔다. 아래에서 종목 일봉 전체를 본다 — 이 미래의 모양이다.
            </p>
          )}
        </CardBody>
      </Card>

      <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <CardBody className="p-4">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1" role="tablist" aria-label="종목">
              {detail.symbols.map((s) => (
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
                    const liq = allLiqs.find((t) => t.symbol === s);
                    setView(liq ? { kind: "window", closedTs: liq.closedTs } : { kind: "daily" });
                  }}
                >
                  {s.replace("USDT", "")}
                  {allLiqs.some((t) => t.symbol === s) ? <span className="ml-1 text-loss">■</span> : null}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1 text-xs">
              <button
                type="button"
                className={`rounded-md border px-2 py-0.5 ${view.kind === "daily" ? "border-gray-900 bg-gray-900 text-white dark:border-blue-gray-100 dark:bg-blue-gray-100 dark:text-gray-900" : "border-blue-gray-200 text-blue-gray-700 dark:border-gray-700 dark:text-blue-gray-200"}`}
                onClick={() => setView({ kind: "daily" })}
              >
                일봉 4.62년 전체
              </button>
              {allLiqs
                .filter((t) => t.symbol === symbol)
                .map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={`rounded-md border px-2 py-0.5 ${
                      view.kind === "window" && view.closedTs === t.closedTs
                        ? "border-loss bg-loss text-white"
                        : "border-blue-gray-200 text-blue-gray-700 dark:border-gray-700 dark:text-blue-gray-200"
                    }`}
                    onClick={() => setView({ kind: "window", closedTs: t.closedTs })}
                  >
                    청산 전후 4h · {fmtTs(t.closedTs).slice(0, 10)}
                  </button>
                ))}
            </div>
          </div>
          {candlesError ? (
            <div className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss" role="alert">
              봉을 못 읽었다 — {candlesError}
            </div>
          ) : candles ? (
            <PriceChart
              bars={bars}
              step={step}
              trades={trades}
              focusId={focusId}
              settings={settings}
              note={`${symbol} · ${candles.frame} · ${bars.length.toLocaleString("ko-KR")}봉 · 합성`}
            />
          ) : (
            <p className="faint">봉을 읽는 중…</p>
          )}
          <div className="mt-3">
            <IndicatorPanel compact />
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
