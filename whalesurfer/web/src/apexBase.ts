import type { ApexOptions } from "apexcharts";

/** ApexCharts 공통 — 툴바 없음 · 배경 투명 · 다크 모드는 `<html data-theme>` 를 따른다. */
export function apexBaseOptions(): ApexOptions {
  const dark = document.documentElement.dataset.theme === "dark";
  return {
    chart: { toolbar: { show: false }, zoom: { enabled: false }, background: "transparent", fontFamily: "inherit" },
    theme: { mode: dark ? "dark" : "light" },
    dataLabels: { enabled: false },
    tooltip: { theme: dark ? "dark" : "light" },
  };
}
