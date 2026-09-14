"""차트 채점 API (`apps/api/grading.py`) — 결과 JSON 읽기 · 매매 변환 · 표시 저장/읽기 · 이름 봉인.

봉 조회(`/candles`)는 시세 어댑터를 부르므로 여기서 재지 않는다 — 인자 거부만 본다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from updown.apps.api.grading import (
    ENV_DIRS,
    list_runs,
    load_payload,
    marks_path,
    router,
    summarize,
    trade_rows,
)
from updown.common import paths

RESULT: dict[str, Any] = {
    "generated_at": "2026-09-14T00:00:00+00:00",
    "cost": "doc",
    "venue": "upbit",
    "symbols": ["KRW-BTC"],
    "runs": [
        {
            "config": {"name": "base"},
            "trades": [
                {
                    "symbol": "KRW-BTC",
                    "kind": "fade",
                    "direction": 1,
                    "entry_ts": "2026-01-01T01:15:00+00:00",
                    "exit_ts": "2026-01-01T05:00:00+00:00",
                    "entry": 100.0,
                    "stop": 98.0,
                    "gross_pct": 2.0,
                    "net_pct": 1.9,
                    "exit_reason": "opp_band",
                    "bars_held": 4,
                },
                {
                    "symbol": "KRW-ETH",
                    "kind": "fade",
                    "direction": -1,
                    "entry_ts": "2026-01-01T01:15:00+00:00",
                    "exit_ts": "2026-01-01T02:00:00+00:00",
                    "entry": 50.0,
                    "stop": 51.0,
                    "gross_pct": -1.0,
                    "net_pct": -1.1,
                    "exit_reason": "breakout",
                    "bars_held": 1,
                },
            ],
        }
    ],
}


def fetch(client: TestClient, url: str, **params: object) -> Response:
    """GET 한 번 — 타입 없는 경계를 여기서 끝낸다 (`test_api_admin.fetch` 와 같은 이유)."""
    got = client.get(url, params=params)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportArgumentType]
    return cast(Response, got)


def put(client: TestClient, url: str, body: dict[str, Any], **params: object) -> Response:
    """PUT 한 번."""
    got = client.put(url, params=params, json=body)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportArgumentType]
    return cast(Response, got)


def body_of(response: Response) -> dict[str, Any]:
    """응답 본문 — `Any` 가 여기서 멈춘다."""
    return response.json()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """로그 루트를 임시 디렉터리로 — 결과 JSON 하나를 `research/` 에 두고 env 로 가리킨다."""
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    monkeypatch.setenv(ENV_DIRS, "research, missing_dir")
    research = tmp_path / "research"
    research.mkdir()
    (research / "run1.json").write_text(json.dumps(RESULT), encoding="utf-8")
    (research / "edges.json").write_text(json.dumps({"edges": {}}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_runs_skips_non_result_json(root: Path) -> None:
    got = list_runs([root / "research"])
    assert [r["file"] for r in got] == ["run1.json"]
    assert got[0]["configs"] == [{"name": "base", "trades": 2}]
    assert got[0]["symbols"] == ["KRW-BTC"]


def test_list_runs_index_skips_unchanged_files(root: Path) -> None:
    index = root / "grading" / "index.json"
    first = list_runs([root / "research"], index)
    assert index.is_file()
    written = json.loads(index.read_text(encoding="utf-8"))
    assert len(written) == 2  # 결과 파일 + 결과 모양이 아닌 파일(요약 null)도 기억한다
    assert [r["file"] for r in first] == ["run1.json"]
    # 색인 요약에 표시를 남기면 파일이 그대로(mtime·size 같음)일 때 그 표시가 돌아온다 = 안 읽었다
    key = str((root / "research" / "run1.json").resolve())
    written[key]["summary"]["venue"] = "from-index"
    index.write_text(json.dumps(written), encoding="utf-8")
    again = list_runs([root / "research"], index)
    assert again[0]["venue"] == "from-index"


def test_load_payload_reuses_same_file(root: Path) -> None:
    path = (root / "research" / "run1.json").resolve()
    one = load_payload(path)
    two = load_payload(path)
    assert one is two


def test_runs_endpoint_lists_existing_dirs_only(root: Path, client: TestClient) -> None:
    body = body_of(fetch(client, "/admin/grading/runs"))
    assert body["dirs"] == [str((root / "research").resolve())]
    assert body["runs"][0]["venue"] == "upbit"


def test_trade_rows_filters_symbol_and_derives_exit() -> None:
    rows = trade_rows(RESULT, "base", "KRW-BTC")
    assert len(rows) == 1
    row = rows[0]
    assert row["side"] == 1
    assert row["exit"] == pytest.approx(102.0)  # pyright: ignore[reportUnknownMemberType]
    assert row["opened_ts"] == 1_767_230_100  # 2026-01-01T01:15Z
    assert row["reason"] == "opp_band"
    short = trade_rows(RESULT, "base", "KRW-ETH")[0]
    assert short["side"] == -1
    assert short["exit"] == pytest.approx(50.5)  # pyright: ignore[reportUnknownMemberType]


@pytest.mark.usefixtures("root")
def test_trades_endpoint_maps_venue_to_market(client: TestClient) -> None:
    body = body_of(
        fetch(client, "/admin/grading/trades", file="run1.json", config="base", symbol="KRW-BTC")
    )
    assert body["market"] == "UPBIT"
    assert len(body["trades"]) == 1
    missing = fetch(
        client, "/admin/grading/trades", file="run1.json", config="nope", symbol="KRW-BTC"
    )
    assert missing.status_code == 404


@pytest.mark.usefixtures("root")
def test_file_name_is_sealed(client: TestClient) -> None:
    bad = fetch(
        client, "/admin/grading/trades", file="../run1.json", config="base", symbol="KRW-BTC"
    )
    assert bad.status_code == 400
    absent = fetch(
        client, "/admin/grading/trades", file="run9.json", config="base", symbol="KRW-BTC"
    )
    assert absent.status_code == 404


def test_marks_roundtrip_and_filtering(root: Path, client: TestClient) -> None:
    empty = body_of(
        fetch(client, "/admin/grading/marks", file="run1.json", config="base", symbol="KRW-BTC")
    )
    assert empty == {"grades": {}, "positions": [], "notes": "", "saved_at": None}
    saved = body_of(
        put(
            client,
            "/admin/grading/marks",
            {
                "grades": {"a": "O", "b": "X", "c": "maybe"},
                "positions": [{"id": "p1", "side": 1, "entry": 100.0}, "junk"],
                "notes": "메모",
            },
            file="run1.json",
            config="base",
            symbol="KRW-BTC",
        )
    )
    assert saved["grades"] == {"a": "O", "b": "X"}
    assert saved["positions"] == [{"id": "p1", "side": 1, "entry": 100.0}]
    assert saved["saved_at"]
    again = body_of(
        fetch(client, "/admin/grading/marks", file="run1.json", config="base", symbol="KRW-BTC")
    )
    assert again["grades"] == {"a": "O", "b": "X"}
    assert (
        marks_path("run1.json", "base", "KRW-BTC")
        == root / "grading" / "marks" / "run1__base__KRW-BTC.json"
    )


def test_summarize_counts_stops_and_mdd() -> None:
    rows = trade_rows(RESULT, "base", None)
    assert [r["symbol"] for r in rows] == ["KRW-BTC", "KRW-ETH"]  # 진입 시각이 같으면 원래 순서
    got = summarize(rows)
    assert got["n"] == 2
    assert got["net_sum"] == pytest.approx(0.8)  # pyright: ignore[reportUnknownMemberType]
    assert got["win_rate"] == 50.0
    assert got["exits"] == {"breakout": 1, "opp_band": 1}
    assert got["stops"] == 0
    # 청산 순: ETH(-1.1) 먼저 → 낙폭 1.1, 그 뒤 BTC(+1.9) 로 회복
    assert got["mdd"] == pytest.approx(1.1)  # pyright: ignore[reportUnknownMemberType]
    assert got["worst"] == pytest.approx(-1.1)  # pyright: ignore[reportUnknownMemberType]
    assert summarize([])["n"] == 0


@pytest.mark.usefixtures("root")
def test_trades_endpoint_carries_summary(client: TestClient) -> None:
    body = body_of(
        fetch(client, "/admin/grading/trades", file="run1.json", config="base", symbol="KRW-BTC")
    )
    assert body["summary"]["config"]["n"] == 2
    assert body["summary"]["symbol"]["n"] == 1


def test_candles_rejects_unknown_market_and_bad_range(client: TestClient) -> None:
    assert (
        fetch(
            client,
            "/admin/grading/candles",
            market="MARS",
            symbol="X",
            timeframe="1h",
            start=1,
            end=2,
        ).status_code
        == 400
    )
    assert (
        fetch(
            client,
            "/admin/grading/candles",
            market="UPBIT",
            symbol="X",
            timeframe="1h",
            start=5,
            end=2,
        ).status_code
        == 400
    )
    # 저장소가 안 붙은 시험 앱 — 브로커로 가지 않고 503 으로 말한다.
    assert (
        fetch(
            client,
            "/admin/grading/candles",
            market="UPBIT",
            symbol="X",
            timeframe="1h",
            start=1,
            end=2,
        ).status_code
        == 503
    )
