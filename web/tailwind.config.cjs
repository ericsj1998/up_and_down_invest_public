/**
 * Tailwind + Material Tailwind (T220 · 2026-09-05).
 *
 * 🔴 `withMT` 가 MT 컴포넌트가 쓰는 색·그림자·반경을 테마에 넣는다 — 이걸 빼면 MT 컴포넌트가
 *    클래스 이름은 붙는데 색이 안 나온다(조용히 흰 화면). `.cjs` 인 이유는 package.json 이
 *    `"type": "module"` 이라 `require` 를 쓰는 이 파일은 CommonJS 로 따로 표시해야 해서다.
 *
 * ⚠️ `content` 에 MT 의 node_modules 경로가 들어 있어야 한다 — MT 는 자기 컴포넌트 안에서
 *    Tailwind 클래스를 문자열로 만들고, 여기 없으면 그 클래스가 빌드에서 잘려 나간다.
 *
 * 다크 모드는 우리 관습(`<html data-theme="dark">`)을 그대로 쓴다 — `main.tsx` 가 첫 그리기 전에
 * 정한다. `class` 전략으로 바꾸지 않는 이유는 옛 CSS(`app.css`)가 그 속성을 보고 있어서다.
 */
const withMT = require("@material-tailwind/react/utils/withMT");

module.exports = withMT({
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    "./node_modules/@material-tailwind/react/components/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@material-tailwind/react/theme/components/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Pretendard", "Apple SD Gothic Neo", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SF Mono", "Cascadia Mono", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        // 손익 부호 — 장식이 아니라 정보다 (tokens.css 의 --gain/--loss 를 그대로).
        gain: { DEFAULT: "#0f7b6c", wash: "#dcf0eb" },
        loss: { DEFAULT: "#b4423a", wash: "#f8e5e3" },
        // 🔴 다크의 바탕·카드는 **두 벌이 같은 값이어야 한다** (2026-09-12).
        //    셸(Tailwind `dark:bg-gray-950/900`)과 옛 화면(`tokens.css` 의 --stone-canvas/--pure-white)이
        //    각자 색을 들고 있어, 토큰만 고치면 사이드바와 카드가 서로 다른 검정이 된다.
        //    여기서 두 칸을 토큰과 같은 값으로 덮어 한 벌로 만든다.
        //    800 은 카드 테두리(`dark:border-gray-800`)와 눌림 배경으로 쓰여 같이 맞춘다.
        gray: { 800: "#26313f", 900: "#161d26", 950: "#0e141b" },
      },
      // 로그인 화면 움직임 (사용자 참고안 apsn_knowledge_graph_app · 2026-09-05): 카드 미끄러져 들어옴 ·
      // 살아 있음을 말하는 점의 맥박 · 배경 빛 덩이의 느린 표류 · 항목 차례로 떠오름. 전부 `motion-reduce:animate-none`.
      keyframes: {
        "card-in": { from: { opacity: "0", transform: "translateY(14px) scale(0.98)" }, to: { opacity: "1", transform: "none" } },
        "fade-up": { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "none" } },
        "pulse-dot": { "50%": { opacity: "0.35" } },
        float: {
          "0%, 100%": { transform: "translate3d(0, 0, 0) scale(1)" },
          "50%": { transform: "translate3d(20px, -26px, 0) scale(1.08)" },
        },
        "float-alt": {
          "0%, 100%": { transform: "translate3d(0, 0, 0) scale(1.05)" },
          "50%": { transform: "translate3d(-24px, 18px, 0) scale(0.96)" },
        },
        spin: { to: { transform: "rotate(360deg)" } },
        // 하단 키워드 마퀴 — 두 벌을 이어 붙여 -50% 까지 밀면 끊김 없이 돈다 (참고안 home-marquee).
        marquee: { from: { transform: "translateX(0)" }, to: { transform: "translateX(-50%)" } },
        // 로그인 오른쪽 분석 패널 — 천천히 재생되는 그림들 (선 그리기 · 막대 자라기 · 링 채우기 · 떠오르기).
        "draw-line": { from: { strokeDashoffset: "1000" }, to: { strokeDashoffset: "0" } },
        "grow-y": { from: { transform: "scaleY(0.12)" }, to: { transform: "scaleY(1)" } },
        "ring-fill": { from: { strokeDashoffset: "163" }, to: { strokeDashoffset: "44" } },
        "float-y": { "0%, 100%": { transform: "translateY(0)" }, "50%": { transform: "translateY(-8px)" } },
        "slide-x": { from: { transform: "translateX(0)" }, to: { transform: "translateX(-50%)" } },
      },
      animation: {
        "card-in": "card-in 0.4s ease-out both",
        "fade-up": "fade-up 0.5s ease-out both",
        "pulse-dot": "pulse-dot 1.4s ease-in-out infinite",
        float: "float 14s ease-in-out infinite",
        "float-alt": "float-alt 18s ease-in-out infinite",
        spin: "spin 0.8s linear infinite",
        marquee: "marquee 44s linear infinite",
        "draw-line": "draw-line 7s ease-in-out infinite alternate",
        "grow-y": "grow-y 3.2s ease-in-out infinite alternate",
        "ring-fill": "ring-fill 5s ease-in-out infinite alternate",
        "float-y": "float-y 7s ease-in-out infinite",
        "slide-x": "slide-x 40s linear infinite",
      },
    },
  },
  plugins: [],
});
