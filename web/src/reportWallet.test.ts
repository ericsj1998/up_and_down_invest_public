/**
 * 리포트 지갑 그래프 층 — 회색(넣은 돈 중 남은 몫) · 빨강(잃은 몫) · 초록(번 몫) (사용자 2026-09-27).
 *
 * 🔴 못 박는 것: 입금은 **회색만** 키운다(번 돈으로 보이면 안 된다) · 빨강과 초록은 한 점에서 동시에 서지 않는다.
 */

import { describe, expect, it } from "vitest";
import { walletLayers, windowGain, type WalletRow } from "./reportWallet";

const at = (h: number) => new Date(Date.UTC(2026, 8, 20, h)).toISOString();
const row = (h: number, balance: string, principal: string): WalletRow => ({
  at: at(h),
  balance,
  principal,
});

describe("walletLayers", () => {
  it("벌면 초록 · 잃으면 빨강 · 둘이 동시에 서지 않는다", () => {
    const got = walletLayers([row(0, "310", "300"), row(1, "290", "300")]);
    expect(got.kept).toEqual([300, 290]);
    expect(got.earned).toEqual([10, 0]);
    expect(got.lost).toEqual([0, 10]);
  });

  it("쌓은 꼭대기 = 잔고(벌 때) · 넣은 돈(잃을 때)", () => {
    const got = walletLayers([row(0, "310", "300"), row(1, "290", "300")]);
    expect((got.kept[0] ?? 0) + (got.earned[0] ?? 0)).toBe(310);
    expect((got.kept[1] ?? 0) + (got.lost[1] ?? 0)).toBe(300);
  });

  it("🔴 입금은 회색만 키운다 — 번 돈은 그대로", () => {
    const got = walletLayers([row(0, "310", "300"), row(1, "376", "366")]);
    expect(got.earned).toEqual([10, 10]);
    expect(got.kept).toEqual([300, 366]);
  });

  it("못 읽는 점은 건너뛴다 — 0 으로 꾸미지 않는다", () => {
    const got = walletLayers([row(0, "", "300"), row(1, "305", "300"), { at: "x", balance: "1", principal: "1" }]);
    expect(got.x).toHaveLength(1);
    expect(got.balance).toEqual([305]);
  });

  it("y 축 바닥은 가장 낮은 회색 꼭대기의 90% 를 10 단위로 내림", () => {
    expect(walletLayers([row(0, "406.8", "366.58")]).floor).toBe(320);
    expect(walletLayers([]).floor).toBe(0);
  });
});

describe("windowGain", () => {
  it("기간 손익 = 잔고 변화 − 넣은 돈 변화 — 입금은 손익이 아니다", () => {
    const got = walletLayers([row(0, "310", "300"), row(1, "376", "366"), row(2, "370", "366")]);
    expect(windowGain(got)).toBe(-6);
  });

  it("🔴 지금까지 번 돈(+40)이 있어도 기간에 움직임이 없으면 0", () => {
    const got = walletLayers([row(0, "406.82", "366.58"), row(5, "406.82", "366.58")]);
    expect(windowGain(got)).toBe(0);
  });

  it("잃은 기간은 음수 · 점이 없으면 null", () => {
    expect(windowGain(walletLayers([row(0, "400", "366"), row(1, "390.5", "366")]))).toBe(-9.5);
    expect(windowGain(walletLayers([]))).toBeNull();
  });
});
