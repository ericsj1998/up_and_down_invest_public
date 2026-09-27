"""휴대폰 · PC 알림 — 브라우저 웹 푸시 (사용자 2026-09-27).

> *"진입, 익절, 손절, 청산, 경보 등은 컴퓨터, 핸드폰 등 알림으로 띄워주면 좋겠어."*

사이트에서 "알림 켜기" 를 누르면 그 브라우저(PC 크롬 · 안드로이드 크롬 · 아이폰은 홈 화면
앱)가 구독을 만들어 여기로 보낸다. 창을 닫아도 서비스 워커(`web/public/sw.js`)가 띄운다.

- **VAPID 키**는 처음 쓸 때 만들어 산출물 볼륨(`notify/vapid.pem` · 권한 600)에 둔다.
  블루그린 두 슬롯이 같은 볼륨을 보므로 같은 키다 — env 를 사람이 고칠 일이 없다.
  🔴 개인 키는 로그 · 응답 어디에도 안 나간다(공개 키만 준다).
- **구독**은 서버 모드별 파일(`subscriptions-live.json` · `-demo.json`).
- **감시 루프**(`notify_loop` · 거래 리더에서만)가 10초마다 판 원장을
  `orchestration.notify.diff_run` 으로 견줘 새 일만 보낸다. 매매 경로는 안 건드린다 —
  알림이 실패해도 매매는 그대로다(로그만 남긴다).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Body, HTTPException, Request

from updown.analysis.playbook.select import load_playbooks
from updown.common import paths
from updown.common.logging.setup import get_logger
from updown.orchestration.notify import Note, RunState, diff_run

router = APIRouter(prefix="/notify", tags=["notify"])
_logger = get_logger("api.notify")

POLL_S = 10.0
"""감시 루프 간격(초)."""

CONTACT = os.environ.get("WEBPUSH_CONTACT", "mailto:noreply@example.com")
"""VAPID `sub` 연락처 — 푸시 서비스가 연락하는 곳. 개인 메일을 넣지 않는다(공개 저장소)."""

_LOCK = asyncio.Lock()


def _mode() -> str:
    """실계좌 서버면 `live` · 아니면 `demo`."""
    from updown.apps.api.auth import on_real_money

    return "live" if on_real_money() else "demo"


def key_path() -> Path:
    """VAPID 개인 키 파일(PEM · 600)."""
    return paths.under("notify", "vapid.pem")


def subs_path() -> Path:
    """이 서버 모드의 구독 목록 파일."""
    return paths.under("notify", f"subscriptions-{_mode()}.json")


def load_or_create_key(path: Path | None = None) -> ec.EllipticCurvePrivateKey:
    """VAPID 개인 키 — 없으면 만들어 둔다(P-256).

    Args:
        path: 키 파일. None 이면 `key_path()`.

    Returns:
        개인 키.

    Raises:
        TypeError: 파일의 키가 EC 키가 아니다.

    Note:
        두 슬롯이 동시에 처음 만드는 경합은 `O_EXCL` 로 막는다 — 진 쪽은 이긴 쪽 파일을 읽는다.
    """
    target = path or key_path()
    if target.exists():
        key = serialization.load_pem_private_key(target.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise TypeError("VAPID 키가 EC 키가 아니다")
        return key
    target.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return load_or_create_key(target)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    _logger.info("notify_vapid_created", payload={"note": "웹 푸시 키를 만들었다 — 파일에만"})
    return key


def public_key_b64(key: ec.EllipticCurvePrivateKey) -> str:
    """브라우저 `applicationServerKey` — 압축 안 한 공개 점(65바이트) base64url(패딩 없음)."""
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return urlsafe_b64encode(raw).rstrip(b"=").decode()


def _as_dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _read_subs() -> list[dict[str, Any]]:
    path = subs_path()
    if not path.exists():
        return []
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    rows = cast("list[object]", raw) if isinstance(raw, list) else []
    return [row for row in map(_as_dict, rows) if row.get("endpoint")]


def _write_subs(subs: list[dict[str, Any]]) -> None:
    path = subs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(subs, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _email(request: Request) -> str:
    who = getattr(request.state, "caller", None)
    return str(getattr(who, "email", "") or "")


@router.get("/key")
async def vapid_key() -> dict[str, Any]:
    """브라우저가 구독을 만들 때 쓰는 공개 키.

    Returns:
        `{public_key, mode}`.
    """
    return {"public_key": public_key_b64(load_or_create_key()), "mode": _mode()}


@router.post("/subscribe")
async def subscribe(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """이 브라우저의 구독을 적는다(같은 endpoint 면 덮어쓴다).

    Args:
        request: 요청 — 누가 구독했나(이메일)를 적는다.
        payload: `{subscription: {endpoint, keys: {p256dh, auth}}, device?}`.

    Returns:
        `{ok, count}` — 이 모드의 구독 수.

    Raises:
        HTTPException: 400 — 구독 모양이 틀렸다.
    """
    sub = _as_dict(payload.get("subscription"))
    keys = _as_dict(sub.get("keys"))
    endpoint = str(sub.get("endpoint", ""))
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(400, "구독 모양이 틀렸다 — endpoint · keys.p256dh · keys.auth 필요")
    row: dict[str, Any] = {
        "endpoint": endpoint,
        "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])},
        "email": _email(request),
        "device": str(payload.get("device", ""))[:80],
        "at": datetime.now(UTC).isoformat(),
    }
    async with _LOCK:
        subs = [s for s in _read_subs() if s.get("endpoint") != endpoint] + [row]
        _write_subs(subs)
    _logger.info("notify_subscribed", payload={"count": len(subs), "device": row["device"]})
    return {"ok": True, "count": len(subs)}


@router.post("/unsubscribe")
async def unsubscribe(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """구독을 지운다.

    Args:
        payload: `{endpoint}`.

    Returns:
        `{ok, count}`.
    """
    endpoint = str(payload.get("endpoint", ""))
    async with _LOCK:
        subs = [s for s in _read_subs() if s.get("endpoint") != endpoint]
        _write_subs(subs)
    return {"ok": True, "count": len(subs)}


@router.post("/diag")
async def diag(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """폰이 권한을 못 받았을 때 무엇이라 답했나 — 로그 한 줄(`notify_client_diag`) (2026-09-27).

    사용자 "핸드폰에서는 알림 허용이 안 뜬다 · PC 크롬은 떴다" — nginx 로그로는 요청이 없다는
    것만 보이고
    브라우저의 답(`denied` · `default`)은 안 보인다. 값은 짧은 낱말뿐이다(개인 정보 없음).

    Args:
        payload: `{answer, before, standalone, device}`.

    Returns:
        `{ok}`.
    """
    _logger.info(
        "notify_client_diag",
        payload={
            "answer": str(payload.get("answer", ""))[:16],
            "before": str(payload.get("before", ""))[:16],
            "standalone": bool(payload.get("standalone", False)),
            "device": str(payload.get("device", ""))[:40],
        },
    )
    return {"ok": True}


@router.post("/test")
async def trial(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """시험 알림 — `endpoint` 가 있으면 그 기기로만, 없으면 구독한 기기 전부로.

    Args:
        payload: `{endpoint?}`.

    Returns:
        `{sent, removed, failed}`.
    """
    note = Note(
        kind="test", title="알림 시험 · 업 앤 다운", body="이 알림이 보이면 연결됐다.", tag="test"
    )
    only = str(payload.get("endpoint", "") or "") or None
    return await push(note, only=only)


def _send_one(sub: dict[str, Any], data: str, key_file: str) -> int | None:
    """한 기기로 보낸다(동기 · 스레드에서). 성공이면 None · 실패면 HTTP 상태(모르면 0)."""
    from pywebpush import (
        WebPushException,
        webpush,  # pyright: ignore[reportUnknownVariableType]
    )

    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
            data=data,
            vapid_private_key=key_file,
            vapid_claims={"sub": CONTACT},
            ttl=3600,
        )
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        return int(getattr(response, "status_code", 0) or 0)
    return None


async def push(note: Note, *, only: str | None = None) -> dict[str, Any]:
    """알림 하나를 구독한 기기들로 — 없어진 구독(404 · 410)은 지운다.

    Args:
        note: 알림.
        only: 이 endpoint 로만.

    Returns:
        `{sent, removed, failed}`.
    """
    subs = [s for s in _read_subs() if only is None or s.get("endpoint") == only]
    if not subs:
        return {"sent": 0, "removed": 0, "failed": 0}
    load_or_create_key()
    body = {
        "title": note.title,
        "body": note.body,
        "tag": note.tag,
        "kind": note.kind,
        "url": "/console",
    }
    data = json.dumps(body, ensure_ascii=False)
    key_file = str(key_path())
    results = await asyncio.gather(
        *(asyncio.to_thread(_send_one, sub, data, key_file) for sub in subs)
    )
    gone = {sub["endpoint"] for sub, code in zip(subs, results, strict=True) if code in (404, 410)}
    failed = sum(1 for code in results if code is not None and code not in (404, 410))
    if gone:
        async with _LOCK:
            _write_subs([s for s in _read_subs() if s.get("endpoint") not in gone])
    sent = len(subs) - len(gone) - failed
    _logger.info(
        "notify_sent",
        payload={
            "kind": note.kind,
            "tag": note.tag,
            "sent": sent,
            "removed": len(gone),
            "failed": failed,
        },
    )
    return {"sent": sent, "removed": len(gone), "failed": failed}


def _labels() -> dict[str, str]:
    """매매법 id → 짧은 이름(`short_label` → `label` → id)."""
    return {b.playbook_id: (b.short_label or b.label or b.playbook_id) for b in load_playbooks()}


def _label_of(labels: dict[str, str], attribution: str) -> str:
    """귀속 키(`playbook@version`) → 짧은 이름."""
    pid = attribution.split("@")[0]
    return labels.get(pid, pid)


async def notify_loop() -> None:
    """거래 리더에서 10초마다 판 원장을 견줘 새 일(진입 · 청산 · 불타기 · 경보)만 알린다.

    Note:
        처음 본 판은 모습만 적는다(재시작 직후 쏟아짐 방지). 구독이 없으면 견주기만 한다.
        실패는 로그만 남기고 다음 바퀴로 — 매매를 막지 않는다.
    """
    from updown.apps.api.walkforward import LIVE_RUNNERS

    states: dict[str, RunState] = {}
    labels: dict[str, str] = {}
    loaded_at: datetime | None = None
    while True:
        try:
            now = datetime.now(UTC)
            if loaded_at is None or (now - loaded_at).total_seconds() > 3600:
                with contextlib.suppress(Exception):
                    labels = _labels()
                loaded_at = now
            names = dict(labels)
            live = dict(LIVE_RUNNERS)
            for run_id, runner in live.items():
                symbol = runner.instrument.symbol
                notes, states[run_id] = diff_run(
                    run_id,
                    symbol.replace("_USDT", "").replace("USDT", ""),
                    list(runner.ledger.records),
                    runner.ledger.realized_cash,
                    list(runner.findings),
                    states.get(run_id),
                    now,
                    label_of=lambda attr, names=names: _label_of(names, attr),
                )
                for note in notes:
                    await push(note)
            for gone in set(states) - set(live):
                states.pop(gone, None)
        except Exception as exc:  # 알림은 매매를 막지 않는다
            _logger.warning("notify_loop_failed", payload={"error": str(exc)[:200]})
        await asyncio.sleep(POLL_S)
