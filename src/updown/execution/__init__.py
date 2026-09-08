"""집행 계층 — 주문 감시·집행 (spec §4.10).

`ApprovedOrder` 를 그대로 집행한다. **가격·수량을 바꿀 권한이 없다** — 이미 확정된 값이다.
브로커 어댑터 획득은 `gateway.OrderGateway` 가 독점한다 (§12.4, plan D-12).
"""
