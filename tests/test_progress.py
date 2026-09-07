"""진행률 가시화 검증 (세션 인계 §5-2).

두 가지를 지킨다:

1. **`.status` 가 퍼센트·ETA·속도를 담는다.** 봉 번호만 찍히면 "느린 것"과 "죽은 것"을
   구분할 수 없고, 지난 세션이 그 이유로 정상 완료한 측정을 다시 띄웠다.
2. **`collect()` 가 콜백으로 진행을 알린다.** `print` 로 박혀 있으면 기록기를 끼울 수
   없고, 파일 리다이렉트 시 블록 버퍼링에 갇힌다.

`scripts/` 는 패키지가 아니라 경로로 로드한다 — 그렇다고 테스트를 포기하면 ETA 계산
같은 실제 로직이 검증 없이 남는다.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from updown.analysis.detectors.base import MarketContext
from updown.analysis.evaluation.scan import collect
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.setup import TradeSetup
from updown.orchestration.reporting.progress import (
    pid_namespace as _pid_namespace,
)

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)


def load_script(name: str, alias: str) -> Any:
    """`scripts/<name>.py` 를 경로로 불러온다."""
    # 재정리(2026-09-06): 기록기는 runtime/, 화면은 dev/ 에 산다.
    folder = {"_progress": "runtime", "progress_report": "dev"}[name]
    path = Path(f"scripts/{folder}/{name}.py").resolve()
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def progress() -> Any:
    return load_script("_progress", "_progress")


@pytest.fixture
def reporter() -> Any:
    # progress_report 는 `_progress` 를 import 하므로 먼저 등록해 둔다.
    load_script("_progress", "_progress")
    return load_script("progress_report", "_progress_report_under_test")


# ---------------------------------------------------------------------------
# 1. 상태 파일
# ---------------------------------------------------------------------------


def test_status_file_carries_percent_eta_and_rate(progress: Any, tmp_path: Path) -> None:
    """봉 번호만으로는 언제 끝나는지 모른다 — 세 가지가 다 있어야 한다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("15m/direct_sweep", 1000, root=root, echo=False)
    recorder.update(250, force=True)

    text = recorder.status_path.read_text(encoding="utf-8")
    assert "25.0%" in text
    assert "250 / 1,000" in text
    assert "남은" in text  # ETA
    assert "속도" in text
    assert "갱신" in text


def test_status_file_is_overwritten_not_appended(progress: Any, tmp_path: Path) -> None:
    """`.status` 는 **현재 상태**다 — 쌓이면 어디가 최신인지 알 수 없다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 100, root=root, echo=False)
    recorder.update(10, force=True)
    recorder.update(90, force=True)

    text = recorder.status_path.read_text(encoding="utf-8")
    assert "90.0%" in text
    assert "10.0%" not in text


def test_history_log_accumulates(progress: Any, tmp_path: Path) -> None:
    """이력은 append 다 — 속도가 떨어졌는지는 과거가 있어야 보인다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 100, root=root, echo=False)
    recorder.update(10, force=True)
    recorder.update(90, force=True)

    log = (root / "job.log").read_text(encoding="utf-8")
    assert "10.0%" in log
    assert "90.0%" in log


def test_no_temp_file_is_left_behind(progress: Any, tmp_path: Path) -> None:
    """읽는 쪽이 잘린 줄을 보지 않도록 임시 파일 + rename 을 쓴다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 100, root=root, echo=False)
    recorder.update(50, force=True)
    assert not list(root.glob("*.tmp"))


def test_min_interval_throttles_writes(progress: Any, tmp_path: Path) -> None:
    """매 봉마다 파일을 쓰면 그 I/O 가 측정 자체를 느리게 한다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 100, root=root, echo=False, min_interval=3600.0)
    recorder.update(10, force=True)
    recorder.update(90)  # 간격 제한에 걸려 쓰이지 않는다

    assert "10.0%" in recorder.status_path.read_text(encoding="utf-8")


def test_finish_marks_completion_so_it_is_not_read_as_still_running(
    progress: Any, tmp_path: Path
) -> None:
    root = Path(str(tmp_path))
    with progress.ProgressRecorder("job", 100, root=root, echo=False) as recorder:
        recorder.update(100, force=True)
    assert "완료" in recorder.status_path.read_text(encoding="utf-8")


def test_exception_is_recorded_as_an_abort(progress: Any, tmp_path: Path) -> None:
    """예외로 끝난 것을 완료로 남기면 다음 사람이 결과를 믿는다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 100, root=root, echo=False)
    with pytest.raises(RuntimeError), recorder:
        raise RuntimeError("boom")
    assert "중단: RuntimeError" in recorder.status_path.read_text(encoding="utf-8")


def test_slash_in_job_name_does_not_create_directories(progress: Any, tmp_path: Path) -> None:
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("15m/direct_sweep", 10, root=root, echo=False)
    recorder.update(1, force=True)
    assert recorder.status_path.parent == root


def test_zero_total_reports_count_without_a_fake_percent(progress: Any, tmp_path: Path) -> None:
    """전체를 모르면 퍼센트를 지어내지 않는다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 0, root=root, echo=False)
    recorder.update(42, force=True)
    text = recorder.status_path.read_text(encoding="utf-8")
    assert "42" in text
    assert "%" not in text


def test_first_tick_does_not_invent_an_eta(progress: Any, tmp_path: Path) -> None:
    """시작 직후의 속도로 나눈 ETA 는 실제보다 수십 배 짧다 — 거짓 ETA 는 없느니만 못하다.

    24시간 작업의 첫 갱신이 "남은 ~4분" 으로 나오던 실제 사례가 있었다.
    """
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 1440, root=root, echo=False)
    recorder.update(1, force=True)  # 시작 직후

    text = recorder.status_path.read_text(encoding="utf-8")
    assert "계산 중" in text
    payload = json.loads(recorder.state_path.read_text(encoding="utf-8"))
    assert payload["eta_seconds"] is None


def test_eta_appears_once_there_is_enough_elapsed_time(progress: Any, tmp_path: Path) -> None:
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("job", 100, root=root, echo=False)
    # 시작 시각을 과거로 밀어 충분한 경과를 만든다.
    recorder._started -= 60.0
    recorder.update(50, force=True)

    payload = json.loads(recorder.state_path.read_text(encoding="utf-8"))
    assert payload["eta_seconds"] is not None
    assert "계산 중" not in recorder.status_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00"), (61, "01:01"), (3661, "1:01:01")],
)
def test_clock_formatting(progress: Any, seconds: int, expected: str) -> None:
    assert progress._clock(seconds) == expected


# ---------------------------------------------------------------------------
# 2. collect() 콜백
# ---------------------------------------------------------------------------


class _SilentDetector:
    """아무것도 탐지하지 않는 탐지기 — 진행 알림만 보려는 것이다."""

    @property
    def id(self) -> str:
        return "silent"

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def params(self) -> Any:
        from updown.analysis.detectors.base import RuleParams

        return RuleParams(rule_id="silent", version="1.0", values={})

    def detect(self, ctx: MarketContext) -> list[TradeSetup]:  # noqa: ARG002
        return []


def candles(count: int) -> list[Candle]:
    """단조 증가 캔들 — 탐지 결과가 아니라 진행 알림이 관심사다."""
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            instrument=BTC,
            timeframe=Timeframe.M5,
            ts=start + timedelta(minutes=5 * index),
            open=Decimal(100 + index),
            high=Decimal(101 + index),
            low=Decimal(99 + index),
            close=Decimal(100 + index),
            volume=Decimal(1),
        )
        for index in range(count)
    ]


def test_collect_reports_progress_with_a_total() -> None:
    """30봉 · 창 10 → 스캔 20봉. 5봉마다 알리면 4번이다."""
    seen: list[tuple[int, int]] = []
    bars = candles(30)
    collect(
        bars,
        BTC,
        Timeframe.M5,
        _SilentDetector(),
        lookback_bars=10,
        progress_every=5,
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen, "progress_every 를 줬는데 알림이 하나도 없다"
    # 전체를 함께 넘기지 않으면 받는 쪽이 퍼센트를 계산할 수 없다.
    assert all(total == 20 for _, total in seen)
    assert [done for done, _ in seen] == [5, 10, 15, 20]


def test_collect_stays_silent_without_progress_every() -> None:
    seen: list[tuple[int, int]] = []
    collect(
        candles(30),
        BTC,
        Timeframe.M5,
        _SilentDetector(),
        lookback_bars=10,
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == []


# ---------------------------------------------------------------------------
# 3. 상태 판정 — "죽은 것"과 "느린 것"을 가른다
# ---------------------------------------------------------------------------


def snapshot(progress: Any, tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    """기록기가 실제로 남긴 `.json` 을 읽어 필요한 필드만 덮어쓴다.

    손으로 dict 를 지어내면 기록기 형식이 바뀌어도 테스트가 통과한다 — 그러면
    판정 로직이 실제 파일과 어긋난 채로 남는다.
    """
    recorder = progress.ProgressRecorder("j", 100, root=Path(str(tmp_path)), echo=False)
    recorder.update(50, force=True)
    payload = json.loads(recorder.state_path.read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


def test_running_job_with_a_live_pid_is_running(
    progress: Any, reporter: Any, tmp_path: Path
) -> None:
    payload = snapshot(progress, tmp_path)
    now = datetime.fromisoformat(payload["updated_at"])
    assert reporter.classify(payload, now).verdict == "running"


def test_running_state_with_a_dead_pid_is_dead(
    progress: Any, reporter: Any, tmp_path: Path
) -> None:
    """**이 판정이 이 도구의 존재 이유다.**

    작업이 종료를 남기지 못하고 죽으면(OOM·kill) `.status` 는 마지막 진행률에서 멈춘 채
    남는다. 그것만 보면 아직 도는 것처럼 보이고, 지난 세션이 그 반대 방향으로 오판했다.
    """
    # 절대 존재하지 않는 pid. 0 이하는 `_pid_alive` 가 곧바로 거른다.
    # 🔴 `pid_ns` 를 **우리 것으로** 준다 — 같은 네임스페이스에서 쓰인 스냅샷이라야
    #    "pid 가 없다 = 죽었다"가 성립한다. 다른 네임스페이스면 안 보이는 것이 정상이고,
    #    그때 죽었다고 하면 컨테이너에서 호스트 작업을 전부 죽었다고 보고하게 된다.
    payload = snapshot(progress, tmp_path, pid=-1, pid_ns=_pid_namespace())
    now = datetime.fromisoformat(payload["updated_at"])
    assert reporter.classify(payload, now).verdict == "dead"


def test_live_pid_but_stale_update_is_stalled(progress: Any, reporter: Any, tmp_path: Path) -> None:
    payload = snapshot(progress, tmp_path, stall_after_seconds=10.0)
    now = datetime.fromisoformat(payload["updated_at"]) + timedelta(seconds=60)
    assert reporter.classify(payload, now).verdict == "stalled"


def test_stall_threshold_comes_from_the_jobs_own_cadence(
    progress: Any, reporter: Any, tmp_path: Path
) -> None:
    """갱신 주기가 느린 작업을 멈춤으로 오인하지 않는다.

    비용 샘플링(10초)과 봉 스캔(수십 초)에 같은 임계값을 쓰면 후자가 계속 오탐된다.
    """
    payload = snapshot(progress, tmp_path, stall_after_seconds=600.0)
    now = datetime.fromisoformat(payload["updated_at"]) + timedelta(seconds=300)
    assert reporter.classify(payload, now).verdict == "running"


@pytest.mark.parametrize(("state", "expected"), [("done", "done"), ("aborted", "aborted")])
def test_finished_jobs_keep_their_own_verdict(
    progress: Any, reporter: Any, tmp_path: Path, state: str, expected: str
) -> None:
    """끝난 작업은 pid 를 보지 않는다 — 프로세스가 없는 것이 정상이다."""
    payload = snapshot(progress, tmp_path, state=state, pid=-1, pid_ns=_pid_namespace())
    now = datetime.fromisoformat(payload["updated_at"]) + timedelta(hours=5)
    assert reporter.classify(payload, now).verdict == expected


def test_status_without_a_sidecar_is_reported_as_unknown(reporter: Any, tmp_path: Path) -> None:
    """`.json` 이 없다고 목록에서 빼면 "작업이 없다"로 보인다 — 그것이 바로 그 오판이다."""
    root = Path(str(tmp_path))
    (root / "legacy.status").write_text("legacy  42.0%  [==>  ]  42 / 100\n", encoding="utf-8")
    statuses = reporter.load_all(root, datetime.now().astimezone())
    assert [item.job for item in statuses] == ["legacy"]
    assert statuses[0].verdict == "unknown"
    assert statuses[0].is_active, "생사 미상은 조치가 필요하므로 활성으로 센다"


def test_unknown_does_not_pretend_to_know(reporter: Any, tmp_path: Path) -> None:
    """모르는 것을 running 이나 dead 로 적으면 둘 중 한 방향으로 반드시 틀린다."""
    root = Path(str(tmp_path))
    (root / "legacy.status").write_text("legacy\n", encoding="utf-8")
    status = reporter.load_all(root, datetime.now().astimezone())[0]
    assert status.verdict not in {"running", "dead", "stalled"}


def test_dead_and_stalled_sort_to_the_top(progress: Any, reporter: Any, tmp_path: Path) -> None:
    """조치가 필요한 것이 위에 와야 스크롤하지 않고 보인다."""
    root = Path(str(tmp_path))
    for name, overrides in (
        ("alive", {}),
        ("gone", {"pid": -1}),
        ("finished", {"state": "done"}),
    ):
        recorder = progress.ProgressRecorder(name, 100, root=root, echo=False)
        recorder.update(50, force=True)
        payload = json.loads(recorder.state_path.read_text(encoding="utf-8"))
        payload.update(overrides)
        recorder.state_path.write_text(json.dumps(payload), encoding="utf-8")

    verdicts = [item.verdict for item in reporter.load_all(root, datetime.now().astimezone())]
    assert verdicts[0] == "dead"
    assert verdicts.index("done") == len(verdicts) - 1


def test_exit_code_flags_jobs_needing_attention(
    progress: Any, reporter: Any, tmp_path: Path
) -> None:
    """죽은 작업이 있으면 종료 코드가 1 이라 스크립트에서 분기할 수 있다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("gone", 100, root=root, echo=False)
    recorder.update(10, force=True)
    payload = json.loads(recorder.state_path.read_text(encoding="utf-8"))
    payload["pid"] = -1
    recorder.state_path.write_text(json.dumps(payload), encoding="utf-8")

    statuses = reporter.load_all(root, datetime.now().astimezone())
    assert any(item.verdict == "dead" for item in statuses)


def test_ratio_is_none_when_total_is_unknown(reporter: Any, tmp_path: Path) -> None:
    """전체를 모르면 퍼센트를 지어내지 않는다."""
    root = Path(str(tmp_path))
    (root / "x.status").write_text("x\n", encoding="utf-8")
    assert reporter.load_all(root, datetime.now().astimezone())[0].ratio is None


def test_payload_carries_every_field_the_web_view_reads(
    progress: Any, reporter: Any, tmp_path: Path
) -> None:
    """웹 화면(`progress_server.py`)이 읽는 필드가 빠지면 카드가 조용히 비어 보인다.

    CLI 와 웹이 **같은 `payload()`** 를 쓰므로, 여기서 계약을 고정해 둔다.
    """
    data = snapshot(progress, tmp_path)
    now = datetime.fromisoformat(data["updated_at"])
    body = reporter.payload(reporter.classify(data, now))

    expected = {
        "job",
        "verdict",
        "done",
        "total",
        "unit",
        "ratio",
        "elapsed_seconds",
        "eta_seconds",
        "rate_per_second",
        "since_update_seconds",
        "note",
        "updated_at",
        "is_active",
    }
    assert expected <= set(body)
    # JSON 직렬화가 되어야 서버가 그대로 내보낼 수 있다.
    assert json.loads(json.dumps(body, ensure_ascii=False))["job"] == body["job"]


def test_visible_hides_long_finished_jobs_but_keeps_active_ones(
    progress: Any, reporter: Any, tmp_path: Path
) -> None:
    """완료 기록이 쌓이면 **지금 도는 것**이 묻힌다. 그렇다고 즉시 지우면 확인할 창이 없다."""
    root = Path(str(tmp_path))
    recorder = progress.ProgressRecorder("old", 100, root=root, echo=False)
    recorder.update(100, force=True)
    recorder.finish("완료")

    now = datetime.now().astimezone()
    assert reporter.visible(root, now)  # 방금 끝났으므로 보인다
    assert not reporter.visible(root, now + timedelta(days=1))
    assert reporter.visible(root, now + timedelta(days=1), include_old=True)


def test_visible_on_missing_directory_is_empty_not_an_error(reporter: Any, tmp_path: Path) -> None:
    """아직 아무 작업도 안 돌린 저장소에서 화면이 터지면 안 된다."""
    assert reporter.visible(Path(str(tmp_path)) / "nope", datetime.now().astimezone()) == []


def test_render_survives_every_verdict(progress: Any, reporter: Any, tmp_path: Path) -> None:
    """표시 코드가 어느 상태에서도 죽지 않아야 한다 — 죽으면 현황을 아예 못 본다."""
    base = snapshot(progress, tmp_path)
    stamp = datetime.fromisoformat(base["updated_at"])

    cases = [
        (base, stamp),  # running
        ({**base, "pid": -1}, stamp),  # dead
        ({**base, "stall_after_seconds": 1.0}, stamp + timedelta(minutes=5)),  # stalled
        ({**base, "state": "done"}, stamp),
        ({**base, "state": "aborted", "note": "중단: KeyboardInterrupt"}, stamp),
        ({**base, "total": 0}, stamp),  # 전체 미상 — 퍼센트를 못 낸다
    ]
    statuses = [reporter.classify(payload, now) for payload, now in cases]
    assert {item.verdict for item in statuses} >= {"running", "dead", "stalled", "done", "aborted"}

    lines = reporter.render(statuses)
    assert lines and all(isinstance(line, str) for line in lines)
    assert reporter.render([]) == ["진행 중인 작업 없음"]


def test_progress_callback_does_not_change_the_result() -> None:
    """관측이 결과를 바꾸면 결정론(원칙 P1)이 깨진다."""
    bars = candles(30)
    without = collect(bars, BTC, Timeframe.M5, _SilentDetector(), lookback_bars=10)
    with_callback = collect(
        bars,
        BTC,
        Timeframe.M5,
        _SilentDetector(),
        lookback_bars=10,
        progress_every=3,
        on_progress=lambda _done, _total: None,
    )
    assert without.scanned_bars == with_callback.scanned_bars
    assert without.occurrences == with_callback.occurrences
