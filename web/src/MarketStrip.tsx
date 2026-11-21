/**
 * 첫 화면 시장 칸 — BTC · ETH · 나스닥 시세 · 공포지수 같은 거시 · 매매법이 지금 읽는 문 값 (사용자 2026-11-21:
 * *"BTC · ETH · 공포지수 같은 것들을 간이 홈에서 확인하고 싶어 · 매매법 근거에 쓰인 종목 · 지수도"*).
 *
 * 화면 폴링이 서버 CPU 의 큰 몫이라(2026-09-30 사고) **펼칠 때만** 묻고, 펼친 동안 5 분에 한 번 · 탭이 보일 때만
 * 다시 묻는다. 서버도 60 초 묶음이고 새로 계산하지 않는다(판이 넣어 둔 값 · 거시 캐시).
 */
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type MarketData, marketStrip } from "./api";
import { useOpenRuns } from "./shell/openRuns";

const POLL_MS = 300_000;
const CARD =
  "rounded-xl border border-blue-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900";

function signedPct(raw: string | null | undefined): string {
  if (raw === null || raw === undefined || raw === "") return "";
  const v = Number(raw);
  if (!Number.isFinite(v)) return "";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}%`;
}

function pctTone(raw: string | null | undefined): string {
  const v = Number(raw);
  if (!Number.isFinite(v) || v === 0) return "text-blue-gray-500 dark:text-blue-gray-300";
  return v > 0 ? "text-gain" : "text-loss";
}

function stateTone(state: string): string {
  if (state === "열림") return "bg-gain-wash text-gain";
  if (state === "쉼") return "bg-loss-wash text-loss";
  return "bg-blue-gray-50 text-blue-gray-600 dark:bg-gray-800 dark:text-blue-gray-200";
}

const TILE = "rounded-lg bg-blue-gray-50/60 p-2 dark:bg-gray-800";

/** 시세 칸 — 판이 있으면 그 판 상세 · 엔진 다리(나스닥)면 엔진 다리 상세로(첫 화면 포지션 줄과 같은 규칙). */
export function PriceTile({
  symbol,
  run,
  leg,
  children,
}: {
  symbol: string;
  run: string;
  leg: string;
  children: ReactNode;
}) {
  const runs = useOpenRuns();
  const clickable = `${TILE} block hover:ring-2 hover:ring-blue-gray-200 dark:hover:ring-gray-600`;
  if (run) {
    return (
      <Link
        to={`/paper/${run}`}
        onClick={() => runs.open(run, symbol)}
        className={clickable}
        aria-label={`${symbol} 상세`}
      >
        {children}
      </Link>
    );
  }
  if (leg) {
    return (
      <Link to={`/leg/${symbol}`} className={clickable} aria-label={`${symbol} 상세`}>
        {children}
      </Link>
    );
  }
  return <div className={TILE}>{children}</div>;
}

export function MarketStrip() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<MarketData | null>(null);
  const [error, setError] = useState("");

  const pull = useCallback(async () => {
    try {
      setData(await marketStrip());
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void pull();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void pull();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [open, pull]);

  return (
    <section className={`${CARD} p-5`} data-testid="market-strip">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between text-left"
      >
        <h3 className="text-sm font-semibold text-blue-gray-900 dark:text-white">
          시장 · 매매법이 보는 값
        </h3>
        <span className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
          {open ? "접기" : "펼치기"}
        </span>
      </button>
      {open ? (
        <div className="mt-3 flex flex-col gap-4 text-sm">
          {error ? (
            <p role="alert" className="text-loss">
              못 읽었다 — {error}
            </p>
          ) : null}
          {data === null && !error ? (
            <p className="text-blue-gray-500 dark:text-blue-gray-300">읽는 중…</p>
          ) : null}
          {data ? (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {data.prices.map((p) => (
                  <PriceTile key={p.symbol} symbol={p.symbol} run={p.run} leg={p.leg}>
                    <div className="flex items-center justify-between text-xs text-blue-gray-500 dark:text-blue-gray-300">
                      <span>{p.symbol.replace("_USDT", "")}</span>
                      {p.run || p.leg ? <span aria-hidden="true">상세 ›</span> : null}
                    </div>
                    <div className="font-mono font-semibold text-blue-gray-900 dark:text-white">
                      {Number(p.last).toLocaleString("ko-KR", { maximumFractionDigits: 4 })}
                    </div>
                    <div className={`font-mono text-xs ${pctTone(p.change_pct)}`}>
                      {signedPct(p.change_pct)} · 24시간
                    </div>
                  </PriceTile>
                ))}
                {data.macro.map((m) => (
                  <div key={m.key} className="rounded-lg bg-blue-gray-50/60 p-2 dark:bg-gray-800">
                    <div className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
                      {m.label}
                    </div>
                    <div className="font-mono font-semibold text-blue-gray-900 dark:text-white">
                      {Number(m.value).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}
                      {m.unit === "%" ? "%" : ""}
                    </div>
                    <div className={`font-mono text-xs ${pctTone(m.change_pct)}`}>
                      {signedPct(m.change_pct)}
                      {m.band ? ` · ${m.band}` : ""}
                    </div>
                  </div>
                ))}
              </div>
              <div>
                <div className="mb-1.5 text-xs font-medium text-blue-gray-500 dark:text-blue-gray-300">
                  진입 문 — 지금 값 대 문턱
                </div>
                <ul className="flex flex-col divide-y divide-blue-gray-50 dark:divide-gray-800">
                  {data.gates.map((g) => (
                    <li key={g.key} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 py-1.5">
                      <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${stateTone(g.state)}`}>
                        {g.state}
                      </span>
                      <span className="font-medium text-blue-gray-900 dark:text-white">{g.name}</span>
                      <span className="font-mono text-blue-gray-700 dark:text-blue-gray-100">{g.value}</span>
                      <span className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
                        {g.rule}
                        {g.legs.length ? ` · ${g.legs.join(" · ")}` : ""}
                        {g.note ? ` · ${g.note}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              {data.failures.length ? (
                <p className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
                  못 읽은 것: {data.failures.map((f) => `${f.key}(${f.reason})`).join(" · ")}
                </p>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
