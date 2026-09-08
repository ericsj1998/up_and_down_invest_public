"""백필 정합성 검증 — **"완료"가 "채웠다"를 뜻하는가** (P1 §1-0t).

실제 사고 재현이 첫 테스트다: AAPL 1h 재백필이 40분을 돌고 `완료` 를 찍었는데
커버리지가 1봉도 안 늘었다. 기존 무결성 검사는 **받아 온 봉들 안의** 구멍만 봐서
그 사실을 못 잡았다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.coverage_check import verdict_for

ANCHOR = datetime(2024, 7, 1, tzinfo=UTC)
NOW = datetime(2026, 8, 9, tzinfo=UTC)


def verdict(
    stored_start: datetime | None,
    stored_end: datetime | None = NOW,
    bars: int = 10_000,
    timeframe: Timeframe = Timeframe.H1,
    reference_bars: int | None = None,
):
    """앵커부터 현재까지 요청한 판정 하나."""
    return verdict_for(
        symbol="AAPL",
        timeframe=timeframe,
        requested_start=ANCHOR,
        requested_end=NOW,
        stored_start=stored_start,
        stored_end=stored_end,
        bars=bars,
        reference_bars=reference_bars,
        reference=Timeframe.M15 if reference_bars else None,
    )


def test_the_aapl_incident_is_caught() -> None:
    """🔴 실제 사고 재현 — 2024-07-01 을 요청했는데 2025-04-02 부터 있었다."""
    result = verdict(stored_start=datetime(2025, 4, 2, tzinfo=UTC), bars=8_198)

    assert not result.ok
    assert "시작이 275일 늦다" in result.reason
    assert "🔴" in result.render()


def test_session_offset_within_a_day_is_not_a_hole() -> None:
    """앵커는 00:00 UTC 인데 미국 장은 13:30 에 연다 — 하루 안쪽은 정상이다."""
    result = verdict(stored_start=ANCHOR + timedelta(hours=13, minutes=30))

    assert result.ok
    assert result.reason == ""


def test_weekend_tail_is_not_a_hole() -> None:
    """금요일 마감 뒤에 돌리면 최신 봉이 사흘 전이다 — 결손이 아니라 주말이다."""
    assert verdict(stored_start=ANCHOR, stored_end=NOW - timedelta(days=3)).ok


def test_a_week_old_tail_is_a_hole() -> None:
    """주말 + 공휴일을 넘기면 수집이 멈춘 것이다."""
    result = verdict(stored_start=ANCHOR, stored_end=NOW - timedelta(days=8))

    assert not result.ok
    assert "끝이 8일 이르다" in result.reason


def test_empty_storage_is_not_silently_ok() -> None:
    """적재가 없으면 "구멍 0건" 이 아니라 **전 구간 결손**이다."""
    result = verdict(stored_start=None, stored_end=None, bars=0)

    assert not result.ok
    assert result.reason == "적재가 하나도 없다"


def test_density_compares_against_the_same_symbol() -> None:
    """1h 봉 수는 같은 종목 15m 의 약 1/4 여야 한다 — 휴장일 달력을 몰라도 된다."""
    # 15m 이 40,000봉이면 1h 은 10,000봉이 정상이다.
    assert verdict(stored_start=ANCHOR, bars=10_000, reference_bars=40_000).density == 1.0
    sparse = verdict(stored_start=ANCHOR, bars=8_000, reference_bars=40_000)
    assert sparse.density == pytest.approx(0.8)
    assert "성기다" in sparse.render()


def test_density_does_not_gate() -> None:
    """⛔ 밀도로 막으면 그 임계값이 곧 새 자유 파라미터가 된다 (§5.6.7).

    경계는 명확하지만 밀도는 합성 경계·조기마감 때문에 정확히 안 맞을 수 있다.
    막지 않고 **찍는다**.
    """
    result = verdict(stored_start=ANCHOR, bars=5_000, reference_bars=40_000)

    assert result.density == pytest.approx(0.5)
    assert result.ok, "경계가 멀쩡하면 밀도가 낮아도 통과시킨다 — 대신 화면에 남는다"


def test_naive_timestamps_are_rejected() -> None:
    """절대 규칙 #7 — naive 를 UTC 로 가정하면 9시간 어긋난 판정이 조용히 통과한다."""
    with pytest.raises(ValueError, match="naive"):
        verdict_for(
            symbol="AAPL",
            timeframe=Timeframe.H1,
            requested_start=datetime(2024, 7, 1),
            requested_end=NOW,
            stored_start=None,
            stored_end=None,
            bars=0,
        )


def test_source_limit_passes_when_synthesis_covers_it() -> None:
    """🔴 **고칠 수 없는 것에 빨간불을 켜지 않는다.**

    토스는 미국 주식의 과거 1h 을 거의 안 준다 (기대 1,489봉에 46봉). 백필을 몇 번
    돌려도 안 채워지므로 계속 실패로 표시하면 **항상 울리는 경보**가 되고, 그러면
    진짜 결손이 섞여도 아무도 안 본다.

    그런데 측정은 이미 15m 합성으로 그 구간을 본다 — 검증이 던져야 할 질문은
    "DB 에 다 있나"가 아니라 **"측정이 앵커 구간을 볼 수 있나"** 다.
    """
    result = verdict_for(
        symbol="MSFT",
        timeframe=Timeframe.H1,
        requested_start=ANCHOR,
        requested_end=NOW,
        stored_start=datetime(2025, 7, 18, tzinfo=UTC),
        stored_end=NOW,
        bars=6_437,
        reference_bars=33_206,
        reference=Timeframe.M15,
        reference_start=ANCHOR,
    )

    assert result.ok, "합성이 덮으므로 통과시킨다"
    assert result.reason == ""
    assert "합성이 덮는다" in result.render(), "조용히 넘기지는 않는다"


def test_source_limit_still_fails_when_synthesis_cannot_cover() -> None:
    """기준 TF 도 짧으면 그건 진짜 결손이다 — 합성이 만능은 아니다."""
    result = verdict_for(
        symbol="MSFT",
        timeframe=Timeframe.H1,
        requested_start=ANCHOR,
        requested_end=NOW,
        stored_start=datetime(2025, 7, 18, tzinfo=UTC),
        stored_end=NOW,
        bars=6_437,
        reference_bars=1_000,
        reference=Timeframe.M15,
        reference_start=datetime(2025, 7, 18, tzinfo=UTC),
    )

    assert not result.ok
    assert "시작이" in result.reason
