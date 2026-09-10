/**
 * 거시 지표 카드 (T262 · 사용자 요구 2026-09-10) — 주식 콘솔 맨 위.
 *
 * VIX 는 큰 카드 하나: 값 · 구간(안정/약한 공포/강한 공포)을 색으로 · 구간 눈금 · 한 줄 설명.
 * 나머지(나스닥100 선물 · S&P 500 · 미국 10년물 · 달러 인덱스 · 금 · WTI · 원달러 · 기준금리 · CPI · 코스피 ·
 * 코스닥 · 한국 10년물)는 작은 카드 격자. 못 받은 지표는 이유와 함께 접힌 줄로 — 조용히 빠지지 않는다.
 */

import { useEffect, useState } from "react";
import { macro, type MacroIndicator, type MacroView } from "./api";

const TONE_COLOR: Record<string, string> = {
  calm: "#2ea043",
  fear: "#d29922",
  panic: "#f85149",
};

function num(v: string | null | undefined, digits: number): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function digitsOf(m: MacroIndicator): number {
  if (m.unit === "%") return 2;
  const n = Math.abs(Number(m.value));
  return n >= 1000 ? 0 : n >= 100 ? 1 : 2;
}

function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function changeChip(pct: string | null | undefined) {
  const n = Number(pct);
  if (!Number.isFinite(n) || pct === null || pct === undefined) return null;
  return (
    <span className={n > 0 ? "gain" : n < 0 ? "loss" : ""} style={{ marginLeft: 6, fontSize: 12 }}>
      {n > 0 ? "+" : ""}
      {n.toFixed(2)}%
    </span>
  );
}

function VixCard({ m, view }: { m: MacroIndicator; view: MacroView }) {
  const color = TONE_COLOR[m.tone ?? ""] ?? "inherit";
  return (
    <div className="card" style={{ padding: 12, borderLeft: `6px solid ${color}` }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
        <strong>{m.label}</strong>
        <span className="faint text-xs">
          {m.source}
          {m.as_of ? ` · ${when(m.as_of)}` : ""}
        </span>
      </div>
      <div className="row" style={{ alignItems: "baseline", gap: 10, marginTop: 4 }}>
        <span style={{ fontSize: 34, fontWeight: 700, color }}>{num(m.value, 2)}</span>
        <span className="chip" style={{ background: color, color: "#fff", borderColor: color }}>
          {m.band ?? "—"}
        </span>
        {changeChip(m.change_pct)}
      </div>
      <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
        {view.vix_bands.map((b, i) => {
          const prev = i === 0 ? null : (view.vix_bands[i - 1]?.below ?? null);
          const range = b.below === null ? `${prev ?? ""} 이상` : prev === null ? `${b.below} 미만` : `${prev}~${b.below}`;
          return (
            <span
              key={b.label}
              className="text-xs"
              style={{
                padding: "2px 8px",
                borderRadius: 999,
                border: `1px solid ${TONE_COLOR[b.tone] ?? "#888"}`,
                color: TONE_COLOR[b.tone] ?? "inherit",
                fontWeight: b.label === m.band ? 700 : 400,
              }}
            >
              {range} {b.label}
            </span>
          );
        })}
      </div>
      <p className="faint text-sm" style={{ margin: "8px 0 0" }}>
        {view.vix_note}
      </p>
    </div>
  );
}

function SmallCard({ m }: { m: MacroIndicator }) {
  return (
    <div className="card" style={{ padding: 10 }}>
      <div className="faint text-xs" style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
        <span>{m.label}</span>
        <span>{when(m.as_of)}</span>
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>
        {num(m.value, digitsOf(m))}
        <span className="faint" style={{ fontSize: 12, marginLeft: 4 }}>
          {m.unit}
        </span>
        {changeChip(m.change_pct)}
      </div>
      <div className="faint text-xs" style={{ marginTop: 2 }}>
        {m.note ? `${m.note} · ` : ""}
        {m.source}
      </div>
    </div>
  );
}

export function MacroPanel() {
  const [view, setView] = useState<MacroView | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const load = () =>
      macro()
        .then((body) => {
          if (alive) {
            setView(body);
            setError("");
          }
        })
        .catch((exc: unknown) => {
          if (alive) setError(String(exc));
        });
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  if (error && !view) return <p className="faint text-sm">거시 지표를 못 읽었다 — {error}</p>;
  if (!view) return <p className="faint text-sm">거시 지표를 읽는 중…</p>;
  const vix = view.indicators.find((m) => m.key === "vix");
  const rest = view.indicators.filter((m) => m.key !== "vix");
  return (
    <section style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 style={{ margin: "4px 0" }}>시장 분위기 · 거시 지표</h3>
        <span className="faint text-xs">{when(view.at)} 갱신 · 1분마다</span>
      </div>
      {vix ? <VixCard m={vix} view={view} /> : null}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))",
          gap: 8,
          marginTop: 8,
        }}
      >
        {rest.map((m) => (
          <SmallCard key={m.key} m={m} />
        ))}
      </div>
      {view.failures.length > 0 ? (
        <details style={{ marginTop: 6 }}>
          <summary className="faint text-sm">못 받은 지표 {view.failures.length}개 — 이유</summary>
          <ul className="text-sm faint" style={{ margin: "4px 0 0 16px" }}>
            {view.failures.map((f) => (
              <li key={f.key}>
                <b>{f.label}</b> — {f.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
