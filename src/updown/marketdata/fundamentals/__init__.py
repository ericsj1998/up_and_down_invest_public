"""재무제표 I/O — `FundamentalsAdapter` 뒤로 출처(EDGAR · DART) 차이를 숨긴다 (T243).

시세 어댑터(`marketdata/toss` 등)와 같은 원칙이다: 상위 도메인은 "어느 기관의 어떤 JSON" 을
몰라야 하고, 여기서 나가는 것은 `common.domain.fundamentals.FinancialFact` 뿐이다. 지표 계산은
`analysis/fundamentals/`.
"""
