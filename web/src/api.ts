/**
 * 서버 호출 — 계약을 한곳에 모은다.
 *
 * 🔴 **가격·금액은 전부 문자열이다.** 서버가 Decimal 로 다루는 값을 number 로 받으면
 * 브라우저에서 조용히 정밀도가 깎인다. 표시할 때만 포맷하고, 계산이 필요하면 서버에
 * 묻는다.
 *
 * 🔴 **모든 요청에 시한이 있다.** 없으면 폴링이 영원히 멈춘다 — 겹침 방어 깃발이 안
 * 내려가 다음 폴링이 전부 건너뛰어지고, 화면은 **조용히 얼어붙는다.** 실제로 겪었다.
 */

import type { Frame, Plan } from "./chartTypes";

const BASE = "/api";

const TIMEOUT_MS = 20_000;
/** 20초면 끊는다. 서버가 그보다 느리면 그것 자체가 알아야 할 사실이다. */

/** 마지막으로 서버와 말이 통한 시각 (ms). */
let lastOk = Date.now();

/**
 * 연결이 살아 있는가.
 *
 * ⚠️ 한 번 실패로 "끊겼다" 고 하지 않는다 — 폴링이 2초 간격이므로 15초면 **연속
 * 실패**다. 그리고 4xx·5xx 는 연결 문제가 **아니다**: 서버가 답했으면 연결은 살아 있다.
 */
export function connection(): { ok: boolean; silentFor: number } {
  const silentFor = Date.now() - lastOk;
  return { ok: silentFor < 15_000, silentFor };
}

/**
 * 인증 때문에 막혔을 때 알린다 — **한 곳에서**.
 *
 * 🔴 화면마다 401 을 따로 처리하면 어딘가는 빠뜨린다. 그러면 사람은 *"보안 확인이
 * 필요하다"* 대신 알 수 없는 빨간 글씨를 보고 고장으로 읽는다.
 *
 * ⇒ 모든 요청이 지나는 이 자리에서 잡아 셸에 알린다. 화면은 아무것도 안 해도 된다.
 */
export type AuthTrouble =
  /** 세션이 끊겼다 — 다시 로그인해야 한다. */
  | { kind: "signed_out" }
  /** 최근 인증이 필요하다 (요구 ③ · 5분). `where` 는 구글로 보낼 주소. */
  | { kind: "reauth"; where: string };

const listeners = new Set<(trouble: AuthTrouble) => void>();

/** 셸이 구독한다. 반환값을 부르면 해제된다. */
export function onAuthTrouble(fn: (trouble: AuthTrouble) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function shout(trouble: AuthTrouble): void {
  for (const fn of listeners) fn(trouble);
}

export async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = TIMEOUT_MS,
): Promise<T> {
  const control = new AbortController();
  // 오래 걸리는 것이 정상인 호출(펀드 생성 = 종목마다 판을 띄운다)은 더 긴 시한을 넘긴다.
  // 20초에 끊으면 브라우저가 연결을 닫고(nginx 499) 서버 쪽 작업까지 취소돼 롤백된다 (2026-09-05 실측).
  const timer = window.setTimeout(() => control.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: control.signal,
    });
    lastOk = Date.now();
    if (!res.ok) {
      const text = await res.text();
      if (res.status === 401) {
        // ⚠️ 401 이 둘이다 — **끊긴 세션**과 **재인증 필요**. 서버가 후자에만
        //    `reauth` 를 실어 보내므로 그것으로 가른다. 섞으면 재인증하면 될 일에
        //    사람을 로그아웃시킨다.
        let where = "";
        try {
          where = String(
            (JSON.parse(text) as { reauth?: string }).reauth ?? "",
          );
        } catch {
          // 본문이 JSON 이 아니면 끊긴 세션으로 본다.
        }
        shout(where ? { kind: "reauth", where } : { kind: "signed_out" });
      }
      // 🔴 본문까지 담아 던진다. 상태 코드만 던지면 서버가 애써 적은 이유가 사라진다.
      throw new Error(`${res.status} ${text}`);
    }
    return (await res.json()) as T;
  } catch (exc) {
    if (exc instanceof DOMException && exc.name === "AbortError") {
      throw new Error(
        `${timeoutMs / 1000}초 안에 응답이 없다 — 서버를 확인한다`,
      );
    }
    throw exc;
  } finally {
    window.clearTimeout(timer);
  }
}

const JSON_POST = { "content-type": "application/json" };

/* ══ 판(RUN) ═══════════════════════════════════════════ */

export type Summary = {
  session_id: string;
  live?: boolean;
  playbook: string;
  symbol: string;
  market?: string;
  started_at: string;
  cursor: string;
  finished: boolean;
  paused: boolean;
  running: boolean;
  /**
   * **새 진입을 받는가** (2026-08-20).
   *
   * ⚠️ `paused` 와 다르다 — 저쪽은 걸음 자체를 멈춰 손절 관리까지 멈춘다.
   */
  auto?: boolean;
  stored: boolean;
  trades: number;
  closed: number;
  wins: number;
  half_breakevens: number;
  liquidations: number;
  win_rate: number | null;
  cash: number;
  seed_cash: number;
  /** 굴리는 돈(증거금). 지갑과 **다른 돈**이다 — 백테스트는 null. */
  margin_budget?: number | null;
  leverage: number;
  return_pct: number;
  /** 금고에서 **꺼내 쓴** 총액 (T21). 어느 판이 금고를 태웠나가 여기 있다. */
  topped_up?: number;
  /** 금고로 **돌려준** 총액 (실현). 꺼낸 것과 방향이 반대다. */
  reserved?: number;
  /** 고점 대비 낙폭 % (T22). */
  drawdown_pct?: number;
  max_drawdown_pct?: number;
  /** 브레이커가 닿은 매매 id — 있으면 새 진입이 멈춰 있다. */
  tripped_at?: string | null;
  drawdown_stop_pct?: number | null;
  /**
   * 🔴 **1.2.0 인지 1.3.0 인지 가르는 유일한 값** (2026-08-30).
   *
   * 두 버전의 번들 구성원이 같아서 `playbook` 문자열로는 구별이 안 된다.
   * β = 손절을 청산거리의 몇 % 안쪽으로 당기나 · 하한 = 그보다 가까우면 안 간다.
   * 비어 있으면 **옛 설정으로 도는 판**이다.
   */
  stop_cap_ratio?: number | null;
  stop_min_pct?: number | null;
};

/**
 * 감시자가 본 이상 하나 (T20 ③).
 *
 * 🔴 **목록이 아니라 따로 온다.** 죽은 판은 *목록에 없는 것*이 증상이라, 행에 매달면
 * 바로 그 경우에 사라진다.
 */
export type Who = {
  /** 로그인했나. */
  signed_in: boolean;
  /**
   * 서버에 구글 로그인이 **설정돼 있나**.
   *
   * 🔴 거짓이면 API 가 아무도 안 막는다 — 조용히 열려 있는 것이 최악이라
   * 화면이 그 사실을 크게 말한다.
   */
  configured: boolean;
  email?: string;
  role?: "pending" | "viewer" | "trader" | "admin";
  /** 마지막 구글 인증이 기한(서버 FRESH_S) 안인가 (주문 경로가 이것을 본다). */
  fresh?: boolean;
  /** 진짜 돈이 걸린 환경(`APP_ENV=live`)인가 — 상단바가 어느 돈이 도는지 늘 보여 준다 (T220 UX 점검). */
  real_money?: boolean;
  /** 지금 답한 서버 — `live`(실계좌) 또는 `demo`(테스트넷 데모 API). Demo Trading 표시가 본다 (T221). */
  mode?: Mode;
  /** 게스트(구글 없이 들어온 익명 · 읽기 전용)인가. 게스트는 데모 고정이라 스위치가 잠긴다. */
  guest?: boolean;
  /** 재인증 기한 시각 (epoch 초) — 우상단 인증 타이머가 그린다. */
  fresh_until?: number;
  /** 답한 API 가 붙어 있는 거래소. **빈 목록 = 실계좌가 없는 로컬 API** (2026-09-06). */
  exchanges?: string[];
  may_trade?: boolean;
  /** 시장 갈래별 권한 (T242) — 화면이 스위치·판 시작 칸을 잠근다. 옛 서버는 없다(잠그지 않는다). */
  markets?: Record<
    string,
    { view: boolean; backtest: boolean; trade: boolean }
  >;
  /** 기능별 권한 (2026-09-07) — 묶음 ∪ 개별. 화면은 이것으로 단추를 켜고 끄고, 판정은 서버가 다시 한다. */
  caps?: string[];
  /** 권한 묶음 이름 (`guest` · `viewer` · `trader` · `admin` · `super_admin` · 관리자가 만든 것). */
  collection?: string;
  may_admin?: boolean;
  /** 권한 묶음을 만들고 고칠 수 있나 — 슈퍼 관리자. 관리자 권한을 남에게 주는 것도 이것이 있어야 한다. */
  may_roles?: boolean;
  /** 감사 권한 — 백테스트·합성 미래의 최종 손익·연차별 손익을 본다 (관리자는 늘 참). */
  may_audit?: boolean;
  /** 처지 (2026-09-07) — `held` 면 문의 카드만 그린다. 다른 창구는 서버가 403 이다. */
  standing?: "active" | "pending" | "held" | "blocked";
  held?: boolean;
  /** 승인 대기가 보류로 바뀌는 시각 (ISO) — 남은 시간을 그린다. 대기가 아니면 null. */
  hold_at?: string | null;
  /** 승인 없이 몇 시간 뒤 보류하나 (관리자 설정). */
  hold_after_hours?: number;
};

/** 보류·대기 중인 사람이 관리자에게 문의한다 — 서버가 기록하고, 메일이 설정돼 있으면 보낸다. */
export function contactAdmin(
  message: string,
): Promise<{ recorded: boolean; mailed: boolean; recipients: number }> {
  return request("/auth/contact", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
}

/** 로그인 상태를 묻는다 — **로그인 안 해도 답한다** (화면이 무엇을 그릴지 정하려면 필요). */
export function me(): Promise<Who> {
  return request("/auth/me");
}

export function logout(): Promise<void> {
  return request("/auth/logout", { method: "POST" });
}

/**
 * Demo Trading (T221) — 어느 백엔드에 물을지는 `updown_mode` 쿠키가 정한다. nginx(배포)와 vite(dev)가 같은 규칙으로
 * `demo` 면 데모 API(테스트넷 · 별도 DB)로 보낸다. 이 쿠키는 권한이 아니라 **행선지**다 — 각 서버가 자기 DB 로 판정한다.
 */
export type Mode = "demo" | "live";

export function modeCookie(): Mode {
  // 🔴 기본은 데모 — 쿠키가 `live` 라고 말할 때만 실계좌 (사용자 2026-09-07 "처음 접속은 데모로 우선").
  //    nginx.conf 의 map · vite.config.ts 의 라우터와 **같은 규칙**이어야 한다 — 어긋나면 화면은 데모라 믿는데
  //    요청은 실계좌로 가는 일이 생긴다.
  return /(?:^|;\s*)updown_mode=live(?:;|$)/.test(document.cookie)
    ? "live"
    : "demo";
}

/** 사람이 **이 탭 세션에서** 실계좌 모드를 직접 골랐나 — 그때만 실계좌 없는 API 에 머문다. */
export function modeChosen(): boolean {
  try {
    return sessionStorage.getItem(CHOSEN_KEY) === "live";
  } catch {
    return false;
  }
}

/**
 * 이 응답을 준 API 에 **실계좌가 없다** (로컬 dev 의 실계좌 모드). 서버는 실계좌 모드에 GATE 가 있어 거짓이다.
 * 첫 진입(모드를 고른 적 없음)은 데모로 돌리고, 사람이 직접 실계좌로 오면 콘솔이 키 설정 카드를 그린다.
 */
export function noRealAccount(who: Who | null): boolean {
  // ⚠️ `who.mode` 가 아니라 **쿠키**를 본다. 로컬 실계좌 API 는 APP_ENV=dev 라 mode 를 늘 "demo" 로 답한다 —
  //    그 값으로는 "내가 어느 쪽 API 에 붙었나" 를 못 가른다. 쿠키가 행선지다 (nginx·vite 가 그걸로 가른다).
  // ⚠️ APP_ENV(real_money)는 보지 않는다 — 판정은 **키로 어댑터를 얻을 수 있는 거래소가 있나** 하나다.
  //    서버 실계좌 API 에 키가 빠져도 같은 카드가 뜬다. 그게 맞다.
  return Boolean(
    who &&
    modeCookie() === "live" &&
    who.exchanges !== undefined &&
    who.exchanges.length === 0,
  );
}

const CHOSEN_KEY = "updown_mode_chosen";

/** 스위치 — 쿠키를 바꾸고 **새로고침**한다. 화면 상태 전부가 다른 서버의 것이 되므로 부분 갱신은 뜻이 없다. */
export function switchMode(
  next: Mode,
  { manual = true }: { manual?: boolean } = {},
): void {
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `updown_mode=${next}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
  // 사람이 직접 고른 것은 **이 탭 세션 안에서만** 기억한다 — 쿠키는 1년짜리라 다음 접속까지 "골랐다" 로 남고,
  // 그러면 실계좌 없는 로컬에서 첫 접근이 데모로 안 돌아간다 (사용자 2026-09-06).
  try {
    if (manual) sessionStorage.setItem(CHOSEN_KEY, next);
    else sessionStorage.removeItem(CHOSEN_KEY);
  } catch {
    // 저장소가 막혀 있으면 매번 기본 규칙(실계좌 없으면 데모)으로 간다.
  }
  location.href = "/console";
}

/** 게스트 입장 — 데모 서버가 읽기 전용 세션과 `updown_mode=demo` 쿠키를 굽는다. 뒤이어 새로고침한다. */
export function guestLogin(): Promise<{
  ok: boolean;
  mode: Mode;
  guest: boolean;
}> {
  return request("/auth/guest", { method: "POST" });
}

/** 한 사람의 한 매매법 권한 — 보기·백테스트·사용 + 묶음 기본값을 덮어썼나 (T230 · 2026-09-08). */
export type PlaybookGrantView = {
  id: string;
  label: string;
  view: boolean;
  backtest: boolean;
  trade: boolean;
  custom: boolean;
};

/** 묶음의 매매법 기본 정책 — 칸마다 "*"(전부) 또는 매매법 id 목록. */
export type PlaybookPolicy = {
  view: "*" | string[];
  backtest: "*" | string[];
  trade: "*" | string[];
};

/** 묶음의 시장 기본 정책 (T242) — 칸마다 "*"(전부) 또는 갈래(coin · domestic · foreign) 목록. */
export type MarketPolicy = PlaybookPolicy;

/** 한 사람의 한 시장 갈래 권한 (T242) — 매매법 칩과 같은 모양이라 같은 칩 컴포넌트로 그린다. */
export type MarketGrantView = PlaybookGrantView;

export type AccountRow = {
  id: string;
  email: string;
  name: string;
  picture: string;
  role: "pending" | "viewer" | "trader" | "admin" | "guest";
  blocked: boolean;
  /** 매매법별 권한 (T230) — 선언된 매매법마다 하나. */
  playbooks: PlaybookGrantView[];
  /** 시장 갈래별 권한 (T242) — 코인 · 국내주식 · 미국주식. 옛 서버는 없다. */
  markets?: MarketGrantView[];
  /** 감사 권한 — 등급과 별개로 관리자가 준다 (2026-09-06). */
  audit: boolean;
  /** 데모 거래 — 열람자에게 테스트넷 주문만 허용 (2026-09-07). 거래자·관리자에겐 표시용. */
  demo_trade: boolean;
  /** 권한 묶음 이름 (빈 문자열 = 승인 대기) · 화면 이름 · 유효 기능 · 묶음 밖 개별 기능 (2026-09-07). */
  collection: string;
  collection_label: string;
  caps: string[];
  extra_caps: string[];
  created_at: string | null;
  approved_at: string | null;
  approved_by: string;
  last_login_at: string | null;
  /** 처지 — 등급과 별개 (2026-09-07). `held` = 승인 없이 하루가 지나 막힘. */
  standing: "active" | "pending" | "held" | "blocked";
  /** 보류가 시작되는(된) 시각. */
  hold_at: string | null;
  /** 관리자가 보류를 풀어 준 기한. */
  hold_released_until: string | null;
  /** 관리자 메모. */
  note: string;
  /** 마지막 관리자 문의 시각. */
  contacted_at: string | null;
  /** 처리 안 한 문의 수. */
  contacts_open: number;
};

export type ContactRow = {
  id: string;
  email: string;
  message: string;
  created_at: string | null;
  mailed: boolean;
  handled_at: string | null;
  handled_by: string;
};

/** 가입한 사람들 — 관리자만 부를 수 있다 (서버가 막는다). `mode` 는 이 표가 어느 서버의 계정인가. */
export function accounts(): Promise<{
  rows: AccountRow[];
  now: string;
  hold_after_hours: number;
  mode: Mode;
}> {
  return request("/auth/users");
}

/** 관리자 설정 — 보류 유예(시간). */
export function holdSettings(): Promise<{
  hold_after_hours: number;
  max_hours: number;
}> {
  return request("/auth/settings");
}

export function setHoldSettings(
  hours: number,
): Promise<{ hold_after_hours: number; max_hours: number }> {
  return request("/auth/settings", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ hold_after_hours: hours }),
  });
}

/** 보류를 푼다(days > 0 · 그 날수 동안) 또는 다시 건다(days = 0). 관리자만. */
export function setAccountHold(
  email: string,
  days: number,
): Promise<AccountRow> {
  return request(`/auth/users/${encodeURIComponent(email)}/hold`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ days }),
  });
}

/** 관리자 메모. */
export function setAccountNote(email: string, note: string): Promise<unknown> {
  return request(`/auth/users/${encodeURIComponent(email)}/note`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ note }),
  });
}

/** 계정 삭제 — 차단과 다르다(다시 로그인하면 새 대기 계정이 생긴다). */
export function deleteAccount(email: string): Promise<unknown> {
  return request(`/auth/users/${encodeURIComponent(email)}`, {
    method: "DELETE",
  });
}

/** 관리자 문의 목록 — 처리 안 한 것이 먼저. */
export function contacts(): Promise<{ rows: ContactRow[]; open: number }> {
  return request("/auth/contacts");
}

export function markContactHandled(id: string): Promise<unknown> {
  return request(`/auth/contacts/${encodeURIComponent(id)}/handled`, {
    method: "POST",
  });
}

export function setAccountRole(email: string, role: string): Promise<unknown> {
  return request(`/auth/users/${encodeURIComponent(email)}/role`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export function setAccountBlocked(
  email: string,
  blocked: boolean,
): Promise<unknown> {
  return request(`/auth/users/${encodeURIComponent(email)}/blocked`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ blocked }),
  });
}

/** 견본 매매법(이동평균 교차) 근거 — 감사 권한 없이 보는 백테스트 요약 (2026-09-08). */
export type SampleSymbol = {
  symbol: string;
  market: string;
  bars_4h: number;
  start: string;
  end: string;
  years: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  total_pct: number;
  cagr_pct: number;
  mdd_pct: number;
  funding_settlements: number;
  /** [ISO 시각, 자본] — 청산마다 한 점. 시작점은 시드. */
  equity: [string, number][];
};

export type SampleEvidence = {
  generated_at: string;
  playbook: string;
  label: string;
  engine: string;
  seed_cash: number;
  rules: string[];
  symbols: SampleSymbol[];
};

export function sampleEvidence(): Promise<SampleEvidence> {
  return request("/evidence/sample", undefined, 30_000);
}

/** 기능 하나의 표시 정보 — 서버가 준다 (이름표는 서버 `CAP_LABELS` 가 단일 출처). */
export type CapInfo = { key: string; label: string; group: string };

/** 권한 묶음 — 내장 여섯 + 관리자가 만든 것. */
export type RoleCollection = {
  name: string;
  label: string;
  caps: string[];
  builtin: boolean;
  /** 매매법 기본 정책 (T230). */
  playbook_policy: PlaybookPolicy;
  /** 시장 기본 정책 (T242). 옛 서버는 없다. */
  market_policy?: MarketPolicy;
  /** 이 묶음을 가진 계정 수 — 0 이어야 지울 수 있다. */
  in_use: number;
};

/** 권한 묶음들과 기능 목록 — 관리자(권한 관리)면 읽는다. */
export function roles(): Promise<{
  collections: RoleCollection[];
  caps: CapInfo[];
}> {
  return request("/auth/roles");
}

/** 권한 묶음을 만들거나 고친다 — 슈퍼 관리자만 (서버가 막는다). */
export function saveRole(
  name: string,
  label: string,
  caps: string[],
  playbook_policy?: PlaybookPolicy,
  market_policy?: MarketPolicy,
): Promise<{
  name: string;
  label: string;
  caps: string[];
  playbook_policy: PlaybookPolicy;
  market_policy?: MarketPolicy;
}> {
  return request(`/auth/roles/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      label,
      caps,
      ...(playbook_policy ? { playbook_policy } : {}),
      ...(market_policy ? { market_policy } : {}),
    }),
  });
}

/** 한 사람의 매매법 권한을 덮어쓴다 — 보기 없는 백테스트/사용은 서버가 400 (T230). */
export function setAccountPlaybook(
  email: string,
  playbookId: string,
  grant: { view: boolean; backtest: boolean; trade: boolean },
): Promise<AccountRow> {
  return request(
    `/auth/users/${encodeURIComponent(email)}/playbooks/${encodeURIComponent(playbookId)}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(grant),
    },
  );
}

/** 한 사람의 시장 갈래 권한을 덮어쓴다 (T242). */
export function setAccountMarket(
  email: string,
  group: string,
  grant: { view: boolean; backtest: boolean; trade: boolean },
): Promise<AccountRow> {
  return request(
    `/auth/users/${encodeURIComponent(email)}/markets/${encodeURIComponent(group)}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(grant),
    },
  );
}

/** 시장 덮어쓰기를 지워 묶음 기본값으로 돌린다 (T242). */
export function clearAccountMarket(
  email: string,
  group: string,
): Promise<AccountRow> {
  return request(
    `/auth/users/${encodeURIComponent(email)}/markets/${encodeURIComponent(group)}`,
    { method: "DELETE" },
  );
}

/** 덮어쓰기를 지워 묶음 기본값으로 돌린다. */
export function clearAccountPlaybook(
  email: string,
  playbookId: string,
): Promise<AccountRow> {
  return request(
    `/auth/users/${encodeURIComponent(email)}/playbooks/${encodeURIComponent(playbookId)}`,
    { method: "DELETE" },
  );
}

export function deleteRole(name: string): Promise<unknown> {
  return request(`/auth/roles/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

/** 계정에 권한 묶음을 배정한다 (빈 문자열 = 승인 대기로). 관리 기능이 든 묶음은 슈퍼 관리자만. */
export function setAccountCollection(
  email: string,
  collection: string,
): Promise<AccountRow> {
  return request(`/auth/users/${encodeURIComponent(email)}/collection`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ collection }),
  });
}

/** 묶음 밖에 개별 기능을 더하거나 뺀다. */
export function setAccountCaps(
  email: string,
  add: string[],
  remove: string[],
): Promise<AccountRow> {
  return request(`/auth/users/${encodeURIComponent(email)}/caps`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ add, remove }),
  });
}

/** 데모 거래를 주거나 거둔다 — 열람자가 테스트넷에서만 주문할 수 있게. 관리자만. */
export function setAccountDemoTrade(
  email: string,
  demo_trade: boolean,
): Promise<unknown> {
  return request(`/auth/users/${encodeURIComponent(email)}/demo_trade`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ demo_trade }),
  });
}

/** 감사 권한을 주거나 거둔다 — 관리자만 (서버가 막는다). */
export function setAccountAudit(
  email: string,
  audit: boolean,
): Promise<unknown> {
  return request(`/auth/users/${encodeURIComponent(email)}/audit`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ audit }),
  });
}

export type Reconcile = {
  /** 마지막으로 대조가 **성공한** 시각. null 이면 아직 한 번도 못 맞춰 봤다. */
  at: string | null;
  period_s: number;
  /** 참이면 **루프가 멈췄다** — 옛 결과를 현재처럼 보여 주는 것이 최악이다. */
  stale: boolean;
  /** 신규 진입을 보류 중인 `거래소:종목` 들. */
  blocked: string[];
  /** 갈린 것의 수 (자세한 것은 배너와 `/walkforward/reconcile`). */
  findings: number;
};

export type Watch = {
  /** 판 id. */
  run: string;
  /** `no_runner` · `task_dead` · `steps_stall` · `reconcile_orphan_position`. */
  code: string;
  detail: string;
  at: string;
  /** 고아 배너에만 실린다 — *이어받기* 단추가 쓴다 (2026-09-01). */
  market?: string;
  symbol?: string;
  /** 감시자의 되살리기 결과 — `부활 실패 n/5 — 이유` · `N초 뒤 다시` · `되살렸다 → id`. 이유가 여기 있다. */
  revive?: string;
  /** 거래소 대조 결과 — 포지션·조건부 손절 유무 (죽은 판에만). */
  guard?: string;
};

export function sessions(): Promise<{
  sessions: Summary[];
  watch?: Watch[];
  /** 방향별 **동시 보유** 판 수 (T24 ③). 코인은 상관 0.7+ 라 같은 방향
   *  N판이 사실상 한 포지션 N배다 — 실측: 동시 숏 6건 = 실효 5.3판. */
  exposure?: Record<string, number>;
  /**
   * 🔴 **거래소 대조 상태** (2026-08-30). 갈린 것이 없어도 실어 보낸다 —
   * 정상일 때 화면이 조용하면 **루프가 죽어도 똑같이 조용해서** 사람이 이
   * 안전장치를 믿을 근거가 없다. `stale` 이 참이면 루프가 멈춘 것이다.
   */
  reconcile?: Reconcile;
}> {
  return request("/walkforward/sessions");
}

/**
 * **왜 들어갔는가** 한 줄 (T16 ①).
 *
 * ⚠️ `grade` 의 부호는 **매매 방향이 아니다** — 근거가 얼마나 확인됐나이고,
 * 방향은 `Trade.direction` 이 말한다. 숏 매매에도 +2 가 실린다.
 */
export type Why = {
  source: string;
  family: string;
  grade: number;
  detail: string;
  price: string | null;
};

/**
 * 순위 한 줄 — **전부 관측값이다.** 합성 점수가 없다 (T18 ②).
 *
 * ⚠️ `spread` 는 **지금 순간**의 1호가 차이다. 24시간 평균이 아니므로 조용한
 * 시간대에 재면 좁게 나온다 — 비용 판단의 근거로 쓰지 않는다.
 */
export type Book = {
  id: string;
  attribution: string;
  /** 화면 이름 (playbooks.yml label). 없으면 attribution. */
  label: string;
  version: string;
  timeframe: string;
  /**
   * **방아쇠 축** — 0.1 과 0.4 의 유일한 차이다 (T17).
   *
   * `null` 이면 진입 축에서 방아쇠가 당겨진다 (= 0.1). 목록에서 안 보이면 둘이 같아
   * 보이고, 사람이 무엇을 띄우는지 모른 채 고른다.
   */
  trigger: string | null;
  /** 라이브 세트(권장) 그룹에 올릴지 — playbooks.yml 의 recommended (2026-08-24). */
  recommended?: boolean;
  /** 어느 시장 묶음에서 도나 (T245) — 없으면 옛 서버(어디서나 보인다). */
  groups?: ("coin" | "stock")[];
  regimes?: string[];
  setups?: string[];
  primary_flags?: string[];
  /**
   * **이 매매법이 측정된 배율** (2026-08-30). RUN 띄우기의 기본값이 여기서 온다.
   *
   * 없으면 화면이 알아서 정한다. 강제가 아니라 기본값이며, 안전장치는 서버에 있다
   * (3배 초과에서 β 없는 판은 거부된다).
   */
  leverage?: number | null;
  /** 기준 백테스트 한 줄 (기간·손익·MDD) — 선택창 표기 전용, 선언(playbooks.yml)이 출처. */
  backtest_note?: string;
  /**
   * **펀드 규칙 선언** (T279 P3·V2 · 2026-09-17) — 이 매매법으로 펀드를 만들면 붙는 자리·명목·정지 규칙.
   * 생성 페이로드가 안 보내도 서버가 플레이북 선언을 그대로 쓴다 — 화면은 그 사실을 보여 주기만 한다.
   */
  fund_rules?: FundRules | null;
};

export type FundRules = {
  /** "slots" 면 자리 예산(총자본 ÷ 자리) · 그 밖은 기존 비중 배분. */
  weight_mode: string;
  slots: number;
  halt_after_stops: number;
  /** 총 명목 ÷ 자리 ≤ 이 값 (문자열 소수). null 이면 상한 없음. */
  notional_cap: string | null;
  /** 상한에 걸리면 남은 여유만큼 줄여서 진입한다 (T286). false 면 그 진입을 버린다. */
  notional_fit?: boolean;
  /** 낙폭 브레이크 — 고점 대비 at 이상 빠지면 신규 진입 크기를 scale 배로. null 이면 없음. */
  drawdown_brake?: { at: string; scale: string } | null;
};

export type Rank = {
  symbol: string;
  /** 어느 탭의 줄인가 — 서버가 `config/symbol_groups.yml` 에서 읽어 말한다 (2026-09-22). */
  group?: string;
  /** 사람이 읽을 풀이 — 티커만으로는 SKHY 가 무엇인지 모른다. 코인은 빈 문자열이다. */
  name?: string;
  /** 태그 키들(`inverse` = 숏 추종 · `leveraged` = 배수). 이름표는 응답의 `tags` 가 준다. */
  tags?: string[];
  price?: number | null;
  turnover?: number | null;
  volatility?: number | null;
  spread?: number | null;
  change?: number | null;
  /**
   * **최근 1시간 고저폭(%)** — 지금 이 종목이 움직이고 있나 (사용자 요구 2026-08-20).
   *
   * 🔴 24시간 변동성으로는 *"지금 어느 종목에 판을 띄울까"* 에 답을 못 한다. 실측:
   * ETH 17.58% 와 XRP 18.18% 가 거의 같아 보이는데, 최근 3시간은 2.83% 대 6.87% 였고
   * **매매가 난 것은 XRP 뿐**이었다.
   *
   * ⛔ 못 읽으면 null 이다. 0 으로 채우면 *"안 움직인다"* 로 읽힌다.
   */
  recent_pct?: number | null;
  /** 호가 눈금이 선언돼 판을 띄울 수 있는가 (T18 ①·④). **설정 질문**이다. */
  tradable?: boolean;
  /** 거래소가 그 계약을 안 줬다. 조용히 빼지 않는다. */
  missing?: string;
  /**
   * **나갈 수 있는가** — 주문이 나가는 곳(testnet)의 호가창 (사용자 제안 2026-08-20).
   *
   * 🔴 위 `spread` 는 **조회용 라이브 API** 값이라 SPCX 가 0.007% 로 BTC 급으로
   * 건강해 보인다 — 증거금 420 을 15시간 묶은 바로 그 계약이다.
   *
   * ⭐ `read` 와 `ok` 가 다르다. 못 읽은 것을 "괜찮다" 로 그리면 안 된다.
   */
  exit?: {
    read: boolean;
    ok: boolean;
    why: string;
    bid_gap?: number;
    ask_gap?: number;
    bid_depth?: number;
    ask_depth?: number;
  };
};

/**
 * 띄울 수 있는 종목 (2026-08-20).
 *
 * 🔴 **화면에 박아 두면 안 된다.** 종목을 넷 추가했는데 고르개에는 다섯 개 그대로
 * 떴다 — 목록이 두 벌이었고 한쪽만 고쳤기 때문이다.
 */
export type Choice = {
  symbol: string;
  label: string;
  /** 호가 눈금이 선언됐나. false 면 서버가 문 앞에서 막는다. */
  tradable: boolean;
  /** 묶음(탭) 키 · 풀이 · 태그 — 순위 표와 같은 출처다 (2026-09-22). */
  group?: string;
  name?: string;
  tags?: string[];
};

/** 종목 묶음(탭) 하나 — **서버가 선언을 읽어 말한다.** 화면은 탭 이름을 모른다. */
export type SymbolGroup = { key: string; label: string; hint?: string };

/** 태그 이름표 — 키 → 화면 글자와 풀이. */
export type SymbolTags = Record<string, { label: string; hint?: string }>;

/**
 * 띄울 수 있는 종목들 — **그 거래소의 것** (2026-09-22).
 *
 * 🔴 전에는 서버가 Gate 이름 아홉 개를 박아 두고 있었다. 지금은 거래소를 넘기면 그 거래소의
 * 눈금 선언 · 펀드 바스켓 · 도는 판에서 파생한 목록이 온다. 안 넘기면 서버가 연결된 거래소를 고른다.
 */
export function symbols(
  market?: string,
): Promise<{ rows: Choice[]; market?: string | null; groups?: SymbolGroup[] }> {
  const query = market ? `?market=${encodeURIComponent(market)}` : "";
  return request(`/exchange/symbols${query}`);
}

/**
 * 종목 순위 — **이 API 에 연결된 거래소**의 표 (2026-09-22).
 *
 * 🔴 전에는 Gate 만 알았다 — 바이낸스 테스트넷에 연결된 로컬 데모에서는 *"연결된 Gate 계정이
 * 없다"* 만 떴다. `market` 을 넘기면 그 거래소, 안 넘기면 서버가 연결된 것을 고른다.
 * 어느 거래소의 표인지는 응답의 `market` 이 말한다 — 화면이 이름을 지어내지 않는다.
 */
export function ranking(
  market?: string,
  group?: string,
): Promise<{
  rows: Rank[];
  at: string;
  note?: string;
  market?: string | null;
  /** 서버가 실제로 답한 탭 — 모르는 키를 보내면 기본 탭으로 답한다. */
  group?: string;
  groups?: SymbolGroup[];
  tags?: SymbolTags;
}> {
  const query = new URLSearchParams();
  if (market) query.set("market", market);
  if (group) query.set("group", group);
  const text = query.toString();
  return request(`/exchange/ranking${text ? `?${text}` : ""}`);
}

/**
 * **여기서 나가려면 얼마에 걸어야 하나** (사용자 요구 2026-08-20).
 *
 * 🔴 값을 기계가 정하지 않는다 — 화면이 나란히 놓고 사람이 누른다. 실측이 그 이유다:
 * SPCX 를 그때 던졌으면 -420, 표시가 아래 지정가로 기다렸더니 -74 였다.
 */
export type EscapePlan = {
  symbol: string;
  side: "롱" | "숏";
  size: number;
  entry: string;
  mark: string;
  bid: string;
  ask: string;
  /** 거래소가 받아 주는 한계가 — 이보다 불리하게는 못 건다. */
  limit: string;
  /** 권장 자리 — 반대편 1호가를 한 눈금 파고든 값. */
  suggested: string;
  /** 권장 자리의 실현 손익 (**수수료 전**). */
  realized: number;
  /** 지금 즉시 나가려면 때려야 할 값. */
  touch: string;
  /** 그 값이 거래소 규칙 안인가. 거짓이면 즉시 탈출이 불가능하다. */
  touch_ok: boolean;
  touch_realized: number | null;
  /** 전량 청산(시장가)이 통과할 것인가. 참이면 이 창이 필요 없다. */
  market_ok: boolean;
};

export function escapeView(
  symbol: string,
  market = "GATE",
): Promise<{ plan: EscapePlan | null; why?: string }> {
  return request(
    `/exchange/escape?symbol=${encodeURIComponent(symbol)}&market=${market}`,
  );
}

export function escapePlace(
  symbol: string,
  price: string,
  market = "GATE",
): Promise<{ status: string; price: string }> {
  return request("/exchange/escape", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify({ symbol, price, market }),
  });
}

export type Trade = {
  trade_id: string;
  outcome: string;
  direction: string;
  entry: string;
  exit: string | null;
  stop: string;
  /** 1차 익절 · 최종 목표 — 러너가 없을 때 차트가 계획선을 그리는 유일한 출처다. */
  first?: string;
  target?: string;
  /** 이 매매가 건 돈 대비 % — 배율이 곱해진 값이라 분모가 `margin_used` 다. */
  gain_pct: number | null;
  /**
   * **이 매매가 실제로 건 돈** (USDT · 증거금).
   *
   * `gain_pct` 와 곱하면 손익 금액이다. NULL 이면(백테스트 · 단독 판 · 옛 행) 화면은
   * **% 만** 쓴다 — 판 예산 같은 다른 돈으로 곱하면 그럴듯한 거짓 금액이 된다.
   */
  margin_used?: string | null;
  /**
   * 이 매매의 배율과 왕복 비용 비율 — **아직 열린 매매의 손익**을 화면이 잴 때 쓴다.
   *
   * 거래소 미실현이 있으면 그쪽이 먼저다(진짜 돈). 없을 때(페이퍼·데모·재생·조회 실패)
   * 원장과 같은 식으로 떨어진다: `(가격 변동% - cost_pct x 100) x leverage`.
   */
  leverage?: number;
  cost_pct?: number;
  planned_rr: number | null;
  realized_rr: number | null;
  achievement: number | null;
  half_by: string | null;
  /** 시스템인가 · 사람인가 · 거래소에서 이어받았나. 근거가 비는 이유를 가른다. */
  actor?: string;
  /**
   * 진입 근거. **비어 있는 것이 곧 결함은 아니다** — 이어받은 기록과 사람이 손으로
   * 낸 기록에는 원래 없다.
   */
  evidence?: Why[];
  opened_at: string | null;
  /** 1차 익절 체결 시각 · 청산 시각 — 차트가 **세로선**으로 찍는다. */
  half_at?: string | null;
  closed_at?: string | null;
  placed_at: string;
};

export type Dashboard = {
  /**
   * **어느 돈 모형으로 낸 값인가** (T14-1).
   *
   * 🔴 `시드 충전` 은 잔고가 시드 아래로 가면 밖에서 넣었다고 **가정**하고,
   * `지갑` 은 지갑에서만 채우고 모자라면 **멈춘다**. 재투입 규칙이 다르므로 누적
   * 손익률의 정의가 다르다 — 라벨 없이 나란히 놓으면 모형 차이가 전략 차이로 읽힌다.
   */
  funding?: string;
  /** 지갑에 남은 돈 — `지갑` 모형에서 거래소 잔액에 대응한다. */
  wallet?: number;
  /** 지갑에서 증거금으로 채워 넣은 총액. `refilled`(가정한 돈)와 다르다. */
  topped_up?: number;
  /** 금고로 빼 둔 총액 (실현). 이 판이 **돌려준** 돈이다. */
  reserved?: number;
  /** 증거금을 못 채워 멈춘 매매 id. 있으면 **그 뒤로 새 진입이 없다**. */
  halted_at?: string | null;
  /** T22 — 낙폭과 브레이커. tripped_at 이 있으면 새 진입이 멈춰 있다. */
  drawdown_pct?: number;
  max_drawdown_pct?: number;
  tripped_at?: string | null;
  drawdown_stop_pct?: number | null;
  trades: number;
  closed: number;
  wins: number;
  win_rate: number | null;
  return_pct: number;
  cash: number;
  seed_cash: number;
  leverage: number;
  half_breakevens: number;
  liquidations: number;
};

export type State = {
  session_id: string;
  /** 이 판의 종목 — 서버는 처음부터 보냈는데 선언에만 없었다 (2026-09-21에 맞췄다). */
  symbol: string;
  cursor: string;
  finished: boolean;
  paused: boolean;
  timeframes: string[];
  dashboard: Dashboard;
  log: Trade[];
  /** 요청한 시간축 하나만 온다 — 다섯을 다 그리면 응답마다 작도가 다섯 벌 돈다. */
  frames: Frame[];
  /**
   * **플레이북이 지금 도는가, 안 돌면 무엇 때문인가.**
   *
   * 🔴 서버는 `session.look()` 을 그대로 불러 채운다 — **화면용 근사가 아니라 실제
   * 판정**이다. 차트 레이어 note 가 경고하는 *"실전과 다르다"* 는 레이어 얘기이고
   * 이 블록에는 해당되지 않는다.
   *
   * ⚠️ `active=false` 면 방아쇠 띠를 그려도 로직은 그 자리를 안 기다린다.
   */
  gate?: {
    active: boolean;
    found: boolean;
    proposals: number;
    has_box: boolean;
    entry_trend: string | null;
    /** 국면 헤드라인 — 진입 축 한 단계 위의 주 추세. 판정 전이면 null. */
    major: string | null;
    /** 자리는 있었는데 **진입 보류**로 막힌 후보 수 (T26 ②). */
    blocked: number;
    /** 왜 막았나 — 걸린 규칙 설명 (최대 3개). */
    blocked_why: string[];
    trend: Record<string, string>;
    running: string[];
    regimes: string[];
  };
  /** 보유 중인 계획. 없으면 null — 차트가 계획선을 안 그린다. */
  position: Plan | null;
  /**
   * 추세추종(full_ride)인가 — 참이면 고정 익절이 없다(목표선은 100R 자리표시자).
   * 차트가 목표·1차선을 숨기고 손절을 "청산" 으로 그리게 한다.
   */
  full_ride?: boolean;
};

export function state(key: string, frame?: string): Promise<State> {
  return request(`/walkforward/state/${key}${frame ? `?frame=${frame}` : ""}`);
}

/* ══ 라이브 ════════════════════════════════════════════ */

/**
 * **이 RUN 이 무엇인가** — 머리말이 쓰는 값들.
 *
 * 🔴 **눈금이 여기 있어야 한다.** 2026-08-18 사고의 원인이 *"원화 상수 500 이 Gate 에서
 * 가격의 0.78% 였다"* 인데, 그 값이 화면 어디에도 없어서 숏 계획이 21건 중 1건만 서는
 * 것을 봉 단위로 파고들 때까지 몰랐다 (T18 ①).
 *
 * ⚠️ **축이 셋이다.** 판정(구조물) · 방아쇠 · 진입가 — 서로 다른 것이고, 같은 값으로
 * 쓰면 화면이 거짓말한다.
 */
export type RunInfo = {
  id: string;
  key: string;
  resumed: boolean;
  /** 🔴 **주문을 한 건도 안 내는 판.** 손익은 원장의 모형이라 실주문 판과 못 비교한다. */
  observe_only?: boolean;
  market: string;
  symbol: string;
  playbook: string;
  judge_frame: string;
  trigger_frame: string;
  price_frame: string;
  leverage: string;
  margin_budget: string | null;
  seed_cash: string;
  funding: string;
  /** 지금 쓰는 호가 눈금. 못 읽으면 null — 0 이 아니다. */
  tick: string | null;
  /** 거래소 최소 호가 (`order_price_round`). */
  spec_tick: string | null;
  /** 눈금 배율 (얼린 상수). */
  tick_ratio: string;
  /** 가격 대비 눈금 비율(%) — 굵은지 얇은지는 이 값이 말한다. */
  tick_pct: string | null;
  price: string | null;
  started_at: string;
};

export type Health = {
  session_id: string;
  /** 🔴 봉이 느는데 이 값이 0 이면 판정이 안 도는 것이다. */
  steps: number;
  /**
   * **판정 축** — 플레이북이 정한 진입 시간축.
   *
   * 🔴 **차트에서 고른 축이 아니다.** 화면이 차트 축으로 "언제 판정하나"를 적었더니
   * 10초봉을 보는 동안 *"첫 10초 봉이 마감되면 돈다"* 고 말했다 — 90배 틀린 말이고,
   * 정상 대기 중인 판이 고장난 것처럼 보였다.
   */
  entry?: string;
  /** 다음 판정 시각 — 지금 자라는 진입 축 봉이 마감되는 때. */
  next_judge_at?: string;
  /**
   * **이 판이 새 판인가, 이어받은 판인가** (T16 ②).
   *
   * 🔴 이것을 모르면 `steps` 가 0 인데 매매가 12건인 상태를 **고장으로 읽는다** —
   * 실제로는 "옛 판을 이어받았고 아직 새 봉을 안 봤다" 이며 완전히 정상이다.
   */
  run?: RunInfo;
  /**
   * 다른 판이 지갑을 쓰고 있어 **예산보다 적게** 주문한 흔적 (다중 RUN ㄷ).
   *
   * ⚠️ 선착순은 **규칙이지 버그가 아니다** — 다만 말하지 않으면 사람은 주문이
   * 작아진 것을 전략 문제로 오해한다.
   */
  squeezed?: { budget: string; usable: string; note: string } | null;
  /**
   * 판을 띄우며 **치운 잔재** (2026-08-21).
   *
   * 🔴 주인 없는 조건부는 새 포지션을 통째로 닫으므로 묻지 않고 거둔다 — 답이 하나뿐인
   * 질문을 시작 순간에 띄우면 사람은 읽지 않고 누른다. 대신 **무엇을 거뒀는지**는
   * 반드시 보여야 한다. 조용히 지우는 것과 조용히 넘어가는 것은 다르다 (규칙 #8).
   */
  swept?: string[];
  /**
   * 안전장치가 **실제로 발동한 기록** — `{이름: {count, at, why}}`.
   *
   * 🔴 안 도는 방어선은 없는 것과 같다. 코드와 테스트만으로는 그것을 알 수 없다 —
   * 오늘 나온 결함 넷 중 단위 테스트가 잡은 것은 0개였다.
   *
   * ⚠️ 0 인 것이 나쁘다는 뜻은 아니다 — 발동할 일이 없었다는 뜻일 수도 있다.
   */
  guards?: Record<string, { count: string; at: string; why: string }>;
  orders: number;
  gaps: number;
  reconnects: number;
  running: boolean;
  pending_bar: string | null;
  /** 판정·주문이 터진 횟수. */
  failures?: number;
  last_error?: string;
  /**
   * 러너가 **스스로 점검한** 결과.
   *
   * 🔴 비어 있는 것이 정상이다 — 하나라도 있으면 판정을 믿으면 안 된다.
   */
  findings?: { code: string; level: string; detail: string }[];
  /**
   * **자동 점검 결과** — 30초마다 러너가 스스로 돈다.
   *
   * 🔴 누르기 전에는 모르는 상태가 없어야 한다. 버튼은 *"지금 당장"* 용으로 남는다.
   */
  probe?: Probe | null;
  /** 매매 id → 거래소 주문 흔적. 원장이 보유중인데 여기 없으면 유령이다. */
  placed?: Record<
    string,
    { order_id: string; status: string; contracts: string; error?: string }
  >;
  frame_ages?: Record<string, number>;
  exchange?: {
    available?: string;
    position_margin?: string;
    broker?: string;
    position?: Record<string, string>;
    error?: string;
  };
};

export function health(key: string): Promise<Health> {
  return request(`/walkforward/live/${key}`);
}

/**
 * 금고 전역 설정 (T21 ⑦).
 *
 * 🔴 **모든 판에 같이 걸린다.** 금고는 하나인데 판마다 다른 상한을 쓰면 판 셋이
 * 각자 태워 합이 세 배가 된다.
 */
/**
 * 금고 한도 하나.
 *
 * ⭐ `refill_cap` 은 **금액(`"300"`) 이거나 비율(`"30%"`)** 이다 (사용자 요구
 * 2026-08-20). 칸을 둘로 나누지 않는다 — 둘 다 채워진 상태가 생기면 어느 쪽이
 * 이기는지를 화면이 설명할 수 없다.
 */
export interface Cap {
  refill_cap: string;
  unit: "amount" | "percent";
}

export function vault(): Promise<Cap> {
  return request("/walkforward/vault");
}

export function setVault(refill_cap: string): Promise<Cap> {
  return request("/walkforward/vault", {
    method: "PUT",
    headers: JSON_POST,
    body: JSON.stringify({ refill_cap }),
  });
}

/**
 * 주인 없는 잔재 하나 — **어느 판도 맡지 않는** 종목의 남은 것들.
 *
 * 🔴 `kind` 가 갈림을 나른다. `주문` 이면 답이 하나라(거둔다) 단추 하나면 되고,
 * `포지션` 이면 닫는 순간 **손익이 확정**되므로 사람이 정해야 한다.
 */
export interface Leftover {
  symbol: string;
  kind: "주문" | "포지션";
  position: Record<string, string> | null;
  orders: {
    id: string;
    kind: string;
    at: string;
    size: string;
    reduce_only: string;
  }[];
}

export function leftovers(): Promise<{ rows: Leftover[]; owned: string[] }> {
  return request("/walkforward/leftovers");
}

export function sweepLeftovers(symbol: string): Promise<{ swept: string[] }> {
  return request(`/walkforward/leftovers/${symbol}`, { method: "POST" });
}

/**
 * **지금 즉시 거래소와 대조한다** — 30초 주기를 안 기다린다 (2026-09-01).
 *
 * 콘솔의 *지금 동기화* 단추가 쓴다. 원장과 거래소가 갈렸는지 바로 다시 재고 결과를
 * 낸다 (`refresh=true`). 신규 진입 보류·고아 배너가 즉시 갱신된다.
 */
export function syncReconcile(): Promise<{
  findings: unknown[];
  blocked: string[];
}> {
  return request("/walkforward/reconcile?refresh=true");
}

/**
 * 고아 포지션을 **살아 있는 판이 다시 이어받게** 한다 (2026-09-01).
 *
 * 🔴 매매법을 바꾸느라 판을 지웠다 새로 만들면 거래소 포지션이 남는데, 자동
 * 이어받기는 기동 때 한 번만 돈다. 이 창구가 그것을 다시 부른다 — 청산이 아니라
 * **되받기**라 손익을 확정하지 않는다.
 */
export function adoptOrphan(
  market: string,
  symbol: string,
): Promise<{ adopted: string; reason: string; findings: unknown[] }> {
  return request("/walkforward/adopt", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify({ market, symbol }),
  });
}

export function startLive(body: Record<string, unknown>): Promise<State> {
  return request("/walkforward/live", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(body),
  });
}

export function dropSession(key: string): Promise<unknown> {
  return request(`/walkforward/sessions/${key}`, { method: "DELETE" });
}

/* ══ 이메일 리포트 (T35) ══════════════════════════════ */

export type ReportPreview = {
  window: { since: string; until: string };
  /** 평문 본문 — 메일로 나가는 그대로. 집계만 있다. */
  body: string;
  /**
   * **메일에 실제로 실리는 HTML** — 차트·주문표·계좌 요약 (T55).
   *
   * 🔴 예전에는 미리보기가 평문만 받아서 **화면이 메일과 다른 것**을 보여 줬다.
   * 거래소 범위 발송처럼 HTML 을 못 만드는 조합에서는 `null` 이다.
   */
  html: string | null;
  /** 원장과 거래소의 손익 부호가 다르면 참 — 한쪽은 틀렸다. */
  diverged: boolean;
  trades: number;
  /** SMTP 설정이 다 있나. 없으면 보내기가 안 산다. */
  configured: boolean;
  default_to: string[];
};

/** `symbol` 을 주면 **그 판만**(판 이메일), 없으면 전체 취합(콘솔 이메일). 발송(`reportSend`)과 같은 길. */
export function reportPreview(
  hours: number,
  symbol?: string,
): Promise<ReportPreview> {
  const scope = symbol ? `&symbol=${encodeURIComponent(symbol)}` : "";
  return request(`/report/preview?hours=${hours}${scope}`);
}

/**
 * 리포트 대시보드 재료 (T220 2단계) — **메일과 같은 숫자**를 JSON 으로. 돈은 문자열(Decimal)이다.
 *
 * ⚠️ 못 읽은 값은 `null` — 화면은 "—" 로 그린다. 0 으로 꾸미지 않는다 (규칙 #8).
 */
export type ReportDashboard = {
  window: { since: string; until: string };
  ledger: {
    trades: number;
    longs: number;
    shorts: number;
    wins: number;
    win_rate: string | null;
    /** 건별 손익률 합 (증거금 기준 · 배율 반영). 복리가 아니다. */
    gain_sum_pct: string | null;
    mean_rr: string | null;
    max_drawdown_pct: string | null;
    by_outcome: Record<string, number>;
    by_playbook: Record<string, number>;
    by_symbol: Record<string, number>;
  };
  /** 거래소가 말하는 손익 — 못 읽으면 null (원장과 나란히 둔다). */
  exchange: {
    pnl: string | null;
    fees: string | null;
    funding: string | null;
    rows: number;
  } | null;
  diverged: boolean;
  note: string;
  account: {
    total: string | null;
    available: string | null;
    locked: string | null;
    realized: string | null;
    unrealized: string | null;
    vault: string | null;
    runs: number;
  } | null;
  /** 리밸런싱 펀드 카드 — 계좌와 판 사이의 중간 층 (2026-09-07). 파일 스냅샷 기준. */
  funds: {
    fund_id: string;
    label: string;
    market: string;
    playbook: string;
    balance: string;
    twr_pct: string;
    drawdown_pct: string;
    max_drawdown_pct: string;
    money_gain: string;
    symbols: string[];
    run_keys: string[];
  }[];
  runs: {
    key: string;
    symbol: string;
    playbook: string;
    fund: string | null;
    gain_pct: string | null;
    takes: number;
    stops: number;
    trades: number;
  }[];
  curve: { at: string; symbol: string; gain_pct: string; cum_pct: string }[];
  /** 자산 시계열 — 달마다 마지막 스냅샷 (KST 달 경계). 배포 뒤부터 쌓인다. */
  equity_monthly: {
    at: string;
    total: string | null;
    available: string | null;
    locked: string | null;
    unrealized: string | null;
    vault: string | null;
    funds: Record<string, string>;
  }[];
  configured: boolean;
  default_to: string[];
};

export function reportDashboard(hours: number): Promise<ReportDashboard> {
  return request(`/report/dashboard?hours=${hours}`);
}

/** 금고 **잔고** (스킴) — 살아 있는 판들의 reserved 빼기 withdrawn 합. 못 읽으면 vault=null (T55). */
export function vaultBalance(): Promise<{
  vault: string | null;
  runs: number;
}> {
  return request("/report/vault");
}

export function reportSend(body: {
  hours?: number;
  since?: string;
  until?: string;
  to?: string[];
  /** 있으면 **그 판만** (판 이메일), 없으면 전체 취합 (콘솔 이메일) — T55. */
  symbol?: string;
  /** 거래소 범위 (2026-08-26) — GATE/BINANCE. 없으면 전체(거래소별 절 포함). */
  market?: string;
}): Promise<{ ok: boolean; body: string; html?: boolean }> {
  return request("/report/send", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(body),
  });
}

/**
 * 도는 판의 **새 진입을 멈추거나 다시 켠다** (2026-08-20).
 *
 * 🔴 **일시정지가 아니다.** `paused` 는 걸음 자체를 멈춰 손절 재장착·거래소 대조·
 * 반익 집행까지 같이 멈춘다 — 포지션을 든 채로 걸면 아무도 안 지키는 포지션이 된다.
 *
 * ⭐ 이쪽은 **새 진입만** 막는다. 이미 든 것은 계속 관리된다.
 */
export function setAuto(key: string, on: boolean): Promise<{ auto: boolean }> {
  return request(`/walkforward/live/${key}/auto`, {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify({ on }),
  });
}

export type Probe = {
  exchange_latest: string | null;
  chart_latest?: string | null;
  feed_cursor: string;
  lag_seconds: number | null;
  frame: string;
  verdict: string;
  streaming: boolean;
};

/**
 * **지금 만들어지고 있는 봉** — 꼬리가 흔들리는 것을 보려고 따로 당긴다.
 *
 * 🔴 `/state` 는 봉 800개 + 도형을 싣고 와서 초 단위로 칠 수 없다. 이 응답은 봉
 * **하나**다.
 *
 * ⚠️ `bar` 가 `null` 이면 **아직 못 받은 것**이다 — 마지막 마감 봉으로 메우지 않는다.
 */
export type Tick = {
  frame: string;
  at: string;
  bar: {
    ts: string;
    open: string;
    high: string;
    low: string;
    close: string;
    volume: string;
  } | null;
};

export function tick(key: string, frame?: string): Promise<Tick> {
  return request(
    `/walkforward/live/${key}/tick${frame ? `?frame=${frame}` : ""}`,
  );
}

/**
 * **판 없이** 지금 만들어지는 봉 하나 — 차트 주문이 쓴다.
 *
 * @param params 종목·거래소·축.
 *
 * 🔴 위의 `tick` 은 **판이 있어야** 부를 수 있다 (러너에게 묻는다). 차트 주문에는 판이
 * 없어서, 꼬리가 자라는 것을 보여 줄 방법이 아예 없었다 — 마감된 봉만 축 주기마다
 * 갈아 끼워서 *"10초마다 받아오는 느낌"* 이 났다 (사용자 신고 2026-08-30).
 *
 * ⚠️ 서버가 0.7초 기억통을 들고 있다 — 화면이 얼마나 자주 불러도 거래소로는 그보다
 * 자주 안 나간다.
 */
export function analysisTick(params: {
  symbol: string;
  market: string;
  timeframe: string;
}): Promise<Tick> {
  const query = new URLSearchParams(params);
  return request(`/analysis/tick?${query.toString()}`);
}

export function setLeverage(
  key: string,
  leverage: number,
): Promise<{ leverage: string }> {
  return request(`/walkforward/live/${key}/leverage`, {
    method: "POST",
    // 🔴 **`content-type` 이 없으면 422 다** (2026-08-19 실측). FastAPI 가 본문을 dict
    //    가 아니라 **문자열**로 읽어 `Input should be a valid dictionary` 를 낸다 —
    //    JSON 을 보냈는데 JSON 이라고 말하지 않은 것이다.
    headers: JSON_POST,
    body: JSON.stringify({ leverage }),
  });
}

export function probe(key: string): Promise<Probe> {
  return request(`/walkforward/live/${key}/probe`, { method: "POST" });
}

export function playbooks(): Promise<{
  playbooks: Book[];
  /** 라이브 배선이 가능한 거래소들 — 서버가 어댑터 계약(QuoteAdapter)에서 파생 (T63 §2c). */
  live_markets: string[];
}> {
  return request("/walkforward/playbooks");
}

/* ══ 거래소 ════════════════════════════════════════════ */

export type Exchange = {
  symbol: string;
  /** 이 상태가 어느 거래소 것인가 (2026-08-26 — 거래소별 콘솔). */
  market?: string;
  account: {
    user_id: string;
    tier: string;
    ip_whitelist: string;
    margin_mode: string;
    /** "true" 테스트넷 · "false" 실계좌 · "" 모름 */
    testnet: string;
    /** 계정 상세를 못 읽었을 때만 (키에 Account 권한이 없는 경우 등) — 나머지 상태는 그대로 온다 */
    error?: string;
  };
  balance: {
    broker: string;
    available: string;
    account_position_margin: string;
    /** 계좌 총액 (거래소 값) — 어댑터가 말할 수 있을 때만 (2026-09-05). */
    total?: string;
    /** 대기 지정가가 잡아 둔 증거금 — 취소되면 available 로 돌아온다. */
    order_margin?: string;
    /**
     * 테이커 편도 수수료 — **분수**다 (`0.0005` = 0.05%). 이름이 단위를 말한다: `_pct` 로
     * 적었다가 화면이 100 으로 한 번 더 나눠 수수료가 0 이 된 적이 있다 (2026-09-19).
     *
     * 실측 대조: 명목 163.7 x 0.0005 = 0.08185 vs 장부 0.081567.
     */
    taker_rate?: string;
  };
  /** 비어 있으면 포지션이 없다. **고른 종목의 것**이다 — 전체는 `positions`. */
  position: Record<string, string>;
  /**
   * 🔴 **열려 있는 모든 포지션** (2026-08-20). 비어 있는 종목은 안 온다.
   *
   * 판을 여러 종목에서 돌리면 `position`(고른 종목) 하나만 보고 *"포지션 없음"* 이라
   * 읽게 된다 — 실제로는 다른 계약에 1408 계약이 열려 있었다.
   */
  positions: (Record<string, string> & { symbol: string })[];
  orders: {
    /** 🔴 어느 계약의 주문인가. 없으면 화면이 남의 종목을 내 것으로 읽는다. */
    symbol?: string;
    id: string;
    size: string;
    left: string;
    price: string;
    text: string;
    status: string;
    create_time: string;
  }[];
  /** 🔴 **체결 이력.** 시장가는 즉시 체결돼 미결에 안 남는다 — 이게 없으면 흔적이 없다. */
  history: {
    /** 실현 손익 — **줄이는 주문에만** 있다. 진입에는 없는 것이 맞다. */
    pnl?: string;
    /**
     * **증거금 대비 수익률(%)** — 수수료가 들어간 값이다 (사용자 신고 2026-08-20).
     *
     * 🔴  과 **같은 근거**에서 나온다. 가격 변동률을 쓰면 부호가 갈려
     * *"손익은 마이너스인데 어떻게 돈을 번 거냐"* 가 된다.
     */
    gain_pct?: string;
    /** 가격이 움직인 폭(%) — **수수료 전**. 둘의 차이가 곧 비용이 먹은 몫이다. */
    move_pct?: string;
    /** 이름이 아니라 **시각 근접으로 추정해** 붙인 손익이다. 있으면 화면이 밝힌다. */
    pnl_guessed?: string;
    /** 그 주문을 낸 RUN 의 배율. 거래소에는 없어 우리 쪽에서 찾는다 — 없으면 빈칸. */
    leverage?: string;
    id: string;
    size: string;
    left: string;
    price: string;
    fill_price: string;
    text: string;
    status: string;
    finish_as: string;
    is_reduce_only: string;
    finish_time: string;
    /** 어느 계약의 체결인가. 판을 지워도 남는 **유일한 종목 출처**다. */
    symbol?: string;
    /** 어느 거래소의 체결인가 — 전체 범위 병합 때 화면이 붙인다 (2026-08-26). */
    market?: string;
  }[];
  /**
   * 체결 한 줄이 **어느 계획에서 나왔나** — 매매 id 앞자리로 찾는다.
   *
   * 🔴 거래소 행에는 체결가와 실현 손익뿐이다. 손절선·1차 익절·목표는 우리 원장에만
   * 있고, 판을 지워도 남는다 (지우는 것은 닫는 것이지 행을 지우는 것이 아니다).
   */
  plans: Record<string, TradePlan>;
  /** 판 표식 6자 → 그 판이 무엇이었나. **닫힌 판도 온다.** */
  runs: Record<string, RunTag>;
  stops: {
    /** 🔴 어느 계약을 지키는 손절인가. 이것이 없어서 "조건부 0건" 을 무방비로 읽었다. */
    symbol?: string;
    id: string;
    trigger_price: string;
    /** 🔴 Gate 조건부는 24시간에 **조용히** 사라진다. */
    expiration: string;
    text: string;
    create_time: string;
  }[];
};

/** 체결 한 줄 뒤의 **계획과 시각** (사용자 요구 2026-08-20).
 *
 * ⚠️ 차트의 (chartTypes)과 이름이 겹쳐 따로 부른다 — 둘은 다른 것이다.
 */
export type TradePlan = {
  trade_id: string;
  symbol: string;
  playbook: string;
  direction: string;
  outcome: string;
  leverage: string;
  entry: string;
  stop: string;
  first: string;
  target: string;
  exit: string | null;
  half_price: string | null;
  half_by: string | null;
  placed_at: string | null;
  opened_at: string | null;
  half_at: string | null;
  closed_at: string | null;
};

/** 판 표식이 가리키는 판 — **닫혀도 남는다.** */
export type RunTag = {
  key: string;
  symbol: string;
  playbook: string;
  leverage: string;
  alive: boolean;
};

export function exchange(
  symbol = "BTC_USDT",
  market = "GATE",
): Promise<Exchange> {
  return request(
    `/exchange/state?symbol=${encodeURIComponent(symbol)}&market=${market}`,
  );
}

export function closePosition(
  symbol = "BTC_USDT",
  market = "GATE",
): Promise<unknown> {
  return request("/exchange/close", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify({ symbol, market }),
  });
}

export function cancelOrder(
  id: string,
  symbol = "BTC_USDT",
  market = "GATE",
): Promise<Exchange> {
  return request(
    `/exchange/orders/${encodeURIComponent(id)}?symbol=${symbol}&market=${market}`,
    {
      method: "DELETE",
    },
  );
}

export function cancelStop(
  id: string,
  symbol = "BTC_USDT",
  market = "GATE",
): Promise<Exchange> {
  return request(
    `/exchange/stops/${encodeURIComponent(id)}?symbol=${symbol}&market=${market}`,
    {
      method: "DELETE",
    },
  );
}

/* ══ 환율 ══════════════════════════════════════════════ */

export type Fx = { usdt_krw: string | null; source: string };

export function fx(): Promise<Fx> {
  return request("/walkforward/fx");
}

// ── 리밸런싱 펀드 (T61) — RUN 과 분리된 능동형 인덱스 ───────────────
export type FundLeg = {
  handle: string;
  equity?: string;
  weight?: string;
  realized?: string;
  unrealized?: string;
  /** 거래소가 이 종목 포지션에 실제로 잡고 있는 증거금 — `equity`(예산+손익)와 다르다. */
  margin?: string;
  holding?: boolean;
  missing?: boolean;
  /** 거짓이면 **포지션이 갈렸다**(고아·유령·무방비) — 손익 미확정 (2026-09-01). */
  reconciled?: boolean;
  /** 거짓이면 **실현손익 회계가 거래소와 부호 반대**(pnl_sign_split) — 동결 격리됨. */
  accounting_ok?: boolean;
  /** 갈렸을 때 거래소 실측으로 귀속된 **진짜 실현손익** — 화면이 원장 허구 대신 이걸 그린다. */
  verified_realized?: string | null;
  position?: {
    side: string;
    entry: string;
    target: string;
    stop: string;
    /** 진입 시각 (ISO) — 없으면 상자를 안 그린다. 가로선만으로는 "언제부터" 를 못 그린다. */
    opened_at?: string | null;
    /**
     * 참이면 **고정 익절선이 없다** — `target` 은 진입+100R 짜리 자리표시자다.
     * 화면은 그 값을 '목표' 로 적지 않는다 (적으면 "218만 달러 대기" 처럼 거짓말한다).
     */
    full_ride?: boolean;
  };
};

export type FundStatus = {
  fund_id: string;
  /** 펀드의 거래소 (T62 P3b) — 모든 세션이 이 거래소로 나간다. */
  market?: string;
  label: string;
  playbook: string;
  basket: string;
  symbols: string[];
  leverage: string;
  balance: string;
  twr_return: string;
  /** 펀드 전체 낙폭 — **TWR 지수** 고점 대비 % (입출금 무관 · 백테스트 MDD 와 같은 자). */
  drawdown_pct?: string;
  max_drawdown_pct?: string;
  /**
   * 🔴 **거래소와 갈린 종목들** (2026-09-01). 비어 있지 않으면 위 잔고·TWR 은 아직
   * 거래소가 확인하지 않은 손익을 담고 있다 — 화면이 헤드라인에 경고를 그린다.
   */
  mismatch?: string[];
  next_tick?: string | null;
  /**
   * 자동 앵커 (T285 · 2026-09-17) — 펀드가 틱마다 거래소 계좌에 총자본을 맞춘다. `mode` account = 계좌가
   * 곧 펀드 · drift = 계좌 안에 펀드 밖 유휴 현금(idle)이 있다. `at` 는 마지막 앵커 시각. 없으면 아직
   * 앵커 전(다른 펀드·단독 판이 같은 거래소를 써서 못 정하면 `skipped` 에 이유).
   */
  anchor?: {
    mode: string;
    at?: string | null;
    idle?: string;
    skipped?: string | null;
  } | null;
  per_symbol: Record<string, FundLeg>;
};

/** 거래소별 잔액 — 콘솔 상단 카드 (T63 §2c 파생 · 사용자 요구 2026-08-26). */
/** 이 API 가 붙어 있는 거래소 — 콘솔은 이 목록만 묻는다. 빈 목록 = 연결 없음 (2026-09-06). */
/** 시장 한 줄 — `group`/`broker` 는 T245(2026-09-09)부터 서버가 준다. 없으면 옛 서버(전부 코인으로 본다). */
export type MarketInfo = {
  name: string;
  ready: boolean;
  scoped: boolean;
  group?: "coin" | "stock";
  broker?: string;
  /** 능력표(T238) — 없으면 옛 서버(코인처럼 그린다). */
  leverage?: boolean;
  short?: boolean;
  funding?: boolean;
  always_open?: boolean;
  /** 재무 출처(EDGAR)가 있는 시장인가 — 저평가 카드는 이 시장만 부른다 (T243·T244). */
  fundamentals?: boolean;
};

/** 장 시간 배지 값 (T245 · `/exchange/market-status`). 시각은 ISO(UTC) — 화면이 사람 시간대로 보여 준다. */
export type MarketStatusView = {
  market: string;
  always_open: boolean;
  state: "open" | "closed" | "unknown";
  why: string;
  session: string;
  next_open: string | null;
  next_close: string | null;
};

/** 저평가 후보 줄 (T244 · `/fundamentals/ranking`). 가격·시총은 문자열, 비율·점수는 숫자. */
export type ValueRow = {
  symbol: string;
  /** 상장 시장. 전체·SP 500 범위에서 행마다 다르다 — 시장을 모르면(AMEX) null · 주문·이력 받기 불가. */
  market?: string | null;
  /** 한글 이름(토스 이름표). 없으면 null. */
  name?: string | null;
  broker?: string | null;
  /** 공시를 받았나. 거짓이면 점수 없이 뒤에 선다 — 0점이 아니다. */
  has_facts: boolean;
  price: string | null;
  price_date: string | null;
  market_cap: string | null;
  /** 저평가 점수 0~100 (쌀수록 100). 없으면 null. */
  score: number | null;
  cheapness: number | null;
  flags: string[];
  metrics: Record<string, { value: number | null; percentile: number | null }>;
  /** 60일 수익(비율 · 0.12 = 12%). 봉이 모자라면 null. */
  momentum_60d: number | null;
  history_points: number;
  latest_filing: { form: string; filed_at: string; url: string | null } | null;
  why: string;
};

export function valueRanking(market: string): Promise<{
  rows: ValueRow[];
  at: string;
  market: string;
  label: string;
  recommended: boolean;
  window_days: number;
  note: string;
}> {
  return request(`/fundamentals/ranking?market=${encodeURIComponent(market)}`);
}

/** 한 종목의 재무 표 (T243 · `/fundamentals/{symbol}`). */
export type FundamentalsView = {
  symbol: string;
  as_of: string;
  price: string | null;
  price_date: string | null;
  market_cap: string | null;
  latest_filed_at: string | null;
  history_points: number;
  notes: string[];
  metrics: Array<{
    key: string;
    label: string;
    group: "price" | "debt" | "earning" | "dilution";
    unit: "x" | "%";
    value: number | null;
    percentile: number | null;
    higher_is_cheaper: boolean | null;
    sources: Array<{
      accession: string;
      form: string | null;
      filed_at: string | null;
      url: string | null;
    }>;
    note: string;
  }>;
  flags: Array<{
    key: string;
    label: string;
    value: number | null;
    threshold: number | null;
  }>;
  score: {
    score: number | null;
    cheapness: number | null;
    used: string[];
    flags: string[];
    penalty: number | null;
    note: string;
  };
  filings: Array<{
    accession: string;
    form: string;
    filed_at: string;
    url: string | null;
  }>;
};

/** 개인 API 토큰 한 줄 (T263 MCP) — 값은 없다. */
export type ApiTokenRow = {
  id: string;
  name: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
};

export function apiTokens(): Promise<{ tokens: ApiTokenRow[] }> {
  return request("/auth/tokens");
}

/** 만든 직후 한 번만 값이 온다. */
export function apiTokenCreate(
  name: string,
): Promise<ApiTokenRow & { token: string }> {
  return request("/auth/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function apiTokenRevoke(
  id: string,
): Promise<{ revoked: boolean; id: string }> {
  return request(`/auth/tokens/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** 거시 지표 한 줄 (T262). */
export type MacroIndicator = {
  key: string;
  label: string;
  value: string;
  unit: string;
  source: string;
  as_of: string | null;
  change_pct: string | null;
  band: string | null;
  tone: string | null;
  note: string;
};

export type MacroView = {
  at: string;
  indicators: MacroIndicator[];
  failures: { key: string; label: string; reason: string }[];
  vix_bands: { below: string | null; label: string; tone: string }[];
  vix_note: string;
  keys: string[];
};

export function macro(): Promise<MacroView> {
  return request("/macro", undefined, 60_000);
}

export type ValueScreenView = {
  rows: (ValueRow & {
    stage?: "history" | "quick" | "none";
    periods?: Record<string, string>;
  })[];
  page: number;
  pages: number;
  size: number;
  total: number;
  sort: string;
  order: string;
  sorts: string[];
  at: string;
  market: string;
  /** 범위에 든 시장들 (`ALL`·`SP500` 이면 둘). */
  markets?: string[];
  /** 전체·SP 500 범위 — 백그라운드로 준비 중인 1단계 종목 수. 0 이면 다 찼다. */
  pending?: number;
  label: string;
  recommended: boolean;
  note: string;
};

/** 스크리닝 표 (T255) — 서버가 거르고 정렬해 쪽으로 준다. 창이 안 늘어난다. */
export function valueScreen(
  market: string,
  p: {
    sort?: string;
    order?: string;
    min_score?: number | null;
    no_flags?: boolean;
    has_facts?: boolean;
    q?: string;
    page?: number;
    size?: number;
  },
): Promise<ValueScreenView> {
  const qs = new URLSearchParams({ market });
  if (p.sort) qs.set("sort", p.sort);
  if (p.order) qs.set("order", p.order);
  if (p.min_score !== null && p.min_score !== undefined)
    qs.set("min_score", String(p.min_score));
  if (p.no_flags) qs.set("no_flags", "true");
  if (p.has_facts) qs.set("has_facts", "true");
  if (p.q) qs.set("q", p.q);
  if (p.page) qs.set("page", String(p.page));
  if (p.size) qs.set("size", String(p.size));
  return request(`/fundamentals/screen?${qs.toString()}`, undefined, 120_000);
}

/** 2단계로 올리기 — companyfacts 이력을 받는다 (T255 "이력 받기"). */
export function fundamentalsRefresh(
  symbol: string,
  market: string,
): Promise<Record<string, unknown>> {
  return request(
    `/fundamentals/${encodeURIComponent(symbol)}/refresh?market=${encodeURIComponent(market)}`,
    { method: "POST" },
    300_000,
  );
}

export function fundamentals(
  symbol: string,
  market: string,
  asOf?: string,
): Promise<FundamentalsView> {
  const tail = asOf ? `&as_of=${encodeURIComponent(asOf)}` : "";
  return request(
    `/fundamentals/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}${tail}`,
  );
}

/** 최근 공시 한 건 (T277 · EDGAR `submissions`) — 사건 이름과 원문 링크. 방향 칸은 없다. */
export type RecentFiling = {
  filed_at: string;
  accession: string;
  form: string;
  form_label: string;
  items: string[];
  labels: string[];
  headline: string;
  url: string;
  description: string;
  material: boolean;
};

export type FilingsView = {
  symbol: string;
  cik: string;
  name: string;
  at: string;
  filings: RecentFiling[];
  /** 못 받았을 때 이유. */
  reason: string | null;
};

export function filings(
  symbol: string,
  market: string,
  limit = 12,
): Promise<FilingsView> {
  return request(
    `/fundamentals/${encodeURIComponent(symbol)}/filings?market=${encodeURIComponent(market)}&limit=${limit}`,
    undefined,
    30_000,
  );
}

/** 온보딩 위저드 초안 (T247 · `/assistant/draft`). */
export type AssistantDraft = {
  step: "consent" | "capital" | "profile" | "setup" | "review" | "done";
  answers: Record<string, unknown>;
  consent_version: string | null;
  consent_at: string | null;
  fund_id: string | null;
  updated_at?: string;
};

export function assistantDraft(): Promise<{
  /** 초안도 펀드도 없다 — 첫 화면을 어시스턴트가 잡는다. */
  first: boolean;
  /** 서버가 초안을 들고 있나. 게스트는 거짓 — 브라우저에 든다. */
  persisted: boolean;
  draft: AssistantDraft | null;
  disclaimer: { version: string; text: string };
  steps: string[];
}> {
  return request("/assistant/draft");
}

export function assistantSaveDraft(body: {
  step: string;
  answers: Record<string, unknown>;
  consent_version?: string;
}): Promise<{ persisted: boolean; draft: AssistantDraft | null }> {
  return request("/assistant/draft", {
    method: "PUT",
    headers: JSON_POST,
    body: JSON.stringify(body),
  });
}

/** 성향에 맞는 후보와 과거 창 실측 (`/assistant/preview`). 손익은 백테스트 권한이 없으면 null(`redacted`). */
export type AssistantPreview = {
  group: string;
  group_label: string;
  tier: string;
  tier_label: string;
  market: string | null;
  chosen: string | null;
  windows: string[];
  note: string;
  candidates: Array<{
    id: string;
    label: string;
    leverage: number | null;
    recommended: boolean;
    backtest_note?: string | null;
    store: {
      years?: number | null;
      total_pct?: number | null;
      cagr_pct?: number | null;
      mdd_pct?: number | null;
      calmar?: number | null;
      trades_count?: number | null;
      liquidations?: number | null;
      underwater_pct?: number | null;
      risk_tier?: string | null;
      risk_tier_label?: string | null;
      frame?: string | null;
      redacted?: boolean;
      windows?: Record<
        string,
        {
          days: number;
          total_pct: number | null;
          mdd_pct: number;
          underwater_pct: number;
          trades: number;
          liquidations: number;
        } | null
      >;
    } | null;
  }>;
};

export function assistantPreview(
  group: string,
  tier: string,
): Promise<AssistantPreview> {
  return request(
    `/assistant/preview?group=${encodeURIComponent(group)}&tier=${encodeURIComponent(tier)}`,
  );
}

export function assistantCreate(body: {
  answers: Record<string, unknown>;
  consent_version: string;
  label?: string;
}): Promise<FundStatus & { draft: AssistantDraft }> {
  return request(
    "/assistant/create",
    { method: "POST", headers: JSON_POST, body: JSON.stringify(body) },
    180_000,
  );
}

/** AI 채팅 대화 (T248 · `/ai/chat/threads`). 메시지는 저장 모양 그대로 — `chat/chat.ts` 가 고른다. */
export type ChatThreadView = {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  count: number;
  messages?: Array<Record<string, unknown> & { role: string; content: string }>;
};

export function chatSettings(): Promise<{
  models: { id: string; rank: number; note: string }[];
  default: string;
  /** T249 관문 — 실험이 켜졌고 n≥30 · 기준선 위 참가자가 있으면 그 모델. */
  gate?: { model: string | null; why: string };
  /** 추천 질문 — 도구마다 하나 (T257 F1). */
  starters?: string[];
  prompt_version: string;
  auto: {
    enabled: boolean;
    note?: string;
    consented?: boolean;
    consent?: { version: string; text: string };
    shares?: number;
    margin?: string;
    max_per_day?: number;
    max_exposure_pct?: string;
    placed_today?: number;
  };
}> {
  return request("/ai/chat/settings");
}
export function chatThreads(): Promise<{ threads: ChatThreadView[] }> {
  return request("/ai/chat/threads");
}
export function chatThread(id: string): Promise<ChatThreadView> {
  return request(`/ai/chat/threads/${encodeURIComponent(id)}`);
}
export function chatCreateThread(body: {
  title?: string;
  model?: string;
}): Promise<ChatThreadView> {
  return request("/ai/chat/threads", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(body),
  });
}
/** 대화 삭제 (T257) — 내 것만. */
export function chatDeleteThread(id: string): Promise<{ deleted: string }> {
  return request(`/ai/chat/threads/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function chatAsk(
  id: string,
  body: { text: string; model?: string },
): Promise<{ job_id: string; thread_id: string }> {
  return request(`/ai/chat/threads/${encodeURIComponent(id)}/messages`, {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(body),
  });
}

/** 온보딩 카드의 단추 (T271) — 모델 없이 초안을 옮기고 다음 카드를 받는다. */
export function chatWizard(
  id: string,
  body: { action: string; step: string; answers?: Record<string, unknown> },
): Promise<{
  card: Record<string, unknown>;
  messages: Array<Record<string, unknown> & { role: string; content: string }>;
}> {
  return request(
    `/ai/chat/threads/${encodeURIComponent(id)}/wizard`,
    { method: "POST", headers: JSON_POST, body: JSON.stringify(body) },
    120_000,
  );
}

export function chatSetAuto(body: {
  enabled: boolean;
  consent_version?: string;
  shares?: number;
  margin?: string;
  max_per_day?: number;
  max_exposure_pct?: number;
}): Promise<Record<string, unknown>> {
  return request("/ai/chat/auto", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(body),
  });
}
export function chatPlaceOrder(body: {
  thread_id: string;
  proposal: Record<string, unknown>;
  shares?: number;
  margin?: string;
  model?: string;
}): Promise<{
  session_id: string;
  order_id: string;
  confirm?: { moved: boolean; stop: string };
}> {
  return request(
    "/ai/chat/orders",
    { method: "POST", headers: JSON_POST, body: JSON.stringify(body) },
    180_000,
  );
}

export type AiParticipantView = {
  participant: string;
  n: number;
  wins: number;
  hit_rate: string | null;
  avg_r: string | null;
  pnl_pct: string;
  mdd_pct: string;
  judged: boolean;
  min_sample: number;
  turns: number;
  prompt_tokens: number;
  completion_tokens: number;
  failures: number;
  /** 대시보드에서 근거 없던 칸 수의 합 — 환각 후보 (T249 2차). */
  dashboard_missing: number;
  last_closed_at: string | null;
  curve: string[];
};

export type AiReportView = {
  started: {
    at: string;
    by?: string | null;
    models?: string[];
    participants?: string[];
  } | null;
  prompt: { version: string; hash: string };
  snapshot: { bars: number; ohlc: number; frames: string[] };
  min_sample: number;
  participants: AiParticipantView[];
  baseline: AiParticipantView;
  default_model: string | null;
  journal: AiJournalRow[];
  /** 마지막 채팅 시험 묶음 (T258) — 없으면 null. */
  eval?: AiEvalView | null;
  tools?: string[];
  reason_hits: {
    reason: string;
    n: number;
    wins: number;
    hit_rate: string | null;
  }[];
  generated_at: string;
};

export type AiJournalRow = {
  closed_at: string;
  symbol: string;
  participant: string;
  outcome: string;
  gain_pct: string;
  realized_rr: string | null;
  won: boolean;
  reasons: string[];
  run_key: string;
};

/** AI 퍼포먼스 리포트 (T249). */
export function aiReport(): Promise<AiReportView> {
  return request("/ai/report");
}

/** 실험 시작 — 되돌릴 수 없다. 관리자만. `confirm` 은 사람이 친 "시작". */
export type AiEvalCase = {
  name: string;
  question: string;
  expect_tools: string[];
  called: string[];
  hit: boolean;
  tools_ok: boolean;
  answered: boolean;
  dashboard: boolean;
  dashboard_missing: number;
  passed: boolean;
  ms: number;
  rounds: number;
  tokens: number;
  failure: string | null;
  excerpt: string;
};

export type AiEvalView = {
  model: string;
  prompt_version: string;
  started_at: string;
  at?: string;
  by?: string | null;
  ms: number;
  n: number;
  passed: number;
  coverage: Record<string, "passed" | "called" | "missed" | "untested">;
  cases: AiEvalCase[];
};

/** 채팅 시험 묶음 실행 (작업) — 실제 모델을 부른다. */
export function aiReportEval(): Promise<{ job_id: string }> {
  return request("/ai/report/eval", { method: "POST" }, 60_000);
}

export function aiReportStart(body: {
  confirm: string;
  models?: string[];
}): Promise<{ started: AiReportView["started"]; participants: string[] }> {
  return request("/ai/report/start", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(body),
  });
}

export function marketStatus(market: string): Promise<MarketStatusView> {
  return request(
    `/exchange/market-status?market=${encodeURIComponent(market)}`,
  );
}
export function exchangeMarkets(): Promise<{
  markets: string[];
  all: MarketInfo[];
}> {
  return request("/exchange/markets");
}

export function consoleBalances(): Promise<{
  balances: Record<
    string,
    {
      available: string;
      position_margin: string;
      broker?: string;
      margin_mode?: string;
      testnet?: string;
      /** 오늘(KST 00시~) 실현 순손익 — 수수료·펀딩 포함 (2026-08-26). */
      today_pnl?: string;
    }
  >;
}> {
  return request("/exchange/balances");
}

/** 펀드 종목 상세 한 줄 (T261) — 원장 몫 + 마감 일봉 + 등락. */
export type FundMember = FundLeg & {
  symbol: string;
  last?: string | null;
  change_1d_pct?: string | null;
  change_5d_pct?: string | null;
  bars: {
    time: number;
    open: string;
    high: string;
    low: string;
    close: string;
    volume: string;
  }[];
  bars_error?: string;
};

export function fundMembers(
  id: string,
  bars = 90,
): Promise<{
  fund_id: string;
  market: string;
  at: string;
  members: FundMember[];
}> {
  return request(
    `/rebalancer/${encodeURIComponent(id)}/members?bars=${bars}`,
    undefined,
    120_000,
  );
}

export function fundList(): Promise<{ funds: FundStatus[] }> {
  return request("/rebalancer");
}

/** 펀드 생성 폼의 기본값 — 바스켓 SSoT 는 서버의 config/baskets.yml 이다 (T63 ②). */
/** 기본 바스켓 — `playbook` 을 주면 매매법별 바스켓(`baskets.yml by_playbook`)이 있을 때 그것을 받는다 (2026-09-17). */
export function fundDefaults(
  market: string,
  playbook = "",
): Promise<{
  playbook: string;
  /** 그 매매법이 **측정된 배율** — 없으면 화면이 알아서 정한다 (2026-08-30). */
  leverage?: string | null;
  members: { symbol: string; weight: string }[];
  /** 이 거래소 testnet 에 계약이 없어 걸러진 종목 — 실계좌에선 포함된다. */
  missing: string[];
}> {
  return request(
    `/rebalancer/defaults?market=${market}&playbook=${encodeURIComponent(playbook)}`,
  );
}

export function fundCreate(body: {
  label: string;
  total_cash: string;
  /** 생략하면 서버가 매매법 선언 배율을 쓴다 (2026-09-02 — 화면 입력 제거). */
  leverage?: string;
  playbook: string;
  /** GATE(기본) 또는 BINANCE — 펀드의 모든 세션이 이 거래소로 나간다 (T62 P3b). */
  market?: string;
  members: { symbol: string; weight: string }[];
}): Promise<FundStatus> {
  // 종목마다 판을 하나씩 띄우므로 오래 걸리는 것이 정상이다 — 느린 서버에서 종목당 15초.
  // 2026-09-05: 20초 시한에 브라우저가 끊어 서버가 4판을 만들고 롤백했다. 3분을 준다.
  return request(
    "/rebalancer",
    {
      method: "POST",
      headers: JSON_POST,
      body: JSON.stringify(body),
    },
    180_000,
  );
}

export function fundTick(id: string): Promise<{
  budgets: Record<string, string>;
  balance: string;
  twr_return: string;
  missing: string[];
  winding_down: string[];
}> {
  return request(`/rebalancer/${id}/tick`, { method: "POST" });
}

export function fundDeposit(
  id: string,
  amount: string,
  note = "",
): Promise<{ balance: string; twr_return: string; flow: string }> {
  return request(`/rebalancer/${id}/deposit`, {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify({ amount, note }),
  });
}

export function fundDrop(
  id: string,
): Promise<{ dropped: string; sessions: string[] }> {
  return request(`/rebalancer/${id}`, { method: "DELETE" });
}

export function fundEditBasket(
  id: string,
  members: { symbol: string; weight: string }[],
): Promise<FundStatus> {
  return request(`/rebalancer/${id}/basket`, {
    method: "PUT",
    headers: JSON_POST,
    body: JSON.stringify({ members }),
  });
}

export function fundChangePlaybook(
  id: string,
  playbook: string,
): Promise<FundStatus> {
  return request(`/rebalancer/${id}/playbook`, {
    method: "PUT",
    headers: JSON_POST,
    body: JSON.stringify({ playbook }),
  });
}

/**
 * **갈린 세션의 원장을 지금 거래소 상태로 재정렬한다** (2026-09-01).
 *
 * 🔴 복구 불가한 과거(재사용 계정에 쌓인 잔재 + 낡은 원장)를 버리고 *지금* 에 앵커한다.
 * 리셋이 아니다 — 거래소 계정·돈·포지션은 안 건드리고, 우리 회계 기준점만 옮긴다.
 * 깨끗한 세션은 안 건드린다.
 */
export function fundResync(
  id: string,
): Promise<FundStatus & { resynced?: string[] }> {
  return request(`/rebalancer/${id}/resync`, { method: "POST" });
}

/**
 * 분석 샌드박스 — 종목·축·플래그를 골라 **지금 시세**의 도형을 받는다 (차트 주문 탭).
 *
 * 🔴 **`/admin/inspect` 를 쓰지 않는다** (2026-08-30 실측):
 *
 *     400 {"detail":"봉인 구간이다 (2026-01-01 이후) — 눈으로 보는 것도 오염이라
 *          열 수 없다 (§1-0t T10)"}
 *
 * ⛔ 그 봉인은 옳다. 점검기는 **연구용**이고, out-of-sample 을 눈으로 보면 그 뒤의
 * 판정이 오염된다 — 되돌릴 수 없는 종류의 오염이다.
 *
 * ⇒ 매매 화면은 **지금 사고팔 자리**를 보므로 목적이 다르다. 서버가 입구를 나눴다
 * (`/analysis/frame`) — 한 입구에 예외를 뚫으면 그 예외가 곧 연구 경로의 구멍이 된다.
 */
/**
 * 분석 응답 — 봉·도형에 **거른 레벨과 계획 제안**이 붙는다.
 *
 * ⚠️ 거르기와 계획은 **서버가 한다**. 화면이 하면 판정과 다른 규칙이 두 벌이 되고,
 * 그 어긋남은 조용하다 (규칙 #9).
 */
export type AnalysisFrame = Frame & {
  /** 이 거래소가 **실제로 주는** 축들 — 화면이 단추를 이것으로 그린다 (T63 ②). */
  frames?: string[];
  /** 쓸 수 있는 레벨만 — 원본은 `levels_raw` 에 수로만 온다. */
  levels: {
    low: string;
    high: string;
    support: boolean;
    touches: string;
    away_pct: string;
    /** 몇 개가 뭉쳐 이 자리가 됐나. */
    merged: string;
  }[];
  /** 작도가 찾은 원본 수 — **몇 개를 버렸는지** 화면이 말해야 한다 (규칙 #8). */
  levels_raw: string;
  atr: string;
  /** 왕복 비용 (%) — 화면이 산수를 다시 돌릴 때 쓴다. 지어내지 않는다. */
  round_trip_pct: string;
  /**
   * 계획 제안. **`null` 이 흔한 것이 정상이다** — 비용을 못 갚는 자리가 대부분이다.
   *
   * 🔴 제안이지 결정이 아니다. 집행값의 SSoT 는 RiskManager 다 (절대 규칙 #4).
   */
  plan: {
    long: boolean;
    entry: string;
    stop: string;
    first: string;
    target: string;
    rr: string;
    need_pct: string;
    stop_pct: string;
    why: string;
  } | null;
};

export function analysisFrame(params: {
  symbol: string;
  market: string;
  flags: string[];
  timeframe: string;
  bars?: number;
}): Promise<AnalysisFrame> {
  const query = new URLSearchParams({
    symbol: params.symbol,
    market: params.market,
    flags: params.flags.join(","),
    timeframe: params.timeframe,
    bars: String(params.bars ?? 400),
  });
  return request(`/analysis/frame?${query.toString()}`);
}

/**
 * 고친 계획을 **RiskManager 에 걸어 본다** — 주문은 안 낸다.
 *
 * 🔴 집행값의 SSoT 는 RiskManager 다 (절대 규칙 #4). 끈 선은 제안이고, 여기서 확정을
 * 거친다 — `moved` 가 참이면 **RiskManager 가 손절을 바꿨다**는 뜻이고, 화면은 바뀐
 * 값을 보여 줘야 한다. 사람이 낸 값이 그대로 나갈 것처럼 보이면 그것이 거짓말이다.
 *
 * ⛔ 이 입구에는 주문 경로가 **없다.** "내면 무엇이 되나" 만 답한다.
 */
export function validatePlan(payload: {
  entry: number;
  stop: number;
  first: number;
  target: number;
  leverage: number;
  market: string;
  /** 숏인가 — 절대 규칙 #10 개정(2026-08-17)으로 열렸다. */
  short?: boolean;
}): Promise<{
  /** RiskManager 를 통과했나 — **주문이 나갔다는 뜻이 아니다**. */
  ok: boolean;
  /** **확정된** 손절가. 사람이 낸 값과 다를 수 있다. */
  stop: string;
  /** RiskManager 가 손절을 바꿨나. */
  moved: boolean;
  reasons: string[];
  beta: string;
  /** 이 배율의 청산 거리 (%). */
  liq_pct: string;
  stop_pct: string;
  rr: string;
  need_pct: string;
}> {
  return request("/analysis/validate", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(payload),
  });
}

/** 확정 결과 — `/analysis/validate` 와 차트 주문이 **같은 모양**을 쓴다. */
export interface Confirmed {
  /** 확정을 통과했나 — **주문이 나갔다는 뜻이 아니다**. */
  ok: boolean;
  /** **확정된** 손절가. 사람이 낸 값과 다를 수 있다. */
  stop: string;
  moved: boolean;
  reasons: string[];
  /** 주문을 **막은** 이유들. 비어 있으면 통과다. */
  blocked: string[];
  beta: string;
  liq_pct: string;
  stop_pct: string;
  rr: string;
  need_pct: string;
}

/**
 * 차트에서 그은 계획으로 **판을 띄우고 그 자리에서 산다** (차트 주문 4단계).
 *
 * @param payload 종목·거래소·예산·배율 + 사람이 정한 네 값 + 본 플래그들.
 * @returns 판 상태. `session_id` 로 기존 RUN 화면이 그대로 열린다.
 *
 * 🔴 **이 판의 주체는 사람이다** (사용자 확정 2026-08-30). `custom` 플레이북에는
 * 셋업도 트레일도 재레버도 없어서, 러너는 **집행·감시·기록**만 한다 — 진입가와 익절
 * 둘은 여기 보낸 값이 그대로 원장에 들어간다.
 *
 * ⚠️ **손절만 바뀔 수 있다.** 청산 거리 밖의 손절은 절대 체결되지 않으므로 안쪽으로
 * 당긴다 (T120: 청산난 판의 81~84%). 바뀌면 응답의 `confirm.moved` 가 참이고,
 * 화면은 **바뀐 값**을 보여 줘야 한다.
 *
 * ⛔ 확정에 걸리면 400 이고 **판은 만들어지지 않는다** — 아무것도 안 하는 빈 판이
 * 남으면 화면에서 진짜 판과 구별되지 않는다.
 */
/** AI 차트 주문 (T273) — 갈래 · 분석 응답. 숫자는 서버 문자열 그대로(지어내지 않는다). */
export type ChartBucket = {
  key: string;
  label: string;
  entry: string;
  context: string[];
  valid_bars: number;
  rr: string;
};
export type ChartPlanSide = {
  side: "long" | "short";
  ok: boolean;
  entry?: string;
  stop?: string;
  stop_moved?: boolean;
  first?: string;
  target?: string;
  rr?: string;
  need_pct?: string;
  stop_pct?: string;
  blocked: string[];
  warnings?: string[];
  basis: string;
  distance?: {
    to_entry_pct: string;
    to_stop_pct: string;
    to_target_pct: string;
    risk_pct: string;
    reward_pct: string;
  };
  market?: string;
};
export type ChartAnalysis = {
  analysis_id: string;
  at: string;
  symbol: string;
  market: string;
  bucket: {
    key: string;
    label: string;
    entry: string;
    context: string[];
    valid_bars: number;
  };
  frame: AnalysisFrame;
  structure: {
    last: string;
    atr: string;
    nearest_support: AnalysisFrame["levels"][number] | null;
    nearest_resistance: AnalysisFrame["levels"][number] | null;
    levels_raw: string;
    swings: {
      swing_high: { price: number; away_pct: number | null; ts: string } | null;
      swing_low: { price: number; away_pct: number | null; ts: string } | null;
    };
    rule_plan: AnalysisFrame["plan"];
    note: string;
  };
  extremes: Record<string, unknown>;
  valuation: Record<string, unknown> | null;
  valuation_note: string;
  vix: { value?: number | string; note?: string; band?: string } | null;
  plans: { long: ChartPlanSide | null; short: ChartPlanSide | null };
  /** 계획에 실제로 쓴 근거 — 서버가 만든 줄 그대로 ("사용한 근거" 카드). */
  evidence?: string[];
  /** 계획이 없을 때 왜 없는지 — 원래 없는 자리인지 오류인지 (2026-09-11). */
  plan_reasons?: { long: string | null; short: string | null };
  note: string;
};

/** AI 비교의 참가자 한 줄 — 실험 원장의 제안. `judgement` 는 채점 뒤에만. */
export type ChartParticipant = {
  participant: string;
  kind: string;
  stance: string;
  entry: string | null;
  stop: string | null;
  first: string | null;
  target: string | null;
  conviction: number | null;
  trigger: string;
  detail: string;
  latency_ms: number;
  judgement?: {
    entered: boolean;
    outcome: string | null;
    net_r: string | null;
    bars_to_entry: number | null;
    bars_held: number | null;
  } | null;
};
export type ChartRun = {
  run_id: string;
  symbol: string;
  market: string;
  taken_at: string;
  entry: string;
  hold_bars: number;
  matures_at: string;
  judged: boolean;
  participants: ChartParticipant[];
};

export type ScoreRow = {
  participant: string;
  bucket: string;
  market: string;
  proposed: number;
  abstained: number;
  entered: number;
  no_entry: number;
  followed: number;
  not_followed: number;
  expired: number;
  follow_pct: number | null;
  avg_net_r: string | null;
  grey: boolean;
};

export function chartOrderScoreboard(
  params: { market?: string; bucket?: string } = {},
): Promise<{
  rows: ScoreRow[];
  min_sample: number;
  cycles: number;
  judged: number;
}> {
  const query = new URLSearchParams();
  if (params.market) query.set("market", params.market);
  if (params.bucket) query.set("bucket", params.bucket);
  return request(`/chart-order/scoreboard?${query.toString()}`);
}

/** `job_id` 가 null 이면 `reuse_minutes` 안의 지난 회차를 그대로 준 것 — 모델을 안 불렀다. */
/** `/analyze` 를 작업으로 — 진행 줄은 `useJobEvents(job_id)`, 결과 이벤트가 `ChartAnalysis` (2026-09-11). */
export function chartOrderAnalyzeJob(payload: {
  symbol: string;
  market: string;
  bucket: string;
}): Promise<{ job_id: string }> {
  return request("/chart-order/analyze-job", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(payload),
  });
}

export function chartOrderRun(payload: {
  symbol: string;
  market: string;
  bucket: string;
  side?: string;
}): Promise<{
  job_id: string | null;
  reused?: boolean;
  run_id?: string;
  participants?: ChartParticipant[];
  note?: string;
}> {
  return request("/chart-order/run", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(payload),
  });
}

export function chartOrderRuns(
  params: { symbol?: string; market?: string; limit?: number } = {},
): Promise<{ runs: ChartRun[] }> {
  const query = new URLSearchParams();
  if (params.symbol) query.set("symbol", params.symbol);
  if (params.market) query.set("market", params.market);
  if (params.limit) query.set("limit", String(params.limit));
  return request(`/chart-order/runs?${query.toString()}`);
}

export function chartOrderResolve(): Promise<{ job_id: string }> {
  return request("/chart-order/resolve", {
    method: "POST",
    headers: JSON_POST,
    body: "{}",
  });
}

export function chartOrderBuckets(): Promise<{ buckets: ChartBucket[] }> {
  return request("/chart-order/buckets");
}

export function chartOrderAnalyze(params: {
  symbol: string;
  market: string;
  bucket: string;
}): Promise<ChartAnalysis> {
  const query = new URLSearchParams(params);
  // ⏳ 토스 시장의 분봉은 1m 원봉 합성이라 **처음 한 번**은 1~2분이 든다(다음부터 DB 꼬리만). 기본 20초면 화면이
  //    먼저 포기해 499 가 나고 서버는 계속 받는다(실계좌 실측 2026-09-11 · SPY 1h). 3분을 준다.
  return request(
    `/chart-order/analyze?${query.toString()}`,
    undefined,
    180_000,
  );
}

export function orderCustom(payload: {
  symbol: string;
  market: string;
  margin: number;
  /** 주식(정수 수량 시장)만 — 주수. 서버가 예산 = 주수 x 진입가로 바꾼다 (T250). */
  shares?: number;
  /** 판정 축 — 차트가 보던 축. 셋업 없는 판(custom)만 받는다 (T250). */
  timeframe?: string;
  leverage: number;
  short: boolean;
  entry: number;
  stop: number;
  first: number;
  target: number;
  /** 사람이 보고 잡은 플래그들 — 판 메타에 남아 나중에 물을 수 있다. */
  flags: string[];
  /** 손절을 몇 분 간격으로 볼 것인가. 안 주면 서버가 1분으로 둔다. */
  price_frame?: string;
}): Promise<{ session_id: string; confirm: Confirmed }> {
  return request("/walkforward/live/custom", {
    method: "POST",
    headers: JSON_POST,
    body: JSON.stringify(payload),
  });
}

/** 주요 일정 달력 (T276 · `/calendar/upcoming`) — 예정일만. 방향을 말하는 칸은 없다. */
export type CalendarEvent = {
  kind: "macro" | "earnings";
  /** ISO 날짜 — 출처의 현지(미국) 기준. */
  date: string;
  /** 발표 시각(UTC ISO). 모르면 null — 카운트다운을 걸지 않는다. */
  at: string | null;
  /** 과거 반응 표의 열쇠(`cpi` · `jobs` · `fomc`). 없으면 null. */
  history: string | null;
  title: string;
  source: string;
  key: string;
  symbol: string | null;
  market: string | null;
  url: string | null;
  detail: Record<string, string | number | null>;
};

export type CalendarView = {
  at: string;
  from: string;
  to: string;
  days: number;
  events: CalendarEvent[];
  /** 못 받은 출처 — 이유와 함께. 조용히 빠지지 않는다. */
  failures: Array<{ key: string; label: string; reason: string }>;
  watch: Array<{
    key: string;
    label: string;
    note: string;
    url: string | null;
  }>;
  /** 오늘 발표 중 값을 아는 것 (지금은 CPI) — 열쇠 → 거시 지표 모양 + `fresh`(발표분이 실렸나). */
  actuals: Record<
    string,
    {
      label: string;
      value: string;
      unit: string;
      note: string;
      as_of: string | null;
      fresh: boolean;
    }
  >;
  /** 서버 시각 — 화면은 자기 시계 대신 이것으로 카운트다운을 잰다. 뉴욕 오프셋은 tz DB(서머타임). */
  clock: {
    utc: string;
    ny: string;
    kst: string;
    ny_offset_min: number;
    ny_zone: string;
    kst_offset_min: number;
  };
};

export function calendarUpcoming(days?: number): Promise<CalendarView> {
  const tail = days ? `?days=${days}` : "";
  return request(`/calendar/upcoming${tail}`, undefined, 60_000);
}

/** 과거 반응 표 (`/calendar/history`) — 행은 사실, 묶음은 표본 수와 함께. 방향 칸은 없다. */
export type CalendarHistory = {
  kind: string;
  label: string | null;
  value_label: string | null;
  value_unit: string | null;
  axis: string | null;
  first_minutes: number | null;
  h1_minutes: number | null;
  h2_minutes: number | null;
  min_sample: number;
  generated_at: string | null;
  symbols: string[];
  rows: Array<{
    date: string;
    at: string;
    /** 그날 나온 값(CPI 전년비 · 실업률). 없으면 없다. */
    value?: number;
    /** 전달 대비 변화(%p). */
    delta?: number | null;
    period?: string;
    moves: Record<
      string,
      { first: number; h1: number; h2: number; spike: number }
    >;
  }>;
  aggregate: {
    n: number;
    held_h1?: Record<string, number | null>;
    first_abs_median?: Record<string, number | null>;
    spike_median?: Record<string, number | null>;
  };
  /** 표가 없을 때 이유. */
  reason: string | null;
};

export function calendarHistory(
  kind: string,
  days = 365,
): Promise<CalendarHistory> {
  return request(
    `/calendar/history?kind=${encodeURIComponent(kind)}&days=${days}`,
    undefined,
    30_000,
  );
}
