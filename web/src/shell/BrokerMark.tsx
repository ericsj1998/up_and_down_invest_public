/**
 * 시장 아이콘 · 브로커 마크 (T245 · 2026-09-09).
 *
 * 두 층으로 나눈다 — **시장 아이콘**(₿ 코인 / 📈 주식)은 스위치와 헤더 배지에, **브로커 마크**(토스 · Gate · Binance)는
 * 판·계좌 카드 옆에. 카드 옆 그림은 "이 돈이 어느 계좌에 있나" 를 답해야 하므로 브로커다.
 *
 * 로고는 전부 사용자가 넣은 파일(`web/public/brand/` · 2026-09-09): 토스 `Toss_Logo_Primary_light.png`(52 KB),
 * `gate_io.svg`, `binance.svg`. 원색 그대로 **흰 판** 위에 올린다 — 어두운 화면에서도 같다(반전·흰 판 로고는 브랜드 색을 잃는다).
 * 비트코인 ₿ 는 공개 도메인 심볼.
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

type Logo = {
  /** 원색 로고 파일 — 어두운 화면에서도 이것을 밝은 판 위에 올린다(흰 판·반전은 브랜드 색을 잃는다). */
  src: string;
  alt: string;
  title: string;
};

/** 브로커 → 로고. 여기 없는 브로커는 글자 배지로 떨어진다. */
const LOGOS: Record<string, Logo> = {
  toss: {
    src: "/brand/Toss_Logo_Primary_light.png",
    alt: "토스",
    title: "토스증권 — 시세는 토스, 체결은 페이퍼(가상)",
  },
  gate: { src: "/brand/gate_io.svg", alt: "Gate", title: "Gate.io 선물" },
  binance: { src: "/brand/binance.svg", alt: "Binance", title: "Binance 선물 (테스트넷)" },
};

/**
 * 브로커 마크 — 서버가 말한 브로커 이름으로 그린다. 로고가 있으면 로고, 없으면 글자 배지.
 * `broker` 가 없으면 아무것도 안 그린다(옛 서버 · 규칙 #8: 모르는 것을 꾸미지 않는다).
 */
export function BrokerMark({ broker, size = "sm" }: { broker?: string; size?: "sm" | "md" }) {
  if (!broker) return null;
  // 사용자 2026-09-09 "로고가 좀 잘 보이게" — 칩 16px · 머리 28px. 어두운 화면에서는 밝은 판을 깐다.
  const h = size === "md" ? "h-7" : "h-4";
  const logo = LOGOS[broker];
  if (logo) {
    return (
      <span
        className="inline-flex shrink-0 items-center rounded-md bg-white px-1.5 py-0.5 ring-1 ring-blue-gray-100 dark:ring-0"
        title={logo.title}
      >
        {/* max-w-none: 표 셀 안에서 전역 img{max-width:100%} 가 폭 계산을 꼬아 옆 칸을 덮었다 (2026-09-10 저평가 카드). */}
        <img src={logo.src} alt={logo.alt} className={`${h} w-auto max-w-none`} />
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
