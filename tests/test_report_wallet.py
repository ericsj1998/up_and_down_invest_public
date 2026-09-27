"""리포트 기간별 지갑 그래프 — 넣은 돈 · 번 돈 · 잃은 돈 (사용자 2026-09-27).

> *"내가 사비로 금액을 추가했을 때는 색을 회색으로 … 내가 벌어서 얻은 수익과 내가 넣은 돈과,
> 잃은 돈이 그래프에서 한번에 보여야 해."*

## 무엇을 막으려는 시험인가

1. 입금이 번 돈으로 보이는 것 — 입금은 잔고와 넣은 돈을 **같이** 올려 차이(번 돈)는 그대로여야 한다.
2. 구간 시작 잔고를 지어내는 것 — 구간 앞 줄이 있으면 그 잔고, 없으면 첫 줄에서 거꾸로 푼 값
   (표시 `reached`).
3. 바이낸스 밀리초 시각을 초로 읽어 먼 미래로 보내는 것.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.orchestration.report.equity import wallet_payload, wallet_series

T0 = datetime(2026, 9, 4, 14, tzinfo=UTC)


def row(hours: float, kind: str, change: str, balance: str, *, ms: bool = False) -> dict[str, str]:
    at = (T0 + timedelta(hours=hours)).timestamp()
    return {
        "type": kind,
        "change": change,
        "balance": balance,
        "time": str(int(at * 1000) if ms else at),
    }


BOOK = [
    row(0, "dnw", "300", "300"),
    row(10, "pnl", "10", "310"),
    row(11, "fee", "-1", "309"),
    row(20, "dnw", "66", "375"),
    row(30, "pnl", "-5", "370"),
]
MOVES = [r for r in BOOK if r["type"] == "dnw"]


class TestWalletSeries:
    def test_deposit_raises_both_lines_and_earned_is_the_gap(self) -> None:
        got = wallet_series(
            BOOK, MOVES, T0 + timedelta(hours=5), T0 + timedelta(hours=40), points=35
        )
        assert got is not None
        first, last = got.points[0], got.points[-1]
        assert (first.balance, first.principal) == (Decimal(300), Decimal(300))
        assert (last.balance, last.principal) == (Decimal(370), Decimal(366))
        assert got.balance - got.principal == Decimal(4)  # 번 10, 수수료 1, 잃은 5
        assert got.deposits == Decimal(66) and got.reached is True
        # 입금 직후(20시) 두 선이 같이 오른다 — 차이는 입금 전과 같다(9)
        at20 = next(p for p in got.points if p.at >= T0 + timedelta(hours=20))
        assert at20.balance - at20.principal == Decimal(9)

    def test_points_cover_the_window_in_order(self) -> None:
        since, until = T0 + timedelta(hours=5), T0 + timedelta(hours=40)
        got = wallet_series(BOOK, MOVES, since, until, points=7)
        assert got is not None
        stamps = [p.at for p in got.points]
        assert stamps[0] == since and stamps[-1] == until and len(stamps) == 8
        assert stamps == sorted(stamps)

    def test_window_before_the_first_row_unwinds_the_first_change(self) -> None:
        got = wallet_series(BOOK[1:], MOVES, T0 + timedelta(hours=5), T0 + timedelta(hours=40))
        assert got is not None
        assert got.reached is False
        assert got.points[0].balance == Decimal(300)  # 310 에서 10 을 거꾸로

    def test_milliseconds_are_read_as_milliseconds(self) -> None:
        book = [
            row(0, "TRANSFER", "300", "300", ms=True),
            row(10, "REALIZED_PNL", "7", "307", ms=True),
        ]
        got = wallet_series(book, book[:1], T0 + timedelta(hours=1), T0 + timedelta(hours=20))
        assert got is not None
        assert (got.balance, got.principal) == (Decimal(307), Decimal(300))

    def test_same_second_rows_are_ordered_by_row_id(self) -> None:
        """🔴 실측(2026-09-27): 같은 초에 수수료(171) · 청산 손익(172) — 거래소는 최신부터 준다.

        시각만으로 줄 세우면 171 이 마지막이 되어 잔고를 410.84 로 읽었다(실제 406.82).
        """
        at = str((T0 + timedelta(hours=10)).timestamp())
        newest_first = [
            {"id": "172", "type": "pnl", "change": "-4.023", "balance": "406.817", "time": at},
            {"id": "171", "type": "fee", "change": "-0.070", "balance": "410.840", "time": at},
            row(0, "dnw", "366", "366") | {"id": "1"},
        ]
        got = wallet_series(
            newest_first, newest_first[2:], T0 + timedelta(hours=1), T0 + timedelta(hours=20)
        )
        assert got is not None
        assert got.balance == Decimal("406.817")

    def test_same_second_without_ids_keeps_the_exchange_order(self) -> None:
        at = str((T0 + timedelta(hours=10)).timestamp())
        newest_first = [
            {"type": "pnl", "change": "-4", "balance": "406", "time": at},
            {"type": "fee", "change": "-1", "balance": "410", "time": at},
        ]
        got = wallet_series(newest_first, [], T0 + timedelta(hours=1), T0 + timedelta(hours=20))
        assert got is not None and got.balance == Decimal(406)

    def test_window_older_than_the_account_counts_as_reached(self) -> None:
        """계좌가 구간 안에서 시작했으면(첫 줄이 첫 입금 · 그 전 잔고 0) 모자란 게 아니다."""
        got = wallet_series(BOOK, MOVES, T0 - timedelta(days=10), T0 + timedelta(hours=40))
        assert got is not None
        assert got.reached is True and got.points[0].balance == Decimal(0)

    def test_no_balance_is_none_not_zero(self) -> None:
        book = [{"type": "pnl", "change": "1", "time": str(T0.timestamp())}]
        assert wallet_series(book, [], T0, T0 + timedelta(hours=1)) is None
        assert wallet_payload(None) is None

    def test_payload_strings(self) -> None:
        got = wallet_payload(
            wallet_series(BOOK, MOVES, T0 + timedelta(hours=5), T0 + timedelta(hours=40), points=2)
        )
        assert got is not None
        assert got["earned"] == "4" and got["principal"] == "366" and got["balance"] == "370"
        assert len(got["points"]) == 3 and set(got["points"][0]) == {"at", "balance", "principal"}
