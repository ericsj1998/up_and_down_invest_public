"""Trend Service — 종목별 타임프레임 추세의 SSoT (P1-4 · spec §4.16).

> "역추세 매매를 최대한 피한다"를 담당하는 독립 모듈.

| 모듈 | 책임 |
|------|------|
| `market_structure` | HH/HL vs LH/LL 판정 + 200 SMA 기울기 |
| `choch_bos` | DOWN→UP 3단계 확인 (CHoCH → BOS → 200MA 재탈환) |
| `state_machine` | 상태 전이 + **비대칭 히스테리시스** |
| `service` | `TrendState` 조회 창구 + 전이 이벤트 기록 |

**다른 모듈은 추세를 다시 계산하지 않는다.** MA 배열이나 스윙으로 자체 판정하는 코드가
생기면 "셋업 게이트가 본 추세"와 "국면 판정이 본 추세"가 갈라진다 — 그것을 막기 위해
§4.16 이 이 모듈을 분리했다. 상세 규약: `service` 모듈 docstring · `docs/rules/trend_rules.md`.

⚠️ **전이는 비대칭이다** — UP 진입은 3단계 전부, DOWN 이탈은 구조 이탈 하나다.
느슨한 쪽이 리스크 축소 방향이며, 그 비대칭 자체가 깜빡임을 막는다
(`state_machine` 모듈 docstring).
"""
