/**
 * 작은 부품 — 카드 · 바로가기 단추 · 오류 카드 · 숫자 칸. Tailwind 만 쓴다(본체의 Material Tailwind 없음).
 */

import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900 ${className}`}
    >
      {children}
    </section>
  );
}

/** 바로가기 — 주소를 모르면 회색 비활성(왜 없는지 말풍선). */
export function LinkButton({ label, href }: { label: string; href: string | null }) {
  const base = "inline-flex items-center rounded-lg border px-3 py-1.5 text-xs font-semibold";
  if (!href) {
    return (
      <span
        className={`${base} cursor-not-allowed border-gray-200 text-gray-400 dark:border-gray-800 dark:text-gray-600`}
        title="바로가기 주소를 아직 모른다 — T442 §5"
      >
        {label}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={`${base} border-gray-300 text-gray-800 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-100 dark:hover:bg-gray-800`}
    >
      {label} ↗
    </a>
  );
}

/** 오류는 조용히 넘기지 않는다 — 서버가 적은 이유를 그대로 보인다. */
export function ErrorCard({ title, message }: { title: string; message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-loss/40 bg-loss-wash p-3 text-sm text-loss">
      <div className="font-semibold">⛔ {title}</div>
      <div className="mt-1 break-words font-mono text-xs">{message}</div>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-gray-100 p-3 dark:border-gray-800" title={hint}>
      <div className="text-[11px] uppercase text-gray-500">{label}</div>
      <div className="mt-1 text-base font-semibold">{value}</div>
      {hint ? <div className="mt-1 text-[10px] leading-snug text-gray-500">{hint}</div> : null}
    </div>
  );
}
