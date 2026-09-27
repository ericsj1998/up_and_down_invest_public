/**
 * 휴대폰 · PC 알림 켜기 (브라우저 웹 푸시 · 사용자 2026-09-27).
 *
 * 순서: 서비스 워커(`/sw.js`) 등록 → 알림 권한 → 서버 공개 키로 구독 → 구독을 서버에 적는다(`/notify/subscribe`).
 * 서버가 판 원장을 10초마다 견줘 진입 · 청산 · 불타기 · 경보를 보낸다(`apps/api/notify.py`).
 *
 * ⚠️ 아이폰은 **홈 화면에 추가한 앱**에서만 된다(iOS 16.4+) — 사파리 탭에서는 `PushManager` 가 없다.
 * ⚠️ 실계좌 · 데모는 서버가 다르고 키가 다르다 — 켠 쪽 모드의 알림만 온다. 다른 모드에서 켜면 앞 구독은 풀린다.
 */

import { request } from "./api";

/** 권한을 못 받았다 — `message` 는 사람이 할 일, `answer` 는 브라우저의 답. */
export class PermissionError extends Error {
  readonly answer: NotificationPermission;

  constructor(message: string, answer: NotificationPermission) {
    super(message);
    this.name = "PermissionError";
    this.answer = answer;
  }
}

export type PushState =
  | "unsupported" // 이 브라우저는 웹 푸시가 없다
  | "ios-home" // 아이폰 사파리 탭 — 홈 화면에 추가해야 한다
  | "denied" // 사람이 알림을 막았다(브라우저 설정에서 풀어야 한다)
  | "off"
  | "on";

/** base64url(패딩 없음) → 바이트 — `applicationServerKey` 가 받는 모양. */
export function urlBase64ToUint8Array(value: string): Uint8Array<ArrayBuffer> {
  const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** 기기 이름 한 줄 — 서버 구독 목록에서 어느 기기인지 알아보게(개인 정보 없음). */
export function deviceLabel(ua: string): string {
  const os = /iPhone|iPad/.test(ua)
    ? "iOS"
    : /Android/.test(ua)
      ? "안드로이드"
      : /Windows/.test(ua)
        ? "Windows"
        : /Mac OS/.test(ua)
          ? "Mac"
          : "기타";
  const browser = /Edg\//.test(ua)
    ? "엣지"
    : /Chrome\//.test(ua)
      ? "크롬"
      : /Firefox\//.test(ua)
        ? "파이어폭스"
        : /Safari\//.test(ua)
          ? "사파리"
          : "브라우저";
  return `${os} · ${browser}`;
}

function supported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function isIosTab(): boolean {
  const ios = /iPhone|iPad/.test(navigator.userAgent);
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return ios && !standalone;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const found = await navigator.serviceWorker.getRegistration("/");
  return found ?? navigator.serviceWorker.register("/sw.js", { scope: "/" });
}

/** 지금 상태 — 화면 단추가 무엇을 보일지. */
export async function pushState(): Promise<PushState> {
  if (!supported()) return isIosTab() ? "ios-home" : "unsupported";
  if (Notification.permission === "denied") return "denied";
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  return sub ? "on" : "off";
}

/**
 * 권한 창 없이 끝났을 때 사람이 할 일 (사용자 2026-09-27 · "핸드폰에서는 알림 허용이 안 뜬다 · PC 크롬은 떴다").
 *
 * 크롬은 사이트가 차단돼 있거나 "조용한 알림 요청" 이 켜져 있으면 **창을 안 띄우고** 바로 `denied` · `default` 로
 * 답한다 — 전에는 `default` 일 때 화면이 아무 말도 안 해 "눌러도 아무 일이 없다" 로 보였다.
 *
 * @param answer 브라우저가 `requestPermission` 에 준 답.
 * @param ua `navigator.userAgent`.
 */
export function permissionHelp(answer: NotificationPermission, ua: string): string {
  if (answer === "granted") return "";
  const android = /Android/.test(ua);
  const ios = /iPhone|iPad/.test(ua);
  const head =
    answer === "denied"
      ? "브라우저가 권한 창 없이 '거부' 로 답했다 — 이 사이트 알림이 막혀 있다."
      : "브라우저가 권한 창을 띄우지 않고 닫았다(조용한 알림 요청 · 무시됨).";
  if (android) {
    return (
      `${head} 크롬 주소창 왼쪽 아이콘(사이트 정보) → 권한 → 알림 → 허용. ` +
      "안 보이면 크롬 ⋮ → 설정 → 사이트 설정 → 알림 에서 '사이트에서 알림 전송 요청 가능' 을 켜고 차단 목록에서 이 사이트를 뺀다. " +
      "폰 설정 → 애플리케이션 → Chrome → 알림 도 켜져 있어야 한다. 그다음 '알림 켜기' 를 다시 누른다."
    );
  }
  if (ios) {
    return `${head} 설정 → 알림 → (홈 화면에 추가한 이 앱) → 알림 허용을 켠 뒤 다시 누른다.`;
  }
  return `${head} 주소창 왼쪽 자물쇠(사이트 정보) → 알림 → 허용 뒤 다시 누른다.`;
}

/** 권한 결과를 서버 로그에 한 줄(`notify_client_diag`) — 폰에서 무엇이 막혔는지 원격으로 본다. 실패는 무시. */
async function reportDiag(answer: string, before: string): Promise<void> {
  const standalone = window.matchMedia?.("(display-mode: standalone)").matches ?? false;
  await request("/notify/diag", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ answer, before, standalone, device: deviceLabel(navigator.userAgent) }),
  }).catch(() => undefined);
}

/** 알림 켜기 — 권한을 묻고 구독해 서버에 적는다. 권한을 못 받으면 `help` 에 할 일을 싣는다. */
export async function enablePush(): Promise<PushState> {
  if (!supported()) return isIosTab() ? "ios-home" : "unsupported";
  const before = Notification.permission;
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    void reportDiag(permission, before);
    throw new PermissionError(permissionHelp(permission, navigator.userAgent), permission);
  }
  const reg = await registration();
  await navigator.serviceWorker.ready;
  const { public_key } = await request<{ public_key: string }>("/notify/key");
  const key = urlBase64ToUint8Array(public_key);
  let sub = await reg.pushManager.getSubscription();
  // 다른 서버(모드) 키로 만든 구독이면 풀고 다시 — 같은 워커에 키는 하나다.
  const had = sub?.options.applicationServerKey;
  if (sub && had && !sameBytes(new Uint8Array(had), key)) {
    await sub.unsubscribe();
    sub = null;
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await request("/notify/subscribe", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ subscription: sub.toJSON(), device: deviceLabel(navigator.userAgent) }),
  });
  return "on";
}

/** 알림 끄기 — 서버에서 지우고 브라우저 구독도 푼다. */
export async function disablePush(): Promise<PushState> {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await request("/notify/unsubscribe", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ endpoint: sub.endpoint }),
    }).catch(() => undefined);
    await sub.unsubscribe();
  }
  return "off";
}

/** 시험 알림 — 이 기기로만. */
export async function testPush(): Promise<{ sent: number; removed: number; failed: number }> {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  return request("/notify/test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ endpoint: sub?.endpoint ?? "" }),
  });
}

export function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}
