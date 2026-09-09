/**
 * 장 시간 배지 (T245) — "정규장 · 마감 05:00" / "장 마감 · 다음 개장 22:30". 24시간 장은 아무것도 안 그린다.
 *
 * 값은 서버 캘린더(`/exchange/market-status`)가 준다 — 화면은 시각을 사람 시간대로 보여 주기만 한다(규칙 #7: 저장은 UTC).
 */
import { useEffect, useState } from "react";
import { marketStatus, type MarketStatusView } from "../api";

const POLL_MS = 60_000;

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** 오늘이 아니면 요일까지 — "22:30" 만 보면 오늘인지 월요일인지 모른다. */
function when(iso: string): string {
  const at = new Date(iso);
  const sameDay = at.toDateString() === new Date().toDateString();
  return sameDay ? hhmm(iso) : `${at.toLocaleDateString("ko-KR", { weekday: "short" })} ${hhmm(iso)}`;
}

export function MarketHours({ market, alwaysOpen }: { market: string; alwaysOpen?: boolean }) {
  const [status, setStatus] = useState<MarketStatusView | null>(null);
  useEffect(() => {
    if (alwaysOpen) return;
    let alive = true;
    const pull = () =>
      marketStatus(market)
        .then((got) => alive && setStatus(got))
        .catch(() => alive && setStatus(null));
    pull();
    const timer = window.setInterval(pull, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [market, alwaysOpen]);
  if (alwaysOpen || !status || status.always_open) return null;
  const open = status.state === "open";
  const unknown = status.state === "unknown";
  const text = open
    ? `정규장${status.next_close ? ` · 마감 ${hhmm(status.next_close)}` : ""}`
    : unknown
      ? `장 시간 모름 · ${status.why}`
      : `장 마감${status.next_open ? ` · 다음 개장 ${when(status.next_open)}` : ""}`;
  return (
    <span
      className={`chip${open ? " gain" : unknown ? " loss" : ""}`}
      title={`${status.why} (${status.session})`}
    >
      {text}
    </span>
  );
}
