/**
 * 라이브 첫 화면 맨 위 — **신규 진입 스위치** 배지 + "매매 중지 · 매매 재개" (T754 · 2026-11-02).
 *
 * 🔴 **새 상태가 아니다.** 서버의 `app_settings.live_entries_halted`(RUN 탭 "전부 중지 · 전부 재개"
 * 와 같은 스위치)를 읽고 쓴다 — 두 자리가 다른 값을 가리키면 사람은 어느 쪽을 믿을지 모른다.
 *
 * 🔴 **신규 진입만 멈춘다.** 든 포지션의 손절 · 청산 · 손절 올리기는 계속 돈다. 문구가 그것을 매번
 * 말해야 거래 리더 상실 · `LIVE_ORDERS` 꺼짐과 헷갈리지 않는다.
 *
 * 권한: 서버가 `manage_roles` 로 판정한다(2026-10-02). 화면은 권한 없는 계정엔 배지만 그리고 버튼을 숨긴다.
 * 폴링: 60초 — 화면 폴링이 API CPU 를 올린 전력이 있어(T331) 10초 폴링에 얹지 않았다. 버튼을 누르면 바로
 * 다시 읽고, RUN 탭이 바꿨을 때는 `announceHaltChanged()` 창 이벤트로 즉시 맞춘다.
 */
import { useCallback, useEffect, useState } from "react";
import { liveAutoAll, setAutoAll, type LiveAutoAllState } from "./api";

const EVENT = "updown:halt-changed";

/** 다른 자리(RUN 탭)가 스위치를 바꿨을 때 부른다 — 배지가 60초를 안 기다리고 다시 읽는다. */
export function announceHaltChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

/** UTC ISO → KST 표시 (사용자 규약: 시각은 KST). */
function kst(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function HaltBanner({ canToggle }: { canToggle: boolean }) {
  const [state, setState] = useState<LiveAutoAllState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // 🔴 확인은 팝업(confirm)이 아니라 화면 안 한 줄 (CLAUDE.md 인라인 패널 규약).
  const [asking, setAsking] = useState<"halt" | "resume" | null>(null);

  const pull = useCallback(() => {
    liveAutoAll()
      .then((got) => {
        setState(got);
        setError("");
      })
      // ⚠️ 못 읽으면 "모른다" 로 둔다 — 멈췄다고도 돈다고도 단정하지 않는다.
      .catch((exc: unknown) => setError(String(exc)));
  }, []);

  useEffect(() => {
    pull();
    const timer = window.setInterval(pull, 60_000);
    window.addEventListener(EVENT, pull);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(EVENT, pull);
    };
  }, [pull]);

  const apply = (on: boolean) => {
    setBusy(true);
    setError("");
    setAutoAll(on)
      .then(() => {
        setAsking(null);
        announceHaltChanged();
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };

  const halted = state?.halted ?? null;
  const tone = halted === null ? "" : halted ? "bad" : "ok";
  // 메모리의 판 상태가 DB 스위치와 어긋나면(리더 교체 직후 등) 숫자로 말한다 — 배지 색만 믿게 두지 않는다.
  const mismatch =
    state && state.runs > 0 && (halted ? state.runs_auto_off < state.runs : state.runs_auto_off > 0);

  return (
    <div className={`halt-banner ${tone}`} data-testid="halt-banner">
      <div className="halt-row">
        <span className={`halt-badge ${tone}`}>
          {halted === null
            ? error
              ? "상태를 못 읽었다"
              : "확인 중"
            : halted
              ? "신규 진입 중지됨 — 든 포지션은 계속 관리됨"
              : "매매 중 — 신규 진입 받음"}
        </span>
        {state ? (
          <span className="faint halt-meta">
            {state.changed_at
              ? `${state.changed_by || "?"} · ${kst(state.changed_at)} KST`
              : state.persisted
                ? "바꾼 기록 없음(기본값 = 매매 중)"
                : "저장소 없음 — 메모리 상태"}
            {state.runs > 0
              ? ` · 도는 판 ${state.runs}개${mismatch ? ` (진입 꺼짐 ${state.runs_auto_off}개 — 스위치와 어긋남)` : ""}`
              : ""}
          </span>
        ) : null}
        {canToggle && state && asking === null ? (
          <span className="halt-actions">
            {halted ? (
              <button
                type="button"
                className="btn small"
                disabled={busy}
                title="모든 라이브 판의 신규 진입을 다시 받는다"
                onClick={() => setAsking("resume")}
              >
                매매 재개
              </button>
            ) : (
              <button
                type="button"
                className="btn small danger"
                disabled={busy}
                title="모든 라이브 판의 신규 진입만 멈춘다 — 든 포지션은 계속 관리된다 · 재시작 뒤에도 유지"
                onClick={() => setAsking("halt")}
              >
                매매 중지
              </button>
            )}
          </span>
        ) : null}
      </div>
      {asking ? (
        <div className="halt-row halt-confirm">
          <span>
            {asking === "halt"
              ? "신규 진입을 멈춘다 — 든 포지션의 손절 · 청산 · 손절 올리기는 계속 돈다. 재시작 뒤에도 유지된다."
              : "신규 진입을 다시 받는다 — 도는 라이브 판 전부에 바로 적용된다."}
          </span>
          <button
            type="button"
            className={`btn small ${asking === "halt" ? "danger" : "primary"}`}
            disabled={busy}
            onClick={() => apply(asking === "resume")}
          >
            {busy ? "…" : asking === "halt" ? "중지 확인" : "재개 확인"}
          </button>
          <button
            type="button"
            className="btn small"
            disabled={busy}
            onClick={() => setAsking(null)}
          >
            취소
          </button>
        </div>
      ) : null}
      {error ? <p className="notice bad halt-error">{error}</p> : null}
    </div>
  );
}
