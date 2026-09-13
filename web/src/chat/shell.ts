/**
 * 뜬 창·도킹 배치의 순수 조각 (T257 · T276 에서 창 둘로 일반화) — 모드 · 위치/크기 기억 · 가장자리 붙이기 판정.
 *
 * 모드: `closed`(동그란 단추만) · `float`(단추 위로 뜬 창 · 머리를 끌어 옮긴다) · `left|right|top|bottom`(화면을
 * 밀어내며 붙는다 · 경계를 끌어 크기) · `popout`(크롬 새 탭 페이지 자신의 모드 · 본 탭엔 저장되지 않는다).
 *
 * 창마다 **저장소 하나**(`createShellStore`) — 채팅(`chat-shell`)과 달력(`calendar-shell`)이 같은 조각을 쓰되 자리를
 * 따로 기억한다. 채팅용 옛 이름(`getShell` · `setShell` · `useChatShell` …)은 채팅 저장소에 묶인 얇은 껍데기다.
 */

import { useSyncExternalStore } from "react";

export type Edge = "left" | "right" | "top" | "bottom";
/** 붙는 자리 — `left` 는 맨 왼쪽(사이드바를 오른쪽으로 밀어낸다), `left-inner` 는 사이드바 오른쪽(본문을 밀어낸다). */
export type DockMode = Edge | "left-inner";
export type Mode = "closed" | "float" | DockMode | "popout";
/** xl(1280px+) 에서 사이드바의 오른쪽 경계(px) — `ml-4 + w-72`. 이 근처에 놓으면 `left-inner`. */
export const SIDEBAR_RIGHT = 304;
export const XL = 1280;

export type ShellState = {
  mode: Mode;
  /** 모드가 `closed`·`popout` 이 되기 전에 무엇이었나 — 다시 열 때 그 자리로. */
  last: Exclude<Mode, "closed" | "popout">;
  bubble: { x: number; y: number };
  float: { x: number; y: number; w: number; h: number };
  dock: Record<DockMode, number>;
  /** 끄는 동안 붙을 자리 미리보기 — 저장하지 않는다. */
  ghost: DockMode | null;
};

export const SHELL_SLOT = "chat-shell";
export const CALENDAR_SLOT = "calendar-shell";
export const CHANNEL = "updown-chat";
/** 가장자리에 이만큼 가까이 놓으면 붙는다(px). */
export const SNAP_PX = 56;
/** 이만큼 움직이기 전엔 "누른 것" 이지 "끈 것" 이 아니다(px). */
export const DRAG_PX = 6;
export const MIN_DOCK = 240;

export const DEFAULT_SHELL: ShellState = {
  mode: "closed",
  last: "float",
  bubble: { x: -1, y: -1 },
  float: { x: -1, y: -1, w: 416, h: 640 },
  dock: { left: 400, "left-inner": 400, right: 400, top: 360, bottom: 360 },
  ghost: null,
};

/** 달력은 7열 격자라 채팅보다 넓게 시작한다. */
export const DEFAULT_CALENDAR_SHELL: ShellState = {
  ...DEFAULT_SHELL,
  float: { x: -1, y: -1, w: 720, h: 600 },
  dock: { left: 520, "left-inner": 520, right: 520, top: 420, bottom: 420 },
};

export function isDock(mode: Mode): mode is DockMode {
  return mode === "left" || mode === "left-inner" || mode === "right" || mode === "top" || mode === "bottom";
}

export function clamp(value: number, lo: number, hi: number): number {
  return Math.min(Math.max(value, lo), hi);
}

/**
 * 끌어다 놓은 자리가 가장자리면 그 변, 사이드바 경계 근처면 `left-inner`, 아니면 null.
 *
 * @param x 놓은 점의 x (뷰포트).
 * @param y 놓은 점의 y.
 * @param width 뷰포트 너비.
 * @param height 뷰포트 높이.
 * @param snap 붙는 거리.
 * @param sidebarRight 사이드바 오른쪽 경계(px). null 이면 사이드바가 없다(xl 미만).
 */
export function edgeAt(
  x: number,
  y: number,
  width: number,
  height: number,
  snap = SNAP_PX,
  sidebarRight: number | null = width >= XL ? SIDEBAR_RIGHT : null,
): DockMode | null {
  const d: Record<Edge, number> = { left: x, right: width - x, top: y, bottom: height - y };
  let best: Edge = "left";
  for (const edge of ["right", "top", "bottom"] as Edge[]) {
    if (d[edge] < d[best]) best = edge;
  }
  if (d[best] <= snap) return best;
  if (sidebarRight !== null && Math.abs(x - sidebarRight) <= snap) return "left-inner";
  return null;
}

/** 도킹 경계를 끈 뒤의 크기 — 최소·최대 사이로. */
export function dockSizeAfterDrag(edge: DockMode, size: number, delta: number, viewport: number): number {
  const grow = edge === "left" || edge === "left-inner" || edge === "top" ? delta : -delta;
  return clamp(size + grow, MIN_DOCK, Math.max(MIN_DOCK, viewport - 200));
}

/** 뜬 창의 크기 조절 손잡이 — 네 변 + 네 모서리. */
export type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export const RESIZE_EDGES: ResizeEdge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
/** 뜬 창의 최소 크기(px) — 이보다 작으면 입력줄과 머리가 겹친다. */
export const MIN_FLOAT = { w: 320, h: 360 };

/**
 * 뜬 창의 손잡이를 끈 뒤의 자리·크기 — 왼쪽·위 손잡이는 반대편을 고정한 채 x/y 도 옮긴다.
 *
 * @param edge 잡은 손잡이.
 * @param start 끌기 시작 때의 창(뷰포트 좌표 · 실제 위치).
 * @param dx 포인터 이동 x.
 * @param dy 포인터 이동 y.
 * @param vw 뷰포트 너비.
 * @param vh 뷰포트 높이.
 */
export function floatAfterResize(
  edge: ResizeEdge,
  start: { x: number; y: number; w: number; h: number },
  dx: number,
  dy: number,
  vw: number,
  vh: number,
): { x: number; y: number; w: number; h: number } {
  let { x, y, w, h } = start;
  const margin = 8;
  if (edge.includes("e")) w = clamp(start.w + dx, MIN_FLOAT.w, Math.max(MIN_FLOAT.w, vw - start.x - margin));
  if (edge.includes("w")) {
    const right = start.x + start.w;
    w = clamp(start.w - dx, MIN_FLOAT.w, Math.max(MIN_FLOAT.w, right - margin));
    x = right - w;
  }
  if (edge.includes("s")) h = clamp(start.h + dy, MIN_FLOAT.h, Math.max(MIN_FLOAT.h, vh - start.y - margin));
  if (edge.includes("n")) {
    const bottom = start.y + start.h;
    h = clamp(start.h - dy, MIN_FLOAT.h, Math.max(MIN_FLOAT.h, bottom - margin));
    y = bottom - h;
  }
  return { x, y, w, h };
}

/** 저장된 배치를 읽는다 — 없거나 깨졌으면 기본. `popout` 은 다시 열 때 `closed`. */
export function readShellFrom(slot: string, defaults: ShellState): ShellState {
  try {
    const raw = localStorage.getItem(slot);
    if (!raw) return defaults;
    const got = JSON.parse(raw) as Partial<ShellState>;
    const mode = got.mode && got.mode !== "popout" ? got.mode : "closed";
    return {
      ...defaults,
      ...got,
      mode,
      dock: { ...defaults.dock, ...(got.dock ?? {}) },
      float: { ...defaults.float, ...(got.float ?? {}) },
      bubble: { ...defaults.bubble, ...(got.bubble ?? {}) },
      ghost: null,
    };
  } catch {
    return defaults;
  }
}

export function readShell(): ShellState {
  return readShellFrom(SHELL_SLOT, DEFAULT_SHELL);
}

export type ShellPatch = Partial<ShellState> | ((was: ShellState) => Partial<ShellState>);

/** 창 하나의 배치 저장소 — 읽기 · 갱신(저장) · 끄는 동안 갱신(저장 안 함) · 구독. */
export type ShellStore = {
  slot: string;
  defaults: ShellState;
  /** 크롬 새 탭 — 경로 · 창 이름 · 창 옵션. */
  popout: { path: string; name: string; features: string };
  get(): ShellState;
  set(patch: ShellPatch): void;
  setTransient(patch: ShellPatch): void;
  persist(): void;
  subscribe(fn: () => void): () => void;
};

export function createShellStore(
  slot: string,
  defaults: ShellState,
  popout: { path: string; name: string; features: string },
): ShellStore {
  let current: ShellState = defaults;
  let loaded = false;
  const listeners = new Set<() => void>();
  const ensure = (): ShellState => {
    if (!loaded) {
      current = readShellFrom(slot, defaults);
      loaded = true;
    }
    return current;
  };
  const setTransient = (patch: ShellPatch): void => {
    const was = ensure();
    const next = { ...was, ...(typeof patch === "function" ? patch(was) : patch) };
    if (next.mode !== "closed" && next.mode !== "popout") next.last = next.mode;
    current = next;
    listeners.forEach((fn) => fn());
  };
  const persist = (): void => {
    try {
      const { ghost: _ghost, ...rest } = ensure();
      localStorage.setItem(slot, JSON.stringify(rest));
    } catch {
      // 기억만 못 한다.
    }
  };
  return {
    slot,
    defaults,
    popout,
    get: ensure,
    setTransient,
    set: (patch) => {
      setTransient(patch);
      persist();
    },
    persist,
    subscribe: (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

/** 어느 컴포넌트에서나 같은 배치 상태를 본다 (`Layout` 은 밀어내기 · 창은 자기 자리). */
export function useShellStore(store: ShellStore): ShellState {
  return useSyncExternalStore(store.subscribe, store.get, () => store.defaults);
}

/** 새 탭 열기 — 본 탭 창은 닫고(단추로 다시 연다) 새 탭은 독립으로 산다. */
export function openPopoutOf(store: ShellStore): Window | null {
  const win = window.open(store.popout.path, store.popout.name, store.popout.features);
  store.set({ mode: "closed" });
  return win;
}

export const chatShell: ShellStore = createShellStore(SHELL_SLOT, DEFAULT_SHELL, {
  path: "/chat",
  name: "updown-chat",
  features: "popup=yes,width=480,height=760",
});

export const calendarShell: ShellStore = createShellStore(CALENDAR_SLOT, DEFAULT_CALENDAR_SHELL, {
  path: "/calendar/popout",
  name: "updown-calendar",
  features: "popup=yes,width=860,height=720",
});

// ── 채팅용 옛 이름 — 채팅 저장소에 묶인 껍데기 ──────────────────────────────
export function getShell(): ShellState {
  return chatShell.get();
}

export function setShell(patch: ShellPatch): void {
  chatShell.set(patch);
}

/** 끄는 동안의 갱신 — 화면은 따라오지만 저장하지 않는다. 놓을 때 `persistShell`. */
export function setShellTransient(patch: ShellPatch): void {
  chatShell.setTransient(patch);
}

export function persistShell(): void {
  chatShell.persist();
}

export function useChatShell(): ShellState {
  return useShellStore(chatShell);
}

/** 채팅 새 탭 — 같은 대화는 서버가 들고 있다. */
export function openPopout(): Window | null {
  return openPopoutOf(chatShell);
}
