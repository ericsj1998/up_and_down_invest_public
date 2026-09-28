"""몫 모드 돈 나누기 (T320 P5) — 한 포지션을 여러 몫이 나눠 쓸 때 펀딩 · 수수료 · 손익 대조 · 화면.

지키는 것:

- 펀딩 정산 한 줄은 **그때 든 몫들에 계약 비로** 나뉜다 · 정산 뒤 닫힌 몫도 제 몫을 받는다 ·
  같은 정산을 두 번 안 붙인다 · 불타기 계약은 불타기 뒤 정산부터 센다.
- Gate 청산 행은 포지션 생애 하나에 한 줄 — 그 수수료를 생애 안 몫들에 **거래 명목 비로** 나눈다 ·
  생애가 안 끝났으면(행 없음) 안 바꾼다 · 앞 생애 수수료를 뒤에 연 몫에 붙이지 않는다.
- 손익 대조는 원장이 비었을 때만 잰다(청산 행이 아직 닫힌 몫을 안 담는다).
- 다리별 미실현 = 몫 계약 x (표시가 - 몫 평단) · 합은 거래소 미실현.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from test_share_runner import (  # pyright: ignore[reportMissingImports]
    ShareExchange,
    _Runner,  # pyright: ignore[reportPrivateUsage]
    armed,
    now_of,
    share,
)
from updown.apps.api.rebalancer import mark_and_multiplier, share_rows, split_unrealized
from updown.common.domain.instrument import Instrument
from updown.orchestration.walkforward.ledger import Ledger, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import funding_weight, liq_findings


def at(hour: int) -> datetime:
    return datetime(2026, 1, 1, hour, tzinfo=UTC)


def opened(record: TradeRecord, hour: int) -> TradeRecord:
    return dc_replace(record, opened_at=at(hour), placed_at=at(hour))


class Funded(_Runner):
    """펀딩 동기화만 보는 러너 — 세션 입구(`apply_funding`)는 원장을 바로 고친다."""

    def __init__(self, exch: ShareExchange, ledger: Ledger) -> None:
        super().__init__(exch, ledger)
        self._last_funding_at = float("-inf")
        self._funding_rows_seen: set[str] = set()
        self._session.apply_funding = self._apply  # pyright: ignore[reportPrivateUsage]

    def _apply(
        self,
        *,
        paid: Decimal,
        pct: Decimal,
        keys: tuple[str, ...] = (),
        reset: bool = False,
        leg: str | None = None,
    ) -> None:
        del reset
        book: Ledger = self._session.ledger  # pyright: ignore[reportPrivateUsage]
        item = next(r for r in book.records if r.playbook == leg and r.outcome is Outcome.OPEN)
        book.replace(
            dc_replace(
                item,
                funding_paid=item.funding_paid + paid,
                funding_pct=item.funding_pct + pct,
                funding_keys=(*item.funding_keys, *keys),
            )
        )


def funding_board(*rows: tuple[int, str]) -> tuple[Funded, TradeRecord, TradeRecord]:
    led = Ledger()
    a = opened(share("aaaaaaaa1111", stop="95", contracts=2, playbook="breakout@0.1.0"), 1)
    b = opened(share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0"), 5)
    led.add(a)
    led.add(b)
    exch = ShareExchange(size=5)

    async def book(limit: int = 30) -> list[dict[str, str]]:
        del limit
        return [
            {
                "type": "fund",
                "text": "BTC_USDT:funding",
                "change": change,
                "time": str(int(at(hour).timestamp())),
            }
            for hour, change in rows
        ]

    exch.account_book = book  # type: ignore[attr-defined]
    return Funded(exch, led), a, b


def sync(run: Funded) -> None:
    asyncio.run(run._sync_funding())  # pyright: ignore[reportPrivateUsage]


class TestFundingIsSplitByContracts:
    def test_a_settlement_goes_to_the_shares_held_then(self) -> None:
        """08시 정산 -5 USDT — 둘 다 들었다: A 2/5 · B 3/5. 03시 정산 -1 은 A 만 들었다."""
        run, a, b = funding_board((3, "-1"), (8, "-5"))
        sync(run)
        assert now_of(run, a).funding_paid == Decimal(3)
        assert now_of(run, b).funding_paid == Decimal(3)
        assert len(now_of(run, a).funding_keys) == 2
        assert len(now_of(run, b).funding_keys) == 1

    def test_a_share_closed_after_the_settlement_still_pays_its_part(self) -> None:
        run, a, b = funding_board((8, "-5"))
        book: Ledger = run._session.ledger  # pyright: ignore[reportPrivateUsage]
        book.replace(a.closed(at=at(9), price=Decimal(110), outcome=Outcome.SIGNAL_EXIT))
        sync(run)
        assert now_of(run, a).funding_paid == Decimal(2), "닫힌 몫도 제 몫을 받는다"
        assert now_of(run, b).funding_paid == Decimal(3), "남은 몫에 몰아 주지 않는다"

    def test_the_same_settlement_is_not_charged_twice(self) -> None:
        run, _a, b = funding_board((8, "-5"))
        sync(run)
        run._last_funding_at = float("-inf")  # pyright: ignore[reportPrivateUsage]
        run._funding_rows_seen = set()  # pyright: ignore[reportPrivateUsage]  # 재시작
        sync(run)
        assert now_of(run, b).funding_paid == Decimal(3)

    def test_the_weight_counts_adds_only_after_they_were_bought(self) -> None:
        item = dc_replace(
            share("x", stop="95", contracts=4, playbook="p@0"), add_contracts=2, add_at=at(6)
        )
        assert funding_weight(item, at(5)) == 4
        assert funding_weight(item, at(7)) == 6


def closed_share(
    trade_id: str, *, contracts: int, entry: str, exit_: str, hours: tuple[int, int], pid: str
) -> TradeRecord:
    base = dc_replace(
        share(trade_id, stop="1", contracts=contracts, playbook=f"{pid}@0.1.0"),
        entry=Decimal(entry),
    )
    return opened(base, hours[0]).closed(
        at=at(hours[1]), price=Decimal(exit_), outcome=Outcome.SIGNAL_EXIT
    )


def fee_board(rows: list[dict[str, str]]) -> tuple[_Runner, TradeRecord, TradeRecord]:
    led = Ledger()
    a = closed_share("aaaaaaaa1111", contracts=2, entry="100", exit_="110", hours=(1, 6), pid="a")
    b = closed_share("bbbbbbbb2222", contracts=3, entry="100", exit_="90", hours=(2, 8), pid="b")
    led.add(a)
    led.add(b)
    exch = ShareExchange(size=0)

    async def closes(instrument: Instrument, limit: int = 30) -> list[dict[str, str]]:
        del instrument, limit
        return rows

    exch.position_closes = closes  # type: ignore[attr-defined]
    return _Runner(exch, led), a, b


def align(run: _Runner, record: TradeRecord) -> None:
    asyncio.run(run._align_fee(record.trade_id))  # pyright: ignore[reportPrivateUsage]


class TestLifeFeeIsSplitByTradedNotional:
    """Gate 청산 행은 포지션 생애 하나에 한 줄 — 그 수수료를 생애 안 몫들에 나눈다."""

    def test_the_fee_follows_each_shares_traded_notional(self) -> None:
        life = {
            "first_open_time": str(at(1).timestamp()),
            "time": str(at(8).timestamp()),
            "pnl_fee": "-0.88",
        }
        run, a, b = fee_board([life])
        align(run, a)
        # A 거래 명목 2x100 + 2x110 = 420 · B 3x100 + 3x90 = 570 · 합 990
        assert now_of(run, a).fee_actual == Decimal("0.88") * 420 / 990
        assert now_of(run, b).fee_actual == Decimal("0.88") * 570 / 990
        assert now_of(run, a).cost_pct == Decimal("0.88") * 420 / 990 / 200

    def test_an_unfinished_life_is_left_alone(self) -> None:
        """포지션이 아직 남았다(청산 행 없음) — 모형 비용을 둔다."""
        run, a, _b = fee_board([])
        align(run, a)
        assert now_of(run, a).fee_actual is None

    def test_an_earlier_life_is_not_charged_to_a_later_share(self) -> None:
        """앞 생애(00 ~ 00:30)가 끝난 뒤 연 몫은 그 생애 수수료를 안 받는다."""
        earlier = {
            "first_open_time": str(at(0).timestamp()),
            "time": str(at(0).timestamp() + 1800),
            "pnl_fee": "-9",
        }
        run, a, _b = fee_board([earlier])
        align(run, a)
        assert now_of(run, a).fee_actual is None


class TestPnlAuditWaitsForAnEmptyBook:
    def test_it_does_not_compare_while_a_share_is_open(self) -> None:
        run, exch, a, _b = armed()
        book: Ledger = run._session.ledger  # pyright: ignore[reportPrivateUsage]
        book.replace(a.closed(at=at(9), price=Decimal(80), outcome=Outcome.STOP_LOSS))
        asked: list[bool] = []

        async def closes(instrument: Instrument, limit: int = 30) -> list[dict[str, str]]:
            del instrument, limit
            asked.append(True)
            return []

        exch.position_closes = closes  # type: ignore[attr-defined]
        run._last_pnl_at = float("-inf")  # pyright: ignore[reportPrivateUsage]
        run._pnl_findings = None  # pyright: ignore[reportPrivateUsage]
        found: Any = asyncio.run(run._pnl_audit())  # pyright: ignore[reportPrivateUsage]
        assert found == []
        assert asked == [], "B 가 남은 동안 거래소 청산 행은 A 를 아직 안 담는다 — 대지 않는다"


class TestUnrealizedIsSplitPerShare:
    def test_each_leg_gets_its_own_mark_to_market(self) -> None:
        a = dc_replace(share("a", stop="1", contracts=2, playbook="breakout@0"), entry=Decimal(100))
        b = dc_replace(share("b", stop="1", contracts=3, playbook="channel@0"), entry=Decimal(120))
        got = split_unrealized([a, b], Decimal(-10), (Decimal(110), Decimal(1)))
        assert got == {"breakout": Decimal(20), "channel": Decimal(-30)}

    def test_without_a_mark_it_falls_back_to_contracts(self) -> None:
        a = share("a", stop="1", contracts=2, playbook="breakout@0")
        b = share("b", stop="1", contracts=3, playbook="channel@0")
        got = split_unrealized([a, b], Decimal(-10), None)
        assert got == {"breakout": Decimal(-4), "channel": Decimal(-6)}

    def test_the_mark_comes_from_the_position_read(self) -> None:
        assert mark_and_multiplier({"mark_price": "110", "value": "55", "size": "5"}) == (
            Decimal(110),
            Decimal("0.1"),
        )
        assert mark_and_multiplier({"mark_price": "0", "value": "55", "size": "5"}) is None


class TestShareRowsForTheScreen:
    """화면 몫 줄 — 종목 줄 아래 다리마다 한 줄(D3 ①)."""

    def shares(self) -> list[TradeRecord]:
        a = dc_replace(
            share("a", stop="95", contracts=2, playbook="breakout@0.1.0"), entry=Decimal(100)
        )
        b = dc_replace(
            share("b", stop="90", contracts=3, playbook="channel@0.1.0"), entry=Decimal(120)
        )
        return [a, b]

    def test_each_share_gets_its_name_money_and_margin(self) -> None:
        rows = share_rows(
            self.shares(),
            "-10",
            "56",
            (Decimal(110), Decimal(1)),
            {"breakout@0.1.0": "돌파 롱", "channel@0.1.0": "일봉 채널"},
        )
        assert [r["name"] for r in rows] == ["돌파 롱", "일봉 채널"]
        assert [r["unrealized"] for r in rows] == ["20.0000", "-30.0000"]
        # 증거금은 진입 명목 비 200 : 360
        assert [r["margin"] for r in rows] == ["20.0000", "36.0000"]
        assert [r["contracts"] for r in rows] == [2, 3]

    def test_unreadable_exchange_values_are_left_empty(self) -> None:
        rows = share_rows(self.shares(), "", "", None, {})
        assert [r["unrealized"] for r in rows] == [None, None]
        assert [r["margin"] for r in rows] == [None, None]
        assert [r["name"] for r in rows] == ["breakout", "channel"], "이름이 없으면 매매법 id"


class TestLiquidationIsCheckedPerShare:
    """감사 ⑨ — 청산가는 합친 포지션 하나의 값 · 몫마다 자기 손절과 댄다."""

    def test_a_far_stop_behind_the_liquidation_is_caught(self) -> None:
        near = dc_replace(share("a", stop="95", contracts=2, playbook="p@0"), entry=Decimal(100))
        far = dc_replace(share("b", stop="80", contracts=3, playbook="q@0"), entry=Decimal(100))
        assert liq_findings(near, Decimal(85), share=True) == []
        found = liq_findings(far, Decimal(85), share=True)
        assert [f["code"] for f in found] == ["liq_inside_stop"]
        assert found[0]["detail"].startswith("몫 b — ")

    def test_one_position_reads_like_before(self) -> None:
        held = dc_replace(share("a", stop="80", contracts=2, playbook="p@0"), entry=Decimal(100))
        found = liq_findings(held, Decimal(85))
        assert found[0]["detail"].startswith("거래소 청산가 85 가 손절 80 보다")
        thin = dc_replace(held, planned_stop=Decimal("87.5"))
        assert [f["code"] for f in liq_findings(thin, Decimal(85))] == ["stop_near_liquidation"]
