"""요청 간격 스로틀 — 거래소 클라이언트 셋이 같은 것을 세 벌 들고 있던 것을 모았다 (2026-09-06).

토큰 버킷 대신 **최소 간격 방식**이다. 버스트를 허용하지 않는 대신 구현이 단순하고, 백필처럼
연속 호출하는 경로에서 429 가 아예 나지 않는다 — 429 를 맞고 재시도하는 것보다 처음부터
간격을 지키는 편이 총 소요가 짧다.

무엇을 한 단위로 묶느냐(업비트·토스는 엔드포인트 **그룹**, Gate 는 **엔드포인트**)는 부르는 쪽이
키를 어떻게 잡느냐의 문제이고, 간격을 지키는 물건 자체는 같다.

T264(2026-09-10)에 `marketdata/throttle.py` 에서 여기로 내려왔다 — 아웃바운드 층(`Outbound`)이
쓰므로 `common` 에 있어야 한다. 옛 경로는 재수출로 남겨 두었다.
"""

from __future__ import annotations

import asyncio

RATE_SAFETY_FACTOR = 0.8
"""안전 마진 — 공개 한도의 이 비율만 쓴다.

여러 프로세스(api·engine)가 같은 IP 를 공유하므로 한도를 꽉 채우면 서로를 429 로 밀어낸다.
프로세스 간 조율은 Redis 락으로 가능하지만(§12.5), 조회 경로에 락을 걸 만한 가치가 없어
마진으로 흡수한다.
"""


class Throttle:
    """키 하나(엔드포인트 또는 그룹)의 초당 요청 간격을 지킨다."""

    def __init__(self, rate_per_second: int, *, safety_factor: float = RATE_SAFETY_FACTOR) -> None:
        """스로틀을 만든다.

        Args:
            rate_per_second: 초당 허용 요청 수. 0 이하는 1 로 올린다.
            safety_factor: 한도의 몇 배까지 쓸지. 공개 조회는 기본 0.8, 자기 IP 한도를 직접
                재는 Gate 는 1.0 을 넘긴다.
        """
        effective = max(1.0, rate_per_second * safety_factor)
        self._min_interval = 1.0 / effective
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        """다음 요청이 허용되는 시점까지 기다린다."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_allowed = now + self._min_interval


__all__ = ["RATE_SAFETY_FACTOR", "Throttle"]
