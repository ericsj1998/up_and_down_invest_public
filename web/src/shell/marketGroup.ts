/**
 * 시장 전환 — 코인 | 주식 (T245 · 2026-09-09).
 *
 * Demo/실계좌 스위치가 "어느 돈을 보는가"를 정하듯, 이 값은 "어느 시장을 보는가"를 정한다. 콘솔·판 목록·리포트에
 * 걸리는 **전제**라 페이지가 아니라 쿠키(`updown_market`)에 산다. 새로고침 없이 바뀐다 — 화면은 같은 서버의
 * 것이고 거르는 기준만 달라지므로(`updown_mode` 와 다른 점).
 *
 * ⛔ 시장 이름 리터럴로 가르지 않는다 — 어느 시장이 코인이고 주식인지는 서버(`/exchange/markets` 의 `group`)가
 * 말한다. 여기 있는 것은 "고른 것" 과 "고른 것으로 거르기" 뿐이다.
 */
import { useCallback, useSyncExternalStore } from "react";
import type { MarketInfo } from "../api";

export type MarketGroup = "coin" | "stock";

export const GROUP_LABEL: Record<MarketGroup, string> = { coin: "코인", stock: "주식" };

const COOKIE = "updown_market";
const EVENT = "updown-market";

/** 쿠키에서 읽는다 — 없거나 모르는 값이면 코인 (T245: 첫 화면은 코인 콘솔). */
export function marketGroupCookie(cookie: string = document.cookie): MarketGroup {
  return /(?:^|;\s*)updown_market=stock(?:;|$)/.test(cookie) ? "stock" : "coin";
}

/** 스위치 — 쿠키를 굽고 구독자에게 알린다. 새로고침하지 않는다. */
export function setMarketGroup(next: MarketGroup): void {
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${COOKIE}=${next}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

/** 지금 고른 시장과 바꾸는 함수. 같은 탭의 모든 구독자가 한 번에 바뀐다. */
export function useMarketGroup(): [MarketGroup, (next: MarketGroup) => void] {
  const group = useSyncExternalStore(subscribe, () => marketGroupCookie(), () => "coin" as const);
  const set = useCallback((next: MarketGroup) => setMarketGroup(next), []);
  return [group, set];
}

/** 서버가 준 목록 중 이 시장 묶음의 것만. `group` 이 없는 옛 서버 응답은 전부 코인으로 본다(코인 화면 불변). */
export function pickMarkets(all: readonly MarketInfo[], group: MarketGroup): MarketInfo[] {
  return all.filter((m) => (m.group ?? "coin") === group);
}

/** 시장 이름의 묶음 — 목록에 없으면 코인(옛 판·옛 서버). */
export function groupOfName(all: readonly MarketInfo[], name: string): MarketGroup {
  return all.find((m) => m.name === name)?.group ?? "coin";
}

/** 시장의 능력(배율·펀딩·24h) — 서버가 말한 값. 모르면 코인처럼(전부 보인다 · 코인 화면 불변). */
export function capsOfName(
  all: readonly MarketInfo[],
  name: string,
): { leverage: boolean; funding: boolean; alwaysOpen: boolean } {
  const m = all.find((one) => one.name === name);
  return {
    leverage: m?.leverage ?? true,
    funding: m?.funding ?? true,
    alwaysOpen: m?.always_open ?? true,
  };
}

/** 매매법이 이 묶음에서 도나 — `groups` 가 없는 옛 서버 응답은 어디서나. */
export function bookInGroup(book: { groups?: readonly string[] }, group: MarketGroup): boolean {
  return !book.groups || book.groups.includes(group);
}

/** 시장 이름의 브로커 — 서버가 말한 값. 없으면 undefined (마크를 안 그린다 · 꾸미지 않는다). */
export function brokerOfName(all: readonly MarketInfo[], name: string): string | undefined {
  return all.find((m) => m.name === name)?.broker;
}
