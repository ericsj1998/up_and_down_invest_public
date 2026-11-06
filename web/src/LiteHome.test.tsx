/**
 * 첫 화면 — 약식 리포트 칸이 그 자리에 있고, 전체 리포트로 옮겨 가는 단추는 없다.
 * 저장소에 DOM 시험 도구가 없어 서버 렌더 결과(문자열)로 잰다 — 숫자 채움은 여기서 못 잰다.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LiteHome } from "./LiteHome";
import { OpenRunsProvider } from "./shell/openRuns";

describe("LiteHome 약식 리포트", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <OpenRunsProvider>
        <LiteHome />
      </OpenRunsProvider>
    </MemoryRouter>,
  );

  it("기간 단추 셋과 칸 이름이 첫 화면에 있다", () => {
    expect(html).toContain("약식 리포트");
    for (const label of ["24시간", "7일", "30일"])
      expect(html).toContain(label);
    expect(html).toContain("순 실현 손익");
    expect(html).toContain("매매 · 승률");
  });

  it("기본 기간은 7일이다", () => {
    expect(html).toMatch(/aria-selected="true"[^>]*>7일</);
  });

  it("전체 리포트로 옮겨 가는 단추는 없다", () => {
    expect(html).not.toContain('href="/report"');
  });
});
