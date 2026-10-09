/**
 * WhaleSurfer 화면 Tailwind (T442 · 2026-10-09). 본체(web/)의 Material Tailwind 는 쓰지 않는다 —
 * 공개 페이지라 가볍게 · 본체 부품에 묶이지 않게. 다크 모드는 `<html data-theme="dark">`.
 * `.cjs` 인 이유는 package.json 이 `"type": "module"` 이어서다.
 */
module.exports = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Pretendard", "Apple SD Gothic Neo", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SF Mono", "Cascadia Mono", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        // 늘림 · 줄임 부호 — 장식이 아니라 정보다(본체와 같은 값).
        gain: { DEFAULT: "#0f7b6c", wash: "#dcf0eb" },
        loss: { DEFAULT: "#b4423a", wash: "#f8e5e3" },
        gray: { 800: "#26313f", 900: "#161d26", 950: "#0e141b" },
      },
    },
  },
  plugins: [],
};
