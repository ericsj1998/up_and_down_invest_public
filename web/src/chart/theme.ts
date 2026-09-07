/**
 * 차트 색 — CSS 토큰을 캔버스 값으로 푼다. 차트는 DOM 밖(캔버스)이라 변수를 값으로 넘겨야 한다.
 *
 * ⚠️ **그릴 때마다 푼다.** 값으로 담아 두면 모듈이 처음 읽힐 때의 테마에 굳는다 (Chart.tsx 의 같은 교훈).
 */

export function tone(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const found = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return found || fallback;
}

/** `#rrggbb` 에 투명도를 붙인다 — 영역은 봉을 가리면 안 된다. */
export function wash(hex: string, alpha: number): string {
  const raw = hex.replace("#", "");
  if (raw.length !== 6) return hex;
  const r = parseInt(raw.slice(0, 2), 16);
  const g = parseInt(raw.slice(2, 4), 16);
  const b = parseInt(raw.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** 차트가 쓰는 색 한 벌 — 테마가 바뀌면 다시 부른다. */
export function palette() {
  return {
    up: tone("--gain", "#0f7b6c"),
    down: tone("--loss", "#b4423a"),
    entry: tone("--entry-line", "#b8860b"),
    text: tone("--warm-gray", "#78716c"),
    grid: tone("--stone-border", "#e8e6e5"),
    bg: tone("--pure-white", "#ffffff"),
    accent: tone("--cyan-signal", "#2196f3"),
    ma: tone("--ma-line", "#8957e5"),
  };
}

/** `data-theme` 이 바뀌면 부른다 — 해제 함수를 돌려준다. */
export function onThemeChange(fn: () => void): () => void {
  if (typeof MutationObserver === "undefined") return () => {};
  const watcher = new MutationObserver(fn);
  watcher.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => watcher.disconnect();
}
