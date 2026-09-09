/**
 * 사이드바 접힘 상태 (T257 · 사용자 2026-09-10 "좌측 사이드바도 접었다 폈다") — 브라우저가 기억하고 `Layout` ·
 * `Sidenav` · 채팅 창(붙을 자리 판정)이 같은 값을 본다.
 */

import { useSyncExternalStore } from "react";

export const SIDENAV_SLOT = "sidenav-collapsed";
/** 펼친 사이드바가 차지하는 폭(px) — `ml-4 + w-72 + 여백` = `xl:ml-80`. */
export const SIDE_WIDE = 320;
/** 접힌 사이드바 폭(px) — 아이콘만. */
export const SIDE_NARROW = 88;

let collapsed = false;
let loaded = false;
const listeners = new Set<() => void>();

function ensure(): boolean {
  if (!loaded) {
    try {
      collapsed = localStorage.getItem(SIDENAV_SLOT) === "1";
    } catch {
      collapsed = false;
    }
    loaded = true;
  }
  return collapsed;
}

export function isSidenavCollapsed(): boolean {
  return ensure();
}

export function setSidenavCollapsed(next: boolean): void {
  ensure();
  collapsed = next;
  try {
    localStorage.setItem(SIDENAV_SLOT, next ? "1" : "0");
  } catch {
    // 기억만 못 한다.
  }
  listeners.forEach((fn) => fn());
}

export function toggleSidenav(): void {
  setSidenavCollapsed(!ensure());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useSidenavCollapsed(): boolean {
  return useSyncExternalStore(subscribe, isSidenavCollapsed, () => false);
}

/** 지금 사이드바가 차지하는 폭(px) — 본문 여백과 채팅 "사이드바 오른쪽" 판정이 쓴다. */
export function sidebarWidth(): number {
  return ensure() ? SIDE_NARROW : SIDE_WIDE;
}
