"""종목 표의 예비 신호 · 포지션에 **다리 이름**이 실린다 (사용자 2026-09-30).

> *"예비 신호와, 구체적으로 어떤 매매법으로 해당 포지션에 들어갔는지 종목 표에서 보여줬으면 해."*
> *"예비 신호에도 종목 다리 이름이 보이게 해달라는 요청이라는 걸 이해한거 맞지?"* — 맞다.
"""

from __future__ import annotations

from typing import Any

import pytest

from updown.apps.api import rebalancer as api

TABLE = {"private_strategy@0.1.0": "MACD 롱", "private_strategy@0.1.0": "돌파 롱"}


@pytest.fixture(autouse=True)
def _named(monkeypatch: pytest.MonkeyPatch) -> None:
    """`leg_label` 대역 — 귀속 키 → 짧은 이름. 모르는 키는 빈 문자열(진짜 함수와 같은 폴백 계약)."""
    monkeypatch.setattr(api, "leg_label", lambda _session, key: TABLE.get(key, ""))


def _named_preview(preview: dict[str, Any] | None) -> dict[str, Any] | None:
    return api._named_preview(object(), preview)  # pyright: ignore[reportPrivateUsage]


class TestNamedPreview:
    def test_signal_carries_the_leg_short_name(self) -> None:
        got = _named_preview(
            {"kind": "signal", "side": "롱", "leg": "private_strategy@0.1.0", "frame": "4h"}
        )
        assert got is not None and got["name"] == "MACD 롱"
        assert got["leg"] == "private_strategy@0.1.0"  # 원래 칸은 그대로

    def test_unknown_leg_falls_back_to_its_id(self) -> None:
        got = _named_preview({"kind": "waiting", "side": "숏", "leg": "custom@1.0.0", "frame": ""})
        assert got is not None and got["name"] == "custom"

    def test_none_stays_none(self) -> None:
        assert _named_preview(None) is None

    def test_original_dict_is_not_mutated(self) -> None:
        raw: dict[str, Any] = {
            "kind": "signal",
            "side": "롱",
            "leg": "private_strategy@0.1.0",
        }
        _named_preview(raw)
        assert "name" not in raw  # PREVIEWS 캐시의 원본을 건드리지 않는다
