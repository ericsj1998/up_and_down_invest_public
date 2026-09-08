"""매매법별 권한 — 보기 · 백테스트 · 사용 (T230 · 사용자 2026-09-08).

기능(`caps.Cap`)은 "이 서버에서 무엇을 할 수 있나" 이고, 여기는 "어느 **매매법**에 대해" 다.
매매법 하나에 칸 셋:

    view       존재·설명·규칙을 본다 (매매법 목록에 뜬다)
    backtest   근거 화면의 그 매매법 표·차트·합성 미래
    trade      그 매매법으로 판·펀드를 연다 — 서버 축은 `demo_trade`/`live_trade` 가 따로 본다

계산 규칙 (사용자 확정 2026-09-08):

    grant(사람, 매매법) = 사람별 덮어쓰기 행이 있으면 그 행
                          없으면 묶음 정책(`PlaybookPolicy`) 이 그 매매법을 허용하는가
    감사(`Cap.AUDIT`) 가 있으면 view·backtest 는 모든 매매법에 참 — trade 는 그대로
    view 없는 backtest/trade 는 금지 (`PlaybookGrant.validate`)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

Kind = Literal["view", "backtest", "trade"]
KINDS: tuple[Kind, ...] = ("view", "backtest", "trade")
ALL = "*"
"""정책 값 — 모든 매매법."""
SAMPLE_PLAYBOOK = "sample_ma_cross"
"""견본 매매법 id — 게스트·열람자에게 백테스트를 열어 두는 유일한 것 (사용자 2026-09-08)."""

Scope = frozenset[str] | str
"""칸 하나의 범위 — `"*"` 또는 매매법 id 집합."""


def _scope_from(raw: object) -> Scope:
    if raw == ALL or raw == "all":
        return ALL
    if isinstance(raw, list | tuple | set | frozenset):
        items = cast("Iterable[object]", raw)
        return frozenset(str(item) for item in items if str(item))
    return frozenset()


def _scope_json(scope: Scope) -> str | list[str]:
    return ALL if scope == ALL else sorted(cast("frozenset[str]", scope))


@dataclass(frozen=True, slots=True)
class PlaybookPolicy:
    """묶음의 매매법 기본값 — 칸마다 "전부" 또는 매매법 목록.

    Attributes:
        view: 볼 수 있는 매매법.
        backtest: 백테스트를 볼 수 있는 매매법.
        trade: 쓸 수 있는 매매법.
    """

    view: Scope = frozenset()
    backtest: Scope = frozenset()
    trade: Scope = frozenset()

    def allows(self, kind: Kind, playbook_id: str) -> bool:
        """이 칸이 그 매매법을 허용하나.

        Args:
            kind: 칸.
            playbook_id: 매매법 id.

        Returns:
            `"*"` 이거나 목록에 있으면 참.
        """
        scope: Scope = getattr(self, kind)
        return scope == ALL or playbook_id in cast("frozenset[str]", scope)

    @classmethod
    def from_json(cls, raw: object) -> PlaybookPolicy:
        """표(JSON)에서 읽는다 — 모르는 모양은 빈 정책 (문을 열지 않는다).

        Args:
            raw: `{"view": "*" | [ids], "backtest": ..., "trade": ...}` 또는 None.

        Returns:
            정책.
        """
        if not isinstance(raw, dict):
            return cls()
        data = cast("dict[str, object]", raw)
        return cls(
            view=_scope_from(data.get("view")),
            backtest=_scope_from(data.get("backtest")),
            trade=_scope_from(data.get("trade")),
        )

    def to_json(self) -> dict[str, Any]:
        """표·API 로 나가는 모양 — 목록은 정렬해 같은 정책이 같은 글자가 되게.

        Returns:
            `{"view": "*" | [ids], "backtest": ..., "trade": ...}`.
        """
        return {
            "view": _scope_json(self.view),
            "backtest": _scope_json(self.backtest),
            "trade": _scope_json(self.trade),
        }


@dataclass(frozen=True, slots=True)
class PlaybookGrant:
    """한 사람의 한 매매법에 대한 유효 권한 (또는 덮어쓰기 행).

    Attributes:
        playbook_id: 매매법.
        view: 본다.
        backtest: 백테스트를 본다.
        trade: 쓴다.
    """

    playbook_id: str
    view: bool
    backtest: bool
    trade: bool

    def validate(self) -> None:
        """`view` 없는 `backtest`/`trade` 는 뜻이 없다 — 이름도 못 보는 매매법을 쓰게 한다.

        Raises:
            ValueError: 그 조합이면.
        """
        if (self.backtest or self.trade) and not self.view:
            raise ValueError(
                f"{self.playbook_id}: 보기(view) 없이 백테스트·사용만 줄 수 없다 — 보기를 먼저 준다"
            )

    def as_json(self) -> dict[str, Any]:
        """API 로 나가는 모양.

        Returns:
            `{id, view, backtest, trade}`.
        """
        return {
            "id": self.playbook_id,
            "view": self.view,
            "backtest": self.backtest,
            "trade": self.trade,
        }


def effective_grant(
    playbook_id: str,
    *,
    policy: PlaybookPolicy,
    row: PlaybookGrant | None,
    audit: bool,
) -> PlaybookGrant:
    """유효 권한 — 사람별 행 > 묶음 정책, 감사는 view·backtest 를 전부 연다.

    Args:
        playbook_id: 매매법.
        policy: 묶음(등급) 정책.
        row: 사람별 덮어쓰기 행. None 이면 정책.
        audit: 감사 기능을 쥐었나 (사용자 2026-09-08: "감사 = 모든 매매법과 백테스트를 본다").

    Returns:
        칸 셋이 정해진 권한.
    """
    base = (
        row
        if row is not None
        else PlaybookGrant(
            playbook_id,
            view=policy.allows("view", playbook_id),
            backtest=policy.allows("backtest", playbook_id),
            trade=policy.allows("trade", playbook_id),
        )
    )
    if audit:
        base = replace(base, view=True, backtest=True)
    return base


_SAMPLE_ONLY: Scope = frozenset({SAMPLE_PLAYBOOK})
_NONE: Scope = frozenset()

BUILTIN_POLICIES: dict[str, PlaybookPolicy] = {
    # 게스트 — 모든 매매법을 보고, 데모에서 거래하고(서버 축은 demo_trade), 백테스트는 견본만
    "guest": PlaybookPolicy(view=ALL, backtest=_SAMPLE_ONLY, trade=ALL),
    "live_watch_guest": PlaybookPolicy(view=ALL, backtest=_SAMPLE_ONLY, trade=_NONE),
    "viewer": PlaybookPolicy(view=ALL, backtest=_SAMPLE_ONLY, trade=_NONE),
    "trader": PlaybookPolicy(view=ALL, backtest=ALL, trade=ALL),
    "admin": PlaybookPolicy(view=ALL, backtest=ALL, trade=ALL),
    "super_admin": PlaybookPolicy(view=ALL, backtest=ALL, trade=ALL),
}
"""내장 묶음의 매매법 기본값 — 표(`role_collections.playbook_policy`)가 비었을 때 · 0115 씨앗."""

DEFAULT_POLICY = PlaybookPolicy(view=ALL, backtest=_SAMPLE_ONLY, trade=_NONE)
"""묶음이 없는 사람(승인 대기 · 모르는 묶음) — 보기와 견본 백테스트만. 대기는 미들웨어가 막는다."""

__all__ = [
    "ALL",
    "BUILTIN_POLICIES",
    "DEFAULT_POLICY",
    "KINDS",
    "SAMPLE_PLAYBOOK",
    "Kind",
    "PlaybookGrant",
    "PlaybookPolicy",
    "Scope",
    "effective_grant",
]
