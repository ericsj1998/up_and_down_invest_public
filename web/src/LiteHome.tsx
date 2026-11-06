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
import { Link } from "react-router-dom";
import { type LiteHomeData, liteHome } from "./api";

const POLL_MS = 60_000;
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
  const earnedPct =
    earned !== null && capital ? (earned / capital) * 100 : null;
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

      {/* 사람이 먼저 묻는 것 둘 — 지금 얼마 · 번 돈 */}
      <section className={`${CARD} grid gap-4 p-5 sm:grid-cols-2`}>
        <div>
          <div className="text-sm font-medium text-blue-gray-500 dark:text-blue-gray-300">
            계좌 총액
          </div>
          <div className="mt-1 break-words font-mono text-3xl font-bold text-blue-gray-900 dark:text-white">
            {money(total)}
            <span className="ml-1.5 text-base font-medium text-blue-gray-500 dark:text-blue-gray-300">
              USDT
            </span>
          </div>
          <div className="mt-1 text-xs text-blue-gray-500 dark:text-blue-gray-300">
            자본금 투입 {money(capital)} USDT · 손익 아님
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
        <div className="col-span-2 sm:col-span-1">
          <Small
            label="매매 상태"
            value={data ? (notLeader ? "리더 아님" : `판 ${data.runs}개`) : "—"}
            className={notLeader ? "text-loss" : ""}
            hint={
              notLeader ? "이 서버가 주문을 내지 않는다" : "주문을 내는 서버"
            }
          />
        </div>
      </div>

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
            {(data?.positions ?? []).map((p) => {
              const pnl = num(p.unrealized);
              const pct = num(p.pct);
              return (
                <li
                  key={p.symbol}
                  className="flex items-center justify-between gap-2 py-2"
                >
                  <span className="flex items-center gap-2 font-medium text-blue-gray-900 dark:text-white">
                    {p.symbol.replace("_USDT", "")}
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                        p.side === "long"
                          ? "bg-gain/10 text-gain"
                          : "bg-loss-wash text-loss"
                      }`}
                    >
                      {p.side === "long" ? "롱" : "숏"}
                    </span>
                  </span>
                  <span className={`font-mono ${tone(pnl)}`}>
                    {money(pnl, true)} USDT
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
