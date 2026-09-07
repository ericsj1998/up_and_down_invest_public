/**
 * 상단바 — 템플릿 `dashboard-navbar.jsx` 를 TSX 로 (T220).
 *
 * 들이지 않은 것: 검색 칸 · 알림 데모 · 설정 톱니. 대신 우리 것이 들어간다 — 재인증 타이머(`AuthTimer`) ·
 * 누구로 로그인했나(`WhoBar`) · 밝기 전환. 제목은 `nav.ts` 표 또는 열린 판 이름에서 온다.
 */
import { Bars3Icon, MoonIcon, SunIcon } from "@heroicons/react/24/solid";
import { useLocation } from "react-router-dom";
import { noRealAccount, type Who } from "../api";
import { AuthTimer, WhoBar } from "../Gate";
import { Typography } from "../mt";
import { pageTitle } from "./nav";
import { useOpenRuns } from "./openRuns";
import { useTheme } from "./theme";

export function Navbar({
  who,
  onOut,
  onMenu,
}: {
  who: Who | null;
  onOut: () => void;
  onMenu: () => void;
}) {
  const { pathname } = useLocation();
  const runs = useOpenRuns();
  const [theme, toggleTheme] = useTheme();

  const parts = pathname.split("/").filter(Boolean);
  const run = parts[0] === "paper" ? parts[1] : undefined;
  const title = run ? runs.label(run) : (pageTitle(pathname) ?? "없는 화면");

  return (
    <header className="sticky top-4 z-40 rounded-xl border border-blue-gray-100 bg-white/80 px-4 py-3 shadow-md shadow-blue-gray-500/5 backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/80">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="grid h-9 w-9 place-items-center rounded-lg text-blue-gray-500 hover:bg-blue-gray-50 xl:hidden dark:hover:bg-gray-800"
            aria-label="메뉴 열기"
            onClick={onMenu}
          >
            <Bars3Icon className="h-6 w-6" />
          </button>
          <div>
            {/* ⭐ 첫 줄은 **어느 돈이 도는가** — 콘솔의 전제다 (UX 점검 2026-09-05). 서버(/auth/me)가 말한 값이다. */}
            <Typography
              variant="small"
              className={`font-medium ${who?.real_money ? "text-loss" : "text-blue-gray-500 dark:text-blue-gray-300"}`}
            >
              {who?.real_money === undefined
                ? run
                  ? "판"
                  : "화면"
                : noRealAccount(who)
                  ? "실계좌 모드 · 이 환경(로컬)에는 실계좌가 없다"
                  : who.real_money
                  ? "실계좌 · 진짜 돈"
                  : who.guest
                    ? "Demo Trading · 테스트넷 · 게스트(읽기만)"
                    : "Demo Trading · 테스트넷 · 페이크머니"}
              {run ? " · 판" : ""}
            </Typography>
            <Typography variant="h6" color="blue-gray" className={run ? "font-mono dark:text-white" : "dark:text-white"}>
              {title}
            </Typography>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* 다크 모드 (T34). 새로고침해도 유지 — localStorage 가 기억한다. */}
          <button
            type="button"
            className="grid h-9 w-9 place-items-center rounded-lg text-blue-gray-500 hover:bg-blue-gray-50 dark:hover:bg-gray-800"
            title="밝기 전환"
            aria-label="밝기 전환"
            onClick={toggleTheme}
          >
            {theme === "dark" ? <SunIcon className="h-5 w-5" /> : <MoonIcon className="h-5 w-5" />}
          </button>
          {/* ⭐ 은행식 인증 타이머 — 재인증 기한과 [연장] (사용자 요청 2026-09-03). */}
          {who?.signed_in ? <AuthTimer who={who} /> : null}
          {/* ⭐ 누구로 로그인했고 무엇을 할 수 있나 — 늘 보인다. */}
          {who?.signed_in ? <WhoBar who={who} onOut={onOut} /> : null}
        </div>
      </div>
    </header>
  );
}
