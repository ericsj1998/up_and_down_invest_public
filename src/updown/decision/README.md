# `decision` — 결정 (spec §4.6, §4.7, §4.8, §4.15)

**책임**: RiskManager(손절/익절/수량 확정), 시장 국면 판정, 서킷 브레이커,
**Portfolio Allocation 판단**(§4.7 — 목표 비중 대비 이탈, 리밸런싱 제안), 버킷 전환 판정.

**입력** `TradeProposal`(analysis) + `PortfolioState`(portfolio) → **출력** `ApprovedOrder` / `Rejection`.

**하지 않는 것**: 지표 계산(→ analysis), 주문 전송(→ execution).

**절대 규칙**:
- **손절/익절의 SSoT 는 여기다** (#4). 다른 계층이 이 값을 바꾸면 리뷰 반려다.
- **손절선은 상향만 허용**한다 (#3, §6.9). 버킷 전환 같은 우회 경로도 차단한다.
- 안전장치는 리스크 프리셋으로 끌 수 없다.

**왜 Allocation 이 portfolio 가 아니라 여기인가**: §4.18 이 "Unified Portfolio=사실 /
Allocation=판단"으로 나눈다. 판단은 전부 이 계층에 모은다 (plan P-2).
