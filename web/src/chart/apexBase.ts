/**
 * ApexCharts 공통 옵션 — 근거 화면과 리포트 대시보드가 **같은 밝기 · 글꼴 · 툴바 없음**을 쓴다.
 *
 * 복제 정리(2026-09-06): 두 파일이 같은 함수를 각자 들고 있었다. 다크 모드 판정은 문서 루트의
 * `data-theme` 하나만 본다 — 차트가 화면과 다른 밝기로 뜨면 사람은 고장으로 읽는다.
 */

import type { ApexOptions } from "apexcharts";

export function apexBaseOptions(): ApexOptions {
  const dark = document.documentElement.dataset.theme === "dark";
  return {
    chart: { toolbar: { show: false }, zoom: { enabled: false }, background: "transparent", fontFamily: "inherit" },
    theme: { mode: dark ? "dark" : "light" },
    grid: { borderColor: dark ? "#374151" : "#eceff1", strokeDashArray: 4 },
    dataLabels: { enabled: false },
    tooltip: { theme: dark ? "dark" : "light" },
  };
}
