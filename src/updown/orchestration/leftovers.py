"""주인 없는 잔재 — **판이 사라진 뒤 거래소에 남는 주문들** (사용자 신고 2026-08-21).

🔴 신고: *"내가 xrp 삭제한건데, 왜 포지션이 남아있지???? 이거 삭제하면 포지션 정리되는게
아니었나?"* — 실측해 보니 **포지션은 정리됐다.** 남은 것은 조건부 손절 주문이었다.

```
포지션   BTC_USDT -1 · ETH_USDT -20          XRP 없음
조건부   BTC 75106.1 · ETH 2370.95 · XRP 1.2741   XRP 여기 있다
```

원인은 `LiveRunner.close_all()` 이 문서에 *"조건부 주문을 거둔다"* 라고 적고 반환값에
`stops_cancelled` 까지 두면서 **본문에 거두는 코드가 없었던** 것이다. 게다가 포지션이
없으면 그 앞에서 빠져나갔다 — XRP 는 이미 손절로 닫힌 뒤였다.

⚠️ **"닫을 포지션이 없다" 와 "치울 것이 없다" 는 다르다.** 이 파일이 그 둘을 가른다.

## 왜 남기면 안 되나

남은 조건부는 `size: 0` = *"포지션 전량 닫기"* 다. 24시간 안에 같은 종목으로 새 판을
띄우면 **그 트리거가 새 포지션을 통째로 닫는다.** 새 판에서는 이유 없이 사라진 것으로
보이고, 원장은 그것을 자기 손절로 세어 **승률 통계가 오염된다.**

## ⛔ 포지션이 있으면 아무것도 거두지 않는다

조건부는 그 포지션의 **유일한 보호막**이다. 잔재를 치우려다 무방비 포지션을 만드는 것은
잔재를 남기는 것보다 훨씬 나쁘다 (§1.2.1 · 절대 규칙 #3).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable

from updown.common.domain.instrument import Instrument
from updown.common.logging.setup import get_logger

_logger = get_logger("orchestration.leftovers")


@dataclass(frozen=True)
class Leftover:
    """거래소에 남은 주문 한 건 — **화면에 그대로 적을 수 있는 모양**이다.

    Note:
        ⭐ 사람에게 보일 때 *"조건부 1건"* 만으로는 지워도 되는지 판단할 수 없다.
        발동가와 수량이 있어야 *"아, 지난 XRP 판 손절이구나"* 가 된다.
    """

    kind: str
    """`조건부` 또는 `지정가`."""

    order_id: str
    """거래소 주문 id — 거두려면 이것이 열쇠다."""

    at: str
    """조건부면 발동가, 지정가면 지정가."""

    size: str
    """계약 수. 조건부의 `0` 은 **"포지션 전량"** 이라는 뜻이지 없다는 뜻이 아니다."""


def held_size(snapshot: Mapping[str, str] | None) -> int:
    """스냅샷에서 계약 수를 읽는다 — **못 읽으면 0 이 아니라 예외로 다룬다**.

    Args:
        snapshot: 거래소 포지션 스냅샷. 없으면 `None` 이나 빈 사전.

    Returns:
        부호 있는 계약 수. 포지션이 없으면 0.

    Note:
        ⚠️ 숏은 음수다. **`abs` 를 여기서 하지 않는다** — 방향을 잃으면 부르는 쪽이
        롱·숏을 되추측하게 된다.

    Raises:
        ValueError: 계약 수를 못 읽었다 — 0 으로 떨어뜨리면 살아 있는 포지션의 손절을 지운다.

    Note:
        🔴 값이 이상하면 **0 으로 떨어뜨리지 않는다.** 0 은 *"포지션이 없다"* 로 읽히고,
        그 뒤에 조건부를 거두는 경로가 붙어 있다 — 못 읽은 것을 없는 것으로 처리하면
        살아 있는 포지션의 손절을 지운다.
    """
    if not snapshot:
        return 0
    raw = str(snapshot.get("size", "0")).strip() or "0"
    try:
        return int(Decimal(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"포지션 계약 수를 못 읽었다: {raw!r}") from exc


def sweepable(
    stops: Sequence[Mapping[str, str]],
    orders: Sequence[Mapping[str, str]],
) -> list[Leftover]:
    """거둘 것들을 고른다 — **순수 함수**다.

    Args:
        stops: `open_stops` 가 준 조건부 목록.
        orders: `open_orders` 가 준 미결 지정가 목록.

    Returns:
        거둘 잔재들. 없으면 빈 목록.

    Note:
        🔴 **진입 지정가는 건드리지 않는다.** 사다리 진입이 걸려 있는 중에 이 함수가
        불릴 수 있고, 그것까지 거두면 *"잔재 청소"* 가 조용한 취소가 된다. 기준은
        `is_reduce_only` — **줄이는 주문만** 보호막의 일부다.

        ⚠️ 조건부는 전부 대상이다. Gate 조건부에는 우리 `text` 가 실리지 않아
        누구 것인지 주문만 보고는 못 가른다. **주인 판정은 종목 단위로 부르는 쪽이
        한다** — 그 종목에 살아 있는 판이 없을 때만 이 함수를 부른다.
    """
    found = [
        Leftover(
            kind="조건부",
            order_id=str(row.get("id", "")),
            at=str(row.get("trigger_price", "")),
            size=str(row.get("size", "")),
        )
        for row in stops
    ]
    found += [
        Leftover(
            kind="지정가",
            order_id=str(row.get("id", "")),
            at=str(row.get("price", "")),
            size=str(row.get("size", "")),
        )
        for row in orders
        if str(row.get("is_reduce_only", "")) == "True"
    ]
    return [item for item in found if item.order_id]


@runtime_checkable
class Sweeper(Protocol):
    """잔재를 보고 거둘 수 있는 어댑터.

    Note:
        🔴 좁게 잡는다 — 이 다섯을 다 말할 수 있는 어댑터에서만 청소가 성립한다.
        하나라도 없으면 *"봤는데 못 거뒀다"* 가 되고, 그것은 거두지 않은 것보다 나쁘다
        (했다고 믿게 된다).
    """

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        """포지션 스냅샷.

        Args:
            instrument: 종목.

        Returns:
            거래소가 말하는 포지션. 없으면 빈 사전.
        """
        ...

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        """걸려 있는 조건부 주문들.

        Args:
            instrument: 종목.

        Returns:
            조건부 행들.
        """
        ...

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        """걸려 있는 미결 지정가 주문들.

        Args:
            instrument: 종목.

        Returns:
            미결 주문 행들.
        """
        ...

    async def cancel_stop(self, stop_id: str) -> None:
        """조건부 하나를 거둔다.

        Args:
            stop_id: 조건부 주문 id.
        """
        ...

    async def cancel_order(self, broker_order_id: str) -> object:
        """지정가 하나를 거둔다.

        Args:
            broker_order_id: 거래소 주문 id.

        Returns:
            거래소 응답 — 여기서는 읽지 않는다.
        """
        ...


async def look(orders: object, instrument: Instrument) -> list[Leftover]:
    """그 종목에 **거둘 것이 있나** — 거두지는 않는다.

    Args:
        orders: 어댑터. `Sweeper` 가 아니면 빈 목록이다.
        instrument: 볼 종목.

    Returns:
        잔재 목록. 포지션이 살아 있으면 **빈 목록**이다.

    Raises:
        ValueError: 포지션 계약 수를 못 읽었을 때 (`held_size`).

    Note:
        ⛔ **포지션이 있으면 빈 목록을 돌려준다** — 잔재가 아니라 보호막이기 때문이다.
        화면에 띄우는 경로도 이 함수를 쓰므로, 여기서 걸러야 사람이 *"지워도 되는 것"*
        으로 잘못 읽지 않는다.
    """
    if not isinstance(orders, Sweeper):
        return []
    if held_size(await orders.position_snapshot(instrument)) != 0:
        return []
    return sweepable(
        await orders.open_stops(instrument),
        await orders.open_orders(instrument),
    )


async def sweep_zombie_entries(
    orders: object, instrument: Instrument, *, why: str, keep: Iterable[object] = ()
) -> list[Leftover]:
    """판이 새로 뜰 때 **주인 잃은 진입 지정가**를 거둔다 (2026-08-25 ADA 고아 사건).

    Args:
        orders: 어댑터. `Sweeper` 가 아니면 아무것도 안 한다.
        instrument: 치울 종목.
        why: 왜 치우는가 — 로그에 남는다.
        keep: 거두지 않을 거래소 주문 id 들 — 저장된 대기 계획을 되살려 주인이 생긴 표 (T218).

    Returns:
        거둔 것들.

    Note:
        🔴 대기 계획(waiting)은 T218 전까지 재시작을 살아남지 못했다 — 그래서 판이 새로 뜰 때
        남아 있는 **비-reduce_only 지정가는 전부 좀비**다. 두면 아무도 모르는 사이에
        채워져 고아 포지션이 된다 (실측 1회: ADA 119계약 · 감사가 잡고 사람이 치웠다).

        ⚠️ `sweep` 과 달리 **포지션이 있어도 거둔다** — 반쯤 채워진 사다리의 남은
        다리도 부활 후에는 관리 주체가 없다 (부활은 보호만 건다 `_protect`).

        ⚠️ 취소가 실패하면(그 사이 체결) 던지지 않고 에러만 남긴다 — 그 갈림은
        ledger_mismatch 감사가 잡는다. 경합 창이 "무한"에서 "수 초"로 줄어드는 것이
        이 함수의 목적이다.
    """
    if not isinstance(orders, Sweeper):
        return []
    out: list[Leftover] = []
    keep_ids = {str(item) for item in keep}
    for row in await orders.open_orders(instrument):
        if str(row.get("is_reduce_only", "")) == "True":
            continue
        # ⭐ T218 — 저장돼 있던 대기 계획을 되살려 **주인이 생긴** 표는 좀비가 아니다.
        if str(row.get("id", "")) in keep_ids:
            continue
        item = Leftover(
            kind="좀비 진입",
            order_id=str(row.get("id", "")),
            at=str(row.get("price", "")),
            size=str(row.get("size", "")),
        )
        if not item.order_id:
            continue
        try:
            await orders.cancel_order(item.order_id)
            out.append(item)
            _logger.info(
                "leftover_zombie_entry_swept",
                payload={"symbol": instrument.symbol, "id": item.order_id, "why": why},
            )
        except Exception as exc:
            _logger.error(
                "leftover_zombie_entry_stuck",
                payload={
                    "symbol": instrument.symbol,
                    "id": item.order_id,
                    "error": str(exc)[:140],
                    "note": "취소 실패 — 이미 체결됐을 수 있다. 고아 포지션 여부를 감사가 본다",
                },
            )
    return out


async def sweep(orders: object, instrument: Instrument, *, why: str) -> list[Leftover]:
    """주인 없는 잔재를 **거둔다**.

    Args:
        orders: 어댑터.
        instrument: 치울 종목.
        why: 왜 치우는가 — 로그에 남는다 (`RUN 삭제` · `판 시작`).

    Returns:
        실제로 거둔 것들.

    Note:
        ⭐ **거둔 것을 남긴다** (§1-0s). *"몇 건"* 만으로는 다음에 *"무엇이 남았었나"* 에
        답할 수 없다 — 발동가까지 남겨야 그 잔재가 어느 판 것이었는지 되짚을 수 있다.

        ⛔ **한 건이 실패해도 나머지를 거둔다.** 첫 건에서 멈추면 나머지가 조용히 남고,
        화면은 *"치웠다"* 로 보인다.

        ⚠️ 실패를 **경고로** 남긴다 — 못 거둔 잔재는 다음 판을 죽일 수 있지만, 그 자체가
        포지션을 위태롭게 하지는 않는다. 에러로 올리면 진짜 무방비 경보와 섞인다.
    """
    found = await look(orders, instrument)
    if not found or not isinstance(orders, Sweeper):
        return []
    swept: list[Leftover] = []
    for item in found:
        with contextlib.suppress(Exception):
            if item.kind == "조건부":
                await orders.cancel_stop(item.order_id)
            else:
                await orders.cancel_order(item.order_id)
            swept.append(item)
    payload = {
        "symbol": instrument.symbol,
        "why": why,
        "found": len(found),
        "swept": len(swept),
        "detail": [f"{item.kind} {item.at} x{item.size}" for item in found],
        "note": "주인 없는 조건부·줄이는 주문이다 — 남기면 다음 판을 닫는다",
    }
    if len(swept) == len(found):
        _logger.info("live_leftovers_swept", payload=payload)
    else:
        # ⚠️ 하나라도 못 거뒀으면 경고다 — 화면이 "치웠다" 로 보이면 안 된다.
        _logger.warning("live_leftovers_swept", payload=payload)
    return swept
