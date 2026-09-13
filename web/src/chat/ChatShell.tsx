/**
 * 채팅 창 (T257) — 틀은 `shell/DockPanel`(달력과 같이 쓴다 · T276), 몸통은 `ChatPanel`, 자리는 `chatShell` 저장소.
 *
 * 동그란 단추 · 뜬 창(머리 드래그 · 손잡이 크기 조절) · 가장자리 도킹(경계 드래그 · 떼어 내기) · 확대(`/assistant`) ·
 * 크롬 새 탭(`/chat`) 은 전부 틀이 한다. 여기는 채팅의 정체(제목 · 슬로건 · 아이콘)만 붙인다.
 */

import { SparklesIcon } from "@heroicons/react/24/solid";
import { useCallback, useState } from "react";
import type { Who } from "../api";
import { DockedPanel, FloatingPanel, PopoutFrame, type PanelSpec } from "../shell/DockPanel";
import { ChatPanel, SLOGAN } from "./ChatPanel";
import { chatShell, type Mode } from "./shell";

export const CHAT_PANEL: PanelSpec = {
  store: chatShell,
  label: "AI 투자 어시스턴트",
  icon: SparklesIcon,
  enlargeTo: "/assistant",
  bubbleIndex: 0,
};

/** 제목은 대화가 정한다(`ChatPanel.onTitle`) — 틀에 넘기려고 여기서 들고 있다. */
function useChatTitle(): [string, (t: string) => void] {
  const [title, setTitle] = useState("AI 투자 어시스턴트");
  const onTitle = useCallback((t: string) => setTitle(t), []);
  return [title, onTitle];
}

/** 동그란 단추 + 뜬 창. */
export function ChatShell({ who }: { who: Who | null }) {
  const [title, onTitle] = useChatTitle();
  return <FloatingPanel spec={CHAT_PANEL} title={title} subtitle={SLOGAN} render={() => <ChatPanel who={who} onTitle={onTitle} />} />;
}

/** 가장자리에 붙은 창 — `Layout` 이 자리를 정한다. */
export function DockedChat({ who, leftOffset = 0 }: { who: Who | null; leftOffset?: number }) {
  const [title, onTitle] = useChatTitle();
  return (
    <DockedPanel spec={CHAT_PANEL} title={title} subtitle={SLOGAN} leftOffset={leftOffset} render={() => <ChatPanel who={who} onTitle={onTitle} />} />
  );
}

/** 크롬 새 탭 — 레이아웃 없이 창만. 본 탭과 독립이다(같은 대화를 서버가 들고 있다). */
export function ChatPopout({ who }: { who: Who | null }) {
  const [title, onTitle] = useChatTitle();
  return (
    <PopoutFrame spec={CHAT_PANEL} title={title} subtitle={SLOGAN}>
      <ChatPanel who={who} onTitle={onTitle} />
    </PopoutFrame>
  );
}

export type { Mode };
