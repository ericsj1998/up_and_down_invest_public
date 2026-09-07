# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# TestClient 응답형
"""T211 — 회전 로그 파일 · 파일 싱크 무해성 · 내려받기 엔드포인트 (2026-09-04).

DoD:
  ① 날짜별 파일로 남고, 상한을 넘으면 오래된 것이 지워진다
  ② 파일 싱크를 강제로 실패시켜도 로깅(스트림)은 계속된다 — 규칙 #8-1
  ③ 날짜·kind 검증 (경로 조작 문자열은 422) · zip 안에 고른 파일만
  ④ 문: 관리자만 (`test_auth_guard.py` 에 `/admin/logs` 추가)
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from updown.apps.api import logs_admin
from updown.common import paths
from updown.common.logging import files as files_mod
from updown.common.logging.files import RotatingJsonlFile
from updown.common.logging.setup import configure_logging, get_logger
from updown.common.security.roles import Need, Role, allows, need_for

# ---------------------------------------------------------------------------
# ① 회전 · 상한
# ---------------------------------------------------------------------------


def test_rotates_by_utc_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    days = iter(["2026-09-04", "2026-09-04", "2026-09-05"])

    class _Clock:
        @staticmethod
        def now(_tz: Any = None) -> datetime:
            return datetime.fromisoformat(next(days)).replace(tzinfo=UTC)

    monkeypatch.setattr(files_mod, "datetime", _Clock)
    f = RotatingJsonlFile(tmp_path, "api")
    f.write_line('{"a":1}')
    f.write_line('{"a":2}')
    f.write_line('{"a":3}')
    f.close()
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == [
        "api-2026-09-04.jsonl",
        "api-2026-09-05.jsonl",
    ]
    assert (tmp_path / "api-2026-09-04.jsonl").read_text().count("\n") == 2


def test_prunes_oldest_when_over_cap(tmp_path: Path) -> None:
    for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
        (tmp_path / f"api-{d}.jsonl").write_bytes(b"x" * 100)
    (tmp_path / "engine-2026-09-01.jsonl").write_bytes(b"x" * 100)  # 다른 proc 은 안 건드린다
    f = RotatingJsonlFile(tmp_path, "api", cap_bytes=250)
    f.write_line("{}")  # 오늘 파일이 열리며 prune
    f.close()
    left = sorted(p.name for p in tmp_path.glob("api-*.jsonl"))
    assert "api-2026-09-01.jsonl" not in left, "가장 오래된 것부터 지워야 한다"
    assert "api-2026-09-03.jsonl" in left
    assert (tmp_path / "engine-2026-09-01.jsonl").exists()


# ---------------------------------------------------------------------------
# ② 규칙 #8-1 — 파일 싱크 실패가 로깅을 막지 않는다
# ---------------------------------------------------------------------------


def test_unwritable_file_dir_does_not_break_logging(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file, not dir")  # mkdir 이 실패한다
    stream = io.StringIO()
    configure_logging("INFO", stream=stream, file_dir=blocker / "app", proc="api")
    try:
        get_logger("t").info("STILL_LOGGING")
    finally:
        configure_logging("INFO")
    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    assert any(e["event_type"] == "STILL_LOGGING" for e in lines)


def test_file_sink_receives_structlog_and_stdlib_lines(tmp_path: Path) -> None:
    import logging

    stream = io.StringIO()
    configure_logging("INFO", stream=stream, file_dir=tmp_path, proc="api")
    try:
        get_logger("t").info("FROM_STRUCTLOG")
        logging.getLogger("uvicorn.error").warning("FROM_STDLIB")
    finally:
        configure_logging("INFO")
    files = list(tmp_path.glob("api-*.jsonl"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "FROM_STRUCTLOG" in body
    assert "FROM_STDLIB" in body
    # 스트림(stderr 역할)에도 그대로 — 파일은 복사본이다
    assert "FROM_STRUCTLOG" in stream.getvalue()


def test_env_switch_disables_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDOWN_LOG_FILES", "0")
    configure_logging("INFO", stream=io.StringIO(), file_dir=tmp_path, proc="api")
    try:
        get_logger("t").info("E")
    finally:
        configure_logging("INFO")
    assert not list(tmp_path.glob("*.jsonl"))


# ---------------------------------------------------------------------------
# ③ 엔드포인트
# ---------------------------------------------------------------------------


@pytest.fixture
def logs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    (root / "app").mkdir(parents=True)
    (root / "funds").mkdir()
    (root / "app" / "api-2026-09-03.jsonl").write_text('{"d":3}\n')
    (root / "app" / "api-2026-09-04.jsonl").write_text('{"d":4}\n')
    (root / "app" / "engine-2026-09-04.jsonl").write_text('{"e":4}\n')
    (root / "app" / "junk.txt").write_text("ignored")
    (root / "funds" / "fund1.json").write_text("{}")
    (root / "report_sends.jsonl").write_text("{}\n")
    monkeypatch.setattr(paths, "logs_root", lambda: root)
    return root


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(logs_admin.router)
    return TestClient(app)


def test_listing_groups_by_kind(client: TestClient, logs_root: Path) -> None:
    body = client.get("/admin/logs").json()
    assert body["root"] == str(logs_root)
    assert body["summary"]["api"]["files"] == 2
    assert body["summary"]["engine"]["files"] == 1
    assert body["summary"]["funds"]["files"] == 1
    assert body["summary"]["report_sends"]["files"] == 1
    assert all(r["name"] != "app/junk.txt" for r in body["files"])


@pytest.mark.usefixtures("logs_root")
def test_download_zips_only_the_range_and_kinds(client: TestClient) -> None:
    res = client.get(
        "/admin/logs/download", params={"from": "2026-09-04", "to": "2026-09-04", "kinds": "api"}
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert zf.namelist() == ["app/api-2026-09-04.jsonl"]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "../etc", "to": "2026-09-04"},
        {"from": "2026-09-04", "to": "2026-09-04; rm -rf /"},
        {"from": "2026-09-05", "to": "2026-09-04"},
        {"from": "2026-09-04", "to": "2026-09-04", "kinds": "api,../../secret"},
    ],
)
@pytest.mark.usefixtures("logs_root")
def test_bad_inputs_are_422(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/admin/logs/download", params=params).status_code == 422


@pytest.mark.usefixtures("logs_root")
def test_empty_range_is_404(client: TestClient) -> None:
    res = client.get("/admin/logs/download", params={"from": "2020-01-01", "to": "2020-01-02"})
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# ④ 문 — 진리표 (미들웨어 통합은 test_auth_guard 가 본다)
# ---------------------------------------------------------------------------


def test_admin_logs_requires_admin() -> None:
    assert need_for("GET", "/admin/logs") is Need.ADMIN
    assert need_for("GET", "/admin/logs/download") is Need.ADMIN
    assert allows(Role.TRADER, Need.ADMIN) is False
    assert allows(Role.ADMIN, Need.ADMIN) is True
