"""쓸 수 있는 레벨과 그 위의 계획 — **다 찾아 주는 것은 정보가 아니다**.

사용자 요구 2026-08-30: *"막 모든 저항을 찾아주고, 그런 게 옳지 않다는 거야. 실제
트레이딩에서 유효하게 쓸 수 있는 정도의 정보를 제공해야 해."*

실측(BTC 4h · 사용자 캡처): 띠가 **32개** 나왔고 상당수가 거의 같은 자리였다.

## 이 시험이 지키는 것

⛔ **눈으로 고르지 않는다** (절대 규칙 #11). 아래 규칙은 전부 셀 수 있는 것이고,
그래서 성과로 검증할 수 있다. 시험은 그 규칙이 **실제로 걸러 내는지**를 본다.

🔴 가장 중요한 것은 **비용 산수**다. RR 3.0 은 좋아 보이지만 비용을 넣은 필요 승률이
답이고, 그 답 없이 낸 계획은 희망이다 — 이 프로젝트가 5m 을 버린 근거가 그 산수다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.levels import MIN_TOUCHES, Level, useful
from updown.analysis.plan import propose, required_win_rate
from updown.analysis.structures.box import Box
from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.structure import PriceRange

INST = Instrument(
    market=Market.BINANCE,
    symbol="BTC_USDT",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
START = datetime(2026, 1, 1, tzinfo=UTC)
COST = Decimal("0.00157")
"""왕복 비용 실측값 (UPBIT 0.157%) — 이 프로젝트가 쓰는 그 수."""


def _candles(closes: list[float], *, span: float = 100.0) -> list[Candle]:
    """종가만 정하고 나머지는 채운다 — 레벨 판정은 종가로 한다."""
    out: list[Candle] = []
    for i, close in enumerate(closes):
        price = Decimal(str(close))
        out.append(
            Candle(
                instrument=INST,
                timeframe=Timeframe.H4,
                ts=START + timedelta(hours=4 * i),
                open=price,
                high=price + Decimal(str(span)) / 4,
                low=price - Decimal(str(span)) / 4,
                close=price,
                volume=Decimal(1),
            )
        )
    return out


def _box(low: float, high: float, *, support: bool, touches: int, at: int) -> Box:
    """레벨 하나 — 접점 수와 마지막 접점 시각만 뜻이 있다."""
    kind = SwingKind.LOW if support else SwingKind.HIGH
    ts = START + timedelta(hours=4 * at)
    point = SwingPoint(index=at, ts=ts, price=Decimal(str((low + high) / 2)), kind=kind)
    return Box(
        kind=kind,
        price_range=PriceRange(low=Decimal(str(low)), high=Decimal(str(high))),
        level=Decimal(str((low + high) / 2)),
        touches=tuple([point] * touches),
        first_ts=START,
        last_ts=ts,
    )


class TestMerging:
    """① ATR 안에 있는 것은 **한 자리다** — 실측에서 셋이 따로 나왔다."""

    def test_three_nearby_levels_become_one(self) -> None:
        rows = _candles([70000.0] * 50)
        boxes = [
            _box(64216, 64358, support=True, touches=5, at=45),
            _box(64400, 64530, support=True, touches=5, at=46),
            _box(64460, 64600, support=True, touches=4, at=47),
        ]
        got = useful(boxes, rows, span=Decimal(500), round_trip=COST)
        assert len(got) == 1, "사람 눈에 한 자리인 것은 한 줄이어야 한다"
        # ⭐ 접점을 **더한다** — 쪼개져 있으면 약해 보인다.
        assert got[0].touches == 14
        assert got[0].merged == 3

    def test_support_and_resistance_never_merge(self) -> None:
        """⚠️ 같은 가격이라도 **역할이 다르면 다른 자리다** — 섞어 뭉치지 않는다.

        🔴 이 성질은 `useful()` 로 직접 못 잰다. 같은 가격의 지지와 저항 중 하나는
        **언제나 이미 관통된 것**이기 때문이다(④): 지금 가격보다 위인 지지는 뚫린
        지지이고, 아래인 저항은 뚫린 저항이다. 둘 다 살아남는 배치가 없다.

        ⇒ 대신 **섞였으면 나올 수 없는 값**으로 잰다. 지지 둘과 저항 하나를 같은
          자리에 두면, 저항은 관통으로 사라지고 지지 둘만 뭉쳐 `merged == 2` 다.
          셋이 섞여 뭉쳤다면 3 이 나온다.
        """
        rows = _candles([70000.0] * 50)
        boxes = [
            _box(64000, 64100, support=True, touches=4, at=45),
            _box(64050, 64150, support=True, touches=4, at=46),
            _box(64010, 64110, support=False, touches=4, at=47),
        ]
        got = useful(boxes, rows, span=Decimal(500), round_trip=COST)
        assert len(got) == 1
        assert got[0].support is True
        assert got[0].merged == 2, "저항이 지지에 섞여 들어갔다"
        assert got[0].touches == 8


class TestFiltering:
    def test_two_touches_is_not_a_level(self) -> None:
        """② 아무 두 점이나 이으면 선이 된다 — 3회부터가 시장의 인정이다."""
        rows = _candles([70000.0] * 50)
        thin = [_box(64000, 64100, support=True, touches=MIN_TOUCHES - 1, at=45)]
        assert useful(thin, rows, span=Decimal(500), round_trip=COST) == []

    def test_a_forgotten_level_is_dropped(self) -> None:
        """③ 마지막 접점이 창 훨씬 앞이면 지금 매매에 쓸 정보가 아니다."""
        rows = _candles([70000.0] * 400)
        old = [_box(64000, 64100, support=True, touches=9, at=5)]
        assert useful(old, rows, span=Decimal(500), round_trip=COST) == []

    def test_a_broken_support_is_dropped(self) -> None:
        """④ 뒤이어 종가가 관통했으면 **지지가 아니라 지나간 곳**이다."""
        # 지지 64,000 을 만든 뒤 62,000 으로 마감해 버린다.
        rows = _candles([64050.0] * 30 + [62000.0] * 20)
        boxes = [_box(64000, 64100, support=True, touches=5, at=25)]
        assert useful(boxes, rows, span=Decimal(500), round_trip=COST) == []

    def test_a_wick_through_does_not_break_it(self) -> None:
        """🔴 꼬리로 찌른 것은 오히려 그 레벨이 **살아 있다**는 증거다.

        그것을 관통으로 세면 진짜 지지를 전부 지운다.
        """
        # ⚠️ 지지를 **지금 가격에서 충분히 떨어뜨린다** — 붙여 두면 비용 문턱(⑤)에
        #    걸려 사라지고, 그러면 이 시험이 관통이 아니라 거리를 재게 된다.
        #    저가는 지지를 스치지만(span 이 크다) 종가는 한 번도 아래로 안 마감한다.
        rows = _candles([66000.0] * 30 + [65000.0] * 20, span=4000.0)
        boxes = [_box(64000, 64100, support=True, touches=5, at=25)]
        assert len(useful(boxes, rows, span=Decimal(200), round_trip=COST)) == 1

    def test_a_level_too_close_to_pay_costs_is_dropped(self) -> None:
        """🔴 **이 파일의 요점.** 0.05% 떨어진 저항은 그릴 수는 있어도 거래가 안 된다.

        왕복 비용도 못 갚는 자리를 보여 주면 사람이 그것으로 계획을 세우고, 그 계획은
        산술적으로 진다 — 5m 을 버린 것과 같은 산수다.
        """
        rows = _candles([70000.0] * 50)
        # 지금 가격 70,000 에서 0.03% 떨어진 자리.
        near = [_box(69975, 69985, support=True, touches=9, at=45)]
        assert useful(near, rows, span=Decimal(500), round_trip=COST) == []

    def test_the_same_level_far_enough_survives(self) -> None:
        """⚠️ 비용 문턱은 **거리**만 본다 — 멀면 같은 자리도 남는다."""
        rows = _candles([70000.0] * 50)
        far = [_box(68000, 68100, support=True, touches=9, at=45)]
        assert len(useful(far, rows, span=Decimal(500), round_trip=COST)) == 1

    def test_nothing_is_a_valid_answer(self) -> None:
        """⚠️ 억지로 채우면 '쓸 수 있는 자리' 라는 말이 뜻을 잃는다."""
        assert useful([], _candles([70000.0] * 50), span=Decimal(500), round_trip=COST) == []


class TestTheCostArithmetic:
    """🔴 `P > (1 + c) / (1 + RR)` — 이 프로젝트의 중심 산수.

    ## ⚠️ `c` 는 **R 단위**다 (2026-08-30 에 고쳤다)

    처음 이 함수를 쓸 때 **가격 대비 비용**을 그대로 넣었다. 두 값이 비슷해 보여서
    시험도 통과했는데(비용 0.157% 는 1 에 비해 거의 0 이라 어떤 단위로 넣든 답이
    비슷하다), **손절이 좁아지면 갈린다** — 그리고 정확히 위험한 쪽으로 갈린다.

    이 클래스의 마지막 시험이 그 증거다: 5m 을 폐기한 실측 조건을 넣으면 R 단위로는
    **100.6~100.8%** 가 나와 문서와 맞고, 가격 대비로는 **37.9%** 가 나와 5m 이
    통과해 버린다. 단위를 틀리면 이 프로젝트가 이미 버린 것을 다시 사게 된다.
    """

    STOP = Decimal("0.02")
    """손절 2% — 이 클래스의 기준 손절폭. R 단위 환산의 분모다."""

    def test_a_one_to_one_trade_needs_more_than_half(self) -> None:
        # 비용이 없어도 50%, 있으면 그보다 위다 — 동전 던지기로는 못 이긴다.
        assert required_win_rate(Decimal(1), COST, self.STOP) > 50

    def test_better_reward_needs_less_win_rate(self) -> None:
        assert required_win_rate(Decimal(3), COST, self.STOP) < required_win_rate(
            Decimal(1), COST, self.STOP
        )

    def test_a_tighter_stop_needs_a_higher_win_rate(self) -> None:
        """🔴 **같은 RR 이라도 손절이 좁으면 더 많이 맞아야 한다.**

        비용이 R 을 통째로 먹기 때문이다 — 가격 대비로 재면 이 차이가 **아예 안 보이고**,
        안 보이는 채로 좁은 손절 계획이 "RR 3" 이라는 이유로 통과한다 (T173 에서 BTC 1h
        계획의 83% 가 그랬다).
        """
        wide = required_win_rate(Decimal(2), COST, Decimal("0.02"))
        tight = required_win_rate(Decimal(2), COST, Decimal("0.002"))
        assert tight > wide + 20, f"좁은 손절 {tight:.1f}% vs 넓은 손절 {wide:.1f}%"

    def test_it_reproduces_the_5m_verdict(self) -> None:
        """🔴 **5m 을 폐기한 계산을 그대로 재현한다** (§P1-8-0b Q1 · CLAUDE.md).

        실측 조건: 익절 거리 **2.47xATR** · 가정 비용 **0.30%** · BTC 5m ATR 약 0.12%.
        문서가 남긴 답은 *"세 k 전부 승률 100% 초과 (100.7~100.9%)"* 다.

        ⛔ 이 시험이 깨지면 **단위가 다시 틀어진 것**이다. 가격 대비로 넣으면 같은
        조건이 37.9% 로 나와 5m 이 통과한다 — 산수로 버린 것을 산수 실수로 되사게 된다.
        """
        atr_pct = Decimal("0.0012")
        for k in (Decimal("1.5"), Decimal("2.0"), Decimal("2.5")):
            got = required_win_rate(Decimal("2.47") / k, Decimal("0.003"), k * atr_pct)
            assert got > 100, f"k={k} 에서 {got:.1f}% — 100% 를 넘어야 폐기 근거가 선다"
            assert got < 101, f"k={k} 에서 {got:.1f}% — 문서 실측은 100.7~100.9% 였다"

    def test_a_stop_of_zero_is_an_input_error(self) -> None:
        """손절이 없으면 R 이 정의되지 않는다 — 0 으로 떨어뜨리지 않는다 (규칙 #8)."""
        with pytest.raises(ValueError, match="손절폭"):
            required_win_rate(Decimal(2), COST, Decimal(0))


class TestProposals:
    def _levels(self, *rows: tuple[float, float, bool, int]) -> list[Level]:
        return [
            Level(
                low=Decimal(str(low)),
                high=Decimal(str(high)),
                support=support,
                touches=touches,
                last_ts=START,
                away_pct=Decimal(1),
                merged=1,
            )
            for low, high, support, touches in rows
        ]

    def test_it_buys_above_support_and_targets_resistance(self) -> None:
        got = propose(
            self._levels((69000, 69300, True, 5), (72000, 72200, False, 5)),
            Decimal(70000),
            span=Decimal(500),
            round_trip=COST,
        )
        assert got is not None
        # ⭐ 지지 **위**에서 산다 — 띠 한가운데가 아니다.
        assert got.entry > Decimal(69300)
        # ⭐ 손절은 지지 **아래** — 붙여 놓으면 꼬리 한 번에 죽는다.
        assert got.stop < Decimal(69000)
        assert got.first < Decimal(72000)
        assert got.long is True

    def test_it_refuses_when_the_reward_is_too_thin(self) -> None:
        """⛔ 손익비가 낮으면 필요 승률이 실측 승률(32%)을 훌쩍 넘는다 — 안 낸다."""
        got = propose(
            self._levels((69800, 69900, True, 5), (70100, 70200, False, 5)),
            Decimal(70000),
            span=Decimal(50),
            round_trip=COST,
        )
        assert got is None

    def test_it_refuses_with_only_one_side(self) -> None:
        """⚠️ 목표 없는 진입도, 손절 없는 진입도 계획이 아니다."""
        one = self._levels((68000, 68200, True, 5))
        assert propose(one, Decimal(70000), span=Decimal(500), round_trip=COST) is None

    def test_it_carries_the_required_win_rate(self) -> None:
        """🔴 화면이 이 값을 **반드시** 같이 보여야 한다 — RR 만 보면 속는다."""
        got = propose(
            self._levels((69000, 69300, True, 5), (72000, 72200, False, 5)),
            Decimal(70000),
            span=Decimal(500),
            round_trip=COST,
        )
        assert got is not None
        assert 0 < got.need_pct < 100
        assert got.rr >= Decimal("1.5")

    def test_it_names_the_levels_it_used(self) -> None:
        """근거 없는 계획은 복기할 수 없다 (§1-0s)."""
        got = propose(
            self._levels((69000, 69300, True, 7), (72000, 72200, False, 4)),
            Decimal(70000),
            span=Decimal(500),
            round_trip=COST,
        )
        assert got is not None
        assert "7회" in got.why
        assert "4회" in got.why

    def test_it_refuses_an_entry_too_far_to_reach(self) -> None:
        """🔴 **실측이 잡은 결함** (2026-08-30). 진입이 현재가보다 22% 아래인 계획이
        나왔다 — RR 10.28 짜리였지만 그것은 매매가 아니다.

        22% 폭락해야 채워지는 지정가는 지금 낼 주문이 아니고, 그 사이 구조가 통째로
        바뀐다. 사용자가 말한 *"실제 트레이딩에서 유효하게 쓸 수 있는 정도"* 가 이것이다.
        """
        far = self._levels((55000, 55200, True, 9), (72000, 72200, False, 5))
        assert propose(far, Decimal(70000), span=Decimal(500), round_trip=COST) is None

    def test_it_refuses_a_stop_thinner_than_the_floor(self) -> None:
        """🔴 T173: BTC 1h 계획의 **83%** 가 손절폭 0.5% 미만이었고 RR 은 3.05 였다.

        지지 바로 밑에 손절을 붙이면 RR 은 얼마든지 커지는데 그 계획은 노이즈에 죽는다.
        """
        # ATR 을 아주 작게 두면 손절이 지지에 딱 붙는다.
        tight = self._levels((69900, 69950, True, 5), (72000, 72200, False, 5))
        assert propose(tight, Decimal(70000), span=Decimal(1), round_trip=COST) is None

    @pytest.mark.parametrize("price", [Decimal(0), Decimal(-1)])
    def test_a_broken_price_makes_no_plan(self, price: Decimal) -> None:
        assert propose(self._levels(), price, span=Decimal(500), round_trip=COST) is None
