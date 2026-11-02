"""T607 — 새 출발 스크립트(`scripts/ops/fresh_start_t607.py`)의 순수 함수 · 계산만 분기 · 멱등."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "fresh_start_t607.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fresh_start_t607", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fresh_start_t607"] = module
    spec.loader.exec_module(module)
    return module


M = _module()
OLD = "fund77e26368"
BOOK = M.DEFAULT_PLAYBOOK


def _members(n: int = 40) -> list[dict[str, str]]:
    return [{"symbol": f"S{i}_USDT", "weight": "1"} for i in range(n)]


def _fund(
    fund_id: str = OLD,
    *,
    label: str = "옛 펀드",
    handles: Mapping[str, str | None] | None = None,
    anchor: dict[str, Any] | None = None,
    balance: str = "78.79",
    playbook: str = BOOK,
) -> dict[str, Any]:
    handles = (
        handles if handles is not None else {m["symbol"]: f"h-{m['symbol']}" for m in _members()}
    )
    per: dict[str, Any] = {}
    for sym, handle in handles.items():
        per[sym] = {"handle": handle, "missing": True} if handle is None else {"handle": handle}
    return {
        "fund_id": fund_id,
        "label": label,
        "playbook": playbook,
        "market": "GATE",
        "symbols": list(handles),
        "balance": balance,
        "anchor": anchor,
        "per_symbol": per,
    }


def _session(key: str, symbol: str, *, auto: bool = False, market: str = "GATE") -> dict[str, Any]:
    return {
        "session_id": key,
        "live": True,
        "symbol": symbol,
        "market": market,
        "auto": auto,
        "trades": 0,
    }


class FakeApi:
    """경로 → 응답. 쓰기 호출은 전부 `calls` 에 남긴다."""

    def __init__(self, routes: dict[Any, Any]) -> None:
        self.routes: dict[Any, Any] = routes
        self.calls: list[tuple[str, str, Any]] = []
        self.on_delete: dict[str, Any] = {}

    def get(self, path: str) -> Any:
        key = path.split("?")[0]
        if key not in self.routes:
            raise AssertionError(f"뜻밖의 GET {path}")
        value = self.routes[key]
        return value(path) if callable(value) else value

    def post(self, path: str, body: Any, *, timeout: float = 0) -> Any:
        del timeout
        self.calls.append(("POST", path, body))
        return self.routes.get(("POST", path), {})

    def delete(self, path: str) -> Any:
        self.calls.append(("DELETE", path, None))
        hook = self.on_delete.get(path)
        if hook is not None:
            hook()
        return self.routes.get(("DELETE", path), {})


def _state(
    total: str = "78.79", positions: int = 0, orders: int = 0, stops: int = 0
) -> dict[str, Any]:
    return {
        "balance": {"total": total},
        "positions": [{"size": "3"}] * positions,
        "orders": [{}] * orders,
        "stops": [{}] * stops,
    }


def _no_sleep(_seconds: float) -> None:
    return None


def _runner(tmp_path: Path, api: FakeApi, **kw: Any) -> Any:
    lines: list[str] = []
    runner = M.Runner(
        api=api,
        dry_run=kw.pop("dry_run", True),
        state_path=tmp_path / "state.json",
        say=lines.append,
        sleep=_no_sleep,
        **kw,
    )
    runner.lines = lines
    return runner


class TestArgs:
    def test_default_is_dry_run(self) -> None:
        args = M.parse_args(["--step", "1"])
        assert args.dry_run is True and args.apply is False

    def test_apply_turns_dry_run_off(self) -> None:
        args = M.parse_args(["--step", "3", "--apply"])
        assert args.dry_run is False and args.apply is True

    def test_both_flags_rejected(self) -> None:
        with pytest.raises(SystemExit):
            M.parse_args(["--step", "1", "--dry-run", "--apply"])

    def test_step_required_and_bounded(self) -> None:
        with pytest.raises(SystemExit):
            M.parse_args([])
        with pytest.raises(SystemExit):
            M.parse_args(["--step", "5"])


class TestPure:
    def test_expected_unbound_from_check_json(self) -> None:
        text = json.dumps(
            {
                "value": "0건 · 🔴 펀드가 묶은 판이 열린 판이 아님 3종(ADA_USDT(550484→eef152), "
                "BTC_USDT(a75a2f→44b75f), XRP_USDT(c6ad36→58b7b0))"
            },
            ensure_ascii=False,
        )
        assert M.expected_unbound_from_check(text) == ["ADA_USDT", "BTC_USDT", "XRP_USDT"]

    def test_expected_unbound_absent_is_none_not_empty(self) -> None:
        assert M.expected_unbound_from_check('{"value": "0건"}') is None

    def test_strangers_are_live_gate_runs_outside_every_fund(self) -> None:
        funds = {"funds": [_fund(handles={"BTC_USDT": "bound", "ETH_USDT": None})]}
        sessions = {
            "sessions": [
                _session("bound", "BTC_USDT"),
                _session("stray", "ADA_USDT"),
                _session("bn", "BTC_USDT", market="BINANCE"),
                {
                    "session_id": "journal",
                    "live": False,
                    "symbol": "X",
                    "market": "GATE",
                    "stored": True,
                },
            ]
        }
        got = M.strangers_of(sessions, funds)
        assert [s["session_id"] for s in got] == ["stray"]

    def test_payload_sends_only_declared_free_fields(self) -> None:
        body = M.payload_for(
            label="새", total_cash=Decimal("78.799"), playbook=BOOK, members=_members(2)
        )
        assert set(body) == {"label", "total_cash", "playbook", "market", "members"}
        assert body["total_cash"] == "78.79" and body["market"] == "GATE"
        assert body["members"][0] == {"symbol": "S0_USDT", "weight": "1"}

    def test_exchange_facts_filters_zero_positions(self) -> None:
        facts = M.exchange_facts(
            {
                "balance": {"total": "10.5"},
                "positions": [{"size": "0"}, {"size": "2"}],
                "orders": [],
                "stops": [{}],
            }
        )
        assert facts["total"] == Decimal("10.5")
        assert len(facts["positions"]) == 1 and len(facts["stops"]) == 1

    def test_verify_after_good(self) -> None:
        handles = {m["symbol"]: f"h-{m['symbol']}" for m in _members()}
        fund = _fund(
            "fundnew", label="새", handles=handles, anchor={"mode": "account", "skipped": None}
        )
        sessions = {"sessions": [_session(h, s) for s, h in handles.items()]}
        checks = M.verify_after(
            {"funds": [fund]},
            sessions,
            label="새",
            playbook=BOOK,
            members=40,
            wallet_total=Decimal("78.79"),
        )
        assert all(c.ok for c in checks)

    def test_verify_after_flags_skipped_anchor_and_strangers(self) -> None:
        handles = {m["symbol"]: f"h-{m['symbol']}" for m in _members()}
        fund = _fund(
            "fundnew",
            label="새",
            handles=handles,
            anchor={"mode": None, "skipped": "펀드 밖 단독 판 1개(ADA_USDT)"},
        )
        rows = [_session(h, s) for s, h in handles.items()] + [_session("stray", "ADA_USDT")]
        checks = M.verify_after(
            {"funds": [fund]},
            {"sessions": rows},
            label="새",
            playbook=BOOK,
            members=40,
            wallet_total=None,
        )
        bad = {c.what for c in checks if not c.ok}
        assert bad == {"펀드 밖 판", "첫 앵커 모드", "앵커 건너뜀(fund_anchor_skipped)"}


class TestStep0:
    def test_records_state_and_lists_problems(self, tmp_path: Path) -> None:
        api = FakeApi(
            {
                "/health": {"trading_leader": True, "version": "1.41.0"},
                "/exchange/state": _state(),
                "/rebalancer": {"funds": [_fund(handles={"BTC_USDT": "b", "ADA_USDT": "dead"})]},
                "/walkforward/sessions": {
                    "sessions": [_session("b", "BTC_USDT"), _session("x", "ADA_USDT", auto=True)]
                },
            }
        )
        r = _runner(tmp_path, api, expected_unbound=["ADA_USDT"])
        r.step0()
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["old_fund"] == OLD and state["playbook"] == BOOK
        assert state["wallet_total"] == "78.79" and state["strangers"] == ["ADA_USDT"]
        assert any("새 진입이 켜진 판 1" in p for p in state["problems"])
        assert api.calls == []  # 읽기만


class TestStep1:
    def _api(self, funds: list[dict[str, Any]], **state_kw: Any) -> FakeApi:
        return FakeApi({"/rebalancer": {"funds": funds}, "/exchange/state": _state(**state_kw)})

    def test_dry_run_sends_nothing(self, tmp_path: Path) -> None:
        api = self._api([_fund()])
        _runner(tmp_path, api).step1(OLD)
        assert api.calls == []

    def test_apply_deletes_once(self, tmp_path: Path) -> None:
        api = self._api([_fund()])
        api.routes[("DELETE", f"/rebalancer/{OLD}")] = {"dropped": OLD, "sessions": ["a"]}
        _runner(tmp_path, api, dry_run=False).step1(OLD)
        assert api.calls == [("DELETE", f"/rebalancer/{OLD}", None)]

    def test_already_archived_skips(self, tmp_path: Path) -> None:
        api = self._api([])
        r = _runner(tmp_path, api, dry_run=False)
        r.step1(OLD)
        assert api.calls == [] and any("건너뜀" in line for line in r.lines)

    def test_refuses_when_exchange_not_flat(self, tmp_path: Path) -> None:
        api = self._api([_fund()], positions=1)
        with pytest.raises(M.StepError, match="비어 있지 않다"):
            _runner(tmp_path, api, dry_run=False).step1(OLD)
        assert api.calls == []

    def test_refuses_to_drop_the_new_fund(self, tmp_path: Path) -> None:
        api = self._api([_fund("fundnew", label=M.DEFAULT_LABEL)])
        with pytest.raises(M.StepError, match="새 펀드 이름"):
            _runner(tmp_path, api, dry_run=False).step1("fundnew")

    def test_needs_an_id(self, tmp_path: Path) -> None:
        with pytest.raises(M.StepError, match="--old-fund"):
            _runner(tmp_path, self._api([])).step1(None)


class TestStep2:
    def _api(self, strangers: list[str], fund: bool = True) -> FakeApi:
        rows = [_session(f"k-{s}", s) for s in strangers]
        funds = [_fund(handles={"BTC_USDT": "b"})] if fund else []
        if fund:
            rows.append(_session("b", "BTC_USDT"))

        def sessions_now(_path: str) -> dict[str, Any]:
            return {"sessions": list(rows)}

        api = FakeApi(
            {
                "/rebalancer": {"funds": funds},
                "/walkforward/sessions": sessions_now,
                "/exchange/state": _state(),
            }
        )
        for s in strangers:
            api.on_delete[f"/walkforward/sessions/k-{s}"] = lambda s=s: rows.remove(
                next(r for r in rows if r["session_id"] == f"k-{s}")
            )
        return api

    def test_dry_run_sends_nothing(self, tmp_path: Path) -> None:
        api = self._api(["ADA_USDT", "XRP_USDT"])
        _runner(tmp_path, api, expected_unbound=["ADA_USDT", "XRP_USDT"]).step2()
        assert api.calls == []

    def test_apply_deletes_each_and_verifies_zero_left(self, tmp_path: Path) -> None:
        api = self._api(["ADA_USDT", "XRP_USDT"])
        _runner(tmp_path, api, dry_run=False, expected_unbound=["ADA_USDT", "XRP_USDT"]).step2()
        assert [c[1] for c in api.calls] == [
            "/walkforward/sessions/k-ADA_USDT",
            "/walkforward/sessions/k-XRP_USDT",
        ]

    def test_mismatch_with_check_json_stops(self, tmp_path: Path) -> None:
        api = self._api(["ADA_USDT"])
        with pytest.raises(M.StepError, match="다르다"):
            _runner(tmp_path, api, dry_run=False, expected_unbound=["XRP_USDT"]).step2()
        assert api.calls == []

    def test_no_expectation_needs_force_while_old_fund_exists(self, tmp_path: Path) -> None:
        api = self._api(["ADA_USDT"])
        with pytest.raises(M.StepError, match="--force"):
            _runner(tmp_path, api, dry_run=False, expected_unbound=None).step2()
        _runner(tmp_path, api, dry_run=False, expected_unbound=None, force=True).step2()
        assert len(api.calls) == 1

    def test_after_archive_everything_left_is_a_stranger(self, tmp_path: Path) -> None:
        api = self._api(["ADA_USDT"], fund=False)
        _runner(tmp_path, api, dry_run=False, expected_unbound=None).step2()
        assert len(api.calls) == 1

    def test_nothing_to_do_is_idempotent(self, tmp_path: Path) -> None:
        api = self._api([])
        r = _runner(tmp_path, api, dry_run=False)
        r.step2()
        assert api.calls == [] and any("건너뜀" in line for line in r.lines)


class TestStep3:
    def _api(
        self, funds: list[dict[str, Any]], sessions: list[dict[str, Any]], **state_kw: Any
    ) -> FakeApi:
        return FakeApi(
            {
                "/rebalancer": {"funds": funds},
                "/walkforward/sessions": {"sessions": sessions},
                "/exchange/state": _state(**state_kw),
                "/rebalancer/defaults": {"playbook": BOOK, "members": _members(), "missing": []},
            }
        )

    def _seed_state(self, tmp_path: Path) -> None:
        (tmp_path / "state.json").write_text(
            json.dumps(
                {"old_fund": OLD, "playbook": BOOK, "symbols": [m["symbol"] for m in _members()]}
            ),
            encoding="utf-8",
        )

    def test_dry_run_computes_payload_but_sends_nothing(self, tmp_path: Path) -> None:
        self._seed_state(tmp_path)
        api = self._api([], [])
        r = _runner(tmp_path, api)
        r.step3()
        assert api.calls == [] and any("total_cash 78.79" in line for line in r.lines)

    def test_apply_posts_declaration_free_payload(self, tmp_path: Path) -> None:
        self._seed_state(tmp_path)
        api = self._api([], [], total="123.456")
        api.routes[("POST", "/rebalancer")] = {
            "fund_id": "fundnew",
            "balance": "123.45",
            "symbols": [m["symbol"] for m in _members()],
            "anchor": {"mode": "account", "skipped": None},
        }
        _runner(tmp_path, api, dry_run=False).step3()
        (method, path, body), *_ = api.calls
        assert (method, path) == ("POST", "/rebalancer")
        assert (
            body["total_cash"] == "123.45"
            and body["playbook"] == BOOK
            and len(body["members"]) == 40
        )
        assert "leverage" not in body and "slots" not in body
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["new_fund"] == "fundnew"

    def test_refuses_while_another_gate_fund_exists(self, tmp_path: Path) -> None:
        self._seed_state(tmp_path)
        api = self._api([_fund()], [])
        with pytest.raises(M.StepError, match="1 단계 먼저"):
            _runner(tmp_path, api, dry_run=False).step3()
        assert api.calls == []

    def test_refuses_while_strangers_remain(self, tmp_path: Path) -> None:
        self._seed_state(tmp_path)
        api = self._api([], [_session("x", "ADA_USDT")])
        with pytest.raises(M.StepError, match="2 단계 먼저"):
            _runner(tmp_path, api, dry_run=False).step3()

    def test_same_label_already_there_skips(self, tmp_path: Path) -> None:
        self._seed_state(tmp_path)
        api = self._api([_fund("fundnew", label=M.DEFAULT_LABEL)], [])
        r = _runner(tmp_path, api, dry_run=False)
        r.step3()
        assert api.calls == [] and any("건너뜀" in line for line in r.lines)

    def test_member_count_mismatch_stops(self, tmp_path: Path) -> None:
        self._seed_state(tmp_path)
        api = self._api([], [])
        api.routes["/rebalancer/defaults"] = {
            "playbook": BOOK,
            "members": _members(39),
            "missing": [],
        }
        with pytest.raises(M.StepError, match="멤버 수 39"):
            _runner(tmp_path, api, dry_run=False).step3()

    def test_symbol_set_differs_from_old_fund_stops(self, tmp_path: Path) -> None:
        self._seed_state(tmp_path)
        api = self._api([], [])
        members = [*_members(39), {"symbol": "NEW_USDT", "weight": "1"}]
        api.routes["/rebalancer/defaults"] = {"playbook": BOOK, "members": members, "missing": []}
        with pytest.raises(M.StepError, match="옛 펀드 종목과 다르다"):
            _runner(tmp_path, api, dry_run=False).step3()

    def test_unknown_playbook_without_state_stops(self, tmp_path: Path) -> None:
        api = self._api([], [])
        with pytest.raises(M.StepError, match="--playbook"):
            _runner(tmp_path, api).step3()


class TestStep4:
    def test_returns_false_when_anchor_skipped(self, tmp_path: Path) -> None:
        handles = {m["symbol"]: f"h-{m['symbol']}" for m in _members()}
        fund = _fund(
            "fundnew", label=M.DEFAULT_LABEL, handles=handles, anchor={"mode": None, "skipped": "x"}
        )
        api = FakeApi(
            {
                "/rebalancer": {"funds": [fund]},
                "/walkforward/sessions": {"sessions": [_session(h, s) for s, h in handles.items()]},
                "/exchange/state": _state(),
            }
        )
        assert _runner(tmp_path, api, playbook=BOOK).step4() is False
        assert api.calls == []
