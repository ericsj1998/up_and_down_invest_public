/**
 * 추세강도(ADX)와 그 문턱 — **차트가 "지금 무엇을 기다리는지" 말하게 하는 값**.
 *
 * 🔴 **판단의 절반이 여기 있는데 화면에 없었다** (사용자 지적 2026-08-30). 근거 원문은
 * ADX 를 인용한다 —
 *
 *     "약상승 캐리 · close 2,434.65 > SMA200 2,042.10 · ADX 34.9 <= 35 (본대 부재)"
 *
 * — 그런데 프론트엔드 전체에 `adx` 라는 글자가 한 번도 없었다. 즉 **왜 캐리이고 왜
 * 본대가 아닌지**를 화면에서 확인할 방법이 없었다.
 *
 * ⛔ **여기서 ADX 를 계산하지 않는다** (규칙 #9). 서버가 세션이 쓰는 그 `adx()` 로 재서
 * 봉별 점으로 보낸다 — 화면이 다시 계산하면 판정과 다른 값을 그리게 되고, 그러면
 * *"왜 저기서 청산됐지"* 가 영영 안 풀린다. 이 파일이 하는 일은 **읽기와 비교**뿐이다.
 */

/** 문턱 하나 — 서버 `overlay.adx_gates` 의 도형 하나에 대응한다. */
export interface Gate {
  /** 설정 키 (`long_adx` · `adx_exit_long` …). */
  key: string;
  /** 사람이 읽는 이름 ("롱 진입"). */
  label: string;
  /** 문턱 값. */
  value: number;
  /**
   * 어느 방향으로 걸리나.
   *
   * - `in`  진입 문 — **이상**이면 열린다
   * - `out` 청산 문 — **이하**면 나간다 (약해져서)
   * - `up`  본대 인계 — **이상**이면 나간다 (강해져서). 캐리 전용이고 방향이 반대다
   */
  kind: "in" | "out" | "up";
  /** 이 문턱을 선언한 플레이북 — 번들이면 본대와 캐리가 섞여 들어온다. */
  owner: string;
}

/**
 * 서버 도형을 문턱으로 읽는다.
 *
 * @param shapes `overlay.adx_gates` 의 도형들.
 * @returns 읽어낸 문턱들. 숫자가 아닌 값은 **버린다**.
 *
 * ⚠️ 못 읽은 것을 0 으로 채우지 않는다 — 0 은 "문턱이 0" 으로 그려지고, 그러면 화면이
 * 없는 규칙을 지어낸다 (규칙 #8).
 */
export function readGates(shapes: readonly Record<string, unknown>[]): Gate[] {
  const out: Gate[] = [];
  for (const shape of shapes) {
    const value = Number(shape["value"]);
    const kind = String(shape["kind"] ?? "");
    if (!Number.isFinite(value)) continue;
    if (kind !== "in" && kind !== "out" && kind !== "up") continue;
    out.push({
      key: String(shape["key"] ?? ""),
      label: String(shape["label"] ?? ""),
      value,
      kind,
      owner: String(shape["owner"] ?? ""),
    });
  }
  // 진입 문을 먼저, 그 안에서는 높은 문턱부터 — 화면에서 35 · 20 · 31 · 16 순으로 읽힌다.
  const rank = { in: 0, up: 1, out: 2 };
  return out.sort((a, b) => rank[a.kind] - rank[b.kind] || b.value - a.value);
}

/**
 * 지금 값이 이 문을 **넘었나**.
 *
 * @param adx 지금 추세강도.
 * @param gate 문턱.
 * @returns `met` 은 조건 성립 여부, `gap` 은 문턱까지 남은 거리(절대값).
 *
 * 🔴 **`met` 의 뜻이 문마다 다르다.** 진입 문은 *"열렸다"* 이고 청산 문은 *"나간다"* 다 —
 * 같은 참이라도 좋은 소식과 나쁜 소식이 갈린다. 화면이 색을 그렇게 칠해야 한다.
 *
 * ⚠️ 경계는 **판정 코드와 같아야 한다**: 진입은 `>=`(탐지기 `strength < long_adx` 면
 * 탈락), 약화 청산은 `<=`(세션 `power <= adx_gate`), 본대 인계는 `>=`
 * (세션 `power >= adx_exit_above_long`). 부등호 하나가 다르면 화면이 판정을 반박한다.
 */
export function statusOf(adx: number, gate: Gate): { met: boolean; gap: number } {
  const met = gate.kind === "out" ? adx <= gate.value : adx >= gate.value;
  return { met, gap: Math.abs(adx - gate.value) };
}

/**
 * 문 하나를 한 줄로 — *"롱 진입 ≥35 · 0.1 모자람"*.
 *
 * @param adx 지금 추세강도.
 * @param gate 문턱.
 *
 * ⛔ **"이제 곧 들어간다" 같은 말을 만들지 않는다.** 화면은 값과 거리만 말하고, 무엇을
 * 할지는 판정이 정한다 (원칙 P4: 분석 ≠ 결정). 여기서 예언하면 사람이 그것을 근거로
 * 손을 대고, 그 결과는 원장에 남지 않는다.
 */
export function gateText(adx: number, gate: Gate): string {
  const { met, gap } = statusOf(adx, gate);
  const sign = gate.kind === "out" ? "≤" : "≥";
  const tail = met ? "충족" : `${gap.toFixed(1)} 모자람`;
  return `${gate.label} ${sign}${gate.value} · ${tail}`;
}

/**
 * 이 문이 **좋은 소식인가** — 화면 색을 정한다.
 *
 * @param gate 문턱.
 * @param met 성립했나.
 *
 * 진입 문이 열린 것은 초록, 청산·인계 문이 걸린 것은 빨강이다. 안 걸린 문은 회색 —
 * *"아직 아무 일도 없다"* 를 색으로도 말한다.
 */
export function gateTone(gate: Gate, met: boolean): "good" | "bad" | "idle" {
  if (!met) return "idle";
  return gate.kind === "in" ? "good" : "bad";
}


/**
 * **SMA 가 내려가고 있나** — 숏 진입의 두 번째 조건.
 *
 * @param shapes `overlay.ma_slope` 의 도형들.
 * @returns 읽어낸 값. 없거나 숫자가 아니면 `null`.
 *
 * 🔴 가격이 SMA 아래라고 숏이 아니다 — 급락 직후에는 가격이 한참 아래인데 거기가
 * 반등 자리일 수 있다. 그래서 **SMA 선 자체가 하루(6봉) 전보다 내려와 있을 것**을
 * 더 요구한다. 가격은 널뛰지만 SMA200 은 천천히 움직이므로, 선의 방향이 곧
 * *"진짜 하락 추세인가"* 다.
 */
export function readSlope(
  shapes: readonly Record<string, unknown>[],
): { bars: number; pct: number } | null {
  const one = shapes.at(0);
  if (!one) return null;
  const bars = Number(one["bars"]);
  const pct = Number(one["pct"]);
  if (!Number.isFinite(bars) || !Number.isFinite(pct)) return null;
  return { bars, pct };
}

/**
 * **얼마나 크게 걸까** — 변동성 타게팅 승수 (T81).
 *
 * @param shapes `overlay.vol_target` 의 도형들.
 * @returns 읽어낸 값. 없거나 숫자가 아니면 `null`.
 *
 * ⚠️ `lookback` 을 같이 들고 온다. 짧은 ATR 사이징과 **다른 것**이 이 규칙의 요점인데
 * (짧게 재면 강추세에서 수량이 줄어 OOS 0/5 로 무너진다 · T81 §8-D), 승수만 보면
 * 어느 쪽인지 구별할 수 없다.
 */
export function readVol(
  shapes: readonly Record<string, unknown>[],
): { vol: number; lookback: number; mult: number } | null {
  const one = shapes.at(0);
  if (!one) return null;
  const vol = Number(one["vol"]);
  const lookback = Number(one["lookback"]);
  const mult = Number(one["mult"]);
  if (!Number.isFinite(vol) || !Number.isFinite(mult)) return null;
  return { vol, lookback, mult };
}


/**
 * **지금 어느 전략이 서 있나** — 문 하나가 아니라 결론.
 *
 * @param shapes `overlay.stance` 의 도형들.
 * @returns 결론. 없으면 `null`.
 *
 * 🔴 문턱 칩만으로는 결론을 잘못 읽는다 (사용자 지적 2026-08-30). *"숏 진입 ≥20 · 충족"*
 * 을 보고 숏 자리라고 읽었는데, 숏은 **ADX · SMA 아래 · 기울기 음수** 셋이 다 맞아야
 * 열린다. 문 하나가 열린 것과 전략이 선 것은 다르다.
 *
 * ⛔ **여기서 판정하지 않는다.** 서버가 탐지기 결과를 그대로 넘긴다 — 화면이 자기
 * 규칙을 가지면 판정과 갈리고, 그 갈림은 조용하다.
 */
export function readStance(
  shapes: readonly Record<string, unknown>[],
): { state: string; why: string; count: number } | null {
  const one = shapes.at(0);
  if (!one) return null;
  const state = String(one["state"] ?? "");
  if (!state) return null;
  return {
    state,
    why: String(one["why"] ?? ""),
    count: Number(one["count"] ?? 0) || 0,
  };
}
