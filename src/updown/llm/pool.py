"""모델 풀 설정 + 팬아웃 (Phase 5 §5-4).

## 동시에 던진다

10종을 순차로 부르면 10배 걸린다. `asyncio.gather` 로 한 번에 던지고, **느린 모델이
전체를 잡지 않도록** 모델별 타임아웃을 각자 건다.

## 부분 실패가 정상이다

일부는 반드시 실패한다. 그래서 `gather` 가 예외로 죽지 않도록 어댑터가 실패를 **값**으로
돌려주며(`port.py`), 여기서는 그 값을 그대로 모아 돌려준다 — 무효응답률이 비교표의 칸이다.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from updown.llm.port import LlmClient, LlmOutcome

DEFAULT_POOL_PATH = Path("config/llm_pool.yml")


class PoolConfigError(ValueError):
    """풀 설정을 읽을 수 없다."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """후보 모델 하나.

    Attributes:
        id: 모델 id (API 에 그대로 보낸다).
        rank: 사용자가 매긴 순위.
        note: 왜 골랐는지. 화면에 그대로 보여 준다 — 근거 없이 켜고 끄지 않게.
    """

    id: str
    rank: int
    note: str


@dataclass(frozen=True, slots=True)
class PoolConfig:
    """팬아웃 설정.

    Attributes:
        endpoint: 완성 엔드포인트.
        models: 후보들 (rank 오름차순).
        timeout_seconds: 모델별 타임아웃.
        max_concurrency: 동시 실행 상한.
        schema_retries: 스키마 위반 시 재시도 횟수.
        temperature: 표집 온도.
    """

    endpoint: str
    models: tuple[ModelSpec, ...]
    timeout_seconds: float
    max_concurrency: int
    schema_retries: int
    temperature: float


def load_pool(path: Path | None = None) -> PoolConfig:
    """풀 설정을 읽는다.

    Args:
        path: 설정 파일. 기본은 `config/llm_pool.yml`.

    Returns:
        설정.

    Raises:
        PoolConfigError: 파일이 없거나 형식이 틀린 경우. **기본값으로 때우지 않는다** —
            모델 목록이 조용히 비면 "전 모델 무효응답" 으로 보인다 (절대 규칙 #8).
    """
    target = path or DEFAULT_POOL_PATH
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PoolConfigError(f"{target} 를 읽을 수 없다: {exc}") from exc
    if not isinstance(raw, dict):
        raise PoolConfigError(f"{target} 최상위가 매핑이 아니다")

    document: dict[str, Any] = cast(dict[str, Any], raw)
    entries = document.get("models")
    if not isinstance(entries, list) or not entries:
        raise PoolConfigError(f"{target} 에 models 목록이 없다")

    models: list[ModelSpec] = []
    for item in entries:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict) or "id" not in item:
            raise PoolConfigError(f"models 항목에 id 가 없다: {item!r}")
        entry: dict[str, Any] = cast(dict[str, Any], item)
        models.append(
            ModelSpec(
                id=str(entry["id"]),
                rank=int(str(entry.get("rank", 0))),
                note=str(entry.get("note", "")),
            )
        )

    fanout = document.get("fanout")
    settings: dict[str, Any] = cast(dict[str, Any], fanout) if isinstance(fanout, dict) else {}
    return PoolConfig(
        endpoint=str(document.get("endpoint", "")),
        models=tuple(sorted(models, key=lambda spec: spec.rank)),
        timeout_seconds=float(str(settings.get("timeout_seconds", 90))),
        max_concurrency=int(str(settings.get("max_concurrency", 10))),
        schema_retries=int(str(settings.get("schema_retries", 1))),
        temperature=float(str(settings.get("temperature", 0.2))),
    )


async def fan_out(
    client: LlmClient,
    config: PoolConfig,
    system_prompt: str,
    user_prompt: str,
    models: tuple[str, ...] | None = None,
) -> list[LlmOutcome]:
    """여러 모델에 **같은 프롬프트**를 동시에 던진다.

    Args:
        client: LLM 포트 구현.
        config: 팬아웃 설정.
        system_prompt: 페르소나.
        user_prompt: 지시 + 차트 데이터.
        models: 부를 모델 id 들. None 이면 풀 전체.

    Returns:
        요청 순서 그대로의 결과들. 실패도 값으로 들어 있다.

    Note:
        🔴 **같은 프롬프트를 준다.** 모델마다 프롬프트를 손보면 모델이 아니라 프롬프트를
        비교하는 것이 된다 (§5-5 G-AI-5).
    """
    chosen = models or tuple(spec.id for spec in config.models)
    gate = asyncio.Semaphore(config.max_concurrency)

    async def call(model: str) -> LlmOutcome:
        """모델 하나를 부른다 (동시 실행 상한 적용).

        Args:
            model: 모델 id.

        Returns:
            성공 또는 실패 — 실패도 값이다 (`gather` 가 예외로 끊기지 않게).
        """
        async with gate:
            return await client.complete(
                model,
                system_prompt,
                user_prompt,
                temperature=config.temperature,
                timeout_seconds=config.timeout_seconds,
            )

    return list(await asyncio.gather(*(call(model) for model in chosen)))
