/**
 * 화면 부품과 표시 규칙.
 *
 * 🔴 **모르는 값은 `—` 다. 0 이 아니다.** 이 프로젝트가 하루에 네 번 같은 실수를 했다 —
 * 잔고를 못 읽은 것이 "잔고 0" 으로, 미점검이 정상으로, 안 본 축이 멈춘 축으로, 비교
 * 불가가 불일치로 보였다. 표시가 거짓말하면 사람은 그것을 믿고, 그 믿음이 라이브에서
 * 가장 비싸다.
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

/** 숫자 문자열/숫자를 자릿수 맞춰. 못 읽으면 `—`. */
export function num(
  value: string | number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return parsed.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 퍼센트. 부호를 붙여 방향이 먼저 읽히게 한다. */
export function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

/**
 * 한국시(KST) 보정 — **표시는 KST, 저장은 UTC** (절대 규칙 #7).
 *
 * 🔴 사용자 지적 2026-08-18: *"지금 시간대가 한국시 기준이 아닌 것 같네."* 맞다.
 * 화면이 UTC 를 그대로 찍고 있었고, 사람은 그것을 자기 시계와 맞춰 읽는다.
 *
 * ⚠️ **고정 오프셋으로 더한다.** 한국은 서머타임이 없어 항상 +9 이고, `Intl` 에
 * 맡기면 실행 환경의 ICU 유무에 따라 결과가 달라진다 — 테스트가 브라우저와 다른
 * 답을 내면 그 표시는 못 믿는다.
 */
const KST_MS = 9 * 60 * 60 * 1000;

function shifted(ms: number): string {
  return new Date(ms + KST_MS).toISOString();
}

/**
 * 지금 **한국시로 몇 시인가** (0~23).
 *
 * @param at 기준 시각(ms). 없으면 지금.
 *
 * ⚠️ 여기서 내보내는 이유 — 야간 음소거가 KST 시각을 알아야 하는데, 상수를 한 벌 더
 * 두면 그 둘이 갈리는 순간이 온다. 이 파일이 KST 의 단일 출처다.
 */
export function kstHour(at: number = Date.now()): number {
  return new Date(at + KST_MS).getUTCHours();
}

/** ISO(UTC) → `08-18 19:15` **KST**. */
export function when(at: string | null | undefined): string {
  if (!at) return "—";
  const ms = Date.parse(at);
  if (!Number.isFinite(ms)) return "—";
  return shifted(ms).slice(5, 16).replace("T", " ");
}

/** 초 타임스탬프 → 같은 형식 (KST). */
export function whenSec(seconds: string | undefined): string {
  const parsed = Number(seconds);
  if (!Number.isFinite(parsed) || parsed <= 0) return "—";
  return shifted(parsed * 1000)
    .slice(5, 16)
    .replace("T", " ");
}

/** ISO(UTC) → `19:15` KST — 축 눈금처럼 좁은 자리용. */
export function clock(at: string | null | undefined, seconds = false): string {
  if (!at) return "—";
  const ms = Date.parse(at);
  if (!Number.isFinite(ms)) return "—";
  return shifted(ms).slice(11, seconds ? 19 : 16);
}

/** 초를 사람 말로 — 나이 표시용. */
export function ago(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds)) return "—";
  if (seconds < 90) return `${Math.round(seconds)}초 전`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}분 전`;
  return `${Math.round(seconds / 3600)}시간 전`;
}

export function Card({
  name,
  value,
  hint,
  tone,
  wide,
}: {
  name: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "gain" | "loss";
  wide?: boolean;
}) {
  // T220: 템플릿(MT Card) 모양 — 흰 바탕 · rounded-xl · 연한 테두리 · shadow-sm. 클래스 이름(card-*)은
  // 옛 CSS 와 시험이 아직 보므로 남기고, 모양은 Tailwind 가 정한다.
  const toneCls =
    tone === "gain"
      ? "text-gain"
      : tone === "loss"
        ? "text-loss"
        : "text-blue-gray-900 dark:text-white";
  return (
    <div
      className={`card flex min-w-0 flex-col gap-0.5 rounded-xl border border-blue-gray-100 bg-white p-4 shadow-sm dark:border-gray-800 dark:bg-gray-900 ${wide ? "wide col-span-full" : ""}`}
    >
      <span className="card-name text-xs text-blue-gray-500 dark:text-blue-gray-300">
        {name}
      </span>
      <b
        className={`card-value break-words font-mono text-xl font-semibold leading-tight ${toneCls}`}
      >
        {value}
      </b>
      {hint ? (
        <span className="card-hint text-xs text-blue-gray-400">{hint}</span>
      ) : null}
    </div>
  );
}

/**
 * 연결 배너.
 *
 * 🔴 끊겼을 때 **화면의 숫자가 지금 값이 아니라는 것**을 말해야 한다. 예전에는 폴링
 * 실패를 조용히 삼켜서 마지막 값이 그대로 남았고, 사람은 그것을 지금 값으로 읽었다.
 */
export function Disconnected({ silentFor }: { silentFor: number }) {
  return (
    <p className="notice bad">
      서버와 연결이 끊겼다 — {Math.round(silentFor / 1000)}초째 응답 없음.
      자동으로 계속 다시 시도한다. <b>화면 숫자는 마지막으로 받은 값이다.</b>
    </p>
  );
}

/**
 * 자가 점검 결과.
 *
 * 🔴 **등급을 갈라 그린다** (2026-08-20). 예전에는 전부 붉게 묶어 *"하나라도 있으면
 * 판정을 믿으면 안 된다"* 를 붙였는데, 그 문장은 `error` 에만 참이다.
 *
 * 실제로 겪은 것: 판을 5개 띄우면 `wallet_unattributable`(경고)이 늘 뜬다 — 판마다
 * 원장이 계좌 전체를 자기 것으로 세니 **가를 수 없다**는 사실 보고이지 결함이 아니다.
 * 그것을 "이상 1건 · 판정을 믿으면 안 된다" 로 그리면 두 가지가 같이 망가진다:
 *
 * - 정상 상태가 **경보처럼** 보인다
 * - 진짜 `error` 가 떴을 때 **구별되지 않는다** — 늘 붉으면 아무도 안 본다
 */
export interface Finding {
  code: string;
  level: string;
  detail: string;
}

/**
 * 자가 점검 결과를 **못 믿을 것**과 **알아 둘 것**으로 가른다.
 *
 * 🔴 순수 함수로 뺀 이유: 이 가름이 틀리면 정상 상태가 경보로 보이거나(판 5개의
 * `wallet_unattributable`) 진짜 결함이 경고에 묻힌다. 그림이 아니라 **규칙**이므로
 * 시험이 잡을 수 있어야 한다.
 *
 * ⚠️ `error` 만 붉다. 등급이 없거나 모르는 값이면 **경고 쪽**에 둔다 — 모르는 것을
 * 치명으로 올리면 늘 붉어지고, 늘 붉으면 아무도 안 본다.
 */
export function splitFindings(items: Finding[]): {
  bad: Finding[];
  soft: Finding[];
} {
  return {
    bad: items.filter((item) => item.level === "error"),
    soft: items.filter((item) => item.level !== "error"),
  };
}

/**
 * **무방비가 진짜인가** — 처음 본 순간에는 아직 모른다 (사용자 신고 2026-08-20).
 *
 * @param naked 지금 조건부 손절이 없는 포지션들.
 * @param seen 종목별로 **처음 무방비로 본 시각**. 이 함수가 새 표를 돌려준다.
 * @param now 지금(ms).
 * @param grace 이만큼 지나도 그대로면 진짜다(ms).
 *
 * 🔴 사용자 신고: *"주문이 진행되면 (…) 실제 손절 주문이 발행되기 전에 바로 뜨네.
 * 그래서 얼럿 알림이 먼저 와."* 맞다 — 체결로 포지션이 생긴 뒤 러너가 조건부를 걸기까지
 * 몇 초가 걸리고, 그 사이의 폴링이 *"포지션은 있는데 조건부 0건"* 을 본다.
 *
 * ⚠️ **사실을 감추는 것이 아니다.** 잠깐 뒤에도 그대로면 그대로 뜬다. 다만 **매 진입마다**
 * 경보가 울리면 사람이 소리를 꺼 버리고, 끄면 진짜일 때도 못 듣는다.
 *
 * ⛔ 화면은 러너가 무장 중인지 알 방법이 없다 (판 없는 포지션도 여기 온다) — 그래서
 * 여기서만 시간으로 잰다. 러너 쪽은 **기회**로 잰다 (`_armed_for`).
 */
export function confirmedNaked<T extends { symbol: string }>(
  naked: readonly T[],
  seen: ReadonlyMap<string, number>,
  now: number,
  grace: number,
): { seen: Map<string, number>; sure: T[] } {
  const next = new Map<string, number>();
  const sure: T[] = [];
  for (const row of naked) {
    // ⭐ 이미 보던 것이면 **처음 본 시각을 유지한다** — 매번 갱신하면 영원히 안 익는다.
    const first = seen.get(row.symbol) ?? now;
    next.set(row.symbol, first);
    if (now - first >= grace) sure.push(row);
  }
  // ⚠️ 손절이 걸린 종목은 표에서 뺀다 — 안 빼면 다음에 무방비가 됐을 때 옛 시각으로
  //    즉시 익어서, 이 함수가 막으려던 그 거짓 경보가 다시 난다.
  return { seen: next, sure };
}

export function Findings({ items }: { items: Finding[] }) {
  const { bad, soft } = splitFindings(items);
  if (!items.length) return null;
  return (
    <>
      {bad.length ? (
        <div className="notice bad">
          <b>자가 점검 — 이상 {bad.length}건</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: "18px" }}>
            {bad.map((item) => (
              <li key={item.code}>{item.detail}</li>
            ))}
          </ul>
          <span className="faint">하나라도 있으면 판정을 믿으면 안 된다</span>
        </div>
      ) : null}
      {/* ⚠️ 경고는 **감추지 않는다.** 알아 둘 것이지 못 믿을 것이 아니다 — 조용히
          버리면 "왜 이 판만 대조가 없지" 에 화면이 답을 못 한다 (규칙 #8). */}
      {soft.length ? (
        <div className="notice warn">
          <b>알아 둘 것 {soft.length}건</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: "18px" }}>
            {soft.map((item) => (
              <li key={item.code}>{item.detail}</li>
            ))}
          </ul>
          <span className="faint">판정을 막지는 않는다 — 상황 보고다</span>
        </div>
      ) : null}
    </>
  );
}

/** 상단 브랜드 표식 — 1px 스트로크 아웃라인 (DESIGN.md: 아이콘은 채우지 않는다). */
export function Mark() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 17l5-6 4 4 5-7 4 5" />
    </svg>
  );
}

/** 시간축 초 → 사람 말 (`900` → `15분봉`). */
export function frameWord(interval: number): string {
  if (interval >= 86400) return `${Math.round(interval / 86400)}일봉`;
  if (interval >= 3600) return `${Math.round(interval / 3600)}시간봉`;
  if (interval >= 60) return `${Math.round(interval / 60)}분봉`;
  return `${interval}초봉`;
}

/**
 * **매매 로직이 돌고 있는가** — 한 줄로 답한다.
 *
 * 🔴 사용자 지적 2026-08-18: *"매매 로직이 돌고 있는지 확인 불가."* 맞다. 판정 횟수·
 * 스트림 상태·봉 나이가 따로 흩어져 있어서, 셋을 머리로 합쳐야 답이 나왔다.
 *
 * ⚠️ **판정은 진입 축 봉이 마감될 때만 돈다.** 15m 이면 15분에 한 번이다 — 그래서
 * *"몇 초째 판정이 없다"* 는 이상이 아니고, **한 주기를 넘겨야** 이상이다.
 *
 * 🔴 **여기 들어오는 `interval` 은 판정 축이어야 한다** (사용자 지적 2026-08-18:
 * *"매매로직에 대한 이해가 전혀 없는 것 같은데? 15분 기준으로 도는 걸로 아는데"*).
 * 차트에서 고른 축을 넣었더니 10초봉을 보는 동안 *"첫 10초 봉이 마감되면 돈다"* 고
 * 적혔고, 12분밖에 안 된 정상 대기가 **고장으로 읽혔다.**
 *
 * @param steps 판정 횟수.
 * @param running 스트림이 붙어 있는가.
 * @param sinceJudged 마지막 판정 이후 초. 모르면 undefined.
 * @param interval **판정 축** 간격(초). 차트 축이 아니다.
 * @param untilNext 다음 판정까지 초. 모르면 undefined.
 */
export function aliveness(
  steps: number | undefined,
  running: boolean | undefined,
  sinceJudged: number | undefined,
  interval: number,
  untilNext?: number,
): { label: string; tone?: "gain" | "loss"; why: string } {
  // ⭐ 다음 판정까지 남은 시간 — "판정 0" 이 **고장인지 대기인지**를 가르는 값이다.
  const next =
    untilNext === undefined || !Number.isFinite(untilNext)
      ? ""
      : ` · 다음 ${untilNext <= 0 ? "곧" : ago(untilNext).replace(" 전", " 뒤")}`;
  if (running === undefined) {
    return {
      label: "모름",
      why: "러너를 못 읽었다 — 판이 없거나 저널만 남은 것이다",
    };
  }
  if (!running) {
    return {
      label: "멈췄다",
      tone: "loss",
      why: "스트림이 끊겼다 — 봉이 안 들어온다",
    };
  }
  if (steps === 0) {
    return {
      label: "첫 판정 대기",
      why: `${frameWord(interval)} 마감마다 판정한다${next}`,
    };
  }
  // 🔴 **한 주기 + 여유를 넘겨야 이상이다.** 그냥 "몇 초 지났다" 로 재면 15m 판정이
  //    거의 항상 빨갛게 뜨고, 그러면 아무도 그 색을 안 믿게 된다.
  if (sinceJudged !== undefined && sinceJudged > interval * 1.5) {
    return {
      label: "판정이 밀렸다",
      tone: "loss",
      why: `마지막 판정이 ${Math.round(sinceJudged)}초 전 — 한 주기(${interval}초)를 넘겼다`,
    };
  }
  return {
    label: "돌고 있다",
    tone: "gain",
    why: `${frameWord(interval)} 판정 ${steps}회 · 마지막 ${ago(sinceJudged)}${next}`,
  };
}

/**
 * 거래소 주문 하나가 **무엇이었나** — 주문 이름으로 가른다.
 *
 * 🔴 사용자 지적 2026-08-18: *"이렇게 청산으로 나오는데, 이거 손절이란 거지? 청산은
 * 내 증거금이 모두 사라졌을 때 (…) 이 주문으로 싹다 손실본게 아닐텐데?"* 맞다.
 * 화면이 `is_reduce_only` 하나만 보고 **"청산"** 이라고 적고 있었는데, 그 필드는
 * *"포지션을 줄이는 주문"* 이라는 뜻일 뿐 **강제청산과 아무 상관이 없다.**
 *
 * ⛔ **강제청산을 말하지 않는다.** 우리는 그것을 구별할 근거가 없다 — 없는 사실을
 * 적느니 무엇을 줄인 주문인지만 말한다.
 *
 * @param text 주문에 우리가 붙인 이름. Gate 가 만든 자동 주문은 `ao-` 로 시작한다.
 * @param reduceOnly 포지션을 줄이는 주문인가.
 */
/**
 * 접힘 상태를 **기억한다** (T18 ⑥).
 *
 * ⚠️ 매번 다시 접게 하면 아무도 안 쓴다 — 그러면 접기 기능이 없는 것과 같다.
 */
export function useFold(key: string, initial = false): [boolean, () => void] {
  const slot = `fold:${key}`;
  const [open, setOpen] = useState(() => {
    try {
      const kept = localStorage.getItem(slot);
      return kept === null ? !initial : kept === "open";
    } catch {
      // ⛔ 저장소를 못 써도 화면은 돌아야 한다 — 사생활 모드·용량 초과가 있다.
      return !initial;
    }
  });
  const toggle = () => {
    setOpen((was) => {
      try {
        localStorage.setItem(slot, was ? "shut" : "open");
      } catch {
        // 기억만 못 한다. 이번 화면은 정상으로 접힌다.
      }
      return !was;
    });
  };
  return [open, toggle];
}

/**
 * 확인했는데 **그 뒤로 새 것이 생겼나** — 확인 배너의 판정 (2026-09-21).
 *
 * 🔴 "확인" 은 지금까지의 N건을 봤다는 뜻이지 앞으로도 안 보겠다는 뜻이 아니다.
 * 확인한 수를 기억해 두고 **그보다 늘면 다시 띄운다** — 안 그러면 한 번 누르는 것으로
 * 조용한 실패가 되고, 그것이 이 규칙이 막으려던 상태다 (규칙 #8).
 */
export function stillWorrying(count: number, acked: number | null): boolean {
  if (!count || count < 0) return false;
  if (acked === null || !Number.isFinite(acked)) return true;
  return count > acked;
}

/**
 * 확인 배너의 상태 — **판마다** 따로 기억하고 새로고침에도 남는다.
 *
 * ⚠️ 슬롯에 판 id 를 넣는다. 안 넣으면 한 판에서 누른 확인이 모든 판을 덮는다
 * (`useFold` 가 실제로 그래서 판 사이에 접힘이 섞인다).
 */
export function useAck(key: string, count: number): [boolean, () => void] {
  const slot = `ack:${key}`;
  const [acked, setAcked] = useState<number | null>(() => {
    try {
      const kept = localStorage.getItem(slot);
      return kept === null ? null : Number(kept);
    } catch {
      // ⛔ 저장소를 못 써도 화면은 돌아야 한다 — 이번 화면은 안 접힌 채로 뜬다.
      return null;
    }
  });
  const ack = () => {
    setAcked(count);
    try {
      localStorage.setItem(slot, String(count));
    } catch {
      // 기억만 못 한다. 이번 화면에서는 닫힌다.
    }
  };
  return [stillWorrying(count, acked), ack];
}

/**
 * **판정을 건너뛴 봉** 알림 — 노란 주의 (2026-09-21 · 사용자 요구 "상단에 노란색으로 주의 정도만").
 *
 * 🔴 왜 있나: 걸음이 예외로 실패하면 그 봉은 통째로 건너뛰어지고 **그 자리의 진입이 사라진다.**
 * 2026-09-21 에 ETH 가 그렇게 한 건을 놓쳤는데(폭을 형제에게 묻다 난 예외 · 1.13.1 에서 고침)
 * 로그 한 줄뿐이라 **사람이 묻기 전까지 아무도 몰랐다.** 붉은 이상(`Findings`)과 달리 판정을
 * 못 믿을 일은 아니라 노랑이고, 확인하면 닫힌다 — 단 새로 나면 다시 뜬다.
 */
export function SkippedBars({
  run,
  count,
  detail,
}: {
  run: string;
  count: number;
  detail?: string | null;
}) {
  const [show, ack] = useAck(`skipped:${run}`, count);
  if (!show) return null;
  return (
    <div className="notice warn" role="status">
      <b>판정을 {count}회 건너뛰었다</b> — {detail || "알 수 없음"}
      <div className="faint" style={{ marginTop: "4px" }}>
        그 봉에 자리가 있었다면 <b>진입이 사라진다</b>. 계속 늘어나면 원인을 봐야 한다.
      </div>
      <button
        type="button"
        className="error-card-close"
        style={{ marginTop: "6px" }}
        onClick={ack}
      >
        확인
      </button>
    </div>
  );
}

/**
 * 접을 수 있는 묶음 — **접어도 한 줄은 남는다** (T18 ⑥).
 *
 * 🔴 완전히 숨기면 *"모르는 것을 아는 것처럼"* 의 반대가 된다 — **볼 수 있었는데 안 본**
 * 상태다. 접힌 자리에 가장 중요한 하나(포지션·손익)를 남긴다.
 */
export function Fold(props: {
  name: string;
  summary: string;
  keep: string;
  initialShut?: boolean;
  children: ReactNode;
}) {
  const [open, toggle] = useFold(props.keep, props.initialShut);
  return (
    <section className="fold">
      <button
        type="button"
        className="fold-head"
        onClick={toggle}
        aria-expanded={open}
      >
        <span className="fold-mark">{open ? "▾" : "▸"}</span>
        <span className="card-name">{props.name}</span>
        {/* ⚠️ 접혔을 때만 요약을 낸다 — 펼친 상태에서 같은 값을 두 번 보이면
            어느 쪽이 최신인지 눈이 헷갈린다. */}
        {open ? null : <span className="faint">{props.summary}</span>}
      </button>
      {open ? props.children : null}
    </section>
  );
}

/**
 * 값 하나 — **이름 · 값 · 왜 그 값인가**.
 *
 * 🔴 **근거를 값 옆에 둔다.** `0.5` 라는 숫자만으로는 굵은지 얇은지 알 수 없다 — BTC
 * 에서는 가격의 0.0008% 지만 XRP 2.1 에서는 24% 다. 그 차이가 2026-08-18 사고였고,
 * 화면에 근거가 없어서 봉 단위로 파고들 때까지 몰랐다.
 *
 * ⚠️ `bad` 는 **모른다·이상**일 때다. 값이 나쁜 것이 아니라 **못 읽었거나 위험한**
 * 상태를 가리킨다 — 이 프로젝트는 그 둘을 구별 못 해 하루에 네 번 틀렸다.
 */
export function Fact(props: {
  name: string;
  value: string;
  note: string;
  bad?: boolean;
}) {
  return (
    <span className="fact" title={props.note}>
      <span className="fact-name">{props.name}</span>
      <b className={props.bad ? "loss" : undefined}>{props.value}</b>
      <span className="faint">{props.note}</span>
    </span>
  );
}

/**
 * **최상단으로** — 화면이 길어졌다 (사용자 요구 2026-08-19).
 *
 * ⚠️ 스크롤이 한 화면을 넘겨야 나타난다. 늘 떠 있으면 짧은 화면에서 아무 일도 안 하는
 * 단추가 오른쪽 아래를 가린다.
 *
 * ⚠️ `prefers-reduced-motion` 이면 부드럽게 안 움직인다 — CSS 가 그 판단을 든다.
 */
export function ToTop() {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const look = () => setShown(window.scrollY > window.innerHeight * 0.6);
    look();
    window.addEventListener("scroll", look, { passive: true });
    return () => window.removeEventListener("scroll", look);
  }, []);

  if (!shown) return null;
  return (
    <button
      type="button"
      className="to-top"
      aria-label="최상단으로"
      title="최상단으로"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
    >
      ↑
    </button>
  );
}

/**
 * 라벨 달린 select — 거래소 · 전략 고르기가 두 화면(RUN 시작 · 펀드 만들기)에 같은 모양으로 있었다
 * (복제 정리 2026-09-06). 선택지는 `{value, label?}` 로 받고 label 이 없으면 value 를 그대로 보여 준다.
 */
export function SelectField(props: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly { value: string; label?: string }[];
}) {
  return (
    <label className="field">
      {props.label}
      <select
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
      >
        {props.options.map((one) => (
          <option key={one.value} value={one.value}>
            {one.label ?? one.value}
          </option>
        ))}
      </select>
    </label>
  );
}

export const UNKNOWN_RUN = "RUN 미상";
/**
 * ⛔ **아무 판에나 붙이지 않는다.** 붙이면 남의 판 성적에 남의 주문이 섞이고, 그
 * 오염은 되돌릴 수 없다 — 어느 것이 섞인 것인지 나중에 못 가른다.
 */

/**
 * 주문 이름에서 **판 표식**을 되뽑는다 (T18 ⑤).
 *
 * ```
 * t-3642fc-863ce363-en-0   →  "3642fc"      새 형식
 * t-863ce36336a2-tp-1      →  "RUN 미상"      옛 형식 (판 표식이 없다)
 * ao-1234567890            →  "RUN 미상"      Gate 가 만든 주문
 * ```
 *
 * 🔴 **`ao-` 는 어느 방법으로도 못 엮는다.** 조건부가 발동해 거래소가 만든 주문이라
 * 이름이 우리 것이 아니다. 우리가 건 조건부 주문 id 로 잇는 것이 남은 길이고, 그것도
 * 실패하면 여기 남는다.
 */
export function runOf(text: string): string {
  if (!text.startsWith("t-")) return UNKNOWN_RUN;
  const parts = text.split("-");
  // 새 형식은 판 표식이 정확히 6자다. 옛 형식은 매매 id 12자가 그 자리에 있다.
  const tag = parts[1];
  if (parts.length >= 4 && tag !== undefined && tag.length === 6 && parts[2])
    return tag;
  return UNKNOWN_RUN;
}

/**
 * 주문 이름에서 **매매 id** 를 되뽑는다 — 서버의 `trade_of_text` 와 같은 규칙.
 *
 * 🔴 **주문 이름은 30자 제한이 있다.** 판 표식이 붙는 새 형식에서는 매매 id 가 8자로
 * 잘려 들어간다:
 *
 * ```
 * t-72bb3b4690f3-cl-0      옛 형식 — 매매 id 12자
 * t-82e456-72bb3b46-cl-1   새 형식 — 판 표식 6자 + 매매 id 8자
 * ```
 *
 * ⚠️ 서버가 **이 앞자리를 그대로 열쇠로** 돌려준다 (`RunStore.trace`). 두 규칙이
 * 갈리면 상세가 통째로 안 붙는다.
 *
 * ⛔ `ao-`(조건부 발동)는 Gate 가 만든 이름이라 매매 id 가 없다 — 지어내면 남의
 * 계획을 이 주문에 붙인다.
 */
export function tradeOf(text: string): string {
  // ⭐ **조건부 발동은 이름 그대로가 열쇠다** (사용자 신고 2026-08-20). `ao-{id}` 에는
  //   우리 매매 id 가 없어서, 서버가 조건부 주문 id 로 되찾아 **이 이름으로도** 계획을
  //   걸어 준다 — 화면은 추정하지 않고 그대로 묻기만 한다.
  if (text.startsWith("ao-")) return text;
  if (!text.startsWith("t-")) return "";
  const parts = text.split("-");
  if (parts.length < 3 || !parts[1]) return "";
  if (parts.length >= 4 && parts[1].length === 6 && parts[2]) return parts[2];
  return parts[1];
}

/** 판 id 에서 주문 이름에 박히는 **표식 6자**를 뽑는다 (T18 ⑤).
 *
 * ⚠️ `runOf` 의 짝이다. 한쪽만 고치면 콘솔이 자기 판을 못 알아본다.
 */
export function runTag(session: string): string {
  return session.slice(-6);
}

export function orderKind(text: string, reduceOnly: boolean): string {
  // ⭐ `ao-` 는 **Gate 가 만든** 주문이다 — 우리가 건 조건부가 발동해서 생긴다.
  if (text.startsWith("ao-")) return "손절 발동";
  if (text.includes("-take_profit-1")) return "1차 익절";
  if (text.includes("-take_profit-")) return "목표 익절";
  if (text.includes("-entry-")) return "진입";
  if (text.startsWith("t-cclose")) return "손으로 닫음";
  if (
    text.startsWith("t-probe") ||
    text.includes("cleanup") ||
    text.includes("stopprobe")
  ) {
    return "점검";
  }
  return reduceOnly ? "포지션 줄임" : "";
}

export const UNKNOWN_FRAME_S = 900;
/**
 * 모르는 축의 길이(초) — **느린 쪽**으로 잡는다.
 *
 * 🔴 두 실수의 값이 다르다: 느리면 늦게 보이고, 빠르면 **요청이 폭주한다.** 이 값이
 * 폴링 주기의 바닥이 되기도 하므로 짧게 잡으면 안 된다.
 */

/**
 * 시간축 문자열 → 초 — **이 프로젝트의 유일한 변환기**.
 *
 * @param frame `"10s"` · `"15m"` · `"4h"` · `"1d"`.
 * @returns 초. 모르는 모양이면 `UNKNOWN_FRAME_S`.
 *
 * ⚠️ 한때 **세 벌**이었다 (사용자 감사 2026-08-30): `Chart.tsx` 의 표, 여기,
 * `OrderTab.tsx` 의 문자열 자르기. 셋의 **모르는 축 기본값이 서로 달랐고**(900·900·60),
 * 그 차이는 아무 신호 없이 폴링 주기와 마커 위치에만 나타났다.
 *
 * ⛔ **크기가 0 이하면 모르는 것으로 친다.** `"0m"` 은 정규식을 통과하는데 0 초가
 * 나오고, 그 값이 폴링 주기가 되면 브라우저가 폭주한다.
 */
export function frameSeconds(frame: string): number {
  const match = /^(\d+)([smhd])$/.exec(frame);
  if (!match) return UNKNOWN_FRAME_S;
  const size = Number(match[1]);
  if (!(size > 0)) return UNKNOWN_FRAME_S;
  const unit = match[2];
  const mult =
    unit === "s" ? 1 : unit === "m" ? 60 : unit === "h" ? 3600 : 86400;
  return size * mult;
}

/**
 * 오류 카드 — **가로로 긴 붉은 카드** (사용자 2026-09-07: "오류 텍스트가 나오는 곳이 너무 가시성이 안 좋아").
 *
 * 서버가 준 `Error: 400 {"detail":"…"}` 꼴은 사람 문장(detail)만 크게, 상태 코드는 작게 아래에 둔다.
 * 닫기는 호출자가 `onClose` 로 준다 — 상태를 여기서 들지 않는다.
 */
export function ErrorCard({
  message,
  title = "오류",
  onClose,
}: {
  message: string;
  title?: string;
  onClose?: () => void;
}) {
  const parsed = parseErrorMessage(message);
  return (
    <div className="error-card" role="alert">
      <span className="error-card-mark" aria-hidden="true">
        ⛔
      </span>
      <div className="error-card-body">
        <div className="error-card-title">{title}</div>
        <div className="error-card-text">{parsed.text}</div>
        {parsed.meta ? (
          <div className="error-card-meta">{parsed.meta}</div>
        ) : null}
      </div>
      {onClose ? (
        <button type="button" className="error-card-close" onClick={onClose}>
          닫기
        </button>
      ) : null}
    </div>
  );
}

/** `Error: 400 {"detail":"…"}` → 문장과 메타로 가른다. 못 가르면 원문 그대로. */
export function parseErrorMessage(raw: string): {
  text: string;
  meta: string | null;
} {
  const trimmed = raw.replace(/^Error:\s*/i, "").trim();
  const match = /^(\d{3})\s+(\{[\s\S]*\})$/.exec(trimmed);
  if (match) {
    const code = match[1] ?? "";
    const json = match[2] ?? "";
    try {
      const body = JSON.parse(json) as { detail?: unknown };
      const detail = body.detail;
      const text =
        typeof detail === "string"
          ? detail
          : detail !== undefined
            ? JSON.stringify(detail)
            : json;
      return { text, meta: `HTTP ${code}` };
    } catch {
      return { text: trimmed, meta: null };
    }
  }
  return { text: trimmed, meta: null };
}
