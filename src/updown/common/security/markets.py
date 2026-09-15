"""시장별 권한 — 보기 · 백테스트 · 거래 (T242 · 사용자 2026-09-09).

T230 의 매매법 축(`playbooks.py`) 옆에 **시장 갈래** 축을 더한다.
갈래는 셋 — 코인 · 국내주식 · 미국주식.

    view       그 갈래의 화면(콘솔 칩 · 판 목록 · 스위치)이 열린다
    backtest   그 갈래 매매법의 근거 화면
    trade      그 갈래에서 판·펀드를 연다 — 서버 축(demo/live)은 `Cap` 이,
               매매법 축은 T230 이 따로 본다

계산 규칙은 매매법과 같다:
    grant(사람, 갈래) = 사람별 덮어쓰기 행 > 묶음 정책(`MarketPolicy`)
    감사(`Cap.AUDIT`) 는 view·backtest 를 전부 연다 — trade 는 그대로
    view 없는 backtest/trade 는 금지

기본값(사용자 초안 5 · 권장안): 게스트 = 모든 시장 보기 · 코인만 거래(데모) · 주식 거래 없음 /
열람자 = 보기만 / 거래자·관리자 = 전부. **브로커 자격(토스 키)은 아직 이 축에 안 묶는다** —
주식은 페이퍼뿐이고 실주문 관문은 `STOCK_LIVE_ORDERS` 가 따로 막는다(T240).
자격이 생기면 그 시장의 trade 를 자격 유무로 다시 조인다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from updown.common.domain.instrument import Market, MarketGroup

Group = Literal["coin", "domestic", "foreign"]
GROUPS: tuple[Group, ...] = ("coin", "domestic", "foreign")
GROUP_LABELS: dict[str, str] = {"coin": "코인", "domestic": "국내주식", "foreign": "미국주식"}
Kind = Literal["view", "backtest", "trade"]
KINDS: tuple[Kind, ...] = ("view", "backtest", "trade")
ALL = "*"
Scope = frozenset[str] | str


def group_of(market: Market) -> Group:
    """시장 → 권한 갈래.

    Args:
        market: 시장.

    Returns:
        `coin` · `domestic` · `foreign`.
    """
    found = MarketGroup.of(market)
    if found is MarketGroup.COIN:
        return "coin"
    if found is MarketGroup.DOMESTIC_STOCK:
        return "domestic"
    return "foreign"


def _scope_from(raw: object) -> Scope:
    """표 값 하나를 범위로 — `"*"`/`"all"` 은 전부, 목록은 **아는 갈래만**, 그 밖은 빈 범위.

    None·오타·모르는 갈래 이름이 문을 여는 쪽으로 읽히면 안 된다 — 모르는 모양은 전부 "없음" 이다
    (`MarketPolicy.from_json` 과 같은 원칙).

    Args:
        raw: JSON 에서 읽은 값.

    Returns:
        `"*"` 또는 갈래 집합.
    """
    if raw == ALL or raw == "all":
        return ALL
    if isinstance(raw, list | tuple | set | frozenset):
        items = cast("Iterable[object]", raw)
        return frozenset(str(item) for item in items if str(item) in GROUPS)
    return frozenset()


def _scope_json(scope: Scope) -> str | list[str]:
    """범위를 표·API 모양으로 — 목록은 정렬해 같은 정책이 같은 글자가 되게."""
    return ALL if scope == ALL else sorted(cast("frozenset[str]", scope))


@dataclass(frozen=True, slots=True)
class MarketPolicy:
    """묶음의 시장 기본값 — 칸마다 "전부" 또는 갈래 목록.

    Attributes:
        view: 볼 수 있는 갈래.
        backtest: 백테스트를 볼 수 있는 갈래.
        trade: 거래할 수 있는 갈래.
    """

    view: Scope = frozenset()
    backtest: Scope = frozenset()
    trade: Scope = frozenset()

    def allows(self, kind: Kind, group: str) -> bool:
        """이 칸이 그 갈래를 허용하나.

        Args:
            kind: 칸.
            group: 갈래.

        Returns:
            `"*"` 이거나 목록에 있으면 참.
        """
        scope: Scope = getattr(self, kind)
        return scope == ALL or group in cast("frozenset[str]", scope)

    @classmethod
    def from_json(cls, raw: object) -> MarketPolicy:
        """표(JSON)에서 읽는다 — 모르는 모양은 빈 정책 (문을 열지 않는다).

        Args:
            raw: `{"view": "*" | [갈래], "backtest": ..., "trade": ...}` 또는 None.

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
        """표·API 로 나가는 모양.

        Returns:
            `{"view": "*" | [갈래], "backtest": ..., "trade": ...}`.
        """
        return {
            "view": _scope_json(self.view),
            "backtest": _scope_json(self.backtest),
            "trade": _scope_json(self.trade),
        }


@dataclass(frozen=True, slots=True)
class MarketGrant:
    """한 사람의 한 갈래에 대한 유효 권한 (또는 덮어쓰기 행).

    Attributes:
        group: 갈래.
        view: 본다.
        backtest: 백테스트를 본다.
        trade: 거래한다.
    """

    group: str
    view: bool
    backtest: bool
    trade: bool

    def validate(self) -> None:
        """`view` 없는 `backtest`/`trade` 는 뜻이 없다.

        Raises:
            ValueError: 그 조합이거나 모르는 갈래.
        """
        if self.group not in GROUPS:
            raise ValueError(f"모르는 시장 갈래다 — {self.group!r} (coin · domestic · foreign)")
        if (self.backtest or self.trade) and not self.view:
            raise ValueError(
                f"{GROUP_LABELS.get(self.group, self.group)}: 보기(view) 없이 "
                "백테스트·거래만 줄 수 없다"
            )

    def as_json(self) -> dict[str, Any]:
        """API 로 나가는 모양.

        Returns:
            `{id, label, view, backtest, trade}`.
        """
        return {
            "id": self.group,
            "label": GROUP_LABELS.get(self.group, self.group),
            "view": self.view,
            "backtest": self.backtest,
            "trade": self.trade,
        }


def effective_market_grant(
    group: str, *, policy: MarketPolicy, row: MarketGrant | None, audit: bool
) -> MarketGrant:
    """유효 권한 — 사람별 행 > 묶음 정책, 감사는 view·backtest 를 전부 연다.

    Args:
        group: 갈래.
        policy: 묶음 정책.
        row: 사람별 덮어쓰기 행. None 이면 정책.
        audit: 감사 기능을 쥐었나.

    Returns:
        유효 권한.
    """
    base = (
        row
        if row is not None
        else MarketGrant(
            group,
            view=policy.allows("view", group),
            backtest=policy.allows("backtest", group),
            trade=policy.allows("trade", group),
        )
    )
    if audit:
        base = replace(base, view=True, backtest=True)
    return base


_COIN: Scope = frozenset({"coin"})
_NONE: Scope = frozenset()
BUILTIN_MARKET_POLICIES: dict[str, MarketPolicy] = {
    # 게스트 — 모든 시장을 보고, 데모에서 코인만 거래. 주식 거래 없음 (사용자 초안 5).
    "guest": MarketPolicy(view=ALL, backtest=ALL, trade=_COIN),
    "live_watch_guest": MarketPolicy(view=ALL, backtest=ALL, trade=_NONE),
    "viewer": MarketPolicy(view=ALL, backtest=ALL, trade=_NONE),
    # 거래자 — 지금은 전부. 브로커 자격이 생기면 그 시장의 trade 를 자격 유무로 다시 조인다.
    "trader": MarketPolicy(view=ALL, backtest=ALL, trade=ALL),
    "admin": MarketPolicy(view=ALL, backtest=ALL, trade=ALL),
    "super_admin": MarketPolicy(view=ALL, backtest=ALL, trade=ALL),
}
"""내장 묶음의 시장 기본값 — 표(`role_collections.market_policy`)가 비었을 때."""
DEFAULT_MARKET_POLICY = MarketPolicy(view=ALL, backtest=ALL, trade=_NONE)
"""묶음이 없는 사람 — 보기·백테스트만."""

__all__ = [
    "ALL",
    "BUILTIN_MARKET_POLICIES",
    "DEFAULT_MARKET_POLICY",
    "GROUPS",
    "GROUP_LABELS",
    "KINDS",
    "Group",
    "Kind",
    "MarketGrant",
    "MarketPolicy",
    "effective_market_grant",
    "group_of",
]
