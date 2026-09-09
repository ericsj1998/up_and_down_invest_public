/**
 * 채팅 창 배치의 순수 조각 (T257).
 */

import { describe, expect, it } from "vitest";
import { DEFAULT_SHELL, dockSizeAfterDrag, edgeAt, isDock, readShell, SHELL_SLOT } from "./shell";

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
});

describe("dockSizeAfterDrag", () => {
  it("왼쪽·위는 오른쪽/아래로 끌면 커지고, 오른쪽·아래는 반대다 · 최소·최대 사이", () => {
    expect(dockSizeAfterDrag("left", 400, 50, 1200)).toBe(450);
    expect(dockSizeAfterDrag("right", 400, 50, 1200)).toBe(350);
    expect(dockSizeAfterDrag("top", 360, -30, 800)).toBe(330);
    expect(dockSizeAfterDrag("bottom", 360, -30, 800)).toBe(390);
    expect(dockSizeAfterDrag("left", 400, -1000, 1200)).toBe(240);
    expect(dockSizeAfterDrag("left", 400, 5000, 1200)).toBe(1000);
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
