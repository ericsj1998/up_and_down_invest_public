"""로그 내려받기 — 관리자 전용 (T211 · 2026-09-04).

> 사용자: *"로그를 파일로 만들어 다운로드 할 수 있는 기능."*

## 무엇을 내주나

| kind | 자리 (`logs_root()` 아래) | 고르는 기준 |
|---|---|---|
| `api` · `engine` | `app/<proc>-YYYY-MM-DD.jsonl` — 회전 파일 (`logging/files.py`) | 파일명 날짜 |
| `funds` | `funds/*.json` — 펀드 원장 | 수정 시각 |
| `walkforward` | `walkforward/**` — RUN 저널 | 수정 시각 |
| `reconcile` | `reconcile/**` — 거래소 대조 | 수정 시각 |
| `report_sends` | `report_sends.jsonl` | 항상 |

## 🔴 경로는 사용자가 주지 않는다

요청은 **날짜 범위와 kind** 뿐이다. 파일은 서버가 정해진 디렉터리를 훑어 고른다 — 사용자
문자열로 경로를 만드는 순간 `../` 가 문이 된다 (T202 `_safe_key` 와 같은 원칙). 날짜는
정규식으로, kind 는 허용 목록으로 검사하고 어긋나면 422 다.

문은 미들웨어가 건다: `/admin/logs` 는 `roles.ADMIN_PREFIXES` 에 있다 — 여기서 또 검사하지
않는다 (두 곳이면 언젠가 한쪽만 고친다).
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from updown.common import paths

router = APIRouter(prefix="/admin/logs", tags=["admin-logs"])

KINDS: tuple[str, ...] = ("api", "engine", "funds", "walkforward", "reconcile", "report_sends")
_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_APP_NAME = re.compile(r"\A(?P<proc>api|engine)-(?P<day>\d{4}-\d{2}-\d{2})\.jsonl\Z")
MAX_ZIP_BYTES = 2 * 1024**3
"""한 번에 묶는 상한 — 넘으면 범위를 좁히라고 422. 메모리가 아니라 디스크 임시 파일이지만
배포 머신 디스크도 유한하다."""


def _parse_day(text: str, name: str) -> date:
    """쿼리의 날짜 문자열 → `date`. 정규식으로 모양부터 거른다 — 파일 이름 글로브에 들어가는 값이다.

    Args:
        text: `YYYY-MM-DD`.
        name: 오류 문장에 쓸 인자 이름.

    Returns:
        날짜.

    Raises:
        HTTPException: 422 모양이 아니거나 없는 날짜.
    """
    if not _DATE.match(text):
        raise HTTPException(422, f"{name} 은 YYYY-MM-DD 다 — 받은 값: {text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(422, f"{name} 이 날짜가 아니다: {text!r}") from exc


def _parse_kinds(text: str | None) -> tuple[str, ...]:
    """쿼리의 `kinds` (쉼표 구분) → 로그 종류 묶음. 비우면 전부.

    Args:
        text: `api,engine` 같은 목록. None · 빈 문자열이면 `KINDS` 전부.

    Returns:
        종류 묶음.

    Raises:
        HTTPException: 422 모르는 종류 — 허용 목록을 같이 적는다.
    """
    if not text:
        return KINDS
    picked = tuple(k.strip() for k in text.split(",") if k.strip())
    bad = [k for k in picked if k not in KINDS]
    if bad:
        raise HTTPException(422, f"모르는 kind: {bad} — 허용: {list(KINDS)}")
    return picked


def _entries(root: Path) -> list[dict[str, Any]]:
    """루트 아래 내려받을 수 있는 파일 전부 — kind · 상대경로 · 크기 · 수정시각(UTC) · 날짜."""
    out: list[dict[str, Any]] = []

    def add(kind: str, p: Path, day: str | None) -> None:
        """파일 하나를 목록에 넣는다 — 디렉터리·없는 경로는 건너뛴다.

        Args:
            kind: 로그 종류.
            p: 파일 경로.
            day: 파일명에서 읽은 날짜. 못 읽으면 None.
        """
        if not p.is_file():
            return
        st = p.stat()
        out.append(
            {
                "kind": kind,
                "name": str(p.relative_to(root)).replace("\\", "/"),
                "bytes": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                "day": day or datetime.fromtimestamp(st.st_mtime, UTC).date().isoformat(),
            }
        )

    app_dir = root / "app"
    if app_dir.is_dir():
        for p in sorted(app_dir.glob("*.jsonl")):
            m = _APP_NAME.match(p.name)
            if m:
                add(m.group("proc"), p, m.group("day"))
    for kind, sub, pattern in (
        ("funds", "funds", "*.json"),
        ("walkforward", "walkforward", "**/*"),
        ("reconcile", "reconcile", "**/*"),
    ):
        d = root / sub
        if d.is_dir():
            for p in sorted(d.glob(pattern)):
                add(kind, p, None)
    add("report_sends", root / "report_sends.jsonl", None)
    return out


@router.get("")
async def listing() -> dict[str, Any]:
    """내려받을 수 있는 파일 목록 — 화면이 범위를 고르는 근거.

    Returns:
        파일 행들과 종류별 집계(파일 수·바이트·첫날·마지막 날).
    """
    root = paths.logs_root()
    rows = _entries(root) if root.exists() else []
    by_kind: dict[str, dict[str, Any]] = {}
    for r in rows:
        agg = by_kind.setdefault(r["kind"], {"files": 0, "bytes": 0, "first": None, "last": None})
        agg["files"] += 1
        agg["bytes"] += r["bytes"]
        agg["first"] = r["day"] if agg["first"] is None else min(agg["first"], r["day"])
        agg["last"] = r["day"] if agg["last"] is None else max(agg["last"], r["day"])
    return {"root": str(root), "kinds": list(KINDS), "summary": by_kind, "files": rows}


@router.get("/download")
async def download(
    from_: str = Query(alias="from"),
    to: str = Query(),
    kinds: str | None = Query(default=None),
) -> StreamingResponse:
    """날짜 범위(포함)·kind 로 고른 파일을 zip 하나로.

    Args:
        from_: 시작 날짜 `YYYY-MM-DD` (쿼리 `from`).
        to: 끝 날짜 (포함).
        kinds: 쉼표로 이은 종류. 없으면 전부.

    Returns:
        zip 스트리밍 응답. 파일명은 `updown-logs-<from>_<to>.zip`.

    Raises:
        HTTPException: 422 — 범위가 뒤집혔거나 366일 초과이거나 묶음이 2GB 를 넘는다.
            404 — 로그 루트가 없거나 그 범위에 파일이 없다.

    Note:
        zip 은 디스크 임시 파일에 만들고 스트리밍한 뒤 지운다 — `zipfile` 이 중앙 디렉터리를
        쓰려면 seek 가 필요해 메모리 스트리밍이 안 되고, 2GB 를 메모리에 올릴 수도 없다.
    """
    start = _parse_day(from_, "from")
    end = _parse_day(to, "to")
    if end < start:
        raise HTTPException(422, "to 가 from 보다 앞이다")
    if (end - start) > timedelta(days=366):
        raise HTTPException(422, "범위는 366일 이하로")
    picked = _parse_kinds(kinds)
    root = paths.logs_root()
    if not root.exists():
        raise HTTPException(404, f"로그 루트가 없다: {root}")

    lo, hi = start.isoformat(), end.isoformat()
    chosen = [r for r in _entries(root) if r["kind"] in picked and lo <= r["day"] <= hi]
    if not chosen:
        raise HTTPException(404, f"{lo}~{hi} · {list(picked)} 에 파일이 없다")
    total = sum(r["bytes"] for r in chosen)
    if total > MAX_ZIP_BYTES:
        raise HTTPException(422, f"묶음이 {total / 1024**2:.0f}MB — 범위를 좁혀라 (상한 2GB)")

    with tempfile.NamedTemporaryFile(prefix="updown-logs-", suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for r in chosen:
            zf.write(root / r["name"], arcname=r["name"])

    def stream(path: Path = tmp_path):
        """임시 zip 을 1 MiB 씩 읽어 흘린다.

        Args:
            path: 임시 파일. 기본 인자로 묶는다 — 클로저가 늦게 바뀐 변수를 잡지 않게.

        Yields:
            바이트 조각.
        """
        with path.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                yield chunk

    def cleanup(path: Path = tmp_path) -> None:
        """응답이 끝난 뒤 임시 zip 을 지운다.

        Args:
            path: 임시 파일. 이미 없어도 조용히 넘어간다.
        """
        path.unlink(missing_ok=True)

    fname = f"updown-logs-{lo}_{hi}.zip"
    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        background=BackgroundTask(cleanup),
    )
