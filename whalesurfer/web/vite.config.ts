import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// WhaleSurfer 화면 (T442 §1-1 ② · 2026-10-09 본체 화면에서 분리).
//
// 본체 화면(web/ · 5173)과 **다른 앱**이다 — 코드도 공유하지 않는다. 독자가 다르기 때문이다
// (본체 = 로그인한 나의 운영 콘솔 · 여기 = 불특정 다수에게 보일 공개 페이지).
// API 는 단독 앱 `whalesurfer.api.app`(8010)이 맡는다. `/api` 앞자리를 떼고 넘긴다 — 배포 때 nginx 가
// 같은 일을 하게 하면 화면 코드가 주소를 몰라도 된다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
