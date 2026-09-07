/**
 * 오른쪽 끝에 붙이기 — **팅기지 않는다** (사용자 신고 2026-08-30).
 *
 * > *"라이브 버튼 누르면 계속 오른쪽으로 한번 팅기듯이 움직였다가 다시 고정되네.
 * >  너무 눈이 아파."*
 *
 * ## 🔴 원인은 "붙는 자리" 가 **두 벌**이었던 것이다
 *
 * 단추는 `scrollToRealTime()` 을 불렀고 — 그것은 **애니메이션으로** 라이브러리 자체의
 * 오른쪽 여백까지 날아간다 — 도착하자마자 구독이 깨어나 우리 여백(`RIGHT_PAD`)으로
 * 도로 잡아당겼다. **그 왕복이 팅김이었다.**
 *
 * 게다가 그 효과가 `frame.candles` 에 딸려 있어 **봉이 올 때마다** 다시 돌았다.
 * 한 번 거슬리는 것이 아니라 갱신마다 반복됐다.
 *
 * ⇒ 이 파일이 지키는 것은 하나다: **붙이는 방법이 한 벌이고, 이미 붙어 있으면
 *   아무것도 안 한다.** 두 자리가 같은 자리를 뜻하면 서로 잡아당길 수 없다.
 */

import { describe, expect, it } from "vitest";
import { RIGHT_PAD, pinRight } from "./Chart";

type Range = { from: number; to: number } | null;

/**
 * `IChartApi` 중 `pinRight` 가 쓰는 부분만 흉내 낸다.
 *
 * ⚠️ 진짜 차트는 캔버스를 요구한다 — 여기서 재려는 것은 **산수와 호출 횟수**이지
 * 그리기가 아니다.
 */
function fake(range: Range) {
  const writes: { from: number; to: number }[] = [];
  const chart = {
    timeScale: () => ({
      getVisibleLogicalRange: () => range,
      setVisibleLogicalRange: (next: { from: number; to: number }) => {
        writes.push(next);
      },
    }),
  };
  return { chart: chart as never, writes };
}

describe("pinRight — 붙이되 흔들지 않는다", () => {
  it("멀리 있으면 오른쪽 끝으로 옮긴다", () => {
    const { chart, writes } = fake({ from: 0, to: 50 });
    expect(pinRight(chart, 200)).toBe(true);
    // 끝은 마지막 봉 + 여백.
    expect(writes[0]?.to).toBe(199 + RIGHT_PAD);
  });

  it("🔴 **배율을 유지한다** — 폭이 그대로여야 한다", () => {
    // 사용자 요구: *"휠 확대 축소 … 동작 시에도 오른쪽은 항상 가장 최신 봉"* —
    // 붙이면서 확대까지 흔들면 그것도 팅김이다.
    const { chart, writes } = fake({ from: 100, to: 140 });
    pinRight(chart, 500);
    expect(writes[0]!.to - writes[0]!.from).toBe(40);
  });

  it("⛔ **이미 붙어 있으면 아무것도 안 한다**", () => {
    // 이것이 팅김을 막는 자리다. 같은 값을 다시 넣으면 라이브러리가 다시 그리고,
    // 그것이 봉마다 한 번이면 눈에 띈다.
    const edge = 199 + RIGHT_PAD;
    const { chart, writes } = fake({ from: edge - 40, to: edge });
    expect(pinRight(chart, 200)).toBe(false);
    expect(writes).toEqual([]);
  });

  it("반 봉 안이면 붙은 것으로 친다 — 봉이 닫힐 때마다 깜빡이면 안 된다", () => {
    const edge = 199 + RIGHT_PAD;
    const { chart, writes } = fake({ from: edge - 40, to: edge - 0.4 });
    expect(pinRight(chart, 200)).toBe(false);
    expect(writes).toEqual([]);
  });

  it("반 봉을 넘으면 다시 붙인다", () => {
    const edge = 199 + RIGHT_PAD;
    const { chart, writes } = fake({ from: edge - 40, to: edge - 0.6 });
    expect(pinRight(chart, 200)).toBe(true);
    expect(writes).toHaveLength(1);
  });

  it("🔴 **두 번 불러도 한 번만 움직인다** — 단추와 구독이 겹쳐도 안전하다", () => {
    // 팅김의 모양이 정확히 이것이었다: 한쪽이 옮기고 다른 쪽이 또 옮겼다.
    let range: Range = { from: 0, to: 50 };
    const writes: { from: number; to: number }[] = [];
    const chart = {
      timeScale: () => ({
        getVisibleLogicalRange: () => range,
        setVisibleLogicalRange: (next: { from: number; to: number }) => {
          writes.push(next);
          range = next;
        },
      }),
    } as never;
    expect(pinRight(chart, 200)).toBe(true);
    expect(pinRight(chart, 200)).toBe(false);
    expect(writes).toHaveLength(1);
  });

  it("봉이 없으면 안 움직인다", () => {
    const { chart, writes } = fake({ from: 0, to: 50 });
    expect(pinRight(chart, 0)).toBe(false);
    expect(writes).toEqual([]);
  });

  it("보이는 범위를 아직 모르면 안 움직인다", () => {
    const { chart, writes } = fake(null);
    expect(pinRight(chart, 200)).toBe(false);
    expect(writes).toEqual([]);
  });

  it("⚠️ 여백을 남긴다 — 0 이면 마지막 봉이 축 라벨에 붙어 잘려 보인다", () => {
    expect(RIGHT_PAD).toBeGreaterThan(0);
  });
});
