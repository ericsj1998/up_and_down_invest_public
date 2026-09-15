"""손익 가리기 — 감사 권한이 없는 사람에게 주는 응답에서 **돈이 얼마가 됐나**를 뺀다 (2026-09-06).

> 사용자: *"게스트들도 차트나 지표, 매매법 등은 확인은 가능하지만, 최종 손익, 년차별 손익 등은
> 무조건 감사 권한이 있어야만 확인 가능한 거야."*

순수 함수다 — 응답 dict 를 받아 같은 모양의 dict 를 돌려주되, 손익 열쇠의 값은 `None` 이고 맨 위에
`redacted: True` 가 붙는다. 화면은 그 표식을 보고 "감사 권한 필요" 를 그린다.

무엇을 가리나:
- 수익률 · 연환산 · 칼마 · 기간별(3년/2년/1년/1달/1일) 수익 · 매매별 손익 (`PNL_KEYS`).
- 자본 곡선 값 (`equity.value` · `equity.fund`) — 곡선은 곧 손익이다.
- 마크다운 표(`{heading, columns, rows}`) 의 행 — 매트릭스·거래소 교차표는 문장 속에 수익률이 있다.

무엇을 남기나: 봉 · 진입/청산 표기(시각·가격·손절선) · 사유별 매매 수 · MDD · 강제청산 수 · 구간 ·
매매법 계보. 위험(낙폭·청산)은 성적이 아니라 **안전** 정보라 남긴다.
"""

from __future__ import annotations

from typing import Any, cast

PNL_KEYS: frozenset[str] = frozenset(
    {
        "total_pct",
        "cagr_pct",
        "calmar",
        "mu_pct",
        "pnl",
        "pnl_pct",
        "gain_pct",
        "cum_pct",
        "total_median_pct",
        "total_worst_pct",
        "total_best_pct",
        "total_p5_pct",
        "total_p95_pct",
        "cvar5_pct",
        "h3y_pct",
        "h2y_pct",
        "h1y_pct",
        "h1m_pct",
        "h1d_pct",
    }
)
"""값을 None 으로 바꾸는 열쇠 — 어디에 있든."""

EQUITY_KEYS: frozenset[str] = frozenset({"value", "fund"})
"""`equity` 아래에서만 가리는 열쇠 — 시장 지수(`market_log`)는 공개 가격이라 남긴다."""


def _is_md_table(obj: dict[str, Any]) -> bool:
    """마크다운 표 노드인가 — `{heading, columns, rows}` 셋을 다 갖고 `rows` 가 목록."""
    return {"heading", "columns", "rows"} <= obj.keys() and isinstance(obj.get("rows"), list)


def _walk(obj: Any, *, under_equity: bool) -> Any:
    """응답 트리를 재귀로 걸으며 손익 열쇠를 None 으로 바꾼 사본을 만든다.

    재귀인 이유: 손익 열쇠는 응답의 정해진 자리에 있지 않다 — 요약·연차별·매매별 목록·중첩 dict
    어디에나 있어서(`PNL_KEYS` "어디에 있든"), 자리를 열거하면 새 응답 하나에 새는 구멍이 생긴다.
    모양을 모르는 채 열쇠 이름으로만 가리는 것이 이 함수의 전부다.

    Args:
        obj: 응답의 한 노드 (dict · list · 스칼라).
        under_equity: `equity` 아래를 걷는 중인가 — 그 안에서만 `EQUITY_KEYS`(`value` · `fund`)
            도 가린다. 같은 이름이라도 `equity` 밖(시장 지수 곡선)의 값은 공개 가격이라 남긴다.

    Returns:
        같은 모양의 새 객체 — 원본은 건드리지 않는다. dict 는 `PNL_KEYS` 열쇠의 값이 None 이고,
        마크다운 표는 행을 비우고 `redacted: True` 를 찍는다 (문장 속 수익률은 열쇠로 못 잡는다).
        스칼라는 그대로.
    """
    if isinstance(obj, dict):
        d = cast("dict[str, Any]", obj)
        if _is_md_table(d):
            return {**d, "rows": [], "redacted": True}
        out: dict[str, Any] = {}
        for key, value in d.items():
            if key in PNL_KEYS or (under_equity and key in EQUITY_KEYS):
                out[key] = None
            else:
                out[key] = _walk(value, under_equity=under_equity or key == "equity")
        return out
    if isinstance(obj, list):
        items = cast("list[Any]", obj)
        return [_walk(v, under_equity=under_equity) for v in items]
    return obj


def redact_pnl(payload: dict[str, Any]) -> dict[str, Any]:
    """손익을 가린 사본을 만든다.

    Args:
        payload: API 응답 (JSON 으로 나갈 dict).

    Returns:
        같은 모양의 새 dict. 손익 열쇠는 None, 마크다운 표의 행은 빈 목록, 맨 위에 `redacted: True`.
        원본은 건드리지 않는다.
    """
    out = _walk(payload, under_equity=False)
    out["redacted"] = True
    return out
