# AI 채팅 — 시험한 시나리오와 결과

> 어시스턴트가 **실제 모델·실제 도구**로 답한 대화의 기록. 설계는 [ai_agents_and_mcp.md](ai_agents_and_mcp.md), 엔진은
> [T258](../planning/tasks/T258_chat_eval_engine.md). 모든 실측은 로컬 데모(바이낸스 테스트넷 · 주식 페이퍼 · EDGAR 100종 적재)에서
> `nvidia/nemotron-3-super-120b-a12b` 로. 숫자는 **그날 그 모델**의 값이고, 프롬프트가 바뀌면(`chat-1.x`) 새 참가자라 표본이 0 부터 다시 쌓인다.

## 1. 판정 기준 (측정 전에 선언)

한 사례는 다섯을 동시에 만족해야 **통과**다 — ① 기대한 도구가 불렸다 ② 도구 오류가 없다 ③ 답이 있다 ④ 대시보드를 기대하는 사례면 명세가 있다
⑤ 명세의 빈 칸(`missing` = 근거 없는 숫자)이 0 이다. 도구가 멀쩡한데 모델이 규칙을 안 지킨 것(추세 질문에 `market_view` 를 안 부름)도 **실패**로 센다 —
사용자가 보는 것은 답이지 도구가 아니다. 한 번의 실패로 프롬프트를 고치지 않는다: 같은 프롬프트로 결과가 갈리면 모델 편차이고, **세 번 누적** 뒤 판단한다.

## 2. 사례 15 (`evaluate.py CASES` · 추천 질문과 같은 문장)

| # | 사례 | 질문 | 기대 도구 | 대시보드 기대 |
|---|---|---|---|---|
| 1 | symbol_resolve | 테슬라 종목 코드가 뭐야? | `symbol_resolve` | — |
| 2 | market_view | 비트코인 지금 추세 어때? | `market_view` | — |
| 3 | valuation | 애플 지금 저렴해, 비싸? | `valuation` | — |
| 4 | positions | 내 포지션 몇 % 이득이야? | `positions` | — |
| 5 | extremes | 엔비디아 고점 근처야? | `extremes` | — |
| 6 | playbook_expectation | private_strategy 매매법 과거 성과 알려줘 | `playbook_expectation` | — |
| 7 | propose_order | NVDA 224 에 사고 217 손절, 234 목표로 제안해줘 | `propose_order` | — |
| 8 | recommend_by_budget | 200만원으로 미국주식 시작하려는데 뭐가 좋아? | `recommend_by_budget` | — |
| 9 | portfolio_exposure | 내 비중에 쏠림 있어? | `portfolio_exposure` | — |
| 10 | trade_journal | AI 매매일지 보여줘 | `trade_journal` | — |
| 11 | screen | 미국주식 저평가 순위 상위 10개 보여줘 | `screen` | ✅ |
| 12 | macro_view | 지금 공포지수(VIX) 얼마야? 환율이랑 미국 금리도 | `macro_view` | — |
| 13 | composite | 200만원으로 미국주식 시작하려는데 뭐가 좋아? 그리고 내 비중도 봐줘 | `recommend_by_budget` + `portfolio_exposure` | ✅ |
| 14 | profile_wizard | 투자 처음인데 성향 진단부터 도와줘 | `profile_wizard` | — |
| 15 | buy_question | 구글 주식 지금 살만 해? | `market_view` + `extremes` + `valuation` | ✅ |

## 3. 실측 묶음

### 3.1 첫 실측 — 2026-09-10 04:14~04:19 KST · chat-1.3 · 12사례

12사례 320.6초 · **9 통과** · 도구 오류 **0**. 실패 3은 전부 모델의 규칙 미준수(도구는 멀쩡). 그 사이 NVIDIA 500 이 4회 났지만 폴백·재시도로 전부 답했다.

| 사례 | 불린 도구 | 판정 | ms | 왕복 | 토큰 | 대시보드 |
|---|---|---|---|---|---|---|
| symbol_resolve | symbol_resolve | ✅ | 6,949 | 2 | 6,170 | — |
| market_view | symbol_resolve | ❌ 종목만 풀고 답했다(추세 도구 안 부름) | 24,064 | 2 | 6,868 | — |
| valuation | symbol_resolve · valuation · render_dashboard | ✅ | 44,907 | 4 | 23,422 | ✅ 빈 칸 0 |
| positions | positions | ✅ | 24,201 | 2 | 6,306 | — |
| extremes | symbol_resolve · extremes | ✅ | 14,228 | 3 | 10,293 | — |
| playbook_expectation | playbook_expectation | ✅ | 13,656 | 2 | 12,185 | — |
| propose_order | symbol_resolve · market_view · propose_order | ✅ | 25,403 | 4 | 18,074 | — |
| recommend_by_budget | recommend_by_budget · render_dashboard | ✅ | 40,714 | 3 | 13,077 | ✅ 빈 칸 0 |
| portfolio_exposure | portfolio_exposure | ✅ | 14,175 | 2 | 6,804 | — |
| trade_journal | trade_journal | ✅ | 7,650 | 2 | 6,298 | — |
| screen | screen | ❌ 순위를 글로만(규칙 8 대시보드 없음) | 40,022 | 2 | 9,060 | ✗ |
| composite | recommend_by_budget · portfolio_exposure · valuation | ❌ 대시보드 없음 | 64,644 | 4 | 22,413 | ✗ |

읽기: 도구는 전부 산다. 환각(`missing`) 0. 남은 것은 프롬프트 준수 — 그래서 chat-1.4 에서 규칙 8("숫자가 여럿이면 마지막에 대시보드")을 명시했다.

### 3.2 두 번째 실측 — 2026-09-10 10:05 KST · chat-1.4 · 13사례

**12 통과** · 515초 · 도구 오류 0. `market_view` 사례만 실패(세 번 불린 것 중 하나가 실패). 새 사례 `macro_view` 통과(대시보드 포함 · VIX 16.46 ·
원달러 · EFFR). 첫 실측에서 실패했던 `screen` · 합성은 이번엔 대시보드까지 통과 — 같은 프롬프트인데 결과가 갈렸다 = 모델 편차. 누적 2/3.

### 3.3 세 번째 실측 — 대기 (chat-1.5 · 15사례 · T271 뒤)

세 번 누적 뒤 `market_view` 가 반복 실패하면 규칙 2·3 의 문장을 손본다(새 참가자 chat-1.5). 그 전에는 고치지 않는다.

## 4. 시험 밖의 실제 대화 — 무엇이 깨졌고 무엇을 고쳤나

| 날짜 | 사용자 질문 | 무슨 일 | 고친 것 |
|---|---|---|---|
| 09-10 00:47Z | "200만원으로 미국주식 투자 해보려 해. 뭐가 좋아? 그리고 내 비중도 봐줘" | 도구 둘은 통과(71ms · 535ms)했는데 모델이 **최종 답을 못 냈다** — 기본 모델 500 → 폴백 사슬(폐기 410 여섯 · 60초 타임아웃 둘) 3.5분 | 모델 풀 정리(폐기 모델 제거 · 410 = 모델 없음으로 따로 셈). 재실측 63초 · 3왕복 · 토큰 8,626/1,477 · 폴백 0 |
| 09-10 | "테슬라 주가 향후 동향" | `market_view` 한 번이 **40초** — 축마다 320봉을 브로커에서 받았다 | 판과 같은 봉 캐시(`StoredCandles`)를 도구가 쓴다 → 축 하나 20초 → 지금 수백 ms |
| 09-10 | "미국주식 50개 훑어서 순위 매겨줘" | 모델이 50종을 하나씩 부르려 했다 — 왕복 폭발 | 서버 배치 도구 `screen`(서버가 정렬 · 상위 N ≤ 50) + 대시보드 명세 도구 |
| 09-10 | "애플 지금 저렴해?" | `valuation` 404 — EDGAR 미적재 | 사용자 연락처 설정 뒤 EDGAR 적재(100종) · 유니버스 상위 100 자동 적재(T260) |
| 09-10 | "오라클 종목 어떻게 생각해. 오늘 실적 발표라 오를 것 같은데" | `symbol_resolve` 가 "오라클" 을 못 풀어 모델이 **티커를 되물었다** — 손 별칭 사전에 7종만 있었다 | 토스가 주는 한글·영문 이름 503종을 이름표로 만들어 두 번째 사전으로(손 별칭 우선). 실측 "오라클 종목 어떻게 생각해" → ORCL · "버크셔" → BRK.B · "JPMorgan" → JPM |
| 09-10 | (같은 수정 뒤) "JPMorgan" | 한 글자 종목(A · J · T)이 긴 질문 안에 "들어 있다" 고 잡혀 A(애질런트)로 풀렸다 | 3글자 미만 별칭은 정확 일치만 |
| 09-10 | "지금 공포지수 얼마야? 환율이랑 금리도" | 야후 `^TNX` 를 ÷10 해 10년물이 0.4% 로 나왔다 · 전일 종가가 5일 전 값이었다 | `^TNX` 는 이미 % · 전일 = 마지막에서 두 번째 종가 · BLS "-" 월 건너뜀 |

## 5. 무엇을 재고 무엇을 안 재나

- 잰다: 도구별 통과/불림/누락 커버리지 · 왕복 · ms · 토큰 · 환각(`missing`) · 모델별 원가(T249).
- 안 잰다: 답이 "좋은가"(사람 채점 금지 · 규칙 #11) · 사용자가 대시보드와 글 중 무엇을 더 쓰는지(선호는 측정 대상이 아니다).
- CI 는 모델을 부르지 않는다 — 가짜 모델로 루프·판정·커버리지만 시험한다(`tests/test_ai_chat_eval.py`). 실제 실측은 사람이 AI 리포트 화면에서 누른다.
