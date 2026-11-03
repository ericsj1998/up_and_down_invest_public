"""분기 우주 교체 계획 — 종목마다 무엇을 할지 정하는 순수 함수 (T758 5 단계 · §4).

교체는 세션을 직접 만지지 않고 이 계획을 따른다. 판단은 하지 않는다 — 다리별 목록은 스냅샷이,
"지금 무엇이 돌고 무엇을 들고 있나" 는 부르는 쪽이 넣는다. 그래서 같은 입력이면 같은 계획이고,
교체 도중 멈췄다 다시 돌려도(재기동 · 리더 상실) 남은 일만 다시 나온다.

🔴 **포지션이 열린 종목의 세션은 다시 띄우지 않는다.** 이어받기(`inherit`)는 2026-09-30 사고 셋의
경로다. 대신 그 종목은 진입 범위(`entry_legs`)만 좁히고 — 빠진 다리는 청산만, 남은 다리는 계속 —
세트 변경은 **평평해진 뒤** 다음 점검에서 한다. 대가: 그 사이 새로 붙은 다리의 진입은 그 종목에서
늦어진다(계좌 재현에서 건수를 잰다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

KEEP = "keep"
"""세트 그대로 · 진입 범위만 새 목록으로."""
NARROW = "narrow"
"""포지션이 있어 세트는 그대로 · 진입 범위 = 새 목록 ∩ 지금 세트(빠진 다리는 청산만)."""
RESPAWN = "respawn"
"""평평함 · 세트가 바뀜 → 거두고 새 세트로 띄움."""
SPAWN = "spawn"
"""새 종목 → 띄움."""
DROP = "drop"
"""목록 밖 · 평평함 → 거둠."""
WIND_DOWN = "wind_down"
"""목록 밖 · 포지션 있음 → 진입 범위 ∅ · 청산까지 둠."""


@dataclass(frozen=True, slots=True)
class MemberAction:
    """종목 하나의 할 일.

    Attributes:
        symbol: 주문 계약 이름.
        action: `keep` · `narrow` · `respawn` · `spawn` · `drop` · `wind_down`.
        legs: 띄울 · 남길 세트(다리 id 정렬). `drop` 이면 빈 튜플.
        entry_legs: 신규 진입을 허용할 다리(정렬). 세트의 부분집합이다.
    """

    symbol: str
    action: str
    legs: tuple[str, ...]
    entry_legs: tuple[str, ...]


def desired_legs(snapshot_legs: Mapping[str, tuple[str, ...]], symbol: str) -> tuple[str, ...]:
    """스냅샷에서 그 종목을 가진 다리들(정렬)."""
    return tuple(sorted(leg for leg, syms in snapshot_legs.items() if symbol in syms))


def plan_rotation(
    snapshot_legs: Mapping[str, tuple[str, ...]],
    running: Mapping[str, tuple[str, ...]],
    busy: Mapping[str, bool],
) -> tuple[MemberAction, ...]:
    """교체 계획.

    Args:
        snapshot_legs: `{다리: 주문 계약 이름들}`(스냅샷 `legs_gate` · 펀드가 쓰는 다리만).
        running: `{종목: 지금 세션 세트(다리 id)}` — 펀드가 돌리는 세션.
        busy: `{종목: 열린 포지션 · 주문이 있나}`. 없는 종목은 평평한 것으로 본다.

    Returns:
        종목 이름 순 계획 — 같은 입력이면 같은 계획이다.
    """
    out: list[MemberAction] = []
    for sym in sorted(set(running) | {s for v in snapshot_legs.values() for s in v}):
        want = desired_legs(snapshot_legs, sym)
        now = tuple(sorted(running.get(sym, ())))
        held = bool(busy.get(sym, False))
        if sym not in running:
            out.append(MemberAction(sym, SPAWN, want, want))
        elif not want:
            if held:
                out.append(MemberAction(sym, WIND_DOWN, now, ()))
            else:
                out.append(MemberAction(sym, DROP, (), ()))
        elif want == now:
            out.append(MemberAction(sym, KEEP, now, want))
        elif held:
            out.append(MemberAction(sym, NARROW, now, tuple(x for x in want if x in now)))
        else:
            out.append(MemberAction(sym, RESPAWN, want, want))
    return tuple(out)


def leg_symbols(actions: tuple[MemberAction, ...]) -> dict[str, tuple[str, ...]]:
    """계획 뒤 다리마다 **세션이 그 다리를 싣는** 종목들 — 펀드 다리 범위(`FundLeg.symbols`)."""
    out: dict[str, set[str]] = {}
    for a in actions:
        for leg in a.legs:
            out.setdefault(leg, set()).add(a.symbol)
    return {leg: tuple(sorted(s)) for leg, s in out.items()}


@dataclass(frozen=True, slots=True)
class RotationConfig:
    """교체 실행 설정(`config/universe.yml` 의 `rotation` 블록 · 성능 · T759 교훈).

    Attributes:
        check_every_s: 리더가 우주 펀드를 점검하는 간격(초).
        batch: 한 번 점검에 띄우기 · 거두기를 몇 종목까지(나머지는 다음 점검 · 계획이 다시 나온다).
        stagger_s: 종목 사이 간격(초) — 되살림 `AUTOSTART_STAGGER_S` 와 같은 뜻.
        quiet_from_minute: 매시 이 분부터 ···
        quiet_to_minute: ··· 다음 시 이 분까지는 띄우기 · 거두기를 쉰다
            (봉 마감 · 메모 deploy-not-at-bar-close).
        rss_limit_mb: 띄우기 전 프로세스 RSS + 예상 증가가 이 값을 넘으면 띄우지 않는다(R7 · 경보).
        board_mb: 판 하나의 예상 메모리(MB · T310 · 추정).
    """

    check_every_s: float = 300.0
    batch: int = 4
    stagger_s: float = 1.5
    quiet_from_minute: int = 45
    quiet_to_minute: int = 5
    rss_limit_mb: float = 750.0
    board_mb: float = 5.0

    def quiet(self, minute: int) -> bool:
        """봉 마감 앞뒤인가."""
        return minute >= self.quiet_from_minute or minute < self.quiet_to_minute


def load_rotation_config(path: Path) -> RotationConfig:
    """`rotation` 블록을 읽는다 — 없으면 기본값."""
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    body = cast("dict[str, Any]", raw.get("rotation") or {})
    base = RotationConfig()
    return RotationConfig(
        check_every_s=float(body.get("check_every_s", base.check_every_s)),
        batch=int(body.get("batch", base.batch)),
        stagger_s=float(body.get("stagger_s", base.stagger_s)),
        quiet_from_minute=int(body.get("quiet_from_minute", base.quiet_from_minute)),
        quiet_to_minute=int(body.get("quiet_to_minute", base.quiet_to_minute)),
        rss_limit_mb=float(body.get("rss_limit_mb", base.rss_limit_mb)),
        board_mb=float(body.get("board_mb", base.board_mb)),
    )
