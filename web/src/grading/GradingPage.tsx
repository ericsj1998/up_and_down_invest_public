/**
 * 차트 채점(dev · T281) — 연구 엔진의 매매를 1H 차트 위에 놓고 사람이 O/X · 수기 포지션을 남기고 JSON 으로 내보낸다.
 *
 * ⚠️ **성과 채점이 아니라 규칙을 끌어내는 입력이다** (절대 규칙 #11 · `grading.ts` 머리말). 여기서 남긴 것은
 * 백테스트 성과에 한 톨도 들어가지 않는다 — 코드 규칙으로 옮겨 OOS 로 판정한다.
 *
 * 사용자 요구(2026-09-14): ① 차트 위 진입·포지션 ② JSON 내보내기 ③ 매매마다 O/X ④ 진입선 가운데 · 손절까지 붉게 ·
 * 목표까지 초록인 롱/숏 그리기 ⑤ 라이브처럼 봉이 움직이는 재생.
 *
 * dev 빌드(`VITE_LABELS=1`)에서만 메뉴에 뜬다. 서버는 관리자만 통과시킨다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Who } from "../api";
import { type IndicatorSpec, type Ohlc } from "../chart/indicators";
import { fmtPrice } from "../chart/trades";
import { gradingCandles, gradingMarks, gradingRuns, gradingTrades, saveGradingMarks, type RunSummary } from "./api";
import {
  EMPTY_MARKS,
  exportPayload,
  gradeSummary,
  newPosition,
  nextCursor,
  rewardRisk,
  startCursor,
  visibleBars,
  visibleTrades,
  type Grade,
  type GradingTrade,
  type Marks,
  type UserPosition,
} from "./grading";
import { GradingChart, type DragKey } from "./GradingChart";

const STEP: Record<string, number> = { "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400 };
const SUB_OF: Record<string, string> = { "1h": "15m", "4h": "1h", "15m": "5m" };
const WARM = 60;
/** 창(일) — 봉 상한(서버 6,000)에 맞춘다: 12일이면 15m 1,152 · 1h 288. */
const DAYS_CHOICES = [6, 12, 24, 45];

const SPECS: IndicatorSpec[] = [
  { id: "bb20-2", type: "bb", length: 20, k: 2, enabled: true, color: "#b8860b" },
  { id: "bb4-4", type: "bb", length: 4, k: 4, enabled: true, color: "#1f4fd8" },
  { id: "ema20", type: "ema", length: 20, enabled: true, color: "#e6a700" },
  { id: "ema50", type: "ema", length: 50, enabled: true, color: "#0e7490" },
  { id: "ema200", type: "ema", length: 200, enabled: false, color: "#8957e5" },
];

function whenUtc(ts: number): string {
  return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) + "Z";
}

function uid(): string {
  return `p${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
}

export function GradingPage({ who }: { who: Who | null }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [dirs, setDirs] = useState<string[]>([]);
  const [file, setFile] = useState("");
  const [config, setConfig] = useState("");
  const [symbol, setSymbol] = useState("");
  const [timeframe, setTimeframe] = useState("1h");
  const [days, setDays] = useState(12);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [subNote, setSubNote] = useState("");
  const [busy, setBusy] = useState(false);

  const [market, setMarket] = useState<string | null>(null);
  const [allTrades, setAllTrades] = useState<GradingTrade[]>([]);
  const [bars, setBars] = useState<Ohlc[]>([]);
  const [subs, setSubs] = useState<Ohlc[]>([]);
  const [marks, setMarks] = useState<Marks>(EMPTY_MARKS);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const [specs, setSpecs] = useState<IndicatorSpec[]>(SPECS);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [placing, setPlacing] = useState<1 | -1 | null>(null);
  const [lookAt, setLookAt] = useState<{ from: number; to: number } | null>(null);

  // 재생
  const [cursor, setCursor] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const step = STEP[timeframe] ?? 3600;
  const subTf = SUB_OF[timeframe] ?? null;
  const subStep = subTf ? (STEP[subTf] ?? step) : step;

  const run = useMemo(() => runs.find((r) => r.file === file) ?? null, [runs, file]);

  useEffect(() => {
    if (!who?.may_admin) return;
    gradingRuns()
      .then((body) => {
        setRuns(body.runs);
        setDirs(body.dirs);
        const first = body.runs[0];
        if (first) {
          setFile(first.file);
          setConfig(first.configs[0]?.name ?? "");
          setSymbol(first.symbols[0] ?? "");
        }
      })
      .catch((exc: unknown) => setError(String(exc)));
  }, [who]);

  // 파일이 바뀌면 설정·종목을 그 파일 것으로
  useEffect(() => {
    if (run === null) return;
    if (!run.configs.some((c) => c.name === config)) setConfig(run.configs[0]?.name ?? "");
    if (!run.symbols.includes(symbol)) setSymbol(run.symbols[0] ?? "");
  }, [run, config, symbol]);

  const windowRange = useMemo(() => {
    if (anchor === null) return null;
    const span = days * 86_400;
    return { from: anchor - Math.floor(span / 2), to: anchor + Math.ceil(span / 2) };
  }, [anchor, days]);

  const load = useCallback(async () => {
    if (!file || !config || !symbol) return;
    setBusy(true);
    setError("");
    try {
      const got = await gradingTrades(file, config, symbol);
      setAllTrades(got.trades);
      setMarket(got.market);
      const saved = await gradingMarks(file, config, symbol);
      setMarks(saved);
      setSavedAt(saved.saved_at);
      setDirty(false);
      setFocusId(null);
      setActiveId(null);
      setCursor(null);
      setPlaying(false);
      // 창의 기준 시각 — 처음엔 매매가 가장 촘촘한 곳(첫 매매 뒤 6일).
      if (anchor === null) {
        const first = got.trades[0];
        setAnchor(first ? first.opened_ts + 3 * 86_400 : Math.floor(Date.now() / 1000) - 7 * 86_400);
      }
    } catch (exc) {
      setError(String(exc));
    } finally {
      setBusy(false);
    }
  }, [file, config, symbol, anchor]);

  // 봉 — 창이 바뀔 때마다 (기준봉 + 하위 봉). 앞쪽에 지표 예열 봉을 더 받는다.
  useEffect(() => {
    if (market === null || windowRange === null || !symbol) return;
    let alive = true;
    const from = windowRange.from - WARM * step;
    setBusy(true);
    // 하위 봉은 없어도 된다(그때 재생은 기준봉 단위) — 그 실패가 기준봉까지 막지 않게 따로 받는다.
    const lower = subTf
      ? gradingCandles(market, symbol, subTf, from, windowRange.to).catch((exc: unknown) => {
          setSubNote(`하위 봉(${subTf}) 없음 — 재생은 ${timeframe} 단위: ${String(exc).slice(0, 120)}`);
          return null;
        })
      : Promise.resolve(null);
    Promise.all([gradingCandles(market, symbol, timeframe, from, windowRange.to), lower])
      .then(([main, sub]) => {
        if (!alive) return;
        setBars(main.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
        const subRows = sub ? sub.candles : [];
        setSubs(subRows.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
        if (sub && subRows.length === 0) setSubNote(`하위 봉(${subTf}) 적재가 없다 — 재생은 ${timeframe} 단위`);
        else if (sub) setSubNote("");
        if (main.candles.length === 0) setError(`${symbol} ${timeframe} 봉이 이 구간에 적재돼 있지 않다 — 창 기준 시각을 옮긴다`);
        setLookAt({ from: windowRange.from, to: windowRange.to });
      })
      .catch((exc: unknown) => alive && setError(String(exc)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [market, symbol, timeframe, windowRange, subTf, step]);

  // 창 안의 매매만
  const trades = useMemo(() => {
    if (windowRange === null) return [];
    return allTrades.filter((t) => t.opened_ts >= windowRange.from && t.opened_ts <= windowRange.to);
  }, [allTrades, windowRange]);

  const shownBars = useMemo(() => visibleBars(bars, subs, cursor, step, subStep), [bars, subs, cursor, step, subStep]);
  const shownTrades = useMemo(() => visibleTrades(trades, cursor, subStep), [trades, cursor, subStep]);

  // 재생 틱
  const tick = useRef<number | null>(null);
  useEffect(() => {
    if (!playing || cursor === null) return;
    tick.current = window.setInterval(() => {
      setCursor((now) => {
        if (now === null) return now;
        const next = nextCursor(subs, bars, now, step);
        if (next === null) setPlaying(false);
        return next ?? now;
      });
    }, Math.max(60, 1000 / speed));
    return () => {
      if (tick.current !== null) window.clearInterval(tick.current);
    };
  }, [playing, speed, subs, bars, step, cursor === null]);

  const startReplay = () => {
    const at = startCursor(bars, subs, WARM, step);
    if (at === null) return;
    setCursor(at);
    setPlaying(true);
  };
  const stepOnce = () => {
    setCursor((now) => (now === null ? startCursor(bars, subs, WARM, step) : (nextCursor(subs, bars, now, step) ?? now)));
  };

  // 저장 (2초 뒤 · 바뀌었을 때만)
  useEffect(() => {
    if (!dirty || !file || !config || !symbol) return;
    const t = window.setTimeout(() => {
      saveGradingMarks(file, config, symbol, marks)
        .then((saved) => {
          setSavedAt(saved.saved_at);
          setDirty(false);
        })
        .catch((exc: unknown) => setError(String(exc)));
    }, 2000);
    return () => window.clearTimeout(t);
  }, [dirty, marks, file, config, symbol]);

  const grade = (id: string, g: Grade) => {
    setMarks((m) => {
      const grades = { ...m.grades };
      if (grades[id] === g) delete grades[id];
      else grades[id] = g;
      return { ...m, grades };
    });
    setDirty(true);
  };

  const place = useCallback(
    (price: number, time: number) => {
      if (placing === null) return;
      const p = newPosition(placing, price, time, step, uid());
      setMarks((m) => ({ ...m, positions: [...m.positions, p] }));
      setActiveId(p.id);
      setPlacing(null);
      setDirty(true);
    },
    [placing, step],
  );

  const drag = useCallback(
    (key: DragKey, price: number) => {
      setMarks((m) => ({
        ...m,
        positions: m.positions.map((p) => (p.id === activeId ? { ...p, [key]: price } : p)),
      }));
      setDirty(true);
    },
    [activeId],
  );

  const patchActive = (patch: Partial<UserPosition>) => {
    setMarks((m) => ({ ...m, positions: m.positions.map((p) => (p.id === activeId ? { ...p, ...patch } : p)) }));
    setDirty(true);
  };

  const removeActive = () => {
    setMarks((m) => ({ ...m, positions: m.positions.filter((p) => p.id !== activeId) }));
    setActiveId(null);
    setDirty(true);
  };

  const download = () => {
    const payload = exportPayload({ file, config, symbol, market, timeframe, bars, trades, marks });
    const blob = new Blob([JSON.stringify(payload, null, 1)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `grading_${file.replace(/\.json$/, "")}_${config}_${symbol}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const active = marks.positions.find((p) => p.id === activeId) ?? null;
  const summary = gradeSummary(trades, marks.grades);

  if (!who?.may_admin) {
    return <p className="faint p-4">관리자만 쓰는 dev 화면이다.</p>;
  }

  return (
    <div className="space-y-3 p-3">
      <div className="flex flex-wrap items-end gap-2 text-sm">
        <label className="field">
          결과 파일
          <select value={file} onChange={(e) => setFile(e.target.value)}>
            {runs.map((r) => (
              <option key={r.file} value={r.file}>
                {r.file} · {r.venue} · {r.cost}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          설정
          <select value={config} onChange={(e) => setConfig(e.target.value)}>
            {(run?.configs ?? []).map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.trades})
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          종목
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {(run?.symbols ?? []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          기준봉
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {["1h", "4h", "15m"].map((t) => (
              <option key={t} value={t}>
                {t} (하위 {SUB_OF[t] ?? "없음"})
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          창(일)
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {DAYS_CHOICES.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn" onClick={() => void load()} disabled={busy || !file}>
          불러오기
        </button>
        <button type="button" className="btn" onClick={download} disabled={bars.length === 0}>
          JSON 내보내기
        </button>
        <span className="faint">
          {dirs.length === 0 ? "결과 디렉터리가 없다 — UPDOWN_GRADING_DIRS 를 확인" : `저장 ${savedAt ? whenUtc(Date.parse(savedAt) / 1000) : "—"}${dirty ? " · 저장 대기" : ""}`}
        </span>
      </div>
      {error ? <p className="text-sm text-red-600">{error}</p> : null}
      {subNote ? <p className="faint text-sm">{subNote}</p> : null}

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="faint">지표</span>
        {specs.map((s) => (
          <label key={s.id} className="inline-flex items-center gap-1">
            <input
              type="checkbox"
              checked={s.enabled}
              onChange={() => setSpecs((all) => all.map((x) => (x.id === s.id ? { ...x, enabled: !x.enabled } : x)))}
            />
            <span style={{ color: s.color }}>{s.type === "bb" ? `BB ${s.length}/${s.k}` : `EMA ${s.length}`}</span>
          </label>
        ))}
        <span className="mx-2 faint">|</span>
        <button type="button" className={`btn ${placing === 1 ? "font-bold" : ""}`} onClick={() => setPlacing(placing === 1 ? null : 1)}>
          롱 그리기
        </button>
        <button type="button" className={`btn ${placing === -1 ? "font-bold" : ""}`} onClick={() => setPlacing(placing === -1 ? null : -1)}>
          숏 그리기
        </button>
        {placing !== null ? <span className="faint">차트를 누르면 그 가격·시각이 진입이다</span> : null}
        <span className="mx-2 faint">|</span>
        <span className="faint">재생</span>
        {cursor === null ? (
          <button type="button" className="btn" onClick={startReplay} disabled={bars.length === 0}>
            처음부터
          </button>
        ) : (
          <>
            <button type="button" className="btn" onClick={() => setPlaying((p) => !p)}>
              {playing ? "멈춤" : "계속"}
            </button>
            <button type="button" className="btn" onClick={stepOnce}>
              한 칸
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setPlaying(false);
                setCursor(null);
              }}
            >
              전부 보기
            </button>
            <span className="font-mono text-xs">{whenUtc(cursor + subStep)}</span>
          </>
        )}
        <label className="inline-flex items-center gap-1">
          속도
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {[1, 2, 4, 8, 16].map((s) => (
              <option key={s} value={s}>
                {s}x
              </option>
            ))}
          </select>
        </label>
      </div>

      <GradingChart
        bars={shownBars}
        step={step}
        specs={specs}
        trades={shownTrades}
        focusId={focusId}
        positions={marks.positions}
        activeId={activeId}
        placing={placing !== null}
        onPlace={place}
        onDrag={drag}
        onPick={setActiveId}
        lookAt={lookAt}
        follow={playing}
      />

      {active ? (
        <div className="flex flex-wrap items-center gap-2 rounded border p-2 text-sm">
          <span className="font-semibold">{active.side === 1 ? "롱" : "숏"} 포지션</span>
          {(["entry", "stop", "target"] as const).map((k) => (
            <label key={k} className="inline-flex items-center gap-1">
              {k === "entry" ? "진입" : k === "stop" ? "손절" : "목표"}
              <input
                className="w-28 font-mono"
                type="number"
                step="any"
                value={active[k]}
                onChange={(e) => patchActive({ [k]: Number(e.target.value) })}
              />
            </label>
          ))}
          <label className="inline-flex items-center gap-1">
            보유 봉
            <input
              className="w-16 font-mono"
              type="number"
              value={Math.round((active.to - active.from) / step)}
              onChange={(e) => patchActive({ to: active.from + Math.max(1, Number(e.target.value)) * step })}
            />
          </label>
          <span className="faint">손익비 {rewardRisk(active)?.toFixed(2) ?? "—"} · {whenUtc(active.from)}</span>
          <input
            className="min-w-[16rem] flex-1"
            placeholder="메모 (왜 여기인가 — 규칙으로 옮길 말)"
            value={active.note}
            onChange={(e) => patchActive({ note: e.target.value })}
          />
          <button type="button" className="btn" onClick={() => setActiveId(null)}>
            선택 해제
          </button>
          <button type="button" className="btn" onClick={removeActive}>
            지우기
          </button>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-4 text-xs">
        {summary.map((s) => (
          <span key={s.kind} className="faint">
            {s.kind}: O {s.o} · X {s.x} · 미채점 {s.none}
          </span>
        ))}
        <span className="faint">수기 포지션 {marks.positions.length}</span>
        <span className="faint">⚠️ O/X 와 수기 포지션은 규칙을 끌어내는 예시다 — 성과가 아니다</span>
      </div>

      <div className="max-h-[22rem] overflow-auto rounded border">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-white dark:bg-blue-gray-900">
            <tr className="text-left">
              <th className="p-1">진입(UTC)</th>
              <th className="p-1">다리</th>
              <th className="p-1">방향</th>
              <th className="p-1">진입가</th>
              <th className="p-1">청산가</th>
              <th className="p-1">순손익</th>
              <th className="p-1">사유</th>
              <th className="p-1">봉</th>
              <th className="p-1">O / X</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => {
              const g = marks.grades[t.id];
              const hidden = cursor !== null && t.opened_ts > cursor + subStep;
              return (
                <tr
                  key={t.id}
                  className={`cursor-pointer border-t ${focusId === t.id ? "bg-amber-50 dark:bg-blue-gray-800" : ""} ${hidden ? "opacity-40" : ""}`}
                  onClick={() => {
                    setFocusId(t.id);
                    setLookAt({ from: t.opened_ts - 40 * step, to: t.closed_ts + 20 * step });
                  }}
                >
                  <td className="p-1 font-mono">{whenUtc(t.opened_ts)}</td>
                  <td className="p-1">{t.kind}</td>
                  <td className="p-1">{t.side === 1 ? "롱" : "숏"}</td>
                  <td className="p-1 font-mono">{fmtPrice(t.entry)}</td>
                  <td className="p-1 font-mono">{fmtPrice(t.exit)}</td>
                  <td className={`p-1 font-mono ${t.pnl >= 0 ? "text-green-700" : "text-red-600"}`}>{t.pnl.toFixed(2)}%</td>
                  <td className="p-1">{t.reason}</td>
                  <td className="p-1 font-mono">{t.bars_held}</td>
                  <td className="p-1">
                    <button
                      type="button"
                      className={`btn mr-1 ${g === "O" ? "font-bold text-green-700" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        grade(t.id, "O");
                      }}
                    >
                      O
                    </button>
                    <button
                      type="button"
                      className={`btn ${g === "X" ? "font-bold text-red-600" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        grade(t.id, "X");
                      }}
                    >
                      X
                    </button>
                  </td>
                </tr>
              );
            })}
            {trades.length === 0 ? (
              <tr>
                <td className="p-2 faint" colSpan={9}>
                  창 안에 매매가 없다 — 창(일)을 늘리거나 아래 시각을 옮긴다.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="faint">창 기준 시각(UTC)</span>
        <input
          type="datetime-local"
          value={anchor === null ? "" : new Date(anchor * 1000).toISOString().slice(0, 16)}
          onChange={(e) => {
            const ms = Date.parse(`${e.target.value}Z`);
            if (!Number.isNaN(ms)) setAnchor(Math.floor(ms / 1000));
          }}
        />
        <button type="button" className="btn" onClick={() => anchor !== null && setAnchor(anchor - days * 86_400)}>
          ◀ 앞 창
        </button>
        <button type="button" className="btn" onClick={() => anchor !== null && setAnchor(anchor + days * 86_400)}>
          다음 창 ▶
        </button>
        <span className="faint">전체 매매 {allTrades.length} · 창 안 {trades.length}</span>
        <input
          className="min-w-[20rem] flex-1"
          placeholder="전체 메모"
          value={marks.notes}
          onChange={(e) => {
            setMarks((m) => ({ ...m, notes: e.target.value }));
            setDirty(true);
          }}
        />
      </div>
    </div>
  );
}
