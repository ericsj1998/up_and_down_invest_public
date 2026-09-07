/**
 * Material Tailwind 계열 화면(근거 · 리포트 대시보드 · 백테스트 상세)이 **같이 쓰는** 작은 조각들.
 *
 * 🔴 복제 정리(2026-09-06 · 사용자 *"같은 디자인 카드를 동적으로 공유 안 하는 게 말이 되나"*):
 *    `Fact` 가 네 파일에, `ChartCard` 가 두 파일에 각자 있었고 미묘하게 달랐다(툴팁 유무 · 줄바꿈 ·
 *    부호 색). 한 벌로 모으고 차이는 prop 으로 받는다. 클래식 CSS 화면의 `ui.tsx` `Fact`(이름·값·메모)와는
 *    다른 물건이라 이름은 같아도 모듈이 다르다.
 */

import type { ReactNode } from "react";
import { Card, CardBody, Typography } from "./mt";

/**
 * 숫자 한 칸 — 라벨 위, 값 아래.
 *
 * @param tone 부호로 색을 정한다 — 양수 gain · 음수 loss · 0/undefined 는 기본색. `className` 을 주면 그것이 이긴다.
 * @param hint 마우스를 올리면 보이는 설명 (`title`).
 */
export function Fact({
  label,
  value,
  tone,
  hint,
  className = "",
}: {
  label: string;
  value: string;
  tone?: number;
  hint?: string;
  className?: string;
}) {
  const color =
    className ||
    (tone === undefined || tone === 0 ? "text-blue-gray-900 dark:text-white" : tone > 0 ? "text-gain" : "text-loss");
  return (
    <div className="rounded-xl border border-blue-gray-100 bg-white p-3 dark:border-gray-800 dark:bg-gray-900" title={hint}>
      <div className="text-xs text-blue-gray-500 dark:text-blue-gray-300">{label}</div>
      <div className={`mt-1 break-words font-mono text-base font-semibold ${color}`}>{value}</div>
    </div>
  );
}

/**
 * 감사 권한이 없어 가려진 자리 — **무엇이** 가려졌는지 말한다 (규칙 #8: 빈 칸을 0 으로 꾸미지 않는다).
 *
 * 사용자 2026-09-06: 게스트·열람자는 차트 · 지표 · 매매법 · 매매 표기까지 보고, 최종 손익 · 연차별 손익 ·
 * 자본 곡선은 감사 권한(`accounts.audit` 또는 관리자)이 있어야 본다. 서버가 값을 `null` 로 보내고
 * (`redact_pnl`), 화면은 그 자리에 이것을 그린다.
 */
export function AuditNotice({ what }: { what: string }) {
  return (
    <div
      className="rounded-lg border border-dashed border-blue-gray-200 bg-blue-gray-50/60 px-3 py-2 text-xs text-blue-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-blue-gray-300"
      role="note"
    >
      🔒 <b>{what}</b> 은(는) <b>감사 권한</b>이 있어야 보인다 — 차트 · 지표 · 매매법 · 매매 표기는 그대로 본다. 관리자가 계정 화면에서 준다.
    </div>
  );
}

/** 제목 달린 차트 카드 — 안에 무엇을 그리든 테두리·여백·제목 글꼴은 같다. */
export function ChartCard({ title, className = "", children }: { title: string; className?: string; children: ReactNode }) {
  return (
    <Card className={`border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900 ${className}`}>
      <CardBody className="p-5">
        <Typography variant="h6" color="blue-gray" className="mb-2 dark:text-white">
          {title}
        </Typography>
        {children}
      </CardBody>
    </Card>
  );
}
