/**
 * 채팅 답의 대시보드 (T256) — 서버가 도구 결과로 **채워 준** 명세를 기존 부품(카드 · 표 · 칩 · 스파크라인)으로 그린다.
 *
 * 모델은 값을 만들지 못한다: 참조가 없던 칸은 빈 칸("—")이고, 몇 개가 비었는지 아래에 적힌다(환각 후보 · T249).
 */

import type { DashboardBlock, DashboardSpec } from "./chat";

function cell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "예" : "아니오";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="faint">—</span>;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const w = 240;
  const h = 40;
  const points = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * h).toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} role="img" aria-label="추이">
      <polyline fill="none" stroke="currentColor" strokeWidth="1.5" points={points} />
    </svg>
  );
}

function Block({ block }: { block: DashboardBlock }) {
  switch (block.kind) {
    case "cards":
      return (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {block.items.map((it, i) => (
            <div key={i} className="rounded-xl border border-blue-gray-100 px-3 py-2 dark:border-gray-700">
              <div className="faint text-xs">{it.label}</div>
              <div className={`text-base font-semibold ${it.value === null || it.value === undefined ? "faint" : ""}`}>
                {cell(it.value)}
                {it.unit && it.value !== null && it.value !== undefined ? <span className="faint text-xs"> {it.unit}</span> : null}
              </div>
            </div>
          ))}
        </div>
      );
    case "table":
      return (
        <div className="table-wrap" style={{ maxHeight: "20rem", overflowY: "auto" }}>
          <table className="text-xs">
            <thead>
              <tr>
                {block.columns.map((c) => (
                  <th key={c.key}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((r, i) => (
                <tr key={i}>
                  {r.map((v, j) => (
                    <td key={j} className={typeof v === "number" ? "num" : ""}>
                      {cell(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "chips":
      return (
        <div className="flex flex-wrap gap-1">
          {block.items.map((s, i) => (
            <span key={i} className="chip">
              {s}
            </span>
          ))}
        </div>
      );
    case "sparkline":
      return <Spark values={block.values} />;
    default:
      return <p className="whitespace-pre-wrap text-sm">{block.text}</p>;
  }
}

export function Dashboard({ spec, missing }: { spec: DashboardSpec; missing: string[] }) {
  return (
    <section className="chat-dashboard mt-3 space-y-3 rounded-2xl border border-blue-gray-100 bg-blue-gray-50/40 p-3 dark:border-gray-700 dark:bg-gray-800/40">
      {spec.title ? <div className="text-sm font-semibold">{spec.title}</div> : null}
      {spec.blocks.map((b, i) => (
        <div key={i}>
          {b.title && b.kind !== "text" ? <div className="faint mb-1 text-xs">{b.title}</div> : null}
          <Block block={b} />
        </div>
      ))}
      {missing.length ? (
        <p className="faint text-xs" title={missing.join("\n")}>
          근거 없는 칸 {missing.length}개는 비워 뒀다 — 도구 결과에 없는 값은 그리지 않는다.
        </p>
      ) : null}
    </section>
  );
}
