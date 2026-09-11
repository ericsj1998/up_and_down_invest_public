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

/**
 * `data-theme` 이 바뀔 때마다 다시 그리게 하는 구독 (2026-09-12).
 *
 * 🔴 ApexCharts 는 옵션을 **그릴 때 한 번** 읽는다. 밝기를 문서 루트에서 직접 읽어 가므로,
 *    사람이 토글을 눌러도 차트는 옛 밝기를 쥔 채 남았다 — 어두운 바탕에 어두운 축 글씨라
 *    "글씨가 안 보인다" 로 보인다. 가격 차트(`Chart.tsx`)는 자기 관찰자가 있어 멀쩡했고,
 *    그래서 화면마다 증상이 달랐다.
 *
 * Returns:
 *    지금 밝기. 바뀌면 값이 달라져 쓰는 컴포넌트가 다시 그린다.
 */
export function useThemeValue(): Theme {
  const [now, setNow] = useState<Theme>(
    () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"),
  );
  useEffect(() => {
    const root = document.documentElement;
    const watcher = new MutationObserver(() => {
      setNow(root.dataset.theme === "dark" ? "dark" : "light");
    });
    watcher.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => watcher.disconnect();
  }, []);
  return now;
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
