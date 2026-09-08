"""매매 라벨 저장소 (2026-09-03 · 사용자 요구 — 화면에서 직접 표시).

사람이 차트에 **자기 판단**을 표시한다: 롱/숏 진입선 · 스왑 · 근거 캔들 · 지지/저항.
그 표시로 규칙을 뽑고, 뽑은 규칙은 **표시하지 않은 구간에서 백테스트로 검증**한다.

## ⚠️ 절대 규칙 #11 과의 관계

규칙 #11 은 *"사람 눈으로 정답지를 만들지 않는다"* 이고, 같은 조항의 예외가
*"그 판단이 규칙으로 환원되어 코드에 남는가"* 다. 그래서 이 저장소의 계약은:

```
⭕ 허용   표시 → 가설 생성 → 규칙 환원 → **표시 안 한 구간**에서 성과 검증
⛔ 금지   표시를 정답지로 두고 "일치율" 을 성과로 보고하는 것
⛔ 금지   표시를 학습 라벨로 모델에 직접 먹이는 것 (규칙이 안 남는다)
```

저장 위치: `logs/labels/<세션이름>.json`. DB 를 안 쓰는 이유는 이것이 **연구 산출물**
이고 스키마가 실험마다 바뀌기 때문이다 — 굳으면 그때 옮긴다.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException

from updown.common.logging.setup import get_logger

router = APIRouter(prefix="/labels", tags=["labels"])
_logger = get_logger("api.labels")

LABEL_ROOT = Path(os.environ.get("LABEL_ROOT", "logs/labels"))
"""표시를 남기는 곳 — 연구 산출물이라 파일이다 (스키마가 실험마다 바뀐다)."""

_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

KINDS = {"long", "short", "swap", "evidence", "level"}
"""표시 종류 — 진입선(롱/숏) · 스왑 · 근거 캔들 · 지지저항 선."""


def _path(name: str) -> Path:
    """세션 파일 경로 — 이름을 강제한다 (경로 탈출 차단 · 보안 리뷰 2026-09-03)."""
    if not _NAME.fullmatch(name):
        raise HTTPException(422, f"세션 이름은 영숫자·_·- 64자다 — 받은 값: {name!r}")
    return LABEL_ROOT / f"{name}.json"


@router.get("")
async def listing() -> dict[str, Any]:
    """저장된 표시 세션 목록.

    Returns:
        `{sessions: [...]}`. 깨진 파일은 목록에서 건너뛴다.
    """
    if not LABEL_ROOT.exists():
        return {"sessions": []}
    rows: list[dict[str, Any]] = []
    for path in sorted(LABEL_ROOT.glob("*.json")):
        try:
            body = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "name": path.stem,
                "symbol": body.get("symbol", ""),
                "timeframe": body.get("timeframe", ""),
                "marks": len(body.get("marks", [])),
                "saved_at": body.get("saved_at", ""),
            }
        )
    return {"sessions": rows}


@router.get("/{name}")
async def load(name: str) -> dict[str, Any]:
    """표시 하나를 읽는다.

    Args:
        name: 세션 이름.

    Returns:
        저장된 표시. 파일이 없으면 빈 표시 — 처음 여는 세션이 오류가 아니다.

    Raises:
        HTTPException: 400 — 파일은 있는데 읽을 수 없다.
    """
    path = _path(name)
    if not path.exists():
        return {"name": name, "marks": [], "note": ""}
    try:
        return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"{name} 표시 파일을 읽을 수 없다: {exc}") from exc


@router.put("/{name}")
async def save(name: str, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """표시를 저장한다 (덮어쓰기).

    Args:
        name: 세션 이름 (영숫자·_·-).
        payload: `{symbol, timeframe, market, marks: [...], note}`.
            `marks` 한 줄 = `{kind, ts, price, side?, note?}`.

    Returns:
        `{name, marks, saved_at}`.

    Raises:
        HTTPException: 422 — `marks` 가 목록이 아니거나 모르는 종류.

    Note:
        ⚠️ **종류를 검증한다** — 오타로 들어온 표시는 나중에 분석에서 조용히 빠진다
        (규칙 #8). 모르는 종류면 422 로 지금 알린다.
    """
    raw: object = payload.get("marks") or []
    if not isinstance(raw, list):
        raise HTTPException(422, "marks 는 목록이어야 한다")
    marks = cast("list[dict[str, Any]]", raw)
    for item in marks:
        kind = str(item.get("kind", ""))
        if kind not in KINDS:
            raise HTTPException(422, f"모르는 표시 종류: {kind!r} — 가능: {sorted(KINDS)}")
    body = {
        "name": name,
        "symbol": str(payload.get("symbol", "")),
        "timeframe": str(payload.get("timeframe", "")),
        "market": str(payload.get("market", "")),
        "note": str(payload.get("note", "")),
        "marks": marks,
        "saved_at": datetime.now(UTC).isoformat(),
    }
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    _logger.info("labels_saved", payload={"name": name, "marks": len(marks)})
    return {"name": name, "marks": len(marks), "saved_at": body["saved_at"]}


@router.delete("/{name}")
async def drop(name: str) -> dict[str, str]:
    """표시 세션을 지운다.

    Args:
        name: 세션 이름.

    Returns:
        `{name, deleted}`. 없던 세션도 성공이다 (멱등).
    """
    path = _path(name)
    if path.exists():
        path.unlink()
    return {"name": name, "deleted": "yes"}
