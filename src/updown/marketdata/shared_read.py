"""**계정 단위 조회를 판들이 나눠 쓴다** (2026-08-29 사고).

## 왜 생겼나

실측(5분 · BINANCE): `account` **266회** · `positionRisk` **357회**. 그런데 판 6개는
**같은 계좌**를 본다 — 여섯이 각자 물어보고 여섯 번 같은 답을 받았다.

그 낭비가 요율 한도를 갉아먹었고, 한도를 넘자 Binance 가 IP 밴을 걸었다. 하필 펀드
복구 중이라 판 6개가 예산 없이 돌았고, 밴은 두드릴수록 2분에서 74분으로 늘어났다.

## ⛔ 종목별 조회는 여기 넣지 않는다

`position_snapshot(symbol)` 은 판마다 답이 **다르다** — 나눠 쓸 것이 없다. 여기 담는
것은 *"계정 전체"* 를 묻는 것뿐이다 (잔고 · 전체 증거금).

## ⚠️ 시한이 짧아야 하는 이유

이 값들은 **손절 감시가 읽는다.** 오래 들고 있으면 이미 청산된 계좌를 멀쩡하다고
읽을 수 있고, 그 오차는 조용하다 (규칙 #8). 그래서 시한은 *"한 걸음"* 정도로만 둔다 —
목적은 **같은 순간에 여섯이 겹쳐 묻는 것**을 하나로 합치는 것이지, 오래 재사용하는
것이 아니다.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Any

TTL_S = 2.0
"""계정 조회를 나눠 쓰는 시간(초).

🔴 **짧아야 한다.** 러너의 손절 감시는 1초마다 돌고, 그 판단이 낡은 잔고를 보면
안 된다. 2초는 *"같은 걸음에 겹친 여섯 번을 한 번으로"* 를 잡되 다음 걸음에는 새로
받는 값이다.

⚠️ 늘리고 싶으면 **무엇이 이 값을 읽는지** 먼저 본다. 지금은 잔고·전체 증거금이고,
둘 다 예산 산정과 감사가 쓴다.
"""

_BOX: dict[str, tuple[float, Any]] = {}
_LOCK = Lock()


async def shared(
    key: str,
    fetch: Callable[[], Awaitable[Any]],
    *,
    ttl: float = TTL_S,
    now: float | None = None,
) -> Any:
    """계정 단위 조회 하나 — 방금 받은 것이 있으면 그것을 나눠 준다.

    Args:
        key: 무엇을 묻는가. **거래소와 계정을 가르는 이름**이어야 한다 —
            `"BINANCE:account"` 처럼. 안 가르면 Gate 답을 Binance 판이 받는다.
        fetch: 실제로 받아 오는 함수.
        ttl: 나눠 쓰는 시간(초).
        now: 시험용 현재 시각.

    Returns:
        조회 결과.

    Note:
        🔴 **실패는 기억하지 않는다.** `fetch` 가 던지면 그대로 올려보내고 아무것도
        남기지 않는다 — 실패를 캐시하면 한 번의 요율 제한이 `ttl` 만큼 이어지고,
        그 사이 손절 감시가 계좌를 못 읽는다.

        ⚠️ **락 안에서 `await` 하지 않는다.** 같은 값을 두 걸음이 동시에 물으면 요청이
        두 번 나갈 수 있는데, 그 낭비는 락을 들고 네트워크를 기다리다 다른 판까지
        멈추는 것보다 싸다.
    """
    clock = time.time() if now is None else now
    with _LOCK:
        kept = _BOX.get(key)
        if kept is not None and clock - kept[0] < ttl:
            return kept[1]
    got = await fetch()
    with _LOCK:
        _BOX[key] = (clock, got)
    return got


def forget(prefix: str | None = None) -> None:
    """기억을 지운다 — 시험과, 방금 주문을 낸 쪽이 쓴다.

    Args:
        prefix: 이 앞자리로 시작하는 것만 지운다. `None` 이면 전부.

    Note:
        ⭐ **주문을 낸 뒤에는 지운다.** 잔고가 방금 바뀌었는데 2초짜리 옛 값을 보면
        다음 주문이 없는 돈으로 산정된다.
    """
    with _LOCK:
        if prefix is None:
            _BOX.clear()
            return
        for key in [k for k in _BOX if k.startswith(prefix)]:
            del _BOX[key]


def size() -> int:
    """기억하고 있는 항목 수 — 진단용.

    Returns:
        만료 여부와 무관한 항목 수.
    """
    with _LOCK:
        return len(_BOX)
