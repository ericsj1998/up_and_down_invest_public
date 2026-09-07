# pyright: reportPrivateUsage=false
# 키 정규화 함수를 직접 시험한다
"""보안 리뷰 (2026-09-03) 수정 2건의 회귀 고정.

1. 재인증 게이트 — 거래소로 주문이 나가는 경로가 `TRADE_PATHS` 에 전부 있는가.
2. 세션 열쇠 — 경로 탈출 모양이 저널 경로에 못 들어가는가.
"""

import pytest
from fastapi import HTTPException

from updown.apps.api.auth import TRADE_PATHS
from updown.apps.api.walkforward import _safe_key


def test_money_moving_paths_all_require_reauth() -> None:
    """수동 매매·세션 삭제(라이브 청산)·잔량 청소·재개·입양이 빠지면 12시간
    쿠키만으로 재인증 없이 돈이 움직인다 — 접두어 목록의 구멍이 곧 우회로다."""
    for prefix in (
        "/walkforward/live",
        "/walkforward/buy",
        "/walkforward/sell",
        "/walkforward/sessions",
        "/walkforward/leftovers",
        "/walkforward/resume",
        "/walkforward/adopt",
        "/exchange/",
        "/rebalancer",
    ):
        assert prefix in TRADE_PATHS, f"{prefix} 가 재인증 게이트에서 빠졌다"


def test_safe_key_accepts_normal_handles() -> None:
    for good in ("live1a2b3c4d", "abc-DEF_09", "a" * 64):
        assert _safe_key(good) == good


def test_safe_key_rejects_traversal_shapes() -> None:
    """`../funds/x` 가 통과하면 저널 기록기가 펀드 상태 파일을 덮어쓴다."""
    for bad in ("../funds/x", "/tmp/x", "a/b", "a" * 65, "", "한글", "a.b"):
        with pytest.raises(HTTPException):
            _safe_key(bad)
