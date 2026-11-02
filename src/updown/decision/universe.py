"""분기 종목 우주 — 거래대금 순위 · 씨앗 추출 · 다리별 목록 (T758).

"이번 분기에 각 다리가 어느 종목에서 새로 들어갈 수 있나" 는 자본 배분 판단이라 이 계층에 둔다
(README §4.7 · 선례 `allocation.rank_members`). 자료를 읽거나 판을 띄우지 않는다 — 일봉 거래대금을
받아 목록을 내는 순수 함수뿐이다. 값은 전부 설정(`config/universe.yml`)에서 온다.

🔴 **미래 봉을 보지 않는다.** 분기 첫날 q 이전의 일봉만 쓴다. q 뒤에 되살아난 종목은 그 분기에
거래 중이 아니다.

🔴 **임의 추출은 모집단 경계에 민감하다.** 순위 경계에서 한 칸만 바뀌어도 표본 일부가 바뀐다. 그래서
부분 자료로 계산하지 않고(부르는 쪽 책임), 모집단 정렬 키는 순위 자료의 심볼 이름 그대로이며,
입력 지문을 스냅샷에 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import yaml

DEFAULT_CONFIG_PATH = Path("config/universe.yml")

RuleName = Literal[
    "top", "sample", "anchor_plus_sample", "anchor_union_top", "anchor_plus_rest_top", "static"
]
_RULES: frozenset[str] = frozenset(
    {"top", "sample", "anchor_plus_sample", "anchor_union_top", "anchor_plus_rest_top", "static"}
)
_ANCHOR_RULES: frozenset[str] = frozenset(
    {"sample", "anchor_plus_sample", "anchor_union_top", "anchor_plus_rest_top"}
)
_SAMPLE_RULES: frozenset[str] = frozenset({"sample", "anchor_plus_sample"})
BOUNDARY_RANKS: tuple[int, ...] = (6, 40, 100)
"""경계 간격을 남기는 순위 — "왜 이 분기 목록이 달라졌나" 를 바로 답하려고 적는다."""


class UniverseConfigError(ValueError):
    """우주 설정이 잘못됐다 — 조용히 기본값으로 돌지 않는다 (규칙 #8)."""


class UniverseDataError(ValueError):
    """이 자료로는 목록을 못 낸다 — 부르는 쪽이 이전 분기 목록을 유지한다 (규칙 #8)."""


@dataclass(frozen=True, slots=True)
class RankRule:
    """순위 · 자격 규칙.

    Attributes:
        lookback_days: 순위 창 길이(q 직전 이 일수 · q 는 안 든다).
        min_days: 창 안에 거래대금이 있는 날의 최소 수.
        live_within_days: q 전 마지막 봉이 이 일수 안이어야 거래 중이다.
        alive_days: 살아 있음 판정 창 길이.
        alive_min_days: 살아 있음 창 안 최소 일수.
        min_ranked: 순위 종목이 이보다 적으면 목록을 내지 않는다.
        exclude: 순위에서 뺄 기초 자산 이름.
        quote: 심볼 끝의 결제 통화(`BTCUSDT` 의 `USDT`).
    """

    lookback_days: int
    min_days: int
    live_within_days: int
    alive_days: int
    alive_min_days: int
    min_ranked: int
    exclude: frozenset[str]
    quote: str = "USDT"


@dataclass(frozen=True, slots=True)
class LegRule:
    """다리 하나의 우주 규칙.

    Attributes:
        rule: `top`(순위 위 n) · `sample`(모집단 임의 n) · `anchor_plus_sample`(닻 + 임의 n) ·
            `anchor_union_top`(닻 합집합 순위 위 n) · `anchor_plus_rest_top`(닻 + 닻 뺀 순위 위 n) ·
            `static`(`symbols` 그대로).
        n: 개수.
        anchor: 닻 이름 — `anchors` 표의 열쇠 또는 `top_dynamic`.
        pool_top: 모집단이 될 순위 범위.
        exclude_sets: 모집단에서 뺄 이름 표들(`excludes` 표의 열쇠).
        exclude_legs: 모집단에서 뺄 앞 다리의 **표본**.
        require_alive: 모집단을 살아 있는 종목으로 제한.
        salt: 씨앗 꼬리.
        symbols: `static` 의 종목.
    """

    rule: RuleName
    n: int = 0
    anchor: str = ""
    pool_top: int = 0
    exclude_sets: tuple[str, ...] = ()
    exclude_legs: tuple[str, ...] = ()
    require_alive: bool = False
    salt: str = ""
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    """우주 설정 한 벌 — 고른 프로필의 다리들.

    Attributes:
        profile: 프로필 이름.
        seed_tag: 씨앗 벌 이름.
        rank: 순위 규칙.
        anchors: 닻 표 `{이름: 심볼들}`.
        excludes: 빼기 표 `{이름: 심볼들}`.
        top_anchor_n: `top_dynamic` 닻의 개수.
        legs: 다리들(적힌 순서 — `exclude_legs` 는 앞 다리만 가리킨다).
    """

    profile: str
    seed_tag: str
    rank: RankRule
    anchors: Mapping[str, frozenset[str]]
    excludes: Mapping[str, frozenset[str]]
    top_anchor_n: int
    legs: Mapping[str, LegRule]

    def digest(self) -> str:
        """규칙 지문 — 값이 바뀌면 다른 규칙이다."""
        body = {
            "profile": self.profile,
            "seed_tag": self.seed_tag,
            "rank": {
                "lookback_days": self.rank.lookback_days,
                "min_days": self.rank.min_days,
                "live_within_days": self.rank.live_within_days,
                "alive_days": self.rank.alive_days,
                "alive_min_days": self.rank.alive_min_days,
                "min_ranked": self.rank.min_ranked,
                "exclude": sorted(self.rank.exclude),
                "quote": self.rank.quote,
            },
            "anchors": {k: sorted(v) for k, v in self.anchors.items()},
            "excludes": {k: sorted(v) for k, v in self.excludes.items()},
            "top_anchor_n": self.top_anchor_n,
            "legs": {
                k: {
                    "rule": v.rule,
                    "n": v.n,
                    "anchor": v.anchor,
                    "pool_top": v.pool_top,
                    "exclude_sets": list(v.exclude_sets),
                    "exclude_legs": list(v.exclude_legs),
                    "require_alive": v.require_alive,
                    "salt": v.salt,
                    "symbols": list(v.symbols),
                }
                for k, v in self.legs.items()
            },
        }
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SymbolDaily:
    """심볼 하나의 일봉 거래대금(입력).

    Attributes:
        symbol: 순위 자료의 심볼 이름(`BTCUSDT`).
        quote_by_day: UTC 일 → 결제 통화 거래대금. 0 이하는 봉이 없는 날로 친다.
        is_coin: 코인 자산인가.
        is_perpetual: 무기한 계약인가.
    """

    symbol: str
    quote_by_day: Mapping[date, float]
    is_coin: bool = True
    is_perpetual: bool = True


@dataclass(frozen=True, slots=True)
class SymbolFacts:
    """분기 첫날 기준 심볼 하나의 사실 — q 이전 봉으로만 만든다.

    Attributes:
        symbol: 심볼.
        quote_sum: 순위 창 거래대금 합.
        days_in_window: 순위 창 안 거래대금이 있는 날 수.
        last_day: q 전 마지막 봉 날짜. 없으면 None.
        recent_days: 살아 있음 창 안 거래대금이 있는 날 수.
        is_coin: 코인 자산인가.
        is_perpetual: 무기한 계약인가.
    """

    symbol: str
    quote_sum: float
    days_in_window: int
    last_day: date | None
    recent_days: int
    is_coin: bool = True
    is_perpetual: bool = True


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    """한 분기의 우주(불변 기록).

    Attributes:
        quarter: 분기 첫날.
        profile: 프로필 이름.
        seed_tag: 씨앗 벌 이름.
        rule_digest: 규칙 지문.
        input_digest: 입력 지문.
        ranked: 순위 심볼(높은 순).
        ranked_quote: 순위 심볼의 창 거래대금.
        alive: 살아 있는 심볼(정렬).
        boundary_gaps: `{경계 순위: 그 순위와 다음 순위의 상대 차}`.
        legs: `{다리: 심볼들(정렬)}`.
    """

    quarter: date
    profile: str
    seed_tag: str
    rule_digest: str
    input_digest: str
    ranked: tuple[str, ...]
    ranked_quote: tuple[float, ...]
    alive: tuple[str, ...]
    boundary_gaps: Mapping[int, float]
    legs: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])


def quarter_start(day: date) -> date:
    """그 날이 속한 UTC 분기의 첫날."""
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _require_quarter(q: date) -> None:
    if q != quarter_start(q):
        raise ValueError(f"{q} 는 분기 첫날이 아니다")


def facts_of(daily: Iterable[SymbolDaily], q: date, rule: RankRule) -> tuple[SymbolFacts, ...]:
    """일봉 표에서 q 기준 사실을 만든다 — 🔴 q 이후 봉은 어떤 값에도 안 든다.

    Args:
        daily: 심볼들의 일봉 거래대금.
        q: 분기 첫날.
        rule: 순위 규칙.

    Returns:
        q 전에 봉이 하나라도 있는 심볼의 사실(심볼 이름 순).

    Raises:
        ValueError: q 가 분기 첫날이 아닐 때.
    """
    _require_quarter(q)
    lo = q - timedelta(days=rule.lookback_days)
    recent_lo = q - timedelta(days=rule.alive_days)
    out: list[SymbolFacts] = []
    for row in daily:
        days = [d for d, v in row.quote_by_day.items() if v > 0 and d < q]
        if not days:
            continue
        window = [d for d in days if d >= lo]
        out.append(
            SymbolFacts(
                symbol=row.symbol,
                quote_sum=math.fsum(row.quote_by_day[d] for d in window),
                days_in_window=len(window),
                last_day=max(days),
                recent_days=sum(1 for d in days if d >= recent_lo),
                is_coin=row.is_coin,
                is_perpetual=row.is_perpetual,
            )
        )
    out.sort(key=lambda f: f.symbol)
    return tuple(out)


def _is_live(fact: SymbolFacts, q: date, rule: RankRule) -> bool:
    if fact.last_day is None:
        return False
    return fact.last_day >= q - timedelta(days=rule.live_within_days)


def ranked_of(
    facts: Iterable[SymbolFacts], q: date, rule: RankRule
) -> tuple[tuple[str, float], ...]:
    """거래대금 순위 — 코인 · 자격 · 거래 중 · 빼는 이름 뺌.

    Args:
        facts: 심볼 사실들.
        q: 분기 첫날.
        rule: 순위 규칙.

    Returns:
        `(심볼, 창 거래대금)` 높은 순. 값이 같으면 심볼 이름 **내림차순**이다 — 측정에 쓴
        순위표와 같은 순서여야 같은 표본이 나온다.

    Note:
        비 ASCII 심볼은 순위에서 빼지 않는다. 자리를 차지하는 것까지가 규칙이고, 뺄 곳은
        `alive_of` 다.
    """
    rows = [
        (f.quote_sum, f.symbol)
        for f in facts
        if f.is_coin
        and f.symbol.removesuffix(rule.quote) not in rule.exclude
        and f.days_in_window >= rule.min_days
        and f.quote_sum > 0
        and _is_live(f, q, rule)
    ]
    rows.sort(reverse=True)
    return tuple((s, v) for v, s in rows)


def alive_of(facts: Iterable[SymbolFacts], q: date, rule: RankRule) -> frozenset[str]:
    """그 분기 살아 있는 심볼 — 무기한 코인 · ASCII · 최근 봉 · 거래 중."""
    return frozenset(
        f.symbol
        for f in facts
        if f.is_coin
        and f.is_perpetual
        and f.symbol.isascii()
        and f.recent_days >= rule.alive_min_days
        and _is_live(f, q, rule)
    )


def _seeded_sample(pool: Sequence[str], n: int, seed: str) -> frozenset[str]:
    """모집단(정렬됨)에서 정해진 씨앗으로 n 개 — 전역 난수를 건드리지 않는다 (규칙 #5)."""
    return frozenset(random.Random(seed).sample(list(pool), min(n, len(pool))))


def _anchor_of(leg: LegRule, cfg: UniverseConfig, top: tuple[str, ...]) -> frozenset[str]:
    if leg.anchor == "top_dynamic":
        return frozenset(top[: cfg.top_anchor_n])
    return cfg.anchors[leg.anchor]


def _pool_of(
    leg: LegRule,
    cfg: UniverseConfig,
    names: tuple[str, ...],
    alive: frozenset[str],
    anchor: frozenset[str],
    samples: Mapping[str, frozenset[str]],
) -> list[str]:
    barred: set[str] = set(anchor)
    for key in leg.exclude_sets:
        barred |= cfg.excludes[key]
    for other in leg.exclude_legs:
        barred |= samples.get(other, frozenset())
    return sorted(
        s
        for s in names[: leg.pool_top]
        if s not in barred and (not leg.require_alive or s in alive)
    )


def legs_of(
    cfg: UniverseConfig, q: date, names: tuple[str, ...], alive: frozenset[str]
) -> dict[str, tuple[str, ...]]:
    """순위 · 살아 있음에서 다리별 목록을 낸다.

    Args:
        cfg: 설정.
        q: 분기 첫날.
        names: 순위 심볼(높은 순).
        alive: 살아 있는 심볼.

    Returns:
        `{다리: 심볼들(정렬)}`.
    """
    out: dict[str, tuple[str, ...]] = {}
    samples: dict[str, frozenset[str]] = {}
    for name, leg in cfg.legs.items():
        if leg.rule == "static":
            out[name] = tuple(sorted(leg.symbols))
            continue
        if leg.rule == "top":
            out[name] = tuple(sorted(names[: leg.n]))
            continue
        anchor = _anchor_of(leg, cfg, names)
        if leg.rule in _SAMPLE_RULES:
            pool = _pool_of(leg, cfg, names, alive, anchor, samples)
            picked = _seeded_sample(pool, leg.n, f"{cfg.seed_tag}|{q.isoformat()}|{leg.salt}")
            samples[name] = picked
            members = picked if leg.rule == "sample" else picked | anchor
        elif leg.rule == "anchor_union_top":
            members = anchor | frozenset(names[: leg.n])
        else:
            members = anchor | frozenset([s for s in names if s not in anchor][: leg.n])
        out[name] = tuple(sorted(members))
    return out


def _input_digest(facts: Iterable[SymbolFacts]) -> str:
    raw = json.dumps(
        [
            [
                f.symbol,
                repr(f.quote_sum),
                f.days_in_window,
                None if f.last_day is None else f.last_day.isoformat(),
                f.recent_days,
                f.is_coin,
                f.is_perpetual,
            ]
            for f in sorted(facts, key=lambda f: f.symbol)
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def universe_from_facts(
    facts: Sequence[SymbolFacts], q: date, cfg: UniverseConfig
) -> UniverseSnapshot:
    """사실에서 한 분기의 우주를 낸다.

    Args:
        facts: `facts_of` 의 결과.
        q: 분기 첫날.
        cfg: 설정.

    Returns:
        스냅샷.

    Raises:
        UniverseDataError: 순위 종목이 `min_ranked` 보다 적다 — 이전 목록을 유지할 일이다.
        ValueError: q 가 분기 첫날이 아닐 때.
    """
    _require_quarter(q)
    ranked = ranked_of(facts, q, cfg.rank)
    if len(ranked) < cfg.rank.min_ranked:
        raise UniverseDataError(f"{q} 순위 종목 {len(ranked)} < {cfg.rank.min_ranked}")
    names = tuple(s for s, _ in ranked)
    values = tuple(v for _, v in ranked)
    alive = alive_of(facts, q, cfg.rank)
    gaps = {
        k: (values[k - 1] - values[k]) / values[k - 1]
        for k in BOUNDARY_RANKS
        if len(values) > k and values[k - 1] > 0
    }
    return UniverseSnapshot(
        quarter=q,
        profile=cfg.profile,
        seed_tag=cfg.seed_tag,
        rule_digest=cfg.digest(),
        input_digest=_input_digest(facts),
        ranked=names,
        ranked_quote=values,
        alive=tuple(sorted(alive)),
        boundary_gaps=gaps,
        legs=legs_of(cfg, q, names, alive),
    )


def universe_for(daily: Iterable[SymbolDaily], q: date, cfg: UniverseConfig) -> UniverseSnapshot:
    """일봉 표에서 한 분기의 우주를 낸다 — `facts_of` + `universe_from_facts`."""
    return universe_from_facts(facts_of(daily, q, cfg.rank), q, cfg)


def _names(raw: object, quote: str, where: str) -> frozenset[str]:
    if not isinstance(raw, list):
        raise UniverseConfigError(f"{where} 가 목록이 아니다")
    return frozenset(f"{item}{quote}" for item in cast("list[object]", raw))


def _str_tuple(raw: object, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise UniverseConfigError(f"{where} 가 목록이 아니다")
    return tuple(str(item) for item in cast("list[object]", raw))


def _leg_rule(name: str, raw: object) -> LegRule:
    if not isinstance(raw, dict):
        raise UniverseConfigError(f"다리 {name} 가 매핑이 아니다")
    body = cast("dict[str, Any]", raw)
    rule = str(body.get("rule", ""))
    if rule not in _RULES:
        raise UniverseConfigError(f"다리 {name} 의 rule {rule!r} 를 모른다")
    leg = LegRule(
        rule=cast("RuleName", rule),
        n=int(body.get("n", 0)),
        anchor=str(body.get("anchor", "")),
        pool_top=int(body.get("pool_top", 0)),
        exclude_sets=_str_tuple(body.get("exclude_sets"), f"{name}.exclude_sets"),
        exclude_legs=_str_tuple(body.get("exclude_legs"), f"{name}.exclude_legs"),
        require_alive=bool(body.get("require_alive", False)),
        salt=str(body.get("salt", "")),
        symbols=_str_tuple(body.get("symbols"), f"{name}.symbols"),
    )
    if rule == "static":
        if not leg.symbols:
            raise UniverseConfigError(f"다리 {name} — static 인데 symbols 가 없다")
        return leg
    if leg.n <= 0:
        raise UniverseConfigError(f"다리 {name} — n 이 0 이하다")
    if rule in _ANCHOR_RULES and not leg.anchor:
        raise UniverseConfigError(f"다리 {name} — anchor 가 없다")
    if rule in _SAMPLE_RULES and (leg.pool_top <= 0 or not leg.salt):
        raise UniverseConfigError(f"다리 {name} — 추출 다리에 pool_top · salt 가 없다")
    return leg


def _validate(cfg: UniverseConfig) -> None:
    seen: set[str] = set()
    for name, leg in cfg.legs.items():
        if (
            leg.rule in _ANCHOR_RULES
            and leg.anchor != "top_dynamic"
            and leg.anchor not in cfg.anchors
        ):
            raise UniverseConfigError(f"다리 {name} 의 anchor {leg.anchor!r} 가 표에 없다")
        for key in leg.exclude_sets:
            if key not in cfg.excludes:
                raise UniverseConfigError(f"다리 {name} 의 exclude_sets {key!r} 가 표에 없다")
        for other in leg.exclude_legs:
            if other not in seen:
                raise UniverseConfigError(
                    f"다리 {name} 의 exclude_legs {other!r} 는 앞 다리가 아니다"
                )
        seen.add(name)


def load_universe_config(
    path: Path = DEFAULT_CONFIG_PATH, *, profile: str | None = None, seed_tag: str | None = None
) -> UniverseConfig:
    """우주 설정을 읽는다.

    Args:
        path: 설정 파일.
        profile: 프로필을 바꿔 읽는다(연구 대조용). None 이면 파일의 `active_profile`.
        seed_tag: 씨앗 벌을 바꿔 읽는다(연구 대조용). None 이면 파일의 값.

    Returns:
        설정.

    Raises:
        UniverseConfigError: 파일이 없거나 · 모양이 틀리거나 · 프로필이 없을 때.
    """
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UniverseConfigError(f"{path} 를 못 읽었다: {exc}") from exc
    if not isinstance(raw, dict):
        raise UniverseConfigError(f"{path} 가 매핑이 아니다")
    top = cast("dict[str, Any]", raw)
    try:
        body_rank = cast("dict[str, Any]", top["rank"])
        quote = str(body_rank.get("quote", "USDT"))
        rank = RankRule(
            lookback_days=int(body_rank["lookback_days"]),
            min_days=int(body_rank["min_days"]),
            live_within_days=int(body_rank["live_within_days"]),
            alive_days=int(body_rank["alive_days"]),
            alive_min_days=int(body_rank["alive_min_days"]),
            min_ranked=int(body_rank["min_ranked"]),
            exclude=frozenset(str(x) for x in cast("list[object]", body_rank["exclude"])),
            quote=quote,
        )
        chosen = profile or str(top["active_profile"])
        profiles = cast("dict[str, Any]", top["profiles"])
        if chosen not in profiles:
            raise UniverseConfigError(f"프로필 {chosen!r} 가 없다 ({sorted(profiles)})")
        legs_raw = cast("dict[str, Any]", profiles[chosen])
        cfg = UniverseConfig(
            profile=chosen,
            seed_tag=seed_tag or str(cast("dict[str, Any]", top["seed"])["tag"]),
            rank=rank,
            anchors={
                str(k): _names(v, quote, f"anchors.{k}")
                for k, v in cast("dict[str, Any]", top.get("anchors") or {}).items()
            },
            excludes={
                str(k): _names(v, quote, f"excludes.{k}")
                for k, v in cast("dict[str, Any]", top.get("excludes") or {}).items()
            },
            top_anchor_n=int(top.get("top_anchor_n", 0)),
            legs={str(k): _leg_rule(str(k), v) for k, v in legs_raw.items()},
        )
        # 🔴 고르지 않은 프로필도 잰다 — 한 줄로 바꾸는 날 처음 터지면 그때는 교체 절차 한가운데다.
        for other_name, other_raw in profiles.items():
            _validate(
                replace(
                    cfg,
                    legs={
                        str(k): _leg_rule(f"{other_name}.{k}", v)
                        for k, v in cast("dict[str, Any]", other_raw).items()
                    },
                )
            )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, UniverseConfigError):
            raise
        raise UniverseConfigError(f"{path} 모양이 틀렸다: {exc!r}") from exc
    _validate(cfg)
    return cfg
