/**
 * 채팅 패널 순수 조각 (T248).
 */

import { describe, expect, it } from "vitest";
import { proposalLine, visibleMessages } from "./chat";

describe("visibleMessages", () => {
  it("도구 메시지와 도구만 부른 assistant 줄은 숨긴다", () => {
    const rows = visibleMessages([
      { role: "user", content: "테슬라 어때" },
      { role: "assistant", content: "", tool_calls: [{ call_id: "1", name: "symbol_resolve", arguments: {} }] },
      { role: "tool", content: "{}" },
      { role: "assistant", content: "TSLA 는 …" },
    ]);
    expect(rows.map((r) => r.role)).toEqual(["user", "assistant"]);
  });
});

describe("proposalLine", () => {
  it("제안을 한 줄로 · 손절 당겨짐 표시", () => {
    const line = proposalLine({ symbol: "NVDA", long: true, entry: "224.3", stop: "217.5", target: "233.9", rr: "1.26", stop_moved: true });
    expect(line).toContain("NVDA 매수");
    expect(line).toContain("손절 당겨짐");
  });
});
