/**
 * 대시보드 레이아웃 — 템플릿 `layouts/dashboard.jsx` (T220).
 *
 * 사이드바(고정 · 모바일은 드로어) + 상단바 + 본문. Configurator 단추·Footer 는 들이지 않았다.
 */
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Who } from "../api";
import { ChatPanel } from "../chat/ChatPanel";
import { OPEN_SLOT } from "../chat/chat";
import { AnalysisCluster } from "./AnalysisCluster";
import { ChartField } from "./ChartField";
import { Navbar } from "./Navbar";
import { Sidenav } from "./Sidenav";
import { useHealth } from "./useHealth";

export function Layout({
  who,
  onOut,
  children,
}: {
  who: Who | null;
  onOut: () => void;
  children: ReactNode;
}) {
  const [drawer, setDrawer] = useState(false);
  // ⭐ AI 채팅 패널(T248) — 라우트 밖이라 화면을 옮겨도 대화가 안 끊긴다. 우측 아래 동그란 단추 · 열림은 브라우저가 기억한다 (2026-09-10 사용자 요구).
  const [chat, setChat] = useState(() => {
    try {
      return localStorage.getItem(OPEN_SLOT) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(OPEN_SLOT, chat ? "1" : "0");
    } catch {
      // 기억만 못 한다.
    }
  }, [chat]);

  return (
    <div className="min-h-screen bg-blue-gray-50/50 dark:bg-gray-950">
      <Sidenav who={who} open={drawer} onClose={() => setDrawer(false)} />
      {drawer ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/30 xl:hidden"
          aria-label="메뉴 닫기"
          onClick={() => setDrawer(false)}
        />
      ) : null}
      <ChatPanel who={who} open={chat} onToggle={() => setChat((was) => !was)} />
      <div className="p-4 xl:ml-80">
        <Navbar who={who} onOut={onOut} onMenu={() => setDrawer(true)} />
        {/* ⚠️ min-w-0 + overflow-x-clip: 넓은 표·pre 는 자기 상자(.table-wrap) 안에서 스크롤한다 — 화면 전체가
            가로로 밀리면 안 된다 (모바일 실측 2026-09-05: 옛 카드가 화면 밖으로 넘쳤다). */}
        <main className="mt-4 min-w-0 overflow-x-clip">{children}</main>
      </div>
    </div>
  );
}

/**
 * 로그인 전 — **심볼 · 한 줄 · 문** 만 (사용자 2026-09-05: "문구는 모두 없애줘 · 배경에 천천히 움직이는 차트").
 *
 * 참고: Binance·Gate 첫 화면처럼 큰 굵은 제목 하나가 화면을 채운다. 배경은 캔들 차트가 천천히 흐르고(`ChartField`),
 * 그 위를 바탕색으로 살짝 덮어 글이 읽히게 한다. 나머지 글은 전부 뺐다 — 남은 문장은 이 서비스가 하는 일 한 줄이다.
 */
/** 서버가 살아 있나 — 로그인 전에도 보이는 맥박 점. */
function Heartbeat() {
  const health = useHealth();
  const ok = !health.failing && health.lastOk !== null;
  return (
    <span className="inline-flex items-center gap-2 text-xs text-blue-gray-500" role="status">
      <span
        className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "animate-pulse-dot bg-gain shadow-[0_0_8px] shadow-gain/70" : "bg-loss"}`}
        aria-hidden="true"
      />
      {ok ? "자동 매매 동작 중 — 이 화면을 꺼도 계속된다" : health.lastOk === null ? "자동 매매 응답 기다리는 중" : `자동 매매 응답 없음 · ${health.ageS}초 전 마지막`}
    </span>
  );
}

export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    // ⭐ 이 화면은 **늘 밝다** — 본문(콘솔·리포트)의 흰 카드 톤과 같게. 다크 모드 토글은 로그인 뒤 화면에만 적용된다
    //    (사용자 2026-09-05: "너무 어두워, 내 앱 디자인에 맞춰").
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#f4f6f8] text-blue-gray-900" data-theme="light">
      <ChartField className="pointer-events-none absolute inset-0 h-full w-full opacity-70" />
      <div
        className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,rgba(244,246,248,0.96)_0%,rgba(244,246,248,0.85)_45%,rgba(244,246,248,0.55)_100%)]"
        aria-hidden="true"
      />

      <div className="relative mx-auto grid w-full max-w-7xl flex-1 items-center gap-10 px-8 py-14 lg:grid-cols-[1fr_1.15fr] lg:gap-12">
        {/* 왼쪽 — 브랜드 줄(심볼 + 이름) · 한 줄 · 약속 세 줄 · 문 · 서버 맥박. 앞 판(약속 세 줄)과 뒤 판(큰 심볼)을 섞었다
            (사용자 2026-09-05: "로고 너무 크다 · 가운데가 비어 보인다 · 이전 것과 적절하게 섞자"). */}
        <section className="flex max-w-xl flex-col items-start gap-7">
          <div className="flex items-center gap-5 animate-fade-up">
            <img src="/brand/up_and_down_logo.png" alt="" className="h-24 w-24 md:h-28 md:w-28" />
            <div>
              <div className="text-3xl font-bold tracking-tight text-blue-gray-900">업 앤 다운</div>
              <div className="text-xs font-medium tracking-[0.18em] text-blue-gray-400">UP &amp; DOWN INVEST</div>
            </div>
          </div>
          <h1
            className="m-0 text-3xl font-extrabold leading-tight tracking-tight text-blue-gray-900 animate-fade-up md:text-4xl"
            style={{ animationDelay: "90ms" }}
          >
            위험을 <span className="text-loss">내리고</span>, 수익은 <span className="text-gain">올리는</span>
            <br />
            자동 트레이딩 애플리케이션
          </h1>
          {/* 부제 한 줄 (사용자 문구 2026-09-05) — 약속 세 줄은 뺐다. */}
          <p className="m-0 text-base text-blue-gray-600 animate-fade-up md:text-lg" style={{ animationDelay: "200ms" }}>
            나만의 펀드를 운용하세요. 자면서도 돈이 벌리게 하세요.
          </p>
          <div className="flex w-full max-w-sm flex-col gap-3 animate-fade-up" style={{ animationDelay: "320ms" }}>
            {children}
            <Heartbeat />
          </div>
        </section>

        {/* 오른쪽 — 분석 패널 묶음이 천천히 재생된다. 좁은 화면(브라우저 확대 포함)에서는 아래로 내려온다 — 숨기지 않는다
            (2026-09-05: 사용자 화면에 패널이 안 보였다 · lg 미만 hidden 이 원인일 수 있어 뺐다). */}
        <section className="w-full justify-self-center lg:justify-self-end animate-fade-up" style={{ animationDelay: "260ms" }}>
          <AnalysisCluster />
        </section>
      </div>
    </div>
  );
}
