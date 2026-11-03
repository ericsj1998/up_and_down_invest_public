"""분기 목록 이름 사슬 (T758 N1) — 순위 이름 → 주문 계약 이름 · 가격비 · 뺀 이유."""

from __future__ import annotations

from updown.decision.universe_names import ContractFacts, NameChainRule, chain_one, gate_legs

RULE = NameChainRule(rebrand={"MATIC": "POL"}, price_tol=0.03)
C = {
    n: ContractFacts(n)
    for n in ("BTC_USDT", "PEPE_USDT", "POL_USDT", "1000SATS_USDT", "OLD_USDT", "NAS100_USDT")
}
C["OLD_USDT"] = ContractFacts("OLD_USDT", in_delisting=True)


def test_plain_and_prefix_and_rebrand() -> None:
    assert chain_one("BTCUSDT", C, RULE) == ("BTC_USDT", 1.0, "ok")
    name, k, why = chain_one("1000PEPEUSDT", C, RULE)
    assert (name, why) == ("PEPE_USDT", "ok") and k == 1 / 1000
    assert chain_one("MATICUSDT", C, RULE)[0] == "POL_USDT"
    assert chain_one("SATSUSDT", C, RULE)[:2] == ("1000SATS_USDT", 1000.0)


def test_reasons_for_dropping() -> None:
    assert chain_one("币安人生USDT", C, RULE)[2] == "non_ascii"
    assert chain_one("NONEUSDT", C, RULE)[2] == "no_contract"
    assert chain_one("OLDUSDT", C, RULE)[2] == "delisting"


def test_already_order_name_passes_through() -> None:
    assert chain_one("NAS100_USDT", C, RULE) == ("NAS100_USDT", 1.0, "ok")


def test_price_ratio_guards_same_ticker_other_asset() -> None:
    legs = {"a": ("BTCUSDT", "1000PEPEUSDT"), "b": ("BTCUSDT",)}
    rank = {"BTCUSDT": 100.0, "1000PEPEUSDT": 10.0}
    good = {"BTC_USDT": 101.0, "PEPE_USDT": 0.0101}
    got, why = gate_legs(legs, C, rank, good, RULE)
    assert got == {"a": ("BTC_USDT", "PEPE_USDT"), "b": ("BTC_USDT",)} and why == {}
    bad = {"BTC_USDT": 101.0, "PEPE_USDT": 0.02}  # 다른 자산 — 두 배 차
    got, why = gate_legs(legs, C, rank, bad, RULE)
    assert got["a"] == ("BTC_USDT",)  # 자리를 다른 종목으로 채우지 않는다
    assert why["1000PEPEUSDT"].startswith("price_mismatch")


def test_missing_price_is_dropped_not_assumed() -> None:
    got, why = gate_legs({"a": ("BTCUSDT",)}, C, {}, {"BTC_USDT": 1.0}, RULE)
    assert got == {"a": ()} and why == {"BTCUSDT": "no_price"}
