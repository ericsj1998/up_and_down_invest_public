"""판의 영속화 — `run > trade > order` 를 여닫고 되읽는다 (T16 ②).

## 이 모듈이 막는 실패 하나

```
거래소     포지션·손절·익절이 그대로 남는다
원장       통째로 사라진다 — 매매 목록 · 손익 · 시드 · 증거금
```

그 순간부터 아무도 그 포지션을 관리하지 않는다. 반익도, 본절 상향도, 손절 재장착도
멈춘다 — 사용자 지적: *"이런식으로 연결이 끊겨버리면 1차 익절이나 그런 대응 자체가
불가능하잖아."*

## 이어붙이는 것과 이어붙이면 안 되는 것

```
✅ 이어붙인다     매매 목록 · 실현 손익 · 시드 · 증거금 예산 · 레버리지
⛔ 안 이어붙인다   판정 횟수(steps) — 이 프로세스가 실제로 본 봉의 수다
⛔ 안 이어붙인다   시드 봉 구간 — 판이 뜬 시각이 다르면 다른 창이다
```

⚠️ **`steps` 를 이어붙이면 "로직이 도는가" 표시가 거짓말한다.** 그 값이 0 인 것은
사실이고, 화면이 이미 "뜬 지 N분" 으로 설명한다.

## 조용히 실패하지 않는다

라이브 판은 **저장 없이 뜨지 않는다.** 저장이 안 되는데 판이 돌면 지금 고치려는 그
버그가 그대로 재현되고, 화면에는 아무 표시가 없다 (절대 규칙 #8). 백테스트는 결정론
복원이 있으므로(같은 봉인·같은 설정 → 같은 원장) 이 저장소를 요구하지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from updown.common.db.models.ops import AppSetting
from updown.common.db.models.walkforward import (
    WalkforwardCalibration,
    WalkforwardOrder,
    WalkforwardRun,
    WalkforwardTrade,
)
from updown.common.logging.setup import get_logger
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    HalfBy,
    Outcome,
    TradeRecord,
    evidence_from_rows,
    evidence_rows,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from updown.common.db.base import JsonDict

_logger = get_logger("orchestration.walkforward.store")


class RunStoreError(RuntimeError):
    """판 영속화 계층의 오류.

    Note:
        ⛔ **조용히 넘기지 않는다.** 라이브 판이 저장 없이 도는 것이 이 태스크가
        고치려는 바로 그 상태다.
    """


def anchor_of(market: str, symbol: str, playbook: str, *, live: bool) -> str:
    """**같은 판인지 가르는 닻** — A 안 (사용자 확정 2026-08-18).

    Args:
        market: 시장 코드 (`GATE`).
        symbol: 종목 (`BTC_USDT`).
        playbook: 성과 귀속 키 (`sample_ma_cross@0.1.0`).
        live: 실계좌 경로인가.

    Returns:
        `GATE:BTC_USDT:sample_ma_cross@0.1.0:live` 꼴의 문자열.

    Note:
        🔴 **버전이 닻에 들어간다.** `@0.1.0` 과 `@0.4.0` 은 다른 매매법이고, 한
        판으로 묶으면 그 성적이 무엇의 성적인지 아무도 모르게 된다 (§1-0s).

        🔴 **라이브와 백테스트가 섞이지 않는다.** 같은 종목·매매법이어도 다른 판이다 —
        섞으면 평균이 오염돼 판단 근거가 무의미해진다.
    """
    return f"{market}:{symbol}:{playbook}:{'live' if live else 'paper'}"


@dataclass(frozen=True, slots=True)
class OpenedRun:
    """열었거나 **이어받은** 판.

    Attributes:
        run_id: DB 행 id.
        key: 화면·저널이 쓰는 짧은 id. 이어받으면 **옛 판의 값**이다.
        opened_at: 처음 열린 시각. 이어받아도 안 바뀐다.
        resumed: 이어받았으면 True — 화면이 새 판과 갈라 말해야 한다.
        records: 되읽은 매매들. 새 판이면 비어 있다.
        meta: 저널 머리. 되짚기용이며 판단에 쓰지 않는다.
    """

    run_id: uuid.UUID
    key: str
    opened_at: datetime
    resumed: bool
    records: tuple[TradeRecord, ...] = ()
    meta: JsonDict = field(default_factory=dict[str, Any])


REFILL_CAP_KEY = "vault.refill_cap"
"""금고에서 꺼내 쓸 수 있는 누적 총액 (T21 ⑦) — **모든 판 공용**."""


class SettingsStore:
    """앱 전체가 공유하는 설정 (T21 ⑦).

    Note:
        🔴 **금고는 하나다.** 재충전 상한을 판마다 다르게 두면 *"이 금고가 얼마나
        탔나"* 를 사람이 못 따라간다 — 판 셋이 각자 200만씩 태우면 600만이 나간다.

        🔴 **메모리에 두지 않는다.** 리스크 한도가 리로드 한 번에 사라지고, 사라진 줄
        모른 채 계속 도는 것이 이 프로젝트가 2026-08-19 하루 종일 걷어낸 모양이다.

        ⚠️ 값은 문자열이다 — 뜻은 읽는 쪽이 정한다. 칸을 타입마다 만들면 설정 하나 늘
        때마다 마이그레이션이 필요하다.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """세션 팩토리를 받는다.

        Args:
            factory: DB 세션 팩토리.
        """
        self._factory = factory

    async def get(self, key: str) -> str | None:
        """설정 하나를 읽는다.

        Args:
            key: 설정 이름.

        Returns:
            값. 없거나 못 읽으면 None.

        Note:
            ⚠️ **못 읽으면 None 이다** — 예외를 밖으로 내지 않는다. 설정 조회 실패가
            판을 못 띄우게 하면 안전 장치가 오히려 길을 막는다.

            ⛔ 다만 **None 은 "제한 없음"** 으로 읽힌다. 그래서 화면이 지금 값을
            보여 줘야 한다 — 안 보이면 사람은 걸어 뒀다고 믿는다.
        """
        try:
            async with self._factory() as session:
                found = (
                    await session.execute(sa.select(AppSetting.value).where(AppSetting.key == key))
                ).scalar_one_or_none()
        except Exception as exc:
            _logger.error("app_setting_unreadable", payload={"key": key, "error": str(exc)[:160]})
            return None
        return None if found is None else str(found)

    async def put(self, key: str, value: str) -> None:
        """설정 하나를 적는다.

        Args:
            key: 설정 이름.
            value: 값. 빈 문자열이면 지운다 (= 제한 없음).

        Raises:
            RunStoreError: DB 에 닿을 수 없는 경우.

        Note:
            🔴 **여기서는 던진다.** 사람이 한도를 걸었는데 조용히 실패하면 걸었다고
            믿는 상태로 돈다 — 조회 실패와 위험도가 다르다 (절대 규칙 #8).
        """
        try:
            async with self._factory() as session:
                if not value:
                    await session.execute(sa.delete(AppSetting).where(AppSetting.key == key))
                else:
                    statement = insert(AppSetting).values(key=key, value=value)
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=["key"], set_={"value": statement.excluded.value}
                        )
                    )
                await session.commit()
        except Exception as exc:
            raise RunStoreError(f"설정을 저장할 수 없다: {exc}") from exc


class RunStore:
    """판을 여닫고 원장을 저장·복원한다.

    Note:
        ⚠️ **세션 팩토리를 받는다** — 엔진을 여기서 만들면 API 가 이미 든 커넥션 풀과
        별도 풀이 생기고, 종료할 때 아무도 안 닫는다.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """저장소를 만든다.

        Args:
            factory: `ApiState` 가 든 세션 팩토리.
        """
        self._factory = factory

    async def open(
        self,
        *,
        key: str,
        market: str,
        symbol: str,
        playbook: str,
        playbook_id: str,
        live: bool,
        seed_cash: Decimal,
        margin_budget: Decimal | None,
        leverage: Decimal,
        skim_pct: Decimal,
        meta: JsonDict,
        profit_line: Decimal | None = None,
        budget_cap: Decimal | None = None,
    ) -> OpenedRun:
        """같은 닻의 **열린** 판이 있으면 이어받고, 없으면 연다.

        Args:
            key: 새로 열 때 쓸 짧은 id. 이어받으면 **무시된다**.
            market: 시장 코드.
            symbol: 종목.
            playbook: 성과 귀속 키.
            playbook_id: 매매법 id.
            live: 실계좌 경로인가.
            seed_cash: 시드 (지갑).
            margin_budget: 굴리는 돈. None 이면 전액.
            leverage: 배율.
            skim_pct: 수익 유보 비율.
            meta: 저널 머리.
            profit_line: **이 선을 넘은 부분에서만** 이익을 실현한다 (T21 ⑤).
                None 이면 모든 이익에서 뗀다 (옛 동작).
            budget_cap: 가용자금의 천장 (T21 ⑥). None 이면 상한이 없다 —
                그러면 청산 한 번의 손실에도 상한이 없다.

        Returns:
            열었거나 이어받은 판.

        Raises:
            RunStoreError: DB 에 닿을 수 없는 경우.

        Note:
            🔴 **이어받으면 옛 key 를 그대로 쓴다.** 다르면 목록에 같은 판이 두 번 뜨고,
            사람은 어느 쪽이 진짜인지 모른다.

            🔴 **시드·증거금·레버리지는 이어받은 값을 이긴다** — 옛 판이 굴리던 조건이
            사실이고, 새 값으로 덮으면 그 판의 과거 손익이 다른 조건에서 난 것이 된다.
            다른 조건으로 돌고 싶으면 **RUN 을 닫고 새로 연다.**

            ⛔ `steps` 와 봉인 구간은 여기 없다 — 이어붙이지 않는다 (모듈 docstring).
        """
        anchor = anchor_of(market, symbol, playbook, live=live)
        try:
            async with self._factory() as session:
                found = (
                    await session.execute(
                        sa.select(WalkforwardRun).where(
                            WalkforwardRun.anchor == anchor,
                            WalkforwardRun.closed_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if found is None:
                    # ⭐ **키로도 찾는다** (2026-08-27 실사고): 플레이북 버전 승격
                    #    (0.4.0→0.4.1)은 닻(귀속 포함)을 바꾸지만 **같은 판**이다.
                    #    닻으로 못 찾았는데 같은 키의 열린 행이 있으면 이어받고,
                    #    닻·귀속을 새 버전으로 이관한다 (전략 전환 T61 과 같은 철학).
                    #    안 하면 같은 키 INSERT 가 UniqueViolation 으로 죽어
                    #    재시작마다 판 전부가 고아가 된다.
                    found = (
                        await session.execute(
                            sa.select(WalkforwardRun).where(
                                WalkforwardRun.key == key,
                                WalkforwardRun.closed_at.is_(None),
                            )
                        )
                    ).scalar_one_or_none()
                    if found is not None:
                        _logger.info(
                            "wf_run_anchor_migrated",
                            payload={
                                "key": key,
                                "from": found.anchor,
                                "to": anchor,
                                "note": "버전 승격 이어받기 — 성적 연속성 유지",
                            },
                        )
                        found.anchor = anchor
                        found.playbook = playbook
                        found.playbook_id = playbook_id
                        await session.commit()
                if found is not None:
                    records = await self._records(session, found.id)
                    _logger.info(
                        "wf_run_resumed",
                        payload={
                            "key": found.key,
                            "anchor": anchor,
                            "trades": len(records),
                            "note": "옛 판을 이어받았다 — steps 와 시드 구간은 안 이어붙인다",
                        },
                    )
                    return OpenedRun(
                        run_id=found.id,
                        key=found.key,
                        opened_at=found.opened_at,
                        resumed=True,
                        records=records,
                        meta=dict(found.meta_json or {}),
                    )
                now = datetime.now(UTC)
                row = WalkforwardRun(
                    key=key,
                    anchor=anchor,
                    live=live,
                    market=market,
                    symbol=symbol,
                    playbook=playbook,
                    playbook_id=playbook_id,
                    seed_cash=seed_cash,
                    margin_budget=margin_budget,
                    leverage=leverage,
                    skim_pct=skim_pct,
                    profit_line=profit_line,
                    budget_cap=budget_cap,
                    opened_at=now,
                    closed_at=None,
                    closed_reason=None,
                    meta_json=dict(meta),
                )
                session.add(row)
                await session.commit()
                _logger.info("wf_run_opened", payload={"key": key, "anchor": anchor})
                return OpenedRun(run_id=row.id, key=key, opened_at=now, resumed=False)
        except RunStoreError:
            raise
        except Exception as exc:
            raise RunStoreError(f"판을 열 수 없다 ({anchor}): {exc}") from exc

    async def plan_of(self, key: str) -> dict[str, str] | None:
        """그 판이 **지금 들고 있는** 매매의 계획만 읽는다 (T20 ④).

        Args:
            key: 판 id.

        Returns:
            `{trade_id, direction, stop}`. 보유 중이 아니면 None.

        Raises:
            RunStoreError: DB 에 닿을 수 없는 경우.

        Note:
            🔴 **감시자가 죽은 판의 손절을 되걸 때 쓴다.** 러너가 없으면 원장이
            메모리에 없고, 그래도 **손절 가격은 알아야 한다** — 모르면 지어내게 되고
            그것은 손절의 SSoT 를 깨는 일이다 (절대 규칙 #4).

            ⚠️ `open()` 을 쓰지 않는다. 그 함수는 판을 **열거나 이어받는** 일이라
            인자가 많고 부작용이 있다. 보기만 하는 길이 따로 있어야 한다.
        """
        try:
            async with self._factory() as session:
                found = (
                    await session.execute(
                        sa.select(
                            WalkforwardTrade.trade_id,
                            WalkforwardTrade.direction,
                            WalkforwardTrade.planned_stop,
                            WalkforwardTrade.entry,
                        )
                        .join(WalkforwardRun, WalkforwardRun.id == WalkforwardTrade.run_id)
                        .where(
                            WalkforwardRun.key == key,
                            WalkforwardTrade.outcome == Outcome.OPEN.value,
                        )
                        .order_by(WalkforwardTrade.placed_at.desc())
                        .limit(1)
                    )
                ).first()
        except Exception as exc:
            raise RunStoreError(f"보유 계획을 읽을 수 없다: {exc}") from exc
        if found is None:
            return None
        return {
            "trade_id": str(found[0]),
            "direction": str(found[1]),
            "stop": str(found[2]),
            "entry": str(found[3]),
        }

    async def plan_by_symbol(self, market: str, symbol: str) -> dict[str, str] | None:
        """그 **종목**을 든 가장 최근 열린 매매의 계획 — 판 key 무관 (T20 ④ 확장).

        Args:
            market: 시장 코드.
            symbol: 종목.

        Returns:
            `{trade_id, direction, stop, entry}`. 열린 매매가 없으면 None.

        Raises:
            RunStoreError: DB 에 닿을 수 없는 경우.

        Note:
            🔴 **`plan_of` 는 현재 판 key 로만 찾는다.** 매매법·비중을 바꾸거나 판을
            재생성하면 key 가 바뀌는데, 거래소 포지션·종목은 그대로다 — 그때 `plan_of`
            는 None 을 내고 감시자가 손절을 못 되건다. 종목으로 찾으면 RiskManager 가
            확정해 **DB 에 영속한** 손절을 되읽을 수 있다 (지어내는 것이 아니다 · 규칙 #4).

            ⚠️ **진입가를 함께 낸다.** 호출부가 거래소 포지션의 진입가와 대조해, 다른
            포지션에 엉뚱한 손절을 붙이는 것을 막는다 — 종목이 같아도 다른 매매일 수 있다.
        """
        try:
            async with self._factory() as session:
                found = (
                    await session.execute(
                        sa.select(
                            WalkforwardTrade.trade_id,
                            WalkforwardTrade.direction,
                            WalkforwardTrade.planned_stop,
                            WalkforwardTrade.entry,
                        )
                        .join(WalkforwardRun, WalkforwardRun.id == WalkforwardTrade.run_id)
                        .where(
                            WalkforwardRun.market == market,
                            WalkforwardRun.symbol == symbol,
                            WalkforwardTrade.outcome == Outcome.OPEN.value,
                        )
                        .order_by(WalkforwardTrade.placed_at.desc())
                        .limit(1)
                    )
                ).first()
        except Exception as exc:
            raise RunStoreError(f"{market}:{symbol} 보유 계획을 읽을 수 없다: {exc}") from exc
        if found is None:
            return None
        return {
            "trade_id": str(found[0]),
            "direction": str(found[1]),
            "stop": str(found[2]),
            "entry": str(found[3]),
        }

    async def holder_of(self, market: str, symbol: str, *, live: bool) -> tuple[str, str] | None:
        """그 종목을 이미 들고 있는 **열린 판** (다중 RUN 경로 가).

        Args:
            market: 시장 코드.
            symbol: 종목.
            live: 실계좌 경로인가.

        Returns:
            `(판 key, 닻)`. 없으면 None.

        Raises:
            RunStoreError: DB 에 닿을 수 없는 경우.

        Note:
            🔴 **Gate 무기한은 종목당 포지션이 하나다** (one-way). 두 판이 같은 종목을
            돌리면 포지션이 합쳐지고, 두 원장이 같은 포지션의 손익을 **각각** 센다 —
            양쪽 성적이 다 틀리며, 한쪽이 청산하면 다른 쪽은 이유를 모른다.

            ⇒ 그래서 **판마다 다른 종목**부터 한다 (사용자 확정 경로 가). 같은 종목에
            여러 판은 원장이 지분을 들어야 하고, 그것은 훨씬 어렵다.

            ⚠️ **닻을 함께 낸다.** 닻이 같으면 그것은 남의 판이 아니라 *우리가 이어받을*
            판이다 — 막으면 재시작이 통째로 실패한다.
        """
        try:
            async with self._factory() as session:
                row = (
                    await session.execute(
                        sa.select(WalkforwardRun.key, WalkforwardRun.anchor).where(
                            WalkforwardRun.market == market,
                            WalkforwardRun.symbol == symbol,
                            WalkforwardRun.live.is_(live),
                            WalkforwardRun.closed_at.is_(None),
                        )
                    )
                ).first()
        except Exception as exc:
            raise RunStoreError(f"{market}:{symbol} 의 열린 판을 못 읽었다: {exc}") from exc
        return None if row is None else (str(row[0]), str(row[1]))

    async def open_runs(self, *, live: bool) -> list[dict[str, Any]]:
        """**열려 있는 RUN 전부** — 기동 훅이 되살릴 목록.

        Args:
            live: 실계좌 경로만 볼 것인가.

        Returns:
            `{key, market, symbol, playbook_id, margin, leverage, meta}` 목록.

        Raises:
            RunStoreError: DB 에 닿을 수 없는 경우.

        Note:
            🔴 **여럿을 되살려야 한다** (2026-08-19 실측으로 잡았다). 기동 훅이 BTC 하나만
            박아 두고 있어서, 손으로 띄운 ETH RUN 이 `src/` 를 고칠 때마다 **조용히
            사라졌다** — 원장은 DB 에 살아남는데(T16) 러너가 안 붙으니 아무도 그
            포지션을 관리하지 않는다. T16 이 고치려던 바로 그 상태다.

            ⚠️ 매매법도 함께 낸다. 안 그러면 되살린 RUN 이 전부 기본 매매법으로 뜨고,
            0.4 로 띄운 RUN 이 0.1 이 되어 **성적이 남의 것과 섞인다**.
        """
        try:
            async with self._factory() as session:
                rows = (
                    (
                        await session.execute(
                            sa.select(WalkforwardRun)
                            .where(
                                WalkforwardRun.closed_at.is_(None),
                                WalkforwardRun.live.is_(live),
                            )
                            .order_by(WalkforwardRun.opened_at)
                        )
                    )
                    .scalars()
                    .all()
                )
        except Exception as exc:
            raise RunStoreError(f"열린 RUN 을 못 읽었다: {exc}") from exc
        return [
            {
                "key": row.key,
                "market": row.market,
                "symbol": row.symbol,
                "playbook_id": row.playbook_id,
                "margin": str(row.margin_budget or ""),
                "leverage": str(row.leverage),
                # 시작 시각 — 근거 화면(T222)이 "펀드 시작 이후 라이브 경로" 의 기점으로 읽는다.
                "opened_at": row.opened_at.isoformat(),
                # ⭐ 띄울 때의 설정(금고 셋 · 브레이커)도 같이 낸다 — 되살릴 때 이것을 안
                #    넘기면 판이 **설정 없이** 되살아난다 (2026-08-23 발견).
                "meta": dict(row.meta_json or {}),
            }
            for row in rows
        ]

    async def save(self, run_id: uuid.UUID, records: Sequence[TradeRecord]) -> None:
        """원장을 통째로 다시 쓴다.

        Args:
            run_id: 판 id.
            records: 지금 원장의 매매 전부.

        Raises:
            RunStoreError: 저장에 실패한 경우.

        Note:
            ⚠️ **매 변경마다 통째로 쓴다** — 저널이 하던 그대로다. 한 판의 매매는 많아야
            수십 건이라 비용이 없고, 이어쓰기로 두면 갱신(대기 → 체결 → 청산)을 표현할
            수 없다.

            ⛔ **여기서 행을 지우지 않는다.** 원장에서 사라진 매매가 DB 에 남는 것이,
            있지도 않은 매매가 지워지는 것보다 낫다 — 전자는 눈에 띄고 후자는 안 띈다.
        """
        if not records:
            return
        rows = [
            {
                "run_id": run_id,
                "trade_id": item.trade_id,
                "playbook": item.playbook,
                "actor": item.actor.value,
                "direction": item.direction.value,
                "outcome": item.outcome.value,
                "placed_at": item.placed_at,
                "opened_at": item.opened_at,
                "closed_at": item.closed_at,
                "half_at": item.half_at,
                "half_by": None if item.half_by is None else item.half_by.value,
                "half_price": item.half_price,
                # ⚠️ 사다리 진입의 근거다. 안 실으면 되살린 판이 평단을 잃고
                #    `filled_ratio` 가 1 로 돌아가 손익이 두 배가 된다.
                "entry_fills_json": {
                    "legs": [[str(price), str(ratio)] for price, ratio in item.entry_fills]
                },
                "entry": item.entry,
                "exit_price": item.exit_price,
                "planned_stop": item.planned_stop,
                "planned_first": item.planned_first,
                "planned_target": item.planned_target,
                "cost_pct": item.cost_pct,
                "funding_paid": item.funding_paid,
                "funding_pct": item.funding_pct,
                # 정산 열쇠가 안 실리면 재시작마다 같은 정산이 다시 붙는다 (T226 · 0114).
                "funding_keys_json": {"keys": list(item.funding_keys)},
                "leverage": item.leverage,
                "note": item.note,
                "evidence_json": evidence_rows(item.evidence),
            }
            for item in records
        ]
        statement = insert(WalkforwardTrade).values(rows)
        update = {
            name: statement.excluded[name] for name in rows[0] if name not in ("run_id", "trade_id")
        }
        try:
            async with self._factory() as session:
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["run_id", "trade_id"], set_=update
                    )
                )
                await session.commit()
        except Exception as exc:
            raise RunStoreError(f"원장을 저장할 수 없다 ({run_id}): {exc}") from exc

    async def record_order(
        self,
        run_id: uuid.UUID,
        trade_id: str,
        *,
        role: str,
        status: str,
        exchange_order_id: str = "",
        contracts: str = "",
        price: Decimal | None = None,
        error: str = "",
        raw: JsonDict | None = None,
    ) -> None:
        """주문 하나의 **지금 상태**를 적는다.

        Args:
            run_id: 판 id.
            trade_id: 매매의 짧은 id.
            role: 진입 · 익절1 · 익절2 · 손절 · 청산 · 이어받음.
            status: 거래소가 말한 상태. `sending` 이 남아 있으면 응답을 못 받은 것이다.
            exchange_order_id: 거래소 주문 id.
            contracts: 계약 수.
            price: 지정가·발동가.
            error: 실패 문구.
            raw: 원문.

        Note:
            🔴 **매매 행을 안 찾는다** (2026-08-19 사고 ④). 예전에는 `wf_trades` 를 먼저
            조회해 부모 id 를 얻었고, 없으면 **조용히 버렸다.** 그런데 러너의 걸음은
            `_place()`(주문) → `_persist()`(저장) 순서라 **진입 주문은 언제나 부모가
            없는 시점에 왔다** — 거래소 체결 77건 중 이 표에 남은 것이 29건뿐이었고
            그 29건은 전부 손절이었다(손절만 걸음마다 다시 적혀 살아남았다).

            ⇒ 이제 판 id + 매매 id 로 바로 적는다. **어느 순서로 와도 남는다.**
            ⭐ 부모 조회가 사라져 DB 왕복이 2회 → 1회다 (실측 왕복 1.07ms).

            ⚠️ 실패해도 **던지지 않는다.** 이 표는 되짚기용이고, 여기서 예외가 나면
            주문 경로가 통째로 멈춘다 (절대 규칙 #8-1).
        """
        try:
            async with self._factory() as session:
                values = {
                    "run_id": run_id,
                    "trade_id": trade_id,
                    "role": role,
                    "status": status,
                    "exchange_order_id": exchange_order_id,
                    "contracts": contracts,
                    "price": price,
                    "error": error,
                    "raw_json": dict(raw or {}),
                }
                statement = insert(WalkforwardOrder).values(values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["run_id", "trade_id", "role"],
                        set_={
                            name: statement.excluded[name]
                            for name in values
                            if name not in ("run_id", "trade_id", "role")
                        },
                    )
                )
                await session.commit()
        except Exception as exc:  # 주문 경로를 막지 않는다 (절대 규칙 #8-1)
            _logger.error(
                "wf_order_unsaved",
                payload={
                    "trade_id": trade_id,
                    "role": role,
                    "error": str(exc)[:200],
                    "note": "되짚기용 기록이다 — 주문 자체는 나갔다",
                },
            )

    async def record_calibration(
        self,
        run_id: uuid.UUID,
        *,
        kind: str,
        trade_id: str = "",
        role: str = "",
        intended_price: Decimal | None = None,
        judge_close: Decimal | None = None,
        judge_ts: datetime | None = None,
        rvol: Decimal | None = None,
        wanted_contracts: Decimal | None = None,
        sent_contracts: Decimal | None = None,
        amount: Decimal | None = None,
        extra: JsonDict | None = None,
    ) -> None:
        """교정 원장에 한 줄 **더한다** (T185).

        Args:
            run_id: 판 id.
            kind: `entry` · `exit` · `funding`.
            trade_id: 매매의 짧은 id (펀딩은 빈 문자열).
            role: `wf_orders.role` 과 같은 이름.
            intended_price: 러너가 걸려고 한 지정가 — **백테스트 가정가**.
            judge_close: 판정 봉 종가 (물러서기의 기준점).
            judge_ts: 판정 봉 시각. **tz-aware 여야 한다** (절대 규칙 #7).
            rvol: 진입 시점 실현변동성 (%/봉).
            wanted_contracts: 증거금 한도가 없었다면 걸었을 계약 수.
            sent_contracts: 실제로 보낸 계약 수.
            amount: 펀딩 정산액 (음수면 받은 것).
            extra: 자본·배율 등 되짚기용 나머지.

        Raises:
            ValueError: `judge_ts` 가 naive 인 경우.

        Note:
            🔴 **덮어쓰지 않는다.** `record_order` 는 역할당 한 행을 덮어써 *지금
            상태* 를 드는데, 교정은 *모든 시도* 를 세는 일이라 정반대다.

            ⚠️ 실패해도 **던지지 않는다.** 이 표는 관측이고, 여기서 예외가 나면
            주문 경로가 통째로 멈춘다 (절대 규칙 #8-1).
        """
        if judge_ts is not None and judge_ts.tzinfo is None:
            raise ValueError("judge_ts 는 tz-aware 여야 한다 (절대 규칙 #7)")
        try:
            async with self._factory() as session:
                session.add(
                    WalkforwardCalibration(
                        run_id=run_id,
                        trade_id=trade_id,
                        kind=kind,
                        role=role,
                        intended_price=intended_price,
                        judge_close=judge_close,
                        judge_ts=judge_ts,
                        rvol=rvol,
                        wanted_contracts=wanted_contracts,
                        sent_contracts=sent_contracts,
                        amount=amount,
                        extra_json=dict(extra or {}),
                    )
                )
                await session.commit()
        except Exception as exc:  # 주문 경로를 막지 않는다 (절대 규칙 #8-1)
            _logger.error(
                "wf_calibration_unsaved",
                payload={
                    "trade_id": trade_id,
                    "kind": kind,
                    "error": str(exc)[:200],
                    "note": "관측 기록이다 — 주문 자체는 나갔다",
                },
            )

    async def trace(self, trade_ids: Sequence[str]) -> dict[str, JsonDict]:
        """체결 이력 한 줄이 **어느 계획에서 나왔나** (사용자 요구 2026-08-20).

        Args:
            trade_ids: 주문 이름에서 뽑은 매매 id — **앞자리만일 수 있다.**

        Returns:
            `{준 값 그대로: {계획가 · 시각 · 방향 · 배율 · 종목}}`. 못 찾으면 빠진다.

        Note:
            ⚠️ **앞자리로 맞춘다.** 주문 이름은 30자 제한이 있어(`TEXT_LIMIT`) 판 표식이
            붙는 새 형식에서는 매매 id 가 **8자로 잘려** 들어간다:

            ```
            t-72bb3b4690f3-cl-0      옛 형식 — 12자 그대로
            t-82e456-72bb3b46-cl-1   새 형식 — 판 표식 6자 + 매매 id 8자
            ```

            그래서 완전 일치로 찾으면 **새 형식이 전부 안 맞는다.**

        Note:
            🔴 사용자 요구: *"주문을 클릭하면 (…) 진입가, 손절가, 1차익절가, 익절
            이렇게 보여주게"* + *"레버리지도 RUN 이 지워져도 남게"*.

            ⭐ **판을 지워도 남는다.** 판을 지우는 것은 `closed_at` 을 찍는 일이지 행을
            지우는 일이 아니다 — 매매·주문 행이 그대로 있고, 여기서 그것을 되읽는다.

            ⚠️ **거래소가 이 값을 모른다.** 체결 이력에는 체결가와 실현 손익만 있고
            *계획*(손절선·1차 익절·목표)은 우리 원장에만 있다. 그래서 조인이 필요하고,
            조인 열쇠는 주문 이름에 박은 매매 id 다 (T18 ⑤).

            ⛔ **실패해도 던지지 않는다** — 상세는 곁들이는 정보이고, 못 읽었다고 이력
            자체가 안 뜨면 그쪽이 더 나쁘다 (절대 규칙 #8-1).
        """
        wanted = [item for item in dict.fromkeys(trade_ids) if item]
        if not wanted:
            return {}
        try:
            async with self._factory() as session:
                rows = (
                    (
                        await session.execute(
                            sa.select(WalkforwardTrade, WalkforwardRun.symbol)
                            .join(WalkforwardRun, WalkforwardRun.id == WalkforwardTrade.run_id)
                            .where(
                                sa.or_(
                                    *(WalkforwardTrade.trade_id.like(f"{item}%") for item in wanted)
                                )
                            )
                        )
                    )
                    .tuples()
                    .all()
                )
        except Exception as exc:
            _logger.warning("wf_trace_unreadable", payload={"error": str(exc)[:200]})
            return {}

        def when(value: datetime | None) -> str | None:
            """시각을 ISO 문자열로 — None 은 그대로.

            Args:
                value: 시각.

            Returns:
                ISO8601 또는 None.
            """
            return None if value is None else value.isoformat()

        # ⭐ **부른 이름 그대로 돌려준다** — 화면은 자기가 뽑은 앞자리로 찾는다.
        found = {item: row for item in wanted for row, _ in rows if row.trade_id.startswith(item)}
        symbols = {row.trade_id: symbol for row, symbol in rows}
        return {
            key: {
                "trade_id": row.trade_id,
                "symbol": symbols.get(row.trade_id, ""),
                "playbook": row.playbook,
                "direction": row.direction,
                "outcome": row.outcome,
                "leverage": str(row.leverage),
                "entry": str(row.entry),
                "stop": str(row.planned_stop),
                "first": str(row.planned_first),
                "target": str(row.planned_target),
                "exit": None if row.exit_price is None else str(row.exit_price),
                "half_price": None if row.half_price is None else str(row.half_price),
                "half_by": row.half_by,
                "funding_paid": None if row.funding_paid is None else str(row.funding_paid),
                "placed_at": when(row.placed_at),
                "opened_at": when(row.opened_at),
                "half_at": when(row.half_at),
                "closed_at": when(row.closed_at),
            }
            for key, row in found.items()
        }

    async def stop_owners(self, stop_ids: Sequence[str]) -> dict[str, str]:
        """조건부 주문 id → **그 손절을 건 매매** (사용자 신고 2026-08-20).

        Args:
            stop_ids: Gate 조건부 주문 id 들 (`ao-` 뒤의 숫자).

        Returns:
            `{조건부 id: 매매 id}`. 못 찾으면 빠진다.

        Note:
            🔴 사용자 신고: 손절이 났는데 콘솔이 *"RUN 미상 · 배율 — · 수익률 —"* 로
            떴다. 원장은 그 매매를 알고 있었는데(`81201e · 손절 · -24.51%`) 화면만
            못 이었다.

            조건부가 발동하면 Gate 가 `ao-{id}` 로 주문을 만드는데 그 이름에 **우리 매매
            id 가 없다.** 이을 열쇠는 우리가 손절을 걸 때 받은 **조건부 주문 id** 뿐이고,
            그것이 `wf_orders` 의 손절 행에 있다.

            ⭐ **추정이 아니다.** 시각·종목으로 맞추는 방법도 있지만 그것은 틀릴 여지가
            있고 (3분 차이로 남의 손익이 붙은 적이 있다), id 는 하나뿐이다.
        """
        wanted = [item for item in dict.fromkeys(stop_ids) if item]
        if not wanted:
            return {}
        try:
            async with self._factory() as session:
                rows = (
                    (
                        await session.execute(
                            sa.select(
                                WalkforwardOrder.exchange_order_id,
                                WalkforwardOrder.trade_id,
                            ).where(WalkforwardOrder.exchange_order_id.in_(wanted))
                        )
                    )
                    .tuples()
                    .all()
                )
        except Exception as exc:
            _logger.warning("wf_stop_owners_unreadable", payload={"error": str(exc)[:200]})
            return {}
        return {str(order_id): str(trade_id) for order_id, trade_id in rows}

    async def run_tags(self) -> dict[str, JsonDict]:
        """판 표식 6자 → **그 판이 무엇이었나** (사용자 요구 2026-08-20).

        Returns:
            `{표식: {key, symbol, playbook, leverage, alive}}`.

        Note:
            🔴 사용자 요구: *"RUN 에 고유 ID 가 아니라 종목명이 적혔으면 좋겠고, RUN 을
            지워도 종목명은 남아있으면 좋겠네."*

            ⭐ 표식은 판 id 의 **뒤 6자**다 (`run_tag`). 주문 이름에 그것이 박혀 있어
            거래소 이력만 보고도 어느 판인지 되짚을 수 있다.

            ⚠️ **닫힌 판도 준다.** 그것이 요구의 핵심이다 — 지운 판의 주문이 이력에는
            영영 남으므로, 그때 종목과 배율을 못 읽으면 화면이 `RUN 미상`만 띄운다.
        """
        try:
            async with self._factory() as session:
                rows = (
                    (
                        await session.execute(
                            sa.select(
                                WalkforwardRun.key,
                                WalkforwardRun.symbol,
                                WalkforwardRun.playbook,
                                WalkforwardRun.leverage,
                                WalkforwardRun.closed_at,
                            ).where(WalkforwardRun.live)
                        )
                    )
                    .tuples()
                    .all()
                )
        except Exception as exc:
            _logger.warning("wf_run_tags_unreadable", payload={"error": str(exc)[:200]})
            return {}
        return {
            key[-6:]: {
                "key": key,
                "symbol": symbol,
                "playbook": playbook,
                "leverage": str(leverage),
                "alive": closed is None,
            }
            for key, symbol, playbook, leverage, closed in rows
        }

    async def rejections(self, run_id: uuid.UUID) -> tuple[int, str]:
        """이 판에서 **거절된 주문**이 몇 건이고 마지막 사유가 무엇인가 (2026-08-20).

        Args:
            run_id: 판 id.

        Returns:
            `(건수, 마지막 사유)`. 없으면 `(0, "")`.

        Note:
            🔴 **화면이 재시작에 잊었다** (사용자 신고 2026-08-20). 러너의 `failures`·
            `last_error` 는 메모리라 프로세스가 다시 뜨면 0 이 된다 — 그래서 **한 건도
            못 내는 판**과 **자리가 아직 안 난 판**이 화면에서 똑같이 보였다. 실측:

            ```
            SOL  15:30  진입0/1 rejected  "계약 수 0 가 최소 1 에 못 미친다"
            19:27 재시작 → 화면 failures 0 · last_error ""   ← 사실이 사라졌다
            ```

            ⇒ 사실은 `wf_orders` 에 남아 있었다. **한 번 읽어 되살린다.**

            ⚠️ **걸음마다 읽지 않는다.** 화면이 판마다 몇 초 간격으로 물어 오므로 폴링
            경로에 두면 DB 왕복이 그만큼 늘고, 그 값은 러너가 이미 메모리로 들고 있다.

            ⛔ **실패해도 던지지 않는다** — 되짚기용 값이고, 여기서 예외가 나면 판이
            아예 못 뜬다 (절대 규칙 #8-1).
        """
        try:
            async with self._factory() as session:
                rows = (
                    await session.execute(
                        sa.select(WalkforwardOrder.error)
                        .where(
                            WalkforwardOrder.run_id == run_id,
                            WalkforwardOrder.status == "rejected",
                        )
                        .order_by(WalkforwardOrder.created_at)
                    )
                ).scalars()
                found = [str(item) for item in rows]
        except Exception as exc:
            _logger.warning("wf_rejections_unreadable", payload={"error": str(exc)[:200]})
            return 0, ""
        return len(found), next((item for item in reversed(found) if item), "")

    async def put_meta(self, run_id: uuid.UUID, field_name: str, value: object) -> None:
        """판 메타의 한 칸을 바꾼다 (T218 — 대기 진입 계획 영속화).

        Args:
            run_id: 판 id.
            field_name: `meta_json` 안의 키.
            value: 넣을 값. None 이면 그 키를 지운다.

        Raises:
            RunStoreError: 저장에 실패한 경우.

        Note:
            ⚠️ 통째로 다시 쓴다 — JSONB 부분 갱신보다 단순하고, 메타는 작다. 같은 판을 두
            프로세스가 동시에 쓰는 일은 리더 락이 막는다.
        """
        try:
            async with self._factory() as session:
                row = (
                    await session.execute(
                        sa.select(WalkforwardRun).where(WalkforwardRun.id == run_id)
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise RunStoreError(f"판이 없다 ({run_id})")
                meta = dict(row.meta_json or {})
                if value is None:
                    meta.pop(field_name, None)
                else:
                    meta[field_name] = value
                await session.execute(
                    sa.update(WalkforwardRun)
                    .where(WalkforwardRun.id == run_id)
                    .values(meta_json=meta)
                )
                await session.commit()
        except RunStoreError:
            raise
        except Exception as exc:
            raise RunStoreError(f"판 메타를 저장할 수 없다 ({run_id}): {exc}") from exc

    async def close(self, key: str, *, reason: str) -> bool:
        """판을 닫는다 — **사람이 RUN 을 지울 때만**.

        Args:
            key: 짧은 id.
            reason: 왜 닫았나.

        Returns:
            닫았으면 True. 그런 판이 없거나 이미 닫혔으면 False.

        Note:
            ⛔ **시간으로 자동 종료하지 않는다.** N 을 정할 근거가 없고, 닫는 순간
            거래소 포지션이 고아가 된다 (사용자 확정 2026-08-18).
        """
        try:
            async with self._factory() as session:
                done = cast(
                    "sa.CursorResult[Any]",
                    await session.execute(
                        sa.update(WalkforwardRun)
                        .where(
                            WalkforwardRun.key == key,
                            WalkforwardRun.closed_at.is_(None),
                        )
                        .values(closed_at=datetime.now(UTC), closed_reason=reason)
                    ),
                )
                await session.commit()
        except Exception as exc:
            _logger.error(
                "wf_run_unclosed",
                payload={"key": key, "error": str(exc)[:200], "note": "닻이 열린 채로 남는다"},
            )
            return False
        return bool(done.rowcount)

    async def _records(self, session: AsyncSession, run_id: uuid.UUID) -> tuple[TradeRecord, ...]:
        """저장된 매매를 원장 기록으로 되돌린다.

        Args:
            session: 열려 있는 세션.
            run_id: 판 id.

        Returns:
            진입 순서를 지킨 기록들.

        Note:
            ⚠️ **정렬이 `placed_at` 이다.** 원장은 순서대로 자본을 굴리므로(`_walk`),
            순서가 어긋나면 복리 계산이 달라진다 — 같은 매매인데 손익률이 변한다.
        """
        rows = (
            (
                await session.execute(
                    sa.select(WalkforwardTrade)
                    .where(WalkforwardTrade.run_id == run_id)
                    .order_by(WalkforwardTrade.placed_at, WalkforwardTrade.trade_id)
                )
            )
            .scalars()
            .all()
        )
        return tuple(_to_record(row) for row in rows)


def _to_record(row: WalkforwardTrade) -> TradeRecord:
    """DB 행 → 원장 기록.

    Args:
        row: 저장된 매매.

    Returns:
        원장 기록.

    Note:
        🔴 **열거형은 값으로 되돌린다** (`Actor("시스템")`). 모르는 값이 오면 여기서
        터지는 것이 맞다 — 조용히 기본값으로 떨어뜨리면 이어받은 판의 방향이나 결과가
        틀린 채로 계속 돈다 (절대 규칙 #8).
    """
    return TradeRecord(
        trade_id=row.trade_id,
        playbook=row.playbook,
        actor=Actor(row.actor),
        direction=Direction(row.direction),
        outcome=Outcome(row.outcome),
        placed_at=row.placed_at,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        half_at=row.half_at,
        half_by=None if row.half_by is None else HalfBy(row.half_by),
        half_price=row.half_price,
        entry_fills=tuple(
            (Decimal(str(price)), Decimal(str(ratio)))
            for price, ratio in (row.entry_fills_json or {}).get("legs", [])
        ),
        entry=row.entry,
        exit_price=row.exit_price,
        funding_paid=row.funding_paid if row.funding_paid is not None else Decimal(0),
        funding_pct=row.funding_pct if row.funding_pct is not None else Decimal(0),
        funding_keys=tuple(str(key) for key in (row.funding_keys_json or {}).get("keys", [])),
        planned_stop=row.planned_stop,
        planned_first=row.planned_first,
        planned_target=row.planned_target,
        cost_pct=row.cost_pct,
        leverage=row.leverage,
        note=row.note,
        evidence=evidence_from_rows(row.evidence_json),
    )
