/**
 * 웹 푸시 도우미 — 공개 키 모양 바꾸기 · 기기 이름 (사용자 2026-09-27).
 */

import { describe, expect, it } from "vitest";
import { deviceLabel, permissionHelp, sameBytes, urlBase64ToUint8Array } from "./pushNotify";

describe("urlBase64ToUint8Array", () => {
  it("패딩 없는 base64url 을 바이트로 — '-' '_' 도 읽는다", () => {
    expect(Array.from(urlBase64ToUint8Array("AQID"))).toEqual([1, 2, 3]);
    expect(Array.from(urlBase64ToUint8Array("-_8"))).toEqual([251, 255]);
  });

  it("65바이트 공개 점(0x04 로 시작)", () => {
    const raw = new Uint8Array(65).fill(7);
    raw[0] = 4;
    const b64 = btoa(String.fromCharCode(...raw)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const got = urlBase64ToUint8Array(b64);
    expect(got.length).toBe(65);
    expect(got[0]).toBe(4);
    expect(sameBytes(got, raw)).toBe(true);
  });
});

describe("deviceLabel", () => {
  it("운영체제 · 브라우저 한 줄", () => {
    expect(deviceLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Version/17.0 Safari/604.1")).toBe(
      "iOS · 사파리",
    );
    expect(deviceLabel("Mozilla/5.0 (Windows NT 10.0) Chrome/128.0 Safari/537.36")).toBe("Windows · 크롬");
    expect(deviceLabel("Mozilla/5.0 (Linux; Android 14) Chrome/128.0 Mobile Safari/537.36")).toBe("안드로이드 · 크롬");
  });
});

describe("permissionHelp", () => {
  const android = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/153.0.0.0 Mobile Safari/537.36";
  it("안드로이드 크롬 — 창 없이 닫힘(default)도 할 일을 말한다", () => {
    const text = permissionHelp("default", android);
    expect(text).toContain("권한 창을 띄우지 않고");
    expect(text).toContain("사이트 설정 → 알림");
    expect(text).toContain("Chrome → 알림");
  });
  it("거부 · 허용", () => {
    expect(permissionHelp("denied", android)).toContain("거부");
    expect(permissionHelp("granted", android)).toBe("");
  });
});
