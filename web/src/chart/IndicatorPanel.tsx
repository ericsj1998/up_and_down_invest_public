/**
 * 차트 설정 패널 — 지표(이평 · 볼린저) 켜고 끄고 더하기 · 매매 표기 켜고 끄기. **모든 차트가 같은 설정을 본다.**
 *
 * 값은 `useChartSettings` 가 든다(브라우저 저장). 이 컴포넌트는 그리기와 입력만 한다 — 계산은 없다.
 */
import { useState } from "react";
import {
  INDICATOR_PALETTE,
  TYPES,
  describe,
  specId,
  useChartSettings,
  type IndicatorSpec,
  type IndicatorType,
  type MarkSettings,
} from "./indicators";

const MARK_ROWS: { key: keyof MarkSettings; label: string; hint: string }[] = [
  { key: "entry", label: "진입 타점", hint: "롱 ↑(봉 아래) · 숏 ↓(봉 위) · 진입가" },
  { key: "exit", label: "청산·익절 위치", hint: "사유(손절·익절·강제청산…) · 청산가. 강제청산은 네모" },
  { key: "lines", label: "손절선 · 진입선 · 청산선", hint: "선택한 매매의 가로선 (가격 축 라벨 포함)" },
  { key: "zones", label: "손절 영역(붉음) · 익절 영역(초록)", hint: "선택한 매매의 시간 구간에만 칠한다. 손절선을 지나 끝났으면 그 구간을 짙게" },
  { key: "labels", label: "마커 글자", hint: "끄면 점·화살표만 남는다 (매매가 많을 때)" },
];

const PRESETS: { label: string; type: IndicatorType; length: number; k?: number }[] = [
  { label: "BB 20/2", type: "bb", length: 20, k: 2 },
  { label: "BB 4/4", type: "bb", length: 4, k: 4 },
  { label: "SMA 20", type: "sma", length: 20 },
  { label: "SMA 50", type: "sma", length: 50 },
  { label: "SMA 200", type: "sma", length: 200 },
  { label: "EMA 9", type: "ema", length: 9 },
];

export function IndicatorPanel({ compact = false }: { compact?: boolean }) {
  const [settings, update, reset] = useChartSettings();
  const [open, setOpen] = useState(!compact);
  const [type, setType] = useState<IndicatorType>("sma");
  const [length, setLength] = useState(20);
  const [k, setK] = useState(2);

  const specs = settings.indicators;
  const setSpecs = (next: IndicatorSpec[]) => update({ indicators: next });

  const upsert = (t: IndicatorType, len: number, kk?: number) => {
    if (!(len > 0)) return;
    const id = specId(t, len, kk);
    const exists = specs.find((s) => s.id === id);
    if (exists) {
      setSpecs(specs.map((s) => (s.id === id ? { ...s, enabled: true } : s)));
      return;
    }
    const color = INDICATOR_PALETTE[specs.length % INDICATOR_PALETTE.length] ?? "#8957e5";
    setSpecs([...specs, { id, type: t, length: len, ...(t === "bb" ? { k: kk ?? 2 } : {}), enabled: true, color }]);
  };

  const enabledCount = specs.filter((s) => s.enabled).length;
  return (
    <div className="rounded-xl border border-blue-gray-100 bg-white text-xs dark:border-gray-800 dark:bg-gray-900">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="font-semibold text-blue-gray-800 dark:text-blue-gray-100">
          차트 설정 <span className="font-normal text-blue-gray-500">· 지표 {enabledCount}개 켜짐 · 모든 차트에 적용</span>
        </span>
        <span aria-hidden="true" className="text-blue-gray-400">
          {open ? "▴" : "▾"}
        </span>
      </button>
      {open ? (
        <div className="grid gap-4 border-t border-blue-gray-50 p-3 md:grid-cols-2 dark:border-gray-800">
          <section>
            <div className="mb-1.5 font-semibold text-blue-gray-700 dark:text-blue-gray-200">겹치는 지표</div>
            <ul className="flex flex-col gap-1">
              {specs.map((s) => (
                <li key={s.id} className="flex items-center gap-2">
                  <input
                    id={`ind-${s.id}`}
                    type="checkbox"
                    checked={s.enabled}
                    onChange={(e) => setSpecs(specs.map((x) => (x.id === s.id ? { ...x, enabled: e.target.checked } : x)))}
                  />
                  <span className="inline-block h-0.5 w-4" style={{ background: s.color }} aria-hidden="true" />
                  <label htmlFor={`ind-${s.id}`} className="flex-1 cursor-pointer text-blue-gray-800 dark:text-blue-gray-100">
                    {describe(s)} <span className="text-blue-gray-400">· {TYPES[s.type]?.label ?? s.type}</span>
                  </label>
                  <input
                    type="color"
                    value={s.color}
                    aria-label={`${describe(s)} 색`}
                    className="h-5 w-6 cursor-pointer border-0 bg-transparent p-0"
                    onChange={(e) => setSpecs(specs.map((x) => (x.id === s.id ? { ...x, color: e.target.value } : x)))}
                  />
                  <button
                    type="button"
                    className="text-blue-gray-400 hover:text-loss"
                    aria-label={`${describe(s)} 지우기`}
                    onClick={() => setSpecs(specs.filter((x) => x.id !== s.id))}
                  >
                    ✕
                  </button>
                </li>
              ))}
              {specs.length === 0 ? <li className="text-blue-gray-400">지표 없음</li> : null}
            </ul>
            <div className="mt-2 flex flex-wrap gap-1">
              {PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  className="rounded-md border border-blue-gray-200 px-2 py-0.5 text-[11px] text-blue-gray-700 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-200 dark:hover:bg-gray-800"
                  onClick={() => upsert(p.type, p.length, p.k)}
                >
                  + {p.label}
                </button>
              ))}
            </div>
            <form
              className="mt-2 flex flex-wrap items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                upsert(type, length, type === "bb" ? k : undefined);
              }}
            >
              <select
                value={type}
                onChange={(e) => setType(e.target.value as IndicatorType)}
                className="rounded-md border border-blue-gray-200 bg-white px-1.5 py-0.5 dark:border-gray-700 dark:bg-gray-800"
                aria-label="지표 종류"
              >
                {(Object.keys(TYPES) as IndicatorType[]).map((tp) => (
                  <option key={tp} value={tp}>
                    {TYPES[tp].label}
                  </option>
                ))}
              </select>
              <label className="inline-flex items-center gap-1">
                길이
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={length}
                  onChange={(e) => setLength(Number(e.target.value))}
                  className="w-16 rounded-md border border-blue-gray-200 bg-white px-1.5 py-0.5 font-mono dark:border-gray-700 dark:bg-gray-800"
                />
              </label>
              {TYPES[type].hasK ? (
                <label className="inline-flex items-center gap-1">
                  k
                  <input
                    type="number"
                    min={0.1}
                    max={10}
                    step={0.1}
                    value={k}
                    onChange={(e) => setK(Number(e.target.value))}
                    className="w-14 rounded-md border border-blue-gray-200 bg-white px-1.5 py-0.5 font-mono dark:border-gray-700 dark:bg-gray-800"
                  />
                </label>
              ) : null}
              <button
                type="submit"
                className="rounded-md bg-gray-900 px-2 py-0.5 text-[11px] font-medium text-white dark:bg-blue-gray-100 dark:text-gray-900"
              >
                추가
              </button>
            </form>
          </section>
          <section>
            <div className="mb-1.5 font-semibold text-blue-gray-700 dark:text-blue-gray-200">매매 표기</div>
            <ul className="flex flex-col gap-1">
              {MARK_ROWS.map((row) => (
                <li key={row.key} className="flex items-start gap-2">
                  <input
                    id={`mark-${row.key}`}
                    type="checkbox"
                    className="mt-0.5"
                    checked={settings.marks[row.key]}
                    onChange={(e) => update({ marks: { ...settings.marks, [row.key]: e.target.checked } })}
                  />
                  <label htmlFor={`mark-${row.key}`} className="cursor-pointer">
                    <span className="text-blue-gray-800 dark:text-blue-gray-100">{row.label}</span>
                    <span className="block text-[11px] text-blue-gray-400">{row.hint}</span>
                  </label>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="mt-3 rounded-md border border-blue-gray-200 px-2 py-0.5 text-[11px] text-blue-gray-600 hover:bg-blue-gray-50 dark:border-gray-700 dark:text-blue-gray-300 dark:hover:bg-gray-800"
              onClick={reset}
            >
              기본값으로
            </button>
            <p className="mt-2 text-[11px] leading-relaxed text-blue-gray-400">
              지표는 화면에서 종가로 계산한 표시용이다. 판정이 쓰는 SMA200·ADX 는 서버가 세션의 함수로 재서 보내며, 콘솔
              차트의 보라 청산선이 그것이다.
            </p>
          </section>
        </div>
      ) : null}
    </div>
  );
}
