"""백테스트 조회 라우트 (Phase 5 §5-9 A2).

## 읽기 전용이다

측정을 **띄우는** 엔드포인트는 두지 않는다. 매트릭스는 12워커에 수 시간을 쓰고, 사용자
허가를 받고 띄우는 물건이다 — HTTP 요청 하나로 그것이 시작될 수 있으면 실수 한 번에
서버가 몇 시간 묶인다. 실행은 계속 `scripts/run_matrix.py` 가 한다.

## 봉인 구간을 숨기지 않고 **표시**한다

리포트에는 어느 구간을 쟀는지가 들어 있다. 화면에서 그것을 지우면 in-sample 결과를
out-of-sample 로 착각하기 쉬워진다 — 지우는 대신 라벨을 붙인다 (절대 규칙 #8 의 정신).
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from updown.common.paths import describe as describe_logs
from updown.orchestration.reporting import ReportNotFoundError, RunStore, aggregate_phases
from updown.orchestration.reporting.charts import list_charts, read_chart
from updown.orchestration.reporting.pool import (
    MIN_SAMPLE,
    group_by_preset,
    pool_bypass,
    pool_cells,
    pool_contributions,
    pool_funnel,
)
from updown.orchestration.reporting.progress import (
    PROGRESS_ROOT,
    forget,
    payload,
    visible,
)

HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _summary_dict(summary: Any) -> dict[str, Any]:
    """요약을 JSON 으로.

    Args:
        summary: `RunSummary`.

    Returns:
        직렬화용 dict. 비율·가격은 **문자열**이다 — float 으로 내리면 소수가 조용히
        바뀌고, 프로젝트 전역이 `Decimal` 인 이유가 그것이다.
    """
    win_rate = summary.win_rate
    return {
        "run_id": summary.run_id,
        "preset": summary.preset,
        "symbol": summary.symbol,
        "display": summary.display,
        "timeframe": summary.timeframe,
        "setup": summary.setup,
        "detected": summary.detected,
        "entered": summary.entered,
        "followed": summary.followed,
        "expired": summary.expired,
        "win_rate": str(win_rate) if win_rate is not None else None,
        "first_rr_median": (
            str(summary.first_rr_median) if summary.first_rr_median is not None else None
        ),
        "stop_pct_median": (
            str(summary.stop_pct_median) if summary.stop_pct_median is not None else None
        ),
        # 🔴 이 칸이 참이면 나머지 수치를 읽으면 안 된다 (§1-0s RR 1.00 결함).
        #    화면이 경고를 띄울 수 있게 요약에 싣는다.
        "degenerate": summary.degenerate,
        # 🔴 국면 **라벨이 맞았는지**. 이 값이 낮으면 국면별 성과표의 모든 줄이
        #    오배정이므로, 성과와 **같은 화면**에 있어야 한다.
        "phase_sample": summary.phase_sample,
        "phase_accuracy": (
            str(summary.phase_accuracy) if summary.phase_accuracy is not None else None
        ),
        "phase_persistence": (
            str(summary.phase_persistence) if summary.phase_persistence is not None else None
        ),
    }


@router.get("/tags")
async def list_tags() -> dict[str, Any]:
    """존재하는 회차 태그 목록.

    Returns:
        `{"tags": ["v2", "v3", ...]}`.
    """
    return {"tags": RunStore().tags()}


@router.get("/runs")
async def list_runs(tag: str | None = Query(default=None)) -> dict[str, Any]:
    """실행 목록.

    Args:
        tag: 회차 태그. 없으면 전부.

    Returns:
        요약 목록과 회차 전체의 국면 검증 합산. 측정을 아직 안 돌렸으면 **빈 목록**이다
        (오류가 아니다).

    Note:
        `phases` 합산을 여기서 계산하지 않고 `aggregate_phases` 를 부른다 — 화면이
        더하면 터미널 출력과 두 벌이 된다.
    """
    runs = RunStore().list_runs(tag)
    return {
        "tag": tag,
        "count": len(runs),
        "runs": [_summary_dict(item) for item in runs],
        # 검증 칸을 가진 실행이 없으면 None — 옛 회차를 "정확도 0%" 로 보이게 하지 않는다.
        "phases": aggregate_phases(runs),
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """리포트 전문.

    Args:
        run_id: 실행 식별자.

    Returns:
        측정 리포트 JSON 그대로.

    Raises:
        HTTPException: 리포트가 없거나 읽을 수 없는 경우 404.

    Note:
        요약이 아니라 **전문**을 준다. 화면이 새 칸을 원할 때마다 서버를 고쳐야 한다면
        그 둘은 결국 갈라진다.
    """
    try:
        return RunStore().load_run(run_id)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=str(exc)) from exc


def _pool_dict(preset: str, reports: list[Any]) -> dict[str, Any]:
    """프리셋 하나의 합계를 JSON 으로.

    Args:
        preset: 프리셋 이름.
        reports: 그 프리셋의 리포트들.

    Returns:
        직렬화용 dict. 비율은 **문자열**이다 (`_summary_dict` 와 같은 이유).
    """
    funnel = pool_funnel(reports)
    bypass = pool_bypass(reports)
    conversion = funnel.conversion

    def cells(key: str) -> list[dict[str, Any]]:
        """칸 표 하나를 직렬화한다.

        Args:
            key: 표 이름.

        Returns:
            칸마다 표본·승수·승률·기대값·판정 가능 여부. Decimal 은 문자열로.
        """
        return [
            {
                "label": label,
                "sample": pool.sample,
                "wins": pool.wins,
                "win_rate": str(pool.win_rate),
                "expectancy": str(pool.expectancy),
                "judgeable": pool.judgeable,
            }
            for label, pool in sorted(pool_cells(reports, key, "label").items())
        ]

    return {
        "preset": preset,
        "runs": len(reports),
        "symbols": sorted({str(item.get("symbol", "")) for item in reports}),
        "timeframes": sorted({str(item.get("timeframe", "")) for item in reports}),
        "funnel": {
            **funnel.totals,
            "conversion": None if conversion is None else str(conversion),
            # 🔴 옛 리포트의 0 을 그냥 더하면 "탈락이 없었다"로 읽힌다. 빠진 건수를
            #    함께 실어야 합계를 믿을지 판단할 수 있다.
            "missing": funnel.missing,
        },
        # 깊이 기록이 없으면 None — 0 으로 채우면 "우회가 없었다"로 읽힌다.
        "bypass": (
            None
            if bypass is None
            else {
                **{key: str(value) for key, value in bypass.rates.items()},
                "entries": bypass.entries,
                "measured": str(bypass.measured),
                # 거짓이면 깊이 표를 축 K 의 증거로 읽으면 안 된다 (§1-0t T9).
                "interpretable": bypass.interpretable,
            }
        ),
        "depths": cells("confluence"),
        "regimes": cells("regimes"),
        "contributions": [
            {
                "flag": arm.flag,
                "win_rate_lift": str(arm.win_rate_lift),
                "expectancy_lift": str(arm.expectancy_lift),
                "with_sample": arm.with_flag.sample,
                "without_sample": arm.without_flag.sample,
                "judgeable": arm.shortfall is None,
                # 🔴 "기다리면 풀린다"와 "룰을 고쳐야 한다"를 가르는 칸이다.
                "shortfall": arm.shortfall,
            }
            for arm in pool_contributions(reports)
        ],
    }


@router.get("/pool")
async def pool(tag: str | None = Query(default=None)) -> dict[str, Any]:
    """프리셋별 합계 + §12.9 판정 (§5-9 A2).

    Args:
        tag: 회차 태그. 없으면 전부.

    Returns:
        프리셋별 합계 목록.

    Note:
        🔴 **프리셋 단위로만 합친다.** 프리셋이 다르면 다른 규칙이고, 합치면 축이
        사라진다 (§5.6.7). 종목·시간축은 합쳐도 되지만 축 후보와 회차는 안 된다.

        집계는 `orchestration.reporting.pool` 이 한다 — 터미널(`pool_results.py`)과
        같은 코드를 부르므로 두 화면이 갈라지지 않는다.
    """
    reports = [summary.raw for summary in RunStore().list_runs(tag)]
    groups = group_by_preset(reports)
    return {
        "tag": tag,
        "min_sample": MIN_SAMPLE,
        "pools": [_pool_dict(preset, groups[preset]) for preset in sorted(groups)],
    }


@router.get("/charts")
async def chart_index() -> dict[str, Any]:
    """매매 차트가 있는 실행 목록.

    Returns:
        `{"charts": [{run_id, display, timeframe, preset, trades}, ...]}`.
        `--export-charts` 를 안 준 회차는 목록에 없다 (오류가 아니다).

    Note:
        거래 배열은 빼고 **개수만** 싣는다 — 한 거래가 400봉이라 다 실으면 수 MB 다.
    """
    return {"charts": list_charts()}


@router.get("/charts/{run_id}")
async def chart_of(run_id: str) -> Response:
    """거래별 오버레이 좌표 전문 (§5-9 A2).

    Args:
        run_id: 실행 식별자.

    Returns:
        차트 JSON **원본 바이트**. 파싱해서 다시 직렬화하면 `Decimal` 문자열이 상할
        수 있고, 수 MB 를 두 번 옮길 이유도 없다.

    Raises:
        HTTPException: 파일이 없거나 이름이 허용 범위를 벗어나면 404.

    Note:
        🔴 이름을 그대로 경로로 쓰지 않는다 — `charts.inside` 가 경로 탈출을 막는다.
    """
    raw = read_chart(run_id)
    if raw is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=f"{run_id} 차트가 없다")
    return Response(content=raw, media_type="application/json")


@router.delete("/progress/{job}")
async def forget_job(job: str) -> dict[str, Any]:
    """끝난 작업의 **진행률 기록**을 지운다 (§5-13).

    Args:
        job: 작업 이름.

    Returns:
        `{"job": ..., "removed": N}`.

    Raises:
        HTTPException: 아직 도는 작업이거나(409) 기록이 없으면(404).

    Note:
        🔴 **측정 결과가 아니라 관측 기록**을 지운다. `logs/results/` 의 리포트는
        건드리지 않는다 — 둘을 한 경로로 합치면 "카드만 치우려다 결과를 날리는" 사고가
        난다 (`progress_server.delete_job` 과 같은 판단).

        ⛔ 도는 작업은 거부한다. 지워도 프로세스가 파일을 다시 만들어 "지워지지
        않는다"로 보인다.

        ⚠️ **띄우는 엔드포인트는 없다** (모듈 docstring). 회차 파라미터는
        `scripts/run_vNN.sh` 에만 있으므로 HTTP 로 재구성하지 않는다.
    """
    now = datetime.now(UTC).astimezone()
    for item in visible(PROGRESS_ROOT, now, include_old=True):
        if item.job == job and item.verdict in {"running", "stalled"}:
            raise HTTPException(
                status_code=HTTP_CONFLICT,
                detail=f"{job} 은 아직 돌고 있다 ({item.verdict}) — 끝난 뒤에 지운다",
            )
    removed = forget(PROGRESS_ROOT, job)
    if removed == 0:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=f"{job} 의 기록이 없다")
    return {"job": job, "removed": removed}


@router.get("/progress")
async def progress(include_old: bool = False) -> dict[str, Any]:
    """지금 도는 장시간 작업들 — 매트릭스·백필·테스트 (`make progress` 와 같은 판정).

    Args:
        include_old: 오래 전에 끝난 작업까지 포함할지.

    Returns:
        작업별 진행률·ETA·판정과 상태별 집계.

    Note:
        🔴 **판정 로직을 여기서 다시 쓰지 않는다.** `orchestration.reporting.progress` 를
        그대로 부른다 — 터미널(`make progress`)·진행 웹 화면·이 라우트가 각자 판정하면
        "터미널은 죽었다는데 화면은 돌고 있다"가 생기고, 그때 무엇을 믿을지 알 수 없다.

        ⚠️ `stalled`(멈춤 의심)는 **정지의 증거가 아니다.** 진행 콜백을 부르지 않는 CPU
        구간이 길면 멀쩡한 작업도 그렇게 보인다 (실측: 준비 단계가 21분간 무보고였다).
        진짜 사망은 pid 가 없는 `dead` 다.
    """
    now = datetime.now(UTC).astimezone()
    statuses = visible(PROGRESS_ROOT, now, include_old=include_old)
    counts: dict[str, int] = {}
    for item in statuses:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    return {
        "generated_at": now.isoformat(),
        "counts": counts,
        "jobs": [payload(item) for item in statuses],
        # 🔴 **어디를 보고 있는지 항상 말한다.**
        #
        #    호스트에서 도는 매트릭스를 컨테이너 API 가 못 보던 사고가 있었다
        #    (각자 다른 `logs/`). 화면은 정상적으로 "작업 없음"을 그렸고, 그래서
        #    **아무도 이상하다고 느끼지 못했다.** 경로와 개수를 늘 함께 내면
        #    "여긴 0건인데 저긴 120건"이 한눈에 보인다 (`common/paths.describe`).
        "source": describe_logs(),
    }
