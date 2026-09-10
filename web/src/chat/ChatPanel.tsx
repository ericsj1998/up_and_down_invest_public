/**
 * AI 투자 어시스턴트 채팅 — 창의 **몸통** (T248 · T257 · 챗GPT식). 창 틀(단추 · 드래그 · 도킹 · 새 탭)은 `ChatShell`,
 * 큰 화면은 `AssistantPage` 가 맡는다.
 *
 * 대화(세션)는 전부 서버 `chat_threads` 에 남고, 마지막에 보던 대화는 브라우저가 기억한다. 답은 작업(job)으로
 * 만들어지고 SSE 로 진행(계획 → 도구 → 답)을 **항상** 보여 준다. 모든 답의 맨 아래에 근거 목록이 있고, 근거를
 * 누르면 옆의 세미 창에 도구 입력·출력이 뜬다. 추천 질문(도구마다 하나)은 늘 떠 있고, 답 뒤에는 다음 질문 3개가 붙는다.
 * 모델 답은 마크다운으로 그린다(`markdown.tsx`).
 *
 * 🔴 AI 는 **제안**만 한다. 제안 카드의 단추는 주식 주문 창에 값을 채울 뿐이고, 판은 사람이 그 창에서 띄운다 (§5.3.1).
 */

import { PaperAirplaneIcon, PlusIcon, SparklesIcon, TrashIcon } from "@heroicons/react/24/solid";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  chatAsk,
  chatCreateThread,
  chatDeleteThread,
  chatPlaceOrder,
  chatWizard,
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
import {
  proposalLine,
  readJob,
  readThread,
  THREAD_SLOT,
  visibleMessages,
  writeJob,
  writeThread,
  type ChatEvidence,
  type ChatMessageView,
} from "./chat";
import { Dashboard } from "./Dashboard";
import { WizardCard } from "./WizardCard";
import { EvidenceView } from "./evidence";
import { Markdown } from "./markdown";
import { useJobEvents } from "./useJobEvents";

export const SLOGAN = "투자를 쉽고, 빠르고, 정확하게";

export function ChatPanel({
  who,
  onTitle,
  wide = false,
  extra,
}: {
  who: Who | null;
  onTitle?: (title: string) => void;
  /** 큰 화면(`/assistant`) — 대화 목록 레일 + 가운데 넓은 본문. */
  wide?: boolean;
  /** 빈 대화 첫 화면에 얹을 것 (큰 화면의 "첫 펀드 설정" 카드). */
  extra?: React.ReactNode;
}) {
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
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
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
    // 돌던 작업도 이어 듣는다 — 서버가 진행 줄을 처음부터 재생하므로 잃는 줄이 없다.
    const pending = readJob();
    if (pending && remembered && pending.thread === remembered) setJobId(pending.job);
    else if (pending) writeJob(null);
  }, [allowed, loadThreads, openThread]);

  // 답이 오면 대화를 다시 읽는다 — 저장된 근거·제안·다음 질문까지 같은 모양으로.
  useEffect(() => {
    if (!job.done || !thread) return;
    if (job.error) setError(job.error);
    chatThread(thread.id)
      .then((got) => {
        setThread(got);
        setJobId(null);
        writeJob(null);
        loadThreads();
      })
      .catch((exc: unknown) => setError(String(exc)));
  }, [job.done, job.error, thread, loadThreads]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [thread?.messages?.length, job.lines.length]);

  // 새 대화 — 서버 행은 첫 질문 때 만든다(빈 대화를 남기지 않는다).
  const fresh = () => {
    setThread(null);
    setListOpen(false);
    setEvidence(null);
    setJobId(null);
    writeJob(null);
    try {
      localStorage.removeItem(THREAD_SLOT);
    } catch {
      // 기억만 못 한다.
    }
  };

  const remove = (id: string) => {
    chatDeleteThread(id)
      .then(() => {
        setConfirmDelete(null);
        if (thread?.id === id) fresh();
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
          writeJob({ thread: id, job: got.job_id });
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
  // T271 — 위저드 카드는 마지막 것만 살아 있다(지난 단계는 읽기 전용).
  const lastWizardIndex = messages.reduce((found, m, i) => (m.wizard ? i : found), -1);
  const [wizBusy, setWizBusy] = useState(false);
  const act = (step: string, action: string, answers: Record<string, unknown>) => {
    if (!thread) return;
    setWizBusy(true);
    setError("");
    chatWizard(thread.id, { action, step, answers })
      .then((got) => setThread((was) => (was ? { ...was, messages: [...(was.messages ?? []), ...got.messages] } : was)))
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setWizBusy(false));
  };
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const suggestions = jobId ? [] : (lastAssistant?.suggestions ?? []);

  if (!allowed) {
    return <p className="faint p-4">로그인한 사람만 쓸 수 있다 — 게스트는 토큰이 드는 기능을 못 쓴다.</p>;
  }

  const threadList = (
    <div className="flex min-h-0 flex-1 flex-col">
      <button type="button" className="mx-2 mt-2 flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-blue-gray-50 dark:hover:bg-gray-800" onClick={fresh}>
        <PlusIcon className="h-4 w-4" /> 새 대화
      </button>
      <div className="faint mx-4 mb-1 mt-3 text-xs">대화</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 text-sm">
        {threads.length === 0 ? <p className="faint px-3 py-2 text-xs">아직 대화가 없다.</p> : null}
        {threads.map((t) => (
          <div key={t.id} className={`group flex items-center gap-1 rounded-lg pr-1 hover:bg-blue-gray-50 dark:hover:bg-gray-800 ${thread?.id === t.id ? "bg-blue-gray-100 dark:bg-gray-800" : ""}`}>
            <button type="button" className="min-w-0 flex-1 truncate px-3 py-2 text-left" onClick={() => openThread(t.id)} title={`${t.count}턴 · ${when(t.updated_at)}`}>
              {t.title || "새 대화"}
            </button>
            {/* 삭제 — 팝업 없이 행 안에서 확인한다. */}
            {confirmDelete === t.id ? (
              <span className="flex shrink-0 items-center gap-1 text-xs">
                <button type="button" className="btn small" onClick={() => remove(t.id)}>
                  삭제
                </button>
                <button type="button" className="btn small" onClick={() => setConfirmDelete(null)}>
                  취소
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="grid h-7 w-7 shrink-0 place-items-center rounded text-blue-gray-400 opacity-0 hover:text-loss group-hover:opacity-100"
                title="이 대화를 지운다"
                aria-label="대화 삭제"
                onClick={() => setConfirmDelete(t.id)}
              >
                <TrashIcon className="h-4 w-4" />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );

  const column = wide ? "mx-auto w-full max-w-3xl px-4" : "px-4";

  return (
    <div className="flex min-h-0 flex-1">
      {wide ? <aside className="hidden w-64 shrink-0 flex-col border-r border-blue-gray-100 md:flex dark:border-gray-800">{threadList}</aside> : null}
      <div className="flex min-h-0 flex-1 flex-col">
        {/* 도구 줄 — 작은 창에서만 대화 목록 단추가 있다(큰 화면은 레일). */}
        <div className="flex items-center gap-2 border-b border-blue-gray-100 px-3 py-1 text-xs dark:border-gray-800">
          {!wide ? (
            <>
              <button type="button" className={`btn small ${listOpen ? "primary" : ""}`} onClick={() => setListOpen((was) => !was)} title="대화 목록">
                대화 {threads.length ? `(${threads.length})` : ""}
              </button>
              <button type="button" className="btn small" onClick={fresh} title="새 대화">
                +
              </button>
            </>
          ) : null}
          <button type="button" className={`btn small ${auto?.enabled ? "primary" : ""}`} onClick={() => setAutoOpen((was) => !was)} title="자동 실행 모드">
            {auto?.enabled ? "자동 ON" : "자동 OFF"}
          </button>
          <select className="min-w-0 max-w-[12rem] flex-1 truncate" value={model} onChange={(e) => setModel(e.target.value)} title="모델 — 성능은 T249 가 잰다">
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.rank}. {m.id}
              </option>
            ))}
          </select>
        </div>

        {listOpen && !wide ? (
          threadList
        ) : (
          <div className="relative flex min-h-0 flex-1">
            <div className="flex min-h-0 flex-1 flex-col">
              {autoOpen ? (
                <div className={`border-b border-blue-gray-100 py-2 text-xs dark:border-gray-800 ${column}`}>
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

              <div className="min-h-0 flex-1 overflow-y-auto py-3 text-sm">
                <div className={column}>
                  {messages.length === 0 ? (
                    <div className="chat-hello py-8 text-center">
                      <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-gray-900 text-white dark:bg-white dark:text-gray-900">
                        <SparklesIcon className="h-6 w-6" />
                      </div>
                      <div className="text-xl font-semibold">AI 투자 어시스턴트</div>
                      <div className="faint">{SLOGAN}</div>
                      {extra ? <div className="mt-5 text-left">{extra}</div> : null}
                      {starters.length ? (
                        <div className={`mt-6 grid gap-2 text-left ${wide ? "sm:grid-cols-2" : ""}`}>
                          {starters.map((s) => (
                            <button
                              key={s}
                              type="button"
                              className="rounded-xl border border-blue-gray-100 px-3 py-2 text-left text-sm hover:bg-blue-gray-50 dark:border-gray-800 dark:hover:bg-gray-800"
                              onClick={() => send(s)}
                            >
                              {s}
                            </button>
                          ))}
                        </div>
                      ) : null}
                      <p className="faint mt-4 text-xs">숫자는 전부 우리 도구가 잰 것이고, 예측은 하지 않는다.</p>
                    </div>
                  ) : null}
                  {messages.map((m, index) =>
                    m.role === "user" ? (
                      <div key={index} className="mb-4 flex justify-end">
                        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-blue-gray-100 px-4 py-2 dark:bg-gray-800">{m.content}</div>
                      </div>
                    ) : (
                      <div key={index} className="mb-5 flex gap-3">
                        <div className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-gray-900 text-white dark:bg-white dark:text-gray-900">
                          <SparklesIcon className="h-4 w-4" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <Markdown text={m.content} />
                          {m.dashboard && m.dashboard.blocks?.length ? <Dashboard spec={m.dashboard} missing={m.dashboard_missing ?? []} /> : null}
                          {m.wizard ? <WizardCard key={`w${index}`} card={m.wizard} active={index === lastWizardIndex} busy={wizBusy} onAct={(action, answers) => act(m.wizard?.step ?? "consent", action, answers)} /> : null}
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
                                <div key={i} className="mt-2 rounded-xl border border-blue-gray-100 p-3 text-xs dark:border-gray-700">
                                  <div className="font-medium">{proposalLine(p)}</div>
                                  {(p.blocked as string[] | undefined)?.length ? <div className="loss">⛔ {(p.blocked as string[]).join(" · ")}</div> : null}
                                  {(p.reasons as string[] | undefined)?.length ? <div className="faint">근거: {(p.reasons as string[]).join(" · ")}</div> : null}
                                  <div className="mt-2 flex flex-wrap items-center gap-2">
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
                          <p className="faint mt-2 flex flex-wrap items-center gap-1 text-xs">
                            <span>근거{m.evidence && m.evidence.length ? ` ${m.evidence.length}:` : ": 도구를 쓰지 않은 답"}</span>
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
                            {m.model ? <span className="truncate">· {m.model}</span> : null}
                          </p>
                        </div>
                      </div>
                    ),
                  )}
                  {/* ⭐ 작업 과정 — 답을 기다리는 동안 항상. 서버가 SSE 로 단계(계획 → 도구 → 답)를 보낸다. */}
                  {jobId ? (
                    <div className="chat-progress mb-4 flex gap-3">
                      <div className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-gray-900 text-white dark:bg-white dark:text-gray-900">
                        <span className="chat-spinner" aria-hidden="true" />
                      </div>
                      <div className="min-w-0 flex-1 text-xs">
                        <b className="text-sm">{job.done ? "정리 중" : (job.lines[job.lines.length - 1] ?? "생각하는 중")}</b>
                        <ol className="faint mt-1 list-decimal pl-5">
                          {job.lines.map((line, i) => (
                            <li key={i}>{line}</li>
                          ))}
                        </ol>
                      </div>
                    </div>
                  ) : null}
                  <div ref={bottom} />
                </div>
              </div>

              <div className={`${column} pb-3`}>
                {error ? <ErrorCard message={error} /> : null}
                {/* ⭐ 다음 질문 — 마지막 답이 제안한 것. 누르면 바로 보낸다. */}
                {suggestions.length ? (
                  <div className="mb-2 flex flex-wrap gap-1 text-xs">
                    {suggestions.map((s) => (
                      <button key={s} type="button" className="rounded-full border border-blue-gray-200 px-3 py-1 hover:bg-blue-gray-50 dark:border-gray-700 dark:hover:bg-gray-800" onClick={() => send(s)}>
                        {s}
                      </button>
                    ))}
                  </div>
                ) : null}
                {/* 추천 질문은 두 종류다 — 빈 화면의 안내(도구마다 고정 한 문장) · 답 뒤의 다음 질문(모델이 문맥으로 만든 것).
                    대화 중엔 후자만 보인다 (사용자 2026-09-10). */}
                <div className="flex items-end gap-2 rounded-2xl border border-blue-gray-200 bg-white p-2 shadow-sm dark:border-gray-700 dark:bg-gray-900">
                  <textarea
                    className="max-h-40 min-h-[2.5rem] flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-sm outline-none"
                    rows={1}
                    value={text}
                    placeholder="무엇이든 물어본다 — 근거는 우리 도구에서만"
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        send(text);
                      }
                    }}
                    disabled={Boolean(jobId)}
                  />
                  <button
                    type="button"
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gray-900 text-white disabled:opacity-40 dark:bg-white dark:text-gray-900"
                    onClick={() => send(text)}
                    disabled={Boolean(jobId) || !text.trim()}
                    aria-label="보내기"
                  >
                    <PaperAirplaneIcon className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>

            {/* ⭐ 근거 세미 창 — 도구 이름 · 인자 · 결과 원문(잘라서) · 지연. */}
            {evidence ? (
              <aside
                className={`chat-evidence flex flex-col border-l border-blue-gray-100 bg-white text-xs dark:border-gray-800 dark:bg-gray-900 ${
                  wide ? "w-96 shrink-0" : "absolute inset-y-0 right-0 z-10 w-[88%] shadow-2xl"
                }`}
              >
                <div className="flex items-center gap-1 border-b border-blue-gray-100 px-3 py-2 dark:border-gray-800">
                  <b className="min-w-0 flex-1 truncate">근거 · {evidence.name}</b>
                  <span className="faint">{evidence.ms}ms</span>
                  <button type="button" className="btn small" onClick={() => setEvidence(null)} title="닫는다">
                    ✕
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3">
                  <EvidenceView evidence={evidence} />
                </div>
              </aside>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
