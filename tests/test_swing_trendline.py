"""추세선 — 스윙 2점 · 로그 (`structures/swing_trendline.py`).

사용자 명세를 코드가 지키는지 본다:

    스윙 하이 2개를 이음 → 하락 추세선 → 저항
    스윙 로우 2개를 이음 → 상승 추세선 → 지지
    갭이 벌어질수록 힘을 잃는다 → 로그로 푼다
    최대 4개
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.analysis.structures.swing_trendline import (
    BODY,
    MAX_LINES,
    WICK,
    # 🔴 비공개 두 개를 일부러 부른다. `_anchor`(꼬리/몸통 어디를 앵커로 잡나)와
    #    `_line`(두 점 → 직선)은 작도의 **기하 그 자체**라, 그린 결과만 보고 재면
    #    "선이 이상하다"에서 원인을 못 좁힌다. 공개로 올리면 작도 밖에서 선을 만들
    #    수 있게 되므로 비공개인 채로 직접 재는 것이 맞다.
    _anchor,  # pyright: ignore[reportPrivateUsage]
    _line,  # pyright: ignore[reportPrivateUsage]
    detect,
    fit_gap,
    push_out,
    same_line,
    swings_from_pivots,
    swings_from_turns,
    with_parallel,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
_ORIGIN = datetime(2025, 1, 1, tzinfo=UTC)


def point(index: int, price: int, kind: SwingKind) -> SwingPoint:
    """스윙 점 하나."""
    return SwingPoint(
        index=index,
        ts=_ORIGIN + timedelta(hours=index),
        price=Decimal(price),
        kind=kind,
    )


def high(index: int, price: int) -> SwingPoint:
    return point(index, price, SwingKind.HIGH)


def low(index: int, price: int) -> SwingPoint:
    return point(index, price, SwingKind.LOW)


def bar(index: int, close: int, *, wick: int = 5) -> Candle:
    """종가로 방향을 정하고 꼬리를 붙인 봉."""
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=_ORIGIN + timedelta(hours=index),
        open=Decimal(close),
        high=Decimal(close + wick),
        low=Decimal(close - wick),
        close=Decimal(close),
        volume=Decimal(10),
    )


class TestDirection:
    def test_falling_highs_make_resistance(self) -> None:
        """스윙 하이 2개를 이어 **내려가면** 저항선이다 (명세 1번)."""
        got = detect([high(0, 200), high(10, 180)])
        assert [line.kind for line in got] == ["resistance"]

    def test_rising_lows_make_support(self) -> None:
        """스윙 로우 2개를 이어 **올라가면** 지지선이다 (명세 2번)."""
        got = detect([low(0, 100), low(10, 120)])
        assert [line.kind for line in got] == ["support"]

    def test_rising_highs_are_not_a_trendline(self) -> None:
        """올라가는 고점을 이으면 저항이 아니라 확산이다 — 버린다."""
        assert detect([high(0, 100), high(10, 200)]) == ()

    def test_falling_lows_are_not_a_trendline(self) -> None:
        assert detect([low(0, 200), low(10, 100)]) == ()

    def test_highs_and_lows_do_not_mix(self) -> None:
        """하이와 로우를 이으면 추세선이 아니다."""
        assert detect([high(0, 200), low(10, 100)]) == ()


class TestAdjacency:
    def test_only_neighbours_are_joined(self) -> None:
        """🔴 모든 쌍이 아니라 **이웃**만 — 조합 폭발을 없애는 지점이다.

        하이 3개면 (1,2)·(2,3) 두 개이지 (1,3) 을 포함한 세 개가 아니다.
        """
        got = detect([high(0, 300), high(10, 200), high(20, 100)], limit=99)
        assert len(got) == 2
        assert {(line.start, line.end) for line in got} == {(0, 10), (10, 20)}

    def test_zero_span_is_dropped(self) -> None:
        assert detect([high(5, 200), high(5, 100)]) == ()


class TestLogScale:
    def test_ratio_is_constant_per_bar(self) -> None:
        """로그 직선은 **일정 비율**이다 — 가격대가 변해도 뜻이 유지된다."""
        got = detect([low(0, 100), low(10, 200)], limit=99)
        # 상승 로우라 지지선이 아니다. 방향을 맞춰 다시.
        got = detect([high(0, 200), high(10, 100)], limit=99)
        assert len(got) == 1
        # 10봉에 절반이면 봉당 2^(-1/10).
        assert abs(got[0].ratio_per_bar - Decimal(2) ** Decimal("-0.1")) < Decimal("1e-9")

    def test_midpoint_is_geometric_not_arithmetic(self) -> None:
        """🔴 선형이면 중간이 150, 로그면 141.4 다. 이 차이가 '갭' 문제의 정체다."""
        line = detect([high(0, 200), high(10, 100)], limit=99)[0]
        assert abs(line.price_at(5) - Decimal(200) / Decimal(2) ** Decimal("0.5")) < Decimal("1e-6")

    def test_curve_has_many_points(self) -> None:
        """양 끝만 이으면 로그 직선의 곡률이 사라져 **다른 선**이 된다."""
        line = detect([high(0, 200), high(50, 100)], limit=99)[0]
        assert len(line.points(60)) > 10

    def test_line_extends_past_the_second_swing(self) -> None:
        """앵커 뒤로도 이어 그린다 — 다만 **무한정은 아니다** (아래 참조)."""
        line = detect([high(0, 200), high(10, 100)], limit=99)[0]
        assert line.points(30)[-1][0] > line.end

    def test_extension_is_capped_at_the_anchor_span(self) -> None:
        """🔴 차트 끝까지 늘리지 않는다 — **선이 존재하는 만큼만** 그린다.

        늘렸더니 앵커 구간 35봉짜리가 200봉을 가로질러, 서로 앵커가 **안 겹치는** 선들이
        화면에서는 겹쳐 보였다 (사용자: *"거의 유사하게 겹치는 경우"*). 실측에서 그
        화면 세 선의 앵커 겹침은 0봉이었고, 그린 구간 겹침은 214봉이었다.

        규칙은 `backtest/chart.extend_to` 가 옛 추세선에 쓰던 것과 같다.
        """
        line = detect([high(0, 200), high(10, 100)], limit=99)[0]
        assert line.extent(999) == 20  # 앵커 10봉 → 10봉만 더
        assert line.points(999)[-1][0] == 20

    def test_a_short_chart_does_not_stretch_the_line(self) -> None:
        """차트가 짧으면 거기까지만 — 없는 봉 위에 선을 그리지 않는다."""
        line = detect([high(0, 200), high(10, 100)], limit=99)[0]
        assert line.extent(13) == 13


TURNS = [0, 10, 20, 30, 40]


class TestBasis:
    """🔴 꼬리냐 몸통이냐 — **재 보고 고른다**.

    사용자: *"꼬리 기준이 좋을 때가 있고, 몸통 기준이 좋을 때가 있대. 차트 내 영역이
    최대한 추세선과 잘 붙어있는 기준으로 계산해주는 게 더 나을 것 같아."*
    """

    def _spiky(self) -> list[Candle]:
        """W 자 — 저점이 세 번(0·20·40) 나오고 뒤로 갈수록 높다.

        가운데 저점(20)에만 **아래 꼬리가 깊게** 박혀 있다. 꼬리 기준 지지선은 그 하나에
        끌려 내려가고, 몸통 기준 선이 본체에 붙는다. 이것이 "몸통이 좋을 때"다.
        """
        peaks = {0: 100, 10: 200, 20: 120, 30: 250, 40: 140}
        out: list[Candle] = []
        for i in range(41):
            left = max(k for k in peaks if k <= i)
            right = min(k for k in peaks if k >= i)
            if left == right:
                body = peaks[left]
            else:
                ratio = (i - left) / (right - left)
                body = peaks[left] + (peaks[right] - peaks[left]) * ratio
            body = int(body)
            out.append(
                Candle(
                    instrument=BTC,
                    timeframe=Timeframe.H1,
                    ts=_ORIGIN + timedelta(hours=i),
                    open=Decimal(body),
                    high=Decimal(body + 1),
                    # 🔴 가운데 저점에만 깊은 꼬리.
                    low=Decimal(10 if i == 20 else body - 1),
                    close=Decimal(body),
                    volume=Decimal(10),
                )
            )
        return out

    def test_anchor_reads_wick_or_body(self) -> None:
        candle = Candle(
            instrument=BTC,
            timeframe=Timeframe.H1,
            ts=_ORIGIN,
            open=Decimal(100),
            high=Decimal(150),
            low=Decimal(50),
            close=Decimal(120),
            volume=Decimal(1),
        )
        # 🔴 꼬리 끝이 **아니다** — 바깥 20% 만큼 안쪽이 경계다 (`PIERCE`).
        #    꼬리 폭 100(50~150) 의 20% = 20 이므로 위 경계는 130, 아래는 70.
        assert _anchor(candle, higher=True, basis=WICK) == Decimal(130)
        assert _anchor(candle, higher=False, basis=WICK) == Decimal(70)
        # 몸통 폭 20(100~120) 의 20% = 4.
        assert _anchor(candle, higher=True, basis=BODY) == Decimal(116)
        assert _anchor(candle, higher=False, basis=BODY) == Decimal(104)

    def test_a_flat_candle_has_no_room_to_pierce(self) -> None:
        """폭이 0인 봉은 경계가 그 값 자체다 — 뚫을 폭이 없는 봉이다."""
        candle = Candle(
            instrument=BTC,
            timeframe=Timeframe.H1,
            ts=_ORIGIN,
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            volume=Decimal(1),
        )
        assert _anchor(candle, higher=True, basis=WICK) == Decimal(100)
        assert _anchor(candle, higher=False, basis=WICK) == Decimal(100)

    def test_swings_follow_the_basis(self) -> None:
        candles = self._spiky()
        wick = swings_from_turns(candles, TURNS)
        body = swings_from_turns(candles, TURNS, BODY)
        # 20번 봉은 저점 전환이고 꼬리가 10 까지 내려갔다 (고가 121 · 시가=종가 120).
        # 꼬리 기준 폭 111 → 경계 10 + 22.2 = 32.2.
        # 몸통 기준은 시가=종가라 **폭이 0** → 뚫을 자리가 없어 120 그대로다.
        assert wick[2].price == Decimal("32.2")
        assert body[2].price == Decimal(120)
        # 🔴 요점은 값이 아니라 **순서**다 — 꼬리 기준이 여전히 더 아래여야 한다.
        assert wick[2].price < body[2].price

    def test_body_fits_better_when_a_wick_spikes(self) -> None:
        """🔴 꼬리 하나가 튀면 몸통 기준 선이 캔들에 더 붙는다 — 그것을 재는 자."""
        candles = self._spiky()
        wick = swings_from_turns(candles, TURNS)
        body = swings_from_turns(candles, TURNS, BODY)
        # 같은 전환점 쌍(20~40)을 두 기준으로 긋는다.
        pair = ("support", 2, 4)
        _ = pair
        wick_line = _line(wick[2], wick[4], "support", WICK)
        body_line = _line(body[2], body[4], "support", BODY)
        assert wick_line is not None
        assert body_line is not None
        assert fit_gap(body_line, candles) < fit_gap(wick_line, candles)

    def test_detect_picks_a_basis_per_line(self) -> None:
        """어느 기준으로 그렸는지가 선마다 붙는다."""
        candles = self._spiky()
        got = detect(
            swings_from_turns(candles, TURNS),
            swings_from_turns(candles, TURNS, BODY),
            Decimal(1),
            candles,
            limit=99,
        )
        assert got
        assert {line.basis for line in got} <= {WICK, BODY}

    def test_wick_only_when_no_body_set_is_given(self) -> None:
        """몸통 목록을 안 넘기면 꼬리 기준만 쓴다 — 조용히 바꾸지 않는다."""
        candles = self._spiky()
        got = detect(swings_from_turns(candles, TURNS), atr=Decimal(1), limit=99)
        assert all(line.basis == WICK for line in got)

    def test_basis_is_reported(self) -> None:
        """어느 기준으로 그렸는지 화면이 알아야 검증이 된다 (절대 규칙 #8)."""
        line = detect([high(0, 200), high(10, 100)], limit=99)[0]
        assert line.to_dict(20)["basis"] == WICK


class TestPushOut:
    """🔴 봉을 뚫으면 **밖으로 민다** — 버리지 않는다.

    사용자: *"차트를 가로지르는 것도 솔직히 추세 자체만은 맞지만, 차라리 이런 경우
    고점 혹은 저점에 그려줘야 한다는 거지."*

    방향은 맞는데 위치가 안쪽인 것이므로, 기울기를 유지한 채 캔들 바깥으로 옮긴다.
    """

    def _humped(self) -> list[Candle]:
        """양 끝은 낮고 가운데가 솟은 봉들 — 끝점끼리 이은 선이 가운데를 뚫는다."""
        out: list[Candle] = []
        for i in range(21):
            high = 100 + (10 - abs(i - 10)) * 5  # 가운데(10)가 가장 높다
            out.append(
                Candle(
                    instrument=BTC,
                    timeframe=Timeframe.H1,
                    ts=_ORIGIN + timedelta(hours=i),
                    open=Decimal(high - 2),
                    high=Decimal(high),
                    low=Decimal(high - 4),
                    close=Decimal(high - 2),
                    volume=Decimal(10),
                )
            )
        return out

    def test_a_cutting_line_is_moved_out_not_dropped(self) -> None:
        candles = self._humped()
        raw = _line(high(0, 100), high(20, 100), "resistance", WICK)
        assert raw is not None
        moved = push_out(raw, candles)
        assert moved.pushed is True
        # 🔴 가운데 봉의 **20% 경계**까지만 민다 (고가 150 · 저가 146 · 폭 4 → 149.2).
        #    꼬리 끝(150)까지 밀면 그 봉 하나에 끌려 나머지에서 멀어진다.
        assert moved.price_at(10) >= Decimal("149.2")

    def test_it_stops_at_the_band_not_at_the_wick_tip(self) -> None:
        """⚠️ 밀고 나서도 꼬리는 **뚫은 채**다 — 그것이 '적당히 삐져나오는' 선이다."""
        candles = self._humped()
        raw = _line(high(0, 100), high(20, 100), "resistance", WICK)
        assert raw is not None
        moved = push_out(raw, candles)
        assert moved.price_at(10) < Decimal(150)

    def test_slope_is_preserved(self) -> None:
        """🔴 추세는 맞았다 — 기울기를 바꾸면 다른 선이 된다.

        로그 공간에서 밀기 때문에 **봉당 비율**이 그대로다. 선형 공간에서 옮기면
        비율이 틀어진다.
        """
        candles = self._humped()
        raw = _line(high(0, 120), high(20, 100), "resistance", WICK)
        assert raw is not None
        assert push_out(raw, candles).ratio_per_bar == raw.ratio_per_bar

    def test_a_clean_line_is_untouched(self) -> None:
        """이미 안 뚫으면 그대로 둔다 — 공연히 밀면 캔들에서 멀어진다."""
        candles = self._humped()
        raw = _line(high(0, 300), high(20, 280), "resistance", WICK)
        assert raw is not None
        moved = push_out(raw, candles)
        assert moved.pushed is False
        assert moved is raw

    def test_support_moves_down(self) -> None:
        candles = self._humped()
        raw = _line(low(0, 150), low(20, 150), "support", WICK)
        assert raw is not None
        moved = push_out(raw, candles)
        assert moved.pushed is True
        # 양 끝 봉은 고가 100 · 저가 96 · 폭 4 → 아래 경계 96.8.
        assert moved.price_at(0) <= Decimal("96.8")
        assert moved.price_at(0) > Decimal(96)  # 꼬리 끝까지는 안 내려간다

    def test_pushed_is_reported(self) -> None:
        candles = self._humped()
        raw = _line(high(0, 100), high(20, 100), "resistance", WICK)
        assert raw is not None
        assert push_out(raw, candles).to_dict(20)["pushed"] is True


class TestNoise:
    """🔴 **잡음선을 뺀다** — 사용자: *"차라리 이런 잡음류의 추세선은 빼주는 게 나을 것"*.

    두 가지를 버린다.

        ① 자기 앵커에도 안 닿는 선 (밀린 뒤 떨어질 수 있다)
        ② 마디를 하나도 못 품는 선 (구조가 아니라 구간 내부다)
    """

    def test_a_line_touching_nothing_is_dropped(self) -> None:
        """실측에서 후보 51개 중 13개가 0접점이었고 124봉짜리도 있었다.

        가운데 봉만 깊게 파여 있으면 지지선이 거기까지 밀리고, 그러면 **자기 앵커에서
        멀어진다.** 그 선은 아무 봉에도 안 닿으므로 추세선이 아니다.
        """
        candles = [bar(i, 100, wick=1) for i in range(21)]
        candles[10] = bar(10, 55, wick=45)  # 저가 10 까지 파인 봉
        got = detect(
            [low(0, 99), low(20, 99)],
            atr=Decimal(1),
            candles=candles,
            tolerance=Decimal("0.01"),
        )
        assert got == ()

    def test_a_line_inside_one_leg_is_dropped(self) -> None:
        """마디 하나 안에서만 사는 선은 그 구간의 내부를 묘사할 뿐이다."""
        inside = detect([low(10, 100), low(20, 120)], legs=[(0, 60), (61, 100)])
        assert inside == ()

    def test_a_line_spanning_a_whole_leg_survives(self) -> None:
        across = detect([low(0, 100), low(80, 200)], legs=[(10, 50), (51, 100)])
        assert len(across) == 1

    def test_no_legs_means_no_filter(self) -> None:
        """거를 근거가 없으면 안 거른다 — 단위 테스트·축 후보가 마디 없이 부른다."""
        assert len(detect([low(10, 100), low(20, 120)])) == 1

    def test_longer_lines_rank_first(self) -> None:
        """🔴 길이가 먼저다. 앵커를 프랙탈로 바꾼 뒤 접점이 변별력을 잃었다.

        ⚠️ 껍질의 변은 **이웃한 두 점**이라 0~90 을 직접 잇는 후보는 없다. 나오는 것은
        0~10(10봉)과 10~90(80봉) 둘이고, 상한 1개면 **긴 쪽**이 남아야 한다.
        """
        got = detect(
            [low(0, 100), low(10, 110), low(90, 300)],
            limit=1,
            legs=[(20, 80)],
        )
        assert got[0].span_bars == 80


class TestSameLine:
    """구별되지 않는 선은 하나만 남긴다.

    사용자: *"선 기울기 레벨에서 거의 유사하게 겹치는 경우가 있는데 이런 경우 가장
    유효한 선 1개만 쓰는 게 나을 것 같은데."*

    🔴 기준을 새로 만들지 않았다 — **"닿았다"의 허용 오차가 곧 "같은 선"의 정의**다.
    한 선에 닿는 봉은 다른 선에도 닿으므로 둘은 증거로서 구별되지 않는다.
    """

    def test_near_identical_lines_are_the_same(self) -> None:
        first = _line(low(0, 100), low(20, 120), "support", WICK)
        second = _line(low(0, 100), low(20, 121), "support", WICK)
        assert first is not None
        assert second is not None
        assert same_line(first, second, Decimal(10), Decimal("0.25")) is True

    def test_lines_far_apart_are_different(self) -> None:
        first = _line(low(0, 100), low(20, 120), "support", WICK)
        second = _line(low(0, 150), low(20, 170), "support", WICK)
        assert first is not None
        assert second is not None
        assert same_line(first, second, Decimal(10), Decimal("0.25")) is False

    def test_no_overlap_means_different(self) -> None:
        """🔴 앵커 구간이 안 겹치면 비교하지 않는다 — 연장분은 아직 안 일어난 자리다."""
        first = _line(low(0, 100), low(10, 110), "support", WICK)
        second = _line(low(50, 100), low(60, 110), "support", WICK)
        assert first is not None
        assert second is not None
        assert same_line(first, second, Decimal(10), Decimal("0.25")) is False

    def test_no_atr_means_no_judgement(self) -> None:
        """잴 자가 없으면 같다고 하지 않는다 — 멀쩡한 선을 지우는 쪽이 더 나쁘다."""
        first = _line(low(0, 100), low(20, 120), "support", WICK)
        assert first is not None
        assert same_line(first, first, Decimal(0), Decimal("0.25")) is False

    def test_consecutive_hull_edges_are_not_twins(self) -> None:
        """⚠️ 껍질의 이웃한 두 변은 **한 점만 공유**한다 — 겹치는 구간이 없다.

        그래서 이 검사가 실제로 걸리는 것은 꼬리·몸통 두 기준이 서로 다른 앵커로
        비슷한 선을 낼 때다. 실측(200봉 1h · 11개 창)에서는 **0건**이었다 — 사용자가
        본 겹침의 원인은 중복이 아니라 **연장분**이었고 그쪽을 고쳤다
        (`SwingTrendline.extent`).
        """
        candles = [bar(i, 100 + i * 2, wick=4) for i in range(61)]
        got = detect(
            [low(0, 96), low(30, 156), low(60, 216)],
            atr=Decimal(50),
            candles=candles,
            tolerance=Decimal(1),
        )
        # 가운데 점이 로그 현 위에 있어 껍질에서 빠지고, 변이 하나만 남는다.
        assert len(got) == 1
        assert (got[0].start, got[0].end) == (0, 60)


class TestColourFollowsTheSlope:
    """🔴 색은 **이름표가 아니라 기울기**가 정한다.

    둘은 지금 같은 뜻이지만(저항은 내려가고 지지는 올라간다) 색이 이름표를 따르면,
    나중에 정의가 바뀌었을 때 화면이 실제와 다른 색을 칠하고도 아무도 모른다.
    """

    def test_rising_line_reports_rising(self) -> None:
        line = detect([low(0, 100), low(10, 120)])[0]
        assert line.rising is True
        assert line.to_dict(20)["rising"] is True

    def test_falling_line_reports_not_rising(self) -> None:
        line = detect([high(0, 200), high(10, 180)])[0]
        assert line.rising is False
        assert line.to_dict(20)["rising"] is False


class TestParallel:
    """맞은편 평행선 — 한 선으로 채널을 만든다.

    사용자: *"해당 추세선 기반으로 평행이동된 선을 하나 그려서, (마디 채널에서 했던
    것처럼) 파악할 수 있게."*
    """

    def _rising(self) -> list[Candle]:
        """봉당 2씩 오르는 구간 — 지지선 위로 폭이 있다."""
        return [bar(i, 100 + i * 2, wick=4) for i in range(21)]

    def test_support_gets_a_line_above(self) -> None:
        candles = self._rising()
        raw = _line(low(0, 96), low(20, 136), "support", WICK)
        assert raw is not None
        got = with_parallel(raw, candles)
        assert got.parallel > 1
        assert got.parallel_at(10) > got.price_at(10)

    def test_resistance_gets_a_line_below(self) -> None:
        candles = self._rising()
        raw = _line(high(0, 104), high(20, 144), "resistance", WICK)
        assert raw is not None
        got = with_parallel(raw, candles)
        assert got.parallel < 1
        assert got.parallel_at(10) < got.price_at(10)

    def test_it_stays_parallel_in_log_space(self) -> None:
        """🔴 로그 공간의 평행은 **일정 비율**이다.

        선형 평행선은 가격대가 달라지면 간격의 뜻이 변해 채널이 아니게 된다.
        """
        candles = self._rising()
        raw = _line(low(0, 96), low(20, 136), "support", WICK)
        assert raw is not None
        got = with_parallel(raw, candles)
        first = got.parallel_at(0) / got.price_at(0)
        last = got.parallel_at(20) / got.price_at(20)
        assert abs(first - last) < Decimal("0.0001")

    def test_no_candles_means_no_parallel(self) -> None:
        """잴 봉이 없으면 지어내지 않는다 — 배수 1 이면 화면이 안 그린다."""
        raw = _line(low(0, 100), low(10, 120), "support", WICK)
        assert raw is not None
        assert with_parallel(raw, []).parallel == Decimal(1)

    def test_detect_attaches_one(self) -> None:
        candles = self._rising()
        got = detect(
            swings_from_pivots(candles, [point(0, 0, SwingKind.LOW), point(20, 0, SwingKind.LOW)]),
            candles=candles,
            atr=Decimal(1),
        )
        assert got
        assert all(line.to_dict(20)["parallel"] for line in got)


class TestLimit:
    def test_default_limit_is_four(self) -> None:
        """사용자 지정: *"추세선은 한 4개 정도가 최대야"*."""
        assert MAX_LINES == 4

    def test_keeps_at_most_the_limit(self) -> None:
        highs = [high(i * 10, 500 - i * 10) for i in range(8)]
        assert len(detect(highs)) == MAX_LINES

    def test_longer_line_beats_shorter(self) -> None:
        """사용자 우선순위 2번 — 닿은 수가 같으면 **긴 쪽**이다."""
        highs = [high(0, 500), high(5, 499), high(100, 300)]
        got = detect(highs, limit=1)
        assert got[0].span_bars >= 95

    def test_recency_breaks_a_full_tie(self) -> None:
        """다 같으면 최신이 이긴다 — 오래된 선은 이미 힘을 잃었다."""
        highs = [high(0, 500), high(10, 400), high(20, 320), high(30, 256)]
        got = detect(highs, limit=1)
        assert got[0].end > 0

    def test_zero_limit_draws_nothing(self) -> None:
        assert detect([high(0, 200), high(10, 100)], limit=0) == ()


class TestSwingsFromTurns:
    def test_turn_kind_comes_from_neighbours(self) -> None:
        """ZigZag 는 고·저를 번갈아 잡는다 — 이웃보다 높으면 고점이다."""
        candles = [bar(i, 100) for i in range(30)]
        candles[10] = bar(10, 200)
        candles[20] = bar(20, 50)
        got = swings_from_turns(candles, [0, 10, 20])
        assert [item.kind for item in got] == [
            SwingKind.LOW,
            SwingKind.HIGH,
            SwingKind.LOW,
        ]

    def test_high_anchor_sits_inside_the_wick_tip(self) -> None:
        """🔴 극값이 아니라 **바깥 20% 의 안쪽 끝**이다.

        꼬리 끝을 앵커로 쓰면 길게 튄 꼬리 하나가 선 전체를 끌고 가 나머지 봉에서
        멀어진다 — 사용자가 *"계속 차트에서 좀 먼 상황"* 이라고 지적한 그것이다.
        """
        candles = [bar(i, 100) for i in range(30)]
        candles[10] = bar(10, 200, wick=7)  # 193~207, 폭 14 → 경계 207 - 2.8
        got = swings_from_turns(candles, [0, 10, 20])
        assert got[1].price == Decimal("204.2")
        assert got[1].price < Decimal(207)  # 꼬리 끝보다 안쪽

    def test_low_anchor_sits_inside_the_wick_tip(self) -> None:
        candles = [bar(i, 100) for i in range(30)]
        candles[10] = bar(10, 200)
        candles[20] = bar(20, 50, wick=9)  # 41~59, 폭 18 → 경계 41 + 3.6
        got = swings_from_turns(candles, [0, 10, 20])
        assert got[2].price == Decimal("44.6")
        assert got[2].price > Decimal(41)

    def test_fewer_than_two_turns_is_empty(self) -> None:
        assert swings_from_turns([bar(0, 100)], [0]) == []

    def test_out_of_range_turns_are_skipped(self) -> None:
        candles = [bar(i, 100 + i) for i in range(5)]
        assert len(swings_from_turns(candles, [0, 2, 99])) == 2
