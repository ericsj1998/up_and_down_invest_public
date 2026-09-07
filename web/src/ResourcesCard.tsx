/**
 * 자원 창 — 관리자 카드 (T215 · 2026-09-04).
 *
 * > 사용자: *"배포 시 배포 공간 스펙을 지정하기 위한 RAM·CPU·저장용량 등을 확인할 수 있는 창."*
 *
 * 30초 폴링. 최근 48회(24분)를 localStorage 에 남겨 스파크라인으로 보인다 — 피크는 순간에
 * 오고(4h 마감·재기동 부활) 그 순간을 안 보면 스펙을 잘못 잡는다 (T64).
 * 경고선(RAM 85% · 디스크 80%)은 서버가 판정해 `warnings` 로 준다 — 화면은 보여만 준다.
 */

import { useEffect, useState } from "react";
import { request } from "./api";

type Proc = { rss: number; cpu_percent: number; threads: number; fds: number | null; uptime_s: number };
type Snap = {
  proc: string;
  ts: number;
  age_s?: number;
  process: Proc;
  cgroup: { memory: { current: number | null; max: number | null; percent: number | null }; cpu: { cores: number | null } };
  host: { load: (number | null)[]; cpu_count: number | null; mem_total: number; mem_used: number; mem_percent: number };
  disks: Record<string, { path: string; total?: number; used?: number; percent?: number | null; missing?: boolean }>;
};
type Body = {
  ts: number;
  api: Snap;
  engine: Snap | null;
  engine_note: string | null;
  db: { size: number | null; tables: { name: string; bytes: number }[]; error?: string };
  redis: { used_memory: number | null; error: string | null };
  runs: { running: number; max: number };
  warnings: string[];
};
type Point = { t: number; api: number; engine: number; load: number };

const SLOT = "resources-history";
const KEEP = 48;

function mb(n: number | null | undefined): string {
  if (n == null) return "—";
  return n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(0)} MB`;
}
function hours(s: number): string {
  return s >= 86400 ? `${(s / 86400).toFixed(1)}일` : `${(s / 3600).toFixed(1)}h`;
}

function load(): Point[] {
  try {
    const raw = localStorage.getItem(SLOT);
    return raw ? (JSON.parse(raw) as Point[]) : [];
  } catch {
    return [];
  }
}
function save(points: Point[]): void {
  try {
    localStorage.setItem(SLOT, JSON.stringify(points.slice(-KEEP)));
  } catch {
    // 저장 못 해도 화면은 산다
  }
}

function Spark({ points, pick, color }: { points: Point[]; pick: (p: Point) => number; color: string }) {
  if (points.length < 2) return <span className="faint">기록 중…</span>;
  const vals = points.map(pick);
  const max = Math.max(...vals, 1);
  const w = 160;
  const h = 28;
  const d = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - (v / max) * (h - 2)}`).join(" ");
  return (
    <svg width={w} height={h} aria-label="최근 추이">
      <polyline points={d} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

function Gauge({ label, value, text }: { label: string; value: number | null | undefined; text: string }) {
  const v = value == null ? 0 : Math.min(100, Math.max(0, value));
  const tone = value == null ? "#97a4b4" : value >= 85 ? "#dc2626" : value >= 70 ? "#d97706" : "#16a34a";
  return (
    <div style={{ minWidth: 180 }}>
      <div className="faint" style={{ fontSize: 12 }}>
        {label}
      </div>
      <div style={{ background: "#e4e9f0", borderRadius: 4, height: 8, overflow: "hidden" }}>
        <div style={{ width: `${v}%`, background: tone, height: "100%" }} />
      </div>
      <div className="mono" style={{ fontSize: 12 }}>
        {text}
      </div>
    </div>
  );
}

function ProcRow({ snap, name }: { snap: Snap; name: string }) {
  const cg = snap.cgroup.memory;
  const ramText = cg.max ? `${mb(cg.current)} / ${mb(cg.max)} (cgroup)` : `${mb(snap.process.rss)} RSS · 한도 없음`;
  const ramPct = cg.max ? cg.percent : (snap.process.rss / snap.host.mem_total) * 100;
  return (
    <tr>
      <td>
        <b>{name}</b>
        {snap.age_s != null ? <span className="faint"> · {snap.age_s.toFixed(0)}s 전</span> : null}
      </td>
      <td className="mono">{mb(snap.process.rss)}</td>
      <td className="mono">{snap.process.cpu_percent.toFixed(0)}%</td>
      <td className="mono">{snap.process.threads}</td>
      <td className="mono">{snap.process.fds ?? "—"}</td>
      <td className="mono">{hours(snap.process.uptime_s)}</td>
      <td style={{ minWidth: 200 }}>
        <Gauge label="RAM" value={ramPct} text={ramText} />
      </td>
    </tr>
  );
}

export function ResourcesCard() {
  const [body, setBody] = useState<Body | null>(null);
  const [error, setError] = useState("");
  const [hist, setHist] = useState<Point[]>(() => load());

  useEffect(() => {
    let alive = true;
    const pull = () =>
      request<Body>("/admin/resources")
        .then((b) => {
          if (!alive) return;
          setBody(b);
          setError("");
          setHist((prev) => {
            const next = [
              ...prev,
              {
                t: b.ts,
                api: b.api.process.rss,
                engine: b.engine?.process.rss ?? 0,
                load: b.api.host.load[0] ?? 0,
              },
            ].slice(-KEEP);
            save(next);
            return next;
          });
        })
        .catch((exc: unknown) => alive && setError(String(exc)));
    pull();
    const timer = window.setInterval(pull, 30_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const host = body?.api.host;
  const disks = body?.api.disks ?? {};
  return (
    <div className="card">
      <h2>자원 {body ? <span className="faint">· RUN {body.runs.running}/{body.runs.max}</span> : null}</h2>
      {error ? <p className="notice bad">{error}</p> : null}
      {body?.warnings.length ? (
        <p className="notice bad">
          {body.warnings.map((w) => (
            <span key={w}>
              🔴 {w}
              <br />
            </span>
          ))}
        </p>
      ) : null}
      <p className="muted">
        30초마다 갱신. 배포 스펙(T64)은 여기 <b>피크</b>로 잡는다 — 4h 마감 직후와 재기동 직후가 피크다.
      </p>
      {host ? (
        <div className="row" style={{ gap: "1.5rem", flexWrap: "wrap" }}>
          <Gauge
            label={`호스트 CPU (load1 / ${host.cpu_count ?? "?"}코어)`}
            value={host.load[0] != null && host.cpu_count ? (host.load[0] / host.cpu_count) * 100 : null}
            text={`load ${host.load.map((x) => (x == null ? "—" : x.toFixed(2))).join(" · ")}`}
          />
          <Gauge label="호스트 RAM" value={host.mem_percent} text={`${mb(host.mem_used)} / ${mb(host.mem_total)}`} />
          {Object.entries(disks).map(([k, d]) => (
            <Gauge
              key={k}
              label={`디스크 ${k}`}
              value={d.missing ? null : d.percent}
              text={d.missing ? `${d.path} 없음` : `${mb(d.used)} / ${mb(d.total)}`}
            />
          ))}
        </div>
      ) : null}
      <table className="grid" style={{ marginTop: "0.75rem" }}>
        <thead>
          <tr>
            <th>프로세스</th>
            <th>RSS</th>
            <th>CPU</th>
            <th>스레드</th>
            <th>fd</th>
            <th>가동</th>
            <th>RAM (한도 대비)</th>
          </tr>
        </thead>
        <tbody>
          {body ? <ProcRow snap={body.api} name="api" /> : null}
          {body?.engine ? (
            <ProcRow snap={body.engine} name="engine" />
          ) : (
            <tr>
              <td colSpan={7} className="muted">
                engine: {body?.engine_note ?? "…"}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="row" style={{ gap: "1.5rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
        <div>
          <div className="faint" style={{ fontSize: 12 }}>
            api RSS 추이 (최근 {hist.length}회)
          </div>
          <Spark points={hist} pick={(p) => p.api} color="#2563eb" />
        </div>
        <div>
          <div className="faint" style={{ fontSize: 12 }}>
            engine RSS 추이
          </div>
          <Spark points={hist} pick={(p) => p.engine} color="#7c3aed" />
        </div>
        <div>
          <div className="faint" style={{ fontSize: 12 }}>
            load1 추이
          </div>
          <Spark points={hist} pick={(p) => p.load} color="#d97706" />
        </div>
      </div>
      {body ? (
        <div className="row" style={{ gap: "2rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
          <div>
            <div className="faint" style={{ fontSize: 12 }}>
              DB {body.db.error ? <span className="loss">읽기 실패</span> : null}
            </div>
            <div className="mono">{mb(body.db.size)}</div>
            <ul className="faint" style={{ fontSize: 12, margin: "4px 0 0", paddingLeft: 16 }}>
              {body.db.tables.slice(0, 6).map((t) => (
                <li key={t.name}>
                  <span className="mono">{t.name}</span> {mb(t.bytes)}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="faint" style={{ fontSize: 12 }}>
              Redis {body.redis.error ? <span className="loss">읽기 실패</span> : null}
            </div>
            <div className="mono">{mb(body.redis.used_memory)}</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
