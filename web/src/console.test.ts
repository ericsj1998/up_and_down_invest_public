/**
 * 콘솔 화면의 두 가지 — **누구로 보고 있나**와 **단추가 안 도망가나** (2026-08-30).
 *
 * 사용자 요구:
 *
 * > *"트레이딩 콘솔에서 유저 카드에 로그인한 사용자의 구글 아이디가 표기되게 해줘."*
 * > *"도는 RUN에서 '편집' '열기' '새탭에서 열기'가 스크롤을 좌우로 옮겼을 때 항상
 * >  고정되어있게 해줘."*
 *
 * ## 왜 CSS 를 시험하나
 *
 * 고정(`position: sticky`)은 **네 가지가 다 맞아야** 도는 성질이다 — 위치·기준면·
 * 불투명 배경·경계선. 하나만 빠져도 "되는 것 같은데 글자가 비친다" 로 조용히
 * 망가지고, 그것은 눈으로만 잡힌다. 이 시험은 그 넷이 다 있는지 본다.
 */

import { describe, expect, it } from "vitest";
import CSS from "./app.css?raw";
import TOKENS from "./tokens.css?raw";
import { roleName } from "./ConsoleTab";

/** `th.head-act, td.act` 규칙 본문만 — 파일 전체에서 찾으면 남의 규칙이 통과시킨다. */
function actRule(): string {
  const head = CSS.indexOf("th.head-act,");
  expect(head).toBeGreaterThan(-1);
  return CSS.slice(head, CSS.indexOf("}", head));
}

describe("액션 열 고정 — 좌우로 밀어도 따라온다", () => {
  it("⚠️ **CSS 를 실제로 읽었나** — 못 읽으면 아래가 전부 헛돈다", () => {
    // 🔴 vitest 는 기본값에서 CSS 를 **빈 문자열로 대신한다.** 그러면 `?raw` 가 빈
    //    값이 되고 아래 시험들이 "규칙을 못 찾았다" 로 실패한다 — 다행히 시끄럽게
    //    실패하지만, 왜인지는 여기서만 보인다 (`vite.config.ts` 의 `test.css`).
    expect(CSS.length).toBeGreaterThan(1000);
    expect(TOKENS.length).toBeGreaterThan(100);
  });

  it("🔴 오른쪽에 고정한다", () => {
    const rule = actRule();
    expect(rule).toContain("position: sticky");
    expect(rule).toContain("right: 0");
  });

  it("⚠️ **불투명 배경**이 있어야 한다 — 없으면 밑의 글자가 비친다", () => {
    // 고정된 칸은 다른 칸 **위로** 지나간다. 배경이 없으면 두 겹의 글자가 겹쳐 보이고,
    // 그것은 "고장" 이 아니라 "읽을 수 없음" 이라 더 나쁘다.
    expect(actRule()).toContain("background: var(--pure-white)");
  });

  it("⚠️ 다크 모드에서도 불투명해야 한다", () => {
    // `--pure-white` 는 다크에서 **어두운 값으로 재정의**된다 — 이름과 달리 양쪽
    // 테마에서 안전한 이유가 그것이다. 그 재정의가 사라지면 다크 화면의 표 오른쪽에
    // 흰 띠가 생기고, 고정 열만 딴 세상처럼 보인다.
    const dark = TOKENS.slice(TOKENS.indexOf('[data-theme="dark"]'));
    expect(dark).toContain("--pure-white:");
  });

  it("⚠️ 경계선을 **그림자로** 그린다 — collapse 된 표는 고정 칸에 테두리를 안 그린다", () => {
    expect(actRule()).toContain("box-shadow: inset");
  });

  it("겹침 순서를 정한다 — 안 정하면 브라우저마다 다르게 보인다", () => {
    expect(actRule()).toContain("z-index");
  });

  it("🔴 표가 **가로로 스크롤**되어야 고정에 뜻이 있다", () => {
    // `.table-wrap` 이 `overflow-x: auto` 를 잃으면 고정은 아무 일도 안 한다 —
    // 그러면 이 시험들이 전부 통과하는데 화면은 안 고쳐진 상태가 된다.
    const head = CSS.indexOf(".table-wrap {");
    expect(head).toBeGreaterThan(-1);
    expect(CSS.slice(head, CSS.indexOf("}", head))).toContain("overflow-x: auto");
  });
});

describe("roleName — 등급을 사람이 쓰는 말로", () => {
  it.each([
    ["admin", "관리자"],
    ["trader", "거래 허용"],
    ["viewer", "승인됨 · 읽기"],
    ["pending", "승인 대기 · 읽기"],
  ])("%s → %s", (role, want) => {
    expect(roleName(role)).toBe(want);
  });

  it("⛔ 모르는 등급은 **가장 좁은 권한**으로 읽는다", () => {
    // 모르는 값을 "관리자" 로 읽으면 화면이 없는 권한을 있다고 말한다.
    expect(roleName(undefined)).toBe("승인 대기 · 읽기");
    expect(roleName("무엇인가")).toBe("승인 대기 · 읽기");
  });
});
