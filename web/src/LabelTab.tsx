/**
 * 매매 라벨 화면 (2026-09-03 · 사용자 요구 — 차트에 직접 표시).
 *
 * 🔴 **왜 전용 SVG 인가**: 표시는 "어느 봉·어느 가격" 을 정확히 집어야 한다.
 * 기존 `Chart`(lightweight-charts)는 그리기에 최적이지만 클릭 → (봉 index, 가격)
 * 환산을 우리가 통제하지 못한다. 여기서는 그 환산이 기능의 전부라 직접 그린다.
 *
 * ⚠️ **표시는 정답지가 아니다** (절대 규칙 #11 예외 조건). 여기서 모은 표시로
 * 규칙을 뽑고, 그 규칙은 **표시하지 않은 구간**에서 백테스트로 검증한다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { analysisFrame, labelLoad, labelSave } from "./api";
import type { Candle } from "./chartTypes";

type Kind = "long" | "short" | "swap" | "evidence" | "level";

type Mark = {
  kind: Kind;
  /** 봉 시각 (초) — 레벨선은 가장 가까운 봉을 잡아 둔다 (재현용). */
  ts: number;
  price: number;
  note?: string;
};

const TOOLS: { kind: Kind; label: string; hint: string; color: string }[] = [
  { kind: "long", label: "롱 진입선", hint: "여기서 롱으로 들어갔다", color: "#0f7b6c" },
  { kind: "short", label: "숏 진입선", hint: "여기서 숏으로 들어갔다", color: "#b4423a" },
  { kind: "swap", label: "스왑", hint: "들고 있던 것을 닫고 반대로 뒤집었다", color: "#7b1fa2" },
  { kind: "evidence", label: "근거 캔들", hint: "이 캔들을 보고 판단했다", color: "#f9a825" },
  { kind: "level", label: "지지·저항", hint: "이 가격선을 보고 있었다", color: "#2962ff" },
];

const TFS = ["5m", "15m", "1h", "4h"];
const H = 460;
const PAD = { l: 8, r: 62, t: 10, b: 22 };

/** 볼린저 — 사용자는 **두 벌**을 쓴다 (20,2)+(4,4). 시가 소스·모집단 표준편차. */
function bands(rows: Candle[], n: number, k: number) {
  const out: { mid: number; up: number; lo: number }[] = [];
  for (let i = 0; i < rows.length; i += 1) {
    if (i < n - 1) {
      out.push({ mid: NaN, up: NaN, lo: NaN });
      continue;
    }
    let sum = 0;
    for (let j = i - n + 1; j <= i; j += 1) sum += Number(rows[j]?.open ?? 0);
    const mid = sum / n;
    let acc = 0;
    for (let j = i - n + 1; j <= i; j += 1) acc += (Number(rows[j]?.open ?? 0) - mid) ** 2;
    const sd = Math.sqrt(acc / n);
    out.push({ mid, up: mid + k * sd, lo: mid - k * sd });
  }
  return out;
}

export function LabelTab() {
  const [symbol, setSymbol] = useState("XRP_USDT");
  const [tf, setTf] = useState("5m");
  const [rows, setRows] = useState<Candle[]>([]);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [tool, setTool] = useState<Kind>("short");
  const [name, setName] = useState("xrp_0902");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const box = useRef<SVGSVGElement | null>(null);
  const [width, setWidth] = useState(1100);

  const pull = useCallback(() => {
    setBusy(true);
    analysisFrame({ symbol, market: "BINANCE", flags: [], timeframe: tf, bars: 240 })
      .then((r) => {
        setRows(r.candles);
        setMsg(`${r.candles.length}봉 · ${tf}`);
      })
      .catch((exc: unknown) => setMsg(`불러오기 실패 — ${String(exc)}`))
      .finally(() => setBusy(false));
  }, [symbol, tf]);

  useEffect(pull, [pull]);

  useEffect(() => {
    labelLoad(name)
      .then((r) => setMarks((r.marks ?? []) as Mark[]))
      .catch(() => setMarks([]));
  }, [name]);

  useEffect(() => {
    const el = box.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const view = useMemo(() => {
    if (rows.length === 0) return null;
    const lows = rows.map((r) => Number(r.low));
    const highs = rows.map((r) => Number(r.high));
    const min = Math.min(...lows);
    const max = Math.max(...highs);
    const span = max - min || 1;
    const lo = min - span * 0.06;
    const hi = max + span * 0.06;
    const w = Math.max(width, 360);
    const iw = w - PAD.l - PAD.r;
    const ih = H - PAD.t - PAD.b;
    const bw = iw / rows.length;
    const x = (i: number) => PAD.l + i * bw + bw / 2;
    const y = (p: number) => PAD.t + ((hi - p) / (hi - lo)) * ih;
    const priceAt = (py: number) => hi - ((py - PAD.t) / ih) * (hi - lo);
    const barAt = (px: number) =>
      Math.max(0, Math.min(rows.length - 1, Math.floor((px - PAD.l) / bw)));
    return { w, bw, x, y, priceAt, barAt, b22: bands(rows, 20, 2), b44: bands(rows, 4, 4) };
  }, [rows, width]);

  const click = (ev: React.MouseEvent<SVGSVGElement>) => {
    if (!view || rows.length === 0) return;
    const r = ev.currentTarget.getBoundingClientRect();
    const px = ev.clientX - r.left;
    const py = ev.clientY - r.top;
    const i = view.barAt(px);
    const bar = rows[i];
    if (!bar) return;
    // 근거 캔들은 봉 자체를 집는다 — 가격은 그 봉 종가로 고정 (재현 시 모호함 제거)
    const price = tool === "evidence" ? Number(bar.close) : view.priceAt(py);
    setMarks((was) => [
      ...was,
      { kind: tool, ts: Math.floor(new Date(bar.ts).getTime() / 1000), price },
    ]);
  };

  const undo = () => setMarks((was) => was.slice(0, -1));
  const clear = () => setMarks([]);

  const save = () => {
    setBusy(true);
    labelSave(name, { symbol, timeframe: tf, market: "BINANCE", note, marks })
      .then((r) => setMsg(`저장됨 — ${r.marks}개 (${name})`))
      .catch((exc: unknown) => setMsg(`저장 실패 — ${String(exc)}`))
      .finally(() => setBusy(false));
  };

  const color = (k: Kind) => TOOLS.find((t) => t.kind === k)?.color ?? "#888";

  return (
    <section className="card wide">
      <h2 style={{ marginTop: 0 }}>매매 라벨 (임시 · 규칙 역산용)</h2>
      <p className="card-hint">
        차트를 클릭해 표시한다. 표시는 <b>정답지가 아니라 가설</b>이며, 여기서 뽑은 규칙은
        표시하지 않은 구간에서 백테스트로 검증한다 (절대 규칙 #11).
      </p>

      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <label className="field">
          종목
          <input value={symbol} onChange={(e) => setSymbol(e.target.value)} size={10} />
        </label>
        <label className="field">
          시간축
          <select value={tf} onChange={(e) => setTf(e.target.value)}>
            {TFS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          세션 이름
          <input value={name} onChange={(e) => setName(e.target.value)} size={12} />
        </label>
        <button className="btn" onClick={pull} disabled={busy}>
          다시 불러오기
        </button>
        <button className="btn primary" onClick={save} disabled={busy || marks.length === 0}>
          저장 ({marks.length})
        </button>
        <button className="btn" onClick={undo} disabled={marks.length === 0}>
          되돌리기
        </button>
        <button className="btn" onClick={clear} disabled={marks.length === 0}>
          전부 지우기
        </button>
      </div>

      <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 8 }}>
        {TOOLS.map((t) => (
          <button
            key={t.kind}
            className={`btn small${tool === t.kind ? " primary" : ""}`}
            title={t.hint}
            onClick={() => setTool(t.kind)}
            style={tool === t.kind ? { borderColor: t.color } : undefined}
          >
            <span style={{ color: t.color }}>■</span> {t.label}
          </button>
        ))}
        <span className="card-hint" style={{ alignSelf: "center" }}>
          {TOOLS.find((t) => t.kind === tool)?.hint}
        </span>
      </div>

      <div style={{ marginTop: 10 }}>
        <svg
          ref={box}
          width="100%"
          height={H}
          onClick={click}
          style={{ cursor: "crosshair", background: "var(--pure-white)", borderRadius: 8 }}
        >
          {view &&
            rows.map((r, i) => {
              const o = Number(r.open);
              const c = Number(r.close);
              const up = c >= o;
              const col = up ? "#26a69a" : "#ef5350";
              const bw = Math.max(view.bw * 0.62, 1);
              return (
                <g key={r.ts}>
                  <line
                    x1={view.x(i)}
                    x2={view.x(i)}
                    y1={view.y(Number(r.high))}
                    y2={view.y(Number(r.low))}
                    stroke={col}
                    strokeWidth={1}
                  />
                  <rect
                    x={view.x(i) - bw / 2}
                    y={view.y(Math.max(o, c))}
                    width={bw}
                    height={Math.max(Math.abs(view.y(o) - view.y(c)), 1)}
                    fill={col}
                  />
                </g>
              );
            })}
          {/* 밴드 두 벌 — 파랑 (20,2) · 빨강 (4,4) */}
          {view &&
            (
              [
                ["b22", "#2962ff"],
                ["b44", "#d32f2f"],
              ] as const
            ).map(([key, col]) =>
              (["mid", "up", "lo"] as const).map((part) => {
                const pts = (view[key] as { mid: number; up: number; lo: number }[])
                  .map((b, i) =>
                    Number.isFinite(b[part]) ? `${view.x(i)},${view.y(b[part])}` : "",
                  )
                  .filter(Boolean)
                  .join(" ");
                return (
                  <polyline
                    key={`${key}-${part}`}
                    points={pts}
                    fill="none"
                    stroke={col}
                    strokeWidth={part === "mid" ? 1.1 : 0.9}
                    strokeDasharray={part === "mid" ? "4 3" : undefined}
                    opacity={0.75}
                  />
                );
              }),
            )}
          {/* 표시 */}
          {view &&
            marks.map((m, n) => {
              const i = rows.findIndex(
                (r) => Math.floor(new Date(r.ts).getTime() / 1000) === m.ts,
              );
              const px = i >= 0 ? view.x(i) : PAD.l;
              const py = view.y(m.price);
              const col = color(m.kind);
              if (m.kind === "level") {
                return (
                  <g key={n}>
                    <line
                      x1={PAD.l}
                      x2={view.w - PAD.r}
                      y1={py}
                      y2={py}
                      stroke={col}
                      strokeWidth={1.4}
                      strokeDasharray="6 4"
                    />
                    <text x={view.w - PAD.r + 4} y={py + 4} fontSize={10} fill={col}>
                      {m.price.toFixed(5)}
                    </text>
                  </g>
                );
              }
              if (m.kind === "evidence") {
                return (
                  <rect
                    key={n}
                    x={px - Math.max(view.bw * 0.75, 3)}
                    y={PAD.t}
                    width={Math.max(view.bw * 1.5, 6)}
                    height={H - PAD.t - PAD.b}
                    fill={col}
                    opacity={0.16}
                  />
                );
              }
              const tri = m.kind === "long" ? 1 : -1;
              return (
                <g key={n}>
                  <line
                    x1={px - 26}
                    x2={px + 26}
                    y1={py}
                    y2={py}
                    stroke={col}
                    strokeWidth={2}
                  />
                  <polygon
                    points={`${px},${py - 9 * tri} ${px - 6},${py + 2 * tri} ${px + 6},${py + 2 * tri}`}
                    fill={col}
                  />
                  {m.kind === "swap" && (
                    <text x={px + 8} y={py - 10} fontSize={11} fill={col}>
                      스왑
                    </text>
                  )}
                </g>
              );
            })}
        </svg>
      </div>

      <div className="row" style={{ gap: 8, marginTop: 8, alignItems: "center" }}>
        <label className="field" style={{ flex: 1 }}>
          메모 (왜 그렇게 봤는가 — 규칙 환원에 가장 큰 도움)
          <input value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      {msg && <p className="card-hint">{msg}</p>}
      <p className="card-hint">
        표시 {marks.length}개 ·{" "}
        {TOOLS.map((t) => `${t.label} ${marks.filter((m) => m.kind === t.kind).length}`).join(
          " · ",
        )}
      </p>
    </section>
  );
}
