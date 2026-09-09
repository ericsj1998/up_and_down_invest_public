"""재무 지표 — 사실(`FinancialFact`)에서 투자자 관점 숫자로 (T243 · 순수 · 결정론).

I/O 가 없다. 입력은 사실 목록과 가격, 출력은 `FundamentalSnapshot`. 같은 입력이면 같은
출력이다 (규칙 #5).

| 모듈 | 하는 일 |
|---|---|
| `series` | 시점 정합(`known_facts`) · 분기 흐름 복원(Q4 = FY - 3분기 · YTD 차분) · TTM · 시점 값 |
| `ratios` | PER · PBR · PSR · EV/EBITDA · FCF 수익률 · 부채비율 · 이자보상 · ROE · 성장 … |
| `percentile` | 자기 5년 역사 백분위 (월말 표본) |
| `score` | 저평가 점수 — 규칙은 `config/fundamentals/us_gaap.yml` 에 글로 |
| `snapshot` | 위를 엮어 한 종목의 표를 만든다 |
"""
