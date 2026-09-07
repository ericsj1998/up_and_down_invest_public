/**
 * 진행 중 봉 폴링 주기.
 *
 * 🔴 처음에는 `refresh()` 를 따라 *간격의 1/10* 로 짰다가 이 테스트가 **전제가 틀린
 * 것**을 잡았다 — 1분봉이 6초로 나왔다. `refresh()` 는 *"새 봉이 언제 생기나"* 를
 * 재고, 진행 중 봉은 *"지금 가격이 얼마인가"* 다. 후자는 간격과 아무 상관이 없다.
 */

import { describe, expect, it } from "vitest";
import { formingMs } from "./useForming";

describe("formingMs", () => {
  it("🔴 축이 무엇이든 1초다 — 종가는 매 체결마다 바뀐다", () => {
    for (const frame of ["10s", "1m", "15m", "1h", "1d"]) {
      expect(formingMs(frame)).toBe(1000);
    }
  });

  it("⚠️ 모르는 축에도 0 을 내지 않는다 — 0 이면 브라우저가 폭주한다", () => {
    expect(formingMs("???")).toBeGreaterThanOrEqual(1000);
  });
});
