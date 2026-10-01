"""DB 매매법 선언 + YAML 합치기 (T349 · 2026-10-02).

파일이 SSoT: 같은 id 는 파일이 이기고 DB 줄은 그늘에 든다 · DB 를 못 읽어도 파일 선언으로
돈다 · 같은 파서 · API 는 슈퍼관리자만 · 파일 id 는 409 · 없는 셋업 · 묶음 구성원은 400.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from fastapi import HTTPException

from updown.analysis.playbook import db_source
from updown.analysis.playbook.select import (
    DEFAULT_CONFIG_PATH,
    PlaybookConfigError,
    load_playbooks,
    parse_block,
)
from updown.apps.api import rebalancer as rb
from updown.apps.api import walkforward as wf
from updown.common.security.caps import Cap

LEG = "private_strategy"


def _file_block(name: str) -> dict[str, Any]:
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(cast("dict[str, Any]", raw["playbooks"][name]))


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    db_source.invalidate()


def test_parse_block_matches_the_file_parser() -> None:
    from_file = next(b for b in load_playbooks(DEFAULT_CONFIG_PATH) if b.playbook_id == LEG)
    assert parse_block(LEG, _file_block(LEG)) == from_file


def test_parse_block_rejects_unknown_keys_like_the_file() -> None:
    body = _file_block(LEG)
    body["no_such_key"] = 1
    with pytest.raises(PlaybookConfigError):
        parse_block(LEG, body)


def test_disabled_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db_source.enabled() is False
    assert db_source.rows() == ()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x")
    monkeypatch.setenv(db_source.ENV_FLAG, "0")
    assert db_source.enabled() is False


def test_unreadable_db_keeps_last_rows_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x")
    monkeypatch.delenv(db_source.ENV_FLAG, raising=False)
    good = (db_source.DeclRow("db_book", _file_block(LEG)),)
    monkeypatch.setattr(db_source, "_fetch", lambda: good)
    assert db_source.rows() == good
    db_source.invalidate()

    def boom() -> tuple[db_source.DeclRow, ...]:
        raise RuntimeError("relation playbook_decls does not exist")

    monkeypatch.setattr(db_source, "_fetch", boom)
    assert db_source.rows() == good  # 마지막으로 읽은 것
    assert "playbook_decls" in db_source.status()["last_error"]


def test_db_rows_are_appended_and_file_wins_on_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _file_block(LEG)
    body["label"] = "DB 사본"
    rows = (
        db_source.DeclRow("db_only_book", body),
        db_source.DeclRow(LEG, body),  # 파일과 같은 id — 그늘
    )
    monkeypatch.setattr(db_source, "rows", lambda: rows)
    books = {b.playbook_id: b for b in load_playbooks()}
    assert "db_only_book" in books
    assert books["db_only_book"].label == "DB 사본"
    assert books[LEG].label != "DB 사본"  # 파일이 이겼다
    # path 를 준 호출(시험 · 도구)은 파일만
    assert "db_only_book" not in {b.playbook_id for b in load_playbooks(DEFAULT_CONFIG_PATH)}


def test_leg_scopes_merge_db_baskets_with_file_winning(monkeypatch: pytest.MonkeyPatch) -> None:
    basket = {"block": "default", "members": [{"symbol": "BTC_USDT", "weight": "1"}]}
    rows = (
        db_source.DeclRow("db_only_book", _file_block(LEG), basket),
        db_source.DeclRow(LEG, _file_block(LEG), basket),
    )
    monkeypatch.setattr(db_source, "rows", lambda: rows)
    scopes = rb._leg_scopes()
    assert scopes["db_only_book"] == ["BTC_USDT"]
    assert scopes[LEG] != ["BTC_USDT"]  # 파일 바스켓(40종)이 이겼다
    members, _ = rb._default_basket("GATE", "db_only_book")
    assert [m["symbol"] for m in members] == ["BTC_USDT"]


class _Who:
    def __init__(self, caps: set[Cap], email: str = "su@x") -> None:
        self._caps = caps
        self.email = email

    def has(self, cap: Cap) -> bool:
        return cap in self._caps


def _request(who: object) -> Any:
    return SimpleNamespace(state=SimpleNamespace(caller=who))


class _Decls:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, dict[str, Any], dict[str, Any] | None, str, bool]] = []
        self.deleted: list[str] = []

    async def put(
        self,
        playbook_id: str,
        body: dict[str, Any],
        *,
        basket: dict[str, Any] | None,
        by: str,
        enabled: bool = True,
    ) -> None:
        self.put_calls.append((playbook_id, body, basket, by, enabled))

    async def delete(self, playbook_id: str, *, by: str) -> bool:
        self.deleted.append(playbook_id)
        assert by
        return playbook_id == "db_only_book"

    async def list(self) -> list[dict[str, Any]]:
        return []


def test_api_requires_superadmin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_decls", _Decls())
    with pytest.raises(HTTPException) as got:
        asyncio.run(
            wf.put_playbook_decl(_request(_Who({Cap.LIVE_DELETE})), {"id": "x_book", "body": {}})
        )
    assert got.value.status_code == 403


def test_api_refuses_file_ids_unknown_setups_and_missing_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wf, "_decls", _Decls())
    monkeypatch.setattr(db_source, "rows", lambda: ())
    su = _request(_Who({Cap.MANAGE_ROLES}))
    with pytest.raises(HTTPException) as dup:
        asyncio.run(wf.put_playbook_decl(su, {"id": LEG, "body": _file_block(LEG)}))
    assert dup.value.status_code == 409
    bad_setup = _file_block(LEG)
    bad_setup["setups"] = ["no_such_rule"]
    with pytest.raises(HTTPException) as unk:
        asyncio.run(wf.put_playbook_decl(su, {"id": "x_book", "body": bad_setup}))
    assert unk.value.status_code == 400 and "no_such_rule" in str(unk.value.detail)
    bundle = _file_block("private_strategy")
    bundle["bundle"] = ["private_strategy", "ghost_leg"]
    bundle.pop("superseded_by", None)
    with pytest.raises(HTTPException) as miss:
        asyncio.run(wf.put_playbook_decl(su, {"id": "x_bundle", "body": bundle}))
    assert miss.value.status_code == 400 and "ghost_leg" in str(miss.value.detail)


def test_api_put_and_delete_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Decls()
    monkeypatch.setattr(wf, "_decls", store)
    monkeypatch.setattr(db_source, "rows", lambda: ())
    su = _request(_Who({Cap.MANAGE_ROLES}, "su@x"))
    body = _file_block(LEG)
    basket = {"block": "default", "members": [{"symbol": "BTC_USDT", "weight": "1"}]}
    out = asyncio.run(
        wf.put_playbook_decl(su, {"id": "db_only_book", "body": body, "basket": basket})
    )
    assert out["ok"] and out["source"] == "db" and out["setups"] == ["private_strategy"]
    assert store.put_calls[0][0] == "db_only_book" and store.put_calls[0][3] == "su@x"
    assert store.put_calls[0][2] == basket
    gone = asyncio.run(wf.delete_playbook_decl(su, "db_only_book"))
    assert gone["ok"] and store.deleted == ["db_only_book"]
    with pytest.raises(HTTPException) as nf:
        asyncio.run(wf.delete_playbook_decl(su, "never_there"))
    assert nf.value.status_code == 404
    with pytest.raises(HTTPException) as file_id:
        asyncio.run(wf.delete_playbook_decl(su, LEG))
    assert file_id.value.status_code == 409


def test_listing_tags_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _file_block("private_strategy")
    body["listed"] = True
    rows = (db_source.DeclRow("db_bundle", body),)
    monkeypatch.setattr(db_source, "rows", lambda: rows)
    listing = {item["id"]: item["source"] for item in wf._playbooks_all()["playbooks"]}
    assert listing["db_bundle"] == "db" and listing["private_strategy"] == "file"
