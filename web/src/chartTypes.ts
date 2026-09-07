/**
 * 차트 페이로드 타입.
 *
 * ⚠️ `api.ts` 와 파일을 가른 이유는 **모양이 다르기 때문**이다. 나머지 응답은 서버가
 * 고정한 필드를 갖는데, 도형(`Shape`)은 플래그마다 키가 다르다 — 그것을 한 파일에
 * 섞으면 "이 응답의 필드는 정해져 있다" 는 규칙이 흐려진다.
 */

export type Candle = {
  ts: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
};

/** 도형 하나 — 플래그마다 키가 다르므로 느슨하게 받는다. */
export type Shape = Record<string, unknown>;

/**
 * **이 도형이 어느 축에서 왔는가** — 서버가 `projected` 에 그 축을 적는다 (없으면
 * 이 축이 스스로 찾은 것이다).
 *
 * 🔴 하위 축(5m·1m)에는 **두 종류가 겹쳐** 온다: 그 축이 스스로 찾은 것과, 진입
 * 축(15m)에서 얹어 준 것. 겹쳐 보여야 어긋남이 보이지만 **구별되지 않으면** 사람은
 * *"축마다 자리가 다 다르다"* 로 읽는다 (사용자 지적 2026-08-19).
 *
 * ⚠️ **매매 로직이 쓰는 것은 진입 축 하나뿐이다.** 그 축이 아닌 도형은 참고이고,
 * 화면이 그 차이를 말하지 않으면 사람은 안 쓰이는 선을 보고 판단하게 된다.
 */
export function borrowedFrom(shape: Shape): string {
  const from = shape["projected"];
  return typeof from === "string" ? from : "";
}

export type Layer = {
  flag: string;
  /**
   * 비었거나 주의할 것이 있으면 그 이유.
   *
   * 🔴 **빈 레이어와 미계산을 가르는 값이다.** 0건이라는 것과 계산이 안 돌았다는 것은
   * 전혀 다른 사실인데, 이 값이 없으면 화면에서 똑같이 보인다.
   */
  note: string;
  count: number;
  shapes: Shape[];
};

export type Frame = {
  timeframe: string;
  /** 이 시간축 전체에 대한 경고 (봉 부족 등). */
  note: string;
  bars: number;
  candles: Candle[];
  layers: Layer[];
};

/** 지금 들고 있는 포지션의 계획값 — 차트가 선으로 그린다. */
export type Plan = {
  entry?: string;
  stop?: string;
  first?: string;
  target?: string;
  /**
   * 추세추종(full_ride)인가 — 참이면 **고정 익절이 없다**. `first`·`target` 은
   * 진입+100R 짜리 먼 자리표시자라 그리면 "29배 목표" 처럼 거짓말한다. 실제 청산은
   * 트레일 손절이므로, 참일 때 차트는 목표선을 숨기고 손절을 "청산" 으로 그린다.
   */
  full_ride?: boolean;
};
