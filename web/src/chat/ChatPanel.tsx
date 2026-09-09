/**
 * AI 투자 어시스턴트 채팅 — 창의 **몸통** (T248 · T257). 창 틀(단추 · 드래그 · 도킹 · 새 탭)은 `ChatShell` 이 맡는다.
 *
 * 대화(세션)는 전부 서버 `chat_threads` 에 남고, 마지막에 보던 대화는 브라우저가 기억한다. 답은 작업(job)으로
 * 만들어지고 SSE 로 진행(계획 → 도구 → 답)을 **항상** 보여 준다. 모든 답의 맨 아래에 근거 목록이 있고, 근거를
 * 누르면 옆의 세미 창에 도구 입력·출력이 뜬다. 추천 질문(도구마다 하나)은 늘 떠 있고, 답 뒤에는 다음 질문 3개가 붙는다.
 *
 * 🔴 AI 는 **제안**만 한다. 제안 카드의 단추는 주식 주문 창에 값을 채울 뿐이고, 판은 사람이 그 창에서 띄운다 (§5.3.1).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  chatAsk,
  chatCreateThread,
  chatPlaceOrder,
  chatSetAuto,
  chatSettings,
  chatThread,
  chatThreads,
  type ChatThreadView,
  type Who,
} from "../api";
import { requestStockOrder } from "../StockOrder";
import { AUTO_ORDER_CONSENT_TEXT, AUTO_ORDER_CONSENT_VERSION } from "../shell/disclaimer";
import { ErrorCard, when } from "../ui";
import { proposalLine, readThread, visibleMessages, writeThread, type ChatEvidence, type ChatMessageView } from "./chat";
import { useJobEvents } from "./useJobEvents";

export const SLOGAN = "투자를 쉽고, 빠르고, 정확하게";

export function ChatPanel({ who, onTitle }: { who: Who | null; onTitle?: (title: string) => void }) {
  const [threads, setThreads] = useState<ChatThreadView[]>([]);
  const [thread, setThread] = useState<ChatThreadView | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [models, setModels] = useState<{ id: string; rank: number }[]>([]);
  const [starters, setStarters] = useState<string[]>([]);
  const [auto, setAuto] = useState<Record<string, unknown> | null>(null);
  const [autoOpen, setAutoOpen] = useState(false);
  const [size, setSize] = useState<Record<number, string>>({});
  const [placed, setPlaced] = useState<Record<number, string>>({});
  const [model, setModel] = useState("");
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [evidence, setEvidence] = useState<ChatEvidence | null>(null);
  const job = useJobEvents(jobId);
  const bottom = useRef<HTMLDivElement | null>(null);

  const allowed = Boolean(who?.signed_in) && !who?.guest;

  useEffect(() => {
    onTitle?.(thread ? thread.title || "새 대화" : "AI 투자 어시스턴트");
  }, [thread, onTitle]);

  const loadThreads = useCallback(() => {
    if (!allowed) return;
    chatThreads()
      .then((body) => setThreads(body.threads))
      .catch((exc: unknown) => setError(String(exc)));
  }, [allowed]);

  const openThread = useCallback((id: string) => {
    chatThread(id)
      .then((got) => {
        setThread(got);
        setListOpen(false);
        setError("");
        writeThread(id);
      })
      .catch((exc: unknown) => setError(String(exc)));
  }, []);

  useEffect(() => {
    if (!allowed) return;
    loadThreads();
    chatSettings()
      .then((got) => {
        setModels(got.models);
        setModel((was) => was || got.default);
        setAuto(got.auto as Record<string, unknown>);
        setStarters(got.starters ?? []);
      })
      .catch(() => {});
    // ⭐ 마지막에 보던 대화로 돌아간다 — 세션은 서버에 있고 브라우저는 어느 것이었는지만 기억한다.
    const remembered = readThread();
    if (remembered) openThread(remembered);
  }, [allowed, loadThreads, openThread]);

  // 답이 오면 대화를 다시 읽는다 — 저장된 근거·제안·다음 질문까지 같은 모양으로.
  useEffect(() => {
    if (!job.done || !thread) return;
    if (job.error) setError(job.error);
    chatThread(thread.id)
      .then((got) => {
        setThread(got);
        setJobId(null);
        loadThreads();
      })
      .catch((exc: unknown) => setError(String(exc)));
  }, [job.done, job.error, thread, loadThreads]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [thread?.messages?.length, job.lines.length]);

  const fresh = () => {
    chatCreateThread({ model })
      .then((got) => {
        setThread(got);
        setListOpen(false);
        writeThread(got.id);
        loadThreads();
      })
      .catch((exc: unknown) => setError(String(exc)));
  };

  const send = (asked: string) => {
    const question = asked.trim();
    if (!question || jobId) return;
    setError("");
    setEvidence(null);
    const go = (id: string) =>
      chatAsk(id, { text: question, model })
        .then((got) => {
          setJobId(got.job_id);
          setText("");
          setThread((was) => (was ? { ...was, messages: [...(was.messages ?? []), { role: "user", content: question }] } : was));
        })
        .catch((exc: unknown) => setError(String(exc)));
    if (thread) {
      go(thread.id);
      return;
    }
    chatCreateThread({ model })
      .then((got) => {
        setThread(got);
        writeThread(got.id);
        loadThreads();
        return go(got.id);
      })
      .catch((exc: unknown) => setError(String(exc)));
  };

  const messages = visibleMessages((thread?.messages ?? []) as ChatMessageView[]);
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const suggestions = jobId ? [] : (lastAssistant?.suggestions ?? []);

  if (!allowed) {
    return <p className="faint p-3">로그인한 사람만 쓸 수 있다 — 게스트는 토큰이 드는 기능을 못 쓴다.</p>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-sky-100 bg-sky-50/60 px-3 py-1 text-xs dark:border-sky-900 dark:bg-sky-950/30">
        <button type="button" className={`btn small ${listOpen ? "primary" : ""}`} onClick={() => setListOpen((was) => !was)} title="대화 목록">
          대화 {threads.length ? `(${threads.length})` : ""}
        </button>
        <button type="button" className="btn small" onClick={fresh} title="새 대화">
          +
        </button>
        <button type="button" className={`btn small ${auto?.enabled ? "primary" : ""}`} onClick={() => setAutoOpen((was) => !was)} title="자동 실행 모드">
          {auto?.enabled ? "자동 ON" : "자동 OFF"}
        </button>
        <select className="min-w-0 flex-1" value={model} onChange={(e) => setModel(e.target.value)} title="모델 — 성능은 T249 가 잰다">
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.rank}. {m.id}
            </option>
          ))}
        </select>
      </div>

      {listOpen ? (
        <div className="min-h-0 flex-1 overflow-y-auto text-sm">
          {threads.length === 0 ? <p className="faint p-3">아직 대화가 없다. 아래에 질문을 쓰면 새 대화가 생긴다.</p> : null}
          {threads.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`flex w-full items-center gap-2 border-b border-blue-gray-50 px-3 py-2 text-left hover:bg-blue-gray-50 dark:border-gray-800 dark:hover:bg-gray-800 ${
                thread?.id === t.id ? "bg-blue-gray-50 dark:bg-gray-800" : ""
              }`}
              onClick={() => openThread(t.id)}
            >
              <span className="min-w-0 flex-1 truncate">{t.title || "새 대화"}</span>
              <span className="faint shrink-0 text-xs">
                {t.count}턴 · {when(t.updated_at)}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-h-0 flex-1 flex-col">
            {autoOpen ? (
              <div className="border-b border-blue-gray-100 px-3 py-2 text-xs dark:border-gray-800">
                <p>{AUTO_ORDER_CONSENT_TEXT}</p>
                <p className="faint">
                  오늘 {String(auto?.placed_today ?? 0)}건 / 상한 {String(auto?.max_per_day ?? 3)} · 노출 상한 {String(auto?.max_exposure_pct ?? 30)}% · 기본 주수{" "}
                  {String(auto?.shares ?? 1)} · 기본 예산 {String(auto?.margin ?? 50)} USDT
                </p>
                <div className="mt-1 flex gap-2">
                  <button
                    type="button"
                    className="btn small primary"
                    onClick={() =>
                      chatSetAuto({ enabled: true, consent_version: AUTO_ORDER_CONSENT_VERSION })
                        .then((got) => setAuto(got))
                        .catch((exc: unknown) => setError(String(exc)))
                    }
                  >
                    동의하고 켠다 (다음에는 묻지 않음)
                  </button>
                  <button
                    type="button"
                    className="btn small"
                    onClick={() =>
                      chatSetAuto({ enabled: false })
                        .then((got) => setAuto(got))
                        .catch((exc: unknown) => setError(String(exc)))
                    }
                  >
                    끈다
                  </button>
                </div>
              </div>
            ) : null}

            <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-sm">
              {messages.length === 0 ? (
                <div className="chat-hello py-6 text-center">
                  <div className="text-lg font-semibold">AI 투자 어시스턴트</div>
                  <div className="faint">{SLOGAN}</div>
                  <p className="faint mt-3 text-xs">무엇을 물을 수 있는지 아래 추천 질문을 눌러 본다. 숫자는 전부 우리 도구가 잰 것이고, 예측은 하지 않는다.</p>
                </div>
              ) : null}
              {messages.map((m, index) => (
                <div key={index} className={`mb-2 ${m.role === "user" ? "text-right" : ""}`}>
                  <div
                    className={`inline-block max-w-[95%] whitespace-pre-wrap rounded-lg px-3 py-2 text-left ${
                      m.role === "user" ? "bg-blue-gray-50 dark:bg-gray-800" : "bg-white ring-1 ring-blue-gray-100 dark:bg-gray-900 dark:ring-gray-700"
                    }`}
                  >
                    {m.content}
                    {m.failure ? <p className="loss text-xs">{m.failure}</p> : null}
                    {m.auto && typeof m.auto === "object" ? (
                      <p className="faint mt-1 text-xs">
                        자동 모드:{" "}
                        {((m.auto as { results?: Array<{ symbol: string; placed: boolean; why?: string; session_id?: string }> }).results ?? []).map((r, i) => (
                          <span key={i} className={`chip ${r.placed ? "gain" : "loss"}`} title={r.why ?? ""}>
                            {r.symbol} {r.placed ? `판 ${r.session_id}` : `안 냄 — ${r.why ?? ""}`}
                          </span>
                        ))}
                      </p>
                    ) : null}
                    {m.proposals && m.proposals.length
                      ? m.proposals.map((p, i) => (
                          <div key={i} className="mt-1 rounded border border-blue-gray-100 p-2 text-xs dark:border-gray-700">
                            <div>{proposalLine(p)}</div>
                            {(p.blocked as string[] | undefined)?.length ? <div className="loss">⛔ {(p.blocked as string[]).join(" · ")}</div> : null}
                            {(p.reasons as string[] | undefined)?.length ? <div className="faint">근거: {(p.reasons as string[]).join(" · ")}</div> : null}
                            <div className="mt-1 flex flex-wrap items-center gap-2">
                              <input
                                className="mono"
                                style={{ width: 90 }}
                                placeholder={p.group === "coin" ? "예산 USDT" : "주수"}
                                value={size[index * 100 + i] ?? ""}
                                onChange={(e) => setSize({ ...size, [index * 100 + i]: e.target.value })}
                              />
                              {/* 🔴 사람이 확인하는 자리 — 값은 서버가 다시 확정하고(RiskManager) actor=AI 로 판이 뜬다. */}
                              <button
                                type="button"
                                className="btn small primary"
                                disabled={p.ok === false || Boolean(placed[index * 100 + i]) || !thread}
                                title="이 제안을 확인하고 판을 띄운다 — actor=AI · 집행값은 RiskManager 가 확정"
                                onClick={() => {
                                  if (!thread) return;
                                  const raw = size[index * 100 + i];
                                  chatPlaceOrder({
                                    thread_id: thread.id,
                                    proposal: p,
                                    model,
                                    ...(p.group === "coin" ? { margin: raw || undefined } : { shares: raw ? Number(raw) : undefined }),
                                  })
                                    .then((got) => setPlaced({ ...placed, [index * 100 + i]: got.session_id }))
                                    .catch((exc: unknown) => setError(String(exc)));
                                }}
                              >
                                {placed[index * 100 + i] ? `판 ${placed[index * 100 + i]}` : "확인하고 주문"}
                              </button>
                              {p.group !== "coin" ? (
                                <button
                                  type="button"
                                  className="btn small"
                                  onClick={() => requestStockOrder(String(p.symbol), String(p.market))}
                                  title="주식 주문 창에 종목을 채운다 — 값을 고쳐서 사람이 낸다"
                                >
                                  주문 창으로
                                </button>
                              ) : null}
                            </div>
                          </div>
                        ))
                      : null}
                    {/* ⭐ 모든 답의 맨 아래 — 근거 목록. 누르면 세미 창에 도구 입력·출력. 근거가 없는 답도 그렇다고 적는다. */}
                    {m.role === "assistant" ? (
                      <p className="faint mt-2 border-t border-dashed border-blue-gray-100 pt-1 text-xs dark:border-gray-700">
                        근거{m.evidence && m.evidence.length ? ` ${m.evidence.length}:` : ": 도구를 쓰지 않은 답"}{" "}
                        {(m.evidence ?? []).map((e, i) => (
                          <button
                            key={i}
                            type="button"
                            className={`chip ${e.ok ? "" : "loss"} ${evidence === e ? "gain" : ""}`}
                            title="이 근거를 세미 창에서 본다"
                            onClick={() => setEvidence(evidence === e ? null : e)}
                          >
                            {e.name} {e.ms}ms
                          </button>
                        ))}
                        {m.model ? ` · ${m.model}` : ""}
                      </p>
                    ) : null}
                  </div>
                </div>
              ))}
              {/* ⭐ 작업 과정 — 답을 기다리는 동안 항상. 서버가 SSE 로 단계(계획 → 도구 → 답)를 보낸다. */}
              {jobId ? (
                <div className="chat-progress rounded-lg border border-blue-gray-100 px-3 py-2 text-xs dark:border-gray-700">
                  <div className="flex items-center gap-2">
                    <span className="chat-spinner" aria-hidden="true" />
                    <b>{job.done ? "정리 중" : (job.lines[job.lines.length - 1] ?? "생각하는 중")}</b>
                  </div>
                  <ol className="faint mt-1 list-decimal pl-5">
                    {job.lines.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ol>
                </div>
              ) : null}
              <div ref={bottom} />
            </div>

            {error ? (
              <div className="px-3">
                <ErrorCard message={error} />
              </div>
            ) : null}

            {/* ⭐ 다음 질문 — 마지막 답이 제안한 것. 누르면 바로 보낸다. */}
            {suggestions.length ? (
              <div className="flex flex-wrap gap-1 border-t border-blue-gray-100 px-3 py-1 text-xs dark:border-gray-800">
                <span className="faint">다음:</span>
                {suggestions.map((s) => (
                  <button key={s} type="button" className="chip" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            ) : null}
            {/* ⭐ 추천 질문 — 늘 떠 있다(무엇을 할 수 있는지 안내). 누르면 그 질문으로 채팅한다. */}
            {starters.length ? (
              <div className="chat-starters flex gap-1 overflow-x-auto border-t border-blue-gray-100 px-3 py-1 text-xs dark:border-gray-800">
                {starters.map((s) => (
                  <button key={s} type="button" className="chip shrink-0" onClick={() => send(s)} disabled={Boolean(jobId)}>
                    {s}
                  </button>
                ))}
              </div>
            ) : null}

            <div className="flex gap-2 border-t border-blue-gray-100 p-2 dark:border-gray-800">
              <input
                className="min-w-0 flex-1"
                value={text}
                placeholder="질문 — 근거는 우리 도구에서만"
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(text);
                  }
                }}
                disabled={Boolean(jobId)}
              />
              <button type="button" className="btn primary" onClick={() => send(text)} disabled={Boolean(jobId) || !text.trim()}>
                보내기
              </button>
            </div>
          </div>

          {/* ⭐ 근거 세미 창 — 도구 이름 · 인자 · 결과 원문(잘라서) · 지연. */}
          {evidence ? (
            <aside className="chat-evidence flex w-64 shrink-0 flex-col border-l border-blue-gray-100 text-xs dark:border-gray-800">
              <div className="flex items-center gap-1 border-b border-blue-gray-100 px-2 py-1 dark:border-gray-800">
                <b className="min-w-0 flex-1 truncate">근거 · {evidence.name}</b>
                <span className="faint">{evidence.ms}ms</span>
                <button type="button" className="btn small" onClick={() => setEvidence(null)} title="닫는다">
                  ✕
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-2">
                <div className="faint">입력</div>
                <pre className="mono whitespace-pre-wrap break-all">{JSON.stringify(evidence.arguments, null, 1)}</pre>
                {evidence.ok ? (
                  <>
                    <div className="faint mt-2">출력 (요약)</div>
                    <pre className="mono whitespace-pre-wrap break-all">{evidence.result || evidence.digest}</pre>
                  </>
                ) : (
                  <>
                    <div className="loss mt-2">실패</div>
                    <pre className="mono whitespace-pre-wrap break-all">{evidence.error}</pre>
                  </>
                )}
              </div>
            </aside>
          ) : null}
        </div>
      )}
    </div>
  );
}
