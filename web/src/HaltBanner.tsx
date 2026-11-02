/**
 * 신규 진입 스위치 **상태 배지** — 콘솔 머리 · RUN 탭 · 관리 화면이 같은 것을 그린다 (T754 · T754b).
 *
 * 🔴 **새 상태가 아니다.** 서버의 `app_settings.live_entries_halted` 를 읽는다. 🔴 **바꾸는 버튼은 여기 없다** —
 * 사이드바 '관리' 의 `AdminHaltControl`(붉은 버튼 + 관리자 비밀번호)만 바꾼다(사용자 2026-11-02 "왜 RUN 에
 * 붙어 있는 거지? 관리에서 제어"). 바뀌면 `announceHaltChanged()` 창 이벤트로 모든 배지가 바로 다시 읽는다.
 *
 * 🔴 **신규 진입만 멈춘다.** 든 포지션의 손절 · 청산 · 손절 올리기는 계속 돈다. 문구가 그것을 매번
 * 말해야 거래 리더 상실 · `LIVE_ORDERS` 꺼짐과 헷갈리지 않는다.
 *
 * 폴링: 60초 — 화면 폴링이 API CPU 를 올린 전력이 있어(T331) 10초 폴링에 얹지 않았다.
 */
import { useCallback, useEffect, useState } from "react";
import { liveAutoAll, type LiveAutoAllState } from "./api";

const EVENT = "updown:halt-changed";

/** 관리 화면이 스위치를 바꿨을 때 부른다 — 모든 배지가 60초를 안 기다리고 다시 읽는다. */
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

export function HaltBanner() {
  const [state, setState] = useState<LiveAutoAllState | null>(null);
  const [error, setError] = useState("");

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
      </div>
      {error ? <p className="notice bad halt-error">{error}</p> : null}
    </div>
  );
}
