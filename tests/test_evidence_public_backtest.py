"""공개 백테스트(견본)는 감사 없이도 그대로 — 목록에서 공개 항목만 되돌린다 (2026-09-08)."""

from updown.apps.api.evidence import unredact_public


def test_only_public_rows_come_back_unredacted() -> None:
    originals = [
        {"id": "e1", "total_pct": 100.0},
        {"id": "sample", "total_pct": 2.0, "public": True},
    ]
    redacted = [
        {"id": "e1", "total_pct": None},
        {"id": "sample", "total_pct": None, "public": True},
    ]
    got = unredact_public(originals, redacted)
    assert got[0]["total_pct"] is None
    assert got[1]["total_pct"] == 2.0
