"""`MarketContext` 조립과 미래 참조 차단 (P1-3 · spec §4.3.1, §4.11).

| 모듈 | 책임 |
|------|------|
| `guard` | as-of 경계 강제 — `AsOfSequence` · `LookaheadError` |
| `builder` | 멀티 TF 캔들 + 지표 + 구조물을 `MarketContext` 로 조립 |

**조립만 한다.** 계산은 `indicators/`·`structures/` 가, 저장은 `marketdata/` 와
`structures.repository` 가 한다. 여기에 판단이 생기면 도메인으로 내려보낸다.
"""
