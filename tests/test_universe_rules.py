"""분기 종목 우주 순수 함수 (T758 U1 · U2) — 설정 파일 없이 값을 직접 넣는다."""

from __future__ import annotations

import dataclasses
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from updown.decision.universe import (
    LegRule,
    RankRule,
    SymbolDaily,
    SymbolFacts,
    UniverseConfig,
    UniverseConfigError,
    UniverseDataError,
    alive_of,
    facts_of,
    legs_of,
    load_universe_config,
    quarter_start,
    ranked_of,
    universe_for,
    universe_from_facts,
)

Q = date(2026, 10, 1)
RULE = RankRule(
    lookback_days=365,
    min_days=90,
    live_within_days=3,
    alive_days=30,
    alive_min_days=7,
    min_ranked=1,
    exclude=frozenset({"USDC"}),
)


def _days(q: date, back: int, value: float = 1.0, *, gap: int = 1) -> dict[date, float]:
    """q 직전 `back` 일 — q 에서 gap 일 앞에서 끝난다."""
    last = q - timedelta(days=gap)
    return {last - timedelta(days=i): value for i in range(back)}


def _fact(symbol: str, value: float, **kw: object) -> SymbolFacts:
    base: dict[str, object] = {
        "symbol": symbol,
        "quote_sum": value,
        "days_in_window": 200,
        "last_day": Q - timedelta(days=1),
        "recent_days": 30,
    }
    base.update(kw)
    return SymbolFacts(**base)  # type: ignore[arg-type]


def _names(rows: tuple[tuple[str, float], ...]) -> list[str]:
    return [s for s, _ in rows]


# ── U1 사실 · 자격 경계 ────────────────────────────────────────────────


def test_facts_ignore_days_on_or_after_q() -> None:
    """🔴 미래 봉은 합 · 일수 · 마지막 봉 어디에도 안 든다."""
    by = {Q - timedelta(days=1): 5.0, Q: 100.0, Q + timedelta(days=9): 100.0}
    got = facts_of([SymbolDaily("AUSDT", by)], Q, RULE)
    assert got[0].quote_sum == 5.0
    assert got[0].days_in_window == 1
    assert got[0].last_day == Q - timedelta(days=1)


def test_relisted_after_q_is_not_live() -> None:
    """q 전에 끊겼다가 q 뒤에 되살아나도 그 분기 거래 중이 아니다(연구 표의 미래 참조 안 옮김)."""
    by = _days(Q, 200, gap=50) | {Q + timedelta(days=30): 9.0}
    facts = facts_of([SymbolDaily("AUSDT", by)], Q, RULE)
    assert ranked_of(facts, Q, RULE) == ()
    assert alive_of(facts, Q, RULE) == frozenset()


@pytest.mark.parametrize(("back", "kept"), [(89, False), (90, True)])
def test_min_days_boundary(back: int, kept: bool) -> None:
    facts = facts_of([SymbolDaily("AUSDT", _days(Q, back))], Q, RULE)
    assert bool(ranked_of(facts, Q, RULE)) is kept


@pytest.mark.parametrize(("gap", "kept"), [(3, True), (4, False)])
def test_live_within_boundary(gap: int, kept: bool) -> None:
    facts = facts_of([SymbolDaily("AUSDT", _days(Q, 200, gap=gap))], Q, RULE)
    assert bool(ranked_of(facts, Q, RULE)) is kept


def test_window_excludes_older_than_lookback() -> None:
    by = {Q - timedelta(days=365): 1.0, Q - timedelta(days=366): 1000.0}
    got = facts_of([SymbolDaily("AUSDT", by)], Q, RULE)[0]
    assert got.quote_sum == 1.0


def test_zero_volume_day_is_no_bar() -> None:
    by = {Q - timedelta(days=1): 0.0, Q - timedelta(days=5): 2.0}
    got = facts_of([SymbolDaily("AUSDT", by)], Q, RULE)[0]
    assert got.last_day == Q - timedelta(days=5)
    assert got.days_in_window == 1


def test_not_quarter_start_raises() -> None:
    with pytest.raises(ValueError, match="분기 첫날"):
        facts_of([], date(2026, 10, 2), RULE)


def test_quarter_start() -> None:
    assert quarter_start(date(2026, 12, 31)) == date(2026, 10, 1)
    assert quarter_start(date(2026, 1, 1)) == date(2026, 1, 1)


# ── U1 순위 ──────────────────────────────────────────────────────────────


def test_rank_order_and_tie_is_symbol_descending() -> None:
    """값이 같으면 심볼 내림차순 — 측정에 쓴 표 `(값, 심볼)` 역정렬과 같다."""
    got = ranked_of([_fact("AUSDT", 5.0), _fact("BUSDT", 5.0), _fact("CUSDT", 9.0)], Q, RULE)
    assert _names(got) == ["CUSDT", "BUSDT", "AUSDT"]


def test_rank_filters() -> None:
    facts = [
        _fact("USDCUSDT", 9.0),
        _fact("STOCKUSDT", 8.0, is_coin=False),
        _fact("OKUSDT", 1.0),
    ]
    assert _names(ranked_of(facts, Q, RULE)) == ["OKUSDT"]


def test_non_ascii_stays_in_rank_but_not_alive() -> None:
    """비 ASCII 는 순위 자리를 차지하고(경계가 밀리지 않게) 살아 있음에서만 빠진다."""
    facts = [_fact("币安人生USDT", 9.0), _fact("OKUSDT", 1.0)]
    assert _names(ranked_of(facts, Q, RULE)) == ["币安人生USDT", "OKUSDT"]
    assert alive_of(facts, Q, RULE) == frozenset({"OKUSDT"})


@pytest.mark.parametrize(
    ("kw", "alive"),
    [
        ({"recent_days": 6}, False),
        ({"recent_days": 7}, True),
        ({"is_perpetual": False}, False),
        ({"is_coin": False}, False),
        ({"last_day": Q - timedelta(days=4)}, False),
    ],
)
def test_alive_rules(kw: dict[str, object], alive: bool) -> None:
    assert (alive_of([_fact("AUSDT", 1.0, **kw)], Q, RULE) != frozenset()) is alive


# ── 다리 규칙 ─────────────────────────────────────────────────────────────


NAMES = tuple(f"S{i:03d}USDT" for i in range(1, 121))
ANCHOR = frozenset({"S003USDT", "S200USDT"})  # S200 은 순위 밖이어도 닻이다


def _cfg(legs: dict[str, LegRule], tag: str = "T1") -> UniverseConfig:
    return UniverseConfig(
        profile="p",
        seed_tag=tag,
        rank=RULE,
        anchors={"core": ANCHOR},
        excludes={"bar": frozenset({"S010USDT"})},
        top_anchor_n=2,
        legs=legs,
    )


def test_top_rule() -> None:
    got = legs_of(_cfg({"a": LegRule("top", n=3)}), Q, NAMES, frozenset())
    assert got["a"] == ("S001USDT", "S002USDT", "S003USDT")


def test_union_top_keeps_anchor_outside_rank() -> None:
    got = legs_of(
        _cfg({"a": LegRule("anchor_union_top", n=2, anchor="core")}), Q, NAMES, frozenset()
    )
    assert set(got["a"]) == {"S001USDT", "S002USDT", "S003USDT", "S200USDT"}


def test_rest_top_skips_anchor_when_counting() -> None:
    got = legs_of(
        _cfg({"a": LegRule("anchor_plus_rest_top", n=3, anchor="core")}), Q, NAMES, frozenset()
    )
    assert set(got["a"]) == {"S001USDT", "S002USDT", "S004USDT", "S003USDT", "S200USDT"}


def test_sample_matches_plain_random_on_sorted_pool() -> None:
    """같은 씨앗 문자열 · 정렬 모집단 → 표준 `random.Random(str).sample` 과 같다."""
    leg = LegRule("anchor_plus_sample", n=4, anchor="core", pool_top=10, salt="t")
    got = legs_of(_cfg({"a": leg}, tag="BRA7"), Q, NAMES, frozenset())
    pool = sorted(s for s in NAMES[:10] if s not in ANCHOR)
    want = set(random.Random(f"BRA7|{Q.isoformat()}|t").sample(pool, 4)) | ANCHOR
    assert set(got["a"]) == want


def test_sample_excludes_anchor_sets_earlier_sample_and_dead() -> None:
    legs = {
        "tri": LegRule("anchor_plus_sample", n=4, anchor="core", pool_top=20, salt="t"),
        "mac": LegRule(
            "sample",
            n=50,
            anchor="core",
            pool_top=30,
            salt="m",
            exclude_sets=("bar",),
            exclude_legs=("tri",),
            require_alive=True,
        ),
    }
    alive = frozenset(NAMES) - {"S020USDT"}
    got = legs_of(_cfg(legs), Q, NAMES, alive)
    tri_sample = set(got["tri"]) - ANCHOR
    mac = set(got["mac"])
    assert not mac & ANCHOR
    assert "S010USDT" not in mac
    assert "S020USDT" not in mac
    assert not mac & tri_sample
    # n 이 모집단보다 크면 모집단 전부 — 표본 크기 오류로 멈추지 않는다
    barred = {"S003USDT", "S010USDT", "S020USDT"} | tri_sample
    assert mac == {s for s in NAMES[:30] if s not in barred}


def test_sample_exclude_leg_is_sample_not_anchor() -> None:
    """빼는 것은 앞 다리의 **표본**이다 — 닻까지 빼지 않는다(닻 없는 뒤 다리에서 드러난다)."""
    legs = {
        "tri": LegRule("anchor_plus_sample", n=1, anchor="core", pool_top=5, salt="t"),
        "mac": LegRule(
            "sample", n=100, anchor="other", pool_top=5, salt="m", exclude_legs=("tri",)
        ),
    }
    cfg = dataclasses.replace(_cfg(legs), anchors={"core": ANCHOR, "other": frozenset()})
    got = legs_of(cfg, Q, NAMES, frozenset(NAMES))
    assert "S003USDT" in got["mac"]  # 닻 S003 은 tri 표본이 아니므로 남는다


def test_top_dynamic_anchor() -> None:
    leg = LegRule("anchor_union_top", n=1, anchor="top_dynamic")
    got = legs_of(_cfg({"a": leg}), Q, NAMES, frozenset())
    assert set(got["a"]) == {"S001USDT", "S002USDT"}


def test_static_rule() -> None:
    got = legs_of(_cfg({"a": LegRule("static", symbols=("X_USDT",))}), Q, NAMES, frozenset())
    assert got["a"] == ("X_USDT",)


# ── U2 결정론 ─────────────────────────────────────────────────────────────


def _daily() -> list[SymbolDaily]:
    return [SymbolDaily(s, _days(Q, 100, value=float(1000 - i))) for i, s in enumerate(NAMES)]


def _full_cfg(tag: str = "T1") -> UniverseConfig:
    return _cfg(
        {
            "tri": LegRule("anchor_plus_sample", n=5, anchor="core", pool_top=40, salt="t"),
            "mac": LegRule(
                "sample",
                n=10,
                anchor="core",
                pool_top=100,
                salt="m",
                exclude_legs=("tri",),
                require_alive=True,
            ),
        },
        tag=tag,
    )


def test_same_input_same_snapshot_and_order_free() -> None:
    a = universe_for(_daily(), Q, _full_cfg())
    b = universe_for(list(reversed(_daily())), Q, _full_cfg())
    assert a == b
    assert a.input_digest == b.input_digest
    assert a.rule_digest == b.rule_digest


def test_global_random_state_untouched_and_unused() -> None:
    random.seed(1)
    before = random.getstate()
    a = universe_for(_daily(), Q, _full_cfg())
    assert random.getstate() == before
    random.seed(2)
    assert universe_for(_daily(), Q, _full_cfg()) == a


def test_seed_or_quarter_changes_sample() -> None:
    a = universe_for(_daily(), Q, _full_cfg("T1"))
    b = universe_for(_daily(), Q, _full_cfg("T2"))
    assert a.legs["mac"] != b.legs["mac"]
    assert a.rule_digest != b.rule_digest
    q2 = date(2027, 1, 1)
    c = universe_for([SymbolDaily(s, _days(q2, 100, 1.0)) for s in NAMES], q2, _full_cfg("T1"))
    assert c.legs["mac"] != a.legs["mac"]


def test_input_change_changes_digest() -> None:
    rows = _daily()
    rows[0] = dataclasses.replace(rows[0], quote_by_day=_days(Q, 100, value=999999.0))
    assert (
        universe_for(rows, Q, _full_cfg()).input_digest
        != universe_for(_daily(), Q, _full_cfg()).input_digest
    )


def test_sort_key_is_source_symbol_name() -> None:
    """모집단 정렬은 순위 자료 이름 그대로다 — `1000PEPEUSDT` 가 숫자 때문에 맨 앞에 온다."""
    names = ("ZUSDT", "1000PEPEUSDT", "AUSDT")
    leg = LegRule("sample", n=1, anchor="none", pool_top=3, salt="m")
    cfg = dataclasses.replace(_cfg({"a": leg}), anchors={"none": frozenset()})
    got = legs_of(cfg, Q, names, frozenset())
    pool = ["1000PEPEUSDT", "AUSDT", "ZUSDT"]
    assert set(got["a"]) == set(random.Random(f"T1|{Q.isoformat()}|m").sample(pool, 1))


def test_too_few_ranked_raises_data_error() -> None:
    cfg = dataclasses.replace(_full_cfg(), rank=dataclasses.replace(RULE, min_ranked=999))
    with pytest.raises(UniverseDataError):
        universe_for(_daily(), Q, cfg)


def test_boundary_gaps() -> None:
    rows = [_fact(f"S{i:03d}USDT", float(200 - i)) for i in range(150)]
    snap = universe_from_facts(rows, Q, _full_cfg())
    assert snap.boundary_gaps[40] == pytest.approx((161 - 160) / 161)
    assert set(snap.boundary_gaps) == {6, 40, 100}


# ── 설정 로더 ─────────────────────────────────────────────────────────────


GOOD = """
active_profile: a
seed: {tag: X1}
rank: {lookback_days: 365, min_days: 90, live_within_days: 3, alive_days: 30,
       alive_min_days: 7, min_ranked: 40, exclude: [USDC]}
top_anchor_n: 6
anchors: {core: [BTC]}
excludes: {bar: [GALA]}
profiles:
  a:
    one: {rule: anchor_plus_sample, anchor: core, pool_top: 40, n: 12, salt: t}
    two: {rule: sample, anchor: core, pool_top: 100, n: 22, salt: m,
          exclude_sets: [bar], exclude_legs: [one], require_alive: true}
  b:
    one: {rule: top, n: 6}
"""


def test_load_ok_and_profile_switch(tmp_path: Path) -> None:
    p = tmp_path / "u.yml"
    p.write_text(GOOD, encoding="utf-8")
    cfg = load_universe_config(p)
    assert cfg.profile == "a"
    assert cfg.seed_tag == "X1"
    assert cfg.anchors["core"] == frozenset({"BTCUSDT"})
    assert cfg.excludes["bar"] == frozenset({"GALAUSDT"})
    assert list(cfg.legs) == ["one", "two"]
    other = load_universe_config(p, profile="b", seed_tag="X2")
    assert other.profile == "b"
    assert other.seed_tag == "X2"
    assert other.digest() != cfg.digest()


@pytest.mark.parametrize(
    "broken",
    [
        GOOD.replace("anchor: core, pool_top: 40", "anchor: nope, pool_top: 40"),
        GOOD.replace("exclude_sets: [bar]", "exclude_sets: [nope]"),
        GOOD.replace("exclude_legs: [one]", "exclude_legs: [two]"),
        GOOD.replace("rule: top", "rule: nope"),
        GOOD.replace("active_profile: a", "active_profile: zzz"),
        GOOD.replace("n: 12", "n: 0"),
        GOOD.replace("seed: {tag: X1}\n", ""),
        "[]",
    ],
)
def test_load_broken_raises(tmp_path: Path, broken: str) -> None:
    p = tmp_path / "u.yml"
    p.write_text(broken, encoding="utf-8")
    with pytest.raises(UniverseConfigError):
        load_universe_config(p)


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(UniverseConfigError):
        load_universe_config(tmp_path / "none.yml")
