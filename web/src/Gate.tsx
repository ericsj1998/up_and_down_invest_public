/**
 * 로그인 문 — **화면 전체를 감싼다** (2026-08-30 배포 준비).
 *
 * ⚠️ 여기는 **편의**지 방어가 아니다. 진짜 방어는 API 미들웨어
 * (`apps/api/auth.guard`)에 있다 — URL 을 아는 사람은 화면을 거치지 않고 API 를
 * 직접 부른다. 이 파일이 하는 일은 *"로그인해야 한다"* 를 사람에게 **말해 주는 것**이다.
 *
 * 🔴 그래서 여기서 무엇을 숨기든 보안이 되지 않는다. 숨기는 이유는 **빈 화면과
 * 오류 배너 대신 무엇을 해야 하는지 보여 주기** 위해서다.
 */

import { useCallback, useEffect, useState } from "react";
import {
  contactAdmin,
  guestLogin,
  me,
  logout as signOut,
  onAuthTrouble,
  type AuthTrouble,
  type Who,
  modeChosen,
  noRealAccount,
  switchMode,
} from "./api";

/**
 * 보조 단추 — 회원가입·게스트. 주 단추(검정)와 **같은 움직임**(살짝 떠오르고 화살표가 미끄러짐), 색만 회색.
 * 눈에 띄는 색은 화면에 하나(주 동작)만 둔다 — 모던·미니멀 (사용자 2026-09-05).
 */
const SECONDARY =
  "group inline-flex w-full items-center justify-center gap-2 rounded-xl border border-blue-gray-200 bg-white px-6 py-3 text-sm font-semibold text-blue-gray-700 transition-[transform,box-shadow,border-color] duration-150 hover:-translate-y-px hover:border-blue-gray-300 hover:shadow-lg hover:shadow-blue-gray-900/10 active:translate-y-0";

/** 구글 'G' — 색 네 개의 표준 마크 (외부 이미지 없이 인라인). */
function GoogleMark() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6C12.3 13.2 17.7 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.5 24.5c0-1.6-.1-2.8-.4-4H24v8.1h12.9c-.3 2.2-1.7 5.4-4.9 7.6l7.5 5.8c4.5-4.2 7-10.3 7-17.5z"
      />
      <path
        fill="#FBBC05"
        d="M10.4 28.8A14.5 14.5 0 0 1 9.5 24c0-1.7.3-3.3.8-4.8l-7.8-6A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.8l7.8-6z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2 1.4-4.7 2.4-8.4 2.4-6.3 0-11.7-3.7-13.6-9.9l-7.8 6C6.5 42.6 14.6 48 24 48z"
      />
    </svg>
  );
}

/** 로그인 상태를 따라간다 — 화면 여러 곳이 같은 답을 봐야 한다. */
export function useMe(): {
  who: Who | null;
  loading: boolean;
  refresh: () => void;
} {
  const [who, setWho] = useState<Who | null>(null);
  const [loading, setLoading] = useState(true);

  const pull = useCallback(() => {
    me()
      .then((got) => {
        // 🔴 로컬에는 실계좌가 없다 (사용자 2026-09-06: "로컬에서는 실계좌로 안 가지는 거 아냐?"). 모드를 고른 적이
        //    없는 첫 진입이 거래소 없는 API 에 닿았으면 데모로 돌린다 — 스위치를 **직접** 끈 사람은 그대로 둔다
        //    (그때는 콘솔이 "테스트넷 API 키 설정이 필요합니다" 카드를 그린다). 서버는 실계좌 모드에 GATE 가 있어 여기 안 걸린다.
        if (noRealAccount(got) && !modeChosen()) {
          switchMode("demo", { manual: false });
          return;
        }
        setWho(got);
      })
      // ⚠️ 못 물어봤으면 **모른다**로 둔다 — "로그인 안 됨" 으로 단정하면 서버가
      //    잠깐 느린 사이에 로그인 화면이 튀어나온다.
      .catch(() => setWho(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    pull();
    // 🔴 재인증 기한(1시간 · 서버 FRESH_S 가 정한다)이 지나는 것을 화면이 알아야 한다 — 주문 단추를 누른
    //    뒤에야 막히면 사람은 고장으로 읽는다. 1분마다 다시 묻는다.
    const timer = setInterval(pull, 60_000);
    return () => clearInterval(timer);
  }, [pull]);

  return { who, loading, refresh: pull };
}

/** 구글로 보낸다 — 지금 보던 화면으로 돌아온다. */
export function goSignIn(force = false): void {
  const back = encodeURIComponent(
    window.location.pathname + window.location.search,
  );
  window.location.href = `/api/auth/login?next_path=${back}${force ? "&force=1" : ""}`;
}

/**
 * 문 앞의 카드 — 참고안(apsn_knowledge_graph_app `login-card`)의 모양: 패널색 · 12px 모서리 · 세로 흐름,
 * 여기에 **미끄러져 들어오는** 진입 움직임을 더했다 (T220 · 2026-09-05). 움직임은 `motion-reduce` 에서 꺼진다.
 */
function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="auth-card w-full animate-card-in rounded-xl border border-blue-gray-100 bg-white p-7 shadow-lg shadow-blue-gray-900/5 motion-reduce:animate-none dark:border-gray-800 dark:bg-gray-900">
      <h1 className="m-0 mb-4 text-lg font-semibold text-blue-gray-900 dark:text-white">
        {title}
      </h1>
      <div className="flex flex-col gap-3">{children}</div>
    </div>
  );
}

/**
 * 로그인 전/대기 화면을 그리고, 통과하면 아이들을 보여 준다.
 *
 * @param who 로그인 상태 (`/auth/me`).
 * @param loading 아직 물어보는 중.
 */
export function Gate({
  who,
  loading,
  children,
}: {
  who: Who | null;
  loading: boolean;
  children: React.ReactNode;
}) {
  if (loading) {
    // ⚠️ 로딩 중에 로그인 화면을 그리면, 이미 로그인한 사람도 새로고침마다
    //    로그인 화면이 깜빡인다.
    return (
      <Card title="확인 중…">
        <span className="inline-flex items-center gap-2 text-sm text-blue-gray-500 dark:text-blue-gray-300">
          <span
            className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-blue-gray-100 border-t-gray-900 motion-reduce:animate-none dark:border-gray-700 dark:border-t-white"
            aria-hidden="true"
          />
          누구로 들어왔는지 서버에 묻고 있다.
        </span>
      </Card>
    );
  }

  if (who && !who.configured) {
    // 🔴 설정이 없으면 API 가 아무도 안 막는다. 그 사실을 **화면이 말한다** —
    //    조용히 열려 있는 것이 이 작업이 막으려던 상태다 (절대 규칙 #8).
    return (
      <Card title="구글 로그인이 설정되지 않았다">
        <p className="notice bad">
          🔴 <b>지금 API 는 아무도 막지 않는다.</b> 외부에 열려 있으면 URL 을
          아는 사람이 판 생성·청산을 그대로 호출할 수 있다.
        </p>
        <p className="muted">
          <code>.env</code> 의 <code>GOOGLE_CLIENT_ID</code> ·{" "}
          <code>GOOGLE_CLIENT_SECRET</code> · <code>GOOGLE_REDIRECT_URI</code> ·{" "}
          <code>SESSION_SECRET</code> 을 채우고 다시 띄운다.
        </p>
      </Card>
    );
  }

  if (!who?.signed_in) {
    return (
      // 문(門)은 단추 둘이다 — 설명 문구는 전부 뺐다 (사용자 2026-09-05). 승인 흐름은 관리 화면이 말한다.
      <div className="flex flex-col gap-2">
        <button
          type="button"
          className="group inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gray-900 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-gray-900/15 transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-xl hover:shadow-gray-900/25 active:translate-y-0"
          onClick={() => goSignIn()}
        >
          <GoogleMark />
          구글로 로그인
          <span
            className="transition-transform duration-150 group-hover:translate-x-0.5"
            aria-hidden="true"
          >
            →
          </span>
        </button>
        {/* 회원가입 = 같은 구글 흐름이다 — 처음 들어오는 계정은 서버가 자동으로 만든다(승인 대기). 다른 점은 계정 선택 창을
          강제로 띄우는 것(`force=1` → prompt=select_account): 이미 로그인된 구글 계정으로 조용히 들어가지 않게. */}
        <button
          type="button"
          className={SECONDARY}
          onClick={() => goSignIn(true)}
        >
          구글로 회원가입
          <span
            className="transition-transform duration-150 group-hover:translate-x-0.5"
            aria-hidden="true"
          >
            →
          </span>
        </button>
        {/* T221 — 게스트: 가입 없이 바로. 데모 API 가 읽기 전용 세션 + updown_mode=demo 쿠키를 굽고, 새로고침하면 전체가
          테스트넷 화면이다. 실계좌는 이 세션을 받지 않는다. 모양은 회원가입과 같은 회색 — 눈에 띄는 색은 주 동작 하나만. */}
        <button
          type="button"
          className={SECONDARY}
          onClick={() => {
            void guestLogin()
              .then(() => {
                location.href = "/console";
              })
              .catch((exc: unknown) =>
                window.alert(`게스트 입장 실패: ${String(exc)}`),
              );
          }}
        >
          게스트로 둘러보기
          <span className="text-xs font-medium text-blue-gray-400">
            Demo Trading
          </span>
          <span
            className="transition-transform duration-150 group-hover:translate-x-0.5"
            aria-hidden="true"
          >
            →
          </span>
        </button>
      </div>
    );
  }

  if (who.held) {
    // 🔴 임시 보류 (사용자 2026-09-07) — 승인 없이 하루가 지난 대기 계정. 아이들을 그리지 않는다:
    //    어차피 서버가 전부 403 이라 빈 화면에 오류만 쌓인다. 문의 카드 하나가 전부다.
    return <HoldCard who={who} />;
  }

  return <>{children}</>;
}

/**
 * 임시 보류 카드 — *"임시 보류 상태입니다. 관리자에게 문의하세요."* + 문의 단추 + 로그아웃.
 *
 * 문의는 서버가 기록하고(관리자 화면에 뜬다) SMTP 가 있으면 관리자 메일로도 간다. 메일이 못 갔으면
 * 그 사실을 여기서 말한다 — "보냈다" 고 했는데 아무도 못 받으면 사람은 기다리기만 한다.
 */
function HoldCard({ who }: { who: Who }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{
    mailed: boolean;
    recipients: number;
  } | null>(null);
  const [error, setError] = useState("");

  const send = () => {
    setBusy(true);
    setError("");
    contactAdmin(message.trim())
      .then((body) =>
        setDone({ mailed: body.mailed, recipients: body.recipients }),
      )
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(false));
  };

  return (
    <Card title="임시 보류 상태입니다">
      <p className="m-0 text-sm text-blue-gray-700 dark:text-blue-gray-200">
        관리자에게 문의하세요.
      </p>
      <p className="m-0 text-xs text-blue-gray-500 dark:text-blue-gray-400">
        {who.email} — 가입 뒤 하루 안에 승인되지 않아 접근이 보류됐다. 관리자가
        승인하거나 보류를 풀면 바로 들어올 수 있다.
      </p>
      {done ? (
        <p className="notice m-0 text-sm">
          ✅ 문의를 남겼다.{" "}
          {done.mailed
            ? `관리자 ${done.recipients}명에게 메일이 갔다.`
            : "메일은 나가지 않았다(발송 설정 없음) — 관리자 화면의 문의 목록에는 남았다."}
        </p>
      ) : (
        <>
          <textarea
            className="w-full rounded-lg border border-blue-gray-200 bg-white px-3 py-2 text-sm text-blue-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-white"
            rows={3}
            maxLength={1000}
            placeholder="관리자에게 전할 말 (선택) — 누구인지, 왜 필요한지"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
          <button
            type="button"
            className="group inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gray-900 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-gray-900/15 transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-xl hover:shadow-gray-900/25 active:translate-y-0 disabled:opacity-50"
            disabled={busy}
            onClick={send}
          >
            {busy ? "보내는 중…" : "관리자 문의"}
            <span
              className="transition-transform duration-150 group-hover:translate-x-0.5"
              aria-hidden="true"
            >
              →
            </span>
          </button>
        </>
      )}
      {error ? <p className="notice bad m-0 text-sm">{error}</p> : null}
      <button
        type="button"
        className={SECONDARY}
        onClick={() => {
          void signOut().finally(() => location.reload());
        }}
      >
        로그아웃
      </button>
    </Card>
  );
}

/** 화면 위에 늘 뜨는 한 줄 — 누구로 로그인했고 무엇을 할 수 있나. */
/**
 * 🔴 은행식 인증 타이머 (사용자 요청 2026-09-03) — 재인증 기한까지 남은 시간을
 * 우상단에 늘 보여 주고, [연장]은 구글을 한 바퀴 돌아온다 (구글 세션이 살아 있으면
 * 순간 이동). ⛔ 서버 도장만 다시 찍는 연장 API 는 만들지 않는다 — 쿠키만 쥔
 * 공격자도 무한 연장이 되어 게이트의 뜻("방금 사람이 있었나")이 죽는다.
 */
export function AuthTimer({ who }: { who: Who }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);
  if (!who.signed_in || who.fresh_until == null) return null;
  const left = Math.floor(who.fresh_until - now / 1000);
  const expired = left <= 0;
  const warn = !expired && left < 5 * 60;
  const mm = Math.floor(Math.max(left, 0) / 60);
  const ss = String(Math.max(left, 0) % 60).padStart(2, "0");
  return (
    <span
      className="link-state"
      title="돈이 움직이는 동작(주문·세션 삭제·펀드)의 재인증 기한 — 연장은 구글 재인증"
    >
      <i className={`dot ${expired ? "bad" : warn ? "warn" : "ok"}`} />
      {expired ? "인증 만료" : `인증 ${mm}:${ss}`}
      <button
        type="button"
        className="btn small"
        onClick={() => goSignIn(true)}
      >
        연장
      </button>
    </span>
  );
}

export function WhoBar({ who, onOut }: { who: Who; onOut: () => void }) {
  const label =
    who.role === "admin"
      ? "관리자"
      : who.role === "trader"
        ? "거래 허용"
        : who.role === "viewer"
          ? "승인됨 · 읽기"
          : "승인 대기 · 읽기";
  return (
    <span className="link-state" title={`${who.email} · ${label}`}>
      <i className={`dot ${who.may_trade ? "ok" : "warn"}`} />
      {who.email} · {label}
      <button
        type="button"
        className="btn small"
        onClick={() => {
          signOut()
            .then(onOut)
            .catch(() => onOut());
        }}
      >
        로그아웃
      </button>
    </span>
  );
}

/**
 * 승인 대기 안내 — **읽기는 되므로 화면을 막지 않는다** (사용자 확정 2026-08-30).
 *
 * ⚠️ 막는 대신 **왜 주문이 안 되는지**를 미리 말한다. 안 그러면 단추를 눌러 보고
 * 오류를 받은 뒤에야 알게 된다.
 */
export function PendingNote({ who }: { who: Who }) {
  if (who.may_trade) return null;
  const holdAt = who.hold_at ? new Date(who.hold_at) : null;
  const left = holdAt
    ? Math.max(0, Math.round((holdAt.getTime() - Date.now()) / 3_600_000))
    : null;
  const why =
    who.role === "pending"
      ? `가입 승인을 기다리는 중이다 — 관리자가 승인하면 목록이 열린다.${
          left != null
            ? ` 약 ${left}시간 안에 승인되지 않으면 임시 보류된다.`
            : ""
        }`
      : who.caps?.includes("demo_trade")
        ? "실계좌 주문 권한이 없다 — 데모(테스트넷)에서는 주문할 수 있다. 상단 Demo Trading 으로 바꾼다."
        : "승인은 됐지만 거래 권한이 없다 — 주문을 맡기려면 관리자가 실거래 또는 데모 거래 기능을 줘야 한다.";
  return (
    <p className="notice">
      🔒 <b>읽기 전용</b>이다. {why}
    </p>
  );
}

/**
 * 인증 때문에 막힌 것을 **셸이 한 곳에서** 받아 띄운다 (요구 ③).
 *
 * 🔴 화면마다 401 을 따로 처리하면 어딘가는 빠뜨린다. 그러면 사람은 *"보안 확인이
 * 필요하다"* 대신 알 수 없는 빨간 글씨를 보고 고장으로 읽는다 — `api.onAuthTrouble`
 * 이 모든 요청이 지나는 자리에서 잡아 준다.
 *
 * 🔴 막기만 하고 **길을 안 알려 주면 사람이 갇힌다.** 그래서 단추를 같이 둔다.
 */
export function AuthTroubleNote({ onSignedOut }: { onSignedOut: () => void }) {
  const [trouble, setTrouble] = useState<AuthTrouble | null>(null);

  useEffect(
    () =>
      onAuthTrouble((next) => {
        setTrouble(next);
        // ⚠️ 세션이 끊겼으면 셸이 **다시 물어봐야** 로그인 화면으로 돌아간다.
        //    안 그러면 폴링 주기(1분)만큼 빈 화면에 오류만 쌓인다.
        if (next.kind === "signed_out") onSignedOut();
        // 🔴 재인증은 **바로 구글로 보낸다** (사용자 2026-09-03: "에러 대신 로그인을
        //    띄워야"). 안내문+버튼만 두면 사람은 그 전에 뜬 빨간 401 본문을 고장으로
        //    읽는다. 재인증 401 은 돈이 움직이는 **사람의 클릭**에서만 오므로(읽기
        //    폴링엔 안 걸린다 — auth.py) 자동 이동이 작업을 끊을 일이 없다.
        if (next.kind === "reauth") goSignIn(true);
      }),
    [onSignedOut],
  );

  if (trouble === null) return null;
  if (trouble.kind === "signed_out") {
    return (
      <p className="notice bad">
        🔴 로그인이 풀렸다 —{" "}
        <button type="button" className="btn small" onClick={() => goSignIn()}>
          다시 로그인
        </button>
      </p>
    );
  }
  return (
    <p className="notice bad">
      🔴 <b>보안 확인이 필요하다</b> — 마지막 구글 인증이 1시간을 넘었다.
      거래소로 나가는 동작은 확인 뒤에 된다.{" "}
      <button
        type="button"
        className="btn small"
        onClick={() => goSignIn(true)}
      >
        다시 인증
      </button>{" "}
      <button
        type="button"
        className="btn small"
        onClick={() => setTrouble(null)}
      >
        닫기
      </button>
    </p>
  );
}
