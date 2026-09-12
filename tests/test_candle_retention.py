"""봉 보관 정책 — 일봉은 어떤 설정으로도 안 지워진다 (T278 · 2026-09-12).

Note:
    🔴 봉 표는 `ts` 기준 월 파티션이라 한 달 안에 **모든 축이 섞여** 있다. 그래서 파티션을 떼면
    일봉까지 사라진다 — 축을 지정한 `DELETE` 만 쓸 수 있고, 그 계획에 일봉이 절대 들어가면
    안 된다. 이 시험이 그 자물쇠다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from updown.common.db.retention import (
    Policy,
    count_sql,
    delete_sql,
    load_policy,
    plan,
)
from updown.common.domain.instrument import Timeframe

NOW = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)


def test_the_shipped_policy_loads() -> None:
    """저장소의 `config/retention.yml` 이 읽히고 일봉이 잠겨 있다."""
    got = load_policy()
    assert Timeframe.D1 in got.never_delete
    assert got.keep_days[Timeframe.M5] > 0


def test_daily_is_never_swept_even_if_someone_adds_it() -> None:
    """설정에 일봉을 적어도 계획에서 빠진다 — 정책 파일 오타가 데이터를 못 지운다."""
    policy = Policy(
        keep_days={Timeframe.M5: 60, Timeframe.D1: 30},
        never_delete=frozenset({Timeframe.D1}),
    )
    frames = [sweep.timeframe for sweep in plan(policy, NOW)]
    assert Timeframe.D1 not in frames
    assert Timeframe.M5 in frames


def test_cutoff_is_now_minus_keep_days() -> None:
    policy = Policy(keep_days={Timeframe.M5: 60}, never_delete=frozenset())
    (sweep,) = plan(policy, NOW)
    assert sweep.cutoff == datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    assert sweep.keep_days == 60


def test_shortest_window_comes_first() -> None:
    """짧게 남기는 축부터 — 가장 많이 지워지는 것을 먼저 보고 멈출 수 있다."""
    policy = Policy(
        keep_days={Timeframe.H1: 400, Timeframe.M5: 60, Timeframe.M15: 180},
        never_delete=frozenset(),
    )
    assert [item.keep_days for item in plan(policy, NOW)] == [60, 180, 400]


def test_empty_policy_plans_nothing() -> None:
    assert plan(Policy(keep_days={}, never_delete=frozenset()), NOW) == []


def test_statements_use_bind_parameters() -> None:
    """값을 문자열로 이어 붙이지 않는다 (§10 — 원시 SQL 은 전부 바인드)."""
    for sql in (delete_sql(), count_sql()):
        assert ":frame" in sql
        assert ":cutoff" in sql
        assert "'" not in sql


def test_unknown_timeframe_is_refused(tmp_path: Path) -> None:
    """오타가 조용히 지나가면 엉뚱한 축이 안 지워지거나 지워진다."""
    bad = tmp_path / "retention.yml"
    bad.write_text("candles:\n  keep_days:\n    7m: 60\n", encoding="utf-8")
    with pytest.raises(ValueError, match="모르는 시간축"):
        load_policy(bad)


def test_non_positive_keep_days_is_refused(tmp_path: Path) -> None:
    """0 일이면 전부 지우라는 뜻이 된다 — 사고다."""
    bad = tmp_path / "retention.yml"
    bad.write_text("candles:\n  keep_days:\n    5m: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="양수가 아니다"):
        load_policy(bad)
