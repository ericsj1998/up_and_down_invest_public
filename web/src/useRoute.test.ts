/**
 * 주소 파싱 규칙.
 *
 * 🔴 옛 화면은 탭을 상태로만 들고 있어서 **새로고침이 늘 첫 탭으로** 갔다. 그 탭이
 * 무거운 화면이라 안 쓸 화면이 API 를 여러 번 불렀다.
 */

import { describe, expect, it } from "vitest";
import { firstSegment, pickRoute, routeParam, segments } from "./useRoute";

const KNOWN = ["paper", "live", "console"];

describe("firstSegment", () => {
  it("앞 슬래시를 벗긴다", () => {
    expect(firstSegment("/paper")).toBe("paper");
  });

  it("뒤를 자른다 — 쿼리·해시·하위 경로", () => {
    expect(firstSegment("/console?symbol=BTC_USDT")).toBe("console");
    expect(firstSegment("/live#top")).toBe("live");
    expect(firstSegment("/paper/detail")).toBe("paper");
  });

  it("루트는 빈 문자열이다", () => {
    expect(firstSegment("/")).toBe("");
  });
});

describe("pickRoute", () => {
  it("아는 탭이면 그것", () => {
    expect(pickRoute("/console", KNOWN, "paper")).toBe("console");
  });

  it("⚠️ 모르는 주소는 기본 탭이다 — 빈 화면을 띄우지 않는다", () => {
    expect(pickRoute("/없는탭", KNOWN, "paper")).toBe("paper");
  });

  it("루트도 기본 탭이다", () => {
    expect(pickRoute("/", KNOWN, "paper")).toBe("paper");
  });

  it("⛔ 부분 일치를 탭으로 읽지 않는다", () => {
    // `/papers` 는 `paper` 가 아니다 — 앞부분만 같다고 통과시키면 오타가 조용히 먹는다.
    expect(pickRoute("/papers", KNOWN, "paper")).toBe("paper");
    expect(firstSegment("/papers")).toBe("papers");
  });
});

describe("segments · routeParam — 판이 주소에 있다 (2026-08-19)", () => {
  it("칸으로 자른다", () => {
    expect(segments("/paper/live123")).toEqual(["paper", "live123"]);
    expect(segments("/console")).toEqual(["console"]);
    expect(segments("/")).toEqual([]);
  });

  it("쿼리·해시를 뗀다", () => {
    expect(segments("/paper/live123?frame=1m")).toEqual(["paper", "live123"]);
    expect(segments("/paper/live123#top")).toEqual(["paper", "live123"]);
  });

  it("두 번째 칸이 판 id 다", () => {
    // 🔴 여러 창에 나란히 띄우려면 판이 주소에 있어야 한다 — 화면 상태로 고르면
    //    창마다 같은 판을 보게 된다.
    expect(routeParam("/paper/live61c65273")).toBe("live61c65273");
  });

  it("판 id 가 없으면 빈 문자열이다 — 지어내지 않는다", () => {
    // ⛔ 임의로 하나를 띄우면 주소와 화면이 다른 것을 가리키고, 새로고침할 때마다
    //    다른 판이 뜬다.
    expect(routeParam("/paper")).toBe("");
    expect(routeParam("/")).toBe("");
  });

  it("판 id 가 붙어도 탭은 첫 칸이 정한다", () => {
    expect(pickRoute("/paper/live123", KNOWN, "console")).toBe("paper");
    expect(firstSegment("/paper/live123")).toBe("paper");
  });
});
