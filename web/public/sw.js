/*
 * 웹 푸시 서비스 워커 (사용자 2026-09-27 · "진입, 익절, 손절, 청산, 경보 등은 컴퓨터, 핸드폰 등 알림으로").
 *
 * 창을 닫아도 브라우저가 이 파일을 깨워 알림을 띄운다. 서버(`apps/api/notify.py`)가 보내는 본문:
 *   { title, body, tag, kind, url }
 * 같은 tag 는 앞 알림을 덮는다 — 매매 하나에 알림 하나(진입 → 청산이 같은 줄을 바꾼다).
 *
 * ⛔ 캐시 · 오프라인은 하지 않는다 — 이 워커는 알림만 한다(옛 화면이 남는 일을 만들지 않는다).
 */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "업 앤 다운", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "업 앤 다운";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      tag: data.tag || undefined,
      renotify: Boolean(data.tag),
      icon: "/favicon-192.png",
      badge: "/favicon-192.png",
      data: { url: data.url || "/console" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/console";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ("focus" in client) {
          client.navigate?.(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
