"""T266-2 펀드 보관 · T266-3 대화 소프트 삭제 판정 — 순수 부분."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from updown.apps.api.ai_chat import _mine  # pyright: ignore[reportPrivateUsage]
from updown.apps.api.rebalancer import _archive_fund_file  # pyright: ignore[reportPrivateUsage]
from updown.common.db.models.accounts import ChatThread


class TestFundArchive:
    def test_dropped_fund_is_kept_with_dropped_at(self, tmp_path: Path) -> None:
        (tmp_path / "f1.json").write_text(
            json.dumps({"fund_id": "f1", "label": "코어", "members": [{"symbol": "BTC_USDT"}]}),
            encoding="utf-8",
        )
        target = _archive_fund_file("f1", root=tmp_path)
        assert target is not None
        assert target == tmp_path / "archive" / "f1.json"
        assert not (tmp_path / "f1.json").exists()  # 목록(루트 *.json)에서는 사라진다
        kept = json.loads(target.read_text(encoding="utf-8"))
        assert kept["label"] == "코어"
        assert kept["members"][0]["symbol"] == "BTC_USDT"
        assert kept["dropped_at"].startswith("2026-")

    def test_missing_file_is_none_not_error(self, tmp_path: Path) -> None:
        assert _archive_fund_file("nope", root=tmp_path) is None
        assert not (tmp_path / "archive").exists()

    def test_corrupt_file_still_archives_the_id(self, tmp_path: Path) -> None:
        (tmp_path / "f2.json").write_text("{not json", encoding="utf-8")
        target = _archive_fund_file("f2", root=tmp_path)
        assert target is not None
        kept = json.loads(target.read_text(encoding="utf-8"))
        assert kept["fund_id"] == "f2"
        assert "dropped_at" in kept


class TestThreadVisibility:
    def _row(self, email: str = "me@example.com") -> ChatThread:
        return ChatThread(id="t1", email=email, title="x", model="m", messages=[])

    def test_mine_requires_owner_and_not_deleted(self) -> None:
        row = self._row()
        assert _mine(row, "me@example.com")
        assert not _mine(row, "other@example.com")
        assert not _mine(None, "me@example.com")
        row.deleted_at = datetime(2026, 9, 10, tzinfo=UTC)
        assert not _mine(row, "me@example.com")  # 지운 대화는 없는 것과 같다
