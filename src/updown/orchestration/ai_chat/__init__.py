"""AI 투자 어시스턴트 채팅 — 도구 기반 에이전트 (T248 · 2026-09-09).

| 모듈 | 하는 일 |
|---|---|
| `snapshot` | 봉 → 요약 스냅샷(압축 OHLC · 우리 지표 · 창 고저 · 추세) — 순수 |
| `aliases` | 종목 별칭 사전(`config/ai_aliases.yml`) + 퍼지 매칭 — 순수 |
| `tools` | 도구 등록부: 이름 · 한국어 설명 + 유사어 · JSON 스키마 · 구현(우리 API/도메인 호출) |
| `agent` | 계획 → 도구 → 종합 루프. 모델은 `llm.ChatClient` 로만 말한다 |

⛔ 여기서 **주문을 내지 않는다.** `propose_order` 는 RiskManager(`decision.risk.manual.confirm`)로
확정한 값을 **제안**으로 돌려주고, 사람이 확인(화면 단추)하면 기존 차트 주문 경로
(`/walkforward/live/custom`)가 판을 띄운다 (§5.3.1 · 규칙 #2).
자동 실행 모드(동의 · 일 3건 · 30%)는 다음 조각.

입주 근거: 여러 도메인을 **호출만** 한다 — `analysis`(지표·구조물) · `decision`(확정) ·
`marketdata`(봉) · `llm`(모델).
"""
