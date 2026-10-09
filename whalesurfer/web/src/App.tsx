import { useState } from "react";
import { WhaleSurferPage } from "./WhaleSurfer";

type Theme = "light" | "dark";

function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem("ws-theme", t);
  } catch {
    // 저장소가 막혀도 화면은 돈다 — 다음 방문에 기본값으로.
  }
}

export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem("ws-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // 위와 같다.
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function App() {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) || "light");
  const flip = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  };
  return (
    <div className="mx-auto max-w-7xl px-4 py-4">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-black tracking-tight">WhaleSurfer</h1>
          <p className="text-xs text-gray-500">유명 투자자들의 13F 보유 지도 — 누가 무엇을 들고 있고, 무엇을 사고팔았나</p>
        </div>
        <button
          type="button"
          onClick={flip}
          className="rounded-lg border border-gray-200 px-3 py-1 text-xs dark:border-gray-700"
          aria-label="밝기 바꾸기"
        >
          {theme === "dark" ? "밝게" : "어둡게"}
        </button>
      </header>
      <WhaleSurferPage />
      <footer className="mt-6 text-[11px] leading-relaxed text-gray-500">
        자료: SEC EDGAR 13F-HR(분기 끝 45일 안 공시 · 롱 보유만) · 티커 OpenFIGI · 사진 위키미디어 공용(저작자 표시). 투자 권유가 아니다.
      </footer>
    </div>
  );
}

export { applyTheme };
