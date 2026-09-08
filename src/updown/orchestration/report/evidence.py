"""근거 묶음 — 백테스트·합성 미래·매트릭스 산출물을 화면용 JSON 으로 조립한다 (T222).

🔴 **숫자를 새로 만들지 않는다.** 이 모듈은 이미 기록된 산출물
(`docs/measurements/t200_lab_results*.txt` ·
`t201_matrix_results.md` · `config/playbooks.yml`)을 **읽어서 옮기기만** 한다. 요약 통계(중앙·최악·
CVaR)는 `scenario_lab.py` 와 같은 식(`np.percentile` 5% · 그 이하의 평균)으로 다시 계산하되, 시험이
문서에 적힌 값과 대조한다 — 식이 어긋나면 화면이 문서와 다른 말을 하게 되므로.

🔴 **옮겨 적은 숫자는 인용 검사를 통과해야 한다.** 표가 아니라 문장 속에 있는 값(E1 재현 등)은
`docs/measurements/evidence_sources.yml` 에 사람이 옮겨 적는데, 각 항목의 `quote` 가 인용한
문서 안에
**글자 그대로** 있어야 묶음이 만들어진다. 문서를 고치고 옮겨 적은 값을 안 고치면 빌드가 죽는다 —
조용히 낡은 숫자를 보여 주는 것보다 낫다 (절대 규칙 #8).

이 모듈은 파일·DB 를 만지지 않는다 — 문자열을 받아 자료형을 돌려준다. 읽고 쓰는 것은
`scripts/build/evidence_bundle.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, cast

# ── 합성 45미래 결과표 (.txt) ─────────────────────────────────────────────────

_NUM = r"[+-]?[\d,]+(?:\.\d+)?"
_LAB_ROW = re.compile(
    rf"^(?P<name>\S.*?)\s+(?P<seed>\d+)\s+(?P<total>{_NUM})\s+(?P<cagr>{_NUM})\s+(?P<mdd>{_NUM})\s+"
    rf"(?P<liq>\d+)\s+(?P<h3y>{_NUM})\s+(?P<h2y>{_NUM})\s+(?P<h1y>{_NUM})\s+(?P<h1m>{_NUM})\s+(?P<h1d>{_NUM})\s*$"
)
_MU = re.compile(r"([+-]?\d+)%/년")


def _num(text: str) -> float:
    return float(text.replace(",", ""))


@dataclass(frozen=True)
class LabRow:
    """45미래 표의 한 줄 — 시나리오 하나 · 씨앗 하나."""

    scenario: str
    mu_pct: float | None
    seed: int
    total_pct: float
    cagr_pct: float
    mdd_pct: float
    liquidations: int
    h3y_pct: float
    h2y_pct: float
    h1y_pct: float
    h1m_pct: float
    h1d_pct: float


@dataclass(frozen=True)
class LabTable:
    """`t200_lab_results*.txt` 하나 — 머리말(#) + 행."""

    header: tuple[str, ...]
    rows: tuple[LabRow, ...]


def parse_lab_txt(text: str) -> LabTable:
    """`scenario_lab.py` 가 쓴 결과 텍스트를 읽는다.

    행 형식: `시나리오 씨앗 전체% CAGR% MDD% 청산 3년% 2년% 1년% 1달% 1일%`. 시나리오 이름에
    공백이 있어 오른쪽에서부터 숫자 열을 잡는다. 머리말은 `#` 줄 — 기준점·생성 시각·"합성 분포 ·
    미래 증명 아님" 문구가 거기 있고, 화면은 그것을 그대로 보여 준다.

    Args:
        text: 파일 내용.

    Returns:
        머리말과 행. 열 이름 줄·빈 줄은 버린다.

    Raises:
        ValueError: 행이 하나도 없으면 — 형식이 바뀐 것이다.
    """
    header: list[str] = []
    rows: list[LabRow] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("#"):
            header.append(line.lstrip("# ").strip())
            continue
        m = _LAB_ROW.match(line)
        if not m:
            continue  # 열 이름 줄
        mu = _MU.search(m["name"])
        rows.append(
            LabRow(
                scenario=m["name"].strip(),
                mu_pct=float(mu.group(1)) if mu else None,
                seed=int(m["seed"]),
                total_pct=_num(m["total"]),
                cagr_pct=_num(m["cagr"]),
                mdd_pct=_num(m["mdd"]),
                liquidations=int(m["liq"]),
                h3y_pct=_num(m["h3y"]),
                h2y_pct=_num(m["h2y"]),
                h1y_pct=_num(m["h1y"]),
                h1m_pct=_num(m["h1m"]),
                h1d_pct=_num(m["h1d"]),
            )
        )
    if not rows:
        raise ValueError("결과 행이 없다 — 표 형식이 바뀌었나")
    return LabTable(header=tuple(header), rows=tuple(rows))


def _percentile(sorted_values: list[float], q: float) -> float:
    """`numpy.percentile` 기본(linear) 과 같은 값 — numpy 없이 (`scenario_lab.py` 와 같은 식)."""
    if not sorted_values:
        raise ValueError("빈 표본")
    pos = (len(sorted_values) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


@dataclass(frozen=True)
class LabSummary:
    """한 세상(블록 규칙)의 분포 요약 — `scenario_lab.py` 출력과 같은 지표."""

    n: int
    total_median_pct: float
    total_worst_pct: float
    total_best_pct: float
    total_p5_pct: float
    total_p95_pct: float
    cvar5_pct: float
    mdd_median_pct: float
    mdd_worst_pct: float
    liquidated_runs: int
    liquidations_total: int


def summarize(rows: Iterable[LabRow]) -> LabSummary:
    """분포 요약. CVaR 은 `scenario_lab.py` 그대로 — 5% 백분위 **이하** 값들의 평균.

    Args:
        rows: 한 세상의 45행.

    Returns:
        요약.

    Raises:
        ValueError: 표본이 비었다.
    """
    items = list(rows)
    if not items:
        raise ValueError("빈 표본")
    totals = sorted(r.total_pct for r in items)
    p5 = _percentile(totals, 5)
    tail = [t for t in totals if t <= p5]
    mdds = [r.mdd_pct for r in items]
    return LabSummary(
        n=len(items),
        total_median_pct=median(totals),
        total_worst_pct=totals[0],
        total_best_pct=totals[-1],
        total_p5_pct=p5,
        total_p95_pct=_percentile(totals, 95),
        cvar5_pct=sum(tail) / len(tail),
        mdd_median_pct=median(mdds),
        mdd_worst_pct=max(mdds),
        liquidated_runs=sum(1 for r in items if r.liquidations > 0),
        liquidations_total=sum(r.liquidations for r in items),
    )


# ── 마크다운 표 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MdTable:
    """마크다운 파이프 표 하나."""

    heading: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def _cells(line: str) -> tuple[str, ...]:
    inner = line.strip().strip("|")
    return tuple(c.strip().replace("**", "") for c in inner.split("|"))


def md_table_after(text: str, heading_contains: str) -> MdTable:
    """`heading_contains` 를 품은 줄 **뒤에 처음 나오는** 파이프 표를 읽는다.

    굵게(`**`)는 벗긴다 — 값이지 꾸밈이 아니다. 구분 줄(`|---|`)은 버린다.

    Args:
        text: 마크다운 전체.
        heading_contains: 표 앞 제목(또는 문장)의 일부.

    Returns:
        표.

    Raises:
        ValueError: 제목이 없거나 그 뒤에 표가 없으면.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if heading_contains in ln), None)
    if start is None:
        raise ValueError(f"제목을 못 찾았다: {heading_contains!r}")
    i = start + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    if i >= len(lines):
        raise ValueError(f"{heading_contains!r} 뒤에 표가 없다")
    columns = _cells(lines[i])
    rows: list[tuple[str, ...]] = []
    for ln in lines[i + 1 :]:
        if not ln.lstrip().startswith("|"):
            break
        cells = _cells(ln)
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return MdTable(heading=lines[start].strip("# ").strip(), columns=columns, rows=tuple(rows))


# ── 옮겨 적은 값의 인용 검사 ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Citation:
    """문장 속 숫자를 옮겨 적은 항목 — `quote` 가 `doc` 안에 글자 그대로 있어야 한다."""

    id: str
    doc: str
    quote: str


def check_citations(items: Iterable[Citation], read: Callable[[str], str]) -> None:
    """모든 인용이 원문에 있는지 확인한다.

    Args:
        items: 옮겨 적은 항목들.
        read: 문서 경로 → 내용.

    Raises:
        ValueError: 하나라도 원문에 없으면 — 어느 항목이 어느 문서에서 빠졌는지 전부 적어서.
    """
    cache: dict[str, str] = {}
    missing: list[str] = []
    for it in items:
        if it.doc not in cache:
            cache[it.doc] = read(it.doc)
        if it.quote not in cache[it.doc]:
            missing.append(f"{it.id}: {it.doc} 에 {it.quote!r} 가 없다")
    if missing:
        raise ValueError(
            "인용 검사 실패 — 문서가 바뀌었으면 evidence_sources.yml 도 고친다:\n"
            + "\n".join(missing)
        )


# ── 묶음 조립 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class World:
    """블록 규칙 하나로 만든 45미래 세상 — 표 + 요약."""

    id: str
    label: str
    canonical: bool
    header: tuple[str, ...]
    summary: LabSummary
    rows: tuple[LabRow, ...]
    # 원문 파일명 (docs/measurements/…txt). 상세 세트(`synth_detail` 의 basis)와 같은 파일이면
    # 화면이
    # 청산 확률·종목별 분해를 그 세상에 붙인다 (2026-09-06).
    source: str = ""


def world_from_txt(
    world_id: str, label: str, text: str, *, canonical: bool = False, source: str = ""
) -> World:
    """결과 텍스트 하나 → 세상 하나.

    Args:
        world_id: 세상 id.
        label: 표시 이름.
        text: `t200_lab_results*.txt` 원문.
        canonical: 기준 세상인가.
        source: 출처 파일 이름.

    Returns:
        표와 요약이 채워진 세상.
    """
    table = parse_lab_txt(text)
    return World(
        id=world_id,
        label=label,
        canonical=canonical,
        header=table.header,
        summary=summarize(table.rows),
        rows=table.rows,
        source=source,
    )


def to_jsonable(obj: Any) -> Any:
    """Dataclass 트리 → JSON 으로 쓸 수 있는 dict/list.

    Args:
        obj: dataclass · dict · list · 스칼라.

    Returns:
        같은 모양의 순수 JSON 값.
    """
    if hasattr(obj, "__dataclass_fields__"):
        data: dict[str, Any] = asdict(obj)
        return {k: to_jsonable(v) for k, v in data.items()}
    if isinstance(obj, dict):
        mapping = cast(dict[Any, Any], obj)
        return {str(k): to_jsonable(v) for k, v in mapping.items()}
    if isinstance(obj, list | tuple):
        seq = cast(Sequence[Any], obj)
        return [to_jsonable(v) for v in seq]
    return obj
