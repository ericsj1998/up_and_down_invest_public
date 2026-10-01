"""DB 에 저장한 매매법 선언을 읽는다 — `playbook_decls` (T349 · 2026-10-02).

파일(`config/playbooks.yml`)이 SSoT 이고 DB 는 덧붙이기다(같은 id 는 파일이 이긴다).
로더(`select.load_playbooks`)가 동기 함수라(세션 · 연구 · 시험 전부가 부른다) 여기서도
**동기 psycopg** 로 읽고, 결과를 짧게(20초) 캐시한다 — API 가 선언을 넣으면 `invalidate()` 로
자기 프로세스 캐시를 비우고, 다른 프로세스(엔진)는 다음 만료 뒤에 본다.

켜짐: `DATABASE_URL` 이 있고 `UPDOWN_PLAYBOOK_DB` 가 "0" 이 아닐 때. 없으면(연구 · 시험) 빈 목록.
실패: 표가 아직 없거나(마이그레이션 전) DB 에 못 닿으면 **마지막으로 읽은 것**을 쓰고 ERROR
로그를 남긴다 · 한 번도 못 읽었으면 빈 목록 — 파일 선언으로는 계속 돈다(규칙 #8-1: 조회
실패가 손절 · 청산을 막으면 안 된다). 상태는 `status()` 로 화면 · API 가 본다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast

from updown.common.logging.setup import get_logger

_logger = get_logger(__name__)
TTL_S = 20.0
ENV_FLAG = "UPDOWN_PLAYBOOK_DB"


@dataclass
class DeclRow:
    """DB 선언 한 줄.

    `body` 는 YAML 블록(`playbooks.<id>`)과 같은 모양 · `basket` 은 `by_playbook.<id>` 블록과
    같은 모양(없으면 None).
    """

    playbook_id: str
    body: dict[str, Any]
    basket: dict[str, Any] | None = None
    created_by: str = ""


@dataclass
class _State:
    rows: tuple[DeclRow, ...] = ()
    fetched_at: float = 0.0
    ever: bool = False
    last_error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


_STATE = _State()


def enabled() -> bool:
    """DB 선언을 읽는가 — `DATABASE_URL` 이 있고 `UPDOWN_PLAYBOOK_DB` 가 "0" 이 아니면."""
    return bool(os.environ.get("DATABASE_URL")) and os.environ.get(ENV_FLAG, "1") != "0"


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    # SQLAlchemy URL(`postgresql+psycopg://…`)을 psycopg DSN 으로 — 드라이버 꼬리만 뗀다
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    loaded: object = json.loads(str(value))
    if not isinstance(loaded, dict):
        raise TypeError("선언 블록은 매핑이어야 한다")
    return cast("dict[str, Any]", loaded)


def _fetch() -> tuple[DeclRow, ...]:
    import psycopg  # 지연 import — 연구 · 시험 경로가 DB 드라이버를 안 들게

    with psycopg.connect(_dsn(), connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT playbook_id, body, basket, created_by FROM playbook_decls "
            "WHERE enabled ORDER BY created_at, playbook_id"
        )
        out: list[DeclRow] = []
        for record in cur.fetchall():
            pid, body, basket, by = cast("tuple[object, object, object, object]", record)
            out.append(
                DeclRow(
                    str(pid),
                    _as_dict(body),
                    None if basket is None else _as_dict(basket),
                    str(by or ""),
                )
            )
        return tuple(out)


def rows() -> tuple[DeclRow, ...]:
    """켜져 있으면 DB 선언 줄들(캐시 20초) · 아니면 빈 튜플."""
    if not enabled():
        return ()
    with _STATE.lock:
        now = time.monotonic()
        if _STATE.ever and now - _STATE.fetched_at < TTL_S:
            return _STATE.rows
        try:
            got = _fetch()
        except Exception as exc:
            # 어떤 DB 오류든 파일 선언으로 계속 돈다 · 상태로 드러낸다 (규칙 #8-1)
            _STATE.last_error = str(exc)[:200]
            _STATE.fetched_at = now
            _logger.error(
                "playbook_db_unreadable",
                payload={"error": _STATE.last_error, "stale_rows": len(_STATE.rows)},
            )
            return _STATE.rows
        _STATE.rows, _STATE.fetched_at, _STATE.ever, _STATE.last_error = got, now, True, ""
        return got


def invalidate() -> None:
    """캐시를 비운다 — API 가 선언을 넣거나 지운 뒤."""
    with _STATE.lock:
        _STATE.fetched_at = 0.0
        _STATE.ever = False


def status() -> dict[str, Any]:
    """화면 · API 용 상태 — 켜짐 · 줄 수 · 마지막 오류."""
    return {
        "enabled": enabled(),
        "rows": len(_STATE.rows),
        "ever": _STATE.ever,
        "last_error": _STATE.last_error,
    }


def basket_symbols(basket: dict[str, Any] | None) -> list[str]:
    """바스켓 블록의 종목들 — `members[].symbol` 만 (없으면 빈 목록)."""
    if not basket:
        return []
    members = cast("list[object]", basket.get("members") or [])
    out: list[str] = []
    for member in members:
        if isinstance(member, dict):
            symbol = cast("dict[str, Any]", member).get("symbol")
            if symbol:
                out.append(str(symbol))
    return out


def baskets() -> dict[str, list[str]]:
    """DB 선언의 바스켓 `{id: 종목들}` — 바스켓이 없는 줄은 뺀다."""
    out: dict[str, list[str]] = {}
    for row in rows():
        symbols = basket_symbols(row.basket)
        if symbols:
            out[row.playbook_id] = symbols
    return out
