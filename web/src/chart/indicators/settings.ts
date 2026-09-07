/**
 * 차트 설정 — 어떤 지표를 겹치고 매매의 무엇을 표기하나. **모든 차트가 같은 설정을 본다** (사용자 요구 2026-09-06).
 *
 * 브라우저(localStorage)에 남는다 — 사람의 취향이고 서버 상태가 아니다. 읽기·쓰기는 전부 try/catch 다:
 * 사생활 창·차단된 저장소에서는 접근 자체가 던진다. 못 읽으면 기본값으로 그린다.
 *
 * 컴포넌트끼리는 `window` 이벤트로 맞춘다 — 근거 화면의 패널에서 바꾸면 콘솔 차트도 바뀐다.
 */
import { useCallback, useEffect, useState } from "react";
import { DEFAULT_SPECS, type IndicatorSpec } from "./registry";

/** 매매 표기 — 사용자가 "무조건 표기" 로 못 박은 다섯 가지와, 영역 칠하기. */
export interface MarkSettings {
  /** 진입 타점(시각·가격·롱/숏) 마커. */
  entry: boolean;
  /** 청산/익절 마커 (사유 · 가격). */
  exit: boolean;
  /** 손절선 · 진입선 · 청산선 가로선 (선택한 매매). */
  lines: boolean;
  /** 손절 구간 붉은 영역 · 익절 구간 초록 영역 (선택한 매매). */
  zones: boolean;
  /** 마커 옆 글자 (가격 · 사유). 끄면 점만 남는다. */
  labels: boolean;
}

export interface ChartSettings {
  indicators: IndicatorSpec[];
  marks: MarkSettings;
}

export const DEFAULT_SETTINGS: ChartSettings = {
  indicators: DEFAULT_SPECS,
  marks: { entry: true, exit: true, lines: true, zones: true, labels: true },
};

const KEY = "updown.chart.settings.v1";
const EVENT = "updown:chart-settings";

function isSpec(v: unknown): v is IndicatorSpec {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  return typeof o["id"] === "string" && typeof o["type"] === "string" && typeof o["length"] === "number";
}

/** 저장된 값을 읽는다 — 모양이 어긋나면 그 부분만 기본값으로. */
export function loadSettings(): ChartSettings {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const got = JSON.parse(raw) as Partial<ChartSettings>;
    const indicators = Array.isArray(got.indicators) ? got.indicators.filter(isSpec) : DEFAULT_SETTINGS.indicators;
    const marks = { ...DEFAULT_SETTINGS.marks, ...(typeof got.marks === "object" && got.marks ? got.marks : {}) };
    return { indicators, marks };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(next: ChartSettings): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // 저장소가 없어도 이 세션 안에서는 이벤트로 맞춘다.
  }
  try {
    window.dispatchEvent(new CustomEvent<ChartSettings>(EVENT, { detail: next }));
  } catch {
    // 이벤트를 못 보내면 각 차트는 다음 마운트에 읽는다.
  }
}

/** 설정과 갱신 함수 — 어느 컴포넌트가 바꿔도 전부 같이 바뀐다. */
export function useChartSettings(): [ChartSettings, (patch: Partial<ChartSettings>) => void, () => void] {
  const [settings, setSettings] = useState<ChartSettings>(() => loadSettings());
  useEffect(() => {
    const onChange = (e: Event) => {
      const detail = (e as CustomEvent<ChartSettings>).detail;
      if (detail) setSettings(detail);
    };
    window.addEventListener(EVENT, onChange);
    return () => window.removeEventListener(EVENT, onChange);
  }, []);
  const update = useCallback((patch: Partial<ChartSettings>) => {
    const next = { ...loadSettings(), ...patch };
    setSettings(next);
    saveSettings(next);
  }, []);
  const reset = useCallback(() => {
    setSettings(DEFAULT_SETTINGS);
    saveSettings(DEFAULT_SETTINGS);
  }, []);
  return [settings, update, reset];
}
