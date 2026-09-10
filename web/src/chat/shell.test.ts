/**
 * 채팅 창 배치의 순수 조각 (T257).
 */

import { describe, expect, it } from "vitest";
import { DEFAULT_SHELL, dockSizeAfterDrag, edgeAt, floatAfterResize, isDock, MIN_FLOAT, readShell, SHELL_SLOT } from "./shell";

// 시험 환경(node)엔 localStorage 가 없다 — 메모리로 대신한다.
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, String(v)),
      removeItem: (k: string) => void store.delete(k),
    },
  });
}

describe("edgeAt", () => {
  it("가장자리 안이면 그 변, 가운데면 null", () => {
    expect(edgeAt(10, 400, 1200, 800)).toBe("left");
    expect(edgeAt(1195, 400, 1200, 800)).toBe("right");
    expect(edgeAt(600, 20, 1200, 800)).toBe("top");
    expect(edgeAt(600, 790, 1200, 800)).toBe("bottom");
    expect(edgeAt(600, 400, 1200, 800)).toBeNull();
  });
  it("xl 이면 사이드바 경계 근처는 left-inner · 아니면 아니다", () => {
    expect(edgeAt(310, 400, 1600, 900)).toBe("left-inner");
    expect(edgeAt(310, 400, 1200, 800)).toBeNull();
    expect(edgeAt(20, 400, 1600, 900)).toBe("left");
  });
});

describe("dockSizeAfterDrag", () => {
  it("왼쪽·위는 오른쪽/아래로 끌면 커지고, 오른쪽·아래는 반대다 · 최소·최대 사이", () => {
    expect(dockSizeAfterDrag("left", 400, 50, 1200)).toBe(450);
    expect(dockSizeAfterDrag("left-inner", 400, 50, 1200)).toBe(450);
    expect(dockSizeAfterDrag("right", 400, 50, 1200)).toBe(350);
    expect(dockSizeAfterDrag("top", 360, -30, 800)).toBe(330);
    expect(dockSizeAfterDrag("bottom", 360, -30, 800)).toBe(390);
    expect(dockSizeAfterDrag("left", 400, -1000, 1200)).toBe(240);
    expect(dockSizeAfterDrag("left", 400, 5000, 1200)).toBe(1000);
  });
});

describe("floatAfterResize", () => {
  const start = { x: 100, y: 100, w: 400, h: 500 };
  it("오른쪽·아래는 그쪽으로 끌면 커지고 자리는 그대로", () => {
    expect(floatAfterResize("e", start, 50, 0, 1920, 1080)).toEqual({ x: 100, y: 100, w: 450, h: 500 });
    expect(floatAfterResize("s", start, 0, 40, 1920, 1080)).toEqual({ x: 100, y: 100, w: 400, h: 540 });
    expect(floatAfterResize("se", start, 50, 40, 1920, 1080)).toEqual({ x: 100, y: 100, w: 450, h: 540 });
  });
  it("왼쪽·위는 반대편을 고정한 채 자리도 옮긴다", () => {
    expect(floatAfterResize("w", start, -50, 0, 1920, 1080)).toEqual({ x: 50, y: 100, w: 450, h: 500 });
    expect(floatAfterResize("n", start, 0, 30, 1920, 1080)).toEqual({ x: 100, y: 130, w: 400, h: 470 });
    expect(floatAfterResize("nw", start, 20, 20, 1920, 1080)).toEqual({ x: 120, y: 120, w: 380, h: 480 });
  });
  it("최소 크기 아래로는 안 줄고, 뷰포트 밖으로는 안 자란다", () => {
    expect(floatAfterResize("e", start, -1000, 0, 1920, 1080).w).toBe(MIN_FLOAT.w);
    expect(floatAfterResize("n", start, 0, 1000, 1920, 1080).h).toBe(MIN_FLOAT.h);
    expect(floatAfterResize("se", start, 5000, 5000, 1920, 1080)).toEqual({ x: 100, y: 100, w: 1812, h: 972 });
    // 왼쪽 손잡이를 왼쪽 끝 너머로 — 오른쪽 변은 그대로, 너비는 그 자리까지만
    const wide = floatAfterResize("w", start, -5000, 0, 1920, 1080);
    expect(wide.x + wide.w).toBe(500);
    expect(wide.x).toBeGreaterThanOrEqual(0);
  });
});

describe("readShell", () => {
  it("저장이 없으면 기본 · popout 은 다시 열 때 closed 로", () => {
    localStorage.removeItem(SHELL_SLOT);
    expect(readShell()).toEqual(DEFAULT_SHELL);
    localStorage.setItem(SHELL_SLOT, JSON.stringify({ ...DEFAULT_SHELL, mode: "popout", last: "right" }));
    const got = readShell();
    expect(got.mode).toBe("closed");
    expect(got.last).toBe("right");
    expect(isDock("right") && !isDock("float")).toBe(true);
  });
});
