/**
 * 도는 RUN 정렬 — 실현 + · 미실현 + · 실현 − · 미실현 − · 진입 대기 (사용자 요구 2026-09-21 → 2026-09-27).
 *
 * 🔴 여기서 못 박는 것 하나: **순서가 들어온 순서에 딸려 가면 안 된다.** 막 만든 18종은
 * 미실현도 손익도 전부 0 이라, 그때 기준이 없으면 화면 순서가 서버의 정렬(만든 시각)을
 * 그대로 따라간다 — 38초 안에 만들어진 순서는 사람에게 아무 뜻이 없다.
 */

import { describe, expect, it } from "vitest";
import {
  GROUP,
  groupOf,
  holdsNow,
  livePnl,
  orderAlive,
  type Beat,
  type Sortable,
} from "./runsOrder";

const row = (over: Partial<Sortable> & { session_id: string }): Sortable => ({
  symbol: over.session_id,
  trades: 0,
  closed: 0,
  return_pct: 0,
  ...over,
});

const pos = (pnl: string): Beat => ({ exchange: { position: { unrealised_pnl: pnl } } });

describe("holdsNow — 지금 들고 있나", () => {
  it("거래소 포지션이 있으면 참이다", () => {
    expect(holdsNow(row({ session_id: "a" }), pos("3"))).toBe(true);
  });

  it("건강 폴링이 아직 없어도 원장의 열린 매매로 잡는다", () => {
    expect(holdsNow(row({ session_id: "a", trades: 1, closed: 0 }), undefined)).toBe(true);
  });

  it("다 닫혔고 포지션도 없으면 거짓이다", () => {
    expect(holdsNow(row({ session_id: "a", trades: 3, closed: 3 }), null)).toBe(false);
  });
});

describe("livePnl — 미실현", () => {
  it("없으면 0 이다 — 없는 것을 위로 올리지 않는다", () => {
    expect(livePnl(undefined)).toBe(0);
    expect(livePnl({ exchange: { position: null } })).toBe(0);
    expect(livePnl(pos(""))).toBe(0);
  });

  it("숫자가 아니면 0 이다", () => {
    expect(livePnl(pos("-"))).toBe(0);
  });

  it("음수도 그대로 읽는다", () => {
    expect(livePnl(pos("-2.5"))).toBe(-2.5);
  });
});

describe("groupOf — 묶음", () => {
  it("들고 있으면 미실현 부호 · 안 들고 있으면 실현 부호 · 둘 다 없으면 진입 대기", () => {
    const held = row({ session_id: "h", trades: 1, closed: 0, return_pct: 9 });
    expect(groupOf(held, pos("2"))).toBe(GROUP.LIVE_UP);
    expect(groupOf(held, pos("-2"))).toBe(GROUP.LIVE_DOWN);
    expect(groupOf(row({ session_id: "u", return_pct: 3 }), null)).toBe(GROUP.REALIZED_UP);
    expect(groupOf(row({ session_id: "d", return_pct: -3 }), null)).toBe(GROUP.REALIZED_DOWN);
    expect(groupOf(row({ session_id: "w" }), null)).toBe(GROUP.WAITING);
  });

  it("들고 있는데 미실현이 아직 안 왔으면 양수 쪽 — 모르는 것을 손실로 내리지 않는다", () => {
    expect(groupOf(row({ session_id: "h", trades: 1, closed: 0 }), undefined)).toBe(GROUP.LIVE_UP);
  });
});

describe("orderAlive", () => {
  it("🔴 미실현 + · 실현 + · 미실현 − · 실현 − · 진입 대기 순이다 (사용자 2026-09-27 · 펀드 표와 같다)", () => {
    const rows = [
      row({ session_id: "대기" }),
      row({ session_id: "미실현-", trades: 1, closed: 0 }),
      row({ session_id: "실현-", return_pct: -4 }),
      row({ session_id: "미실현+", trades: 1, closed: 0 }),
      row({ session_id: "실현+", return_pct: 2 }),
    ];
    const got = orderAlive(rows, { "미실현-": pos("-1"), "미실현+": pos("5") });
    expect(got.map((r) => r.session_id)).toEqual(["미실현+", "실현+", "미실현-", "실현-", "대기"]);
  });

  it("이득 보유 판이 실현 이익 판보다 위다", () => {
    const rows = [
      row({ session_id: "보유", trades: 1, closed: 0 }),
      row({ session_id: "빈판", return_pct: 0.1 }),
    ];
    const got = orderAlive(rows, { 보유: pos("50") });
    expect(got.map((r) => r.session_id)).toEqual(["보유", "빈판"]);
  });

  it("들고 있는 판끼리는 미실현 내림차순", () => {
    const rows = ["a", "b", "c"].map((id) => row({ session_id: id, trades: 1, closed: 0 }));
    const got = orderAlive(rows, { a: pos("1"), b: pos("9"), c: pos("-3") });
    expect(got.map((r) => r.session_id)).toEqual(["b", "a", "c"]);
  });

  it("안 들고 있는 판끼리는 실현 손익률 내림차순", () => {
    const rows = [
      row({ session_id: "a", return_pct: 1 }),
      row({ session_id: "b", return_pct: 7 }),
      row({ session_id: "c", return_pct: -2 }),
    ];
    expect(orderAlive(rows, {}).map((r) => r.session_id)).toEqual(["b", "a", "c"]);
  });

  it("🔴 전부 0 이면 **들어온 순서와 무관하게** 종목 이름 순이다", () => {
    const ids = ["XRP", "ADA", "BTC", "SOL"];
    const rows = ids.map((id) => row({ session_id: id, symbol: id }));
    const once = orderAlive(rows, {}).map((r) => r.symbol);
    const twice = orderAlive(rows.slice().reverse(), {}).map((r) => r.symbol);
    expect(once).toEqual(["ADA", "BTC", "SOL", "XRP"]);
    expect(twice).toEqual(once);
  });

  it("원본 배열을 안 건드린다", () => {
    const rows = [row({ session_id: "a" }), row({ session_id: "b", return_pct: 9 })];
    const before = rows.map((r) => r.session_id);
    orderAlive(rows, {});
    expect(rows.map((r) => r.session_id)).toEqual(before);
  });
});
