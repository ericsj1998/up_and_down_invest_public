# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# TestClient 응답형
"""T215 — 자원 스냅샷 · cgroup 파싱 · 경고선 · `/admin/resources` 모양 · 문 (2026-09-04)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from updown.apps.api import resources_admin
from updown.apps.engine.resource_beat import ENGINE_KEY
from updown.common import resources
from updown.common.security.roles import Need, need_for

# ---------------------------------------------------------------------------
# 스냅샷 · cgroup
# ---------------------------------------------------------------------------


def test_snapshot_has_every_section() -> None:
    snap = resources.snapshot("api", disks={"root": Path("/")})
    assert snap["proc"] == "api"
    assert snap["process"]["rss"] > 0 and snap["process"]["threads"] >= 1
    assert set(snap["cgroup"]) == {"memory", "cpu"}
    assert snap["host"]["mem_total"] > 0
    assert snap["disks"]["root"]["total"] > 0


def test_cgroup_v2_parsing(tmp_path: Path) -> None:
    (tmp_path / "memory.current").write_text("268435456\n")
    (tmp_path / "memory.max").write_text("1073741824\n")
    (tmp_path / "cpu.max").write_text("50000 100000\n")
    mem = resources.cgroup_memory(tmp_path)
    assert mem == {"current": 268435456, "max": 1073741824, "percent": 25.0}
    assert resources.cgroup_cpu(tmp_path) == {"cores": 0.5}


def test_cgroup_no_limit_is_none_not_zero(tmp_path: Path) -> None:
    """한도 없음(`max`)을 0 으로 읽으면 사용률이 무한대가 되거나 100% 로 꾸며진다."""
    (tmp_path / "memory.current").write_text("1\n")
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "cpu.max").write_text("max 100000\n")
    assert resources.cgroup_memory(tmp_path)["max"] is None
    assert resources.cgroup_memory(tmp_path)["percent"] is None
    assert resources.cgroup_cpu(tmp_path)["cores"] is None
    assert resources.cgroup_memory(tmp_path / "nope")["current"] is None


def test_missing_disk_is_reported_not_dropped() -> None:
    out = resources.disk_stats({"gone": Path("/definitely/not/here")})
    assert out["gone"]["missing"] is True


# ---------------------------------------------------------------------------
# 경고선
# ---------------------------------------------------------------------------


def _snap(cg_pct: float | None, host_pct: float, disk_pct: float) -> dict[str, Any]:
    return {
        "proc": "api",
        "cgroup": {"memory": {"percent": cg_pct}},
        "host": {"mem_percent": host_pct},
        "disks": {"logs": {"percent": disk_pct}},
    }


def test_warnings_prefer_cgroup_limit_over_host() -> None:
    assert resources.warnings_for(_snap(90.0, 20.0, 10.0)) == [
        "api: RAM 90% (cgroup 한도) — 85% 경고선"
    ]
    assert resources.warnings_for(_snap(None, 90.0, 10.0)) == ["api: RAM 90% (호스트) — 85% 경고선"]
    assert resources.warnings_for(_snap(50.0, 90.0, 10.0)) == []


def test_disk_warning_at_80() -> None:
    warns = resources.warnings_for(_snap(10.0, 10.0, 81.0))
    assert warns == ["api: 디스크 logs 81% — 80% 경고선"]


# ---------------------------------------------------------------------------
# 엔드포인트 모양
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self, engine_json: str | None) -> None:
        self._engine = engine_json

    async def get(self, key: str) -> str | None:
        assert key == ENGINE_KEY
        return self._engine

    async def info(self, _section: str) -> dict[str, Any]:
        return {"used_memory": 12345}


def _app(engine_json: str | None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(resources_admin.router)
    app.state.updown = SimpleNamespace(redis=_FakeRedis(engine_json), session_factory=object())

    async def fake_db(_factory: Any) -> dict[str, Any]:
        return {"size": 777, "tables": [{"name": "candles", "bytes": 700}]}

    monkeypatch.setattr(resources_admin, "_db_stats", fake_db)
    return TestClient(app)


def test_endpoint_shape_with_engine_beat(monkeypatch: pytest.MonkeyPatch) -> None:
    beat = resources.snapshot("engine")
    client = _app(json.dumps(beat), monkeypatch)
    body = client.get("/admin/resources").json()
    assert body["api"]["proc"] == "api"
    assert body["engine"]["proc"] == "engine" and body["engine"]["age_s"] >= 0
    assert body["db"]["size"] == 777 and body["redis"]["used_memory"] == 12345
    assert set(body["runs"]) == {"running", "max"}
    assert isinstance(body["warnings"], list)


def test_endpoint_says_when_engine_beat_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """engine 스냅샷이 없으면 null 로 두고 **이유**를 경고에 싣는다 — 빈칸을 0 으로 안 꾸민다."""
    body = _app(None, monkeypatch).get("/admin/resources").json()
    assert body["engine"] is None
    assert "engine 비트 없음" in body["engine_note"]
    assert any("engine 비트 없음" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# 문
# ---------------------------------------------------------------------------


def test_resources_requires_admin() -> None:
    assert need_for("GET", "/admin/resources") is Need.ADMIN
