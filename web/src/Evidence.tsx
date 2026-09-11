/**
 * 근거 화면 — **어떤 데이터**에서 **어떤 전략**을 돌려 **어떤 결과**가 나왔나 (T222 · 사용자 2026-09-05).
 *
 * 🔴 숫자는 전부 `/api/evidence/bundle` 에서 온다 (2026-09-06 감사 권한 게이트로 정적 파일에서 API 뒤로). 그 파일은 `scripts/build/evidence_bundle.py` 가 기록된 산출물
 *    (`docs/status/t200_lab_results*.txt` · `t201_matrix_results.md` · `evidence_sources.yml` · `playbooks.yml`)을
 *    옮겨 만든 **정적 파일**이다 — API 가 아니라 nginx 가 준다. 화면은 계산하지 않고 그린다. 못 읽은 값은 "—".
 *
 * 규칙: 수익률엔 MDD 병기 · λ 표기 · 합성은 "합성" 이라 적는다 · 합성 분포는 미래 증명이 아니다.
 *
 * ⚠️ 데모·실계좌 어느 쿠키로 와도 같은 것을 본다 — 연구 결과는 돈이 아니라 공개 정보다. 게스트도 본다.
 */

import type { ApexOptions } from "apexcharts";
import { useEffect, useMemo, useState } from "react";
import Chart from "react-apexcharts";
import { useSearchParams } from "react-router-dom";
import { request } from "./api";
import {
  defaultWorld,
  groupByScenario,
  logToPct,
  mdd,
  multiple,
  pct,
  yearsBetween,
  type Bundle,
  type Dataset,
  type LiveMatch,
  type MdTable,
  type RealResult,
  type Strategy,
  type World,
} from "./evidence/model";
import { ChartsSection } from "./evidence/ChartsSection";
import { LiquidationFact } from "./evidence/LiquidationFact";
import { SyntheticDetailPanel, type Pick } from "./evidence/SyntheticDetail";
import { Card, CardBody, Typography } from "./mt";
import { useThemeValue } from "./shell/theme";
import { AuditNotice, ChartCard, Fact } from "./mtui";
import { sampleEvidence, type SampleEvidence, type SampleSymbol } from "./api";
import { apexBaseOptions } from "./chart/apexBase";

export function Evidence() {
  // 🔴 밝기 구독 — ApexCharts 가 토글을 따라오게 한다 (2026-09-12 · `useThemeValue` 주석 참고).
  useThemeValue();
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [error, setError] = useState("");
  const [sample, setSample] = useState<SampleEvidence | null>(null);
  const [sampleError, setSampleError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    // 감사 권한이 없으면 서버가 손익을 null 로 보내고 `redacted` 를 켠다. 그 사람에게는 **견본 매매법 결과**를 기본으로
    // 보여 준다 (사용자 2026-09-08) — 둘을 같이 받아 두고, 아래에서 어느 쪽을 그릴지 정한다.
    Promise.allSettled([
      request<Bundle>("/evidence/bundle", undefined, 60_000),
      sampleEvidence(),
    ]).then(([b, s]) => {
      if (!alive) return;
      if (b.status === "fulfilled") setBundle(b.value);
      else setError(String(b.reason));
      if (s.status === "fulfilled") setSample(s.value);
      else setSampleError(String(s.reason));
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, []);

  if (loading) return <p className="faint">근거 묶음을 읽는 중…</p>;

  // 감사가 아니거나(가려짐) 묶음 자체가 없으면(공개본) — 노란 카드 + 견본 결과만. 가려진 칸을 늘어놓지 않는다.
  if (!bundle || bundle.redacted) {
    return (
      <div className="flex flex-col gap-6">
        <StandardOnlyNotice hasBundle={Boolean(bundle)} />
        {sample ? (
          <SampleView data={sample} />
        ) : (
          // 견본 파일이 아직 없는 배포(404)는 고장이 아니다 — 무엇으로 채우는지 말한다. 그 외 오류는 원문을 보인다.
          <AuditNotice
            what={
              /404/.test(sampleError)
                ? "견본 매매법 근거는 아직 생성되지 않았다 — 관리자가 scripts/build/sample_evidence.py 를 돌리면 여기 채워진다. 실제 매매법의 성적"
                : `견본 근거를 못 읽었다 (${sampleError || error}). 실제 매매법의 성적`
            }
          />
        )}
        {/* 견본을 고르면 그 봉·매매·자본 곡선이 나온다 — 실제 매매법 항목은 가려진 채 목록에만 (사용자 2026-09-08). */}
        <Section
          no="⑤"
          title="차트로 본다 — 견본 매매법"
          lead="견본 매매법이 실측 4h 봉 위에 남긴 진입·손절선·청산과 자본 곡선. 실제 매매법의 차트는 감사 권한이 있어야 열린다."
        >
          <ChartsSection backtestOnly />
        </Section>
        {bundle ? <Intro bundle={bundle} /> : null}
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss"
        role="alert"
      >
        근거 묶음을 못 읽었다 — {error}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Intro bundle={bundle} />
      {/* 견본 매매법은 권한과 무관하게 누구에게나 보인다 (사용자 2026-09-08 "기존 그대로 + 견본 추가"). */}
      {sample ? (
        <Section
          no="⓪"
          title="견본 매매법 (누구나 본다)"
          lead="이동평균 교차 견본을 실계좌와 같은 엔진으로 돌린 표본 — 아래 실제 매매법 근거와 같은 잣대로 읽는다."
        >
          <SampleView data={sample} />
        </Section>
      ) : null}
      <Section
        no="①"
        title="어떤 데이터에서"
        lead="실측은 거래소가 준 봉이고, 합성은 그 실측에서 이어지는 가상의 미래다. 둘을 같은 색으로 칠하지 않는다."
      >
        <Timeline datasets={bundle.datasets} />
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {bundle.datasets.map((d) => (
            <DatasetCard key={d.id} d={d} />
          ))}
        </div>
      </Section>
      <Section
        no="②"
        title="어떤 전략을 돌렸을 때"
        lead="플레이북은 측정 전에 버전을 고정한다 — 구성을 바꾸면 새 버전이고 새 후보다. 아래는 선언 파일이 말하는 계보 그대로다."
      >
        <StrategyTable strategies={bundle.strategies} />
      </Section>
      <Section
        no="③"
        title="어떤 결과가 나왔나"
        lead="실측은 '그 경로'의 값이고, 합성은 분포다. 수익률 옆에는 늘 MDD 가 있다."
      >
        <Sub title="실측 — 한 번 있었던 경로">
          <RealTable rows={bundle.results.real} />
        </Sub>
        <Sub
          title="거래소 교차 — 같은 다리(legs) 위 정적 vs D2 랭크비중"
          source={bundle.results.source}
        >
          <Table t={bundle.results.venues} />
        </Sub>
        <Sub title="합성 45미래 — 분포로 본다 (같은 도구 · 데이터만 합성)">
          <Worlds worlds={bundle.synthetic.worlds} />
        </Sub>
        <Sub
          title="펀드 구성 매트릭스 — A 실측 4.5년 × B 보정 45미래"
          source={bundle.results.source}
        >
          <Table t={bundle.results.matrix} />
          <p className="mt-2 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            B 열은 45미래 분포의 중앙·최악·CVaR(최악 5%
            평균)·MDD(중앙/최악)·청산(판 수/45). 실측 한 창(A)이 아니라
            분포(B)가 구성을 고른 근거다.
          </p>
        </Sub>
        <Sub
          title="사고 제거 후보 — 고비중 청산을 없애는 구성 (2.0.0 채택 근거)"
          source={bundle.results.source}
        >
          <Table t={bundle.results.accidents} />
        </Sub>
        <Sub
          title="D2 채택 전 관문 — 전체창 · b90 · 창 민감도"
          source={bundle.results.source}
        >
          <Table t={bundle.results.xcheck_d2} />
        </Sub>
      </Section>
      <Section
        no="④"
        title="라이브는 어느 미래를 닮아가나"
        lead="펀드 시작 이후 Gate 6종의 동일가중 지수를 45미래의 같은 길이 앞부분과 견준다 — 누적 수익·변동성·낙폭 세 모양으로. 예언이 아니라 위치 확인이다."
      >
        <LiveMatchCard />
      </Section>
      <Section
        no="⑤"
        title="차트로 본다 — 백테스트 · 합성 45미래"
        lead="위 표의 숫자가 나온 그 봉과 그 매매다. 백테스트는 실측 4h 봉 위에, 합성 미래는 그 세상의 일봉·청산 전후 4h 봉 위에 진입·손절선·청산을 표기한다. 매매를 누르면 그 자리로 가고, 지표(볼린저·이평)는 설정에서 겹친다."
      >
        <ChartsSection />
      </Section>
    </div>
  );
}

// ── 감사 없음 → 견본만 (사용자 2026-09-08) ──────────────────────────────────────

/** 노란 카드 — 왜 이 화면이 견본만 보이는지 한 줄로. */
function StandardOnlyNotice({ hasBundle }: { hasBundle: boolean }) {
  return (
    <div className="notice warn" role="status" style={{ fontSize: 14 }}>
      🔔 <b>견본(표준) 매매법의 결과만 보인다.</b> 아래는 이동평균 교차 견본을
      실계좌와 같은 엔진으로 돌린 것이다 — 엣지 주장이 아니라 플랫폼이 어떻게
      재는지 보여 주는 표본이다.{" "}
      {hasBundle
        ? "실제 운용 매매법의 근거는 감사 권한이 있어야 열린다 — 관리자가 계정 화면에서 준다."
        : "이 배포에는 실제 운용 매매법의 근거 묶음이 없다."}
    </div>
  );
}

function pctOf(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** 견본 매매법 결과 — 종목 표 + 자본 곡선(시드 대비 %). 숫자는 전부 `scripts/build/sample_evidence.py` 산출물. */
function SampleView({ data }: { data: SampleEvidence }) {
  const series = data.symbols.map((s) => ({
    name: s.symbol,
    data: s.equity.map(([ts, v]) => [
      Date.parse(ts),
      Number(((v / data.seed_cash - 1) * 100).toFixed(2)),
    ]),
  }));
  const options: ApexOptions = {
    chart: {
      type: "line",
      toolbar: { show: false },
      animations: { enabled: false },
      background: "transparent",
    },
    stroke: { width: 2, curve: "stepline" },
    xaxis: { type: "datetime" },
    yaxis: {
      labels: { formatter: (v: number) => `${v.toFixed(0)}%` },
      title: { text: "시드 대비 %" },
    },
    tooltip: { y: { formatter: (v: number) => `${v.toFixed(2)}%` } },
    legend: { position: "top" },
    theme: {
      mode:
        document.documentElement.dataset.theme === "dark" ? "dark" : "light",
    },
  };
  return (
    <Section
      no="견본"
      title={`${data.label} — 같은 엔진, 실측 봉`}
      lead={`${data.engine}. 생성 ${new Date(data.generated_at).toLocaleString("ko-KR")}.`}
    >
      <ul className="m-0 list-disc pl-5 text-xs text-blue-gray-600 dark:text-blue-gray-300">
        {data.rules.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      <div className="overflow-x-auto">
        <table className="grid" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>종목</th>
              <th>구간</th>
              <th className="num">4h 봉</th>
              <th className="num">매매</th>
              <th className="num">승 / 패</th>
              <th className="num">승률</th>
              <th
                className="num"
                title="시드 대비 실현 · 수수료·펀딩 모형 포함 · λ=1"
              >
                손익
              </th>
              <th className="num">연환산</th>
              <th className="num" title="자본 곡선의 최대 낙폭">
                MDD
              </th>
            </tr>
          </thead>
          <tbody>
            {data.symbols.map((s: SampleSymbol) => (
              <tr key={s.symbol}>
                <td className="mono">{s.symbol}</td>
                <td className="faint">
                  {s.start.slice(0, 10)} ~ {s.end.slice(0, 10)} ({s.years}년)
                </td>
                <td className="num">{s.bars_4h.toLocaleString()}</td>
                <td className="num">{s.trades}</td>
                <td className="num">
                  <span className="gain">{s.wins}</span> /{" "}
                  <span className="loss">{s.losses}</span>
                </td>
                <td className="num">
                  {s.win_rate_pct === null
                    ? "—"
                    : `${s.win_rate_pct.toFixed(1)}%`}
                </td>
                <td className={`num ${s.total_pct >= 0 ? "gain" : "loss"}`}>
                  {pctOf(s.total_pct)}
                </td>
                <td className="num">{pctOf(s.cagr_pct)}</td>
                <td className="num loss">{s.mdd_pct.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ChartCard title="자본 곡선 — 청산마다 한 점 (시드 대비 %)">
        <Chart type="line" height={360} options={options} series={series} />
      </ChartCard>
    </Section>
  );
}

// ── 머리 ────────────────────────────────────────────────────────────────────────

function Intro({ bundle }: { bundle: Bundle }) {
  return (
    <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <CardBody className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <Typography
              variant="h5"
              color="blue-gray"
              className="dark:text-white"
            >
              백테스트 리포트 — 무엇으로 검증했나
            </Typography>
            <p className="mt-1 text-sm text-blue-gray-600 dark:text-blue-gray-300">
              이 화면은 자동 매매가 <b>왜 이 구성으로 도는지</b>의 기록이다. 새
              계산은 없다 — 아래 숫자는 전부 문서와 결과 파일에서 옮겨 왔고,
              옮긴 값은 빌드마다 원문과 대조된다.
            </p>
          </div>
          <span className="rounded-full bg-blue-gray-50 px-2.5 py-1 text-xs text-blue-gray-600 dark:bg-gray-800 dark:text-blue-gray-300">
            묶음 생성{" "}
            {new Date(bundle.generated_at).toLocaleString("ko-KR", {
              timeZone: "Asia/Seoul",
            })}{" "}
            KST
          </span>
        </div>
        <ul className="mt-3 grid gap-2 text-xs text-blue-gray-700 sm:grid-cols-2 dark:text-blue-gray-200">
          {bundle.rules.map((r) => (
            <li
              key={r}
              className="flex gap-2 rounded-lg border border-blue-gray-50 px-3 py-2 dark:border-gray-800"
            >
              <span aria-hidden="true">•</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

function Section({
  no,
  title,
  lead,
  children,
}: {
  no: string;
  title: string;
  lead: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4">
      <div>
        <Typography
          variant="h6"
          color="blue-gray"
          className="flex items-center gap-2 dark:text-white"
        >
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-gray-900 text-sm text-white dark:bg-blue-gray-100 dark:text-gray-900">
            {no}
          </span>
          {title}
        </Typography>
        <p className="mt-1 text-sm text-blue-gray-600 dark:text-blue-gray-300">
          {lead}
        </p>
      </div>
      {children}
    </section>
  );
}

function Sub({
  title,
  source,
  children,
}: {
  title: string;
  source?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <CardBody className="p-5">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <Typography
            variant="small"
            className="font-semibold text-blue-gray-800 dark:text-blue-gray-100"
          >
            {title}
          </Typography>
          {source ? (
            <span className="font-mono text-[11px] text-blue-gray-400">
              {source}
            </span>
          ) : null}
        </div>
        {children}
      </CardBody>
    </Card>
  );
}

// ── ① 데이터 ──────────────────────────────────────────────────────────────────

const REAL = "#455a64";
const SYNTH = "#8957e5";

function Timeline({ datasets }: { datasets: Dataset[] }) {
  const series = useMemo(() => {
    const points: Array<{ x: string; y: [number, number]; fillColor: string }> =
      [];
    for (const d of datasets) {
      const c = d.coverage;
      if (!c) continue;
      if (d.kind === "real" && c.since && c.until) {
        points.push({
          x: d.label,
          y: [Date.parse(c.since), Date.parse(c.until)],
          fillColor: REAL,
        });
      } else if (d.kind === "synthetic" && c.starts_after && c.years) {
        const start = Date.parse(c.starts_after);
        points.push({
          x: `${d.label} (합성)`,
          y: [start, start + c.years * 365.25 * 86_400_000],
          fillColor: SYNTH,
        });
      }
    }
    return [{ data: points }];
  }, [datasets]);
  const count = series[0]?.data.length ?? 0;
  if (!count) return null;
  const options: ApexOptions = {
    ...apexBaseOptions(),
    plotOptions: {
      bar: { horizontal: true, barHeight: "55%", borderRadius: 3 },
    },
    xaxis: { type: "datetime", labels: { datetimeUTC: true, format: "yyyy" } },
    yaxis: { labels: { maxWidth: 260, style: { fontSize: "12px" } } },
    tooltip: {
      ...apexBaseOptions().tooltip,
      x: { format: "yyyy-MM-dd" },
    },
    legend: { show: false },
  };
  return (
    <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <CardBody className="p-5">
        <div className="mb-1 flex flex-wrap items-center gap-4 text-xs text-blue-gray-600 dark:text-blue-gray-300">
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: REAL }}
              aria-hidden="true"
            />{" "}
            실측 (거래소 봉)
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: SYNTH }}
              aria-hidden="true"
            />{" "}
            합성 미래 (실측 끝에서 이어짐)
          </span>
        </div>
        <Chart
          type="rangeBar"
          height={Math.max(180, count * 38 + 60)}
          series={series}
          options={options}
        />
      </CardBody>
    </Card>
  );
}

function DatasetCard({ d }: { d: Dataset }) {
  const c = d.coverage;
  const synthetic = d.kind === "synthetic";
  const years = c ? yearsBetween(c.since, c.until) : null;
  return (
    <div
      className={`rounded-xl border bg-white p-4 dark:bg-gray-900 ${
        synthetic
          ? "border-dashed border-[#8957e5]/50"
          : "border-blue-gray-100 dark:border-gray-800"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-blue-gray-900 dark:text-white">
            {d.label}
          </div>
          <div className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
            {d.timeframe}
          </div>
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
            synthetic
              ? "bg-[#8957e5]/10 text-[#6d3fd1]"
              : "bg-blue-gray-50 text-blue-gray-600 dark:bg-gray-800 dark:text-blue-gray-300"
          }`}
        >
          {synthetic ? "합성" : "실측"}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        {c?.since && c.until ? (
          <Row
            k="구간"
            v={`${c.since} ~ ${c.until}${years ? ` (${years.toFixed(2)}년)` : ""}`}
          />
        ) : null}
        {c?.starts_after ? (
          <Row k="시작" v={`${c.starts_after} 이후 ${c.years ?? "—"}년`} />
        ) : null}
        {c?.futures ? (
          <Row
            k="미래 수"
            v={`${c.futures}개 (드리프트 ${c.drifts?.length ?? "—"} × 씨앗 ${c.seeds?.length ?? "—"})`}
          />
        ) : null}
        {c?.symbols ? (
          <Row k="종목" v={`${c.symbols.length}종 · ${c.symbols.join(" ")}`} />
        ) : null}
        {typeof c?.bars === "number" ? (
          <Row k="봉 수" v={c.bars.toLocaleString("ko-KR")} />
        ) : null}
        {typeof c?.bars_4h === "number" ? (
          <Row
            k="4h 봉"
            v={`${c.bars_4h.toLocaleString("ko-KR")} (최장 ${c.years_4h_max ?? "—"}년)`}
          />
        ) : null}
        {d.coverage_1d?.bars ? (
          <Row k="1d 봉" v={d.coverage_1d.bars.toLocaleString("ko-KR")} />
        ) : null}
        {!c ? <Row k="적재 범위" v="— (빌드 때 못 읽음)" /> : null}
        <Row k="어디에" v={d.where} />
        {d.paths_persisted === false ? (
          <Row k="경로 저장" v="안 됨 — 씨앗으로 다시 만든다" />
        ) : null}
      </dl>
      {d.method ? (
        <p className="mt-3 text-xs leading-relaxed text-blue-gray-600 dark:text-blue-gray-300">
          {d.method}
        </p>
      ) : null}
      {d.block_rule_canonical ? (
        <p className="mt-1 text-xs text-blue-gray-600 dark:text-blue-gray-300">
          <b>기준 블록:</b> {d.block_rule_canonical}
          <br />
          <b>스트레스:</b> {d.block_rule_stress}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-1">
        {d.used_for.map((u) => (
          <span
            key={u}
            className="rounded-md bg-blue-gray-50 px-2 py-0.5 text-[11px] text-blue-gray-700 dark:bg-gray-800 dark:text-blue-gray-200"
          >
            {u}
          </span>
        ))}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt className="text-blue-gray-500 dark:text-blue-gray-400">{k}</dt>
      <dd className="break-words font-mono text-blue-gray-800 dark:text-blue-gray-100">
        {v}
      </dd>
    </>
  );
}

// ── ② 전략 ────────────────────────────────────────────────────────────────────

function StrategyTable({ strategies }: { strategies: Strategy[] }) {
  return (
    <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <CardBody className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
                <th className="px-4 py-3">버전</th>
                <th className="px-4 py-3">플레이북</th>
                <th className="px-4 py-3">배율</th>
                <th className="px-4 py-3">시간축</th>
                <th className="px-4 py-3">선언된 백테스트 (λ 병기)</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s) => (
                <tr
                  key={s.id}
                  className={`border-b border-blue-gray-50 dark:border-gray-800 ${s.listed ? "bg-gain-wash/60 dark:bg-gray-800" : ""}`}
                >
                  <td className="px-4 py-2.5 font-mono text-xs">
                    {s.version}
                    {s.listed ? (
                      <span className="ml-2 rounded-full bg-gain px-2 py-0.5 text-[10px] font-semibold text-white">
                        지금 선택창
                      </span>
                    ) : null}
                  </td>
                  <td className="whitespace-normal px-4 py-2.5">
                    <div className="font-medium text-blue-gray-900 dark:text-white">
                      {s.label ?? s.id}
                    </div>
                    <div className="font-mono text-[11px] text-blue-gray-400">
                      {s.id}
                      {s.bundle?.length
                        ? ` · 번들 ${s.bundle.join(" + ")}`
                        : ""}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">
                    {s.leverage === null ? "—" : `${s.leverage}x`}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">
                    {s.timeframe ?? "—"}
                  </td>
                  <td className="whitespace-normal px-4 py-2.5 text-xs text-blue-gray-700 dark:text-blue-gray-200">
                    {s.backtest_note ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

// ── ③ 결과 ────────────────────────────────────────────────────────────────────

function RealTable({ rows }: { rows: RealResult[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] text-left text-sm">
        <thead>
          <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
            <th className="px-3 py-2">전략</th>
            <th className="px-3 py-2">데이터</th>
            <th className="px-3 py-2 text-right">구간</th>
            <th className="px-3 py-2 text-right">전체 수익률</th>
            <th className="px-3 py-2 text-right">CAGR</th>
            <th className="px-3 py-2 text-right">MDD</th>
            <th className="px-3 py-2 text-right">청산</th>
            <th className="px-3 py-2">λ</th>
            <th className="px-3 py-2">메모</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              className="border-b border-blue-gray-50 align-top dark:border-gray-800"
            >
              {/* ⚠️ app.css 가 모든 td 를 nowrap 으로 둔다 — 글 칸은 줄바꿈을 되살린다. */}
              <td className="whitespace-normal px-3 py-2 text-xs font-medium text-blue-gray-900 dark:text-white">
                {r.strategy}
              </td>
              <td className="whitespace-normal px-3 py-2 text-xs">{r.venue}</td>
              <td className="px-3 py-2 text-right font-mono text-xs">
                {r.years === null ? "—" : `${r.years}년`}
              </td>
              <td
                className={`px-3 py-2 text-right font-mono text-xs ${toneOf(r.total_pct)}`}
              >
                {pct(r.total_pct)}
              </td>
              <td className="px-3 py-2 text-right font-mono text-xs">
                {r.cagr_pct == null ? "—" : pct(r.cagr_pct, 0)}
              </td>
              <td className="px-3 py-2 text-right font-mono text-xs text-loss">
                {mdd(r.mdd_pct)}
              </td>
              <td className="px-3 py-2 text-right font-mono text-xs">
                {r.liquidations == null ? "—" : r.liquidations}
              </td>
              <td className="px-3 py-2 font-mono text-xs">{r.lam ?? "—"}</td>
              <td className="min-w-[220px] whitespace-normal px-3 py-2 text-xs text-blue-gray-600 dark:text-blue-gray-300">
                {r.note}
                <div className="mt-0.5 font-mono text-[10px] text-blue-gray-400">
                  {[...new Set(r.sources)].join(" · ")}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function toneOf(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0)
    return "text-blue-gray-900 dark:text-white";
  return v > 0 ? "text-gain" : "text-loss";
}

function Table({ t }: { t: MdTable }) {
  if (t.redacted) return <AuditNotice what={t.heading || "이 표"} />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
            {t.columns.map((c, i) => (
              <th
                key={`${c}-${i}`}
                className={`px-3 py-2 ${i ? "text-right" : ""}`}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {t.rows.map((r, ri) => (
            <tr
              key={ri}
              className="border-b border-blue-gray-50 dark:border-gray-800"
            >
              {r.map((c, ci) => (
                <td
                  key={ci}
                  className={`px-3 py-2 text-xs ${ci ? "text-right font-mono" : "whitespace-normal font-medium text-blue-gray-900 dark:text-white"}`}
                >
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Worlds({ worlds }: { worlds: World[] }) {
  const [id, setId] = useState<string>(() => defaultWorld(worlds)?.id ?? "");
  const world = worlds.find((w) => w.id === id) ?? defaultWorld(worlds);
  const [all, setAll] = useState(false);
  // ⭐ 미래 하나를 고르면 그 미래의 차트(자본 곡선 · 청산 자리 · 종목 봉)가 아래에 열린다 (사용자 요구 2026-09-06).
  //    `/evidence?future=<번호>` 로 바로 열 수도 있다 (공유·확인용).
  const [params] = useSearchParams();
  const [picked, setPicked] = useState<Pick | null>(() => {
    const raw = params.get("future");
    return raw !== null && Number.isInteger(Number(raw))
      ? { k: Number(raw) }
      : null;
  });
  if (!world) return <p className="faint">세상이 없다.</p>;
  const s = world.summary;
  const groups = groupByScenario(world.rows);
  return (
    <div className="flex flex-col gap-4">
      <div
        className="flex flex-wrap items-center gap-2"
        role="tablist"
        aria-label="블록 규칙"
      >
        {worlds.map((w) => (
          <button
            key={w.id}
            type="button"
            role="tab"
            aria-selected={w.id === world.id}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
              w.id === world.id
                ? "border-gray-900 bg-gray-900 text-white dark:border-blue-gray-100 dark:bg-blue-gray-100 dark:text-gray-900"
                : "border-blue-gray-200 text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
            }`}
            onClick={() => setId(w.id)}
          >
            {w.canonical ? "⭐ " : ""}
            {w.label}
          </button>
        ))}
      </div>
      <div className="rounded-lg bg-blue-gray-50/60 px-3 py-2 font-mono text-[11px] leading-relaxed text-blue-gray-600 dark:bg-gray-800 dark:text-blue-gray-300">
        {world.header.map((h) => (
          <div key={h}>{h}</div>
        ))}
      </div>
      {s.total_median_pct === null ? (
        <AuditNotice what="45미래 수익률 분포 · 산점도 · 연차별 분해" />
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Fact
          label="전체 수익률 중앙"
          value={pct(s.total_median_pct)}
          className={toneOf(s.total_median_pct)}
        />
        <Fact
          label="최악 미래"
          value={pct(s.total_worst_pct)}
          className={toneOf(s.total_worst_pct)}
        />
        <Fact
          label="최악 5% 평균 (CVaR)"
          value={pct(s.cvar5_pct)}
          className={toneOf(s.cvar5_pct)}
        />
        <Fact
          label="5~95% 구간"
          value={`${pct(s.total_p5_pct)} ~ ${pct(s.total_p95_pct)}`}
        />
        <Fact
          label="MDD 중앙 / 최악"
          value={`${mdd(s.mdd_median_pct)} / ${mdd(s.mdd_worst_pct)}`}
          className="text-loss"
        />
        <LiquidationFact world={world} />
      </div>
      <p className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
        점(아래 산점도)이나 45행 표의 행을 누르면 <b>그 미래의 차트</b>가 열린다
        — 자본 곡선, 강제청산을 어디서 당했는지, 종목별 봉.
      </p>
      <div className="grid gap-4 xl:grid-cols-5">
        <div className="xl:col-span-3">
          {s.total_median_pct === null ? null : (
            <Scatter world={world} onPick={setPicked} />
          )}
        </div>
        <div className="overflow-x-auto xl:col-span-2">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
                <th className="px-2 py-2">시나리오</th>
                <th className="px-2 py-2 text-right">중앙</th>
                <th className="px-2 py-2 text-right">범위 (5씨앗)</th>
                <th className="px-2 py-2 text-right">MDD 중앙</th>
                <th className="px-2 py-2 text-right">청산</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr
                  key={g.scenario}
                  className="border-b border-blue-gray-50 dark:border-gray-800"
                >
                  <td className="px-2 py-1.5 text-xs font-medium text-blue-gray-900 dark:text-white">
                    {g.scenario}
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right font-mono text-xs ${toneOf(g.median_total_pct)}`}
                  >
                    {pct(g.median_total_pct)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-[11px] text-blue-gray-600 dark:text-blue-gray-300">
                    {pct(g.min_total_pct)} ~ {pct(g.max_total_pct)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-xs text-loss">
                    {mdd(g.median_mdd_pct)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-xs">
                    {g.liquidated}/{g.rows.length}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div>
        <button
          type="button"
          className="rounded-lg border border-blue-gray-200 px-3 py-1 text-xs font-medium text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
          onClick={() => setAll((v) => !v)}
          aria-expanded={all}
        >
          {all
            ? "45행 접기"
            : `${world.rows.length}행 전부 보기 (3년·2년·1년·1달·1일 분해)`}
        </button>
        {all ? (
          <AllRows world={world} picked={picked} onPick={setPicked} />
        ) : null}
      </div>
      {picked ? (
        <div className="rounded-xl border border-[#8957e5]/40 bg-[#8957e5]/5 p-4 dark:bg-gray-900">
          <SyntheticDetailPanel pick={picked} onClose={() => setPicked(null)} />
        </div>
      ) : null}
    </div>
  );
}

function AllRows({
  world,
  picked,
  onPick,
}: {
  world: World;
  picked: Pick | null;
  onPick: (p: Pick) => void;
}) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead>
          <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
            <th className="px-2 py-2">시나리오</th>
            <th className="px-2 py-2 text-right">씨앗</th>
            <th className="px-2 py-2 text-right">전체</th>
            <th className="px-2 py-2 text-right">CAGR</th>
            <th className="px-2 py-2 text-right">MDD</th>
            <th className="px-2 py-2 text-right">청산</th>
            <th className="px-2 py-2 text-right">3년</th>
            <th className="px-2 py-2 text-right">2년</th>
            <th className="px-2 py-2 text-right">1년</th>
            <th className="px-2 py-2 text-right">1달</th>
            <th className="px-2 py-2 text-right">1일</th>
          </tr>
        </thead>
        <tbody>
          {world.rows.map((r) => (
            <tr
              key={`${r.scenario}-${r.seed}`}
              className={`cursor-pointer border-b border-blue-gray-50 hover:bg-blue-gray-50/60 dark:border-gray-800 dark:hover:bg-gray-800 ${
                picked &&
                "scenario" in picked &&
                picked.scenario === r.scenario &&
                picked.seed === r.seed
                  ? "bg-[#8957e5]/10"
                  : ""
              }`}
              onClick={() => onPick({ scenario: r.scenario, seed: r.seed })}
              title="누르면 이 미래의 차트를 본다"
            >
              <td className="px-2 py-1 text-xs">{r.scenario}</td>
              <td className="px-2 py-1 text-right font-mono text-xs">
                s{r.seed}
              </td>
              <td
                className={`px-2 py-1 text-right font-mono text-xs ${toneOf(r.total_pct)}`}
              >
                {pct(r.total_pct)}
              </td>
              <td className="px-2 py-1 text-right font-mono text-xs">
                {pct(r.cagr_pct)}
              </td>
              <td className="px-2 py-1 text-right font-mono text-xs text-loss">
                {mdd(r.mdd_pct)}
              </td>
              <td className="px-2 py-1 text-right font-mono text-xs">
                {r.liquidations}
              </td>
              {[r.h3y_pct, r.h2y_pct, r.h1y_pct, r.h1m_pct, r.h1d_pct].map(
                (v, i) => (
                  <td
                    key={i}
                    className={`px-2 py-1 text-right font-mono text-xs ${toneOf(v)}`}
                  >
                    {pct(v)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PALETTE = [
  "#b4423a",
  "#c65a2e",
  "#b8860b",
  "#607d8b",
  "#0e7490",
  "#0f7b6c",
  "#2e7d32",
  "#5b3fd1",
  "#8957e5",
];

interface Dot {
  x: number;
  y: number;
  meta: {
    scenario: string;
    seed: number;
    total: number;
    mdd: number;
    liq: number;
  };
}

function Scatter({
  world,
  onPick,
}: {
  world: World;
  onPick: (p: { scenario: string; seed: number }) => void;
}) {
  const groups = groupByScenario(world.rows);
  const series = groups.map((g) => ({
    name: g.scenario,
    // 가려진 행(total null)은 점을 찍지 않는다 — 감사가 아니면 산점도 자체를 안 그리지만 형식은 지킨다.
    data: g.rows.flatMap<Dot>((r) =>
      r.total_pct === null
        ? []
        : [
            {
              x: r.mdd_pct,
              y: multiple(r.total_pct),
              meta: {
                scenario: r.scenario,
                seed: r.seed,
                total: r.total_pct,
                mdd: r.mdd_pct,
                liq: r.liquidations,
              },
            },
          ],
    ),
  }));
  const base = apexBaseOptions();
  const options: ApexOptions = {
    ...base,
    chart: {
      ...base.chart,
      events: {
        // 점을 누르면 그 미래를 고른다 — 상세 차트가 아래에 열린다.
        dataPointSelection: (
          _e: unknown,
          _ctx: unknown,
          cfg: { seriesIndex: number; dataPointIndex: number },
        ) => {
          const dot = series[cfg.seriesIndex]?.data[cfg.dataPointIndex];
          if (dot) onPick({ scenario: dot.meta.scenario, seed: dot.meta.seed });
        },
      },
    },
    colors: PALETTE,
    markers: { size: 6, strokeWidth: 1 },
    legend: { position: "bottom", fontSize: "11px" },
    xaxis: {
      type: "numeric",
      title: { text: "MDD % (오른쪽이 더 깊은 낙폭)" },
      labels: { formatter: (v: string) => `${Number(v).toFixed(0)}%` },
      tickAmount: 6,
    },
    yaxis: {
      logarithmic: true,
      title: { text: "자본 배수 (로그)" },
      labels: {
        formatter: (v: number) =>
          v >= 10 ? `×${v.toFixed(0)}` : `×${v.toFixed(1)}`,
      },
    },
    annotations: {
      yaxis: [
        {
          y: 1,
          borderColor: "#78909c",
          strokeDashArray: 4,
          label: { text: "원금 (0%)", style: { fontSize: "10px" } },
        },
      ],
    },
    tooltip: {
      ...apexBaseOptions().tooltip,
      custom: ({
        seriesIndex,
        dataPointIndex,
        w,
      }: {
        seriesIndex: number;
        dataPointIndex: number;
        w: { config: { series: Array<{ data: Dot[] }> } };
      }) => {
        const d = w.config.series[seriesIndex]?.data[dataPointIndex];
        if (!d) return "";
        const m = d.meta;
        return `<div style="padding:6px 10px;font-size:12px"><b>${m.scenario}</b> · s${m.seed}<br/>전체 ${pct(m.total)} · MDD ${mdd(m.mdd)} · 청산 ${m.liq}</div>`;
      },
    },
  };
  return (
    <Chart type="scatter" height={340} series={series} options={options} />
  );
}

// ── ④ 라이브 vs 45미래 (GET /evidence/live_match) ──────────────────────────────

function LiveMatchCard() {
  // ⭐ `/evidence?since=2026-09-05T00:00:00Z` 로 기점을 바꿔 볼 수 있다 — 열린 판이 없는 dev 에서 확인용. 기본은 서버가
  //    열린 판의 시작 시각으로 정한다.
  const [params] = useSearchParams();
  const since = params.get("since");
  const [data, setData] = useState<LiveMatch | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const path = since
      ? `/evidence/live_match?since=${encodeURIComponent(since)}`
      : "/evidence/live_match";
    request<LiveMatch>(path, undefined, 60_000)
      .then((got) => alive && setData(got))
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [since]);

  if (error) {
    return (
      <div
        className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss"
        role="alert"
      >
        라이브 비교를 못 읽었다 — {error}
      </div>
    );
  }
  if (!data)
    return <p className="faint">라이브 6종 4h 봉을 받아 45미래와 견주는 중…</p>;

  if (data.status === "no_runs") {
    return (
      <Card className="border border-dashed border-blue-gray-200 shadow-none dark:border-gray-700 dark:bg-gray-900">
        <CardBody className="p-5 text-sm text-blue-gray-600 dark:text-blue-gray-300">
          열린 판이 없어 기점(펀드 시작)이 없다. 판이 생기면 그 시각부터 잰다.
          <p className="mt-2 text-xs text-blue-gray-500">{data.caveat}</p>
        </CardBody>
      </Card>
    );
  }

  const bars = data.bars ?? 0;
  const ratio = Math.min(1, bars / data.min_bars);
  const short = data.status === "insufficient";
  return (
    <div className="flex flex-col gap-4">
      {/* 표본 — 이 카드의 전제. 부족하면 아래 순위는 참고만. */}
      <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <CardBody className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm">
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  short ? "bg-loss-wash text-loss" : "bg-gain-wash text-gain"
                }`}
              >
                {short ? "표본 부족 · 참고만" : "표본 충족"}
              </span>
              <span className="font-mono text-xs text-blue-gray-600 dark:text-blue-gray-300">
                4h 봉 {bars.toLocaleString("ko-KR")} / {data.min_bars} (90일)
                {data.elapsed_days !== undefined
                  ? ` · ${data.elapsed_days}일 경과`
                  : ""}
              </span>
            </div>
            <span className="font-mono text-[11px] text-blue-gray-400">
              {data.since
                ? `${data.since.slice(0, 10)} ~ ${data.until?.slice(0, 10) ?? ""}`
                : ""}
              {data.symbols ? ` · ${data.symbols.length}종 동일가중` : ""}
            </span>
          </div>
          <div
            className="mt-3 h-2 w-full overflow-hidden rounded-full bg-blue-gray-50 dark:bg-gray-800"
            aria-hidden="true"
          >
            <div
              className={`h-full ${short ? "bg-blue-gray-300" : "bg-gain"}`}
              style={{ width: `${ratio * 100}%` }}
            />
          </div>
          {data.reason ? (
            <p className="mt-2 text-xs text-blue-gray-500">{data.reason}</p>
          ) : null}
          <p className="mt-3 text-xs leading-relaxed text-blue-gray-600 dark:text-blue-gray-300">
            {data.caveat}
          </p>
        </CardBody>
      </Card>

      {data.live && data.nearest ? (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Fact
              label="라이브 누적 수익 (지수)"
              value={pct(data.live.cum_pct)}
              className={toneOf(data.live.cum_pct)}
            />
            <Fact
              label="라이브 연환산 변동성"
              value={pct(data.live.vol_pct, 0).replace("+", "")}
            />
            <Fact
              label="라이브 최대 낙폭"
              value={mdd(data.live.mdd_pct)}
              className="text-loss"
            />
          </div>
          {/* 차트와 표를 위아래로 — 나란히 두면 표 열이 잘린다 (실측 2026-09-06). */}
          <div className="flex flex-col gap-4">
            <ChartCard title="같은 길이 앞부분 — 라이브 vs 가까운 미래 3">
              {data.chart ? <LiveMatchChart chart={data.chart} /> : null}
            </ChartCard>
            <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
              <CardBody className="p-0">
                <div className="px-5 pt-5">
                  <Typography
                    variant="h6"
                    color="blue-gray"
                    className="dark:text-white"
                  >
                    가까운 미래 {data.nearest.length} — 그 미래의 4.62년 결말
                  </Typography>
                  <p className="mt-1 text-xs text-blue-gray-500 dark:text-blue-gray-300">
                    거리 = 세 모양(누적·변동성·낙폭)을 45미래의 표준편차로 나눈
                    유클리드. 작을수록 가깝다 · 순위로만 읽는다.
                  </p>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-blue-gray-50 text-xs uppercase text-blue-gray-500 dark:border-gray-800 dark:text-blue-gray-300">
                        <th className="px-4 py-2">#</th>
                        <th className="px-4 py-2">미래</th>
                        <th className="px-4 py-2 text-right">거리</th>
                        <th className="px-4 py-2 text-right">앞부분 누적</th>
                        <th className="px-4 py-2 text-right">결말 전체</th>
                        <th className="px-4 py-2 text-right">결말 MDD</th>
                        <th className="px-4 py-2 text-right">청산</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.nearest.map((m) => (
                        <tr
                          key={`${m.scenario}-${m.seed}`}
                          className="border-b border-blue-gray-50 dark:border-gray-800"
                        >
                          <td className="px-4 py-2 font-mono text-xs">
                            {m.rank}
                          </td>
                          <td className="px-4 py-2 text-xs font-medium text-blue-gray-900 dark:text-white">
                            {m.scenario}{" "}
                            <span className="font-mono text-blue-gray-400">
                              s{m.seed}
                            </span>
                          </td>
                          <td className="px-4 py-2 text-right font-mono text-xs">
                            {m.distance.toFixed(2)}
                          </td>
                          <td
                            className={`px-4 py-2 text-right font-mono text-xs ${toneOf(m.head.cum_pct)}`}
                          >
                            {pct(m.head.cum_pct)}
                          </td>
                          <td
                            className={`px-4 py-2 text-right font-mono text-xs ${toneOf(m.outcome_total_pct)}`}
                          >
                            {pct(m.outcome_total_pct)}
                          </td>
                          <td className="px-4 py-2 text-right font-mono text-xs text-loss">
                            {mdd(m.outcome_mdd_pct)}
                          </td>
                          <td className="px-4 py-2 text-right font-mono text-xs">
                            {m.outcome_liquidations}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {data.paths_meta ? (
                  <div className="border-t border-blue-gray-50 px-5 py-3 font-mono text-[11px] text-blue-gray-400 dark:border-gray-800">
                    경로 세트 {data.paths_meta.futures}미래 · 블록{" "}
                    {data.paths_meta.block} · 기준표 {data.paths_meta.basis} ·
                    생성 {data.paths_meta.generated.slice(0, 10)}
                  </div>
                ) : null}
              </CardBody>
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}

const LIVE_COLOR = "#0f7b6c";
const NEAR_COLORS = ["#8957e5", "#b8860b", "#607d8b"];

function LiveMatchChart({ chart }: { chart: NonNullable<LiveMatch["chart"]> }) {
  const toSeries = (values: number[]) =>
    values.map((v, i) => ({ x: i, y: logToPct(v) }));
  const series = [
    { name: "라이브 (Gate 6종 지수)", data: toSeries(chart.live) },
    ...chart.nearest.map((n) => ({ name: n.label, data: toSeries(n.values) })),
  ];
  const options: ApexOptions = {
    ...apexBaseOptions(),
    colors: [LIVE_COLOR, ...NEAR_COLORS],
    stroke: {
      curve: "straight",
      width: chart.nearest
        .map(() => 1.5)
        .concat([2.5])
        .reverse(),
    },
    legend: { position: "bottom", fontSize: "11px" },
    xaxis: {
      type: "numeric",
      title: { text: "봉 (그리기용으로 점을 줄임)" },
      labels: { show: false },
    },
    yaxis: {
      labels: {
        formatter: (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(0)}%`,
      },
      title: { text: "지수 수익률" },
    },
    tooltip: {
      ...apexBaseOptions().tooltip,
      y: { formatter: (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%` },
    },
    annotations: {
      yaxis: [{ y: 0, borderColor: "#78909c", strokeDashArray: 4 }],
    },
  };
  return <Chart type="line" height={300} series={series} options={options} />;
}
