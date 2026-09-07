"""손익 가리기 — 감사 권한 없는 응답에서 돈이 얼마가 됐나를 뺀다 (2026-09-06)."""

from __future__ import annotations

from typing import Any

from updown.common.security.redact import redact_pnl
from updown.common.security.roles import Role, may_audit


class TestMayAudit:
    def test_admin_always(self) -> None:
        assert may_audit(Role.ADMIN, False) is True

    def test_granted_flag_for_others(self) -> None:
        assert may_audit(Role.VIEWER, True) is True
        assert may_audit(Role.TRADER, False) is False
        assert may_audit(Role.GUEST, False) is False

    def test_anonymous_never(self) -> None:
        assert may_audit(None, True) is False


class TestRedactPnl:
    def test_pnl_keys_become_none_everywhere(self) -> None:
        payload: dict[str, Any] = {
            "total_pct": 169610.8,
            "mdd_pct": 50.08,
            "trades": [{"pnl": 12.3, "entry": 100.0, "reason": "soft"}],
            "summary": {"cvar5_pct": -3.0, "liquidated_runs": 2},
        }
        out = redact_pnl(payload)
        assert out["redacted"] is True
        assert out["total_pct"] is None
        assert out["trades"][0]["pnl"] is None
        assert out["summary"]["cvar5_pct"] is None
        # 위험·구조 정보는 남는다
        assert out["mdd_pct"] == 50.08
        assert out["trades"][0]["entry"] == 100.0
        assert out["summary"]["liquidated_runs"] == 2

    def test_equity_curve_hidden_but_market_index_kept(self) -> None:
        payload: dict[str, Any] = {
            "equity": {
                "t": [1, 2],
                "value": [1.0, 1.1],
                "fund": [1.0, 1.2],
                "market_log": [0.0, 0.1],
            }
        }
        out = redact_pnl(payload)
        assert out["equity"]["value"] is None
        assert out["equity"]["fund"] is None
        assert out["equity"]["market_log"] == [0.0, 0.1]
        assert out["equity"]["t"] == [1, 2]

    def test_markdown_tables_lose_rows(self) -> None:
        payload = {"matrix": {"heading": "h", "columns": ["a"], "rows": [["+12%"]]}}
        out = redact_pnl(payload)
        assert out["matrix"]["rows"] == []
        assert out["matrix"]["redacted"] is True

    def test_original_untouched(self) -> None:
        payload: dict[str, Any] = {"total_pct": 1.0, "rows": [{"pnl": 2.0}]}
        redact_pnl(payload)
        assert payload["total_pct"] == 1.0
        assert payload["rows"][0]["pnl"] == 2.0
