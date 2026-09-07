import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ThemeProvider } from "./mt";
import { applyTheme, initialTheme } from "./shell/theme";
// ⚠️ 순서가 뜻이다 — Tailwind(리셋 포함)가 먼저, 옛 화면 CSS 가 뒤에서 이긴다 (T220 6단계까지).
import "./index.css";
import "./app.css";

// 다크 모드 (T34) — **첫 그리기 전에** 정한다. 렌더 뒤에 정하면 밝은 화면이 한 프레임 번쩍인다.
applyTheme(initialTheme());

const root = document.getElementById("root");
if (!root) {
  // ⛔ 조용히 넘어가지 않는다 — 빈 화면이 뜨면 원인을 찾는 데 시간이 든다.
  throw new Error("#root 가 없다 — index.html 을 확인한다");
}

createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
