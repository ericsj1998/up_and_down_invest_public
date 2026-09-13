/**
 * 뜬 창·도킹 창 **틀** (T257 채팅에서 뽑아 T276 달력과 같이 쓴다) — 동그란 단추(드래그 · 가장자리에 놓으면 그 변에
 * 붙는다 · 창이 열려 있으면 숨는다) · 뜬 창(머리 드래그 · 네 변·네 모서리 손잡이로 크기) · 가장자리 도킹(밀어내기 ·
 * 경계 드래그 · 머리를 잡아 끌면 떨어져 나와 다시 붙일 수 있다 · 왼쪽은 둘: 맨 왼쪽 = 사이드바를 밀어냄 · 사이드바
 * 오른쪽 = 본문을 밀어냄) · 붙이기 단추 하나(누르면 방향 메뉴) · 확대(큰 화면 경로) · 크롬 새 탭 · 닫기.
 *
 * 무엇을 담느냐(`body`)와 어디에 기억하느냐(`store`)만 다르다. 드래그는 요소가 아니라 **window** 의 포인터 이벤트로
 * 잡는다 — 끄는 도중 모드가 바뀌어(도킹 → 뜬 창) 창이 다시 그려져도 드래그가 끊기지 않는다. 배치 상태는 `shell.ts`
 * 의 저장소가 단일 출처이고 `Layout` 이 같은 상태로 본문을 밀어낸다.
 *
 * ⚠️ 색은 Material Tailwind 팔레트(`withMT`)만 있다 — 기본 팔레트 이름(`sky` 등)은 빌드에서 잘려 나가 배경이
 *    투명해진다(2026-09-10 실측). 무채색(`gray` · `blue-gray`)만 쓴다 (사용자: "파랑 별로 · 검은색으로").
 */

import { ArrowsPointingOutIcon, ArrowTopRightOnSquareIcon, Squares2X2Icon, XMarkIcon } from "@heroicons/react/24/solid";
import { useEffect, useState, type ComponentType, type ReactNode, type SVGProps } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  clamp,
  DRAG_PX,
  dockSizeAfterDrag,
  edgeAt,
  floatAfterResize,
  isDock,
  openPopoutOf,
  RESIZE_EDGES,
  useShellStore,
  XL,
  type DockMode,
  type Mode,
  type ResizeEdge,
  type ShellStore,
} from "../chat/shell";
import { sidebarWidth } from "./sidenavState";

export type PanelIcon = ComponentType<SVGProps<SVGSVGElement>>;

/** 창 하나의 정체 — 제목 · 아이콘 · 큰 화면 경로 · 단추 자리(아래에서 몇 번째). */
export type PanelSpec = {
  store: ShellStore;
  label: string;
  icon: PanelIcon;
  /** "확대" 가 가는 경로 — 이 경로에 있으면 작은 창은 닫는다(같은 것이 두 곳에 뜨지 않게). */
  enlargeTo: string;
  /** 동그란 단추의 기본 자리 — 화면 오른쪽 아래에서 몇 번째 칸인가(0 = 맨 아래). */
  bubbleIndex: number;
};

/** 손잡이 자리·커서 — 변은 6px 띠, 모서리는 14px 네모. */
const HANDLE_STYLE: Record<ResizeEdge, React.CSSProperties> = {
  n: { top: -3, left: 14, right: 14, height: 6, cursor: "ns-resize" },
  s: { bottom: -3, left: 14, right: 14, height: 6, cursor: "ns-resize" },
  e: { right: -3, top: 14, bottom: 14, width: 6, cursor: "ew-resize" },
  w: { left: -3, top: 14, bottom: 14, width: 6, cursor: "ew-resize" },
  ne: { top: -4, right: -4, width: 14, height: 14, cursor: "nesw-resize" },
  sw: { bottom: -4, left: -4, width: 14, height: 14, cursor: "nesw-resize" },
  nw: { top: -4, left: -4, width: 14, height: 14, cursor: "nwse-resize" },
  se: { bottom: -4, right: -4, width: 14, height: 14, cursor: "nwse-resize" },
};

const EDGES: Array<{ edge: DockMode; glyph: string; title: string }> = [
  { edge: "left", glyph: "⇤", title: "맨 왼쪽 (사이드바를 오른쪽으로 밀어낸다)" },
  { edge: "left-inner", glyph: "◀", title: "사이드바 오른쪽 (본문을 밀어낸다)" },
  { edge: "top", glyph: "▲", title: "위" },
  { edge: "bottom", glyph: "▼", title: "아래" },
  { edge: "right", glyph: "▶", title: "오른쪽" },
];

const BUBBLE = 56;
const BUBBLE_GAP = 72;

type DragKind = "bubble" | "float" | "docked";

/** 지금 사이드바 오른쪽 경계(px) — xl 미만이면 사이드바가 없다. 접힘/펼침을 따라간다. */
function sidebarRight(): number | null {
  return window.innerWidth >= XL ? sidebarWidth() - 16 : null;
}

/** 뜬 창의 손잡이를 끌어 크기를 바꾼다 — 사용자 요청 2026-09-11 "상하좌우를 클릭해서 늘릴 수도 있게". */
function beginResize(store: ShellStore, e: React.PointerEvent, edge: ResizeEdge) {
  if (e.button !== 0) return;
  e.preventDefault();
  e.stopPropagation();
  const startX = e.clientX;
  const startY = e.clientY;
  const was = store.get().float;
  const origin = { ...floatBase(was), w: was.w, h: was.h };
  const move = (ev: PointerEvent) => {
    const next = floatAfterResize(edge, origin, ev.clientX - startX, ev.clientY - startY, window.innerWidth, window.innerHeight);
    store.setTransient({ float: next });
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    store.persist();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

/**
 * window 포인터 이벤트로 끄는 한 번의 드래그. 짧게 누르고 뗀 것(6px 미만)은 `onTap`.
 * 단추든 창이든 **가장자리에 놓으면 그 변에 붙고**, 아니면 놓은 자리에 머문다.
 */
function beginDrag(store: ShellStore, bubbleIndex: number, e: React.PointerEvent, kind: DragKind, onTap?: () => void) {
  if (e.button !== 0) return;
  e.preventDefault();
  const startX = e.clientX;
  const startY = e.clientY;
  const start = store.get();
  const origin = kind === "bubble" ? bubbleBase(start.bubble, bubbleIndex) : floatBase(start.float);
  let moved = false;
  let grabX = 0;
  let grabY = 0;
  const where = (ev: PointerEvent) => edgeAt(ev.clientX, ev.clientY, window.innerWidth, window.innerHeight, undefined, sidebarRight());
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
        store.setTransient({ mode: "float", float: { ...start.float, x: ev.clientX - grabX, y: ev.clientY - grabY } });
      }
    }
    const ghost = where(ev);
    if (kind === "bubble") {
      store.setTransient({
        bubble: { x: clamp(origin.x + dx, 0, window.innerWidth - BUBBLE), y: clamp(origin.y + dy, 0, window.innerHeight - BUBBLE) },
        ghost,
      });
      return;
    }
    const x = kind === "docked" ? ev.clientX - grabX : origin.x + dx;
    const y = kind === "docked" ? ev.clientY - grabY : origin.y + dy;
    store.setTransient((was) => ({
      float: { ...was.float, x: clamp(x, -was.float.w + 80, window.innerWidth - 80), y: clamp(y, 0, window.innerHeight - 40) },
      ghost,
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
    const edge = where(ev);
    store.setTransient({ ghost: null, ...(edge ? { mode: edge } : {}) });
    store.persist();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

/** 창 머리 — 아이콘 · 제목 · 부제 · 붙이기(하나 · 메뉴) · 확대 · 새 탭 · 닫기. 잡아 끌면 옮긴다(도킹이면 떼어 낸다). */
export function PanelHeader({ spec, title, subtitle, mode }: { spec: PanelSpec; title: string; subtitle?: string; mode: Mode }) {
  const [menu, setMenu] = useState(false);
  const navigate = useNavigate();
  const { store } = spec;
  const Icon = spec.icon;
  const draggable = mode === "float" || isDock(mode);
  const enlarge = () => {
    store.set({ mode: "closed" });
    navigate(spec.enlargeTo);
  };
  return (
    <div
      className={`relative flex select-none items-center gap-2 border-b border-blue-gray-100 bg-blue-gray-50/60 px-3 py-2 dark:border-gray-800 dark:bg-gray-800/60 ${draggable ? "cursor-move touch-none" : ""}`}
      onPointerDown={draggable ? (e) => beginDrag(store, spec.bubbleIndex, e, mode === "float" ? "float" : "docked") : undefined}
      title={draggable ? "잡아 끌어 옮긴다 — 가장자리에 놓으면 붙는다" : undefined}
    >
      <Icon className="h-5 w-5 shrink-0 text-gray-900 dark:text-white" />
      <div className="min-w-0">
        <b className="block truncate text-sm">{title}</b>
        {subtitle ? <span className="faint block text-xs">{subtitle}</span> : null}
      </div>
      <span className="ml-auto flex shrink-0 gap-0.5" onPointerDown={(e) => e.stopPropagation()}>
        {mode !== "popout" ? (
          <button type="button" className={`btn small ${isDock(mode) ? "primary" : ""}`} title="가장자리에 붙인다" onClick={() => setMenu((was) => !was)}>
            <Squares2X2Icon className="h-3.5 w-3.5" />
          </button>
        ) : null}
        {mode !== "popout" ? (
          <button type="button" className="btn small" title="확대 — 큰 화면에서 본다" onClick={enlarge}>
            <ArrowsPointingOutIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
        {mode !== "popout" ? (
          <button type="button" className="btn small" title="크롬 새 탭으로" onClick={() => openPopoutOf(store)}>
            <ArrowTopRightOnSquareIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
        {mode !== "popout" ? (
          <button type="button" className="btn small" title="닫는다" onClick={() => store.set({ mode: "closed" })}>
            <XMarkIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </span>
      {menu ? (
        <div className="absolute right-2 top-full z-10 mt-1 flex gap-0.5 rounded-lg border border-blue-gray-100 bg-white p-1 shadow-lg dark:border-gray-700 dark:bg-gray-900" onPointerDown={(e) => e.stopPropagation()}>
          {EDGES.map(({ edge, glyph, title: t }) => (
            <button
              key={edge}
              type="button"
              className={`btn small ${mode === edge ? "primary" : ""}`}
              title={t}
              onClick={() => {
                setMenu(false);
                store.set({ mode: edge });
              }}
            >
              {glyph}
            </button>
          ))}
          <button
            type="button"
            className={`btn small ${mode === "float" ? "primary" : ""}`}
            title="떼어서 띄운다"
            onClick={() => {
              setMenu(false);
              store.set({ mode: "float" });
            }}
          >
            ◱
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** 머리 + 몸통. 몸통은 `mode` 를 받아 그린다(뜬 창·도킹·새 탭에서 다르게 보일 수 있게). */
export function PanelFrame({
  spec,
  mode,
  title,
  subtitle,
  className,
  children,
}: {
  spec: PanelSpec;
  mode: Mode;
  title: string;
  subtitle?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`flex min-h-0 flex-col overflow-hidden bg-white dark:bg-gray-900 ${className ?? ""}`} aria-label={spec.label}>
      <PanelHeader spec={spec} title={title} subtitle={subtitle} mode={mode} />
      {children}
    </section>
  );
}

/** 창 하나를 열고 닫는다 — 닫혀 있으면 마지막 자리로 열고, 열려 있으면 닫는다. */
function togglePanel(store: ShellStore): void {
  store.set((was) => ({ mode: was.mode === "closed" || was.mode === "popout" ? was.last : "closed" }));
}

/**
 * **단추는 하나다** (사용자 2026-09-14: "메인 화면에 단추가 2개 이상 있는 걸 안하고 싶거든"). 동그란 단추 하나(AI 어시스턴트)
 * 만 떠 있고, 마우스를 올리면 그 위로 작은 단추들(달력 · 앞으로 더 붙을 것들)이 펼쳐지고 떼면 다시 접힌다 — 시야를 안
 * 가린다. 단추를 누르면 그 창이 열리고 단추는 **회색으로 남는다** · 다시 누르면 닫힌다. 끌어서 옮기고 가장자리에 놓으면
 * 첫 창(어시스턴트)이 그 변에 붙는다(자리는 첫 창의 저장소가 기억한다).
 *
 * @param primary 큰 단추의 창.
 * @param others 위로 펼쳐지는 작은 단추들 — 아래에서 위 순서.
 */
export function Launcher({ primary, others }: { primary: PanelSpec; others: PanelSpec[] }) {
  const shell = useShellStore(primary.store);
  const { pathname } = useLocation();
  const [hover, setHover] = useState(false);
  const bubble = bubbleBase(shell.bubble, 0);
  const PrimaryIcon = primary.icon;
  const primaryOpen = shell.mode !== "closed" && shell.mode !== "popout";
  const primaryBig = pathname === primary.enlargeTo;
  const active = "bg-blue-gray-300 text-blue-gray-900 hover:bg-blue-gray-400 dark:bg-gray-600 dark:text-white dark:hover:bg-gray-500";
  const idle = "bg-gray-900 text-white hover:bg-gray-700 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-200";
  return (
    <div
      className="fixed z-[60]"
      style={{ left: bubble.x, top: bubble.y, width: BUBBLE, height: BUBBLE }}
      onPointerEnter={() => setHover(true)}
      onPointerLeave={() => setHover(false)}
    >
      {/* 작은 단추들 — 큰 단추 바로 위로 쌓인다. 펼쳐질 때만 보이고 누를 수 있다. */}
      <div
        className="absolute left-1/2 flex -translate-x-1/2 flex-col-reverse items-center gap-2 transition-all duration-150"
        style={{ bottom: BUBBLE + 8, opacity: hover ? 1 : 0, transform: `translate(-50%, ${hover ? 0 : 12}px)`, pointerEvents: hover ? "auto" : "none" }}
        aria-hidden={!hover}
      >
        {others.map((spec) => (
          <MiniLauncher key={spec.store.slot} spec={spec} pathname={pathname} active={active} idle={idle} />
        ))}
      </div>
      <button
        type="button"
        className={`chat-bubble grid h-14 w-14 touch-none select-none place-items-center rounded-full shadow-xl ${primaryOpen ? active : idle} ${primaryBig ? "opacity-50" : ""}`}
        aria-label={`${primary.label} ${primaryOpen ? "닫기" : "열기"}`}
        aria-pressed={primaryOpen}
        title={primaryBig ? `${primary.label} — 지금 큰 화면에 있다` : `${primary.label} — 누르면 ${primaryOpen ? "닫고" : "열고"}, 끌어서 옮기고, 가장자리에 놓으면 붙는다. 올리면 다른 창 단추가 펼쳐진다`}
        onPointerDown={(e) => beginDrag(primary.store, 0, e, "bubble", primaryBig ? undefined : () => togglePanel(primary.store))}
      >
        <PrimaryIcon className="h-7 w-7" />
      </button>
    </div>
  );
}

function MiniLauncher({ spec, pathname, active, idle }: { spec: PanelSpec; pathname: string; active: string; idle: string }) {
  const shell = useShellStore(spec.store);
  const Icon = spec.icon;
  const open = shell.mode !== "closed" && shell.mode !== "popout";
  const big = pathname === spec.enlargeTo;
  return (
    <button
      type="button"
      className={`grid h-10 w-10 select-none place-items-center rounded-full shadow-lg ${open ? active : idle} ${big ? "opacity-50" : ""}`}
      aria-label={`${spec.label} ${open ? "닫기" : "열기"}`}
      aria-pressed={open}
      title={big ? `${spec.label} — 지금 큰 화면에 있다` : `${spec.label} — 누르면 ${open ? "닫는다" : "연다"}`}
      onClick={() => {
        if (!big) togglePanel(spec.store);
      }}
    >
      <Icon className="h-5 w-5" />
    </button>
  );
}

/**
 * 뜬 창 + 붙을 자리 미리보기. 여는 단추는 `Launcher` 가 하나로 모아 그린다.
 *
 * @param spec 창의 정체.
 * @param render 몸통 — 모드를 받아 그린다.
 */
export function FloatingPanel({ spec, title, subtitle, render }: { spec: PanelSpec; title: string; subtitle?: string; render: (mode: Mode) => ReactNode }) {
  const { store } = spec;
  const shell = useShellStore(store);
  const { pathname } = useLocation();
  const float = floatBase(shell.float);
  const open = shell.mode === "float";
  const big = pathname === spec.enlargeTo;
  // 큰 화면에 들어오면 작은 창은 닫는다 — 같은 것이 두 곳에 뜨지 않게. (렌더 중 저장소를 바꾸지 않는다.)
  useOnBig(big, store);

  return (
    <>
      {shell.ghost ? <div className={`chat-ghost chat-ghost-${shell.ghost}`} aria-hidden="true" /> : null}

      {open && !big ? (
        <div
          className="chat-pop fixed z-50 flex flex-col overflow-hidden rounded-2xl border border-blue-gray-100 shadow-2xl dark:border-gray-800"
          style={{ left: float.x, top: float.y, width: shell.float.w, height: shell.float.h, maxWidth: "96vw", maxHeight: "92vh" }}
        >
          <PanelFrame spec={spec} mode="float" title={title} subtitle={subtitle} className="flex-1">
            {render("float")}
          </PanelFrame>
          {RESIZE_EDGES.map((edge) => (
            <div
              key={edge}
              className="absolute z-10 touch-none select-none"
              style={HANDLE_STYLE[edge]}
              onPointerDown={(e) => beginResize(store, e, edge)}
              aria-hidden="true"
              title="끌어서 크기 조절"
            />
          ))}
        </div>
      ) : null}
    </>
  );
}


function useOnBig(big: boolean, store: ShellStore): void {
  useEffect(() => {
    if (big && store.get().mode !== "closed") store.set({ mode: "closed" });
  }, [big, store]);
}

/**
 * 가장자리에 붙은 창. 오른쪽·위·아래·사이드바 오른쪽은 `Layout` 이 본문 옆(또는 위/아래)에 두고, **맨 왼쪽은 화면 왼쪽
 * 끝에 고정**해 사이드바와 본문을 그만큼 밀어낸다(`Layout` 이 여백을 준다). 경계를 끌어 크기를 바꾼다.
 *
 * @param leftOffset 맨 왼쪽에 붙은 다른 창들의 너비 — 그만큼 오른쪽에 선다.
 */
export function DockedPanel({
  spec,
  title,
  subtitle,
  render,
  leftOffset = 0,
}: {
  spec: PanelSpec;
  title: string;
  subtitle?: string;
  render: (mode: Mode) => ReactNode;
  leftOffset?: number;
}) {
  const { store } = spec;
  const shell = useShellStore(store);
  const edge = isDock(shell.mode) ? shell.mode : null;
  const horizontal = edge === "left" || edge === "left-inner" || edge === "right";
  const onSplit = (e: React.PointerEvent) => {
    if (!edge || e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const base = shell.dock[edge];
    const move = (ev: PointerEvent) => {
      const viewport = horizontal ? window.innerWidth : window.innerHeight;
      const size = dockSizeAfterDrag(edge, base, horizontal ? ev.clientX - startX : ev.clientY - startY, viewport);
      store.setTransient((was) => ({ dock: { ...was.dock, [edge]: size } }));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      store.persist();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  if (!edge) return null;
  const size = shell.dock[edge];
  const splitter = (
    <div
      className={`chat-splitter shrink-0 touch-none rounded bg-blue-gray-100 hover:bg-blue-gray-300 dark:bg-gray-800 dark:hover:bg-gray-600 ${horizontal ? "w-1.5 cursor-col-resize" : "h-1.5 cursor-row-resize"}`}
      role="separator"
      aria-orientation={horizontal ? "vertical" : "horizontal"}
      title="끌어서 크기를 바꾼다"
      onPointerDown={onSplit}
    />
  );
  const body = (className: string) => (
    <PanelFrame spec={spec} mode={edge} title={title} subtitle={subtitle} className={className}>
      {render(edge)}
    </PanelFrame>
  );
  if (edge === "left") {
    return (
      <div className="chat-dock fixed top-0 z-[55] flex h-screen items-stretch gap-1 p-2 pr-0" style={{ left: leftOffset }}>
        <div className="shrink-0" style={{ width: size }}>
          {body("h-full rounded-xl border border-blue-gray-100 shadow-sm dark:border-gray-800")}
        </div>
        {splitter}
      </div>
    );
  }
  // ⭐ 페이지가 길어도 창은 화면을 따라온다 — 붙은 창은 뷰포트에 sticky, 높이는 화면 높이(좌우) 또는 지정 크기(상하).
  //    (사용자 2026-09-10: "입력창이 저 아래 가 있다")
  const stick = horizontal ? "sticky top-4 self-start" : edge === "top" ? "sticky top-4 self-stretch" : "sticky bottom-4 self-stretch";
  const frame = (
    <div className={`chat-dock shrink-0 ${stick}`} style={horizontal ? { width: size, height: "calc(100vh - 2rem)" } : { height: size }}>
      {body("h-full rounded-xl border border-blue-gray-100 shadow-sm dark:border-gray-800")}
    </div>
  );
  return edge === "top" || edge === "left-inner" ? (
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

/** 크롬 새 탭 — 레이아웃 없이 창만. */
export function PopoutFrame({ spec, title, subtitle, children }: { spec: PanelSpec; title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="flex h-screen flex-col bg-blue-gray-50/50 dark:bg-gray-950">
      <PanelFrame spec={spec} mode="popout" title={title} subtitle={subtitle} className="flex-1">
        {children}
      </PanelFrame>
    </div>
  );
}

function bubbleBase(at: { x: number; y: number }, index: number): { x: number; y: number } {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  if (at.x >= 0 && at.y >= 0) return { x: clamp(at.x, 0, window.innerWidth - BUBBLE), y: clamp(at.y, 0, window.innerHeight - BUBBLE) };
  return { x: window.innerWidth - 80, y: window.innerHeight - 80 - BUBBLE_GAP * index };
}

function floatBase(at: { x: number; y: number; w: number; h: number }): { x: number; y: number } {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  if (at.x >= 0 && at.y >= 0) return { x: clamp(at.x, -at.w + 80, window.innerWidth - 80), y: clamp(at.y, 0, window.innerHeight - 40) };
  return { x: Math.max(8, window.innerWidth - at.w - 24), y: Math.max(8, window.innerHeight - at.h - 100) };
}
