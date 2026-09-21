/**
 * **결말** — 이 매매가 이겼나 졌나 (사용자 요구 2026-09-21).
 *
 * 🔴 **표와 차트가 같은 규칙을 봐야 한다.** 체결 이력 표는 '손절' 이라고 적는데 차트는 '보합'
 * 이라고 적으면, 같은 매매가 두 화면에서 다른 결말을 갖는다. 그래서 규칙을 어느 한쪽 화면이
 * 아니라 여기에 둔다.
 *
 * ⚠️ 전에는 화면의 '결말' 칸이 Gate 의 `finish_as`(체결·취소·강제청산)를 보여 줬다. 그것은
 * **주문이 어떻게 끝났나**이지 **매매가 어떻게 됐나**가 아니다 — 손절로 닫힌 주문도 "체결"
 * 이라 결과가 화면에 없었다. 그 값은 '상태' 칸으로 옮겼다.
 */

/**
 * **보합 띠** — 이 안이면 이겼다고도 졌다고도 하지 않는다 (사용자 확정: *"보합(손실률 0.5퍼 이내)"*).
 *
 * ⚠️ 이 값은 화면에 **보이는 숫자**(증거금 대비 · 배율 반영) 기준이다. 4배에서 0.5% 는
 * 가격으로 0.125% 다 — 띠를 옮길 때 어느 분모의 % 인지를 같이 본다.
 */
export const FLAT_BAND = 0.5;

export type Verdict = { label: string; tone: "gain" | "loss" | ""; why: string };

/** 수익률 하나로 가르는 판정 — 셋을 나누는 **유일한 자리**다. */
export function verdictOfPct(net: number): Verdict {
  if (Math.abs(net) <= FLAT_BAND) {
    return {
      label: "보합",
      tone: "",
      why: `증거금 대비 ${net >= 0 ? "+" : ""}${net.toFixed(2)}% — 보합 띠(±${FLAT_BAND}%) 안이다`,
    };
  }
  return net > 0
    ? { label: "익절", tone: "gain", why: `증거금 대비 +${net.toFixed(2)}%` }
    : { label: "손절", tone: "loss", why: `증거금 대비 ${net.toFixed(2)}%` };
}

/**
 * 체결 이력 한 줄의 결말.
 *
 * 판정 순서 (근거가 확실한 쪽부터):
 *   ① 원장이 취소라고 하면 취소 — 손익을 지어내지 않는다
 *   ② 실현 수익률(`net`)이 있으면 그것으로. 보합 띠 안이면 보합
 *   ③ 수익률이 없고 거래소 실현 USDT 만 있으면 **부호만** 말한다 (보합은 % 가 있어야 잰다)
 *   ④ 아직 안 닫혔으면 보유중
 *   ⑤ 아무 근거도 없으면 빈칸 — 모르는 것을 옮기지 않는다
 */
export function verdictOf(args: {
  net: number | null;
  pnl: string | undefined;
  outcome: string | undefined;
  reduceOnly: boolean;
  tradeOpen: boolean;
}): Verdict {
  const { net, pnl, outcome, reduceOnly, tradeOpen } = args;
  if (outcome === "취소") {
    return { label: "취소", tone: "", why: "원장이 취소로 적은 매매다 — 손익이 없다" };
  }
  if (net !== null) return verdictOfPct(net);
  const money = pnl === undefined || pnl === "" ? null : Number(pnl);
  if (money !== null && Number.isFinite(money)) {
    return money >= 0
      ? { label: "익절", tone: "gain", why: `거래소 실현 +${money} USDT · 수익률을 못 재 보합은 안 가린다` }
      : { label: "손절", tone: "loss", why: `거래소 실현 ${money} USDT · 수익률을 못 재 보합은 안 가린다` };
  }
  if (!reduceOnly && tradeOpen) {
    return { label: "보유중", tone: "", why: "아직 안 닫혔다 — 결말은 청산될 때 난다" };
  }
  return { label: "—", tone: "", why: "결말을 가릴 근거가 없다 — 지어내지 않는다" };
}

/**
 * 차트 상자의 결말 딱지 — 손익률과 "아직 열려 있나" 만 안다.
 *
 * ⚠️ 열린 매매는 **보유중**이다. 지금 이익이라고 "익절" 이라 적으면 되돌아갔을 때 화면이
 * 거짓말한 것이 된다 — 결말은 청산될 때 난다.
 */
export function verdictOfTrade(gainPct: number | null, open: boolean): Verdict {
  if (open) return { label: "보유중", tone: "", why: "아직 안 닫혔다" };
  if (gainPct === null) {
    return { label: "청산", tone: "", why: "손익을 못 읽었다 — 결말을 지어내지 않는다" };
  }
  return verdictOfPct(gainPct);
}
