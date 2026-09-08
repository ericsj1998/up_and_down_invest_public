"""업비트 어댑터 — 코인 현물 **조회 전용** (spec §4.2, P0-7).

| 모듈 | 책임 |
|------|------|
| `client` | HTTP 호출 · 그룹별 rate limit 스로틀 · 지수 백오프 재시도 |
| `mapping` | 업비트 필드 ↔ 도메인 모델. **업비트 필드 이름을 아는 유일한 곳** |
| `adapter` | `BrokerAdapter` 조회 구현 + 주문 경로 차단 |
| `ws` | 티커 WS 스트림 + 자동 재접속 |

실측 규격은 `docs/platform/upbit_api_notes.md` 에 있다.

> ⚠️ **절대 규칙 #0** — 주문 경로는 `UpbitAdapter` 를 직접 생성·import 하지 않는다.
> 반드시 `execution.gateway.OrderGateway` 를 통한다 (spec §12.4, plan D-12).
> 이 어댑터에는 주문 메서드가 아예 없다 (명시적 예외).
"""
