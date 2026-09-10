"""라벨 세션 복제 — 원본 그대로 · 새 이름 · 덮어쓰기 없음 (2026-09-10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from updown.apps.api import labels


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(labels, "LABEL_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(labels.router)
    return TestClient(app)


def _post(client: TestClient, url: str, body: dict[str, Any] | None = None) -> Any:
    return client.post(url, json=body if body is not None else {})  # pyright: ignore[reportUnknownMemberType]


def test_duplicate_copies_marks_under_a_free_name(client: TestClient, tmp_path: Path) -> None:
    client.put(
        "/labels/xrp_0902",
        json={
            "symbol": "XRPUSDT",
            "timeframe": "15m",
            "market": "BINANCE",
            "marks": [{"kind": "long", "ts": 1, "price": 2.5}],
        },
    )  # pyright: ignore[reportUnknownMemberType]
    first = _post(client, "/labels/xrp_0902/duplicate")
    assert first.status_code == 200, first.text
    assert first.json()["to"] == "xrp_0902-copy"
    assert first.json()["marks"] == 1
    second = _post(client, "/labels/xrp_0902/duplicate")
    assert second.json()["to"] == "xrp_0902-copy2"
    copied = cast(
        "dict[str, Any]", json.loads((tmp_path / "xrp_0902-copy.json").read_text(encoding="utf-8"))
    )
    assert copied["name"] == "xrp_0902-copy"
    assert copied["copied_from"] == "xrp_0902"
    assert copied["marks"][0]["price"] == 2.5
    assert (tmp_path / "xrp_0902.json").exists(), "원본은 그대로"


def test_duplicate_to_named_target_refuses_overwrite(client: TestClient) -> None:
    client.put(
        "/labels/a", json={"symbol": "X", "timeframe": "1h", "market": "BINANCE", "marks": []}
    )  # pyright: ignore[reportUnknownMemberType]
    ok = _post(client, "/labels/a/duplicate", {"to": "b"})
    assert ok.status_code == 200 and ok.json()["to"] == "b"
    again = _post(client, "/labels/a/duplicate", {"to": "b"})
    assert again.status_code == 409
    missing = _post(client, "/labels/nope/duplicate")
    assert missing.status_code == 404
    bad = _post(client, "/labels/a/duplicate", {"to": "../etc"})
    assert bad.status_code == 422
