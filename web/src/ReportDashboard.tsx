/**
 * 리포트 대시보드 — **한 화면** (T220 2단계 · 사용자 2026-09-05: *"딱 한 화면에 대시보드 형태로 싹 보이게"*).
 *
 * 🔴 숫자는 전부 서버(`/report/dashboard`)에서 온다 — 메일과 **같은 함수**가 만든 값이다. 화면은 계산하지 않고
 *    그린다. 못 읽은 값은 "—" (0 으로 꾸미지 않는다 · 규칙 #8).
 *
 * 규칙: 손익은 **금액 + %** 병기 · 수익률엔 **MDD 병기** · 누적 곡선은 원장 청산 순서의 합(복리 아님).
 *
 * ⚠️ 폴링은 60초 — 계좌·거래소 I/O 가 붙은 무거운 요청이다. 기간을 바꾸면 즉시 한 번 받는다.
 */

import {
  ArrowTrendingDownIcon,
  BanknotesIcon,
  ReceiptPercentIcon,
  ScaleIcon,
} from "@heroicons/react/24/solid";
import type { ApexOptions } from "apexcharts";
import { useEffect, useMemo, useState } from "react";
import Chart from "react-apexcharts";
import { Link, useSearchParams } from "react-router-dom";
import { reportDashboard, type ReportDashboard as Data } from "./api";
import { Card, CardBody, CardHeader, Typography } from "./mt";
import { ReportMailCard } from "./ReportTab";
import { useOpenRuns } from "./shell/openRuns";
import { useThemeValue } from "./shell/theme";
import { ChartCard, Fact } from "./mtui";
import { apexBaseOptions } from "./chart/apexBase";
import { walletLayers, windowGain } from "./reportWallet";

const POLL_MS = 60_000;

/** 기간 선택지 — 서버 `hours` 는 1~744. */
const PERIODS = [
  { hours: 24, label: "24시간" },
  { hours: 168, label: "7일" },
  { hours: 720, label: "30일" },
] as const;

/** 서버 Decimal 문자열 → 수. null 은 그대로 (모른다). */
function n(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function usdt(value: number | null, digits = 2): string {
  return value === null
    ? "—"
    : `${value.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits })} USDT`;
}

function pct(value: number | null, digits = 2): string {
  return value === null
    ? "—"
    : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function tone(value: number | null): string {
  if (value === null || value === 0)
    return "text-blue-gray-900 dark:text-white";
  return value > 0 ? "text-gain" : "text-loss";
}

export function ReportDashboard() {
  // 🔴 밝기를 구독한다 (2026-09-12). ApexCharts 는 옵션을 그릴 때 한 번 읽으므로, 토글을 눌러도
  //    이 화면이 다시 그려지지 않으면 차트가 옛 밝기를 쥔 채 남는다 — 어두운 바탕에 어두운 축
  //    글씨. 값이 바뀌면 아래 `apexBaseOptions()` 가 새 밝기로 다시 계산된다.
  useThemeValue();
  // ⭐ 기간은 주소에 있다 (`/report?hours=168`) — 새로고침·링크 공유에 살아남는다 (탭은 주소다 규칙).
  const [params, setParams] = useSearchParams();
  const fromUrl = Number(params.get("hours"));
  const hours = PERIODS.some((p) => p.hours === fromUrl) ? fromUrl : 24;
  const setHours = (next: number) =>
    setParams(next === 24 ? {} : { hours: String(next) }, { replace: true });
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const pull = () => {
      reportDashboard(hours)
        .then((got) => {
          if (!alive) return;
          setData(got);
          setError("");
        })
        .catch((exc: unknown) => alive && setError(String(exc)))
        .finally(() => alive && setLoading(false));
    };
    setLoading(true);
    pull();
    const timer = setInterval(pull, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [hours]);

  // 24시간에 청산이 없으면 차트가 비는데, 그때 "7일로 보기" 한 번이 답이다 (UX 점검). 이미 넓으면 안 준다.
  const widen = hours === 24 ? () => setHours(168) : undefined;

  const led = data?.ledger;
  const ex = data?.exchange ?? null;
  const acct = data?.account ?? null;
  const pnl = n(ex?.pnl);
  const gainSum = n(led?.gain_sum_pct);
  const mdd = n(led?.max_drawdown_pct);
  const winRate = n(led?.win_rate);
  const costs = ex ? (n(ex.fees) ?? 0) + (n(ex.funding) ?? 0) : null;

  return (
    <div className="flex flex-col gap-5">
      {/* 기간 · 대조 상태 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          className="inline-flex rounded-lg border border-blue-gray-100 bg-white p-1 dark:border-gray-800 dark:bg-gray-900"
          role="tablist"
          aria-label="기간"
        >
          {PERIODS.map((p) => (
            <button
              key={p.hours}
              type="button"
              role="tab"
              aria-selected={hours === p.hours}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                hours === p.hours
                  ? "bg-gray-900 text-white dark:bg-blue-gray-100 dark:text-gray-900"
                  : "text-blue-gray-700 hover:bg-blue-gray-50 dark:text-blue-gray-200 dark:hover:bg-gray-800"
              }`}
              onClick={() => setHours(p.hours)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {data ? (
            <span
              className={`rounded-full px-2.5 py-1 font-medium ${data.diverged ? "bg-loss-wash text-loss" : "bg-gain-wash text-gain"}`}
              title={data.note}
            >
              {data.diverged ? "🔴 원장·거래소 불일치" : "✅ 원장·거래소 일치"}
            </span>
          ) : null}
          {data ? (
            <span className="text-blue-gray-500 dark:text-blue-gray-300">
              {new Date(data.window.since).toLocaleString("ko-KR", {
                timeZone: "Asia/Seoul",
              })}{" "}
              ~{" "}
              {new Date(data.window.until).toLocaleString("ko-KR", {
                timeZone: "Asia/Seoul",
              })}{" "}
              KST
            </span>
          ) : null}
          {loading ? (
            <span className="text-blue-gray-400">읽는 중…</span>
          ) : null}
        </div>
      </div>

      {error ? (
        <div
          className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss"
          role="alert"
        >
          리포트를 못 읽었다 — {error}
        </div>
      ) : null}

      {/* 성과 카드 4장 — 금액+% · MDD 병기 */}
      <div className="grid grid-cols-2 gap-2 sm:gap-4 xl:grid-cols-4">
        {/* 두 출처를 문장으로 가른다 (UX 점검) — 거래소가 말하는 실현 금액 · 원장이 계산한 손익률 합. */}
        <Stat
          icon={<BanknotesIcon className="h-6 w-6 text-white" />}
          color="bg-gray-900"
          title="실현 손익 — 거래소가 말하는 값"
          value={<span className={tone(pnl)}>{usdt(pnl)}</span>}
          footer={
            <>
              원장 계산: <b className={tone(gainSum)}>{pct(gainSum)}</b> (건별
              손익률 합 · 복리 아님)
              {ex ? ` · 거래소 행 ${ex.rows}` : " · 거래소 못 읽음"}
            </>
          }
        />
        <Stat
          icon={<ArrowTrendingDownIcon className="h-6 w-6 text-white" />}
          color="bg-loss"
          title="최대 낙폭 (MDD)"
          value={
            <span>
              {mdd === null
                ? "—"
                : mdd === 0
                  ? "0.00%"
                  : `−${Math.abs(mdd).toFixed(2)}%`}
            </span>
          }
          footer={<>원장 손익률 합 경로의 고점 대비 · 구간 안</>}
        />
        <Stat
          icon={<ScaleIcon className="h-6 w-6 text-white" />}
          color="bg-gain"
          title="매매 · 승률"
          value={
            <span>
              {led ? `${led.trades}건` : "—"}
              {winRate !== null ? (
                <span className="ml-2 text-base font-medium text-blue-gray-600 dark:text-blue-gray-300">
                  승률 {winRate.toFixed(0)}%
                </span>
              ) : null}
            </span>
          }
          footer={
            led ? (
              <>
                롱 {led.longs} · 숏 {led.shorts} · 승 {led.wins} · 평균 손익비{" "}
                {n(led.mean_rr) === null ? "—" : n(led.mean_rr)?.toFixed(2)}
              </>
            ) : (
              "—"
            )
          }
        />
        <Stat
          icon={<ReceiptPercentIcon className="h-6 w-6 text-white" />}
          color="bg-blue-gray-700"
          title="수수료 + 펀딩 (거래소)"
          value={<span className={tone(costs)}>{usdt(costs, 4)}</span>}
          footer={
            ex ? (
              <>
                수수료 {usdt(n(ex.fees), 4)} · 펀딩 {usdt(n(ex.funding), 4)}
              </>
            ) : (
              "거래소 못 읽음"
            )
          }
        />
      </div>

      {/* 계좌 — 거래소가 말하는 사실 */}
      <div className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Fact label="계좌 총액" value={usdt(n(acct?.total))} />
        <Fact label="가용" value={usdt(n(acct?.available))} />
        <Fact label="포지션 증거금" value={usdt(n(acct?.locked))} />
        <Fact
          label="미실현 손익"
          value={usdt(n(acct?.unrealized))}
          className={tone(n(acct?.unrealized))}
        />
        <Fact
          label="금고 잔고"
          value={usdt(n(acct?.vault))}
          hint="수익선 넘겨 빼 둔 돈"
        />
        <Fact label="살아 있는 판" value={acct ? `${acct.runs}개` : "—"} />
      </div>

      {/* 펀드 종합 — 계좌와 판 사이의 중간 층 (사용자 2026-09-07) */}
      <FundsSection data={data} />

      {/* 차트 */}
      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard
          title={`계좌 총액 · ${PERIODS.find((p) => p.hours === hours)?.label ?? `${hours}시간`} (지갑 잔고 · 회색 넣은 돈 · 초록 번 돈 · 빨강 잃은 돈)`}
          className="xl:col-span-3"
        >
          <EquityChart data={data} />
        </ChartCard>
      </div>
      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard
          title="누적 손익률 (원장 · 청산 순서 합)"
          className="xl:col-span-2"
        >
          <CurveChart data={data} widen={widen} />
        </ChartCard>
        <ChartCard title="결과 분포">
          <OutcomeChart data={data} widen={widen} />
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard title="판별 손익률 (구간)" className="xl:col-span-1">
          <RunsBar data={data} widen={widen} />
        </ChartCard>
        <Card className="border border-blue-gray-100 shadow-sm xl:col-span-2 dark:border-gray-800 dark:bg-gray-900">
          <CardBody className="p-0">
            <div className="px-5 pt-5">
              <Typography
                variant="h6"
                color="blue-gray"
                className="dark:text-white"
              >
                판 — 구간에 활동한 라이브 판
              </Typography>
            </div>
            <RunsTable data={data} />
          </CardBody>
        </Card>
      </div>

      {/* 외부 전송 — 미리보기 → 확인 → 보내기 (되돌릴 수 없다). 콘솔·판 화면의 "지금 보내기"를 여기로 모았다 —
          판 고르기로 "이 판만" 도 된다 (UX 점검 2026-09-05). */}
      <ReportMailCard
        hours={hours}
        symbols={[
          ...new Set(
            (data?.runs ?? []).filter((r) => r.trades > 0).map((r) => r.symbol),
          ),
        ]}
        runsCount={data?.runs.length ?? 0}
      />
    </div>
  );
}

function Stat({
  icon,
  color,
  title,
  value,
  footer,
}: {
  icon: React.ReactNode;
  color: string;
  title: string;
  value: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      {/* ⭐ 폰 폭(`sm` 아래)은 아이콘을 빼고 두 장씩 — 아이콘 카드 네 장이 화면 한 장을 먹었다 (사용자 2026-09-27). */}
      <CardHeader
        floated={false}
        shadow={false}
        className={`absolute hidden h-12 w-12 place-items-center rounded-xl sm:grid ${color}`}
      >
        {icon}
      </CardHeader>
      {/* ⚠️ 아이콘이 absolute 라 제목이 길면 겹친다 — 왼쪽 여백을 아이콘 폭만큼 둔다 (실측 2026-09-05). */}
      <CardBody className="p-3 text-left sm:p-4 sm:pl-20 sm:text-right">
        <Typography
          variant="small"
          className="text-xs font-normal text-blue-gray-600 sm:text-sm dark:text-blue-gray-300"
        >
          {title}
        </Typography>
        <Typography
          variant="h4"
          color="blue-gray"
          className="break-words font-mono text-lg sm:text-2xl dark:text-white"
        >
          {value}
        </Typography>
      </CardBody>
      {footer ? (
        <div className="border-t border-blue-gray-50 p-3 text-[11px] text-blue-gray-600 sm:p-4 sm:text-xs dark:border-gray-800 dark:text-blue-gray-300">
          {footer}
        </div>
      ) : null}
    </Card>
  );
}

function Empty({ text, widen }: { text: string; widen?: () => void }) {
  return (
    <div className="py-10 text-center text-sm text-blue-gray-400">
      <p>{text}</p>
      {widen ? (
        <button
          type="button"
          className="mt-2 rounded-lg border border-blue-gray-200 px-3 py-1 text-xs font-medium text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
          onClick={widen}
        >
          7일로 보기
        </button>
      ) : null}
    </div>
  );
}

const GAIN = "#0f7b6c";
const LOSS = "#b4423a";

/** 펀드 카드들 — 파일 스냅샷 기준(메일과 같은 원천). 펀드가 없으면 아무것도 그리지 않는다. */
function FundsSection({ data }: { data: Data | null }) {
  const funds = data?.funds ?? [];
  if (!data || !funds.length) return null;
  const byKey = new Map((data.runs ?? []).map((r) => [r.key, r] as const));
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {funds.map((f) => {
        const twr = Number(f.twr_pct);
        const gain = Number(f.money_gain);
        const mdd = Number(f.max_drawdown_pct);
        const owned = f.run_keys
          .map((k) => byKey.get(k))
          .filter((r): r is NonNullable<typeof r> => !!r);
        return (
          <Card
            key={f.fund_id}
            className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900"
          >
            <CardBody className="p-5">
              <div className="flex items-baseline justify-between gap-3">
                <Typography
                  variant="h6"
                  color="blue-gray"
                  className="dark:text-white"
                >
                  {f.label}
                </Typography>
                <span className="text-xs text-blue-gray-400">
                  {f.market} · {f.playbook.replace(/@[\d.]+/g, "")} ·{" "}
                  {f.symbols.length}종
                </span>
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-4">
                <Fact label="펀드 잔고" value={usdt(Number(f.balance))} />
                <Fact
                  label="성과 (TWR)"
                  value={pct(twr)}
                  className={tone(twr)}
                  hint="입출금과 분리한 수익률"
                />
                <Fact
                  label="최대 낙폭"
                  value={mdd === 0 ? "—" : `−${mdd.toFixed(2)}%`}
                  className="text-loss"
                />
                <Fact
                  label="금액 손익"
                  value={usdt(gain)}
                  className={tone(gain)}
                  hint="잔고 − 넣은 돈"
                />
              </div>
              <p className="mt-3 text-xs text-blue-gray-400">
                구간에 매매한 판 {owned.filter((r) => r.trades > 0).length} /{" "}
                {f.run_keys.length} ·{" "}
                {owned
                  .filter((r) => r.trades > 0)
                  .map((r) => `${r.symbol} ${pct(n(r.gain_pct))}`)
                  .join(" · ") || "이 구간 주문 없음"}
              </p>
            </CardBody>
          </Card>
        );
      })}
    </div>
  );
}

/**
 * 지갑 그래프 칠 — **불투명한 옅은 색**(밝음 · 어두움). 반투명을 겹치면 회색 위에 빨강이 비쳐 "넣은 돈" 이
 * 갈색(= 잃은 돈처럼)으로 보였다 (사용자 2026-09-27: *"넣은 돈 잃은 돈 번 돈이 분리가 잘 안되어 있는 걸로 보여"*).
 */
const WALLET_FILL = {
  light: { put: "#d3d9de", lost: "#eeaea7", earned: "#b8e2d9", line: "#37474f", putLine: "#8a959e" },
  dark: { put: "#3a4550", lost: "#6e302b", earned: "#1d5a50", line: "#e0e6ea", putLine: "#9aa4ad" },
} as const;

/**
 * 계좌 총액 — **고른 기간**의 지갑 잔고를 넣은 돈 · 번 돈 · 잃은 돈 세 층으로 (사용자 2026-09-27).
 *
 * 값은 서버(`wallet` · 거래소 자금 원장)가 준 점 그대로다. 층 산식은 `reportWallet.ts` — 입금은 회색만 키운다.
 * ⚠️ 지갑 잔고는 **실현 기준**이다(미실현 제외) — 위 "계좌 총액" 칸과 같은 정의.
 *
 * 🔴 **쌓지 않고 덮는다.** ApexCharts 쌓기(stacked area)는 값이 늘 0 인 층(번 동안의 "잃은 돈")을 바닥까지
 * 칠해 회색과 섞었다. 그래서 바닥부터 칠하는 불투명 면 셋을 **뒤에서 앞으로** 겹친다:
 *   ① 초록 = 바닥 ~ 잔고 · ② 빨강 = 바닥 ~ 넣은 돈 · ③ 회색 = 바닥 ~ min(잔고, 넣은 돈)
 * 남는 것: 회색(넣은 돈 중 남은 몫) · 그 위 빨강(넣은 돈 > 잔고인 몫) 또는 초록(잔고 > 넣은 돈인 몫) — 한 점에 하나만.
 */
function EquityChart({ data }: { data: Data | null }) {
  const theme = useThemeValue();
  const wallet = data?.wallet ?? null;
  const layers = useMemo(() => walletLayers(wallet?.points ?? []), [wallet]);
  if (!data) return <Empty text="읽는 중…" />;
  if (wallet === null)
    return <Empty text="거래소 자금 원장을 못 읽었다 — 넣은 돈 · 번 돈을 그릴 수 없다" />;
  if (layers.x.length === 0) return <Empty text="구간에 그릴 잔고가 없다" />;
  const earned = n(wallet.earned);
  const principal = n(wallet.principal);
  const deposits = n(wallet.deposits);
  const earnedPct =
    earned !== null && principal !== null && principal !== 0 ? (earned / principal) * 100 : null;
  const span = windowGain(layers);
  const paint = WALLET_FILL[theme];
  const pair = (ys: number[]) => layers.x.map((x, i) => ({ x, y: ys[i] ?? 0 }));
  const base = apexBaseOptions();
  const options: ApexOptions = {
    ...base,
    chart: { ...base.chart, stacked: false },
    // 선은 둘만 — 잔고(진한 실선) · 넣은 돈(회색 점선). 회색 면에는 선을 긋지 않는다.
    stroke: {
      curve: "stepline",
      width: [2, 1.5, 0],
      dashArray: [0, 4, 0],
      colors: [paint.line, paint.putLine, "transparent"],
    },
    colors: [paint.earned, paint.lost, paint.put],
    fill: { type: "solid", opacity: [1, 1, 1] },
    dataLabels: { enabled: false },
    legend: {
      show: true,
      position: "top",
      horizontalAlign: "left",
      // 한 층을 끄면 덮기 순서가 깨져 색이 거짓말을 한다 — 누르기를 막는다.
      onItemClick: { toggleDataSeries: false },
      markers: { fillColors: [paint.earned, paint.lost, paint.put] },
    },
    xaxis: { type: "datetime", labels: { datetimeUTC: false } },
    yaxis: { min: layers.floor, labels: { formatter: (v: number) => usdt(v, 0) } },
    tooltip: {
      ...base.tooltip,
      shared: true,
      // 층 값 셋 대신 사람이 묻는 것 — 그때 잔고 · 넣은 돈 · 그 차이.
      custom: ({ dataPointIndex }: { dataPointIndex: number }) => {
        const bal = layers.balance[dataPointIndex] ?? 0;
        const put = layers.principal[dataPointIndex] ?? 0;
        const gap = bal - put;
        const when = new Date(layers.x[dataPointIndex] ?? 0).toLocaleString("ko-KR", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        });
        return (
          `<div style="padding:6px 8px;font-size:12px">` +
          `<div style="opacity:.7">${when}</div>` +
          `<div>지갑 잔고 <b>${usdt(bal)}</b></div>` +
          `<div>넣은 돈 <b>${usdt(put)}</b></div>` +
          `<div style="color:${gap >= 0 ? GAIN : LOSS}">${gap >= 0 ? "번 돈" : "잃은 돈"} ` +
          `<b>${usdt(Math.abs(gap))}</b></div></div>`
        );
      },
    },
  };
  return (
    <>
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-1 pb-1 text-sm">
        <span>
          넣은 돈 <b>{usdt(principal)}</b>
        </span>
        <span>
          지갑 잔고 <b>{usdt(n(wallet.balance))}</b>
        </span>
        <span className={tone(earned)}>
          지금까지 {earned !== null && earned < 0 ? "잃은 돈" : "번 돈"}{" "}
          <b>
            {usdt(earned === null ? null : Math.abs(earned))} ({pct(earnedPct)})
          </b>
        </span>
        <span className={tone(span)}>
          이 기간 손익 <b>{span === null ? "—" : `${span > 0 ? "+" : ""}${usdt(span)}`}</b>
        </span>
        {deposits !== null && deposits !== 0 ? (
          <span className="text-blue-gray-500">
            이 기간 {deposits > 0 ? "입금" : "출금"} <b>{usdt(Math.abs(deposits))}</b>
          </span>
        ) : null}
      </div>
      {!wallet.reached ? (
        <p className="px-1 pb-1 text-xs text-blue-gray-400">
          받은 장부가 기간 시작까지 닿지 않았다 — 첫 점은 받은 가장 옛 줄에서 거꾸로 푼 값이다.
        </p>
      ) : null}
      <Chart
        type="area"
        height={260}
        series={[
          // 뒤에서 앞으로 — 순서가 곧 색 규칙이다(위 설명). 이름은 보이는 몫의 이름이다.
          { name: "번 돈", data: pair(layers.balance) },
          { name: "잃은 돈", data: pair(layers.principal) },
          { name: "넣은 돈", data: pair(layers.kept) },
        ]}
        options={options}
      />
    </>
  );
}

function CurveChart({
  data,
  widen,
}: {
  data: Data | null;
  widen?: () => void;
}) {
  const points = data?.curve ?? [];
  const series = useMemo(() => {
    const dots = points.map((p) => ({
      x: new Date(p.at).getTime(),
      y: Number(p.cum_pct),
    }));
    // ⭐ 구간 끝에 "지금" 점 하나 — 마지막 청산 뒤로 선이 이어져 오늘까지의 위치가 읽힌다 (UX 점검). 값은 새로
    //    만들지 않는다: 마지막 누적값 그대로다.
    const last = dots[dots.length - 1];
    if (last && data)
      dots.push({ x: new Date(data.window.until).getTime(), y: last.y });
    return [{ name: "누적 손익률 %", data: dots }];
  }, [points, data]);
  if (!data) return <Empty text="읽는 중…" />;
  if (!points.length)
    return (
      <Empty text="구간에 청산된 매매가 없다 — 그릴 것이 없다" widen={widen} />
    );
  const last = Number(points[points.length - 1]?.cum_pct ?? 0);
  const options: ApexOptions = {
    ...apexBaseOptions(),
    stroke: { curve: "stepline", width: 2 },
    colors: [last >= 0 ? GAIN : LOSS],
    fill: {
      type: "gradient",
      gradient: { shadeIntensity: 0.4, opacityFrom: 0.35, opacityTo: 0.02 },
    },
    xaxis: { type: "datetime", labels: { datetimeUTC: false } },
    yaxis: { labels: { formatter: (v: number) => `${v.toFixed(2)}%` } },
    markers: { size: 3 },
    tooltip: {
      ...apexBaseOptions().tooltip,
      x: { format: "MM-dd HH:mm" },
      y: { formatter: (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%` },
    },
  };
  return <Chart type="area" height={260} series={series} options={options} />;
}

function OutcomeChart({
  data,
  widen,
}: {
  data: Data | null;
  widen?: () => void;
}) {
  const entries = Object.entries(data?.ledger.by_outcome ?? {}).sort(
    (a, b) => b[1] - a[1],
  );
  if (!data) return <Empty text="읽는 중…" />;
  if (!entries.length)
    return <Empty text="구간에 청산된 매매가 없다" widen={widen} />;
  const options: ApexOptions = {
    ...apexBaseOptions(),
    labels: entries.map(([k]) => k),
    legend: { position: "bottom" },
    colors: ["#0f7b6c", "#b4423a", "#607d8b", "#b8860b", "#8957e5", "#0e7490"],
    plotOptions: {
      pie: {
        donut: {
          size: "68%",
          labels: {
            show: true,
            total: {
              show: true,
              label: "청산",
              formatter: () => String(data.ledger.trades),
            },
          },
        },
      },
    },
  };
  return (
    <Chart
      type="donut"
      height={260}
      series={entries.map(([, v]) => v)}
      options={options}
    />
  );
}

function RunsBar({ data, widen }: { data: Data | null; widen?: () => void }) {
  const rows = (data?.runs ?? []).filter((r) => r.gain_pct !== null);
  if (!data) return <Empty text="읽는 중…" />;
  if (!rows.length)
    return <Empty text="구간 손익률이 있는 판이 없다" widen={widen} />;
  const values = rows.map((r) => Number(r.gain_pct));
  const options: ApexOptions = {
    ...apexBaseOptions(),
    plotOptions: {
      bar: {
        horizontal: true,
        borderRadius: 4,
        distributed: true,
        barHeight: "60%",
      },
    },
    colors: values.map((v) => (v >= 0 ? GAIN : LOSS)),
    legend: { show: false },
    xaxis: {
      categories: rows.map((r) => r.symbol),
      labels: { formatter: (v: string) => `${Number(v).toFixed(1)}%` },
    },
    tooltip: {
      ...apexBaseOptions().tooltip,
      y: { formatter: (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%` },
    },
  };
  return (
    <Chart
      type="bar"
      height={Math.max(160, rows.length * 34 + 60)}
      series={[{ name: "손익률", data: values }]}
      options={options}
    />
  );
}

function RunsTable({ data }: { data: Data | null }) {
  const runs = useOpenRuns();
  // ⭐ 매매가 있던 판만 표에 — 메일은 "주문 없으면 한 줄" 로 전부 싣지만, 화면 표에서 0/0 이 수십 줄이면 있는 것이 안 보인다.
  //    나머지는 수만 적는다 (숨기는 것이 아니라 접는 것 · 콘솔에 전부 있다).
  const all = data?.runs ?? [];
  const rows = all.filter((r) => r.trades > 0);
  const idle = all.length - rows.length;
  if (!data) return <Empty text="읽는 중…" />;
  if (!all.length) return <Empty text="구간에 활동한 라이브 판이 없다" />;
  if (!rows.length)
    return (
      <Empty
        text={`구간에 매매한 판이 없다 (살아 있는 판 ${idle}개는 주문 없음)`}
      />
    );
  const th =
    "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-blue-gray-500 dark:text-blue-gray-300";
  const td = "px-4 py-3 text-sm text-blue-gray-800 dark:text-blue-gray-100";
  return (
    <div className="mt-3 overflow-x-auto">
      {idle > 0 ? (
        <p className="px-5 pb-2 text-xs text-blue-gray-400">
          매매 없는 판 {idle}개는 표에서 뺐다 — 콘솔에 전부 있다.
        </p>
      ) : null}
      <table className="w-full min-w-[34rem]">
        <thead className="border-b border-blue-gray-50 dark:border-gray-800">
          <tr>
            <th className={th}>종목</th>
            <th className={th}>플레이북</th>
            <th className={th}>펀드</th>
            <th className={`${th} text-right`}>손익률</th>
            <th className={`${th} text-right`}>익절 / 손절</th>
            <th className={`${th} text-right`}>매매</th>
            <th className={th}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const g = n(r.gain_pct);
            return (
              <tr
                key={r.key}
                className="border-b border-blue-gray-50 last:border-0 dark:border-gray-800"
              >
                <td className={`${td} font-mono font-semibold`}>{r.symbol}</td>
                {/* 버전(@0.7.0)을 떼고 줄바꿈을 허용한다 — 잘린 이름은 이름이 아니다 (UX 점검). 전체는 title 에. */}
                <td
                  className={`${td} max-w-[14rem] whitespace-normal break-all font-mono text-xs`}
                  title={r.playbook}
                >
                  {r.playbook.replace(/@[\d.]+/g, "")}
                </td>
                <td className={td}>
                  {r.fund ?? <span className="text-blue-gray-400">개별</span>}
                </td>
                <td className={`${td} text-right font-mono ${tone(g)}`}>
                  {pct(g)}
                </td>
                <td className={`${td} text-right font-mono`}>
                  <span className="text-gain">{r.takes}</span> /{" "}
                  <span className="text-loss">{r.stops}</span>
                </td>
                <td className={`${td} text-right font-mono`}>{r.trades}</td>
                <td className={`${td} text-right`}>
                  <Link
                    to={`/paper/${r.key}`}
                    className="text-xs font-medium text-blue-gray-700 underline-offset-2 hover:underline dark:text-blue-gray-200"
                    onClick={() => runs.open(r.key, r.symbol)}
                  >
                    차트 열기 →
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
