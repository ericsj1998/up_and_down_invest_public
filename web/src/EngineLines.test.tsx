/**
 * 판 목록의 엔진 다리 칸 (T830) — 판 없이 도는 다리도 RUN 곁에 한 줄씩 보인다.
 * 저장소에 DOM 시험 도구가 없어 서버 렌더 결과(문자열)로 잰다.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { EngineLines } from "./Runs";

function html(engines: Parameters<typeof EngineLines>[0]["engines"]): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <EngineLines engines={engines} />
    </MemoryRouter>,
  );
}

describe("EngineLines", () => {
  it("엔진이 없으면 칸을 안 그린다", () => {
    expect(html([])).toBe("");
  });

  it("엔진 한 줄 — 몫 수 · 실현 · 상세로 가는 단추", () => {
    const out = html([
      { market: "GATE", symbol: "NAS100_USDT", units: 3, ghosts: 2, realized: "12.345" },
    ]);
    expect(out).toContain("엔진 다리 1개");
    expect(out).toContain("NAS100_USDT");
    expect(out).toContain("+12.35 USDT");
    expect(out).toContain('href="/leg/NAS100_USDT"');
    expect(out).toContain("gain");
  });

  it("손해는 붉게 · 못 읽은 엔진은 이유를 적는다", () => {
    const out = html([
      { market: "GATE", symbol: "NAS100_USDT", units: 1, ghosts: 0, realized: "-4.2" },
      { market: "GATE", symbol: "SPX_USDT", error: "boom" },
    ]);
    expect(out).toContain("-4.20 USDT");
    expect(out).toContain("loss");
    expect(out).toContain("못 읽었다 — boom");
  });
});
