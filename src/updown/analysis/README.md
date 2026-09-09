# `analysis` — 분석 (spec §4.3, §4.3.1, §4.4, §4.5, §4.16)

**책임**: 자체 구현 지표(§2.1 — 라이브러리 위임 금지), 차트 구조물 탐지, 셋업 탐지 플러그인,
Signal Aggregator, Trend Service. 산출물은 `TradeSetup` / `TradeProposal` 이다.

**하지 않는 것**:
- 손절/익절/수량 **확정** — SSoT 는 `decision.RiskManager` 다 (절대 규칙 #4).
- 주문 집행. `analysis → execution` import 는 `.importlinter` 의 `forbidden` 계약이 차단한다.

**새 차트 개념을 추가할 때**: 탐지 파일 1개 + 레지스트리 1줄이면 끝나야 한다.
기존 코드를 고쳐야 한다면 플러그인 계약(§4.3.1)이 잘못 잡힌 것이다.

**임계값**: 코드에 박지 않는다. `RuleParams` 로 설정(YAML/DB)에서 주입한다 (§4.3.1).
값 자체는 **표준 이론값으로 고정**하며 성과를 보고 미세조정하지 않는다 (§5.6.1 ②, §5.6.2).

## 하위 패키지

| 패키지 | 책임 | 문서 |
|--------|------|------|
| `detectors/` | 셋업 탐지 플러그인 계약 (`SetupDetector`·`MarketContext`) | spec §4.3.1 |
| `structures/` | **공용** 구조물 — 스윙·추세선·채널·박스·합류·영속화 | [docs/rules/structure_rules.md](../../../docs/rules/structure_rules.md) |
| `indicators/` | **자체 구현** 지표 — SMA/EMA·ATR·RSI·거래량 배수·크로스 | [docs/rules/indicator_rules.md](../../../docs/rules/indicator_rules.md) |
| `fundamentals/` | **재무 지표** (T243) — 시점 정합(`known_facts`) · 분기 복원(Q4 = FY − 3분기) · TTM · PER/PBR/EV·EBITDA/FCF·부채·ROE·성장 · 자기 5년 백분위 · 저평가 점수. 입력은 `FinancialFact` 와 종가, IO 없음 | [docs/planning/tasks/T243_fundamentals_edgar.md](../../../docs/planning/tasks/T243_fundamentals_edgar.md) |

**지표는 라이브러리에 위임하지 않는다** (절대 규칙 #9). pandas-ta 는 정답지 대조용
**dev 의존성**이며, 런타임 코드가 pandas·numpy 를 import 하지 않는지 테스트가 강제한다.

⚠️ **지표 출력은 입력과 길이가 같다.** 워밍업은 `None` 이고 잘라내지 않는다 — `structures/`
가 봉 번호를 x축으로 쓰므로, 길이가 어긋나면 손절가가 엉뚱한 봉의 ATR 로 계산되면서도
아무 예외가 나지 않는다.

**`structures/` 를 플러그인이 직접 다시 계산하지 않는다.** 각자 스윙을 구하면 합류
(confluence) 판정이 불가능해지고, 추세선을 각자 그으면 "꼬리 끝 기준"(§6.5) 전역 규칙을
어기는 경로가 생긴다.

⚠️ **스윙에는 출력이 둘이고 섞어 쓰면 안 된다** — 구조물 작도는 `find_pivots()`,
전저점·전고점(§6.4)은 `prior_swings()`. 자세한 이유는 위 문서 §2.
