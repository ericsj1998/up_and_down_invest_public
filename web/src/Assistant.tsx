/**
 * AI 투자 어시스턴트 — 첫 접속 온보딩 위저드 (T247 · 2026-09-09).
 *
 * 동의 → 자본 → 성향 → 매매 설정(과거 창 실측 대시보드) → 검토 → 생성. 처음엔 AI 가 아니라 **사전 세팅**이다 —
 * 답이 곧 펀드 생성 페이로드가 된다. '다음에 설정하기' 로 콘솔로 바로 갈 수 있고, 탭에서 다시 열어 이어 한다.
 *
 * ⭐ "예상" 이라는 말을 쓰지 않는다. 숫자는 전부 서버가 저장소에서 낸 **과거 창 실측**이고 λ·기간·MDD 라벨이 붙는다.
 * 1일 수익률은 없다. 게스트는 서버가 초안을 안 받으므로(`persisted: false`) 브라우저에 든다.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  assistantCreate,
  assistantDraft,
  assistantPreview,
  assistantSaveDraft,
  type AssistantPreview,
  type Who,
} from "./api";
import {
  blockers,
  contributed,
  nextStep,
  prevStep,
  readLocal,
  writeLocal,
  STEPS,
  type Answers,
  type Cadence,
  type Step,
} from "./assistant";
import { DISCLAIMER_TEXT, DISCLAIMER_VERSION } from "./shell/disclaimer";
import { ErrorCard, num, pct } from "./ui";

const STEP_LABEL: Record<Step, string> = {
  consent: "동의",
  capital: "자본",
  profile: "성향",
  setup: "매매 설정",
  review: "검토",
  done: "완료",
};

const GROUPS = [
  { id: "coin", label: "코인" },
  { id: "domestic", label: "국내주식" },
  { id: "foreign", label: "미국주식" },
] as const;

const TIERS = [
  { id: "aggressive", label: "공격적 투자", hint: "손익이 가장 큰 매매법을 기본으로" },
  { id: "balanced", label: "균형 투자", hint: "MDD 대비 손익(Calmar)이 가장 좋은 매매법을 기본으로" },
  { id: "safe", label: "안전 투자", hint: "MDD·수면 아래가 가장 작은 매매법을 기본으로" },
] as const;

function Windows({ store }: { store: NonNullable<AssistantPreview["candidates"][number]["store"]> }) {
  const keys = ["3y", "2y", "1y", "1m"] as const;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>창</th>
            <th className="num">손익</th>
            <th className="num">MDD</th>
            <th className="num">매매</th>
            <th className="num">청산</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => {
            const w = store.windows?.[key];
            return (
              <tr key={key}>
                <td className="mono">{key}</td>
                {w ? (
                  <>
                    <td className={`num ${w.total_pct === null ? "" : w.total_pct >= 0 ? "gain" : "loss"}`}>
                      {w.total_pct === null ? "가림" : pct(w.total_pct)}
                    </td>
                    <td className="num">{pct(-Math.abs(w.mdd_pct))}</td>
                    <td className="num">{w.trades}</td>
                    <td className="num">{w.liquidations}</td>
                  </>
                ) : (
                  <td colSpan={4} className="faint">
                    구간 없음 — 저장소가 이 창보다 짧다
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Assistant({ who }: { who: Who | null }) {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("consent");
  const [answers, setAnswers] = useState<Answers>({ capital: 1_000_000, contribution: 0, cadence: "month", years: 1 });
  const [consented, setConsented] = useState(false);
  const [persisted, setPersisted] = useState(false);
  const [fundId, setFundId] = useState<string | null>(null);
  const [preview, setPreview] = useState<AssistantPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // 초안 읽기 — 서버가 들고 있으면 그것, 아니면(게스트) 브라우저.
  useEffect(() => {
    let alive = true;
    assistantDraft()
      .then((got) => {
        if (!alive) return;
        setPersisted(got.persisted);
        if (got.persisted && got.draft) {
          setStep(got.draft.step === "done" ? "review" : got.draft.step);
          setAnswers((was) => ({ ...was, ...got.draft?.answers }));
          setConsented(got.draft.consent_version !== null);
          setFundId(got.draft.fund_id);
        } else {
          const local = readLocal();
          if (local) {
            setStep(local.step === "done" ? "review" : local.step);
            setAnswers((was) => ({ ...was, ...local.answers }));
            setConsented(local.consented);
          }
        }
      })
      .catch((exc: unknown) => alive && setError(String(exc)))
      .finally(() => alive && setLoaded(true));
    return () => {
      alive = false;
    };
  }, []);

  const save = useCallback(
    (next: Step, body: Answers, consentNow = false) => {
      if (!persisted) writeLocal({ step: next, answers: body, consented: consented || consentNow });
      return assistantSaveDraft({
        step: next,
        answers: body as Record<string, unknown>,
        consent_version: consentNow ? DISCLAIMER_VERSION : undefined,
      });
    },
    [persisted, consented],
  );

  // 성향·갈래가 정해지면 후보와 실측을 받는다.
  useEffect(() => {
    if (!loaded || !answers.group || !answers.tier) return;
    let alive = true;
    assistantPreview(answers.group, answers.tier)
      .then((got) => {
        if (!alive) return;
        setPreview(got);
        setAnswers((was) => (was.playbook ? was : { ...was, playbook: got.chosen ?? undefined }));
      })
      .catch((exc: unknown) => alive && setError(String(exc)));
    return () => {
      alive = false;
    };
  }, [loaded, answers.group, answers.tier]);

  const stuck = blockers(step, answers, consented);
  const allowedGroups = useMemo(
    () => GROUPS.filter((g) => !who?.markets || who.markets[g.id]?.trade),
    [who],
  );

  const go = (next: Step, consentNow = false) => {
    setBusy(true);
    setError("");
    save(next, answers, consentNow)
      .then(() => {
        if (consentNow) setConsented(true);
        setStep(next);
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };

  const create = () => {
    setBusy(true);
    setError("");
    assistantCreate({ answers: answers as Record<string, unknown>, consent_version: DISCLAIMER_VERSION })
      .then((got) => {
        setFundId(got.fund_id);
        setStep("done");
        if (!persisted) writeLocal({ step: "done", answers, consented: true });
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };

  const chosen = preview?.candidates.find((c) => c.id === answers.playbook) ?? null;

  return (
    <div className="page">
      <div className="page-head">
        <h1>AI 투자 어시스턴트</h1>
        <p>
          질문 몇 개로 첫 펀드를 만든다. 지금은 AI 가 아니라 <b>사전 세팅</b>이다 — 답이 곧 펀드 설정이 된다. 숫자는 전부
          과거 창 <b>실측</b>이고 예상이 아니다.
        </p>
      </div>

      <div className="row">
        {STEPS.filter((s) => s !== "done").map((s) => (
          <span key={s} className={`chip ${s === step ? "gain" : STEPS.indexOf(s) < STEPS.indexOf(step) ? "" : "faint"}`}>
            {STEP_LABEL[s]}
          </span>
        ))}
        {!persisted && loaded ? (
          <span className="chip faint" title="게스트는 서버가 초안을 안 받는다 — 이 브라우저에만 남는다">
            브라우저 저장
          </span>
        ) : null}
        <button className="btn small" onClick={() => navigate("/console")}>
          다음에 설정하기
        </button>
      </div>

      {error ? <ErrorCard message={error} /> : null}

      {step === "consent" ? (
        <section className="card">
          <p>{DISCLAIMER_TEXT}</p>
          <p className="faint">문구 버전 {DISCLAIMER_VERSION} — 동의는 이 버전으로 기록된다.</p>
          <div className="row">
            <button className="btn primary" disabled={busy} onClick={() => go("capital", true)}>
              {consented ? "다음" : "동의하고 다음"}
            </button>
          </div>
        </section>
      ) : null}

      {step === "capital" ? (
        <section className="card">
          <div className="row">
            <label className="field">
              <span className="faint">시작 금액</span>
              <input
                type="number"
                min={1}
                value={answers.capital ?? 0}
                onChange={(e) => setAnswers({ ...answers, capital: Number(e.target.value) || 0 })}
              />
            </label>
            <label className="field">
              <span className="faint">추가 납입</span>
              <input
                type="number"
                min={0}
                value={answers.contribution ?? 0}
                onChange={(e) => setAnswers({ ...answers, contribution: Number(e.target.value) || 0 })}
              />
            </label>
            <label className="field">
              <span className="faint">주기</span>
              <select value={answers.cadence ?? "month"} onChange={(e) => setAnswers({ ...answers, cadence: e.target.value as Cadence })}>
                <option value="week">주</option>
                <option value="month">월</option>
                <option value="year">년</option>
              </select>
            </label>
            <label className="field">
              <span className="faint">기간(년)</span>
              <input
                type="number"
                min={0}
                value={answers.years ?? 1}
                onChange={(e) => setAnswers({ ...answers, years: Number(e.target.value) || 0 })}
              />
            </label>
          </div>
          <p className="faint">
            납입 누계 {num(contributed(answers.capital ?? 0, answers.contribution ?? 0, answers.cadence ?? "month", answers.years ?? 0), 0)}
            {" "}— 수익률을 곱하지 않은 값이다(예상 금지). 추가 납입은 1단계에서는 표시만 한다.
          </p>
          <Nav step={step} stuck={stuck} busy={busy} onBack={() => setStep(prevStep(step))} onNext={() => go(nextStep(step))} />
        </section>
      ) : null}

      {step === "profile" ? (
        <section className="card">
          <p className="faint">종목 갈래 — 거래 권한이 있는 것만 보인다.</p>
          <div className="row">
            {allowedGroups.map((g) => (
              <button
                key={g.id}
                className={answers.group === g.id ? "chip gain" : "chip"}
                style={{ cursor: "pointer", font: "inherit" }}
                onClick={() => setAnswers({ ...answers, group: g.id, playbook: undefined })}
              >
                {g.label}
              </button>
            ))}
            {allowedGroups.length === 0 ? <span className="chip loss">거래할 수 있는 갈래가 없다 — 관리자에게</span> : null}
          </div>
          <p className="faint" style={{ marginTop: 8 }}>
            성향 — 기본 매매법을 고르는 규칙이다. 나중에 바꿀 수 있다.
          </p>
          <div className="row">
            {TIERS.map((t) => (
              <button
                key={t.id}
                className={answers.tier === t.id ? "chip gain" : "chip"}
                title={t.hint}
                style={{ cursor: "pointer", font: "inherit" }}
                onClick={() => setAnswers({ ...answers, tier: t.id, playbook: undefined })}
              >
                {t.label}
              </button>
            ))}
          </div>
          <Nav step={step} stuck={stuck} busy={busy} onBack={() => setStep(prevStep(step))} onNext={() => go(nextStep(step))} />
        </section>
      ) : null}

      {step === "setup" ? (
        <section className="card">
          {preview ? (
            <>
              <p className="card-hint">
                {preview.group_label} · {preview.tier_label} — 기본은 <b>{preview.chosen ?? "없음"}</b>
                {preview.market ? ` · 시장 ${preview.market}` : " · 지금 열린 시장이 없다"}. {preview.note}
              </p>
              <div className="row">
                {preview.candidates.map((c) => (
                  <button
                    key={c.id}
                    className={answers.playbook === c.id ? "chip gain" : "chip"}
                    style={{ cursor: "pointer", font: "inherit" }}
                    title={c.backtest_note ?? ""}
                    onClick={() => setAnswers({ ...answers, playbook: c.id })}
                  >
                    {c.label}
                    {c.recommended ? " · 채택" : ""}
                    {c.store?.risk_tier_label ? ` · ${c.store.risk_tier_label}` : ""}
                  </button>
                ))}
                {preview.candidates.length === 0 ? <span className="chip loss">이 갈래에 고를 매매법이 없다</span> : null}
              </div>
              {chosen ? (
                chosen.store ? (
                  <div style={{ marginTop: 8 }}>
                    <p className="faint">
                      {chosen.label} · λ={chosen.leverage ?? 1} · {chosen.store.frame ?? ""} · 저장소 {chosen.store.years ?? "?"}년 ·
                      전체 손익 {chosen.store.total_pct === null || chosen.store.total_pct === undefined ? "가림" : pct(chosen.store.total_pct)} ·
                      MDD {pct(-Math.abs(chosen.store.mdd_pct ?? 0))} · 매매 {chosen.store.trades_count ?? "—"} · 청산 {chosen.store.liquidations ?? "—"}
                      {chosen.store.redacted ? " · 손익은 이 매매법의 백테스트 권한이 없어 가려졌다" : ""}
                    </p>
                    <Windows store={chosen.store} />
                  </div>
                ) : (
                  <p className="faint">이 매매법은 저장소(백테스트 실측)가 없다 — 숫자 없이 고르는 것이다.</p>
                )
              ) : null}
            </>
          ) : (
            <p className="faint">후보를 읽는 중…</p>
          )}
          <Nav step={step} stuck={stuck} busy={busy} onBack={() => setStep(prevStep(step))} onNext={() => go(nextStep(step))} />
        </section>
      ) : null}

      {step === "review" ? (
        <section className="card">
          <ul>
            <li>시작 금액 {num(answers.capital ?? 0, 0)} · 추가 납입 {num(answers.contribution ?? 0, 0)}/{answers.cadence ?? "month"} · {answers.years ?? 0}년</li>
            <li>
              갈래 {GROUPS.find((g) => g.id === answers.group)?.label ?? "—"} · 성향 {TIERS.find((t) => t.id === answers.tier)?.label ?? "—"}
            </li>
            <li>매매법 {chosen?.label ?? answers.playbook ?? "—"}{preview?.market ? ` · 시장 ${preview.market}` : ""}</li>
            <li className="faint">{who?.guest ? "게스트는 데모 펀드만 만든다." : "펀드는 지금 답한 서버(데모/실계좌)에 만들어진다."}</li>
          </ul>
          {fundId ? (
            <p className="faint">이미 만든 펀드 {fundId} 가 있다 — 다시 만들면 펀드가 하나 더 생긴다.</p>
          ) : null}
          <div className="row">
            <button className="btn small" disabled={busy} onClick={() => setStep(prevStep(step))}>
              이전
            </button>
            <button className="btn primary" disabled={busy || !answers.playbook || !preview?.market} onClick={create}>
              {busy ? "만드는 중…" : "이 기준으로 생성"}
            </button>
            <button className="btn small" disabled={busy} onClick={() => navigate("/console")}>
              다음에 이어서
            </button>
          </div>
        </section>
      ) : null}

      {step === "done" ? (
        <section className="card">
          <div className="row">
            <span className="chip gain">펀드를 만들었다</span>
            {fundId ? <span className="mono">{fundId}</span> : null}
            <button className="btn primary" onClick={() => navigate("/console")}>
              콘솔로 간다
            </button>
            <button className="btn small" onClick={() => setStep("review")}>
              설정을 다시 본다
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function Nav({
  step,
  stuck,
  busy,
  onBack,
  onNext,
}: {
  step: Step;
  stuck: string[];
  busy: boolean;
  onBack: () => void;
  onNext: () => void;
}) {
  return (
    <div className="row" style={{ marginTop: 8 }}>
      {step !== "capital" ? (
        <button className="btn small" disabled={busy} onClick={onBack}>
          이전
        </button>
      ) : null}
      <button className="btn primary" disabled={busy || stuck.length > 0} onClick={onNext} title={stuck.join(" · ")}>
        다음
      </button>
      {stuck.map((s) => (
        <span key={s} className="chip loss">
          {s}
        </span>
      ))}
    </div>
  );
}
