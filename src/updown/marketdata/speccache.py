"""계약 명세 **기억통** — 거의 안 바뀌는 값을 매번 묻지 않는다 (2026-08-29 사고).

## 왜 생겼나

실측(5분): `exchangeInfo` **130회**. 계약 명세(호가 단위·최소 수량·승수)는 거래소가
상장 규칙을 바꿀 때나 변하는 값인데, 걸음마다 다시 물어보고 있었다.

그 낭비가 요율 한도를 갉아먹었고, 한도를 넘자 Binance 가 IP 밴을 걸었다 —
그리고 하필 펀드 복구 중이라 판 6개가 예산 없이 돌았다.

## ⛔ 영원히 들고 있지 않는다

명세는 **바뀔 수 있다.** 상장 규칙이 바뀌었는데 옛 호가 단위로 주문을 내면 전부
거절되고, 그 상태는 조용하다 (규칙 #8). 그래서 시한을 둔다 — 오래 들고 있어 얻는
것보다 틀린 값을 오래 쓰는 위험이 크다.

## ⚠️ 실패를 기억하지 않는다

못 받아 온 것은 **빈칸으로 두고 다음에 다시 묻는다.** 실패를 캐시하면 한 번의 요율
제한이 시한만큼 이어진다 — 고치려던 것이 원인이 된다.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Any

TTL_S = 3600.0
"""명세를 들고 있는 시간(초).

⚠️ 길게 잡을수록 호출은 줄지만 **틀린 명세를 쓰는 창**도 길어진다. 한 시간이면
5분에 130회 부르던 것이 종목당 한 번이 되고, 규칙 변경은 다음 시간에 따라온다.
"""

_BOX: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCK = Lock()


async def spec(
    venue: str,
    symbol: str,
    fetch: Callable[[], Awaitable[dict[str, Any]]],
    *,
    ttl: float = TTL_S,
    now: float | None = None,
) -> dict[str, Any]:
    """계약 명세를 준다 — 기억에 있으면 그것, 없으면 받아서 기억한다.

    Args:
        venue: 거래소 이름 (`BINANCE` · `GATE`). 같은 종목 이름이 거래소마다 다른
            명세를 가지므로 **반드시 갈라서** 기억한다.
        symbol: 종목/계약 이름.
        fetch: 실제로 받아 오는 함수.
        ttl: 기억하는 시간(초).
        now: 시험용 현재 시각.

    Returns:
        명세 딕셔너리.

    Note:
        🔴 **실패는 기억하지 않는다.** `fetch` 가 던지면 그대로 올려보내고 아무것도
        남기지 않는다 — 실패를 캐시하면 요율 제한 한 번이 `ttl` 만큼 이어진다.

        ⚠️ **락 안에서 `await` 하지 않는다.** 같은 종목을 두 걸음이 동시에 물으면
        요청이 두 번 나갈 수 있는데, 그 낭비는 락을 들고 네트워크를 기다리다 다른
        종목까지 막는 것보다 싸다.
    """
    key = f"{venue}:{symbol}"
    clock = time.time() if now is None else now
    with _LOCK:
        kept = _BOX.get(key)
        if kept is not None and clock - kept[0] < ttl:
            return kept[1]
    got = await fetch()
    with _LOCK:
        _BOX[key] = (clock, got)
    return got


def forget(venue: str | None = None) -> None:
    """기억을 지운다 — 시험과, 명세가 바뀐 것을 아는 사람이 쓴다.

    Args:
        venue: 이 거래소 것만 지운다. `None` 이면 전부.
    """
    with _LOCK:
        if venue is None:
            _BOX.clear()
            return
        for key in [k for k in _BOX if k.startswith(f"{venue}:")]:
            del _BOX[key]


def size() -> int:
    """기억하고 있는 명세 수 — 진단용.

    Returns:
        만료 여부와 무관한 항목 수.
    """
    with _LOCK:
        return len(_BOX)
