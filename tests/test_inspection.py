"""점검기 코어 (`orchestration/inspection`).

여기서 지키려는 성질은 둘이다.

    **워밍업이 모자란 시점을 안 뽑는다** — 상위 TF 가 조용히 비면 데이터 부족을
    시장의 성질로 오해한다. (미래 절단은 `analysis/context/guard.py` 가 맡는다)
    **같은 시드는 같은 화면** — 재현되지 않으면 이의제기를 나중에 검토할 수 없다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.oos import OOS_BOUNDARY
from updown.orchestration.inspection import (
    MomentUnavailableError,
    pick_moment,
    warmup_for,
)

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
_START = datetime(2024, 1, 1, tzinfo=UTC)


def bar(ts: datetime, timeframe: Timeframe = Timeframe.H1) -> Candle:
    """가격은 무의미한 봉 하나 — 여기서 재는 것은 `ts` 뿐이다."""
    return Candle(
        instrument=BTC,
        timeframe=timeframe,
        ts=ts,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
    )


class TestWarmupFor:
    def test_the_coarsest_timeframe_decides(self) -> None:
        """1d 300봉을 보려면 300일이 필요하다 — 15m 기준으로 잡으면 상위가 빈다."""
        assert warmup_for([Timeframe.M15, Timeframe.H1, Timeframe.D1], 300) == timedelta(days=300)

    def test_empty_timeframes_is_refused(self) -> None:
        with pytest.raises(ValueError, match="비었다"):
            warmup_for([], 300)

    def test_zero_bars_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1 미만"):
            warmup_for([Timeframe.H1], 0)


class TestPickMoment:
    def _pick(self, seed: int, *, latest: datetime | None = None):
        """워밍업 30일 · 1시간 격자로 한 건 뽑는다."""
        return pick_moment(
            seed=seed,
            earliest=_START,
            latest=latest or datetime(2025, 1, 1, tzinfo=UTC),
            warmup=timedelta(days=30),
            step=timedelta(hours=1),
        )

    def test_same_seed_gives_the_same_moment(self) -> None:
        """절대 규칙 #5. 재현되지 않으면 이의제기를 나중에 검토할 수 없다."""
        assert self._pick(7).at == self._pick(7).at

    def test_different_seeds_spread_out(self) -> None:
        """시드가 다르면 시점도 흩어진다 — 한 점만 나오면 랜덤이 아니다."""
        moments = {self._pick(seed).at for seed in range(20)}
        assert len(moments) > 15

    def test_warmup_is_respected(self) -> None:
        """워밍업 이전을 뽑으면 상위 시간축이 조용히 빈다 (절대 규칙 #8)."""
        for seed in range(30):
            assert self._pick(seed).at >= _START + timedelta(days=30)

    def test_never_crosses_the_seal(self) -> None:
        """⛔ 봉인 구간은 눈으로도 보지 않는다."""
        for seed in range(30):
            moment = pick_moment(
                seed=seed,
                earliest=_START,
                latest=datetime(2027, 1, 1, tzinfo=UTC),  # 봉인 너머를 넘겨 본다
                warmup=timedelta(days=30),
                step=timedelta(hours=1),
            )
            assert moment.at < OOS_BOUNDARY

    def test_the_seal_does_not_depend_on_the_caller(self) -> None:
        """호출부가 상한을 잘못 넘겨도 봉인은 지켜진다 — 성실성에 맡기지 않는다."""
        assert self._pick(1, latest=datetime(2030, 1, 1, tzinfo=UTC)).latest == OOS_BOUNDARY

    def test_no_room_raises_instead_of_guessing(self) -> None:
        """조용히 아무 시점이나 돌려주면 '지표가 안 잡힌다'로 오해하게 된다."""
        with pytest.raises(MomentUnavailableError, match="뽑을 시점이 없다"):
            pick_moment(
                seed=1,
                earliest=_START,
                latest=_START + timedelta(days=10),
                warmup=timedelta(days=30),
                step=timedelta(hours=1),
            )

    def test_moment_sits_on_the_grid(self) -> None:
        """격자에 앉아야 봉 경계와 맞는다."""
        moment = self._pick(3)
        assert moment.at.minute == 0
        assert moment.at.second == 0

    def test_grid_is_anchored_to_epoch_not_the_range(self) -> None:
        """기준이 구간 시작이면, 같은 시드가 적재 범위에 따라 다른 시점을 준다."""
        wide = pick_moment(
            seed=5,
            earliest=_START - timedelta(minutes=37),
            latest=datetime(2025, 1, 1, tzinfo=UTC),
            warmup=timedelta(days=30),
            step=timedelta(hours=1),
        )
        assert wide.at.minute == 0

    def test_naive_bounds_are_refused(self) -> None:
        with pytest.raises(ValueError, match="naive"):
            pick_moment(
                seed=1,
                earliest=datetime(2024, 1, 1),
                latest=datetime(2025, 1, 1, tzinfo=UTC),
                warmup=timedelta(days=1),
                step=timedelta(hours=1),
            )
