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

  it("시간축 단추 셋 중 일봉이 먼저 골라져 있다", () => {
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1);
    expect(html).toMatch(/aria-pressed="true"[^>]*>일봉</);
    expect(html).toContain("1시간");
    expect(html).toContain("4시간");
  });

  it("청산 자리 칸 · 상태 한 줄은 서버가 준 값만 적고 모르면 대시다", () => {
    expect(html).toContain("청산 자리");
    expect(html).toContain('data-testid="engine-facts"');
    expect(html).toContain("다음 계단");
    expect(html).toContain("없음");
  });

  it("몫마다 파는 방식과 자리를 적는다", () => {
    const held = renderToStaticMarkup(
      <EngineLegPanel
        symbol="NAS100_USDT"
        market="GATE"
        name="나스닥"
        status={{
          ...ENGINE,
          units: [
            {
              key: "u2",
              kind: "k",
              contracts: 3,
              fill: "100",
              ghost: false,
              stop: "70",
              opened: "2026-11-01T21:00:00+00:00",
              exit: "104",
              exit_how: "limit",
            },
            {
              key: "u3",
              kind: "k",
              contracts: 3,
              fill: "100",
              ghost: false,
              stop: "70",
              opened: "2026-11-02T21:00:00+00:00",
              exit: null,
              exit_how: "close_below",
            },
          ],
          mark: "102",
          margin: "60",
          unrealized: "3",
          step_units: 1,
          max_units: 5,
        }}
        onClose={() => undefined}
      />,
    );
    expect(held).toContain("매도 지정가");
    expect(held).toContain("종가가 이 아래면 청산 — 값은 다음 마감 뒤 표시");
    expect(held).toContain("+2.00%");
    expect(held).toContain("+5.00%");
  });
});
