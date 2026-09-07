/**
 * 주인 없는 잔재 화면 — **판정이 틀리면 살아 있는 포지션의 손절을 지운다**.
 *
 * 🔴 그림은 눈으로 보면 되지만 이 두 함수는 규칙이다. `canSweep` 이 한 번 잘못 참을
 * 내면 사람이 단추를 누르고, 그 순간 보호막이 사라진다 (§1.2.1 · 절대 규칙 #3).
 */

import { describe, expect, it } from "vitest";
import { canSweep, unrealised } from "./Leftovers";
import type { Leftover } from "./api";

function row(over: Partial<Leftover> = {}): Leftover {
  return {
    symbol: "XRP_USDT",
    kind: "주문",
    position: null,
    orders: [
      {
        id: "2090627461732630528",
        kind: "조건부",
        at: "1.2741",
        size: "0",
        reduce_only: "",
      },
    ],
    ...over,
  };
}

describe("canSweep — 거두기를 그려도 되나", () => {
  it("포지션 없이 주문만 남았으면 거둔다", () => {
    expect(canSweep(row())).toBe(true);
  });

  it("포지션이 있으면 절대 아니다", () => {
    // ⛔ 그 주문들은 잔재가 아니라 **유일한 보호막**이다.
    expect(canSweep(row({ kind: "포지션", position: { size: "-20" } }))).toBe(false);
  });

  it("거둘 것이 없으면 단추를 안 그린다", () => {
    // ⚠️ 눌러도 아무 일도 없는 단추는 "했다" 는 착각만 만든다.
    expect(canSweep(row({ orders: [] }))).toBe(false);
  });
});

describe("unrealised — 지금 닫으면 얼마인가", () => {
  it("실측한 그 ETH 포지션", () => {
    const shown = unrealised({ unrealised_pnl: "6.388", margin: "24.123554625" });
    expect(shown).toContain("+6.388");
    expect(shown).toContain("USDT");
  });

  it("손실이면 부호를 붙이지 않는다 — 숫자가 이미 음수다", () => {
    expect(unrealised({ unrealised_pnl: "-9.1037", margin: "24" })).toContain("-9.1037");
  });

  it("증거금 대비 비율을 같이 낸다", () => {
    // 🔴 절대액만 보면 6 USDT 가 큰지 작은지 모른다. 20배에서 증거금의 26% 다.
    const shown = unrealised({ unrealised_pnl: "6.388", margin: "24.123554625" });
    expect(shown).toContain("증거금의");
  });

  it("증거금을 못 읽으면 비율을 지어내지 않는다", () => {
    const shown = unrealised({ unrealised_pnl: "6.388", margin: "0" });
    expect(shown).toContain("6.388");
    expect(shown).not.toContain("증거금의");
  });

  it("모르면 모른다 — 0 으로 그리지 않는다", () => {
    // ⛔ 못 읽은 것을 "+0.00" 으로 그리면 이 프로젝트가 네 번 한 실수를 반복한다.
    expect(unrealised(null)).toBeNull();
    expect(unrealised({ unrealised_pnl: "" })).toBeNull();
    expect(unrealised({ unrealised_pnl: "몰라" })).toBeNull();
  });
});
