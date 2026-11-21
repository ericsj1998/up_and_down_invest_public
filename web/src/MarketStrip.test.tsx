/**
 * 첫 화면 시장 칸의 시세 칸 — 누르면 그 종목 상세로 간다(사용자 2026-11-22: *"종목들 클릭하면 상세로"*).
 * 판이 있으면 판 화면 · 판 없이 엔진 다리면 엔진 다리 화면 · 둘 다 아니면 누를 곳이 없다.
 * 저장소에 DOM 시험 도구가 없어 서버 렌더 결과(문자열)로 잰다.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PriceTile } from "./MarketStrip";
import { OpenRunsProvider } from "./shell/openRuns";

function html(symbol: string, run: string, leg: string): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <OpenRunsProvider>
        <PriceTile symbol={symbol} run={run} leg={leg}>
          값
        </PriceTile>
      </OpenRunsProvider>
    </MemoryRouter>,
  );
}

describe("PriceTile", () => {
  it("판이 있으면 판 상세로", () => {
    const out = html("BTC_USDT", "live-gate-btc", "");
    expect(out).toContain('href="/paper/live-gate-btc"');
    expect(out).toContain('aria-label="BTC_USDT 상세"');
  });

  it("판 없는 엔진 다리면 엔진 다리 상세로", () => {
    expect(html("NAS100_USDT", "", "1")).toContain('href="/leg/NAS100_USDT"');
  });

  it("갈 곳이 없으면 링크가 아니다", () => {
    const out = html("ETH_USDT", "", "");
    expect(out).not.toContain("href=");
    expect(out).toContain("값");
  });
});
