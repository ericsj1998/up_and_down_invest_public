/**
 * 페이퍼넷 자금 링크 — **두 번 틀린 값이라 시험이 지킨다**.
 *
 * 🔴 이 파일은 원래 `canClaim`(총 자산 1000 초과면 못 받는다)을 재고 있었다. 그 규칙은
 * **이 화면과 무관한 문서**에서 온 것이었고 (2026-08-21 사용자 확인), 그래서 판정 자체를
 * 지웠다. 지금 지킬 값어치가 있는 것은 규칙이 아니라 **주소**다 —
 *
 * ```
 * 1차  /testnet/futures_trade/USDT/BTC_USDT   확인 없이 넣었다 → 빈 화면
 * 2차  /help/futures/.../how-to-trade-on-testnet  도움말 → 틀린 규칙의 출처였다
 * 3차  testnet.gate.com/myaccount/myfunds     사람이 열어서 확인했다
 * ```
 *
 * ⚠️ Gate 는 자동 요청을 Cloudflare 로 막으므로 **링크가 사는지 시험이 확인할 수 없다.**
 * 시험이 할 수 있는 것은 *"확인된 값에서 조용히 미끄러지지 않는가"* 뿐이다.
 */

import { describe, expect, it } from "vitest";
import { FUNDS_URL, PATH } from "./Faucet";

describe("자금 링크", () => {
  it("사람이 확인한 그 주소다", () => {
    expect(FUNDS_URL).toBe("https://testnet.gate.com/myaccount/myfunds");
  });

  it("틀린 것으로 판명된 주소로 돌아가지 않는다", () => {
    expect(FUNDS_URL).not.toContain("futures_trade");
    expect(FUNDS_URL).not.toContain("/help/");
  });

  it("메인넷이 아니라 테스트넷이다", () => {
    // 🔴 이 한 글자가 빠지면 **진짜 돈이 있는 화면**이 열린다.
    expect(FUNDS_URL).toContain("testnet.");
  });

  it("링크가 죽어도 갈 길이 글로 남는다", () => {
    // ⚠️ 주소와 글이 따로 놀면 글 쪽이 사람을 엉뚱한 데로 보낸다.
    for (const part of ["testnet.gate.com", "myaccount", "myfunds"]) {
      expect(PATH).toContain(part);
      expect(FUNDS_URL).toContain(part);
    }
  });
});
