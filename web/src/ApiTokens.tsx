/**
 * 개인 API 토큰 (T263 · MCP) — 만들기 · 목록 · 되돌리기 · Claude Desktop 설정 조각.
 *
 * 토큰 값은 **만든 직후 한 번만** 보인다(서버는 해시만 둔다). 팝업 없음 — 되돌리기는 행 안 확인.
 * 토큰은 읽기와 MCP 만 된다 — 주문·설정 변경은 여전히 구글 로그인 화면에서.
 */

import { useCallback, useEffect, useState } from "react";
import { apiTokenCreate, apiTokenRevoke, apiTokens, type ApiTokenRow, type Who } from "./api";
import { ErrorCard } from "./ui";

function when(text: string | null): string {
  if (!text) return "—";
  const d = new Date(text);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("ko-KR");
}

/** Claude Desktop `claude_desktop_config.json` 조각 — 원격 MCP 를 `mcp-remote` 로 잇는 표준 모양. */
export function desktopConfig(origin: string, token: string): string {
  const url = `${origin.replace(/\/$/, "")}/api/mcp`;
  return JSON.stringify(
    {
      mcpServers: {
        updown: {
          command: "npx",
          args: ["-y", "mcp-remote", url, "--header", `Authorization: Bearer ${token}`],
        },
      },
    },
    null,
    2,
  );
}

export function ApiTokens({ who }: { who: Who | null }) {
  const [rows, setRows] = useState<ApiTokenRow[]>([]);
  const [name, setName] = useState("Claude Desktop");
  const [fresh, setFresh] = useState<{ token: string; name: string } | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");

  const load = useCallback(() => {
    apiTokens()
      .then((body) => setRows(body.tokens))
      .catch((exc: unknown) => setError(String(exc)));
  }, []);
  useEffect(() => {
    if (who?.signed_in && !who.guest) load();
  }, [who, load]);

  if (!who?.signed_in || who.guest) {
    return <p className="faint p-4">로그인한 사람만 토큰을 만든다 — 게스트는 안 된다.</p>;
  }

  const create = () => {
    setBusy(true);
    setError("");
    apiTokenCreate(name.trim() || "MCP")
      .then((body) => {
        setFresh({ token: body.token, name: body.name });
        load();
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };
  const revoke = (id: string) => {
    setBusy(true);
    apiTokenRevoke(id)
      .then(() => {
        setConfirm(null);
        load();
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };
  const copy = (text: string, what: string) => {
    navigator.clipboard
      ?.writeText(text)
      .then(() => setCopied(what))
      .catch(() => setCopied(""));
  };
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  return (
    <section className="p-4" style={{ maxWidth: 880 }}>
      <h2 style={{ margin: "0 0 4px" }}>개인 API 토큰 · MCP</h2>
      <p className="faint text-sm" style={{ margin: "0 0 12px" }}>
        Claude Desktop · Cursor · ChatGPT 같은 바깥 AI 가 이 앱의 도구(종목 풀기 · 시장 구조 · 재무 · 포지션 · 스크리닝 ·
        거시 지표 · 제안 …)를 쓰게 하는 열쇠다. 토큰은 <b>읽기와 MCP 만</b> 된다 — 주문·설정은 여전히 화면에서.
        값은 만든 직후 한 번만 보인다.
      </p>
      {error ? <ErrorCard title="토큰" message={error} onClose={() => setError("")} /> : null}
      <div className="row" style={{ gap: 6, marginBottom: 12 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="이름 (예: Claude Desktop)" style={{ width: 220 }} />
        <button type="button" className="btn" onClick={create} disabled={busy}>
          {busy ? "만드는 중…" : "토큰 만들기"}
        </button>
        <span className="faint text-xs">구글 재인증이 최근이어야 한다(만료면 다시 로그인하라고 답한다).</span>
      </div>
      {fresh ? (
        <div className="card" style={{ padding: 12, marginBottom: 12, borderLeft: "6px solid #2ea043" }}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
            <strong>{fresh.name} — 지금 복사해 두세요. 다시 볼 수 없다.</strong>
            <button type="button" className="btn small" onClick={() => setFresh(null)}>
              닫기
            </button>
          </div>
          <pre className="mono" style={{ margin: "8px 0", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {fresh.token}
          </pre>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            <button type="button" className="btn small" onClick={() => copy(fresh.token, "token")}>
              {copied === "token" ? "복사됨" : "토큰 복사"}
            </button>
            <button type="button" className="btn small" onClick={() => copy(desktopConfig(origin, fresh.token), "config")}>
              {copied === "config" ? "복사됨" : "Claude Desktop 설정 복사"}
            </button>
          </div>
          <details style={{ marginTop: 8 }}>
            <summary className="faint text-sm">Claude Desktop 설정 조각 (claude_desktop_config.json)</summary>
            <pre className="mono text-xs" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {desktopConfig(origin, fresh.token)}
            </pre>
            <p className="faint text-xs" style={{ margin: "4px 0 0" }}>
              Claude Desktop → 설정 → 개발자 → 설정 편집. 저장 뒤 Claude 를 다시 켠다. Cursor·ChatGPT 는 원격 MCP 주소{" "}
              <code>{origin}/api/mcp</code> 와 위 헤더를 그대로 넣는다.
            </p>
          </details>
        </div>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>이름</th>
              <th>만든 때</th>
              <th>마지막 사용</th>
              <th>상태</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="faint">
                  토큰이 없다.
                </td>
              </tr>
            ) : null}
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td>{when(r.created_at)}</td>
                <td>{when(r.last_used_at)}</td>
                <td>{r.revoked_at ? <span className="chip">되돌림</span> : <span className="chip gain">살아 있음</span>}</td>
                <td>
                  {r.revoked_at ? null : confirm === r.id ? (
                    <span className="inline-flex items-center gap-1">
                      <span className="text-xs">이 토큰을 쓰는 클라이언트가 끊긴다.</span>
                      <button type="button" className="btn small danger" onClick={() => revoke(r.id)} disabled={busy}>
                        되돌리기
                      </button>
                      <button type="button" className="btn small" onClick={() => setConfirm(null)}>
                        취소
                      </button>
                    </span>
                  ) : (
                    <button type="button" className="btn small" onClick={() => setConfirm(r.id)}>
                      되돌리기
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
