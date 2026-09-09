/**
 * 화면 목록 — 사이드바와 상단바가 **같은 표**를 본다 (T220).
 *
 * ⚠️ **`to` 는 주소다** — 바꾸면 저장해 둔 링크가 깨진다 (옛 탭 id 와 같다: console · report · accounts · paper · label).
 *    이름만 바꾼다.
 */
import type { ComponentType, SVGProps } from "react";
import { BanknotesIcon, BeakerIcon, ChartBarIcon, CpuChipIcon, SparklesIcon, TagIcon, UsersIcon } from "@heroicons/react/24/solid";

export type Icon = ComponentType<SVGProps<SVGSVGElement>>;

export interface Page {
  to: string;
  name: string;
  icon: Icon;
  /** 관리자에게만 보인다. ⚠️ 숨김은 방어가 아니다 — 서버가 `/auth/users` 를 관리자만 통과시킨다. */
  admin?: boolean;
  /** dev 빌드(`VITE_LABELS=1`)에서만 보인다. */
  dev?: boolean;
}

// 🔬 라벨(임시) 화면은 배포에서 뺀다 (사용자 2026-09-04 · T214). Vite 가 빌드 시 리터럴로 박는다.
export const LABELS_ON = import.meta.env.VITE_LABELS === "1";

export const PAGES: readonly Page[] = [
  // 🔴 **콘솔이 홈이다** (사용자 확정 2026-08-19). 거래소가 말하는 사실이 첫 화면이다.
  { to: "/console", name: "거래 콘솔", icon: BanknotesIcon },
  // AI 투자 어시스턴트 (T247) — 첫 접속 온보딩 · 이어 하기 · 펀드 기본값 편집. 처음 온 사람은 `/` 가 여기로 보낸다.
  { to: "/assistant", name: "AI 투자 어시스턴트", icon: SparklesIcon },
  { to: "/report", name: "리포트", icon: ChartBarIcon },
  // 백테스트 리포트 (T222) — 어떤 데이터·전략·결과로 검증했나. 주소는 /evidence 그대로(링크 보존) · 이름만 바꿈 (사용자 2026-09-06).
  { to: "/evidence", name: "백테스트 리포트", icon: BeakerIcon },
  // AI 퍼포먼스 리포트 (T249) — 참가자(모델 x 프롬프트 x 스냅샷)별 페이퍼 실측. n<30 은 회색.
  { to: "/ai-report", name: "AI 리포트", icon: CpuChipIcon },
  { to: "/accounts", name: "관리", icon: UsersIcon, admin: true },
  { to: "/label", name: "라벨(임시)", icon: TagIcon, dev: true },
];

/** 주소의 첫 칸으로 화면 이름을 찾는다. 판 주소면 null — 판 이름은 열린 판 목록이 안다. */
export function pageTitle(pathname: string): string | null {
  const head = `/${pathname.split("/").filter(Boolean)[0] ?? ""}`;
  return PAGES.find((page) => page.to === head)?.name ?? null;
}
