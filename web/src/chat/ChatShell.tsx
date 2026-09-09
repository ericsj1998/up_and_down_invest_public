/**
 * 채팅 창 **틀** (T257) — 동그란 단추(드래그 · 어디든) · 뜬 창(머리 드래그) · 가장자리 도킹(밀어내기 · 경계 드래그 ·
 * 머리를 잡아 끌면 떨어져 나와 다시 붙일 수 있다) · 크롬 새 탭 · 열고 닫는 애니메이션. 몸통은 `ChatPanel`.
 *
 * 드래그는 요소가 아니라 **window** 의 포인터 이벤트로 잡는다 — 끄는 도중 모드가 바뀌어(도킹 → 뜬 창) 창이 다시
 * 그려져도 드래그가 끊기지 않는다. 배치 상태는 `shell.ts` 가 단일 출처이고 `Layout` 이 같은 상태로 본문을 밀어낸다.
 */

import { ArrowsPointingOutIcon, ArrowTopRightOnSquareIcon, SparklesIcon, XMarkIcon } from "@heroicons/react/24/solid";
import { useCallback, useEffect, useState } from "react";
import type { Who } from "../api";
import { ChatPanel, SLOGAN } from "./ChatPanel";
import {
  CHANNEL,
  clamp,
  DRAG_PX,
  dockSizeAfterDrag,
  edgeAt,
  getShell,
  isDock,
  openPopout,
  persistShell,
  setShell,
  setShellTransient,
  useChatShell,
  type Edge,
  type Mode,
} from "./shell";

const EDGES: Array<{ edge: Edge; glyph: string; title: string }> = [
  { edge: "left", glyph: "◀", title: "왼쪽에 붙인다 (화면을 밀어낸다)" },
  { edge: "top", glyph: "▲", title: "위에 붙인다" },
  { edge: "bottom", glyph: "▼", title: "아래에 붙인다" },
  { edge: "right", glyph: "▶", title: "오른쪽에 붙인다" },
];

const BUBBLE = 56;
const HEAD_BG = "bg-sky-50 dark:bg-sky-950/40 border-sky-100 dark:border-sky-900";

type DragKind = "bubble" | "float" | "docked";

/**
 * window 포인터 이벤트로 끄는 한 번의 드래그. 짧게 누르고 뗀 것(6px 미만)은 `onTap`.
 * 단추는 어디든 놓이고(가장자리에 놓아도 붙지 않는다), 창은 가장자리에 놓으면 붙는다.
 */
function beginDrag(e: React.PointerEvent, kind: DragKind, onTap?: () => void) {
  if (e.button !== 0) return;
  e.preventDefault();
  const startX = e.clientX;
  const startY = e.clientY;
  const start = getShell();
  const origin = kind === "bubble" ? bubbleBase(start.bubble) : floatBase(start.float);
  let moved = false;
  let grabX = 0;
  let grabY = 0;
  const move = (ev: PointerEvent) => {
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (!moved) {
      if (Math.hypot(dx, dy) < DRAG_PX) return;
      moved = true;
      if (kind === "docked") {
        // ⭐ 붙어 있던 창을 떼어 낸다 — 포인터 아래에 머리가 오게 뜬 창으로 바꾼다.
        const w = Math.min(start.float.w, window.innerWidth - 16);
        grabX = Math.min(w / 2, 160);
        grabY = 20;
        setShellTransient({ mode: "float", float: { ...start.float, x: ev.clientX - grabX, y: ev.clientY - grabY } });
      }
    }
    if (kind === "bubble") {
      setShellTransient({
        bubble: { x: clamp(origin.x + dx, 0, window.innerWidth - BUBBLE), y: clamp(origin.y + dy, 0, window.innerHeight - BUBBLE) },
      });
      return;
    }
    const x = kind === "docked" ? ev.clientX - grabX : origin.x + dx;
    const y = kind === "docked" ? ev.clientY - grabY : origin.y + dy;
    setShellTransient((was) => ({
      float: { ...was.float, x: clamp(x, -was.float.w + 80, window.innerWidth - 80), y: clamp(y, 0, window.innerHeight - 40) },
      ghost: edgeAt(ev.clientX, ev.clientY, window.innerWidth, window.innerHeight),
    }));
  };
  const up = (ev: PointerEvent) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    if (!moved) {
      onTap?.();
      return;
    }
    if (kind !== "bubble") {
      const edge = edgeAt(ev.clientX, ev.clientY, window.innerWidth, window.innerHeight);
      setShellTransient({ ghost: null, ...(edge ? { mode: edge } : {}) });
    }
    persistShell();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

/** 창 머리 — 제목 · 슬로건 · 붙이기 아이콘 · 떠 있음 · 새 탭 · 닫기. 잡아 끌면 옮긴다(도킹이면 떼어 낸다). */
export function ChatHeader({ title, mode }: { title: string; mode: Mode }) {
  const draggable = mode === "float" || isDock(mode);
  return (
    <div
      className={`flex select-none items-center gap-2 border-b px-3 py-2 ${HEAD_BG} ${draggable ? "cursor-move touch-none" : ""}`}
      onPointerDown={draggable ? (e) => beginDrag(e, mode === "float" ? "float" : "docked") : undefined}
      title={draggable ? "잡아 끌어 옮긴다 — 가장자리에 놓으면 붙는다" : undefined}
    >
      <SparklesIcon className="h-5 w-5 shrink-0 text-sky-600 dark:text-sky-300" />
      <div className="min-w-0">
        <b className="block truncate text-sm">{title}</b>
        <span className="block text-xs text-sky-700/80 dark:text-sky-200/80">{SLOGAN}</span>
      </div>
      <span className="ml-auto flex shrink-0 gap-0.5" onPointerDown={(e) => e.stopPropagation()}>
        {mode !== "popout"
          ? EDGES.map(({ edge, glyph, title: t }) => (
              <button key={edge} type="button" className={`btn small ${mode === edge ? "primary" : ""}`} title={t} onClick={() => setShell({ mode: edge })}>
                {glyph}
              </button>
            ))
          : null}
        {mode !== "popout" ? (
          <button type="button" className={`btn small ${mode === "float" ? "primary" : ""}`} title="떠 있는 창" onClick={() => setShell({ mode: "float" })}>
            <ArrowsPointingOutIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
        {mode !== "popout" ? (
          <button type="button" className="btn small" title="크롬 새 탭으로" onClick={() => openPopout()}>
            <ArrowTopRightOnSquareIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
        {mode !== "popout" ? (
          <button type="button" className="btn small" title="닫는다" onClick={() => setShell({ mode: "closed" })}>
            <XMarkIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </span>
    </div>
  );
}

/** 머리 + 몸통. */
export function ChatFrame({ who, mode, className }: { who: Who | null; mode: Mode; className?: string }) {
  const [title, setTitle] = useState("AI 투자 어시스턴트");
  const onTitle = useCallback((t: string) => setTitle(t), []);
  return (
    <section className={`flex min-h-0 flex-col overflow-hidden bg-white dark:bg-gray-900 ${className ?? ""}`} aria-label="AI 투자 어시스턴트">
      <ChatHeader title={title} mode={mode} />
      <ChatPanel who={who} onTitle={onTitle} />
    </section>
  );
}

/** 동그란 단추 + 뜬 창 + 붙을 자리 미리보기. 도킹된 창은 `DockedChat`(Layout 안)이 그린다. */
export function ChatShell({ who }: { who: Who | null }) {
  const shell = useChatShell();

  // 새 탭이 열리고 닫히는 것을 듣는다 — 본 탭은 그동안 창을 숨긴다.
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const bc = new BroadcastChannel(CHANNEL);
    bc.onmessage = (e: MessageEvent<{ type?: string }>) => {
      if (e.data?.type === "popout-open") setShell({ mode: "popout" });
      if (e.data?.type === "popout-closed" && getShell().mode === "popout") setShell({ mode: "closed" });
    };
    return () => bc.close();
  }, []);

  const bubble = bubbleBase(shell.bubble);
  const float = floatBase(shell.float);
  const open = shell.mode === "float";
  const tap = () => {
    if (getShell().mode === "popout") {
      window.open("/chat", "updown-chat");
      return;
    }
    setShell((was) => ({ mode: was.mode === "closed" ? was.last : "closed" }));
  };

  return (
    <>
      {shell.ghost ? <div className={`chat-ghost chat-ghost-${shell.ghost}`} aria-hidden="true" /> : null}
      <button
        type="button"
        className="chat-bubble fixed z-[60] grid h-14 w-14 touch-none select-none place-items-center rounded-full bg-sky-600 text-white shadow-xl hover:bg-sky-500 dark:bg-sky-400 dark:text-gray-900 dark:hover:bg-sky-300"
        style={{ left: bubble.x, top: bubble.y }}
        aria-label={shell.mode === "closed" ? "AI 투자 어시스턴트 열기" : "AI 투자 어시스턴트 닫기"}
        title={shell.mode === "popout" ? "새 탭에서 열려 있다 — 누르면 그 탭으로" : "AI 투자 어시스턴트 — 누르면 열고, 끌어서 어디든 옮긴다"}
        onPointerDown={(e) => beginDrag(e, "bubble", tap)}
      >
        {shell.mode === "closed" || shell.mode === "popout" ? <SparklesIcon className="h-7 w-7" /> : <XMarkIcon className="h-7 w-7" />}
        {shell.mode === "popout" ? <span className="absolute right-1 top-1 h-3 w-3 rounded-full bg-emerald-400" /> : null}
      </button>

      {open ? (
        <div
          className="chat-pop fixed z-50 flex flex-col overflow-hidden rounded-2xl border border-blue-gray-100 shadow-2xl dark:border-gray-800"
          style={{ left: float.x, top: float.y, width: shell.float.w, height: shell.float.h, maxWidth: "96vw", maxHeight: "92vh" }}
        >
          <ChatFrame who={who} mode="float" className="flex-1" />
        </div>
      ) : null}
    </>
  );
}

/** 가장자리에 붙은 창 — `Layout` 이 본문 옆(또는 위/아래)에 둔다. 경계를 끌어 크기를 바꾼다. */
export function DockedChat({ who }: { who: Who | null }) {
  const shell = useChatShell();
  const edge = isDock(shell.mode) ? shell.mode : null;
  const horizontal = edge === "left" || edge === "right";
  const onSplit = (e: React.PointerEvent) => {
    if (!edge || e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const base = shell.dock[edge];
    const move = (ev: PointerEvent) => {
      const viewport = horizontal ? window.innerWidth : window.innerHeight;
      const size = dockSizeAfterDrag(edge, base, horizontal ? ev.clientX - startX : ev.clientY - startY, viewport);
      setShellTransient((was) => ({ dock: { ...was.dock, [edge]: size } }));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      persistShell();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  if (!edge) return null;
  const size = shell.dock[edge];
  const splitter = (
    <div
      className={`chat-splitter shrink-0 touch-none rounded bg-sky-100 hover:bg-sky-300 dark:bg-sky-900 dark:hover:bg-sky-700 ${horizontal ? "w-1.5 cursor-col-resize" : "h-1.5 cursor-row-resize"}`}
      role="separator"
      aria-orientation={horizontal ? "vertical" : "horizontal"}
      title="끌어서 크기를 바꾼다"
      onPointerDown={onSplit}
    />
  );
  const frame = (
    <div className="chat-dock shrink-0" style={horizontal ? { width: size } : { height: size }}>
      <ChatFrame who={who} mode={edge} className="h-full rounded-xl border border-blue-gray-100 shadow-sm dark:border-gray-800" />
    </div>
  );
  return edge === "left" || edge === "top" ? (
    <>
      {frame}
      {splitter}
    </>
  ) : (
    <>
      {splitter}
      {frame}
    </>
  );
}

/** 크롬 새 탭 — 레이아웃 없이 창만. 열리고 닫힘을 본 탭에 알린다. */
export function ChatPopout({ who }: { who: Who | null }) {
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const bc = new BroadcastChannel(CHANNEL);
    bc.postMessage({ type: "popout-open" });
    const bye = () => bc.postMessage({ type: "popout-closed" });
    window.addEventListener("beforeunload", bye);
    return () => {
      window.removeEventListener("beforeunload", bye);
      bc.close();
    };
  }, []);
  return (
    <div className="flex h-screen flex-col bg-blue-gray-50/50 dark:bg-gray-950">
      <ChatFrame who={who} mode="popout" className="flex-1" />
    </div>
  );
}

function bubbleBase(at: { x: number; y: number }): { x: number; y: number } {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  if (at.x >= 0 && at.y >= 0) return { x: clamp(at.x, 0, window.innerWidth - BUBBLE), y: clamp(at.y, 0, window.innerHeight - BUBBLE) };
  return { x: window.innerWidth - 80, y: window.innerHeight - 80 };
}

function floatBase(at: { x: number; y: number; w: number; h: number }): { x: number; y: number } {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  if (at.x >= 0 && at.y >= 0) return { x: clamp(at.x, -at.w + 80, window.innerWidth - 80), y: clamp(at.y, 0, window.innerHeight - 40) };
  return { x: Math.max(8, window.innerWidth - at.w - 24), y: Math.max(8, window.innerHeight - at.h - 100) };
}
