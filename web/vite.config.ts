import http from "node:http";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// 🔴 **새 화면이 기본이다** (사용자 확정 2026-08-18). 옛 화면(`frontend/`)은 5174 로
//    비켜 두고 유지만 한다 — 지우지 않는 이유는 측정 화면·실험 탭이 아직 거기 있어서다.
//
// API 는 다른 포트에서 돈다. 프록시로 같은 오리진처럼 보이게 해 CORS 설정을 서버에
// 넣지 않는다 — 서버가 브라우저 사정을 알 이유가 없다.

/**
 * `/api` 전달자 — 배포의 nginx 와 **같은 규칙** (T221 Demo Trading · 2026-09-05).
 *
 *   updown_mode=demo 쿠키  → 데모 API (8002 · compose.dev 의 api_demo)
 *   /api/auth/guest        → 늘 데모 (쿠키 무관)
 *   그 외                  → dev API (8000)
 *
 * ⚠️ vite 의 `server.proxy` 는 http-proxy 라 요청별 목적지(`router`)를 지원하지 않는다 — 그 옵션은 조용히 무시됐다
 *    (2026-09-05 실측: 쿠키를 붙여도 8000 으로 갔다). 그래서 미들웨어로 직접 넘긴다. 몸통은 pipe 라 SSE 도 흐른다.
 */
function apiRouter(): Plugin {
  const pick = (req: http.IncomingMessage): number => {
    const cookie = req.headers.cookie ?? "";
    // 기본은 데모 — 쿠키가 live 라고 말할 때만 실계좌 API (nginx.conf 와 같은 규칙 · 2026-09-07).
    const live =
      /(?:^|;\s*)updown_mode=live(?:;|$)/.test(cookie) &&
      req.url !== "/auth/guest";
    return live ? 8000 : 8002;
  };
  return {
    name: "updown-api-router",
    configureServer(server) {
      // `use("/api", …)` 는 앞자리를 떼고 넘긴다 — `req.url` 이 `/exchange/state` 처럼 온다 (nginx 의 rewrite 와 같은 일).
      server.middlewares.use("/api", (req, res) => {
        const port = pick(req);
        const headers = { ...req.headers, host: `localhost:${port}` };
        const up = http.request(
          {
            host: "127.0.0.1",
            port,
            method: req.method,
            path: req.url,
            headers,
          },
          (r) => {
            res.writeHead(r.statusCode ?? 502, r.headers);
            r.pipe(res);
          },
        );
        up.on("error", (exc) => {
          res.writeHead(502, { "content-type": "application/json" });
          res.end(
            JSON.stringify({
              detail: `dev 프록시: ${port} 에 못 붙었다 — ${String(exc)}`,
            }),
          );
        });
        req.pipe(up);
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), apiRouter()],
  // ⚠️ **시험이 CSS 원문을 읽는다** (`console.test.ts`). 고정(`position: sticky`)은
  //    위치·기준면·불투명 배경·경계선 넷이 다 맞아야 도는 성질이라 하나만 빠져도
  //    조용히 망가지는데, 그것을 잡을 방법이 원문 검사뿐이다.
  //
  //    이 스위치가 없으면 vitest 가 CSS 를 **빈 문자열로 대신**해서 `?raw` 가
  //    빈 값이 되고, 시험이 통과하는 것처럼 보인다 (2026-08-30 에 그렇게 속았다).
  test: { css: true },
  server: {
    port: 5173,
  },
});
