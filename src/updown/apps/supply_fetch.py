"""공급 일정 받기 — 짧게 사는 따로 된 프로세스 (T1057).

    python -m updown.apps.supply_fetch < 요청.json > 결과.json

엔진 작업(`orchestration.rebalancer.supply_job`)이 하루 한 번 띄우고 끝나면 사라진다 — 일정 파일
수백 개(풀면 약 450 MB · gzip 약 47 MB)를 흘려 읽는 동안의 메모리가 실계좌 api 프로세스(1 GB 서버 ·
우주 점검 메모리 문)에 남지 않게 한다. 조립만 한다: 받기는 `marketdata.provider` 가 주는 원천 ·
사건 셈은 `analysis.supply_schedule` 의 함수다(펀드 재현도 같은 함수).

요청(표준 입력 JSON): `{"asof": ISO, "horizon_days": N, "kinds": [갈래…], "names": [순위 거래소
무기한 이름…], "pace_s": 초}`. 결과(표준 출력 JSON 하나): 원천마다 목록 수 · 실패 파일 · 받은 양,
갈래마다 짝 지은 프로젝트 수 · `asof` 날 ~ `asof + horizon_days` 날의 사건(크기 문턱 전 · 파일
ETag · Last-Modified 를 붙여). 목록 · 코인 목록을 못 받으면 `{"ok": false, "fatal": …}` 를 낸다
(부르는 쪽이 실패로 적고 알림한다 · 규칙 #8). 파일 하나의 실패(어떤 예외든)는 `failed` 에 적고
계속한다. 갈래마다 받은 일정 전체에서 규칙의 꼴 · 범주에 맞는 사건 줄 수(`kind_lines`)도 센다 —
자료 칸이 바뀌어 사건이 조용히 0 이 되는 것을 부르는 쪽이 잡는다.

🔴 메모리가 모자라면 이 프로세스가 먼저 죽는다 — 시작하자마자 `oom_score_adj` 1000 · `nice` 를
건다(같은 컨테이너의 실계좌 api 가 아니라 이것을 커널이 고르게). 부르는 쪽은 시크릿 없는 최소
환경만 넘긴다.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from updown.analysis.supply_schedule import (
    SupplyRule,
    coin_symbol,
    day_events,
    kind_lines,
    load_supply_rules,
    rank_names,
    rank_symbol,
)
from updown.marketdata.coingecko.coins import CoinList, CoinListError
from updown.marketdata.defillama.emissions import Schedule, ScheduleFeedError
from updown.marketdata.file_fetch import FetchInfo


class ScheduleSource(Protocol):
    """일정 원천(`marketdata.defillama.emissions.EmissionsSource` 꼴)."""

    calls: int
    wire_bytes: int

    def protocol_list(self, tmp: Path) -> tuple[tuple[str, ...], FetchInfo]:
        """목록."""
        ...

    def schedule(self, slug: str, tmp: Path) -> tuple[Schedule, FetchInfo]:
        """일정 하나."""
        ...


class CoinSource(Protocol):
    """코인 목록 원천(`marketdata.coingecko.coins.CoinListSource` 꼴)."""

    def coin_list(self, tmp: Path) -> tuple[CoinList, FetchInfo]:
        """코인 목록."""
        ...


def _peak_rss_kb() -> int | None:
    try:
        import resource
    except ImportError:  # pragma: no cover - 리눅스 밖
        return None
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def run(
    payload: Mapping[str, Any],
    *,
    schedule_source: Callable[[str, float], ScheduleSource],
    coin_source: Callable[[], CoinSource],
    rules: Mapping[str, SupplyRule] | None = None,
    tmp: Path | None = None,
) -> dict[str, Any]:
    """요청 하나를 처리한다(시험이 원천을 넣는다).

    Args:
        payload: 요청(모듈 설명).
        schedule_source: 원천 이름 · 간격 → 일정 원천.
        coin_source: 코인 목록 원천.
        rules: 갈래 규칙(비면 설정 파일).
        tmp: 임시 폴더(비면 새로 만들고 지운다).

    Returns:
        결과 사전(모듈 설명).
    """
    t0 = time.perf_counter()
    all_rules = dict(rules) if rules is not None else load_supply_rules()
    asof = datetime.fromisoformat(str(payload["asof"]))
    lo = asof.date()
    hi = lo + timedelta(days=int(payload["horizon_days"]))
    chosen = [all_rules[str(k)] for k in payload["kinds"]]
    names_all = [str(x) for x in payload.get("names") or ()]
    pace_s = float(payload.get("pace_s", 0.1))
    if tmp is None:
        with tempfile.TemporaryDirectory(prefix="supply_") as d:
            return run(
                payload,
                schedule_source=schedule_source,
                coin_source=coin_source,
                rules=all_rules,
                tmp=Path(d),
            )
    try:
        coins, coin_info = coin_source().coin_list(tmp)
    except CoinListError as exc:
        return {"ok": False, "fatal": f"코인 목록: {exc}"[:300]}
    names = {r.kind: rank_names(names_all, r) for r in chosen}
    out_kinds: dict[str, Any] = {
        r.kind: {"mapped": 0, "kind_lines": 0, "events": []} for r in chosen
    }
    out_sources: dict[str, Any] = {}
    for source in sorted({r.source for r in chosen}):
        src = schedule_source(source, pace_s)
        try:
            slugs, list_info = src.protocol_list(tmp)
        except ScheduleFeedError as exc:
            return {"ok": False, "fatal": f"{source} 목록: {exc}"[:300]}
        failed: list[list[str]] = []
        files_ok = 0
        mine = [r for r in chosen if r.source == source]
        for slug in slugs:
            try:
                schedule, info = src.schedule(slug, tmp)
                rows = _file_rows(schedule, info, mine, names, coins, lo, hi)
            except (
                Exception
            ) as exc:  # 파일 하나의 실패(끊김 · 깨진 파일 · 뜻밖의 꼴)는 적고 계속한다
                failed.append([slug, f"{type(exc).__name__}: {exc}"[:160]])
                continue
            files_ok += 1
            for kind, (mapped, lines, events) in rows.items():
                out_kinds[kind]["mapped"] += mapped
                out_kinds[kind]["kind_lines"] += lines
                out_kinds[kind]["events"].extend(events)
            del schedule
        out_sources[source] = {
            "list_n": len(slugs),
            "list_etag": list_info.etag,
            "files_ok": files_ok,
            "failed": failed,
            "calls": src.calls,
            "wire_bytes": src.wire_bytes,
        }
    return {
        "ok": True,
        "asof": asof.isoformat(),
        "coins": {"n": coins.coins, "etag": coin_info.etag},
        "sources": out_sources,
        "kinds": out_kinds,
        "seconds": round(time.perf_counter() - t0, 1),
        "peak_rss_kb": _peak_rss_kb(),
    }


def _file_rows(
    schedule: Schedule,
    info: FetchInfo,
    rules: list[SupplyRule],
    names: Mapping[str, frozenset[str]],
    coins: CoinList,
    lo: Any,
    hi: Any,
) -> dict[str, tuple[int, int, list[dict[str, Any]]]]:
    """일정 하나 → 갈래마다 `(짝 지음 0 · 1, 꼴 · 범주 줄 수, 창 안 사건들)`."""
    out: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
    for rule in rules:
        lines = kind_lines(schedule, rule)
        rank = rank_symbol(
            coin_symbol(schedule.gecko_id, schedule.token, coins), names[rule.kind], rule
        )
        if rank is None:
            out[rule.kind] = (0, lines, [])
            continue
        events = [
            {**ev.to_json(), "etag": info.etag, "last_modified": info.last_modified}
            for ev in day_events(schedule, rank, rule, lo, hi)
        ]
        out[rule.kind] = (1, lines, events)
    return out


NICE = 10
"""받기 프로세스의 CPU 양보(리눅스 nice 더하기) — 실계좌 api 의 걸음보다 뒤에 돈다."""


def lower_priority() -> None:
    """이 프로세스를 메모리 부족 때 먼저 죽을 쪽 · 낮은 CPU 우선순위로 둔다(못 걸면 그냥 간다).

    Note:
        `oom_score_adj` 를 올리는 것은 권한 없이 된다(내리는 것만 막힌다). 리눅스가 아니거나 막히면
        건너뛴다 — 받기를 못 하게 하지는 않는다(메모리 여유는 띄우는 쪽이 먼저 본다).
    """
    with contextlib.suppress(OSError):
        Path("/proc/self/oom_score_adj").write_text("1000", encoding="ascii")
    with contextlib.suppress(OSError, AttributeError):
        os.nice(NICE)


def main() -> int:
    """진입점 — 표준 입력의 요청을 처리해 표준 출력에 결과 JSON 하나를 낸다."""
    lower_priority()
    from updown.marketdata.provider import coin_list_source, supply_schedule_source

    payload = json.load(sys.stdin)
    result = run(
        payload,
        schedule_source=lambda source, pace: supply_schedule_source(source, pace_s=pace),
        coin_source=coin_list_source,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
