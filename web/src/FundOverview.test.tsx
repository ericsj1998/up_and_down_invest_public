/**
 * 엔진 다리 칸 — 판이 없어도 열린다. 판도 엔진도 없는 칸은 잠겨 있다.
 * 저장소에 DOM 시험 도구가 없어 서버 렌더 결과(문자열)로 잰다 — 클릭 동작 자체는 여기서 못 잰다.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { FundEngineStatus, FundLeg, FundStatus } from "./api";
import { fundOfEngine } from "./EngineLegPage";
import { EngineLegPanel } from "./EngineLegPanel";
import { FundOverview } from "./FundOverview";

vi.mock("./Chart", () => ({ Chart: () => null }));

const ENGINE: FundEngineStatus = {
  symbol: "NAS100_USDT",
  last_day: "2026-11-05",
  zone: true,
  units: [
    {
      key: "u1",
      kind: "bounce",
      contracts: 3,
      fill: "26000.5",
      ghost: true,
      stop: null,
      opened: "2026-10-20T20:00:00+00:00",
    },
  ],
  buy: null,
  contracts: 0,
  realized: "0",
  fees: "0",
  findings: [],
  unrealized: "",
};

function overview(sym: string, leg: Partial<FundLeg>): string {
  const f = {
    market: "GATE",
    legs: [],
    per_symbol: { [sym]: leg },
  } as unknown as FundStatus;
  return renderToStaticMarkup(
    <FundOverview
      f={f}
      groups={[{ key: "a", label: "묶음", symbols: [sym] }] as never}
      ticks={{}}
    />,
  );
}

describe("FundOverview 엔진 다리", () => {
  it("판이 없어도 엔진 칸은 열려 있다", () => {
    expect(overview("NAS100_USDT", { handle: "", engine: ENGINE })).not.toMatch(
      /<button[^>]*disabled/,
    );
  });

  it("판도 엔진도 없는 칸은 잠겨 있다", () => {
    expect(overview("BTC_USDT", { handle: "" })).toMatch(
      /<button[^>]*disabled/,
    );
  });
});

describe("fundOfEngine", () => {
  const coin = { per_symbol: { BTC_USDT: { handle: "a" } } } as never;
  const nas = {
    per_symbol: { NAS100_USDT: { handle: "", engine: ENGINE } },
  } as never;

  it("그 종목을 엔진으로 돌리는 펀드를 찾는다", () => {
    expect(fundOfEngine([coin, nas], "NAS100_USDT")).toBe(nas);
  });

  it("판 종목 · 없는 종목은 엔진 다리가 아니다", () => {
    expect(fundOfEngine([coin, nas], "BTC_USDT")).toBeUndefined();
    expect(fundOfEngine([coin, nas], "ETH_USDT")).toBeUndefined();
  });
});

describe("EngineLegPanel", () => {
  const html = renderToStaticMarkup(
    <EngineLegPanel
      symbol="NAS100_USDT"
      market="GATE"
      name="나스닥"
      status={ENGINE}
      onClose={() => undefined}
    />,
  );

  it("가상 몫은 가상이라고 적고 손절 · 미실현 빈 값은 대시로 적는다", () => {
    expect(html).toContain("가상");
    expect(html).toContain("26,000.50");
    expect(html).toContain("미실현 <b>—</b>");
  });

  it("차트 칸은 받기 전 안내를 보인다", () => {
    expect(html).toContain("차트 불러오는 중");
  });
});
