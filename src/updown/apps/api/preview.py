"""진입 가능성 모으기 — 펀드 화면의 **깜빡임** 재료 (사용자 2026-09-27).

> *"측정할 수 있는 선에서, 진입 가능성이 있는 종목은 테두리가 깜빡이는 느낌이면 어떨까?"*

정의(사용자 확정 "그 정의로 진행"): ① 지정가 진입 표가 걸려 있다(`waiting`)
② 형성 중 봉이 지금 값으로
닫히면 같은 탐지기가 후보를 낸다(`signal` · "마감 전 예비 신호"). 계산은 `LiveRunner.preview` —
판정 · 원장 · 주문을 안 건드린다.

- **누가 보고 있을 때만 돈다.** 펀드 현황(`/rebalancer`)을 부르면 `touch()` 가 시각을
  적고, 10분 넘게
  아무도 안 보면 한 바퀴도 안 돈다 — 1 GB 서버에서 보는 사람 없는 계산을 하지 않는다.
- 한 바퀴 = 판마다 한 번 · 판 사이 0.3초 쉼 · 바퀴 사이 5분. 거래 리더에서만 돈다(판이 거기 있다).
  한 바퀴가 10초를 넘으면 경고 한 줄(`preview_sweep_slow`).
- **마감이 가까운 축만 잰다**(T331 · `LiveRunner.preview` 의 `near_close`) — 1H 판은 매시 45분 뒤,
  4H · 1D 판은 마감 30분 전부터. 그 밖의 시간엔 판마다 조회 0 · 탐지 0 이라 한 바퀴가 거의 공짜다.
  2분 x 40판 x 전 시간 훑기가 평시 CPU 약 10%p 였다(2026-09-30 크레딧 고갈).
- 실패는 그 판만 비우고 로그 한 줄 — 매매를 막지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any

from updown.common.logging.setup import get_logger

_logger = get_logger("api.preview")

SWEEP_EVERY = 300.0
"""바퀴 사이(초) — 예비 신호 창(1H 판 15분)에 두세 번 걸리면 충분하다 (T331 · 2분 → 5분)."""

BETWEEN_RUNS = 0.3
"""판 사이 쉼(초) — 한 판 계산이 이벤트 루프를 오래 쥐지 않게 나눈다."""

WATCH_WINDOW = 600.0
"""마지막으로 화면이 본 뒤 이만큼(초) 지나면 멈춘다."""

STALE_AFTER = 600.0
"""이보다 오래된 값은 화면에 싣지 않는다(초)."""

PREVIEWS: dict[str, tuple[float, dict[str, Any] | None]] = {}
"""판 id → (잰 시각 monotonic, 진입 가능성 또는 None)."""

_seen_at: float | None = None


def touch() -> None:
    """화면이 펀드 현황을 읽었다 — 다음 바퀴를 돌게 한다."""
    global _seen_at
    _seen_at = time.monotonic()


def preview_of(handle: str) -> dict[str, Any] | None:
    """그 판의 진입 가능성 — 없거나 낡았으면 None."""
    got = PREVIEWS.get(handle)
    if got is None or time.monotonic() - got[0] > STALE_AFTER:
        return None
    return got[1]


def watched(now: float, seen_at: float | None) -> bool:
    """누가 보고 있나(순수)."""
    return seen_at is not None and now - seen_at <= WATCH_WINDOW


async def sweep_once() -> tuple[int, float, float]:
    """판마다 한 번 잰다.

    Returns:
        (잰 판 수, 계산에 쓴 초 합 — 판 사이 쉼은 뺀다, 가장 오래 걸린 한 판의 초).
    """
    from updown.apps.api.walkforward import LIVE_RUNNERS

    runs = dict(LIVE_RUNNERS)
    done = 0
    busy = 0.0
    worst = 0.0
    for run_id, runner in runs.items():
        started = time.monotonic()
        try:
            PREVIEWS[run_id] = (time.monotonic(), await runner.preview())
            done += 1
        except Exception as exc:  # 화면용 — 한 판 실패가 다른 판 · 매매를 막지 않는다
            PREVIEWS.pop(run_id, None)
            _logger.warning("preview_failed", payload={"run": run_id, "error": str(exc)[:160]})
        took = time.monotonic() - started
        busy += took
        worst = max(worst, took)
        await asyncio.sleep(BETWEEN_RUNS)
    for gone in set(PREVIEWS) - set(runs):
        PREVIEWS.pop(gone, None)
    return done, busy, worst


SLOW_SWEEP = 10.0
"""한 바퀴의 **계산 시간 합**(판 사이 쉼 제외)이 이보다 크면(초) 경고 — 1 GB 서버의 CPU 를 지켜본다.

1.21.0 배포 첫 바퀴에서 쉼(0.3초 x 40 = 12초)까지 세어 18초로 경고가 났다 — 실제 계산은 약 6초였다.
"""


def found_now() -> list[str]:
    """지금 진입 가능성이 있는 판 id(정렬) — 바뀔 때만 로그에 남기는 데 쓴다."""
    return sorted(run for run, (_, got) in PREVIEWS.items() if got)


PREVIEW_FIELDS = ("kind", "side", "leg", "frame", "entry", "stop")
"""로그에 싣는 예비 신호 칸 — `LiveRunner.preview()` 가 주는 것 중 복기에 필요한 것."""


def preview_rows(
    previews: Mapping[str, tuple[float, dict[str, Any] | None]], found: Sequence[str]
) -> list[dict[str, Any]]:
    """깜빡이는 판들의 예비 신호를 로그 한 줄에 실을 모양으로 (순수 · T445).

    Args:
        previews: `PREVIEWS` — 판 id → (잰 시각, 신호 또는 None).
        found: 지금 깜빡이는 판 id 들.

    Returns:
        판마다 `{run, kind, side, leg, frame, entry, stop}` — 없는 칸은 빈 문자열. 최대 40판.
    """
    rows: list[dict[str, Any]] = []
    for run in list(found)[:40]:
        got = previews.get(run, (0.0, None))[1] or {}
        rows.append({"run": run, **{k: str(got.get(k, "") or "") for k in PREVIEW_FIELDS}})
    return rows


async def preview_loop() -> None:
    """누가 보고 있으면 `SWEEP_EVERY` 마다 한 바퀴 — 화면을 연 직후엔 5초 안에 첫 바퀴."""
    last: float | None = None
    shown: list[str] | None = None
    while True:
        now = time.monotonic()
        if watched(now, _seen_at) and (last is None or now - last >= SWEEP_EVERY):
            last = now
            done, busy, worst = await sweep_once()
            if busy > SLOW_SWEEP:
                _logger.warning(
                    "preview_sweep_slow",
                    payload={"runs": done, "busy_s": round(busy, 1), "worst_s": round(worst, 2)},
                )
            found = found_now()
            if found != shown:
                # 깜빡임 목록이 바뀔 때만 — "화면이 제대로 깜빡이나" 를 로그로 대조한다.
                # ⭐ T445(2026-10-09) — 판 id 와 종류만 적던 것을 다리 · 방향 · 진입가 · 손절 ·
                #    축까지 적는다. 분석기가 "예비 신호 → 마감 때 진입했나 · 결과는" 을 잇는
                #    재료다. 한 줄에 최대 40판 · 바뀔 때만이라 부하는 없다.
                shown = found
                _logger.info(
                    "preview_found",
                    payload={
                        "count": len(found),
                        "runs": [
                            f"{run}:{(PREVIEWS[run][1] or {}).get('kind', '')}" for run in found
                        ][:40],
                        "previews": preview_rows(PREVIEWS, found),
                        "busy_s": round(busy, 1),
                    },
                )
        await asyncio.sleep(5.0)
