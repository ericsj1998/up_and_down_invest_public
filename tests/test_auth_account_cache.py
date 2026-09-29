"""미들웨어의 계정 행 읽기 30초 캐시 — 요청 240/분이 DB 풀을 채우지 않게 (T331)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from updown.apps.api import auth as mod


@pytest.fixture(autouse=True)
def clean_cache() -> None:
    mod.invalidate_account()


@pytest.mark.asyncio
async def test_a_second_request_inside_the_ttl_does_not_touch_the_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str] = []

    async def fake_account_of(email: str) -> object:
        reads.append(email)
        return SimpleNamespace(email=email)

    monkeypatch.setattr(mod, "account_of", fake_account_of)
    first = await mod.account_cached("a@x")
    second = await mod.account_cached("a@x")
    assert first is second and reads == ["a@x"]


@pytest.mark.asyncio
async def test_the_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []
    clock = [1000.0]

    async def fake_account_of(email: str) -> object:
        reads.append(email)
        return SimpleNamespace(email=email)

    monkeypatch.setattr(mod, "account_of", fake_account_of)
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    await mod.account_cached("a@x")
    clock[0] += mod.ACCOUNT_CACHE_S + 1
    await mod.account_cached("a@x")
    assert reads == ["a@x", "a@x"]


@pytest.mark.asyncio
async def test_a_missing_account_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []

    async def fake_account_of(email: str) -> object | None:
        reads.append(email)
        return None

    monkeypatch.setattr(mod, "account_of", fake_account_of)
    assert await mod.account_cached("new@x") is None
    assert await mod.account_cached("new@x") is None
    assert reads == ["new@x", "new@x"]


@pytest.mark.asyncio
async def test_invalidate_drops_one_person(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []

    async def fake_account_of(email: str) -> object:
        reads.append(email)
        return SimpleNamespace(email=email)

    monkeypatch.setattr(mod, "account_of", fake_account_of)
    await mod.account_cached("a@x")
    await mod.account_cached("b@x")
    mod.invalidate_account("a@x")
    await mod.account_cached("a@x")
    await mod.account_cached("b@x")
    assert reads == ["a@x", "b@x", "a@x"]


def test_every_account_mutation_invalidates() -> None:
    """등급 · 차단 · 삭제 · 되살림 · 로그아웃 · 보류 해제 — 고치는 곳마다 캐시를 비운다."""
    from pathlib import Path

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert source.count("invalidate_account(") >= 7
    assert "found = await account_cached(email)" in source
