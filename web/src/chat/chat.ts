/**
 * 채팅 패널의 순수 조각 (T248 · T257) — 저장된 메시지에서 화면에 보일 것만 고르기 · 제안 카드 판정 · 마지막 대화 기억.
 */

export type ChatEvidence = {
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  ms: number;
  digest: string;
  error?: string;
  /** 도구 결과 원문(잘라서) — 근거 세미 창 (T257). 옛 메시지엔 없다. */
  result?: string;
};

export type ChatMessageView = {
  role: "user" | "assistant" | "tool" | string;
  content: string;
  at?: string;
  model?: string;
  evidence?: ChatEvidence[];
  proposals?: Array<Record<string, unknown>>;
  /** 다음 질문 제안 (T257 F3). */
  suggestions?: string[];
  failure?: string | null;
  auto?: unknown;
  tool_calls?: Array<{ call_id: string; name: string; arguments: Record<string, unknown> }>;
};

/** 화면에는 user · assistant(최종 답)만 — 도구 메시지와 도구만 부른 assistant 줄은 근거 칩으로 접힌다. */
export function visibleMessages(messages: readonly ChatMessageView[]): ChatMessageView[] {
  return messages.filter((m) => m.role === "user" || (m.role === "assistant" && !(m.tool_calls && m.tool_calls.length) && m.content));
}

export const OPEN_SLOT = "chat-open";
export const THREAD_SLOT = "chat-thread";

/** 마지막에 보던 대화 id — 세션은 서버에 있고 브라우저는 어느 것이었는지만 기억한다. */
export function readThread(): string | null {
  try {
    return localStorage.getItem(THREAD_SLOT);
  } catch {
    return null;
  }
}

export function writeThread(id: string): void {
  try {
    localStorage.setItem(THREAD_SLOT, id);
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
