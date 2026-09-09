/**
 * AI 투자 어시스턴트 채팅 — 도킹 패널 (T248 · 2026-09-09).
 *
 * 우측(기본) · 좌측 · 아래 · 떠 있음으로 옮길 수 있다. 라우트 밖(`Layout`)에 붙어 화면을 옮겨도 대화가 안 끊긴다.
 * 답은 작업(job)으로 만들어지고 SSE 로 진행을 본다 — 탭을 닫아도 답은 대화에 남는다.
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
import { ErrorCard } from "../ui";
import { proposalLine, readDock, visibleMessages, writeDock, type ChatMessageView, type Dock } from "./chat";
import { useJobEvents } from "./useJobEvents";

const DOCK_CLASS: Record<Dock, string> = {
  right: "fixed right-4 top-4 z-50 h-[calc(100vh-32px)] w-96 max-w-[92vw]",
  left: "fixed left-4 top-4 z-50 h-[calc(100vh-32px)] w-96 max-w-[92vw]",
  bottom: "fixed bottom-4 left-4 right-4 z-50 h-[45vh] xl:left-[21rem]",
  float: "fixed bottom-6 right-6 z-50 h-[60vh] w-[28rem] max-w-[92vw] shadow-2xl",
};

export function ChatPanel({ who, open, onClose }: { who: Who | null; open: boolean; onClose: () => void }) {
  const [dock, setDock] = useState<Dock>(readDock);
  const [threads, setThreads] = useState<ChatThreadView[]>([]);
  const [thread, setThread] = useState<ChatThreadView | null>(null);
  const [models, setModels] = useState<{ id: string; rank: number }[]>([]);
  const [auto, setAuto] = useState<Record<string, unknown> | null>(null);
  const [autoOpen, setAutoOpen] = useState(false);
  const [size, setSize] = useState<Record<number, string>>({});
  const [placed, setPlaced] = useState<Record<number, string>>({});
  const [model, setModel] = useState("");
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const job = useJobEvents(jobId);
  const bottom = useRef<HTMLDivElement | null>(null);

  const allowed = Boolean(who?.signed_in) && !who?.guest;

  const loadThreads = useCallback(() => {
    if (!allowed) return;
    chatThreads()
      .then((body) => setThreads(body.threads))
      .catch((exc: unknown) => setError(String(exc)));
  }, [allowed]);

  useEffect(() => {
    if (!open || !allowed) return;
    loadThreads();
    chatSettings()
      .then((got) => {
        setModels(got.models);
        setModel((was) => was || got.default);
        setAuto(got.auto as Record<string, unknown>);
      })
      .catch(() => {});
  }, [open, allowed, loadThreads]);

  // 답이 오면 대화를 다시 읽는다 — 저장된 근거·제안까지 같은 모양으로.
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

  const openThread = (id: string) => {
    chatThread(id)
      .then((got) => {
        setThread(got);
        setError("");
      })
      .catch((exc: unknown) => setError(String(exc)));
  };

  const fresh = () => {
    chatCreateThread({ model })
      .then((got) => {
        setThread(got);
        loadThreads();
      })
      .catch((exc: unknown) => setError(String(exc)));
  };

  const send = () => {
    const asked = text.trim();
    if (!asked || jobId) return;
    setError("");
    const go = (id: string) =>
      chatAsk(id, { text: asked, model })
        .then((got) => {
          setJobId(got.job_id);
          setText("");
          setThread((was) => (was ? { ...was, messages: [...(was.messages ?? []), { role: "user", content: asked }] } : was));
        })
        .catch((exc: unknown) => setError(String(exc)));
    if (thread) {
      go(thread.id);
      return;
    }
    chatCreateThread({ model })
      .then((got) => {
        setThread(got);
        loadThreads();
        return go(got.id);
      })
      .catch((exc: unknown) => setError(String(exc)));
  };

  if (!open) return null;
  const messages = visibleMessages((thread?.messages ?? []) as ChatMessageView[]);

  return (
    <aside
      className={`${DOCK_CLASS[dock]} flex flex-col overflow-hidden rounded-xl border border-blue-gray-100 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900`}
      aria-label="AI 투자 어시스턴트"
    >
      <div className="flex items-center gap-2 border-b border-blue-gray-100 px-3 py-2 dark:border-gray-800">
        <b className="text-sm">AI 투자 어시스턴트</b>
        <span className="faint text-xs">제안만 · 주문은 사람이</span>
        <span className="ml-auto flex gap-1">
          {(["right", "left", "bottom", "float"] as Dock[]).map((d) => (
            <button
              key={d}
              type="button"
              className={`btn small ${dock === d ? "primary" : ""}`}
              title={`${d} 에 붙인다`}
              onClick={() => {
                setDock(d);
                writeDock(d);
              }}
            >
              {d === "right" ? "▶" : d === "left" ? "◀" : d === "bottom" ? "▼" : "◱"}
            </button>
          ))}
          <button type="button" className="btn small" onClick={onClose} title="닫는다">
            ✕
          </button>
        </span>
      </div>

      {!allowed ? (
        <p className="faint p-3">로그인한 사람만 쓸 수 있다 — 게스트는 토큰이 드는 기능을 못 쓴다.</p>
      ) : (
        <>
          <div className="flex items-center gap-2 border-b border-blue-gray-100 px-3 py-1 text-xs dark:border-gray-800">
            <select value={thread?.id ?? ""} onChange={(e) => (e.target.value ? openThread(e.target.value) : setThread(null))}>
              <option value="">새 대화</option>
              {threads.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title || t.id} ({t.count})
                </option>
              ))}
            </select>
            <button type="button" className="btn small" onClick={fresh}>
              +
            </button>
            <button type="button" className={`btn small ${auto?.enabled ? "primary" : ""}`} onClick={() => setAutoOpen((was) => !was)} title="자동 실행 모드">
              {auto?.enabled ? "자동 ON" : "자동 OFF"}
            </button>
            <select value={model} onChange={(e) => setModel(e.target.value)} title="모델 — 성능은 T249 가 잰다">
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.rank}. {m.id}
                </option>
              ))}
            </select>
          </div>

          {autoOpen ? (
            <div className="border-b border-blue-gray-100 px-3 py-2 text-xs dark:border-gray-800">
              <p>{AUTO_ORDER_CONSENT_TEXT}</p>
              <p className="faint">
                오늘 {String(auto?.placed_today ?? 0)}건 / 상한 {String(auto?.max_per_day ?? 3)} · 노출 상한 {String(auto?.max_exposure_pct ?? 30)}% · 기본 주수 {String(auto?.shares ?? 1)} · 기본 예산 {String(auto?.margin ?? 50)} USDT
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
              <p className="faint">
                예: "테슬라 지금 저렴해?" · "비트코인 동향" · "내 포지션 몇 % 이득?" · "200만원으로 뭘 살까" · "NVDA 224 에 사고 217 손절
                234 목표로 제안해줘"
              </p>
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
                  {m.evidence && m.evidence.length ? (
                    <p className="faint mt-1 text-xs">
                      근거:{" "}
                      {m.evidence.map((e, i) => (
                        <span key={i} className={`chip ${e.ok ? "" : "loss"}`} title={e.error || e.digest}>
                          {e.name} {e.ms}ms
                        </span>
                      ))}
                      {m.model ? ` · ${m.model}` : ""}
                    </p>
                  ) : null}
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
                          {(p.blocked as string[] | undefined)?.length ? (
                            <div className="loss">⛔ {(p.blocked as string[]).join(" · ")}</div>
                          ) : null}
                          {(p.reasons as string[] | undefined)?.length ? (
                            <div className="faint">근거: {(p.reasons as string[]).join(" · ")}</div>
                          ) : null}
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
                </div>
              </div>
            ))}
            {jobId ? (
              <div className="faint text-xs">
                {job.lines.slice(-4).map((line, i) => (
                  <div key={i}>· {line}</div>
                ))}
                {!job.done ? <div>생각하는 중…</div> : null}
              </div>
            ) : null}
            <div ref={bottom} />
          </div>

          {error ? (
            <div className="px-3">
              <ErrorCard message={error} />
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
                  send();
                }
              }}
              disabled={Boolean(jobId)}
            />
            <button type="button" className="btn primary" onClick={send} disabled={Boolean(jobId) || !text.trim()}>
              보내기
            </button>
          </div>
        </>
      )}
    </aside>
  );
}
