/**
 * 배경 차트 — 천천히 흐르는 캔들 (로그인 화면 · 2026-09-05 사용자 요구: "배경에 애니메이션으로 천천히 움직이는 차트").
 *
 * 무작위 걸음으로 만든 봉이 오른쪽에서 태어나 왼쫽으로 흘러간다. 실제 시세가 아니다 — 장식이고, 그래서 값·축·눈금을
 * 그리지 않는다(숫자를 지어내지 않는다). 색은 CSS 토큰(--gain/--loss/--stone-muted)에서 읽어 다크 모드에 따라온다.
 *
 * ⚠️ OS "동작 줄이기" 와 무관하게 돈다 (사용자 요구). 탭이 숨으면 브라우저가 rAF 를 멈춰 CPU 를 안 쓴다.
 */
import { useEffect, useRef } from "react";

type Candle = { o: number; h: number; l: number; c: number };

function readPalette() {
  const s = getComputedStyle(document.documentElement);
  const pick = (name: string, fallback: string) => s.getPropertyValue(name).trim() || fallback;
  return {
    gain: pick("--gain", "#0f7b6c"),
    loss: pick("--loss", "#b4423a"),
    grid: pick("--stone-muted", "#b0bec5"),
    line: pick("--cyan-signal", "#2196f3"),
  };
}

export function ChartField({ className = "" }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // ⭐ OS "동작 줄이기" 를 따르지 않는다 — 로그인 화면의 배경 차트는 사용자가 요구한 움직임이다 (2026-09-05).
    const reduce = false;
    let palette = readPalette();
    const themeObs = new MutationObserver(() => {
      palette = readPalette();
    });
    themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    const STEP = 22; // 봉 간격(px)
    const SPEED = 0.22; // px/frame — 한 봉 지나는 데 ~100 프레임(1.7초)
    let W = 0;
    let H = 0;
    let candles: Candle[] = [];
    let offset = 0; // 0..STEP 사이를 흐르다 한 봉을 밀어낸다
    let last = 100;

    const rand = (a: number, b: number) => a + Math.random() * (b - a);
    const nextCandle = (): Candle => {
      const o = last;
      const drift = rand(-1.6, 1.6);
      const c = o + drift;
      const wick = rand(0.4, 2.2);
      const h = Math.max(o, c) + wick * Math.random();
      const l = Math.min(o, c) - wick * Math.random();
      last = c;
      // 너무 멀리 가지 않게 가운데로 당긴다 — 화면을 벗어난 차트는 차트가 아니다.
      last += (100 - last) * 0.02;
      return { o, h, l, c };
    };

    const build = () => {
      const rect = canvas.getBoundingClientRect();
      W = rect.width;
      H = rect.height;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.floor(W * dpr));
      canvas.height = Math.max(1, Math.floor(H * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.ceil(W / STEP) + 3;
      last = 100;
      candles = Array.from({ length: n }, nextCandle);
    };

    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      // 세로 격자 — 아주 옅게, 봉 간격에 맞춰 함께 흐른다.
      ctx.strokeStyle = palette.grid;
      ctx.globalAlpha = 0.12;
      ctx.lineWidth = 1;
      for (let x = -offset; x < W; x += STEP * 4) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
      }
      // 값 → y: 봉들의 최고·최저를 화면 세로 20~80% 에 맞춘다.
      let lo = Infinity;
      let hi = -Infinity;
      for (const k of candles) {
        lo = Math.min(lo, k.l);
        hi = Math.max(hi, k.h);
      }
      const pad = (hi - lo) * 0.15 || 1;
      lo -= pad;
      hi += pad;
      const y = (v: number) => H * 0.82 - ((v - lo) / (hi - lo)) * H * 0.64;

      // 종가 선 — 봉 뒤에 부드럽게.
      ctx.strokeStyle = palette.line;
      ctx.globalAlpha = 0.22;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      candles.forEach((k, i) => {
        const x = i * STEP - offset + STEP / 2;
        if (i === 0) ctx.moveTo(x, y(k.c));
        else ctx.lineTo(x, y(k.c));
      });
      ctx.stroke();

      // 봉.
      const bodyW = Math.max(6, STEP * 0.55);
      candles.forEach((k, i) => {
        const x = i * STEP - offset + STEP / 2;
        const up = k.c >= k.o;
        const color = up ? palette.gain : palette.loss;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.55;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, y(k.h));
        ctx.lineTo(x, y(k.l));
        ctx.stroke();
        const top = y(Math.max(k.o, k.c));
        const bot = y(Math.min(k.o, k.c));
        ctx.globalAlpha = up ? 0.42 : 0.5;
        ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1.5, bot - top));
      });
      ctx.globalAlpha = 1;
    };

    let raf = 0;
    const step = () => {
      offset += SPEED;
      if (offset >= STEP) {
        offset -= STEP;
        candles.shift();
        candles.push(nextCandle());
      }
      draw();
      raf = requestAnimationFrame(step);
    };

    build();
    if (reduce) draw();
    else raf = requestAnimationFrame(step);

    const ro = new ResizeObserver(() => {
      build();
      if (reduce) draw();
    });
    ro.observe(canvas);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      themeObs.disconnect();
    };
  }, []);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
}
