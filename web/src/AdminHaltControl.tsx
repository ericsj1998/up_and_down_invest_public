/**
 * 사이드바 '관리' 화면 맨 위 — **모든 펀드 · RUN 의 신규 진입을 전부 멈추는 붉은 버튼** + 재개 버튼 +
 * 관리자 비밀번호 입력란 (T754b · 사용자 2026-11-02 "왜 RUN 에 붙어 있는 거지? 관리에서 제어 · 개빨간
 * 버튼 · 관리자 비밀번호").
 *
 * 🔴 **새 상태가 아니다.** 서버 `app_settings.live_entries_halted` 하나 — 콘솔 머리 배지(`HaltBanner`)와
 * 같은 스위치다. 여기서 바꾸면 `announceHaltChanged()` 로 배지가 바로 다시 읽는다.
 *
 * 🔴 **신규 진입만 멈춘다.** 든 포지션의 손절 · 청산 · 손절 올리기는 계속 돈다(§1.2.1). 문구가 매번 말한다.
 *
 * 비밀번호: 서버가 env `ADMIN_PW` 와 상수 시간으로 맞춘다 — 화면은 받아 보내고 **보낸 뒤 비운다**(성공 · 실패
 * 모두). 저장 · 자동완성 · 로그 없음. 서버에 `ADMIN_PW` 가 없으면 503 이 오고 그 말을 그대로 보인다.
 * 권한: `manage_roles` 가 없으면 배지만 — 버튼 · 입력란은 그리지 않는다(서버가 다시 막는다).
 */
import { useState } from "react";
import { setAutoAll, type Who } from "./api";
import { HaltBanner, announceHaltChanged } from "./HaltBanner";

export function AdminHaltControl({ who }: { who: Who | null }) {
  const canToggle = !!who?.caps?.includes("manage_roles");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState<"halt" | "resume" | "">("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const apply = (on: boolean) => {
    if (!pw) {
      setError("관리자 비밀번호를 먼저 넣는다");
      return;
    }
    setBusy(on ? "resume" : "halt");
    setError("");
    setDone("");
    setAutoAll(on, pw)
      .then((out) => {
        setDone(
          on
            ? `신규 진입 재개 — 도는 판 ${out.runs}개 중 ${out.changed}개가 바뀜`
            : `신규 진입 전부 중지 — 도는 판 ${out.runs}개 중 ${out.changed}개가 바뀜 · 든 포지션은 계속 관리됨`,
        );
        announceHaltChanged();
      })
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => {
        // 🔴 보낸 뒤 비운다 — 맞았든 틀렸든 화면에 비밀번호를 남겨 두지 않는다.
        setPw("");
        setBusy("");
      });
  };

  return (
    <div className="card admin-halt" data-testid="admin-halt">
      <h2>모든 펀드 · RUN 신규 진입 스위치</h2>
      <p className="muted">
        누르면 <b>모든 펀드와 모든 RUN</b> 이 새 포지션을 열지 않는다 — 든 포지션의 손절 · 청산 · 손절 올리기는
        계속 돈다. 재시작 · 리더 교체 · 매매법 전환 뒤에도 유지된다. 관리자 비밀번호(서버 env <code>ADMIN_PW</code>)와
        슈퍼관리자 권한이 <b>둘 다</b> 있어야 바뀐다.
      </p>
      {/* 지금 상태 — 콘솔 머리와 같은 배지(누가 · 언제). */}
      <HaltBanner />
      {canToggle ? (
        <>
          <label className="admin-halt-pw">
            <span className="faint">관리자 비밀번호</span>
            <input
              className="input"
              type="password"
              name="admin-halt-pw"
              autoComplete="off"
              spellCheck={false}
              value={pw}
              disabled={busy !== ""}
              placeholder="서버 env ADMIN_PW"
              onChange={(event) => setPw(event.target.value)}
              aria-label="관리자 비밀번호"
            />
          </label>
          <button
            type="button"
            className="admin-halt-btn halt"
            disabled={busy !== "" || !pw}
            title="모든 펀드 · 모든 RUN 의 신규 진입을 전부 멈춘다 — 든 포지션은 계속 관리된다 · 재시작 뒤에도 유지"
            onClick={() => apply(false)}
          >
            {busy === "halt" ? "중지 중…" : "모든 펀드 · RUN 신규 진입 전부 중지"}
          </button>
          <button
            type="button"
            className="admin-halt-btn resume"
            disabled={busy !== "" || !pw}
            title="모든 펀드 · 모든 RUN 의 신규 진입을 다시 받는다"
            onClick={() => apply(true)}
          >
            {busy === "resume" ? "재개 중…" : "진입 재개"}
          </button>
        </>
      ) : (
        <p className="faint">이 스위치는 슈퍼관리자(권한 <code>manage_roles</code>)만 바꿀 수 있다.</p>
      )}
      {done ? <p className="notice">{done}</p> : null}
      {error ? <p className="notice bad">{error}</p> : null}
    </div>
  );
}
