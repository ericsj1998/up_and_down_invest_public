/**
 * 면책 문구 — 한 곳에서만 (T244 ⑤ · T247 동의 문구와 같은 문장을 재사용한다).
 *
 * ⭐ 문구는 **버전을 가진다** (T247: `event_logs.consent_given` 에 문구 버전·시각). 문장을 고치면 버전을 올린다 —
 * 어떤 문장에 동의했는지가 기록의 뜻이다.
 */
export const DISCLAIMER_VERSION = "2026-09-09.1";

export const DISCLAIMER_TEXT =
  "이 화면의 숫자·순위·후보는 정보 제공이며 투자 권유가 아닙니다. 투자 판단과 그 결과의 책임은 본인에게 있습니다.";

/** 자동 실행 모드 동의 (T248) — 서버 `security/consent.py` 와 같은 문장·버전. 시험이 대조한다. */
export const AUTO_ORDER_CONSENT_VERSION = "2026-09-09.1";
export const AUTO_ORDER_CONSENT_TEXT =
  "자동 실행 모드를 켜면 AI 제안이 RiskManager 확정값으로 확인 없이 주문됩니다. 일 최대 건수와 총자본 대비 노출 상한 안에서만 나가며, 결과의 책임은 본인에게 있습니다.";
