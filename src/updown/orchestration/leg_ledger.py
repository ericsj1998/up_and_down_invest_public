"""엔진 다리 매매를 판 원장에 같은 꼴로 적는다 (T830).

판 없이 도는 엔진 다리(나스닥)는 매매를 자기 장부 파일에만 적었다. 리포트 · 라이브 점검처럼
판 원장(`wf_trades`)을 읽는 기능은 그래서 엔진을 못 보고, 기능마다 엔진판을 따로 붙여야 했다
(T822 · T829). 엔진이 **몫 하나 = 매매 한 줄**로 같은 원장에 적으면 그 기능들이 엔진을 저절로
본다.

실행은 그대로 따로다 — 엔진은 `EngineTradeSink.put` 으로 줄을 넘기기만 하고, 이 모듈이 원장 줄로
옮겨 배경에서 쓴다. 🔴 엔진 장부 파일이 진짜 장부이고 원장은 사본이라, 쓰기가 늦거나 실패해도 엔진의
매매는 막히지 않는다(절대 규칙 8-1). 실패한 줄은 폴백 파일에 남고 오류 로그가 난다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from uuid import UUID

from updown.common.logging.setup import get_logger
from updown.orchestration.leg_engines import (
    TRADE_OPEN,
    TRADE_SIGNAL_EXIT,
    TRADE_STOP_LOSS,
    TRADE_TAKE_PROFIT,
    EngineTrade,
)
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Outcome,
    TradeRecord,
    member_pnl,
)

_logger = get_logger("orchestration.leg_ledger")

_OUTCOME = {
    TRADE_OPEN: Outcome.OPEN,
    TRADE_TAKE_PROFIT: Outcome.TAKE_PROFIT,
    TRADE_STOP_LOSS: Outcome.STOP_LOSS,
    TRADE_SIGNAL_EXIT: Outcome.SIGNAL_EXIT,
}


def engine_record(trade: EngineTrade, *, playbook: str, leverage: Decimal) -> TradeRecord:
    """엔진 매매 한 줄 → 원장 기록.

    Args:
        trade: 엔진이 넘긴 줄.
        playbook: 다리 귀속 키(`매매법 id@버전`).
        leverage: 다리 선언 배율 — 원장의 의도 배율 · `margin_used = 명목 ÷ 배율`.

    Returns:
        원장 기록. 닫힌 줄이면 `member_pnl(기록) == trade.pnl` 이 되도록 `contract_adjust` 를 채운다
        — 원장 칸(진입 · 청산 · 비용)으로 다시 센 손익과 엔진이 센 손익의 차를 그 칸에 둔다(T606 과
        같은 자리 · 장부 몫 = 엔진 몫).

    Raises:
        ValueError: 배율이 양수가 아니다.
    """
    if leverage <= 0:
        raise ValueError(f"엔진 다리 배율 {leverage} — 양수여야 한다")
    notional = Decimal(trade.contracts) * trade.multiplier * trade.entry
    target = Decimal(0) if trade.target is None else trade.target
    note = f"엔진 · {trade.kind}" + (f" · {trade.note}" if trade.note else "")
    record = TradeRecord(
        trade_id=trade.trade_id,
        playbook=playbook,
        actor=Actor.ADOPTED if trade.adopted else Actor.SYSTEM,
        placed_at=trade.opened_at,
        entry=trade.entry,
        opened_at=trade.opened_at,
        planned_stop=Decimal(0) if trade.stop is None else trade.stop,
        planned_target=target,
        planned_first=target,
        direction=Direction.SHORT if trade.short else Direction.LONG,
        outcome=_OUTCOME[trade.outcome],
        closed_at=trade.closed_at,
        exit_price=trade.exit_price,
        cost_pct=trade.fees / notional,
        leverage=leverage,
        contracts=trade.contracts,
        fee_actual=trade.fees,
        margin_used=notional / leverage,
        note=note,
    )
    if trade.pnl is None:
        return record
    return replace(record, contract_adjust=trade.pnl - member_pnl(record))


class RecordSaver(Protocol):
    """원장 저장소에서 이 모듈이 쓰는 면(`RunStore.save`)."""

    async def save(self, run_id: UUID, records: Sequence[TradeRecord]) -> None:
        """기록을 덮어쓴다(같은 `trade_id` 는 갱신)."""
        ...


class EngineLedger:
    """`EngineTradeSink` 구현 — 줄을 받아 배경에서 원장에 쓴다.

    Note:
        `put` 은 큐에 넣기만 한다(막히지 않는다 · 예외를 안 낸다). 같은 줄이 다시 오면 건너뛴다 —
        엔진이 저장할 때마다 몫 전부를 다시 넘겨도 DB 쓰기는 바뀐 줄뿐이다.
    """

    def __init__(
        self,
        saver: RecordSaver,
        run_id: UUID,
        *,
        playbook: str,
        leverage: Decimal,
        fallback: Path,
    ) -> None:
        """기록기를 만든다 — `start()` 를 불러야 쓰기 시작한다.

        Args:
            saver: 원장 저장소.
            run_id: 엔진 기록 판 id(`RunStore.open_engine_run`).
            playbook: 다리 귀속 키.
            leverage: 다리 선언 배율.
            fallback: 쓰기 실패한 줄을 덧붙일 파일(JSON 한 줄씩).
        """
        self.saver = saver
        self.run_id = run_id
        self.playbook = playbook
        self.leverage = leverage
        self.fallback = fallback
        self.failed = 0
        self._queue: asyncio.Queue[EngineTrade] = asyncio.Queue()
        self._last: dict[str, EngineTrade] = {}
        self._task: asyncio.Task[None] | None = None

    def put(self, trade: EngineTrade) -> None:
        """한 줄을 넘긴다 — 막히지 않는다 · 예외를 안 낸다."""
        try:
            if self._last.get(trade.trade_id) == trade:
                return
            self._last[trade.trade_id] = trade
            self._queue.put_nowait(trade)
        except Exception as exc:  # 규칙 8-1 — 기록 실패가 엔진을 멈추지 않는다
            self._spill(trade, f"put: {exc!r}")

    def start(self) -> None:
        """배경 쓰기를 시작한다(이미 돌면 그대로)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain(), name=f"leg_ledger:{self.run_id}")

    def stop(self) -> None:
        """배경 쓰기를 멈춘다(큐에 남은 줄은 폴백 파일로)."""
        if self._task is not None:
            self._task.cancel()
        while not self._queue.empty():
            self._spill(self._queue.get_nowait(), "stopped")

    async def flush(self) -> None:
        """큐가 빌 때까지 기다린다(시험 · 끝내기 전)."""
        await self._queue.join()

    async def _drain(self) -> None:
        while True:
            trade = await self._queue.get()
            try:
                record = engine_record(trade, playbook=self.playbook, leverage=self.leverage)
                await self.saver.save(self.run_id, [record])
            except Exception as exc:
                self._spill(trade, repr(exc))
            finally:
                self._queue.task_done()

    def _spill(self, trade: EngineTrade, error: str) -> None:
        """실패한 줄을 폴백 파일에 남기고 오류 로그를 낸다 — 여기서도 예외를 안 낸다."""
        self.failed += 1
        self._last.pop(trade.trade_id, None)  # 다음 put 에서 다시 쓰게
        _logger.error(
            "leg_ledger_write_failed",
            payload={"trade": trade.trade_id, "run": str(self.run_id), "error": error[:200]},
        )
        try:
            self.fallback.parent.mkdir(parents=True, exist_ok=True)
            row = {k: (str(v) if v is not None else None) for k, v in asdict(trade).items()}
            with self.fallback.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"error": error[:200], **row}, ensure_ascii=False) + "\n")
        except Exception as exc:
            _logger.error("leg_ledger_fallback_failed", payload={"error": repr(exc)[:200]})
