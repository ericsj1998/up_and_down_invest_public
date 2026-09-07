"""이의제기·재현 탐색·결과 (`objection.py` · `reproduce.py` · `outcome.py`).

여기서 지키려는 성질:

    **같은 이의는 한 건이다** — 새로고침이 "몇 번 지적했나"를 부풀리면 안 된다.
    **손그림이 규칙으로 환원을 시도받는다** — 그것이 정답지가 되지 않는 유일한 장치다.
    **보유 손익이 전략 성과로 안 읽힌다** — 이름과 문서에 박혀 있어야 한다.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.orchestration.inspection.objection import (
    Objection,
    load_all,
    mark_seen,
    save,
    saw_outcome_at,
)
from updown.orchestration.inspection.outcome import forward
from updown.orchestration.inspection.reproduce import (
    MATCH_ATR,
    count_of,
    line_distance,
    search_for_count,
    search_for_line,
)
from updown.orchestration.inspection.snapshot import build_frame

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
_AT = datetime(2025, 6, 23, 10, tzinfo=UTC)


def bar(index: int, open_: int, close: int, *, wick: int = 2) -> Candle:
    """봉 하나."""
    low, high = min(open_, close), max(open_, close)
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=_AT + timedelta(hours=index),
        open=Decimal(open_),
        high=Decimal(high + wick),
        low=Decimal(low - wick),
        close=Decimal(close),
        volume=Decimal(10),
    )


def wave(count: int) -> list[Candle]:
    """스윙이 잡히도록 오르내리는 봉들."""
    out: list[Candle] = []
    for i in range(count):
        base = 100 + i * 2 + (12 if (i // 4) % 2 else 0)
        out.append(bar(i, base, base + (3 if (i // 4) % 2 == 0 else -3)))
    return out


def an_objection(**kwargs: object) -> Objection:
    """기본값이 채워진 이의제기."""
    fields: dict[str, object] = {
        "symbol": "KRW-BTC",
        "as_of": _AT,
        "timeframe": "1h",
        "flag": "structure.trendline",
        "kind": "removed",
        "shape": {"x1": 3, "y1": "100"},
    }
    fields.update(kwargs)
    return Objection(**fields)  # type: ignore[arg-type]


class TestObjectionIdentity:
    def test_same_content_is_one_record(self) -> None:
        """🔴 새로고침·더블클릭이 '두 번 지적했다'가 되면 안 된다."""
        assert an_objection().id == an_objection().id

    def test_time_does_not_change_the_id(self) -> None:
        later = an_objection(created_at=datetime(2030, 1, 1, tzinfo=UTC))
        assert later.id == an_objection().id

    def test_different_shape_is_a_different_record(self) -> None:
        assert an_objection(shape={"x1": 9}).id != an_objection().id

    def test_different_flag_is_a_different_record(self) -> None:
        assert an_objection(flag="structure.box").id != an_objection().id

    def test_comment_does_not_split_the_record(self) -> None:
        """같은 지적에 말만 덧붙인 것은 여전히 같은 지적이다."""
        assert an_objection(comment="이건 아니다").id == an_objection().id

    def test_a_different_window_is_a_different_record(self) -> None:
        """🔴 창 크기가 다르면 **같은 좌표가 다른 자리**다 — 같은 지적이 아니다."""
        assert an_objection(bars=200).id != an_objection(bars=300).id


class TestSelfDescribing:
    """🔴 기록이 **자기 자신을 설명해야** 나중에 다시 그릴 수 있다.

    `shape` 의 좌표는 `index`(봉 번호)인데 그 번호는 "창 안에서 몇 번째"라는 뜻이다.
    창 크기를 안 남기면 같은 시점을 300봉으로 다시 열었을 때 봉 199 가 전혀 다른 봉을
    가리키고, 화면은 **조용히 다른 자리**에 선을 그린다.
    """

    def test_window_size_survives_a_round_trip(self, tmp_path: Path) -> None:
        save(an_objection(bars=200), tmp_path)
        assert load_all(tmp_path)[0].bars == 200

    def test_old_records_report_zero_rather_than_guessing(self, tmp_path: Path) -> None:
        """⛔ 200 으로 채워 넣지 않는다 — 어긋난 좌표를 맞는 것처럼 보여 준다."""
        (tmp_path / "old.json").write_text(
            json.dumps(
                {
                    "symbol": "KRW-BTC",
                    "as_of": _AT.isoformat(),
                    "timeframe": "1h",
                    "flag": "structure.trendline",
                    "kind": "removed",
                    "shape": {},
                    "created_at": _AT.isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert load_all(tmp_path)[0].bars == 0

    def test_point_timestamps_ride_along_untouched(self, tmp_path: Path) -> None:
        """좌표에 실린 절대 시각이 그대로 남아야 창이 달라져도 봉을 되찾는다."""
        line = [
            {"index": 85, "price": "52569700", "ts": "2022-03-10T13:00:00+00:00"},
            {"index": 199, "price": "48435676", "ts": "2022-03-14T00:00:00+00:00"},
        ]
        save(an_objection(shape={"line": line}, bars=200), tmp_path)
        got = load_all(tmp_path)[0].shape["line"]
        assert got[0]["ts"] == "2022-03-10T13:00:00+00:00"
        assert got[1]["index"] == 199


class TestObjectionStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        save(an_objection(comment="추세선이 여기 있으면 안 된다"), tmp_path)
        got = load_all(tmp_path)
        assert len(got) == 1
        assert got[0].comment == "추세선이 여기 있으면 안 된다"
        assert got[0].as_of == _AT

    def test_missing_folder_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert load_all(tmp_path / "없음") == []

    def test_resubmitting_overwrites_rather_than_duplicates(self, tmp_path: Path) -> None:
        save(an_objection(), tmp_path)
        save(an_objection(saw_outcome=True), tmp_path)
        got = load_all(tmp_path)
        assert len(got) == 1
        # 나중 것이 이긴다 — 오염 표시는 켜지는 쪽으로 남아야 한다.
        assert got[0].saw_outcome is True

    def test_newest_first(self, tmp_path: Path) -> None:
        save(an_objection(shape={"a": 1}, created_at=datetime(2025, 1, 1, tzinfo=UTC)), tmp_path)
        save(an_objection(shape={"a": 2}, created_at=datetime(2026, 1, 1, tzinfo=UTC)), tmp_path)
        assert load_all(tmp_path)[0].shape == {"a": 2}

    def test_unreadable_file_raises_rather_than_vanishing(self, tmp_path: Path) -> None:
        """🔴 조용히 빼면 '지적한 적 없다'가 된다 (절대 규칙 #8)."""
        (tmp_path / "broken.json").write_text("{", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_all(tmp_path)


class TestRevealLog:
    """🔴 여기가 순환이었다.

    처음엔 `saw_outcome_at` 이 **이의제기 목록**을 뒤졌다. 그런데 그 플래그를 켜는
    유일한 경로가 "이미 켜진 이의제기가 있는가"라 첫 불이 영원히 안 붙었다. 실제로
    결과를 열고 이의제기를 냈더니 `saw_outcome=False` 로 남는 것을 확인하고 갈랐다.
    """

    def test_nothing_seen_at_first(self, tmp_path: Path) -> None:
        assert saw_outcome_at("KRW-BTC", _AT, tmp_path) is False

    def test_marking_turns_it_on(self, tmp_path: Path) -> None:
        mark_seen("KRW-BTC", _AT, tmp_path)
        assert saw_outcome_at("KRW-BTC", _AT, tmp_path) is True

    def test_first_call_reports_not_already_seen(self, tmp_path: Path) -> None:
        """처음 여는 것과 다시 여는 것은 다르다."""
        assert mark_seen("KRW-BTC", _AT, tmp_path) is False
        assert mark_seen("KRW-BTC", _AT, tmp_path) is True

    def test_it_is_per_symbol_and_moment(self, tmp_path: Path) -> None:
        mark_seen("KRW-BTC", _AT, tmp_path)
        assert saw_outcome_at("KRW-BTC", _AT + timedelta(hours=1), tmp_path) is False
        assert saw_outcome_at("KRW-ETH", _AT, tmp_path) is False

    def test_an_objection_alone_does_not_mark_the_moment(self, tmp_path: Path) -> None:
        """⛔ 이의제기가 공개 기록을 대신하면 순환으로 돌아간다."""
        save(an_objection(saw_outcome=True), tmp_path)
        assert saw_outcome_at("KRW-BTC", _AT, tmp_path) is False


class TestReproduce:
    def test_finds_a_setting_that_hits_the_target(self) -> None:
        """파라미터로 재현되면 축 후보 경로로 간다 (규칙 #12)."""
        candles = wave(200)
        current = count_of(candles, Timeframe.H1, "structure.trendline")
        got = search_for_count(candles, Timeframe.H1, "structure.trendline", current)
        assert got.reproduced is True
        assert "파라미터 문제" in got.verdict

    def test_impossible_target_says_the_definition_is_wrong(self) -> None:
        """🔴 어떤 값으로도 안 나오면 그것이 더 값진 발견이다."""
        got = search_for_count(wave(200), Timeframe.H1, "structure.trendline", 99999)
        assert got.reproduced is False
        assert "정의 자체" in got.verdict

    def test_near_miss_is_not_called_a_definition_problem(self) -> None:
        """🔴 격자 해상도 차이를 '알고리즘을 다시 써라'로 읽으면 며칠이 날아간다.

        목표 15 에 14 가 나오는 것은 소수 격자를 12칸으로 훑어서 생긴 차이다.
        """
        candles = wave(200)
        current = count_of(candles, Timeframe.H1, "structure.trendline")
        got = search_for_count(candles, Timeframe.H1, "structure.trendline", current + 1)
        assert "정의 자체" not in got.verdict
        assert "파라미터 문제" in got.verdict

    def test_far_miss_is_still_a_definition_problem(self) -> None:
        got = search_for_count(wave(200), Timeframe.H1, "structure.box", 5000)
        assert "정의 자체" in got.verdict

    def test_at_most_three_candidates(self) -> None:
        """규칙 #12 — 실험이 많을수록 우연히 이기는 것이 나온다."""
        got = search_for_count(wave(200), Timeframe.H1, "structure.trendline", 5)
        assert len(got.best) <= 3

    def test_search_is_bounded(self) -> None:
        """상한이 없으면 화면이 멈춘 것처럼 보인다."""
        got = search_for_count(wave(150), Timeframe.H1, "structure.trendline", 5)
        assert 0 < got.tried <= 240

    def test_flag_without_parameters_says_so(self) -> None:
        got = search_for_count(wave(100), Timeframe.H1, "indicator.rsi", 1)
        assert got.reproduced is False
        assert "탐색할 것이 없다" in got.verdict

    def test_zero_distance_candidate_reports_its_count(self) -> None:
        candles = wave(200)
        current = count_of(candles, Timeframe.H1, "structure.box")
        got = search_for_count(candles, Timeframe.H1, "structure.box", current)
        assert got.best[0].count == current


class TestOutcome:
    def test_no_forward_bars_is_none_not_zero(self) -> None:
        """0% 로 내면 '안 움직였다'는 관측으로 둔갑한다."""
        assert forward([], "1h", _AT, Decimal(1000)) is None

    def test_hold_pnl_uses_the_first_open_not_the_known_close(self) -> None:
        """시점의 종가로 잡으면 '그 시점을 알고 샀다'가 된다."""
        got = forward([bar(1, 100, 110), bar(2, 110, 120)], "1h", _AT, Decimal(1000))
        assert got is not None
        assert got.entry == Decimal(100)
        # 1000원으로 10주, 100 → 120 이므로 200원.
        assert got.hold_pnl == Decimal(200)
        assert got.hold_pct == Decimal(20)

    def test_excursions_use_wicks(self) -> None:
        """최대 유·불리는 몸통이 아니라 도달한 가격이다."""
        got = forward([bar(1, 100, 100, wick=30)], "1h", _AT, Decimal(1000))
        assert got is not None
        assert got.max_gain_pct == Decimal(30)
        assert got.max_drop_pct == Decimal(-30)

    def test_candle_keys_match_the_frontend_type(self) -> None:
        """스냅샷과 같은 규약이어야 한다 — 다르면 이후 전개만 일직선이 된다."""
        got = forward([bar(1, 100, 110)], "1h", _AT, Decimal(1000))
        assert got is not None
        assert set(got.to_dict()["candles"][0]) == {
            "ts",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }

    def test_serialises_prices_as_strings(self) -> None:
        got = forward([bar(1, 100, 110)], "1h", _AT, Decimal(1000))
        assert got is not None
        assert isinstance(got.to_dict()["hold_pnl"], str)


class TestLineDistance:
    """🔴 개수 대신 **좌표**로 재는 부분.

    개수는 완전히 다른 선으로도 맞는다. 사람이 주장한 것은 "그 선이 저기"이므로
    좌표가 본선이다.
    """

    def test_identical_lines_are_zero(self) -> None:
        shape = {"x1": 0, "y1": "100", "x2": 10, "y2": "110"}
        assert line_distance([(0, 100.0), (10, 110.0)], shape, 1.0) == 0

    def test_parallel_offset_is_measured_in_atr(self) -> None:
        """ATR 로 나눈다 — 고정 %는 시간축 간 4.1배 다른 뜻이었다 (§1-0h)."""
        shape = {"x1": 0, "y1": "100", "x2": 10, "y2": "110"}
        # 5 만큼 위에 그린 선. ATR 이 10 이면 0.5xATR.
        assert line_distance([(0, 105.0), (10, 115.0)], shape, 10.0) == 0.5

    def test_same_endpoints_different_slope_is_not_zero(self) -> None:
        """🔴 양 끝만 보면 기울기가 다른 선을 같은 선으로 센다."""
        shape = {"x1": 0, "y1": "100", "x2": 10, "y2": "100"}
        # 끝점은 같지만 가운데가 크게 휘는 선 (V 자를 직선으로 근사한 셈).
        got = line_distance([(0, 100.0), (10, 100.0)], shape, 1.0)
        assert got == 0  # 둘 다 직선이라 같다 — 아래 케이스가 진짜 검사다
        tilted = {"x1": 0, "y1": "90", "x2": 10, "y2": "110"}
        assert line_distance([(0, 100.0), (10, 100.0)], tilted, 1.0) != 0

    def test_no_overlap_is_none_not_far(self) -> None:
        """🔴 겹치는 봉 구간이 없으면 **비교 불가**다.

        서로 다른 시기에 그은 두 선은 가격이 비슷해도 같은 선이 아니다. 큰 수로
        뭉개면 정렬만 뒤로 밀릴 뿐 "재현 안 됨"과 구분되지 않는다.
        """
        shape = {"x1": 100, "y1": "100", "x2": 110, "y2": "110"}
        assert line_distance([(0, 100.0), (10, 110.0)], shape, 1.0) is None

    def test_zero_atr_is_none(self) -> None:
        """잴 자가 없으면 답하지 않는다 (절대 규칙 #8)."""
        shape = {"x1": 0, "y1": "100", "x2": 10, "y2": "110"}
        assert line_distance([(0, 100.0), (10, 110.0)], shape, 0.0) is None

    def test_one_point_is_not_a_line(self) -> None:
        assert line_distance([(0, 100.0)], {"x1": 0, "y2": "1"}, 1.0) is None


def log_line_shape(samples: int = 24) -> dict[str, object]:
    """봉 0~200 에서 100 → 900 으로 가는 **로그 직선** 하나.

    `swing_trendline` 이 내는 모양 그대로다 — 현(chord) 좌표(`x1..y2`)와 실제로 그려지는
    점들(`curve`)을 **둘 다** 담는다. 이 둘이 다르다는 것이 아래 검사의 전부다.
    """
    curve = [(200 * i / (samples - 1), 100 * 9 ** (i / (samples - 1))) for i in range(samples)]
    return {
        "x1": 0,
        "y1": "100",
        "x2": 200,
        "y2": "900",
        "curve": [[x, str(price)] for x, price in curve],
    }


class TestLogLineDistance:
    """🔴 **두 선을 같은 공간에서 읽는다.**

    `swing_trendline` 은 로그 직선이라 선형 차트에서 **곡선**으로 그려진다. 그런데
    거리를 잴 때 양쪽을 다 직선으로 펴면, 그 곡선을 현으로 근사하는 일이 된다 —
    **있지도 않은 거리**가 생기고 사람이 앵커만 옮긴 선에 "정의 자체가 다르다"가 붙는다.

    사람이 화면에서 본 것은 곡선이다. 판정이 현을 보면 도구가 자기 그림을 배신한다
    (절대 규칙 #8).
    """

    def test_tracing_the_drawn_curve_is_a_match(self) -> None:
        """곡선 위의 두 점을 찍으면 거리가 0 이어야 한다.

        구간을 일부러 **짧게**(50~150) 잡았다. 도형과 x 범위가 같으면 현끼리도 우연히
        맞아서, 옛 코드도 통과해 버린다 — 갈라지는 곳은 범위가 다를 때다.
        """
        on_curve = [(50.0, 100 * 9**0.25), (150.0, 100 * 9**0.75)]
        got = line_distance(on_curve, log_line_shape(), 10.0)
        assert got is not None
        assert got < 0.1, f"곡선을 그대로 따라 그렸는데 {got:.2f}xATR 이 나왔다"
        assert got < float(MATCH_ATR)

    def test_reading_it_as_a_chord_is_far_off(self) -> None:
        """🔴 회귀 방지 — `curve` 를 떼면(=옛 동작) 같은 선이 **15xATR** 떨어진다.

        같은 사람 선, 같은 추세, 차이는 **읽는 공간뿐**이다. 이 숫자가 `MATCH_ATR`(0.25)
        을 60배 넘기므로 결론이 "파라미터 문제"에서 "정의 문제"로 뒤집힌다.
        """
        shape = log_line_shape()
        del shape["curve"]
        on_curve = [(50.0, 100 * 9**0.25), (150.0, 100 * 9**0.75)]
        got = line_distance(on_curve, shape, 10.0)
        assert got is not None
        assert got > 10

    def test_a_broken_curve_falls_back_instead_of_crashing(self) -> None:
        """모양이 어긋난 `curve` 는 **절반만 읽지 않는다** — 짧아진 선과의 거리는 거짓이다."""
        shape = {"x1": 0, "y1": "100", "x2": 10, "y2": "110", "curve": [[0, "100"], "nope"]}
        assert line_distance([(0, 100.0), (10, 110.0)], shape, 1.0) == 0

    def test_a_single_point_curve_is_not_a_curve(self) -> None:
        shape = {"x1": 0, "y1": "100", "x2": 10, "y2": "110", "curve": [[0, "100"]]}
        assert line_distance([(0, 100.0), (10, 110.0)], shape, 1.0) == 0


class TestSearchForLine:
    def test_a_line_the_algorithm_already_draws_is_reproduced(self) -> None:
        """알고리즘이 지금 긋는 선을 그대로 그리면 표준값에서 재현된다."""
        candles = wave(200)
        frame = build_frame(candles, Timeframe.H1, ["structure.trendline"])
        drawn_from = frame.layers[0].shapes[0]
        line = [
            (float(drawn_from["x1"]), float(drawn_from["y1"])),
            (float(drawn_from["x2"]), float(drawn_from["y2"])),
        ]
        got = search_for_line(candles, Timeframe.H1, "structure.trendline", line)
        assert got.reproduced is True
        assert "파라미터 문제" in got.verdict

    def test_a_line_nowhere_near_the_data_is_a_definition_problem(self) -> None:
        """🔴 아무 데도 없는 자리에 그으면 정의 문제로 답해야 한다."""
        candles = wave(200)
        got = search_for_line(candles, Timeframe.H1, "structure.trendline", [(0, 1.0), (150, 2.0)])
        assert got.reproduced is False
        assert "정의" in got.verdict

    def test_two_points_are_required(self) -> None:
        got = search_for_line(wave(100), Timeframe.H1, "structure.trendline", [(0, 100.0)])
        assert got.reproduced is False
        assert "두 개" in got.verdict

    def test_flag_without_parameters_says_so(self) -> None:
        got = search_for_line(wave(100), Timeframe.H1, "indicator.rsi", [(0, 100.0), (10, 100.0)])
        assert "탐색할 것이 없다" in got.verdict

    def test_candidates_carry_atr_distance(self) -> None:
        """거리가 ATR 배수여야 종목·시간축을 넘어 비교된다."""
        candles = wave(200)
        frame = build_frame(candles, Timeframe.H1, ["structure.trendline"])
        shape = frame.layers[0].shapes[0]
        got = search_for_line(
            candles,
            Timeframe.H1,
            "structure.trendline",
            [(float(shape["x1"]), float(shape["y1"])), (float(shape["x2"]), float(shape["y2"]))],
        )
        assert got.best
        assert got.best[0].distance <= float(MATCH_ATR)
