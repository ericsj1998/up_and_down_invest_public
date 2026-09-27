/**
 * 휴대폰 · PC 알림 단추 — "알림 소리" 접힘 안 (사용자 2026-09-27).
 *
 * 소리는 화면이 열려 있을 때만 울린다. 이 단추로 켜면 창을 닫아도 기기 알림(진입 · 익절 · 손절 · 청산 · 불타기 ·
 * 경보)이 온다. 무엇을 언제 보내나는 서버(`apps/api/notify.py` · `orchestration/notify.py`)가 정한다.
 */

import { useEffect, useState } from "react";
import { disablePush, enablePush, pushState, testPush, type PushState } from "./pushNotify";

const HINT: Record<PushState, string> = {
  unsupported: "이 브라우저는 기기 알림(웹 푸시)을 지원하지 않는다.",
  "ios-home":
    "아이폰은 사파리 공유 단추 → \"홈 화면에 추가\" 로 앱을 만든 뒤, 그 앱에서 켤 수 있다(iOS 16.4 이상).",
  denied: "이 사이트의 알림이 막혀 있다 — 브라우저 사이트 설정에서 알림을 허용한 뒤 다시 누른다.",
  off: "켜면 창을 닫아도 진입 · 익절 · 손절 · 청산 · 불타기 · 경보가 이 기기 알림으로 온다.",
  on: "이 기기로 알림이 온다. 기기마다 따로 켠다(PC · 휴대폰).",
};

export function PushToggle() {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    pushState()
      .then(setState)
      .catch(() => setState("unsupported"));
  }, []);

  const run = (job: () => Promise<PushState>) => {
    setBusy(true);
    setMsg("");
    job()
      .then(setState)
      .catch((exc: unknown) => setMsg(`실패 — ${String(exc)}`))
      .finally(() => setBusy(false));
  };

  const test = () => {
    setBusy(true);
    setMsg("");
    testPush()
      .then((got) => setMsg(got.sent > 0 ? "시험 알림을 보냈다 — 몇 초 안에 떠야 한다" : "보낼 구독이 없다 — 다시 켠다"))
      .catch((exc: unknown) => setMsg(`실패 — ${String(exc)}`))
      .finally(() => setBusy(false));
  };

  if (state === null) return null;
  return (
    <div className="card" style={{ marginBottom: 8 }}>
      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        <span className="card-name">휴대폰 · PC 알림</span>
        <span className={state === "on" ? "text-gain" : "faint"}>{state === "on" ? "✅ 켜짐" : "꺼짐"}</span>
        {state === "off" || state === "denied" ? (
          <button type="button" className="btn small" disabled={busy} onClick={() => run(enablePush)}>
            알림 켜기
          </button>
        ) : null}
        {state === "on" ? (
          <>
            <button type="button" className="btn small" disabled={busy} onClick={test}>
              시험 알림
            </button>
            <button type="button" className="btn small" disabled={busy} onClick={() => run(disablePush)}>
              끄기
            </button>
          </>
        ) : null}
      </div>
      <div className="text-xs faint" style={{ marginTop: 4 }}>
        {HINT[state]}
      </div>
      {msg ? (
        <div className="text-xs" style={{ marginTop: 4 }}>
          {msg}
        </div>
      ) : null}
    </div>
  );
}
