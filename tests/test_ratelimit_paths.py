# pyright: reportUnusedFunction=false
# autouse 픽스처는 pytest 가 이름으로 쓴다
"""T217 — 경로별 weight 계측 (2026-09-04). 짐작으로 세 번 깎고도 89% 라 실측을 넣는다."""

from __future__ import annotations

import pytest

from updown.marketdata import ratelimit as rl


@pytest.fixture(autouse=True)
def _clean() -> None:
    rl._PATHS.clear()  # pyright: ignore[reportPrivateUsage]
    rl._USED_SEEN.clear()  # pyright: ignore[reportPrivateUsage]
    rl._LAST_REPORT.clear()  # pyright: ignore[reportPrivateUsage]
    rl._METERS.clear()  # pyright: ignore[reportPrivateUsage]


def _hdr(used: int) -> dict[str, str]:
    return {"x-mbx-used-weight-1m": str(used)}


def test_paths_are_counted_with_official_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    """헤더 증분은 동시 요청에 비단조라 못 쓴다 — 건수 x 공식 weight 가 정직하다."""
    t = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    rl.observe("BINANCE", _hdr(100), limit=2400, path="/fapi/v1/klines")
    t[0] += 1
    rl.observe("BINANCE", _hdr(90), limit=2400, path="/fapi/v1/klines")  # 비단조여도 상관없다
    t[0] += 1
    rl.observe("BINANCE", _hdr(140), limit=2400, path="/fapi/v1/income")
    rows = rl.paths_1m("BINANCE")
    assert rows[0] == {"path": "/fapi/v1/income", "n": 1, "weight": 30}
    assert rows[1] == {"path": "/fapi/v1/klines", "n": 2, "weight": 4}


def test_peak_used_is_the_ip_wide_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    """헤더 최댓값이 IP 전체 소모 — 우리 추정과의 차가 바깥 클라이언트의 몫이다."""
    t = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    rl.observe("BINANCE", _hdr(2000), limit=2400, path="/a")
    t[0] += 2
    rl.observe("BINANCE", _hdr(5), limit=2400, path="/b")
    assert rl.peak_used_1m("BINANCE") == 2000
    t[0] += 61
    assert rl.peak_used_1m("BINANCE") == 0


def test_rows_expire_after_60s(monkeypatch: pytest.MonkeyPatch) -> None:
    t = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    rl.observe("BINANCE", _hdr(10), limit=2400, path="/a")
    t[0] += 61
    assert rl.paths_1m("BINANCE") == []


def test_no_path_means_no_counting() -> None:
    rl.observe("BINANCE", _hdr(10), limit=2400)
    assert rl.paths_1m("BINANCE") == []
