/**
 * AI 퍼포먼스 리포트 — 참가자(모델 x 프롬프트 x 스냅샷)별 페이퍼 실측 (T249 · 2026-09-10).
 *
 * 백테스트 리포트와 같은 지표만 싣는다: 표본 n · 방향 적중률 · 평균 R · 손익(%) · **MDD 병기** · 토큰.
 * 🔴 **표본 30 미만은 회색** — 판정 보류. 이 화면은 어느 모델이 낫다고 말하지 않는다. 관문(n≥30 · 기준선 위)을
 *    지난 참가자만 "채팅 기본 모델" 칸에 뜬다.
 *
 * ⛔ 시작 스위치는 **되돌릴 수 없다** — 켜는 순간 프롬프트를 바꾸면 새 참가자(표본 0)다. 팝업 없이 행 안 확인
 *    패널에서 '시작' 을 직접 친다.
 */

import { useCallback, useEffect, useState } from "react";
import { aiReport, aiReportStart, type AiReportView, type Who } from "./api";
import { ErrorCard, num, when } from "./ui";

/** 참가자 키 `모델@버전#해시/스냅샷` 을 세 칸으로. */
export function splitParticipant(key: string): { model: string; prompt: string; snapshot: string } {
  const at = key.indexOf("@");
  const model = at < 0 ? key : key.slice(0, at);
  const rest = at < 0 ? "" : key.slice(at + 1);
  const slash = rest.indexOf("/");
  const prompt = slash < 0 ? rest : rest.slice(0, slash);
  const snapshot = slash < 0 ? "" : rest.slice(slash + 1);
  return { model, prompt, snapshot };
}

/** 표본이 관문 아래인 줄의 클래스 — 회색. */
export function rowTone(judged: boolean): string {
  return judged ? "" : "faint";
}

function Sparkline({ curve }: { curve: string[] }) {
  const values = curve.map(Number);
  if (values.length < 2) return <span className="faint">—</span>;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const w = 120;
  const h = 28;
  const points = values
    .map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * h).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={w} height={h} aria-label="자본 곡선" role="img">
      <polyline fill="none" stroke="currentColor" strokeWidth="1.5" points={points} />
    </svg>
  );
}

export function AiReport({ who }: { who: Who | null }) {
  const [view, setView] = useState<AiReportView | null>(null);
  const [error, setError] = useState("");
  const [arming, setArming] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    aiReport()
      .then((got) => {
        setView(got);
        setError("");
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(load, [load]);

  const start = async () => {
    setBusy(true);
    try {
      await aiReportStart({ confirm: typed });
      setArming(false);
      setTyped("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const isAdmin = who?.role === "admin";

  return (
    <div className="page">
      {error && <ErrorCard message={error} />}
      {view && (
        <>
          <section>
            <h2>토너먼트</h2>
            {view.started ? (
              <p>
                <span className="chip live">진행 중</span> {when(view.started.at)} 부터 · 프롬프트 {view.prompt.version}#
                {view.prompt.hash} · 참가 {view.started.models?.length ?? 0} 모델 · 표본 관문 n≥{view.min_sample}
              </p>
            ) : (
              <>
                <p className="faint">
                  아직 켜지지 않았다. 켜면 그때부터의 AI 판 매매가 참가자별로 쌓인다. 프롬프트 {view.prompt.version}#
                  {view.prompt.hash} · 스냅샷 {view.snapshot.bars}봉/{view.snapshot.ohlc}OHLC.
                </p>
                {isAdmin && !arming && (
                  <button type="button" className="btn" onClick={() => setArming(true)}>
                    실험 시작…
                  </button>
                )}
                {isAdmin && arming && (
                  <div className="notice warn">
                    <p>
                      🔴 <b>되돌릴 수 없다.</b> 켜는 순간 라이브 구간이 시작되고, 프롬프트를 바꾸면 모든 모델이 새 참가자가 되어
                      표본이 0 부터 다시 쌓인다. 계속하려면 <code>시작</code> 을 친다.
                    </p>
                    <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="시작" aria-label="확인 문구" />
                    <button type="button" className="btn danger" disabled={busy || typed !== "시작"} onClick={start}>
                      켠다
                    </button>
                    <button type="button" className="btn" onClick={() => setArming(false)}>
                      취소
                    </button>
                  </div>
                )}
                {!isAdmin && <p className="faint">시작은 관리자만 켤 수 있다.</p>}
              </>
            )}
            <p>
              채팅 기본 모델 관문: {view.default_model ? <b>{view.default_model}</b> : <span className="faint">지난 참가자 없음 (풀 기본값)</span>}
              {" · "}기준선(사람·시스템 라이브) n={view.baseline.n}
              {view.baseline.hit_rate !== null && ` · 적중 ${num(Number(view.baseline.hit_rate), 1)}%`}
              {view.baseline.avg_r !== null && ` · R ${num(Number(view.baseline.avg_r), 2)}`}
            </p>
          </section>

          <section>
            <h2>참가자</h2>
            {view.participants.length === 0 ? (
              <p className="faint">참가자가 없다 — AI 주문이 하나라도 나가면 그 모델이 등록된다.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>모델</th>
                      <th>프롬프트</th>
                      <th className="num">n</th>
                      <th className="num">적중률</th>
                      <th className="num">평균 R</th>
                      <th className="num">손익</th>
                      <th className="num">MDD</th>
                      <th>곡선</th>
                      <th className="num">턴</th>
                      <th className="num">토큰(입/출)</th>
                      <th>마지막 청산</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.participants.map((p) => {
                      const parts = splitParticipant(p.participant);
                      const pnl = Number(p.pnl_pct);
                      return (
                        <tr key={p.participant} className={rowTone(p.judged)} title={p.judged ? "" : `표본 ${p.n} < ${p.min_sample} — 판정 보류`}>
                          <td>{parts.model}</td>
                          <td>
                            <code>{parts.prompt}</code> <span className="faint">{parts.snapshot}</span>
                          </td>
                          <td className="num">
                            {p.n}
                            {!p.judged && <span className="faint"> /{p.min_sample}</span>}
                          </td>
                          <td className="num">{p.hit_rate === null ? "—" : `${num(Number(p.hit_rate), 1)}%`}</td>
                          <td className="num">{p.avg_r === null ? "—" : num(Number(p.avg_r), 2)}</td>
                          <td className={`num ${pnl > 0 ? "gain" : pnl < 0 ? "loss" : ""}`}>{num(pnl, 2)}%</td>
                          <td className="num loss">-{num(Number(p.mdd_pct), 2)}%</td>
                          <td>
                            <Sparkline curve={p.curve} />
                          </td>
                          <td className="num">{p.turns}{p.failures > 0 && <span className="faint"> (실패 {p.failures})</span>}</td>
                          <td className="num">
                            {p.prompt_tokens.toLocaleString()}/{p.completion_tokens.toLocaleString()}
                          </td>
                          <td>{p.last_closed_at ? when(p.last_closed_at) : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="faint">
              회색 줄은 표본 {view.min_sample} 미만 — 판정 보류. 손익은 매매별 수익률(비용 차감)의 합, MDD 는 그 누적 곡선의 최대 낙폭.
              페이퍼 실측이며 예상이 아니다.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
