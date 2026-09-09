/**
 * 시장 아이콘 · 브로커 마크 (T245 · 2026-09-09).
 *
 * 두 층으로 나눈다 — **시장 아이콘**(₿ 코인 / 📈 주식)은 스위치와 헤더 배지에, **브로커 마크**(토스 · GATE · BINANCE)는
 * 판·계좌 카드 옆에. 카드 옆 그림은 "이 돈이 어느 계좌에 있나" 를 답해야 하므로 브로커다.
 *
 * 토스 로고는 `web/public/brand/Toss_Logo_Primary*.png` (사용자가 넣음 · 2026-09-09). 비트코인 ₿ 는 공개 도메인 심볼.
 * 로고가 없는 브로커는 이름 글자 배지 — 없는 그림을 지어내지 않는다.
 */
import { ArrowTrendingUpIcon } from "@heroicons/react/24/solid";
import type { MarketGroup } from "./marketGroup";

/** 비트코인 심볼 — 주황 원 안의 ₿ (public domain). */
export function BitcoinIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <circle cx="16" cy="16" r="16" fill="#F7931A" />
      <path
        fill="#fff"
        d="M22.6 14.1c.3-2.1-1.3-3.2-3.5-4l.7-2.8-1.7-.4-.7 2.8c-.5-.1-.9-.2-1.4-.3l.7-2.8-1.7-.4-.7 2.8-1.1-.3-2.4-.6-.5 1.8s1.3.3 1.2.3c.7.2.8.6.8 1l-.8 3.2c0 0 .1 0 .2.1l-.2-.1-1.1 4.5c-.1.2-.3.5-.8.4 0 0-1.2-.3-1.2-.3l-.9 2 2.2.6 1.2.3-.7 2.9 1.7.4.7-2.8c.5.1.9.2 1.4.4l-.7 2.8 1.7.4.7-2.9c3 .6 5.2.3 6.1-2.3.8-2.1 0-3.4-1.6-4.2 1.1-.3 2-1 2.2-2.6zm-4 5.6c-.5 2.2-4.2 1-5.4.7l1-3.9c1.2.3 5 .9 4.4 3.2zm.5-5.6c-.5 2-3.6.9-4.6.7l.9-3.5c1 .2 4.2.7 3.7 2.8z"
      />
    </svg>
  );
}

/** 주식 아이콘 — 파란 원 안의 오름 화살표. */
export function StockIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <span
      className={`inline-grid place-items-center rounded-full bg-[#0064FF] text-white ${className}`}
      aria-hidden="true"
    >
      <ArrowTrendingUpIcon className="h-[70%] w-[70%]" />
    </span>
  );
}

/** 시장 묶음 아이콘 — 스위치·헤더 배지가 쓴다. */
export function GroupIcon({ group, className }: { group: MarketGroup; className?: string }) {
  return group === "coin" ? <BitcoinIcon className={className} /> : <StockIcon className={className} />;
}

/**
 * 브로커 마크 — 서버가 말한 브로커 이름으로 그린다. 로고가 있으면 로고, 없으면 글자 배지.
 * `broker` 가 없으면 아무것도 안 그린다(옛 서버 · 규칙 #8: 모르는 것을 꾸미지 않는다).
 */
export function BrokerMark({ broker, size = "sm" }: { broker?: string; size?: "sm" | "md" }) {
  if (!broker) return null;
  const h = size === "md" ? "h-5" : "h-3.5";
  if (broker === "toss") {
    return (
      <span className="inline-flex shrink-0 items-center" title="토스증권 — 시세는 토스, 체결은 페이퍼(가상)">
        <img src="/brand/Toss_Logo_Primary.png" alt="토스" className={`${h} w-auto dark:hidden`} />
        <img src="/brand/Toss_Logo_Primary_White.png" alt="토스" className={`hidden ${h} w-auto dark:inline`} />
      </span>
    );
  }
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded px-1 font-mono text-[10px] font-semibold uppercase leading-4 text-blue-gray-600 ring-1 ring-blue-gray-200 dark:text-blue-gray-200 dark:ring-gray-700 ${size === "md" ? "text-xs" : ""}`}
      title={`브로커 ${broker}`}
    >
      {broker}
    </span>
  );
}
