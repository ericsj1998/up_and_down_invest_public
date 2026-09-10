/**
 * 채팅 창 배치의 순수 조각 (T257) — 모드 · 위치/크기 기억 · 가장자리 붙이기 판정 · 탭 간 통지.
 *
 * 모드: `closed`(동그란 단추만) · `float`(단추 위로 뜬 창 · 머리를 끌어 옮긴다) · `left|right|top|bottom`(화면을
 * 밀어내며 붙는다 · 경계를 끌어 크기) · `popout`(크롬 새 탭 페이지 자신의 모드 · 본 탭엔 저장되지 않는다).
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

export function readShell(): ShellState {
  try {
    const raw = localStorage.getItem(SHELL_SLOT);
    if (!raw) return DEFAULT_SHELL;
    const got = JSON.parse(raw) as Partial<ShellState>;
    const mode = got.mode && got.mode !== "popout" ? got.mode : "closed";
    return {
      ...DEFAULT_SHELL,
      ...got,
      mode,
      dock: { ...DEFAULT_SHELL.dock, ...(got.dock ?? {}) },
      float: { ...DEFAULT_SHELL.float, ...(got.float ?? {}) },
      bubble: { ...DEFAULT_SHELL.bubble, ...(got.bubble ?? {}) },
      ghost: null,
    };
  } catch {
    return DEFAULT_SHELL;
  }
}

let current: ShellState = DEFAULT_SHELL;
let loaded = false;
const listeners = new Set<() => void>();

function ensure(): ShellState {
  if (!loaded) {
    current = readShell();
    loaded = true;
  }
  return current;
}

export function getShell(): ShellState {
  return ensure();
}

export function setShell(patch: Partial<ShellState> | ((was: ShellState) => Partial<ShellState>)): void {
  setShellTransient(patch);
  persistShell();
}

/** 끄는 동안의 갱신 — 화면은 따라오지만 저장하지 않는다. 놓을 때 `persistShell`. */
export function setShellTransient(patch: Partial<ShellState> | ((was: ShellState) => Partial<ShellState>)): void {
  const was = ensure();
  const next = { ...was, ...(typeof patch === "function" ? patch(was) : patch) };
  if (next.mode !== "closed" && next.mode !== "popout") next.last = next.mode;
  current = next;
  listeners.forEach((fn) => fn());
}

export function persistShell(): void {
  try {
    const { ghost: _ghost, ...rest } = ensure();
    localStorage.setItem(SHELL_SLOT, JSON.stringify(rest));
  } catch {
    // 기억만 못 한다.
  }
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** 어느 컴포넌트에서나 같은 배치 상태를 본다 (`Layout` 은 밀어내기 · `ChatShell` 은 창). */
export function useChatShell(): ShellState {
  return useSyncExternalStore(subscribe, getShell, () => DEFAULT_SHELL);
}

/** 새 탭 열기 — 본 탭 창은 닫고(단추로 다시 연다) 새 탭은 독립으로 산다. 같은 대화는 서버가 들고 있다. */
export function openPopout(): Window | null {
  const win = window.open("/chat", "updown-chat", "popup=yes,width=480,height=760");
  setShell({ mode: "closed" });
  return win;
}
