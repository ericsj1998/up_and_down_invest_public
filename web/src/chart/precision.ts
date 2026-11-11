/**
 * 판 차트 가격 축 자릿수 — 가격이 아주 작은 코인(PEPE 류)도 축 숫자가 서로 갈리게.
 *
 * 전에는 소수 한 자리로 고정이라 0.00001 짜리 코인이 축에 전부 "0.0" 으로 찍혔다.
 * 1 미만은 유효 숫자 네 자리를 남긴다(0.00001234 → 소수 8 자리).
 */
export function axisPrecision(close: number | undefined): number {
  if (close === undefined || !Number.isFinite(close) || close <= 0) return 1;
  if (close >= 1000) return 1;
  if (close >= 10) return 2;
  if (close >= 1) return 3;
  return Math.min(10, Math.max(4, Math.ceil(-Math.log10(close)) + 3));
}
