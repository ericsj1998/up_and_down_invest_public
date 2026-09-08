"""추세필터 코어 — 멀티자산 SMA 롱/현금 전략의 검증 백테스트 (T58).

T57 에서 이것이 우리가 본 모든 것(친구 4~6x 엔진·우리 박스·buy-hold)을 리스크조정으로
압도함을 확인했다. 여기는 그 코어를 **재현 가능한 in-repo 모듈**로 옮긴 것이다 (B-1).
"""

from updown.orchestration.trend_filter.core import (
    TrendResult,
    backtest,
    sleeve_returns,
)

__all__ = ["TrendResult", "backtest", "sleeve_returns"]
