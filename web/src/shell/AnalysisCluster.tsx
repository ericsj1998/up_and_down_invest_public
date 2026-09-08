/**
 * 로그인 오른쪽 — 분석 패널 묶음이 **천천히 재생**된다 (사용자 2026-09-05: "우측에 분석창 여러 개가 디자인처럼 천천히
 * 재생되는 느낌"). 자본 곡선(선) · 종목별 손익(막대) · 결과 분포(링) · 봉 띠 · 신호 점.
 *
 * ⚠️ 전부 **그림**이다 — 실제 값이 아니라서 숫자·축·단위를 적지 않는다(지어낸 숫자를 화면에 두지 않는다 · 규칙 #8).
 *    카드 모양은 본문(MT Card: 흰 바탕 · rounded-xl · 연한 테두리)과 같다.
 * ⭐ 움직임은 OS "동작 줄이기" 와 무관하게 돈다 — 제품의 얼굴이고 사용자가 움직임을 요구했다 (2026-09-05).
 */

const LINE = "M0 78 C 30 70, 50 40, 80 46 S 130 74, 160 52 S 210 18, 250 30 S 300 44, 340 14";
const AREA = `${LINE} L340 100 L0 100 Z`;

/** 막대 6개 — 기준선 위(초록)·아래(빨강). 값은 모양일 뿐이다. */
const BARS: { h: number; up: boolean }[] = [
  { h: 46, up: true },
  { h: 28, up: false },
  { h: 63, up: true },
  { h: 52, up: true },
  { h: 22, up: false },
  { h: 80, up: true },
];

/** 봉 띠 — 두 벌을 이어 흐른다. */
const CANDLES: { o: number; c: number; h: number; l: number }[] = [
  { o: 40, c: 52, h: 58, l: 36 }, { o: 52, c: 47, h: 56, l: 42 }, { o: 47, c: 61, h: 66, l: 45 },
  { o: 61, c: 58, h: 68, l: 54 }, { o: 58, c: 66, h: 72, l: 55 }, { o: 66, c: 60, h: 70, l: 56 },
  { o: 60, c: 49, h: 62, l: 46 }, { o: 49, c: 55, h: 60, l: 44 }, { o: 55, c: 64, h: 69, l: 52 },
  { o: 64, c: 70, h: 76, l: 61 }, { o: 70, c: 63, h: 74, l: 60 }, { o: 63, c: 71, h: 78, l: 62 },
];

function Panel({
  title,
  className = "",
  delay = 0,
  children,
}: {
  title: string;
  className?: string;
  delay?: number;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`animate-float-y rounded-xl border border-blue-gray-100 bg-white p-4 shadow-lg shadow-blue-gray-900/5 ${className}`}
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-blue-gray-400">{title}</div>
      {children}
    </div>
  );
}

function LinePanel() {
  return (
    <svg viewBox="0 0 340 100" className="h-28 w-full" aria-hidden="true">
      <defs>
        <linearGradient id="lp-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0f7b6c" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#0f7b6c" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[25, 50, 75].map((y) => (
        <line key={y} x1="0" x2="340" y1={y} y2={y} stroke="#eceff1" strokeWidth="1" />
      ))}
      <path d={AREA} fill="url(#lp-fill)" />
      <path
        d={LINE}
        fill="none"
        stroke="#0f7b6c"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeDasharray="1000"
        className="animate-draw-line"
      />
    </svg>
  );
}

function BarsPanel() {
  return (
    <div className="relative h-28">
      <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-blue-gray-200" />
      <div className="absolute inset-0 flex items-stretch justify-between gap-2 px-1">
        {BARS.map((b, i) => (
          <div key={i} className="relative flex-1">
            <span
              className={`absolute inset-x-0 block rounded-sm ${b.up ? "bottom-1/2 origin-bottom bg-gain/80" : "top-1/2 origin-top bg-loss/80"} animate-grow-y`}
              style={{ height: `${b.h / 2}%`, animationDelay: `${i * 160}ms` }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function RingPanel() {
  // r=26 → 둘레 163.4. dashoffset 163 → 44 로 채워진다 (약 73%). 값이 아니라 모양이다.
  return (
    <div className="flex items-center gap-4">
      <svg viewBox="0 0 64 64" className="h-24 w-24 -rotate-90" aria-hidden="true">
        <circle cx="32" cy="32" r="26" fill="none" stroke="#eceff1" strokeWidth="9" />
        <circle
          cx="32"
          cy="32"
          r="26"
          fill="none"
          stroke="#0f7b6c"
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray="163.4"
          className="animate-ring-fill"
        />
        <circle cx="32" cy="32" r="26" fill="none" stroke="#b4423a" strokeWidth="9" strokeDasharray="30 133.4" strokeDashoffset="-133.4" opacity="0.85" />
      </svg>
      <div className="flex flex-col gap-2">
        {[
          ["bg-gain", "w-24"],
          ["bg-loss", "w-10"],
          ["bg-blue-gray-200", "w-16"],
        ].map(([color, w], i) => (
          <div key={i} className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
            <span className={`h-2 rounded-full bg-blue-gray-50 ${w}`} />
          </div>
        ))}
      </div>
    </div>
  );
}

function CandlePanel() {
  const rows = [...CANDLES, ...CANDLES];
  return (
    <div className="relative h-24 overflow-hidden">
      <div className="absolute inset-y-0 left-0 flex w-max items-end gap-2 animate-slide-x">
        {rows.map((k, i) => {
          const up = k.c >= k.o;
          const top = 96 - Math.max(k.o, k.c);
          const bodyH = Math.max(2, Math.abs(k.c - k.o));
          return (
            <div key={i} className="relative h-24 w-3">
              <span
                className={`absolute left-1/2 w-px -translate-x-1/2 ${up ? "bg-gain" : "bg-loss"}`}
                style={{ top: `${96 - k.h}%`, height: `${k.h - k.l}%` }}
              />
              <span
                className={`absolute inset-x-0 rounded-[2px] ${up ? "bg-gain" : "bg-loss"}`}
                style={{ top: `${top}%`, height: `${bodyH}%` }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SignalPanel() {
  return (
    <div className="flex flex-col gap-3">
      {[
        ["bg-gain", "w-28"],
        ["bg-gain", "w-20"],
        ["bg-loss", "w-24"],
        ["bg-blue-gray-300", "w-16"],
      ].map(([color, w], i) => (
        <div key={i} className="flex items-center gap-3">
          <span
            className={`h-2.5 w-2.5 rounded-full ${color} animate-pulse-dot`}
            style={{ animationDelay: `${i * 350}ms` }}
          />
          <span className={`h-2 rounded-full bg-blue-gray-100 ${w}`} />
          <span className="ml-auto h-2 w-8 rounded-full bg-blue-gray-50" />
        </div>
      ))}
    </div>
  );
}

export function AnalysisCluster() {
  return (
    <div className="grid w-full max-w-2xl grid-cols-2 gap-4" aria-hidden="true">
      <Panel title="누적 손익" className="col-span-2" delay={0}>
        <LinePanel />
      </Panel>
      <Panel title="종목별 손익" delay={900}>
        <BarsPanel />
      </Panel>
      <Panel title="익절 · 손절 비율" delay={1800}>
        <RingPanel />
      </Panel>
      <Panel title="실시간 봉" delay={2700}>
        <CandlePanel />
      </Panel>
      <Panel title="진입 신호" delay={3600}>
        <SignalPanel />
      </Panel>
    </div>
  );
}
