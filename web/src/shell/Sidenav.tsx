/**
 * 왼쪽 사이드바 — 템플릿(material-tailwind-dashboard-react `widgets/layout/sidenav.jsx`)을 TSX 로 옮겼다 (T220).
 *
 * 바뀐 것: 브랜드는 우리 심볼 · 항목은 `nav.ts` 의 표 · **열린 판** 절이 붙는다(옛 탭 리본의 역할) ·
 * Configurator(색 고르기)는 들이지 않았다.
 *
 * ⭐ `NavLink` 는 `<a href>` 다 — 가운데 클릭·새 탭·주소 복사가 그냥 된다 (옛 화면 규칙 유지).
 */
import { PresentationChartLineIcon, XMarkIcon } from "@heroicons/react/24/solid";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { modeCookie, noRealAccount, switchMode, type Who } from "../api";
import { GroupIcon } from "./BrokerMark";
import { GROUP_LABEL, useMarketGroup, type MarketGroup } from "./marketGroup";

/**
 * Demo Trading 스위치 (T221) — 거래소 사이트의 "Demo Trading" 처럼 한 번에 전환한다.
 *
 * 켜면 모든 요청이 데모 API(테스트넷 · 별도 DB)로 간다. 게스트는 데모 **고정**(스위치 잠김) — 실계좌 서버가
 * 게스트 쪽지를 받지 않으므로 잠금은 편의이고 방어는 서버에 있다. 서버가 답한 `mode` 가 진실이고, 쿠키는 행선지다.
 */
function DemoSwitch({ who }: { who: Who | null }) {
  // 🔴 **쿠키가 행선지다.** `who.mode` 는 답한 API 의 환경(paper·dev = demo)이라, 로컬 실계좌 API(APP_ENV=dev) 도
  //    "demo" 라고 답한다 — 그걸 보면 스위치가 늘 데모로 그려져 눌러도 안 바뀌는 것처럼 보인다 (사용자 2026-09-06).
  const demo = modeCookie() === "demo";
  const locked = Boolean(who?.guest);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={demo}
      disabled={locked}
      title={
        locked
          ? "게스트는 Demo Trading 만 볼 수 있다 — 실계좌는 구글로 가입해 승인받은 사람만"
          : demo
            ? "지금 테스트넷(페이크머니)을 보고 있다 — 누르면 실계좌로"
            : noRealAccount(who)
              ? "이 환경(로컬)의 실계좌 모드에는 거래소가 없다 — 테스트넷 API 키를 넣거나 Demo Trading 에서 본다"
              : "지금 실계좌(진짜 돈)를 보고 있다 — 누르면 Demo Trading 으로"
      }
      onClick={() => switchMode(demo ? "live" : "demo")}
      // 모던·미니멀 (사용자 2026-09-05) + 안쪽을 회색으로 채움 (사용자 2026-09-06). 상태는 점 색과 글자로만 — 데모는
      // 회색 점, 실계좌는 빨간 점(진짜 돈이라는 신호 하나만 남긴다).
      className={`flex w-full items-center justify-between rounded-lg border border-blue-gray-200 bg-blue-gray-50 px-3 py-2 text-left text-xs font-semibold text-blue-gray-700 transition-[border-color,box-shadow,background-color] duration-150 hover:border-blue-gray-300 hover:bg-blue-gray-100 hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-80 dark:border-gray-700 dark:bg-gray-800 dark:text-blue-gray-200 dark:hover:bg-gray-700`}
    >
      <span className="flex min-w-0 items-center gap-2">
        <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${demo ? "bg-blue-gray-400" : "bg-loss"}`} aria-hidden="true" />
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate">{demo ? "DEMO TRADING" : "LIVE"}</span>
          <span className="truncate text-[10px] font-medium opacity-70">
            {demo ? (locked ? "테스트넷 · 게스트 고정" : "테스트넷 · 페이크머니") : "실계좌 · 진짜 돈"}
          </span>
        </span>
      </span>
      {locked ? null : (
        <span className="shrink-0 text-[10px] font-medium opacity-70" aria-hidden="true">
          {demo ? "실계좌로 →" : "데모로 →"}
        </span>
      )}
    </button>
  );
}

/**
 * 시장 스위치 (T245) — 코인 | 주식. Demo 스위치 바로 아래, 같은 결. 새로고침 없이 바뀐다 — 같은 서버의 화면을
 * 다른 기준으로 거를 뿐이다. 어느 시장이 코인/주식인지는 서버가 말하고(`/exchange/markets`), 여기는 고르기만 한다.
 */
function MarketSwitch() {
  const [group, setGroup] = useMarketGroup();
  const seg = (which: MarketGroup) => {
    const on = group === which;
    return (
      <button
        key={which}
        type="button"
        role="radio"
        aria-checked={on}
        title={which === "coin" ? "코인 시장 — Gate·Binance 선물" : "주식 시장 — 토스 시세 · 페이퍼(가상 체결)"}
        onClick={() => setGroup(which)}
        className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors ${
          on
            ? "bg-white text-blue-gray-900 shadow-sm dark:bg-gray-700 dark:text-white"
            : "text-blue-gray-500 hover:text-blue-gray-800 dark:text-blue-gray-300 dark:hover:text-white"
        }`}
      >
        <GroupIcon group={which} className="h-4 w-4" />
        {GROUP_LABEL[which]}
      </button>
    );
  };
  return (
    <div
      role="radiogroup"
      aria-label="시장 전환"
      className="mt-2 flex rounded-lg border border-blue-gray-200 bg-blue-gray-50 p-1 dark:border-gray-700 dark:bg-gray-800"
    >
      {seg("coin")}
      {seg("stock")}
    </div>
  );
}
import { Typography } from "../mt";
import { LABELS_ON, PAGES } from "./nav";
import { useOpenRuns } from "./openRuns";
import { useHealth } from "./useHealth";

function ServerState() {
  const health = useHealth();
  const bad = health.failing || health.lastOk === null;
  const text = bad
    ? health.ageS === null
      ? "자동 매매 응답 없음"
      : `자동 매매 응답 없음 · ${health.ageS}초 전 마지막`
    : "자동 매매 동작 중";
  return (
    <div
      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
        bad
          ? "border-loss/40 bg-loss-wash text-loss"
          : "border-blue-gray-50 text-blue-gray-600 dark:border-gray-800 dark:text-blue-gray-300"
      }`}
      title={bad ? "API 가 /health 에 답하지 않는다 — 화면 숫자는 지금 값이 아니다" : "매매는 서버에서 돈다 — 이 화면을 꺼도 계속된다"}
      role="status"
    >
      <span className={`inline-block h-2 w-2 rounded-full ${bad ? "bg-loss" : "bg-gain"}`} aria-hidden="true" />
      {text}
    </div>
  );
}

export function Sidenav({
  who,
  open,
  onClose,
}: {
  who: Who | null;
  /** 모바일 드로어가 열려 있나 (xl 이상에서는 늘 보인다). */
  open: boolean;
  onClose: () => void;
}) {
  const runs = useOpenRuns();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const pages = PAGES.filter(
    (page) => (!page.admin || who?.may_admin) && (!page.dev || LABELS_ON),
  );

  const closeRun = (run: string) => {
    runs.close(run);
    // ⚠️ 보고 있던 판을 닫으면 갈 곳이 필요하다 — 콘솔로. 남은 판으로 자동 이동하지 않는다:
    //    안 고른 판이 떠 있으면 주소와 눈이 어긋난다.
    if (pathname === `/paper/${run}`) navigate("/console");
  };

  const item =
    "flex w-full items-center gap-3 rounded-lg px-4 py-2.5 text-sm font-medium capitalize transition-colors";
  const idle = "text-blue-gray-700 hover:bg-blue-gray-50 dark:text-blue-gray-200 dark:hover:bg-gray-800";
  const active = "bg-gray-900 text-white shadow-md dark:bg-blue-gray-100 dark:text-gray-900";

  return (
    <aside
      className={`${open ? "translate-x-0" : "-translate-x-80"} fixed inset-0 z-50 my-4 ml-4 h-[calc(100vh-32px)] w-72 overflow-y-auto rounded-xl border border-blue-gray-100 bg-white shadow-sm transition-transform duration-300 xl:translate-x-0 dark:border-gray-800 dark:bg-gray-900`}
      aria-label="화면 목록"
    >
      <div className="relative">
        <NavLink to="/console" className="flex items-center gap-3 px-6 pb-2 pt-6" onClick={onClose}>
          <img src="/brand/up_and_down_logo.png" alt="" className="h-8 w-8" />
          <Typography variant="h6" color="blue-gray" className="dark:text-white">
            업 앤 다운
          </Typography>
        </NavLink>
        {/* T221 Demo Trading — 로고 바로 아래, 늘 보이는 자리. 어느 돈을 보고 있는지가 이 화면의 전제다. */}
        <div className="px-6 pb-4">
          <DemoSwitch who={who} />
          {/* T245 시장 전환 — 어느 시장을 보는지가 어느 돈을 보는지 다음의 전제다. */}
          <MarketSwitch />
        </div>
        <button
          type="button"
          className="absolute right-2 top-2 grid h-8 w-8 place-items-center rounded-lg text-blue-gray-500 hover:bg-blue-gray-50 xl:hidden"
          aria-label="메뉴 닫기"
          onClick={onClose}
        >
          <XMarkIcon className="h-5 w-5" />
        </button>
      </div>

      <nav className="m-4">
        <ul className="mb-4 flex flex-col gap-1">
          {pages.map((page) => (
            <li key={page.to}>
              <NavLink
                to={page.to}
                className={({ isActive }) => `${item} ${isActive ? active : idle}`}
                onClick={onClose}
              >
                <page.icon className="h-5 w-5 text-inherit" />
                {page.name}
              </NavLink>
            </li>
          ))}
        </ul>

        {/* 🔴 **연 RUN 은 항목이다.** 고정 항목 하나면 RUN 을 바꿀 때마다 앞엣것을 잃는다. */}
        {runs.opened.length ? (
          <ul className="mb-4 flex flex-col gap-1">
            <li className="mx-3.5 mb-2 mt-4">
              <Typography
                variant="small"
                color="blue-gray"
                className="font-black uppercase opacity-75 dark:text-blue-gray-200"
              >
                열린 판
              </Typography>
            </li>
            {runs.opened.map((run) => (
              <li key={run} className="flex items-center gap-1">
                <NavLink
                  to={`/paper/${run}`}
                  className={({ isActive }) => `${item} min-w-0 ${isActive ? active : idle}`}
                  onClick={onClose}
                  title={run}
                >
                  <PresentationChartLineIcon className="h-5 w-5 shrink-0 text-inherit" />
                  <span className="truncate font-mono normal-case">{runs.label(run)}</span>
                </NavLink>
                <button
                  type="button"
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-blue-gray-400 hover:bg-blue-gray-50 hover:text-blue-gray-700 dark:hover:bg-gray-800"
                  aria-label={`${run} 닫기`}
                  title="목록에서만 닫는다 — RUN 은 계속 돈다"
                  onClick={() => closeRun(run)}
                >
                  <XMarkIcon className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </nav>

      {/* 🔴 매매는 서버에서 돈다 — 이 화면을 꺼도 계속된다. 글자가 아니라 **상태**다: /health 가 답하면 초록,
          못 받으면 빨강 + 마지막 응답 시각 (UX 점검 2026-09-05). */}
      <div className="absolute bottom-4 left-0 right-0 px-6">
        <ServerState />
      </div>
    </aside>
  );
}
