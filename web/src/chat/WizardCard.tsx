/**
 * 위저드 카드 (T271) — 온보딩 단계를 채팅 안 카드로. 값은 서버(`wizard.card_for`)가 만들고 여기서는 그린다.
 *
 * 단추 클릭은 모델을 거치지 않는다 — `POST /ai/chat/threads/{id}/wizard` 가 초안을 저장하고 다음 카드를 돌려준다.
 * 마지막 카드만 살아 있고(`active`), 지난 카드는 읽기 전용이다. 팝업 없음 — 되돌릴 수 없는 "펀드 만들기" 는 카드 안 확인 줄.
 */
import { useState } from "react";
import type { WizardCard as Card } from "./chat";

export function WizardCard({
  card,
  active,
  busy,
  onAct,
}: {
  card: Card;
  active: boolean;
  busy: boolean;
  onAct: (action: string, answers: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>({ ...card.answers });
  const [confirming, setConfirming] = useState(false);
  const disabled = !active || busy;
  const pick = (key: string, value: string) => setDraft({ ...draft, [key]: value });

  return (
    <section className="chat-wizard mt-3 space-y-3 rounded-2xl border border-blue-gray-100 bg-blue-gray-50/40 p-3 text-sm dark:border-gray-700 dark:bg-gray-800/40">
      <div className="flex items-center justify-between">
        <div className="font-semibold">
          {card.index}/{card.total} · {card.title}
        </div>
        {!active ? <span className="faint text-xs">지난 단계</span> : null}
      </div>
      {card.error ? <p className="loss text-xs">{card.error}</p> : null}
      {card.step === "consent" ? (
        <div className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-xl border border-blue-gray-100 bg-white p-2 text-xs dark:border-gray-700 dark:bg-gray-900">
          {card.text}
        </div>
      ) : (
        <p className="faint text-xs">{card.text}</p>
      )}
      {card.fields.length ? (
        <div className="grid grid-cols-2 gap-2">
          {card.fields.map((f) => (
            <label key={f.key} className="field text-xs">
              {f.label}
              {f.type === "select" ? (
                <select value={String(draft[f.key] ?? f.value ?? "")} disabled={disabled} onChange={(e) => pick(f.key, e.target.value)}>
                  {(f.options ?? []).map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="number"
                  min={0}
                  value={String(draft[f.key] ?? f.value ?? "")}
                  disabled={disabled}
                  onChange={(e) => pick(f.key, e.target.value)}
                />
              )}
            </label>
          ))}
        </div>
      ) : null}
      {card.groups.map((g) => (
        <div key={g.key}>
          <div className="faint mb-1 text-xs">{g.label}</div>
          <div className="flex flex-wrap gap-1">
            {g.options.map((o) => {
              const on = String(draft[g.key] ?? "") === o.value || (draft[g.key] === undefined && o.selected);
              return (
                <button key={o.value} type="button" className={`chip${on ? " gain" : ""}`} title={o.hint ?? ""} disabled={disabled} onClick={() => pick(g.key, o.value)}>
                  {o.label}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      {card.options.length ? (
        <div className="space-y-1">
          {card.options.map((o) => {
            const on = String(draft.playbook ?? "") === o.value || (draft.playbook === undefined && o.selected);
            return (
              <button
                key={o.value}
                type="button"
                className={`flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left text-xs ${on ? "border-green-500" : "border-blue-gray-100 dark:border-gray-700"}`}
                disabled={disabled}
                onClick={() => pick("playbook", o.value)}
              >
                <span>
                  <span className="font-medium">{o.label}</span>
                  {o.recommended ? <span className="chip gain ml-1">추천</span> : null}
                  {o.default ? <span className="chip ml-1">성향 기본</span> : null}
                </span>
                <span className="faint">{o.hint}</span>
              </button>
            );
          })}
        </div>
      ) : null}
      {card.summary.length ? (
        <ul className="list-disc pl-5 text-xs">
          {card.summary.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      ) : null}
      {card.fund_id ? (
        <p className="text-xs">
          펀드 <span className="mono">{card.fund_id}</span> — <a href="/console">콘솔에서 보기</a>
        </p>
      ) : null}
      {active ? (
        <div className="flex flex-wrap items-center gap-2">
          {card.actions.map((a) =>
            a.confirm ? (
              confirming ? (
                <span key={a.action} className="flex items-center gap-2 text-xs">
                  정말 만들까요? 주문이 나갑니다(재인증 필요).
                  <button type="button" className="btn small primary" disabled={busy} onClick={() => onAct(a.action, draft)}>
                    예, 만든다
                  </button>
                  <button type="button" className="btn small" disabled={busy} onClick={() => setConfirming(false)}>
                    아니오
                  </button>
                </span>
              ) : (
                <button key={a.action} type="button" className="btn small primary" disabled={busy} onClick={() => setConfirming(true)}>
                  {a.label}
                </button>
              )
            ) : (
              <button key={a.action} type="button" className={`btn small${a.primary ? " primary" : ""}`} disabled={busy} onClick={() => onAct(a.action, draft)}>
                {a.label}
              </button>
            ),
          )}
        </div>
      ) : null}
    </section>
  );
}
