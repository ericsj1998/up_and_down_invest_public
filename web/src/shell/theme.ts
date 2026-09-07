/**
 * 밝기 — `<html data-theme>` 하나가 진실이다 (T34 · T220).
 *
 * 옛 CSS(`app.css`)와 Tailwind(`darkMode: selector`)가 **같은 속성**을 본다. 저장값이 없으면 OS 설정을 따르고,
 * 첫 그리기 전에 `main.tsx` 가 정한다 (렌더 뒤에 정하면 밝은 화면이 한 프레임 번쩍인다).
 */
import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

export function initialTheme(): Theme {
  const saved = localStorage.getItem("theme");
  if (saved === "dark" || saved === "light") return saved;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(
    () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"),
  );

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((was) => {
      const next: Theme = was === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("theme", next);
      } catch {
        // 기억만 못 한다.
      }
      return next;
    });
  }, []);

  return [theme, toggle];
}
