"""WhaleSurfer 단독 API 앱 — 본체 API 와 다른 프로세스 · 다른 포트 (T442 §1-1 ② · 2026-10-09).

로컬 실행(연구 PC · 저장소 루트):

    set -a; . ./.env.dev; set +a
    uv run --no-sync uvicorn whalesurfer.api.app:app --port 8010

화면은 `whalesurfer/web`(Vite · 5174)이 `/api` 를 이 포트로 넘긴다. 쓰는 설정은 EDGAR 연락처
(`EDGAR_USER_AGENT`) 하나다 — 본체 설정 파일을 그대로 읽지만 DB · 거래소 키는 쓰지 않는다.
🔴 인증이 없다 — 로컬 전용. 공개하려면 T442 §1-1 ③(별도 서버 · 인증 · 요금제)이 먼저다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from updown.common.config import load_settings
from updown.common.logging.setup import configure_logging
from whalesurfer.api.routes import attach_whalesurfer, router


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    settings = load_settings()
    configure_logging(settings.log_level)
    attach_whalesurfer(settings)
    try:
        yield
    finally:
        attach_whalesurfer(None)


def create_app() -> FastAPI:
    """WhaleSurfer 라우터만 붙인 앱을 만든다."""
    app = FastAPI(title="WhaleSurfer", version="0.1.0", lifespan=_lifespan)
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": True, "app": "whalesurfer"}

    return app


app = create_app()
