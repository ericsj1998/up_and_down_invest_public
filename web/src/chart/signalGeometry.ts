/**
 * 신호 때 탐지기가 본 선 → 차트 선분 (순수 · `Chart` 가 그린다 · 시험이 부른다 · T378 · 2026-10-05).
 *
 * 사용자 2026-10-05: *"화면에 탐지기가 신호 때 본 선을 그대로 그리기 — 이거는 해줬으면 좋겠고."*
 * 서버(`orchestration/inspection/signal_geometry.py`)가 탐지기의 같은 함수로 잰 끝점을 준다 — 여기서는
 * **다시 계산하지 않고** 차트 시간 축에 올리기만 한다(절대 규칙 #9).
 */

import type { SignalGeometry } from "../chartTypes";

/** 선분 하나 — 시리즈 하나가 된다(LWC 에 "선분" 개념이 없다). */
export type SignalLineSpec = {
  key: string;
  kind: SignalGeometry["kind"];
  role: "upper" | "lower";
  /** 두 점 — 시각은 초(UTC). */
  points: { time: number; value: number }[];
  colorVar: string;
  fallback: string;
  /** 열린 매매는 굵게(2) · 닫힌 매매는 얇게(1). */
  width: 1 | 2;
  /** 다시 잰 사건이 신호와 다르면 점선 — 조용히 실선으로 그리지 않는다. */
  dashed: boolean;
};

const COLORS: Record<SignalGeometry["kind"], { colorVar: string; fallback: string }> = {
  channel: { colorVar: "--signal-channel", fallback: "#3b6fd6" },
  triangle: { colorVar: "--signal-triangle", fallback: "#8e44ad" },
};

function seconds(ts: string): number {
  return Math.floor(Date.parse(ts) / 1000);
}

/**
 * 선 끝점들 → 이 차트에 그릴 선분들 (순수).
 *
 * 🔴 **탐지기 판정 축보다 긴 봉의 차트에는 안 그린다** — 4h 삼각을 일봉 차트에 올리면 끝점이 봉 시각에
 * 안 맞아 시간 축에 없는 칸이 생긴다. 판정 축 간격이 차트 간격의 배수일 때만(일봉 채널은 일봉 · 4h · 1h …).
 * ⚠️ 차트에 있는 봉 범위로 자른다(끝점 사이 직선 보간) — 범위 밖 시각을 넣으면 시간 축이 늘어난다.
 *
 * @param items 서버가 준 선들.
 * @param chartFrame 이 차트의 시간축(`1h` …).
 * @param firstTs 차트 첫 봉 시각(초).
 * @param lastTs 차트 마지막 봉 시각(초).
 * @param secondsOf 시간축 → 초(`ui.frameSeconds` — 공용 변환기를 받는다).
 * @returns 그릴 선분들.
 */
export function signalLineSpecs(
  items: SignalGeometry[] | undefined,
  chartFrame: string,
  firstTs: number,
  lastTs: number,
  secondsOf: (frame: string) => number,
): SignalLineSpec[] {
  if (!items || items.length === 0 || !(lastTs > firstTs)) return [];
  const chartS = secondsOf(chartFrame);
  const out: SignalLineSpec[] = [];
  for (const item of items) {
    const geomS = secondsOf(item.frame);
    if (!(chartS > 0) || !(geomS > 0) || geomS % chartS !== 0) continue;
    for (const line of item.lines) {
      const a = seconds(line.t1);
      const b = seconds(line.t2);
      if (!(b > a) || b < firstTs || a > lastTs) continue;
      const at = (t: number): number => line.p1 + ((line.p2 - line.p1) * (t - a)) / (b - a);
      const s = Math.max(a, firstTs);
      const e = Math.min(b, lastTs);
      if (!(e > s)) continue;
      out.push({
        key: `${item.trade_id}:${line.role}`,
        kind: item.kind,
        role: line.role,
        points: [
          { time: s, value: at(s) },
          { time: e, value: at(e) },
        ],
        colorVar: COLORS[item.kind].colorVar,
        fallback: COLORS[item.kind].fallback,
        width: item.open ? 2 : 1,
        dashed: !item.matched,
      });
    }
  }
  return out;
}

/**
 * 차트 아래 한 줄 설명 — 어떤 매매의 어떤 선인가 · 다시 잰 값이 다르면 그렇다고 적는다 (순수).
 *
 * @param item 서버가 준 선.
 * @returns 사람 문장.
 */
export function signalNote(item: SignalGeometry): string {
  const head = item.kind === "channel" ? "일봉 채널 신호" : "삼각수렴 신호";
  const when = item.signal_ts.slice(0, 16).replace("T", " ");
  const state = item.open ? "보유 중" : "닫힘";
  const warn = item.matched ? "" : " · ⚠️ 지금 다시 잰 사건이 신호와 다름(창 앞머리 차이)";
  return `${head} ${when} UTC 봉 · ${state} · ${item.note}${warn}`;
}
