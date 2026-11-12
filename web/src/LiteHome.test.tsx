/**
 * 첫 화면 — 약식 리포트 칸이 그 자리에 있고, 전체 리포트로 옮겨 가는 단추는 없다.
 * 저장소에 DOM 시험 도구가 없어 서버 렌더 결과(문자열)로 잰다 — 숫자 채움은 여기서 못 잰다.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LiteHome, RecentRows } from "./LiteHome";
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

  it("잡힌 증거금 · 열린 명목 칸이 첫 화면에 있다", () => {
    expect(html).toContain("잡힌 증거금");
    expect(html).toContain("쓸 수 있는 돈");
    expect(html).toContain("열린 명목");
    expect(html).toContain("계좌 배율");
  });

  it("큰 숫자는 미실현 포함 평가이고 계좌 총액은 미실현 제외라고 적어 아래 줄에 낸다", () => {
    expect(html).toContain("미실현 포함 평가");
    // 큰 글씨 칸이 평가액이다 — 지갑 총액 줄보다 앞에 온다.
    expect(html).toMatch(/text-4xl[^>]*data-testid="lite-equity"/);
    expect(html).toContain('data-testid="lite-total"');
    expect(html).toContain("계좌 총액(미실현 제외)");
    expect(html.indexOf('data-testid="lite-equity"')).toBeLessThan(
      html.indexOf('data-testid="lite-total"'),
    );
  });

  it("최근 매매 칸은 접힌 채로 뜬다 — 열기 전에는 읽지 않는다", () => {
    expect(html).toContain("최근 매매 50건");
    expect(html).toMatch(/aria-expanded="false"[^>]*>펼치기</);
    expect(html).not.toContain('data-testid="recent-row"');
  });

  it("기본 기간은 7일이다", () => {
    expect(html).toMatch(/aria-selected="true"[^>]*>7일</);
  });

  it("전체 리포트로 옮겨 가는 단추는 없다", () => {
    expect(html).not.toContain('href="/report"');
  });
});

describe("최근 매매 목록", () => {
  const row = {
    symbol: "PAXG_USDT",
    at: "2026-11-10T13:40:00+00:00",
    pnl: "-1.20",
    fees: "-0.37",
    funding: "0.02",
    net: "-1.55",
    balance: "802.56",
    fills: 2,
  };
  const win = {
    ...row,
    symbol: "NAS100_USDT",
    at: "2026-11-10T05:59:55+00:00",
    pnl: "2.58",
    fees: "-0.03",
    funding: "0",
    net: "2.55",
    balance: "804.11",
    fills: 1,
  };

  it("줄마다 종목 · 순손익 색 · 수수료 · 그 뒤 계좌를 낸다", () => {
    const html = renderToStaticMarkup(
      <RecentRows
        data={{
          rows: [row, win],
          count: 2,
          wins: 1,
          net: "1.00",
          pnl: "1.38",
          fees: "-0.40",
          funding: "0.02",
          open_fees: "-0.11",
          open_funding: "0.01",
          oldest: "2026-10-11T00:00:00+00:00",
          unreadable: 0,
        }}
      />,
    );
    expect(html.match(/data-testid="recent-row"/g)).toHaveLength(2);
    expect(html).toContain("PAXG");
    expect(html).not.toContain("PAXG_USDT");
    expect(html).toContain("체결 2번");
    expect(html).toMatch(/text-loss[^>]*>−1\.55/);
    expect(html).toMatch(/text-gain[^>]*>\+2\.55/);
    expect(html).toContain("802.56");
    expect(html).toContain("2건 · 이긴 매매 1건");
    expect(html).toContain("그 체결 직후 지갑 잔고(미실현 제외)");
    expect(html).toContain("아직 어느 줄에도 안 들어갔다");
  });

  it("주문 번호로 이은 줄에만 롱/숏 · 다리 이름을 붙인다", () => {
    const html = renderToStaticMarkup(
      <RecentRows
        data={{
          rows: [{ ...row, leg: "급락 되돌림", side: "롱" }, win],
          count: 2,
          wins: 1,
          net: "1.00",
          pnl: "1.38",
          fees: "-0.40",
          funding: "0.02",
          open_fees: "0",
          open_funding: "0",
          oldest: "2026-10-11T00:00:00+00:00",
          unreadable: 0,
        }}
      />,
    );
    expect(html.match(/data-testid="recent-leg"/g)).toHaveLength(1);
    expect(html).toContain("롱 · 급락 되돌림");
  });

  it("합계는 손익 시작점 뒤만 세고 그 앞 매매는 흐리게 남긴다", () => {
    const old = {
      ...row,
      symbol: "ONE_USDT",
      at: "2026-11-02T12:15:00+00:00",
      pnl: "-300.00",
      net: "-300.32",
      balance: "78.47",
      fills: 1,
    };
    const html = renderToStaticMarkup(
      <RecentRows
        data={{
          rows: [row, win, old],
          count: 3,
          wins: 1,
          net: "-299.32",
          pnl: "-298.62",
          fees: "-0.72",
          funding: "0.02",
          since: "2026-11-02T15:00:00+00:00",
          after: {
            count: 2,
            wins: 1,
            net: "1.00",
            pnl: "1.38",
            fees: "-0.40",
            funding: "0.02",
          },
          open_fees: "0",
          open_funding: "0",
          oldest: "2026-10-11T00:00:00+00:00",
          unreadable: 0,
        }}
      />,
    );
    expect(html.match(/data-testid="recent-row"/g)).toHaveLength(2);
    expect(html.match(/data-testid="recent-row-before"/g)).toHaveLength(1);
    expect(html).toMatch(/opacity-50[^>]*data-testid="recent-row-before"/);
    expect(html).toMatch(/손익 시작점\([^)]*\) 뒤 2건 · 이긴 매매 1건/);
    // 요약의 합은 시작점 뒤 값이다 — 목록 전체 합(−299.32)이 아니다.
    expect(html).toMatch(/recent-summary[\s\S]*?text-gain[^>]*>\+1\.00/);
    expect(html).toContain("시작점 앞 1건은 아래에 흐리게 두고 합계에서 뺐다");
  });

  it("장부를 못 읽으면 빈 목록이 아니라 못 읽었다고 말한다", () => {
    const html = renderToStaticMarkup(<RecentRows data={{ rows: null }} />);
    expect(html).toContain("거래소 장부를 못 읽었다");
    expect(html).toContain('role="alert"');
  });
});
