/**
 * 로그 내려받기 — 관리자 카드 (T211 · 2026-09-04).
 *
 * > 사용자: *"로그를 파일로 만들어 다운로드 할 수 있는 기능."*
 *
 * 날짜 범위와 종류만 고른다. 파일 이름은 서버가 정한다 — 화면이 경로를 만들지 않는다.
 * 내려받기는 `<a href>` 다: 브라우저가 쿠키를 붙여 그대로 받고, 큰 zip 도 스트리밍된다.
 *
 * ⚠️ 권한은 서버가 본다 (`/admin/logs` 는 관리자 접두어). 이 카드는 관리자 자리에만 놓는다.
 */

import { useEffect, useState } from "react";
import { request } from "./api";

type Summary = Record<string, { files: number; bytes: number; first: string | null; last: string | null }>;
type Listing = { root: string; kinds: string[]; summary: Summary; files: unknown[] };

const NAMES: Record<string, string> = {
  api: "API 로그",
  engine: "엔진 로그",
  funds: "펀드 원장",
  walkforward: "RUN 저널",
  reconcile: "거래소 대조",
  report_sends: "리포트 발송",
};

function mb(n: number): string {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;
}

function isoDay(offsetDays: number): string {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  return d.toISOString().slice(0, 10);
}

export function LogsCard() {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [from, setFrom] = useState(isoDay(-1));
  const [to, setTo] = useState(isoDay(0));
  const [kinds, setKinds] = useState<string[]>(["api", "engine"]);

  useEffect(() => {
    request<Listing>("/admin/logs")
      .then((body) => {
        setListing(body);
        setError("");
      })
      .catch((exc: unknown) => setError(String(exc)));
  }, []);

  const toggle = (k: string) =>
    setKinds((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]));

  const href =
    `/api/admin/logs/download?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}` +
    `&kinds=${encodeURIComponent(kinds.join(","))}`;
  const ready = kinds.length > 0 && from <= to;

  return (
    <div className="card">
      <h2>로그 내려받기</h2>
      {error ? <p className="notice bad">{error}</p> : null}
      <p className="muted">
        날짜 범위(UTC · 양끝 포함)와 종류를 골라 zip 으로 받는다. 앱 로그는 날짜별 회전 파일이고,
        나머지는 그 기간에 바뀐 파일이다.
      </p>
      <div className="row" style={{ gap: "0.75rem", flexWrap: "wrap", alignItems: "end" }}>
        <label>
          <span className="faint">부터</span>
          <br />
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          <span className="faint">까지</span>
          <br />
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        {ready ? (
          <a className="btn primary" href={href}>
            zip 내려받기
          </a>
        ) : (
          <span className="btn" aria-disabled="true">
            종류를 고르고 날짜를 맞춘다
          </span>
        )}
      </div>
      <table className="grid" style={{ marginTop: "0.75rem" }}>
        <thead>
          <tr>
            <th />
            <th>종류</th>
            <th>파일</th>
            <th>크기</th>
            <th>기간</th>
          </tr>
        </thead>
        <tbody>
          {(listing?.kinds ?? Object.keys(NAMES)).map((k) => {
            const s = listing?.summary[k];
            return (
              <tr key={k} className={s ? "" : "muted"}>
                <td>
                  <input type="checkbox" checked={kinds.includes(k)} onChange={() => toggle(k)} />
                </td>
                <td>
                  <b>{NAMES[k] ?? k}</b> <span className="faint mono">{k}</span>
                </td>
                <td>{s ? s.files : 0}</td>
                <td>{s ? mb(s.bytes) : "—"}</td>
                <td className="faint">{s ? `${s.first} ~ ${s.last}` : "없음"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {listing ? <p className="faint mono">root {listing.root}</p> : null}
    </div>
  );
}
