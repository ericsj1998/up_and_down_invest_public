"""모의 라이브 (T13) — 봉인 구간을 한 봉씩 걸어가며 손으로 매매한다.

```
sealed   봉인 커서 — 커서 이후를 읽으면 SealBreach
ledger   매매 기록 — 계획 RR · 실제 RR · 달성률
session  한 봉씩 굴리기 — 분석 · 청산 · 진입
```

🔴 **정확도 검증의 기본 수단이다.** 매트릭스가 못 잡은 결함이 눈으로 본 회차마다
하나씩 나왔다 (T13 문서의 표).
"""

from updown.orchestration.walkforward.ledger import (
    Actor,
    EvidenceRow,
    Funding,
    Ledger,
    Outcome,
    TradeRecord,
    evidence_rows,
)
from updown.orchestration.walkforward.sealed import Seal, SealBreachError, SealedFeed
from updown.orchestration.walkforward.session import Session, Snapshot, run_to_end

__all__ = [
    "Actor",
    "EvidenceRow",
    "Funding",
    "Ledger",
    "Outcome",
    "Seal",
    "SealBreachError",
    "SealedFeed",
    "Session",
    "Snapshot",
    "TradeRecord",
    "evidence_rows",
    "run_to_end",
]
