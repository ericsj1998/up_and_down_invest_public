/**
 * 채팅 패널의 순수 조각 (T248) — 저장된 메시지에서 화면에 보일 것만 고르기 · 제안 카드 판정 · 도킹 위치 기억.
 */

export type ChatMessageView = {
  role: "user" | "assistant" | "tool" | string;
  content: string;
  at?: string;
  model?: string;
  evidence?: Array<{ name: string; arguments: Record<string, unknown>; ok: boolean; ms: number; digest: string; error?: string }>;
  proposals?: Array<Record<string, unknown>>;
  failure?: string | null;
  auto?: unknown;
  tool_calls?: Array<{ call_id: string; name: string; arguments: Record<string, unknown> }>;
};

/** 화면에는 user · assistant(최종 답)만 — 도구 메시지와 도구만 부른 assistant 줄은 근거 칩으로 접힌다. */
export function visibleMessages(messages: readonly ChatMessageView[]): ChatMessageView[] {
  return messages.filter((m) => m.role === "user" || (m.role === "assistant" && !(m.tool_calls && m.tool_calls.length) && m.content));
}

export type Dock = "right" | "left" | "bottom" | "float";
export const DOCK_SLOT = "chat-dock";
export const OPEN_SLOT = "chat-open";

export function readDock(): Dock {
  try {
    const raw = localStorage.getItem(DOCK_SLOT);
    return raw === "left" || raw === "bottom" || raw === "float" ? raw : "right";
  } catch {
    return "right";
  }
}

export function writeDock(dock: Dock): void {
  try {
    localStorage.setItem(DOCK_SLOT, dock);
  } catch {
    // 기억만 못 한다.
  }
}

/** 제안 카드의 한 줄 — 서버 `propose_order` 결과를 사람이 읽을 문장으로. */
export function proposalLine(p: Record<string, unknown>): string {
  const symbol = String(p.symbol ?? "");
  const side = p.long === false ? "숏" : "매수";
  const entry = String(p.entry ?? "—");
  const stop = String(p.stop ?? "—");
  const target = String(p.target ?? "—");
  const rr = String(p.rr ?? "—");
  const moved = p.stop_moved === true ? " · 손절 당겨짐" : "";
  return `${symbol} ${side} 진입 ${entry} · 손절 ${stop} · 목표 ${target} · RR ${rr}${moved}`;
}
