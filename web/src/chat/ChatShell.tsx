/**
 * 채팅 창 **틀** (T257) — 동그란 단추(드래그) · 뜬 창(머리 드래그) · 가장자리 도킹(밀어내기 · 경계 드래그) ·
 * 크롬 새 탭 · 열고 닫는 애니메이션. 몸통은 `ChatPanel`.
 *
 * 배치 상태는 `shell.ts`(브라우저 기억 · 탭 간 통지)가 단일 출처다. `Layout` 은 같은 상태를 읽어 본문을 밀어낸다.
 */

import { ArrowsPointingOutIcon, ArrowTopRightOnSquareIcon, SparklesIcon, XMarkIcon } from "@heroicons/react/24/solid";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Who } from "../api";
import { ChatPanel, SLOGAN } from "./ChatPanel";
import { CHANNEL, clamp, DRAG_PX, dockSizeAfterDrag, edgeAt, getShell, isDock, openPopout, setShell, useChatShell, type Edge, type Mode } from "./shell";

const EDGES: Array<{ edge: Edge; glyph: string; title: string }> = [
  { edge: "left", glyph: "◀", title: "왼쪽에 붙인다 (화면을 밀어낸다)" },
  { edge: "top", glyph: "▲", title: "위에 붙인다" },
  { edge: "bottom", glyph: "▼", title: "아래에 붙인다" },
  { edge: "right", glyph: "▶", title: "오른쪽에 붙인다" },
];

type Drag = { id: number; startX: number; startY: number; originX: number; originY: number; moved: boolean };

/** 포인터로 끄는 공통 조각 — 누른 자리에서 `DRAG_PX` 넘게 움직여야 "끈 것" 이다. */
function useDrag(onMove: (dx: number, dy: number, x: number, y: number) => void, onEnd: (moved: boolean, x: number, y: number) => void) {
  const drag = useRef<Drag | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    drag.current = { id: e.pointerId, startX: e.clientX, startY: e.clientY, originX: 0, originY: 0, moved: false };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < DRAG_PX) return;
    d.moved = true;
    onMove(dx, dy, e.clientX, e.clientY);
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    onEnd(d.moved, e.clientX, e.clientY);
  };
  return { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp };
}

/** 창 머리 — 제목 · 슬로건 · 붙이기 아이콘 · 새 탭 · 닫기. `float` 이면 머리를 끌어 옮긴다. */
export function ChatHeader({
  title,
  mode,
  onHeaderDrag,
}: {
  title: string;
  mode: Mode;
  onHeaderDrag?: ReturnType<typeof useDrag>;
}) {
  return (
    <div
      className={`flex select-none items-center gap-2 border-b border-blue-gray-100 px-3 py-2 dark:border-gray-800 ${mode === "float" ? "cursor-move" : ""}`}
      {...(onHeaderDrag ?? {})}
    >
      <SparklesIcon className="h-5 w-5 shrink-0 text-blue-gray-500" />
      <div className="min-w-0">
        <b className="block truncate text-sm">{title}</b>
        <span className="faint block text-xs">{SLOGAN}</span>
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
export function ChatFrame({ who, mode, onHeaderDrag, className }: { who: Who | null; mode: Mode; onHeaderDrag?: ReturnType<typeof useDrag>; className?: string }) {
  const [title, setTitle] = useState("AI 투자 어시스턴트");
  const onTitle = useCallback((t: string) => setTitle(t), []);
  return (
    <section className={`flex min-h-0 flex-col overflow-hidden bg-white dark:bg-gray-900 ${className ?? ""}`} aria-label="AI 투자 어시스턴트">
      <ChatHeader title={title} mode={mode} onHeaderDrag={onHeaderDrag} />
      <ChatPanel who={who} onTitle={onTitle} />
    </section>
  );
}

/** 동그란 단추 + 뜬 창. 도킹된 창은 `DockedChat`(Layout 안)이 그린다. */
export function ChatShell({ who }: { who: Who | null }) {
  const shell = useChatShell();
  const [ghost, setGhost] = useState<Edge | null>(null);
  const [bubbleAt, setBubbleAt] = useState<{ x: number; y: number } | null>(null);
  const [floatAt, setFloatAt] = useState<{ x: number; y: number } | null>(null);

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

  const bubbleDrag = useDrag(
    (dx, dy, x, y) => {
      const base = bubbleBase(shell.bubble);
      setBubbleAt({ x: clamp(base.x + dx, 8, window.innerWidth - 64), y: clamp(base.y + dy, 8, window.innerHeight - 64) });
      setGhost(edgeAt(x, y, window.innerWidth, window.innerHeight));
    },
    (moved, x, y) => {
      setGhost(null);
      if (!moved) {
        // 짧게 누름 = 열고 닫기.
        if (shell.mode === "popout") {
          window.open("/chat", "updown-chat");
          return;
        }
        setShell((was) => ({ mode: was.mode === "closed" ? was.last : "closed" }));
        return;
      }
      const edge = edgeAt(x, y, window.innerWidth, window.innerHeight);
      if (edge) {
        setBubbleAt(null);
        setShell({ mode: edge });
        return;
      }
      const at = bubbleAt ?? bubbleBase(shell.bubble);
      setBubbleAt(null);
      setShell({ bubble: at });
    },
  );

  const headerDrag = useDrag(
    (dx, dy, x, y) => {
      const base = floatBase(shell.float);
      setFloatAt({ x: clamp(base.x + dx, 0, window.innerWidth - 80), y: clamp(base.y + dy, 0, window.innerHeight - 60) });
      setGhost(edgeAt(x, y, window.innerWidth, window.innerHeight));
    },
    (moved, x, y) => {
      setGhost(null);
      if (!moved) return;
      const edge = edgeAt(x, y, window.innerWidth, window.innerHeight);
      const at = floatAt ?? floatBase(shell.float);
      setFloatAt(null);
      if (edge) setShell({ mode: edge, float: { ...shell.float, ...at } });
      else setShell({ float: { ...shell.float, ...at } });
    },
  );

  const bubble = bubbleAt ?? bubbleBase(shell.bubble);
  const float = floatAt ?? floatBase(shell.float);
  const open = shell.mode === "float";

  return (
    <>
      {ghost ? <div className={`chat-ghost chat-ghost-${ghost}`} aria-hidden="true" /> : null}
      <button
        type="button"
        className={`chat-bubble fixed z-50 grid h-14 w-14 touch-none select-none place-items-center rounded-full bg-gray-900 text-white shadow-xl hover:bg-gray-700 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-200 ${bubbleAt ? "" : "transition-transform duration-300"}`}
        style={{ left: bubble.x, top: bubble.y }}
        aria-label={shell.mode === "closed" ? "AI 투자 어시스턴트 열기" : "AI 투자 어시스턴트 닫기"}
        title={shell.mode === "popout" ? "새 탭에서 열려 있다 — 누르면 그 탭으로" : "AI 투자 어시스턴트 — 누르면 열고, 끌어서 옮기거나 가장자리에 붙인다"}
        {...bubbleDrag}
      >
        {shell.mode === "closed" || shell.mode === "popout" ? <SparklesIcon className="h-7 w-7" /> : <XMarkIcon className="h-7 w-7" />}
        {shell.mode === "popout" ? <span className="absolute right-1 top-1 h-3 w-3 rounded-full bg-emerald-400" /> : null}
      </button>

      {open ? (
        <div
          className={`chat-pop fixed z-50 flex flex-col overflow-hidden rounded-2xl border border-blue-gray-100 shadow-2xl dark:border-gray-800 ${floatAt ? "" : "transition-[left,top] duration-200"}`}
          style={{ left: float.x, top: float.y, width: shell.float.w, height: shell.float.h, maxWidth: "96vw", maxHeight: "92vh" }}
        >
          <ChatFrame who={who} mode="float" onHeaderDrag={headerDrag} className="flex-1" />
        </div>
      ) : null}
    </>
  );
}

/** 가장자리에 붙은 창 — `Layout` 이 본문 옆(또는 위/아래)에 둔다. 경계를 끌어 크기를 바꾼다. */
export function DockedChat({ who }: { who: Who | null }) {
  const shell = useChatShell();
  const [live, setLive] = useState<number | null>(null);
  const edge = isDock(shell.mode) ? shell.mode : null;
  const horizontal = edge === "left" || edge === "right";
  const drag = useDrag(
    (dx, dy) => {
      if (!edge) return;
      const viewport = horizontal ? window.innerWidth : window.innerHeight;
      setLive(dockSizeAfterDrag(edge, shell.dock[edge], horizontal ? dx : dy, viewport));
    },
    (moved) => {
      if (!edge || !moved) return;
      const size = live ?? shell.dock[edge];
      setLive(null);
      setShell({ dock: { ...shell.dock, [edge]: size } });
    },
  );
  if (!edge) return null;
  const size = live ?? shell.dock[edge];
  const splitter = (
    <div
      className={`chat-splitter shrink-0 touch-none bg-blue-gray-100 hover:bg-blue-gray-300 dark:bg-gray-800 dark:hover:bg-gray-600 ${horizontal ? "w-1.5 cursor-col-resize" : "h-1.5 cursor-row-resize"}`}
      role="separator"
      aria-orientation={horizontal ? "vertical" : "horizontal"}
      title="끌어서 크기를 바꾼다"
      {...drag}
    />
  );
  const frame = (
    <div className={`chat-dock shrink-0 ${live === null ? "transition-[width,height] duration-300" : ""}`} style={horizontal ? { width: size } : { height: size }}>
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
  if (at.x >= 0 && at.y >= 0 && typeof window !== "undefined") {
    return { x: clamp(at.x, 8, window.innerWidth - 64), y: clamp(at.y, 8, window.innerHeight - 64) };
  }
  return typeof window === "undefined" ? { x: 0, y: 0 } : { x: window.innerWidth - 80, y: window.innerHeight - 80 };
}

function floatBase(at: { x: number; y: number; w: number; h: number }): { x: number; y: number } {
  if (at.x >= 0 && at.y >= 0 && typeof window !== "undefined") {
    return { x: clamp(at.x, 0, Math.max(0, window.innerWidth - at.w)), y: clamp(at.y, 0, Math.max(0, window.innerHeight - at.h)) };
  }
  return typeof window === "undefined" ? { x: 0, y: 0 } : { x: Math.max(8, window.innerWidth - at.w - 24), y: Math.max(8, window.innerHeight - at.h - 100) };
}
