"""리포트 읽기 계층 — **없는 것을 0 으로 만들지 않는가**를 먼저 본다.

깨진 파일이나 빈 디렉터리를 조용히 "거래 0건"으로 넘기면 화면에서 측정 실패와 구분되지
않는다 (절대 규칙 #8). 그래서 경계 처리 테스트가 앞에 있다.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from updown.orchestration.reporting import ReportNotFoundError, RunStore, aggregate_phases


def write(directory: Path, run_id: str, **overrides: object) -> None:
    """최소 리포트 하나를 쓴다.

    Args:
        directory: 대상 디렉터리.
        run_id: 실행 식별자 겸 파일명.
        overrides: 덮어쓸 최상위 칸.
    """
    payload: dict[str, object] = {
        "run_id": run_id,
        "preset": "v6_matrend_nearest_next_support_nearest_with_buffer",
        "symbol": "KRW-BTC",
        "display": "비트코인(KRW-BTC)",
        "timeframe": "15m",
        "outcomes": {"followed": 157, "not_followed": 285, "expired": 5, "realized": 447},
        "funnel": {"detected": 1368, "entered": 447},
        "plans": {
            "degenerate": False,
            "first_rr": {"median": "1.492"},
            "stop_pct": {"median": "0.0039"},
        },
    }
    payload.update(overrides)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_missing_directory_is_empty_not_an_error(tmp_path: Path) -> None:
    """아직 측정을 안 돌린 것은 **정상 상태**다 — 화면이 빈 목록을 받는다."""
    assert RunStore(tmp_path / "없음").list_runs() == []


def test_broken_file_raises_instead_of_looking_like_zero_trades(tmp_path: Path) -> None:
    """🔴 깨진 리포트를 빈 dict 로 넘기면 "거래 0건"으로 보인다 — 멈추는 편이 낫다."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "run_broken.json").write_text("{ 이건 JSON 이 아니다", encoding="utf-8")

    with pytest.raises(ReportNotFoundError):
        RunStore(tmp_path).list_runs()


def test_summary_reads_the_fields_a_table_needs(tmp_path: Path) -> None:
    """목록 화면이 쓰는 칸들이 채워진다."""
    write(tmp_path, "run_BTC_15m_v6")

    summary = RunStore(tmp_path).list_runs()[0]

    assert summary.run_id == "run_BTC_15m_v6"
    assert summary.setup == "추세 눌림목"
    assert summary.entered == 447
    assert summary.detected == 1368
    assert summary.first_rr_median == Decimal("1.492")
    assert summary.stop_pct_median == Decimal("0.0039")


def test_win_rate_is_none_without_trades(tmp_path: Path) -> None:
    """익절률 0% 와 "거래가 없었다"는 다른 사실이다."""
    write(tmp_path, "run_empty", outcomes={"followed": 0, "realized": 0})

    assert RunStore(tmp_path).list_runs()[0].win_rate is None


def test_tag_filter_selects_one_round(tmp_path: Path) -> None:
    """회차 태그로 고른다 — 회차를 섞으면 규칙이 다른 실행이 한 표에 들어간다."""
    write(tmp_path, "run_a_v5", preset="v5_order_nearest")
    write(tmp_path, "run_b_v6", preset="v6_matrend_nearest")

    picked = RunStore(tmp_path).list_runs(tag="v6")

    assert [item.run_id for item in picked] == ["run_b_v6"]


def test_setup_label_falls_back_instead_of_guessing(tmp_path: Path) -> None:
    """모르는 프리셋을 기존 셋업 이름으로 몰지 않는다 — 비교가 거짓말한다."""
    write(tmp_path, "run_new", preset="v9_brandnew_nearest")

    assert RunStore(tmp_path).list_runs()[0].setup == "v9"


def test_raw_report_is_available_in_full(tmp_path: Path) -> None:
    """요약은 편의고 **전문이 계약**이다 — 화면이 새 칸을 원해도 여기를 안 고친다."""
    write(tmp_path, "run_full")

    raw = RunStore(tmp_path).load_run("run_full")

    assert raw["outcomes"]["not_followed"] == 285


def test_loading_an_unknown_run_raises(tmp_path: Path) -> None:
    """없는 실행을 빈 리포트로 주지 않는다."""
    with pytest.raises(ReportNotFoundError, match="없다"):
        RunStore(tmp_path).load_run("run_nope")


def test_degenerate_plan_is_surfaced(tmp_path: Path) -> None:
    """🔴 계획이 상수로 붕괴하면 나머지 수치를 읽으면 안 된다 (§1-0s RR 1.00 결함)."""
    write(
        tmp_path,
        "run_bad",
        plans={"degenerate": True, "first_rr": {"median": "1.00"}, "stop_pct": {"median": "0.01"}},
    )

    assert RunStore(tmp_path).list_runs()[0].degenerate is True


def phases(
    sample: int, correct: int, persisted: int, cells: list[dict[str, object]]
) -> dict[str, object]:
    """국면 검증 칸 하나.

    Args:
        sample: 표본. `correct`: 적중. `persisted`: 지속.
        cells: 교차표 칸들.

    Returns:
        리포트의 `phases` 블록.
    """
    return {
        "sample": sample,
        "correct": correct,
        "persisted": persisted,
        "accuracy": str(Decimal(correct) / Decimal(sample)) if sample else None,
        "persistence": str(Decimal(persisted) / Decimal(sample)) if sample else None,
        "matrix": cells,
    }


def test_old_report_without_phases_is_none_not_zero_accuracy(tmp_path: Path) -> None:
    """🔴 검증 칸이 없던 회차를 "정확도 0%" 로 보여주면 없는 결함을 본 것이 된다."""
    write(tmp_path, "run_old")
    summary = RunStore(tmp_path).list_runs()[0]

    assert summary.phases is None
    assert summary.phase_accuracy is None
    assert summary.phase_sample == 0
    assert aggregate_phases([summary]) is None


def test_phase_summary_exposes_accuracy_and_persistence(tmp_path: Path) -> None:
    write(
        tmp_path,
        "run_new",
        phases=phases(4, 1, 3, [{"judged": "up", "realized": "down", "count": 4}]),
    )
    summary = RunStore(tmp_path).list_runs()[0]

    assert summary.phase_sample == 4
    assert summary.phase_accuracy == Decimal("0.25")
    assert summary.phase_persistence == Decimal("0.75")


def test_aggregate_sums_cells_across_runs(tmp_path: Path) -> None:
    """합산은 서버가 한다 — 화면에서 더하면 터미널 출력과 두 벌이 된다."""
    write(
        tmp_path,
        "run_a",
        phases=phases(
            3,
            2,
            2,
            [
                {"judged": "up", "realized": "up", "count": 2},
                {"judged": "up", "realized": "down", "count": 1},
            ],
        ),
    )
    write(
        tmp_path,
        "run_b",
        phases=phases(2, 0, 1, [{"judged": "up", "realized": "down", "count": 2}]),
    )

    merged = aggregate_phases(RunStore(tmp_path).list_runs())

    assert merged is not None
    assert merged["sample"] == 5
    assert merged["correct"] == 2
    assert merged["persisted"] == 3
    assert merged["accuracy"] == "0.4"
    assert merged["matrix"] == [
        {"judged": "up", "realized": "down", "count": 3},
        {"judged": "up", "realized": "up", "count": 2},
    ]


def test_aggregate_ignores_runs_that_never_measured(tmp_path: Path) -> None:
    """옛 회차가 섞여 있어도 **분모를 늘리지 않는다** — 안 잰 것은 틀린 것이 아니다."""
    write(tmp_path, "run_old")
    write(
        tmp_path,
        "run_new",
        phases=phases(
            2,
            1,
            2,
            [
                {"judged": "down", "realized": "down", "count": 1},
                {"judged": "down", "realized": "up", "count": 1},
            ],
        ),
    )

    merged = aggregate_phases(RunStore(tmp_path).list_runs())

    assert merged is not None
    assert merged["sample"] == 2
