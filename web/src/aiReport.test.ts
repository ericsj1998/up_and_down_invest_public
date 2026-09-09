/**
 * AI 퍼포먼스 리포트 순수 조각 (T249).
 */

import { describe, expect, it } from "vitest";
import { rowTone, splitParticipant } from "./AiReport";

describe("splitParticipant", () => {
  it("모델@버전#해시/스냅샷 을 세 칸으로", () => {
    const got = splitParticipant("nvidia/x-120b@chat-1.0#ab12cd34/b60-o12-1d.1h");
    expect(got.model).toBe("nvidia/x-120b");
    expect(got.prompt).toBe("chat-1.0#ab12cd34");
    expect(got.snapshot).toBe("b60-o12-1d.1h");
  });
  it("옛 키(해시 없음)도 깨지지 않는다", () => {
    expect(splitParticipant("probe@chat-1.0")).toEqual({ model: "probe", prompt: "chat-1.0", snapshot: "" });
  });
});

describe("rowTone", () => {
  it("표본 미달은 회색", () => {
    expect(rowTone(false)).toBe("faint");
    expect(rowTone(true)).toBe("");
  });
});
