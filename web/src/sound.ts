/**
 * 매매 소리 — **화면을 안 보고 있을 때 알려 주는 것**.
 *
 * 🔴 **소리는 사실만 낸다.** 우리 원장이 아니라 **거래소 체결 이력**이 울린다 — 원장이
 * 거짓말한 적이 있고(2026-08-19: 승률 100% 라고 3시간), 그때 소리까지 원장을 따라
 * 울렸으면 사람은 잘 되고 있다고 **귀로도** 믿었을 것이다.
 *
 * ⚠️ **음원 파일을 두지 않는다.** 웹오디오로 만든다 — 바이너리를 리포에 넣으면 빌드가
 * 무거워지고, 소리 하나 고치려고 파일을 다시 만들어야 한다. 여기서는 주파수 몇 개면 된다.
 */

import { kstHour } from "./ui";

/** 무슨 일이 났나 — 소리가 갈리는 단위. */
export type Ring = "entry" | "gain" | "loss" | "exit" | "alarm";

export interface SoundSettings {
  /** 소리를 켤까. */
  on: boolean;
  /** 0~100. */
  volume: number;
  /** 야간에는 조용히 할까. */
  night: boolean;
  /** 조용해지는 시각 (KST, 0~23). */
  from: number;
  /** 다시 울리는 시각 (KST, 0~23). */
  to: number;
}

export const DEFAULTS: SoundSettings = {
  on: false,
  volume: 40,
  night: true,
  // ⭐ 사용자 지정 기본값 — 자정부터 아침 8시까지 (KST).
  from: 0,
  to: 8,
};

const SLOT = "console-sound";

export function loadSettings(): SoundSettings {
  try {
    const kept: unknown = JSON.parse(localStorage.getItem(SLOT) ?? "null");
    if (!kept || typeof kept !== "object") return DEFAULTS;
    // ⚠️ 저장된 값이 낡았을 수 있다 — 없는 칸은 기본값으로 채운다. 통째로 버리면
    //    칸 하나 늘릴 때마다 사람 설정이 초기화된다.
    return { ...DEFAULTS, ...(kept as Partial<SoundSettings>) };
  } catch {
    return DEFAULTS;
  }
}

export function saveSettings(value: SoundSettings): void {
  try {
    localStorage.setItem(SLOT, JSON.stringify(value));
  } catch {
    // ⛔ 저장 실패로 화면을 멈추지 않는다 — 소리 설정은 그 정도 값어치다.
  }
}

/**
 * 지금이 **조용히 할 시간인가**.
 *
 * @param hour 지금 시각 (KST, 0~23).
 * @param from 조용해지는 시각.
 * @param to 다시 울리는 시각.
 *
 * 🔴 **자정을 넘는 구간이 기본값이다** (0~8 은 안 넘지만 22~8 은 넘는다). 단순히
 * `from <= hour && hour < to` 로 쓰면 22~8 이 **영원히 거짓**이 되어 밤새 울린다.
 *
 * ⚠️ `from === to` 는 **구간 없음**으로 본다. "24시간 내내"로 읽으면 소리를 켜 둔
 * 사람이 아무 소리도 못 듣게 되고, 그 이유가 화면에 안 보인다.
 */
export function quietNow(hour: number, from: number, to: number): boolean {
  if (from === to) return false;
  if (from < to) return hour >= from && hour < to;
  // 자정을 넘는다 — 밤 쪽이거나 새벽 쪽이면 조용하다.
  return hour >= from || hour < to;
}

/** 지금 울려도 되나 — 설정 전체를 한 번에 본다. */
export function audible(
  value: SoundSettings,
  at: number = Date.now(),
): boolean {
  if (!value.on) return false;
  return !(value.night && quietNow(kstHour(at), value.from, value.to));
}

/**
 * 체결 한 줄이 **무슨 소리인가**.
 *
 * @param row 거래소 체결 이력 한 줄.
 *
 * ⚠️ 실현 손익은 **줄이는 주문에만** 있다 — 진입에는 없는 것이 맞다. 그래서 `pnl` 이
 * 없으면 진입으로 읽는다.
 */
export function ringOf(row: {
  pnl?: string;
  is_reduce_only?: string;
  text?: string;
}): Ring {
  // 🔴 **먼저 "나가는 주문인가" 를 묻는다** (사용자 신고 2026-08-20: *"우리가 사전에
  //    정의하지 않은 소리가 나는 상황"*).
  //
  //    예전에는 `pnl` 만 봤다. 그런데 Gate 는 **부분 체결된 청산에 pnl 을 안 준다** —
  //    실측 200건 중 21건(10.5%)이 줄이는 주문인데 진입 소리로 났고, 그중에는
  //    `ao-`(조건부 발동 = 손절)가 섞여 있었다. **손절이 났는데 귀에는 "들어갔다"
  //    로 들린 것**이고, 그것이 가장 잘못된 방향의 거짓말이다.
  //
  // ⚠️ `is_reduce_only` 는 원래 이 함수의 인자에 **선언만 돼 있고 안 읽혔다.**
  //    화면(`orderKind`)은 같은 행을 보고 "손절 발동" 이라고 옳게 적고 있었다 —
  //    규칙이 두 벌이었고 약한 쪽이 귀를 몰았다.
  const exiting =
    row.is_reduce_only === "True" || (row.text ?? "").startsWith("ao-");
  if (!exiting) return "entry";
  const value = Number(row.pnl);
  // ⛔ **모르는 것을 이겼다/졌다로 지어내지 않는다.** 나간 것은 사실이고 방향은
  //    아직 사실이 아니다 — 중립 소리가 그 상태 그대로다.
  if (row.pnl === undefined || row.pnl === "" || !Number.isFinite(value)) {
    return "exit";
  }
  return value >= 0 ? "gain" : "loss";
}

/**
 * 이번 폴링에서 **울려야 할 체결**과 다음에 쓸 기억.
 *
 * @param history 거래소 체결 이력. **`null` 은 "아직 안 왔다"** 이고 `[]` 는 "비어 있다".
 * @param seen 지금까지 본 주문 id. `null` 이면 아직 한 번도 안 심었다.
 * @returns `{ seen, ring }`. 아직 안 왔으면 `null` — 아무것도 하지 않는다.
 *
 * 🔴 **새로고침마다 이력 전체가 울렸다** (사용자 신고 2026-08-20). 심는 규칙은
 * 처음부터 있었는데 심는 **시점**이 한 박자 일렀다 — 첫 렌더는 응답 전이라 이력이
 * 비어 있고, 그 빈 것으로 심으면 곧 도착하는 수십 건이 전부 "새것" 이 된다.
 *
 * ⇒ *"안 왔다"* 와 *"없다"* 를 가른다. 둘을 같은 `[]` 로 두면 구별할 방법이 없다.
 *
 * ⚠️ **옛 것부터 울린다** — 이력은 최신이 앞이라 그대로 돌면 순서가 거꾸로 들린다.
 */
export function freshFills<T extends { id: string; finish_as?: string }>(
  history: readonly T[] | null,
  seen: ReadonlySet<string> | null,
): { seen: Set<string>; ring: readonly T[] } | null {
  if (history === null) return null;
  const filled = history.filter((row) => row.finish_as === "filled");
  // ⭐ **첫 응답은 심는 것이다** — 화면을 열었다고 지난 이력이 울리면 안 된다.
  if (seen === null) return { seen: new Set(filled.map((row) => row.id)), ring: [] };
  const ring = filled.filter((row) => !seen.has(row.id)).reverse();
  return { seen: new Set([...seen, ...ring.map((row) => row.id)]), ring };
}

/**
 * 한 번에 여러 건이 왔을 때 **무엇 하나를 울릴 것인가**.
 *
 * @param rows 이번에 새로 채워진 것들 (옛것부터).
 * @returns 울릴 한 줄. 없으면 `null`.
 *
 * 🔴 **밀린 것을 다 울리지 않는다** (사용자 신고 2026-08-29: *"사운드가 발생하면
 * 밀려 있던 체결이 모두 재생된다"*). 탭을 백그라운드에 두면 브라우저가 폴링을 늦추고,
 * 돌아오는 순간 한 응답에 수십 건이 들어온다 — 그것을 180ms 간격으로 다 울리면
 * 소리가 **알림이 아니라 소음**이 된다.
 *
 * ⛔ **그렇다고 "최신 것"을 고르지 않는다.** 다섯 건 중 손절이 섞여 있고 마지막이
 * 진입이면, 귀에는 *"들어갔다"* 만 들리고 **손절은 소리 없이 지나간다.** 그것은
 * 2026-08-20 에 이미 한 번 겪은 거짓말이고(`ringOf` 주석), 방향이 가장 나쁜 쪽이다.
 *
 * ⇒ **가장 무거운 것**을 고른다: 손절 > 익절 > 중립 청산 > 진입.
 *   한 건만 울리므로 조용하고, 무거운 것이 이기므로 거짓말하지 않는다.
 *
 * ⚠️ 셈은 화면이 따로 말한다 — 소리는 *"무슨 일이 났나"* 만 전한다.
 */
const WEIGHT: Record<Ring, number> = {
  loss: 4,
  gain: 3,
  exit: 2,
  entry: 1,
  alarm: 0,
};

export function loudest<T extends { pnl?: string; is_reduce_only?: string; text?: string }>(
  rows: readonly T[],
): T | null {
  let best: T | null = null;
  let mark = -1;
  for (const row of rows) {
    const weight = WEIGHT[ringOf(row)];
    // ⚠️ `>` 다 (`>=` 가 아니다) — 같은 무게면 **먼저 온 것**을 남긴다. 뒤엣것으로
    //    바꾸면 "최신" 이 되어 위 규칙과 어긋나고, 무엇이 울릴지 예측할 수 없어진다.
    if (weight > mark) {
      mark = weight;
      best = row;
    }
  }
  return best;
}

let box: AudioContext | null = null;

function context(): AudioContext | null {
  if (box) return box;
  const Maker =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!Maker) return null;
  box = new Maker();
  return box;
}

/**
 * 소리가 **잠겨 있나** — 브라우저는 사람이 한 번 누르기 전까지 오디오를 막는다.
 *
 * 🔴 이것을 화면이 말해야 한다. 안 말하면 사람은 켰다고 믿고 있는데 아무 소리도 안
 * 나고, 그것을 버그로 읽는다 (절대 규칙 #8).
 */
export function locked(): boolean {
  return context()?.state === "suspended";
}

/** 사람이 누른 김에 잠금을 푼다. */
export async function unlock(): Promise<void> {
  const found = context();
  if (found && found.state === "suspended") await found.resume();
}

/**
 * **타격** — 짧은 잡음 한 방 (레버가 걸리는 소리).
 *
 * 🔴 **타격감은 여기서 나온다.** 사인파만 쓰면 아무리 크게 해도 *"삐"* 일 뿐이다 —
 * 물체가 부딪히는 소리에는 반드시 **잡음 성분**이 있고, 귀는 그걸로 "때렸다" 를 읽는다.
 *
 * ⚠️ **소리를 서서히 올리지 않는다.** 램프를 주면 그 순간 타격이 죽는다 — 시작값을
 * 그대로 꽂고 떨어뜨리기만 한다.
 */
function hit(
  ctx: AudioContext,
  at: number,
  peak: number,
  hz: number,
  len: number,
): void {
  const frames = Math.max(1, Math.floor(ctx.sampleRate * len));
  const buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < frames; i += 1) data[i] = Math.random() * 2 - 1;
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  // ⭐ 띠 통과로 **색을 정한다** — 넓게 두면 '치익' 하는 바람 소리가 되고 타격이 흐려진다.
  const band = ctx.createBiquadFilter();
  band.type = "bandpass";
  band.frequency.value = hz;
  band.Q.value = 1.1;
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(peak, at);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + len);
  source.connect(band).connect(gain).connect(ctx.destination);
  source.start(at);
  source.stop(at + len);
}

/**
 * **금속 벨** — 슬롯머신·금전등록기의 "칭".
 *
 * 🔴 **배음이 정수배가 아니다.** 정수배로 쌓으면 오르간처럼 둥글게 들린다. 금속은
 * 비정수배(2.76 · 5.40)로 울리고, 그 어긋남이 *"쇳소리"* 의 정체다.
 *
 * ⚠️ 삼각파를 쓴다 — 사각파는 배음이 너무 많아 싸구려 전자음이 된다.
 */
function bell(
  ctx: AudioContext,
  at: number,
  hz: number,
  peak: number,
  len: number,
): void {
  const partials: [number, number][] = [
    [1, 1],
    [2.76, 0.5],
    [5.4, 0.22],
  ];
  for (const [ratio, share] of partials) {
    const wave = ctx.createOscillator();
    wave.type = "triangle";
    wave.frequency.value = hz * ratio;
    const gain = ctx.createGain();
    // ⚠️ 높은 배음일수록 **빨리 죽는다** — 실제 금속이 그렇고, 안 그러면 계속 쨍하다.
    const life = len * (1 / (0.6 + ratio * 0.4));
    gain.gain.setValueAtTime(peak * share, at);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + life);
    wave.connect(gain).connect(ctx.destination);
    wave.start(at);
    wave.stop(at + life);
  }
}

/**
 * 한 번 울린다.
 *
 * @param ring 무슨 일인가.
 * @param volume 0~100.
 *
 * 🔴 **슬롯머신 "챠킹"** 이 기준이다 (사용자 요구 2026-08-20: *"소리가 너무 귀여워졌는데?
 * 원래 좀 더 타격감 있는 챠킹! 하는 슬롯머신 소리였는데"*).
 *
 * 만드는 방법은 늘 같다 — **잡음 타격 + 금속 벨**. 타격이 "챠", 벨이 "킹" 이다.
 * 둘 중 하나만 있으면 각각 *"치익"* 과 *"띵"* 이 되고, 겹쳐야 챠킹이 된다.
 *
 * ⛔ 실패해도 던지지 않는다 — 소리가 안 난다고 화면이 멈추면 그게 더 나쁘다.
 */
export function play(ring: Ring, volume: number): void {
  const ctx = context();
  if (!ctx || ctx.state !== "running" || volume <= 0) return;
  // ⚠️ 잡음과 벨은 같은 값이라도 귀에 크기가 다르다 — 벨을 낮게 잡는다.
  const loud = volume / 100;
  const now = ctx.currentTime;

  if (ring === "entry") {
    // 진입 — 레버가 걸리는 **한 방**. 자주 나므로 벨을 안 붙인다.
    hit(ctx, now, loud * 0.5, 1800, 0.045);
    bell(ctx, now + 0.01, 1320, loud * 0.1, 0.1);
    return;
  }
  if (ring === "gain") {
    // 익절 — **챠킹.** 타격 뒤 벨 둘이 올라간다.
    hit(ctx, now, loud * 0.55, 2400, 0.05);
    bell(ctx, now + 0.02, 1975, loud * 0.16, 0.5);
    bell(ctx, now + 0.13, 2637, loud * 0.16, 0.75);
    return;
  }
  if (ring === "loss") {
    // 손절 — 같은 타격에 벨이 **내려간다.** 방향을 귀로 가른다.
    // ⚠️ 잡음을 낮게 깎아 둔탁하게 — 이겼을 때와 소리의 '기분' 이 달라야 한다.
    hit(ctx, now, loud * 0.55, 900, 0.075);
    bell(ctx, now + 0.02, 1245, loud * 0.15, 0.45);
    bell(ctx, now + 0.14, 830, loud * 0.15, 0.6);
    return;
  }
  if (ring === "exit") {
    // 나갔다 — **방향을 모른다.** 벨 하나가 **제자리**다: 안 올라가고 안 내려간다.
    // ⚠️ 익절·손절과 같은 타격을 쓰되 두 번째 벨이 없다 — 그 빈자리가 곧 "모른다" 다.
    hit(ctx, now, loud * 0.55, 1500, 0.06);
    bell(ctx, now + 0.02, 1568, loud * 0.16, 0.55);
    return;
  }
  // 경보 — 타격 셋. 벨을 안 쓴다: 예쁘면 경보가 아니다.
  for (let i = 0; i < 3; i += 1) {
    const at = now + i * 0.16;
    hit(ctx, at, loud * 0.6, 3000, 0.06);
    bell(ctx, at + 0.005, 2200, loud * 0.13, 0.14);
  }
}
