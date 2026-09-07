"""측정 리포트 **읽기** 계층 (Phase 5 §5-9 A1).

## 왜 만들었나 — 계산과 서식이 한 함수에 있었다

`scripts/research/pool_results.py` 같은 스크립트들은 JSON 을 읽어 **집계하면서 동시에 표를 찍는다**.
그래서 화면(React 탭)을 붙이려면 같은 집계를 두 번 쓰게 되고, 두 벌은 반드시 갈라진다.

이 패키지는 **데이터만** 돌려준다. 서식은 스크립트가, 직렬화는 API 가 각자 한다.

⚠️ **스크립트를 없애지 않는다.** CI·야간 매트릭스가 터미널에서 돈다. 없어지면 매트릭스
운용이 불편해진다 — 화면이 생겼다고 터미널을 뺏을 이유가 없다.
"""

from updown.orchestration.reporting.store import (
    ReportNotFoundError,
    RunStore,
    RunSummary,
    aggregate_phases,
)

__all__ = ["ReportNotFoundError", "RunStore", "RunSummary", "aggregate_phases"]
