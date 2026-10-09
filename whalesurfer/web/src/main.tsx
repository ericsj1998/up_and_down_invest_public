import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App, applyTheme, initialTheme } from "./App";
import "./index.css";

// 다크 모드 — 첫 그리기 전에 정한다(밝은 화면이 한 프레임 번쩍이지 않게).
applyTheme(initialTheme());

const root = document.getElementById("root");
if (!root) {
  // 조용히 넘어가지 않는다 — 빈 화면이 뜨면 원인을 찾는 데 시간이 든다.
  throw new Error("#root 가 없다 — index.html 을 확인한다");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
