/**
 * 가벼운 첫 화면 (사용자 2026-11-06: *"아주 라이트하게 확인할 수 있는 화면을 처음 띄워주면 어떨까? 진짜 필요한것만
 * 딱 출력해주는거지. 이후에, 상세 보기로 가는 걸 고를 수 있게."*).
 *
 * 화면 폴링이 서버 CPU 의 큰 몫이라 첫 화면은 요청 하나만 1 분에 한 번, 탭이 보일 때만 보낸다. 서버도 30 초 묶음이다.
 * 판 카드 · 차트 · 표는 사람이 "상세 보기" 를 고를 때만 뜬다.
 *
 * 카드는 흰 면 + 그림자로 회색 배경에서 띄운다(사용자 2026-11-06 "배경색이랑 똑같아서 가시성이 떨어진다").
 * 맨 위 큰 칸은 사람이 먼저 묻는 것 — 지금 얼마이고 번 돈은 얼마인가.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  type LiteBrief,
  type LiteHomeData,
  type LitePosition,
  type LiteRecent,
  liteBrief,
  liteHome,
  liteRecent,
} from "./api";
import { fmtPrice } from "./chart/trades";
import { exitText, noteText } from "./engineFacts";
import { heldText, legNames, liteFacts } from "./liteFacts";
import { useOpenRuns } from "./shell/openRuns";

const POLL_MS = 60_000;
const BRIEF_PERIODS = [
  { hours: 24, label: "24시간" },
  { hours: 168, label: "7일" },
  { hours: 720, label: "30일" },
] as const;
const CARD =
  "rounded-xl border border-blue-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900";

function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function money(value: number | null, signed = false): string {
  if (value === null) return "—";
  const body = Math.abs(value).toLocaleString("ko-KR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const sign = value < 0 ? "−" : signed && value > 0 ? "+" : "";
  return `${sign}${body}`;
}

function tone(value: number | null): string {
  if (value === null || value === 0)
    return "text-blue-gray-900 dark:text-white";
  return value > 0 ? "text-gain" : "text-loss";
}

function Small({
  label,
  value,
  hint,
  className = "",
}: {
  label: string;
  value: string;
  hint?: string;
  className?: string;
}) {
  return (
    <div className={`${CARD} p-4`}>
      <div className="text-xs font-medium text-blue-gray-500 dark:text-blue-gray-300">
        {label}
      </div>
      <div
        className={`mt-1 break-words font-mono text-lg font-semibold ${className || "text-blue-gray-900 dark:text-white"}`}
      >
        {value}
      </div>
      {hint ? (
        <div className="mt-1 text-xs text-blue-gray-500 dark:text-blue-gray-300">
          {hint}
        </div>
      ) : null}
    </div>
  );
}

/** 시각을 KST 짧은 꼴로 — 구간 표시 전용. */
function kst(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * 약식 리포트 — 전체 리포트 맨 위 숫자만 기간별로. 열 때와 기간을 바꿀 때만 읽는다(폴링 없음 · 서버 60 초 묶음).
 * 손익 시작점 때문에 구간이 잘렸으면 그 사실을 적는다 — "30일" 칸의 몇 시간치를 30일 성과로 읽지 않게.
 */
export function BriefReport() {
  const [hours, setHours] = useState<number>(BRIEF_PERIODS[1].hours);
  const [brief, setBrief] = useState<LiteBrief | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    liteBrief(hours)
      .then((got) => {
        if (!alive) return;
        setBrief(got);
        setError("");
      })
      .catch((exc: unknown) => {
        if (alive) setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      alive = false;
    };
  }, [hours]);

  const net = num(brief?.net);
  const fees = num(brief?.fees);
  const funding = num(brief?.funding);
  const costs = fees === null || funding === null ? null : fees + funding;
  const winRate = num(brief?.win_rate);
  const meanRr = num(brief?.mean_rr);
  const mdd = num(brief?.max_drawdown_pct);

  return (
    <section className={`${CARD} p-5`} data-testid="brief-report">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-blue-gray-900 dark:text-white">
          약식 리포트
        </h3>
        <div
          className="inline-flex rounded-lg border border-blue-gray-100 p-0.5 dark:border-gray-700"
          role="tablist"
          aria-label="기간"
        >
          {BRIEF_PERIODS.map((p) => (
            <button
              key={p.hours}
              type="button"
              role="tab"
              aria-selected={hours === p.hours}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${
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
      </div>
      {error ? (
        <p role="alert" className="text-sm text-loss">
          못 읽었다 — {error}
        </p>
      ) : null}
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <dt className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
            순 실현 손익
          </dt>
          <dd className={`mt-1 font-mono text-lg font-semibold ${tone(net)}`}>
            {money(net, true)}
          </dd>
          <dd className="mt-0.5 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            USDT · 수수료 · 펀딩 포함
          </dd>
        </div>
        <div>
          <dt className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
            매매 · 승률
          </dt>
          <dd className="mt-1 font-mono text-lg font-semibold text-blue-gray-900 dark:text-white">
            {brief ? `${brief.trades}건` : "—"}
            {winRate === null ? "" : ` · ${winRate.toFixed(0)}%`}
          </dd>
          <dd className="mt-0.5 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            {brief
              ? `롱 ${brief.longs} · 숏 ${brief.shorts} · 승 ${brief.wins}${meanRr === null ? "" : ` · 손익비 ${meanRr.toFixed(2)}`}`
              : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
            낙폭
          </dt>
          <dd className="mt-1 font-mono text-lg font-semibold text-blue-gray-900 dark:text-white">
            {mdd === null
              ? "—"
              : mdd === 0
                ? "0.00%"
                : `−${Math.abs(mdd).toFixed(2)}%`}
          </dd>
          <dd className="mt-0.5 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            건별 손익률 합 기준 · 계좌 낙폭 아님
          </dd>
        </div>
        <div>
          <dt className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
            수수료 + 펀딩
          </dt>
          <dd className={`mt-1 font-mono text-lg font-semibold ${tone(costs)}`}>
            {money(costs, true)}
          </dd>
          <dd className="mt-0.5 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            USDT
          </dd>
        </div>
      </dl>
      {brief ? (
        <p className="mt-3 text-xs text-blue-gray-500 dark:text-blue-gray-300">
          {kst(brief.since)} ~ {kst(brief.until)} KST
          {brief.clipped ? " · 손익 시작점부터만 셈" : ""}
          {brief.net === null ? " · 거래소를 못 읽어 금액 칸이 비었다" : ""}
        </p>
      ) : null}
      {brief?.diverged ? (
        <p role="alert" className="mt-1 text-xs font-medium text-loss">
          원장 · 거래소 불일치 — {brief.note}
        </p>
      ) : null}
    </section>
  );
}

/**
 * 최근 매매 목록의 몸통 — 받은 값을 그리기만 한다(시험이 이것을 직접 그린다).
 * 한 줄 = 손익을 실현한 주문 하나. 순손익 = 실현 손익 + 그 매매의 수수료(진입 + 청산) + 펀딩.
 */
export function RecentRows({ data }: { data: LiteRecent }) {
  if (data.rows === null) {
    return (
      <p role="alert" className="text-sm text-loss">
        거래소 장부를 못 읽었다 — 목록이 비어 있는 것이 아니다
      </p>
    );
  }
  if (data.rows.length === 0) {
    return (
      <p className="text-sm text-blue-gray-500 dark:text-blue-gray-300">
        거래소 장부(약 30일치)에 실현 매매가 없다
      </p>
    );
  }
  // 합계는 손익 시작점 뒤만 — 홈의 다른 숫자와 같은 기준이다. 그 앞 매매는 목록에 남기되
  // 흐리게 두고 합계에서 뺀다(새 출발 앞 손익이 지금 성적처럼 읽히지 않게).
  const since = data.since ?? null;
  const sums = since && data.after ? data.after : data;
  const sinceMs = since ? Date.parse(since) : null;
  const before =
    sinceMs === null
      ? 0
      : data.rows.filter((row) => Date.parse(row.at) < sinceMs).length;
  const net = num(sums.net);
  const fees = num(sums.fees);
  const funding = num(sums.funding);
  const openFees = num(data.open_fees);
  const openFunding = num(data.open_funding);
  return (
    <>
      <p
        className="mb-2 text-xs text-blue-gray-700 dark:text-blue-gray-100"
        data-testid="recent-summary"
      >
        {since ? `손익 시작점(${kst(since)}) 뒤 ` : ""}
        {sums.count ?? data.rows.length}건 · 이긴 매매 {sums.wins ?? 0}건 ·
        순손익 합{" "}
        <span className={`font-mono font-semibold ${tone(net)}`}>
          {money(net, true)}
        </span>{" "}
        USDT · 수수료 합{" "}
        <span className="font-mono font-semibold">{money(fees, true)}</span> ·
        펀딩 합{" "}
        <span className="font-mono font-semibold">{money(funding, true)}</span>
        {before > 0
          ? ` · 시작점 앞 ${before}건은 아래에 흐리게 두고 합계에서 뺐다`
          : ""}
      </p>
      <ul className="flex flex-col divide-y divide-blue-gray-50 text-sm dark:divide-gray-800">
        {data.rows.map((row) => {
          const rowNet = num(row.net);
          const old = sinceMs !== null && Date.parse(row.at) < sinceMs;
          return (
            <li
              key={`${row.symbol}:${row.at}`}
              className={old ? "py-2 opacity-50" : "py-2"}
              data-testid={old ? "recent-row-before" : "recent-row"}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium text-blue-gray-900 dark:text-white">
                  {row.symbol.replace("_USDT", "")}
                  {row.leg ? (
                    <span
                      className="ml-2 text-xs font-normal text-blue-gray-700 dark:text-blue-gray-100"
                      data-testid="recent-leg"
                    >
                      {row.side ? `${row.side} · ` : ""}
                      {row.leg}
                    </span>
                  ) : null}
                  <span className="ml-2 text-xs font-normal text-blue-gray-500 dark:text-blue-gray-300">
                    {kst(row.at)}
                    {row.fills > 1 ? ` · 체결 ${row.fills}번` : ""}
                  </span>
                </span>
                <span className={`font-mono font-semibold ${tone(rowNet)}`}>
                  {money(rowNet, true)} USDT
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-blue-gray-500 dark:text-blue-gray-300">
                <span>
                  실현{" "}
                  <span className="font-mono">{money(num(row.pnl), true)}</span>
                </span>
                <span>
                  수수료{" "}
                  <span className="font-mono">
                    {money(num(row.fees), true)}
                  </span>
                </span>
                <span>
                  펀딩{" "}
                  <span className="font-mono">
                    {money(num(row.funding), true)}
                  </span>
                </span>
                <span>
                  그 뒤 계좌{" "}
                  <span className="font-mono font-medium text-blue-gray-700 dark:text-blue-gray-100">
                    {money(num(row.balance))}
                  </span>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-blue-gray-500 dark:text-blue-gray-300">
        순손익 = 실현 손익 + 그 매매의 수수료(진입 + 청산) + 펀딩 · 그 뒤 계좌 =
        그 체결 직후 지갑 잔고(미실현 제외) · 시각은 KST
        {data.oldest
          ? ` · 거래소 장부는 약 30일치(${kst(data.oldest)}부터) — 그보다 앞서 들어간 매매는 진입 수수료가 빠져 있을 수 있다`
          : ""}
        {openFees !== null && openFunding !== null
          ? ` · 열린 포지션에 쌓인 수수료 ${money(openFees, true)} · 펀딩 ${money(openFunding, true)} 는 아직 어느 줄에도 안 들어갔다`
          : ""}
        {data.unreadable
          ? ` · 못 읽은 장부 줄 ${data.unreadable}개는 빠졌다`
          : ""}
      </p>
    </>
  );
}

/**
 * 최근 매매 50 건 — 접힌 채로 뜨고 열 때만 읽는다(폴링 없음 · 서버 60 초 묶음).
 * 사용자 2026-11-10: 종목 · 손익(색) · 총 수수료 · 체결 시각 · 그때의 계좌.
 */
export function RecentTrades() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<LiteRecent | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let alive = true;
    liteRecent()
      .then((got) => {
        if (!alive) return;
        setData(got);
        setError("");
      })
      .catch((exc: unknown) => {
        if (alive) setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      alive = false;
    };
  }, [open]);

  return (
    <section className={`${CARD} p-5`} data-testid="recent-trades">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-blue-gray-900 dark:text-white">
          최근 매매 50건
        </h3>
        <button
          type="button"
          aria-expanded={open}
          className="rounded-lg border border-blue-gray-100 px-3 py-1 text-xs font-medium text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
          onClick={() => setOpen((was) => !was)}
        >
          {open ? "접기" : "펼치기"}
        </button>
      </div>
      {open ? (
        <div className="mt-3">
          {error ? (
            <p role="alert" className="text-sm text-loss">
              못 읽었다 — {error}
            </p>
          ) : data ? (
            <RecentRows data={data} />
          ) : (
            <p className="text-sm text-blue-gray-500 dark:text-blue-gray-300">
              읽는 중…
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}

/** 가격 — 못 읽으면 대시. */
function price(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? fmtPrice(parsed) : "—";
}

/**
 * 포지션 한 줄 — 먼저 볼 것은 다리 · 증거금 · 손절이다 (사용자 2026-11-07 "잡힌 증거금 금액 등 필수로 봐야 하는 값").
 * 몫이 여럿이면 손절이 몫마다 달라 다리 이름 옆에 한 줄씩 적는다.
 */
function PositionRow({ p, nowMs }: { p: LitePosition; nowMs: number }) {
  const runs = useOpenRuns();
  const navigate = useNavigate();
  const pnl = num(p.unrealized);
  const pct = num(p.pct);
  const facts = liteFacts(p);
  const held = heldText(p, nowMs);
  const legs = legNames(p);
  const name = p.symbol.replace("_USDT", "");
  const cells: { label: string; value: string; className?: string }[] = [
    { label: "증거금", value: `${money(num(p.margin))} USDT` },
    {
      label: "명목",
      value: `${money(num(p.notional))} USDT${facts.leverage === null ? "" : ` · ${facts.leverage.toFixed(1)}배`}`,
    },
    { label: "진입 → 지금", value: `${price(p.entry)} → ${price(p.mark)}` },
  ];
  if (p.shares.length <= 1) {
    cells.push({
      label: "손절",
      value:
        facts.stop === null
          ? "—"
          : `${fmtPrice(facts.stop)}${facts.stopDistancePct === null ? "" : ` (${facts.stopDistancePct.toFixed(1)}%)`}`,
    });
    cells.push({
      label: "손절 시",
      value:
        facts.stopPnl === null ? "—" : `${money(facts.stopPnl, true)} USDT`,
      className: tone(facts.stopPnl),
    });
    // T822 — 몫이 하나여도 서버가 청산 자리를 줬으면 손절 옆에 적는다.
    const only = p.shares[0];
    const exit = only ? exitText(only.exit, only.exit_how, fmtPrice) : "";
    if (exit) cells.push({ label: "청산 자리", value: exit });
  }
  // T822 — 엔진 다리 한 줄 요약(계단 몫 · 다음 계단 · 다음 마감). 상세 화면과 같은 값이다.
  const note = p.engine ? noteText(p.engine, nowMs, fmtPrice) : "";
  cells.push({ label: "계약", value: p.contracts });
  if (held) cells.push({ label: "보유", value: held });
  return (
    <li
      className="-mx-2 cursor-pointer rounded-lg px-2 py-3 hover:bg-blue-gray-50 dark:hover:bg-gray-800"
      data-testid="lite-position"
      onClick={(event) => {
        // 이름 링크는 스스로 옮겨 간다 · 값을 드래그해 복사하는 중이면 옮기지 않는다.
        if ((event.target as HTMLElement).closest("a")) return;
        if (window.getSelection()?.toString()) return;
        if (p.run) {
          runs.open(p.run, p.symbol);
          navigate(`/paper/${p.run}`);
        } else {
          navigate(`/leg/${p.symbol}`);
        }
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex flex-wrap items-center gap-2 font-medium text-blue-gray-900 dark:text-white">
          {/* 판이 있으면 그 판 화면 · 없으면(나스닥) 엔진 다리 화면 */}
          {p.run ? (
            <Link
              to={`/paper/${p.run}`}
              className="underline-offset-2 hover:underline"
              onClick={() => runs.open(p.run, p.symbol)}
            >
              {name}
            </Link>
          ) : (
            <Link
              to={`/leg/${p.symbol}`}
              className="underline-offset-2 hover:underline"
            >
              {name}
            </Link>
          )}
          <span
            className={`rounded px-1.5 py-0.5 text-xs font-medium ${
              p.side === "long"
                ? "bg-gain/10 text-gain"
                : "bg-loss-wash text-loss"
            }`}
          >
            {p.side === "long" ? "롱" : "숏"}
          </span>
          {legs ? (
            <span className="text-xs font-normal text-blue-gray-500 dark:text-blue-gray-300">
              {legs}
            </span>
          ) : null}
        </span>
        <span className={`font-mono ${tone(pnl)}`}>
          {money(pnl, true)} USDT
          {pct === null ? "" : ` (${pct > 0 ? "+" : ""}${pct.toFixed(1)}%)`}
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
        {cells.map((cell) => (
          <div key={cell.label} className="min-w-0">
            <dt className="text-blue-gray-500 dark:text-blue-gray-300">
              {cell.label}
            </dt>
            <dd
              className={`break-words font-mono font-medium ${cell.className ?? "text-blue-gray-900 dark:text-white"}`}
            >
              {cell.value}
            </dd>
          </div>
        ))}
      </dl>
      {p.shares.length > 1 ? (
        <ul className="mt-1.5 text-xs text-blue-gray-500 dark:text-blue-gray-300">
          {p.shares.map((share, index) => {
            const exit = exitText(share.exit, share.exit_how, fmtPrice);
            return (
              <li key={`${share.name}:${index}`}>
                {share.name} · 손절{" "}
                <span className="font-mono">{price(share.stop)}</span>
                {exit ? ` · ${exit}` : ""}
              </li>
            );
          })}
        </ul>
      ) : null}
      {note ? (
        <p
          className="mt-1.5 text-xs text-blue-gray-500 dark:text-blue-gray-300"
          data-testid="lite-engine-note"
        >
          {note}
        </p>
      ) : null}
      {facts.stopWarning ? (
        <p role="alert" className="mt-1.5 text-xs font-medium text-loss">
          ⚠ {facts.stopWarning}
        </p>
      ) : null}
    </li>
  );
}

export function LiteHome() {
  const [data, setData] = useState<LiteHomeData | null>(null);
  const [error, setError] = useState("");

  const pull = useCallback(async () => {
    try {
      setData(await liteHome());
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void pull();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void pull();
    }, POLL_MS);
    const onShow = () => {
      if (document.visibilityState === "visible") void pull();
    };
    document.addEventListener("visibilitychange", onShow);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onShow);
    };
  }, [pull]);

  const total = num(data?.total);
  const earned = num(data?.earned);
  const capital = num(data?.capital);
  const realized = num(data?.realized_24h);
  const unrealized = num(data?.unrealized);
  const marginUsed = num(data?.margin_used);
  const available = num(data?.available);
  const notional = num(data?.notional);
  const accountLeverage = num(data?.account_leverage);
  const halted = data?.halted === true;
  const nowMs = data ? Date.parse(data.at) : 0;
  const earnedPct =
    earned !== null && capital ? (earned / capital) * 100 : null;
  // 🔴 `total` 은 거래소 지갑 총액이라 **미실현이 이미 빠져 있다**(쓸 수 있는 돈 + 잡힌 증거금 ·
  //    11-07 실측 730.49 + 92.77 = 823.25 · 미실현 −4.38 은 따로). 총액에서 미실현을 또 빼면 두 번 빠진다.
  //    미실현을 넣은 평가액이 큰 숫자이고(사용자 2026-11-10), 지갑 총액은 "미실현 제외" 라고 적어
  //    아래 줄에 낸다(콘솔의 "지금 다 닫으면" 과 같은 원칙 · 여기는 청산 수수료 전).
  const equity =
    total !== null && unrealized !== null ? total + unrealized : null;
  const notLeader = data?.leader === false;
  const at = data
    ? new Date(data.at).toLocaleTimeString("ko-KR", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3 sm:gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-blue-gray-900 dark:text-white">
          지금 계좌
        </h2>
        <div className="flex items-center gap-3 text-sm">
          {at ? (
            <span className="text-blue-gray-500 dark:text-blue-gray-300">
              {at} 기준
            </span>
          ) : null}
          <Link
            to="/console"
            className="rounded-lg bg-gray-900 px-3.5 py-1.5 font-medium text-white shadow-sm hover:bg-gray-700 dark:bg-white dark:text-gray-900 dark:hover:bg-blue-gray-100"
          >
            상세 보기
          </Link>
        </div>
      </div>

      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-loss/30 bg-loss-wash p-3 text-sm text-loss"
        >
          못 읽었다 — {error}
        </div>
      ) : null}

      {/* 사람이 먼저 묻는 것 둘 — 지금 닫으면 얼마 · 번 돈.
          큰 숫자 = 미실현 포함 평가(사용자 2026-11-10 "크기를 키워주고 이득이면 초록 손해면 빨강").
          색은 지갑 총액(미실현 제외)보다 큰가 작은가 = 미실현의 부호다. */}
      <section className={`${CARD} grid gap-4 p-5 sm:grid-cols-2`}>
        <div>
          <div className="text-sm font-medium text-blue-gray-500 dark:text-blue-gray-300">
            미실현 포함 평가
          </div>
          <div
            className={`mt-1 break-words font-mono text-4xl font-bold sm:text-5xl ${tone(equity === null ? null : unrealized)}`}
            data-testid="lite-equity"
          >
            {money(equity)}
            <span className="ml-1.5 text-base font-medium text-blue-gray-500 dark:text-blue-gray-300">
              USDT
            </span>
          </div>
          <div
            className="mt-1.5 text-xs text-blue-gray-500 dark:text-blue-gray-300"
            data-testid="lite-total"
          >
            계좌 총액(미실현 제외){" "}
            <span className="font-mono font-semibold text-blue-gray-700 dark:text-blue-gray-100">
              {money(total)}
            </span>{" "}
            USDT · 미실현{" "}
            <span className={`font-mono font-semibold ${tone(unrealized)}`}>
              {money(unrealized, true)}
            </span>
          </div>
          <div className="mt-0.5 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            자본금 투입 {money(capital)} USDT · 손익 아님 · 청산 수수료 전
          </div>
        </div>
        <div className="border-t border-blue-gray-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0 dark:border-gray-700">
          <div className="text-sm font-medium text-blue-gray-500 dark:text-blue-gray-300">
            지금까지 손익
          </div>
          <div
            className={`mt-1 break-words font-mono text-3xl font-bold ${tone(earned)}`}
          >
            {money(earned, true)}
            <span className="ml-1.5 text-base font-medium">USDT</span>
          </div>
          <div className="mt-1 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            {earnedPct === null
              ? "입출금을 뺀 값 · 미실현 제외"
              : `자본금의 ${earnedPct > 0 ? "+" : ""}${earnedPct.toFixed(1)}% · 입출금을 뺀 값 · 미실현 제외`}
          </div>
        </div>
      </section>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4">
        <Small
          label="최근 24시간 실현"
          value={`${money(realized, true)} USDT`}
          className={tone(realized)}
        />
        <Small
          label="미실현 손익"
          value={`${money(unrealized, true)} USDT`}
          className={tone(unrealized)}
        />
        <Small
          label="잡힌 증거금"
          value={`${money(marginUsed)} USDT`}
          hint={`쓸 수 있는 돈 ${money(available)} USDT`}
        />
        <Small
          label="열린 명목"
          value={`${money(notional)} USDT`}
          hint={
            accountLeverage === null
              ? "계좌 배율 —"
              : `계좌 배율 ${accountLeverage.toFixed(2)}배`
          }
        />
        <div className="col-span-2 sm:col-span-1">
          <Small
            label="매매 상태"
            value={
              data
                ? notLeader
                  ? "리더 아님"
                  : halted
                    ? "진입 정지"
                    : `판 ${data.runs}개`
                : "—"
            }
            className={notLeader || halted ? "text-loss" : ""}
            hint={
              notLeader
                ? "이 서버가 주문을 내지 않는다"
                : halted
                  ? `판 ${data?.runs ?? 0}개 · 새 진입만 멈춤 · 보유분 관리는 계속`
                  : data?.halted === null
                    ? "정지 스위치를 못 읽었다"
                    : "새 진입 켜짐"
            }
          />
        </div>
      </div>

      <BriefReport />

      <RecentTrades />

      <section className={`${CARD} p-5`}>
        <h3 className="mb-3 text-sm font-semibold text-blue-gray-900 dark:text-white">
          열린 포지션 {data ? `${data.positions.length}개` : ""}
        </h3>
        {data && data.positions.length === 0 ? (
          <p className="text-sm text-blue-gray-500 dark:text-blue-gray-300">
            없음
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-blue-gray-50 text-sm dark:divide-gray-800">
            {(data?.positions ?? []).map((p) => (
              <PositionRow key={p.symbol} p={p} nowMs={nowMs} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
