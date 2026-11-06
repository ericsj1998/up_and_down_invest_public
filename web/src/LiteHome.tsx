/**
 * 가벼운 첫 화면 (사용자 2026-11-06: *"아주 라이트하게 확인할 수 있는 화면을 처음 띄워주면 어떨까? 진짜 필요한것만
 * 딱 출력해주는거지. 이후에, 상세 보기로 가는 걸 고를 수 있게."*).
 *
 * 화면 폴링이 서버 CPU 의 큰 몫이라 첫 화면은 요청 하나만 1 분에 한 번, 탭이 보일 때만 보낸다. 서버도 30 초 묶음이다.
 * 판 카드 · 차트 · 표는 사람이 "상세 보기" 를 고를 때만 뜬다.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type LiteHomeData, liteHome } from "./api";

const POLL_MS = 60_000;

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
  return `${sign}${body} USDT`;
}

function tone(value: number | null): string {
  if (value === null || value === 0) return "";
  return value > 0 ? "text-gain" : "text-loss";
}

function Fact({
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
    <div className="rounded-lg border border-blue-gray-100 p-3 dark:border-gray-800">
      <div className="text-xs text-blue-gray-500 dark:text-blue-gray-300">
        {label}
      </div>
      <div
        className={`mt-1 break-words font-mono text-lg font-semibold ${className}`}
      >
        {value}
      </div>
      {hint ? (
        <div className="mt-1 text-xs text-blue-gray-400">{hint}</div>
      ) : null}
    </div>
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

  const earned = num(data?.earned);
  const capital = num(data?.capital);
  const realized = num(data?.realized_24h);
  const unrealized = num(data?.unrealized);
  const earnedPct =
    earned !== null && capital ? (earned / capital) * 100 : null;
  const at = data
    ? new Date(data.at).toLocaleTimeString("ko-KR", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">지금 계좌</h2>
        <div className="flex items-center gap-3 text-sm">
          {at ? <span className="text-blue-gray-400">{at} 기준</span> : null}
          <Link
            to="/console"
            className="rounded-lg border border-blue-gray-200 px-3 py-1.5 font-medium hover:bg-blue-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
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

      <div className="grid grid-cols-2 gap-2 sm:gap-3">
        <Fact
          label="계좌 총액"
          value={money(num(data?.total))}
          hint="가용 + 포지션 증거금"
        />
        <Fact
          label="자본금 투입"
          value={money(capital)}
          hint="입금 − 출금 · 손익 아님"
        />
        <Fact
          label="지금까지 손익"
          value={money(earned, true)}
          hint={
            earnedPct === null
              ? "총액 − 자본금"
              : `자본금의 ${earnedPct.toFixed(1)}% · 미실현 제외`
          }
          className={tone(earned)}
        />
        <Fact
          label="최근 24시간 실현"
          value={money(realized, true)}
          className={tone(realized)}
        />
        <Fact
          label="미실현 손익"
          value={money(unrealized, true)}
          className={tone(unrealized)}
        />
        <Fact
          label="매매 상태"
          value={
            data
              ? data.leader === false
                ? "리더 아님"
                : `판 ${data.runs}개`
              : "—"
          }
          className={data?.leader === false ? "text-loss" : ""}
          hint={
            data?.leader === false
              ? "이 서버가 주문을 내지 않는다"
              : "주문을 내는 서버"
          }
        />
      </div>

      <section className="rounded-lg border border-blue-gray-100 p-3 dark:border-gray-800">
        <h3 className="mb-2 text-sm font-semibold">
          열린 포지션 {data ? `${data.positions.length}개` : ""}
        </h3>
        {data && data.positions.length === 0 ? (
          <p className="text-sm text-blue-gray-400">없음</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {(data?.positions ?? []).map((p) => {
              const pnl = num(p.unrealized);
              const pct = num(p.pct);
              return (
                <li
                  key={p.symbol}
                  className="flex items-center justify-between gap-2"
                >
                  <span>
                    {p.symbol.replace("_USDT", "")}{" "}
                    <span className="text-xs text-blue-gray-500">
                      {p.side === "long" ? "롱" : "숏"}
                    </span>
                  </span>
                  <span className={`font-mono ${tone(pnl)}`}>
                    {money(pnl, true)}
                    {pct === null
                      ? ""
                      : ` (${pct > 0 ? "+" : ""}${pct.toFixed(1)}%)`}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
