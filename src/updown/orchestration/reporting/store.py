"""측정 리포트 저장소 — `logs/results/*.json` 을 **데이터로** 읽는다.

## 원본을 그대로 돌려주는 칸이 하나 있다

`RunSummary.raw` 는 리포트 JSON 전문이다. 요약만 주면 화면이 새 칸을 원할 때마다 여기를
고쳐야 하고, 그때마다 스크립트와 API 가 갈라진다. **요약은 편의고 전문이 계약이다.**

## ⛔ 여기서 판정하지 않는다

"§12.9 를 넘었나", "축을 세울까"는 이 계층의 일이 아니다. 표본 수를 싣고 끝낸다 —
판정 규칙은 각 축 문서와 `pool_excursions.py` 가 **측정 전에 선언**한 것을 쓴다
(§5.6.2 자동조율 금지).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from updown.common.paths import under

type Report = dict[str, Any]
"""리포트 JSON 한 건.

`Any` 를 쓰는 이유: 리포트는 **측정 스크립트가 소유한 스키마**이고 여기는 읽기만 한다.
여기서 타입을 다시 선언하면 두 벌이 되고, 칸이 늘 때마다 갈라진다 — 스키마의 SSoT 는
`orchestration/backtest/report.py` 하나여야 한다.
"""

DEFAULT_RESULTS_DIR = under("results")

#: 프리셋 접두사 → 사람이 읽는 셋업 이름.
#:
#: 🔴 `measure_run` 이 파일명에 심는 문자열과 **같은 표**여야 한다. 어긋나면 화면이
#:    셋업을 잘못 붙이는데, 숫자는 멀쩡해 보여서 아무도 모른다 (절대 규칙 #8).
SETUP_LABELS: dict[str, str] = {
    "breakout": "돌파",
    "matrend": "추세 눌림목",
    "order": "눌림목",
}


class ReportNotFoundError(FileNotFoundError):
    """요청한 리포트가 없다."""


@dataclass(frozen=True, slots=True)
class RunSummary:
    """실행 하나의 요약 — 목록·비교표가 쓰는 최소 칸.

    Attributes:
        run_id: 실행 식별자 (= 파일명 stem).
        preset: 프리셋 문자열. 회차 태그와 축 조합이 여기 들어 있다.
        symbol: 종목 코드. `display`: 사람이 읽는 이름.
        timeframe: 진입 시간축.
        setup: 셋업 이름 (`SETUP_LABELS` 로 옮긴 값).
        entered: 실현 거래 수 (= `outcomes.realized`).
        followed: 익절 수. `expired`: 만료 청산 수.
        detected: 탐지 수. 진입과 함께 봐야 전환율이 뜻을 갖는다.
        first_rr_median: 계획 1차 RR 중앙값. 없으면 None.
        stop_pct_median: 계획 손절폭 중앙값(비율). 없으면 None.
        degenerate: 계획 분포가 **상수로 붕괴**했는가 (§1-0s). 이 칸이 참이면 나머지
            수치를 읽으면 안 된다 — 전략이 아니라 코드가 만든 값이다.
        phases: 국면 판정 검증 (`regime_check`). 옛 리포트에는 없어 None 이다.
        raw: 리포트 JSON 전문.

    Note:
        기대 R 을 요약에 넣지 않았다. 깊이별 가중합이라 계산 규칙이 있는 값이고, 그것을
        여기서 굳히면 화면과 스크립트가 다른 정의를 쓰게 된다. `raw` 에서 필요할 때
        계산한다.
    """

    run_id: str
    preset: str
    symbol: str
    display: str
    timeframe: str
    setup: str
    entered: int
    followed: int
    expired: int
    detected: int
    first_rr_median: Decimal | None
    stop_pct_median: Decimal | None
    degenerate: bool
    phases: Report | None
    raw: Report

    @property
    def phase_accuracy(self) -> Decimal | None:
        """국면 판정 정확도. 검증 칸이 없거나 표본이 0 이면 None.

        Note:
            0 을 돌려주지 않는다 — 옛 리포트에서 "0% 정확"으로 읽히면 없는 결함을
            본 것이 된다 (`_phases_dict` 와 같은 이유).
        """
        return _ratio(self.phases, "accuracy")

    @property
    def phase_persistence(self) -> Decimal | None:
        """국면 지속률 (진입 판정이 청산까지 유지된 비율)."""
        return _ratio(self.phases, "persistence")

    @property
    def phase_sample(self) -> int:
        """국면 검증 표본. 칸이 없으면 0 이다."""
        return int(self.phases.get("sample", 0)) if self.phases else 0

    @property
    def win_rate(self) -> Decimal | None:
        """익절률. 진입이 없으면 None — 0% 와 "거래가 없었다"는 다르다."""
        if not self.entered:
            return None
        return Decimal(self.followed) / Decimal(self.entered)


def _setup_of(preset: str) -> str:
    """프리셋에서 셋업 이름을 읽는다.

    Args:
        preset: 프리셋 문자열.

    Returns:
        사람이 읽는 이름. 표에 없으면 프리셋 첫 토큰을 그대로 (조용히 한 칸으로 몰지
        않는다 — 새 셋업이 생겼는데 옛 이름으로 섞이면 비교가 거짓말한다).
    """
    for needle, label in SETUP_LABELS.items():
        if needle in preset:
            return label
    return preset.split("_", 1)[0]


def _ratio(section: Report | None, key: str) -> Decimal | None:
    """비율 칸 하나를 `Decimal` 로 꺼낸다.

    Args:
        section: `phases` 같은 묶음. 없으면 None.
        key: 칸 이름.

    Returns:
        값. 칸이 없거나 `null` 이면 None — 표본 0 을 0% 로 바꾸지 않는다.
    """
    if not section:
        return None
    value = section.get(key)
    return Decimal(str(value)) if value is not None else None


def _median(section: Report | None, key: str) -> Decimal | None:
    """분포 요약에서 중앙값을 꺼낸다.

    Args:
        section: `plans` 같은 분포 묶음. 없으면 None.
        key: 분포 이름 (`first_rr` 등).

    Returns:
        중앙값. 칸이 없으면 None.
    """
    if not section:
        return None
    spread = section.get(key)
    if not isinstance(spread, dict):
        return None
    value = cast(Report, spread).get("median")
    return Decimal(str(value)) if value is not None else None


def aggregate_phases(summaries: Sequence[RunSummary]) -> Report | None:
    """여러 실행의 국면 검증을 한 장으로 합친다.

    Args:
        summaries: 합칠 실행들.

    Returns:
        합산 교차표. 검증 칸을 **가진 실행이 하나도 없으면** None 이다 — 옛 회차를
        "표본 0" 으로 보여주면 국면 판정이 전부 틀린 것처럼 읽힌다.

    Note:
        🔴 합산은 여기서 한다. 화면에서 더하면 터미널 출력과 두 벌이 되고, 두 벌은
        반드시 갈라진다 (모듈 docstring).

        ⚠️ 실행마다 종목·시간축이 다르므로 이 표는 **전체 경향**이다. 어느 조합에서
        판정이 무너지는지는 행별 정확도를 봐야 한다.
    """
    carried = [item.phases for item in summaries if item.phases]
    if not carried:
        return None
    matrix: dict[tuple[str, str], int] = {}
    sample = correct = persisted = 0
    for section in carried:
        sample += int(section.get("sample", 0))
        correct += int(section.get("correct", 0))
        persisted += int(section.get("persisted", 0))
        for cell in cast(list[Report], section.get("matrix") or []):
            key = (str(cell.get("judged")), str(cell.get("realized")))
            matrix[key] = matrix.get(key, 0) + int(cell.get("count", 0))
    return {
        "sample": sample,
        "correct": correct,
        "persisted": persisted,
        # 표본 0 이면 비율을 만들지 않는다 (`RunSummary.phase_accuracy` 와 같은 규칙).
        "accuracy": str(Decimal(correct) / Decimal(sample)) if sample else None,
        "persistence": str(Decimal(persisted) / Decimal(sample)) if sample else None,
        "matrix": [
            {"judged": judged, "realized": realized, "count": count}
            for (judged, realized), count in sorted(matrix.items())
        ],
    }


@dataclass(frozen=True, slots=True)
class RunStore:
    """리포트 디렉터리 하나를 읽는다.

    Attributes:
        directory: 리포트 위치. 기본은 `logs/results`.

    Note:
        경로를 주입받는 이유는 테스트 때문만이 아니다 — 회차를 다른 디렉터리에 보관해
        비교하는 일이 생기고, 그때 전역 상수를 고치게 하면 안 된다.
    """

    directory: Path = DEFAULT_RESULTS_DIR

    def list_runs(self, tag: str | None = None) -> list[RunSummary]:
        """리포트를 읽어 요약 목록으로.

        Args:
            tag: 회차 태그 (`v6`). None 이면 전부.

        Returns:
            `run_id` 오름차순 요약들. 디렉터리가 없으면 빈 목록 — 아직 측정을 안 돌린
            정상 상태다.
        """
        if not self.directory.is_dir():
            return []
        pattern = f"*{tag}*.json" if tag else "*.json"
        return [self._summarize(path) for path in sorted(self.directory.glob(pattern))]

    def load_run(self, run_id: str) -> Report:
        """리포트 전문을 읽는다.

        Args:
            run_id: 실행 식별자.

        Returns:
            JSON 전문.

        Raises:
            ReportNotFoundError: 파일이 없는 경우.
        """
        path = self.directory / f"{run_id}.json"
        if not path.is_file():
            raise ReportNotFoundError(f"{run_id} 리포트가 없다: {path}")
        return self._read(path)

    def tags(self) -> list[str]:
        """존재하는 회차 태그들.

        Returns:
            정렬된 태그 목록. 프리셋의 첫 토큰이 태그다 (`v6_matrend_...` → `v6`).
        """
        found = {summary.preset.split("_", 1)[0] for summary in self.list_runs()}
        return sorted(found)

    def _summarize(self, path: Path) -> RunSummary:
        """리포트 하나를 요약으로.

        Args:
            path: 리포트 경로.

        Returns:
            요약.
        """
        data = self._read(path)
        outcomes = cast(Report, data.get("outcomes") or {})
        funnel = cast(Report, data.get("funnel") or {})
        plans = cast(Report | None, data.get("plans"))
        preset = str(data.get("preset", ""))
        return RunSummary(
            run_id=str(data.get("run_id", path.stem)),
            preset=preset,
            symbol=str(data.get("symbol", "")),
            display=str(data.get("display", "")),
            timeframe=str(data.get("timeframe", "")),
            setup=_setup_of(preset),
            entered=int(outcomes.get("realized", 0)),
            followed=int(outcomes.get("followed", 0)),
            expired=int(outcomes.get("expired", 0)),
            detected=int(funnel.get("detected", 0)),
            first_rr_median=_median(plans, "first_rr"),
            stop_pct_median=_median(plans, "stop_pct"),
            degenerate=bool(plans.get("degenerate")) if plans else False,
            phases=cast(Report | None, data.get("phases")),
            raw=data,
        )

    @staticmethod
    def _read(path: Path) -> Report:
        """JSON 을 읽는다.

        Args:
            path: 경로.

        Returns:
            파싱 결과.

        Raises:
            ReportNotFoundError: 내용이 JSON 객체가 아닌 경우 — 깨진 파일을 빈 dict 로
                넘기면 화면에 "거래 0건"으로 뜨고, 그것은 측정 실패와 구분되지 않는다.
        """
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportNotFoundError(f"{path} 를 읽을 수 없다: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ReportNotFoundError(f"{path} 가 JSON 객체가 아니다 — 리포트 형식이 아니다")
        return cast(Report, parsed)
