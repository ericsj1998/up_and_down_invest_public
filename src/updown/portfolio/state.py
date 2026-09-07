"""Unified Portfolio 의 사실 스냅샷 (spec §4.18, §9 `portfolio_snapshots`).

**사실만 담는다.** 목표 비중 대비 이탈률·리밸런싱 제안은 §4.7 Allocation 의 **판단**이며
`decision/` 소속이다 (plan P-2). 여기에 `target_weight` 가 생기면 경계가 무너진 것이다.

이 타입이 `portfolio` 계층에 있고 `analysis` 보다 **아래**인 이유: §4.6 이 RiskManager 의
입력으로 `PortfolioState` 를 요구하므로 `decision` 보다 낮아야 하고, 사실 집계는 아무
판단에도 의존하지 않으므로 낮게 두는 것이 자연스럽다 (plan D-6).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from updown.common.domain.instrument import Bucket
from updown.common.domain.market import Balance


@dataclass(frozen=True, slots=True)
class BucketExposure:
    """버킷별 현재 노출 (spec §4.7 입력 "현재 잔고/포지션", §9 `by_bucket_json`).

    Attributes:
        bucket: 전략 버킷.
        value_krw: KRW 환산 평가금액.
        position_count: 열린 포지션 수.

    Note:
        **목표 비중이 여기 없는 것은 의도다.** 목표는 사용자 설정이고 이탈 판정은
        §4.7 Allocation 의 판단이다. 사실 객체가 목표를 알면 "현재가 목표에서 벗어났다"는
        계산을 이 계층에서 하고 싶어지고, 그 순간 `portfolio ↔ decision` 순환이 시작된다.
    """

    bucket: Bucket
    value_krw: Decimal
    position_count: int


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """RiskManager 가 판단 근거로 쓰는 계좌 사실 스냅샷 (spec §4.6 입력, §4.18).

    Attributes:
        user_id: 소유 사용자.
        as_of: 스냅샷 기준 시각 (UTC).
        total_equity_krw: 총 평가금액 (KRW 정규화). **포지션 사이징의 분모**다 —
            `qty = (total_equity_krw * risk_pct) / (entry - stop_loss)` (spec §4.6).
        cash_krw: 예수금 합계 (KRW 환산).
        realized_pnl_krw: 누적 실현 손익.
        unrealized_pnl_krw: 누적 미실현 손익.
        fx_pnl_krw: 환율 변동 손익. **투자 손익과 분리**해 담는다 — 미분리 시 해외주식
            성과가 왜곡된다 (spec §4.18).
        daily_realized_pnl_krw: 당일 실현 손익. 일일 손실 한도 도달 판정(킬 스위치,
            spec §4.6)의 입력이다.
        open_position_count: 열린 포지션 총수. 최대 동시 포지션 가드레일의 입력이다.
        by_bucket: 버킷별 노출. 버킷 배분 한도 판정에 쓴다.
        by_account: 계좌별 잔고. 페이퍼 계좌도 `broker='paper'` 로 같은 스키마에 들어온다
            (spec §4.19).
        is_stale: 브로커 조회 실패로 **마지막 스냅샷을 재사용**하고 있는지 (spec §4.18).

    Note:
        `is_stale` 이 필수인 이유는 spec §4.18 의 "조용히 오래된 값을 최신처럼 보여주지
        않는다" 요구다. 이 플래그가 없으면 조회 실패 시 낡은 총자산으로 포지션 크기를
        계산하게 되고, 그것은 조용한 실패다 (spec §7).

        시간가중수익률(TWR, spec §4.18)은 여기 없다. 입출금 이벤트로 구간을 분할해
        계산하는 **대시보드용 파생 지표**이고 RiskManager 의 판단에는 쓰이지 않는다.
        `allocation_ledger` 를 재료로 orchestration 에서 산출한다.
    """

    user_id: str
    as_of: datetime
    total_equity_krw: Decimal
    cash_krw: Decimal
    realized_pnl_krw: Decimal
    unrealized_pnl_krw: Decimal
    fx_pnl_krw: Decimal
    daily_realized_pnl_krw: Decimal
    open_position_count: int
    by_bucket: Mapping[Bucket, BucketExposure]
    by_account: tuple[Balance, ...]
    is_stale: bool
