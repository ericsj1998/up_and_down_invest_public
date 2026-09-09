/**
 * 작은 마크다운 렌더러의 블록 자르기 (T257).
 */

import { describe, expect, it } from "vitest";
import { parseBlocks } from "./markdown";

describe("parseBlocks", () => {
  it("제목 · 목록 · 표 · 인용 · 코드 · 문단을 가른다", () => {
    const got = parseBlocks(["### 요약", "- 하나", "- 둘", "", "| a | b |", "|---|---|", "| 1 | 2 |", "> 주의", "```", "x = 1", "```", "끝 문단"].join("\n"));
    expect(got.map((b) => b.kind)).toEqual(["h", "ul", "table", "quote", "code", "p"]);
    const table = got[2];
    expect(table.kind === "table" && table.head).toEqual(["a", "b"]);
    expect(table.kind === "table" && table.rows).toEqual([["1", "2"]]);
  });
  it("모르는 문법은 문단 글자로 남는다 (사라지지 않는다)", () => {
    const got = parseBlocks("[[이상한]] 문법 {x}");
    expect(got).toEqual([{ kind: "p", text: "[[이상한]] 문법 {x}" }]);
  });
  it("번호 목록", () => {
    const got = parseBlocks("1. 첫째\n2) 둘째");
    expect(got).toEqual([{ kind: "ol", items: ["첫째", "둘째"] }]);
  });
});
