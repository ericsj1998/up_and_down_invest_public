"""콘솔이 **모든 종목**을 보는가 (사용자 신고 2026-08-20).

Note:
    🔴 콘솔은 `exchange()` 를 **기본값 BTC_USDT** 로만 부른다. 판 6개가 여섯 종목에서
    도는 동안 화면은 BTC 만 보고 *"미결 0건 · 조건부 0건"* 이라고 했다.

    ⛔ **조건부 0건은 "손절이 없다"로 읽힌다.** 실제로는 SPCX 에 1408 계약과 발동가
    139.31 이 멀쩡히 걸려 있었다 — 화면이 낼 수 있는 가장 나쁜 거짓말이다 (규칙 #8).

    ⚠️ 08-19 에 **체결 이력**에서 같은 병을 이미 고쳤는데(사고 ⑨) 나머지 셋에는 안
    들어갔다. 그래서 여기서 셋을 다 잠근다.
"""

from __future__ import annotations

from typing import Any

from updown.apps.api import exchange as api


class Adapter:
    """종목마다 다른 것을 들고 있는 거래소 대역."""

    def __init__(self, *, blows: tuple[str, ...] = ()) -> None:
        self.blows = blows
        self.asked: list[str] = []

    def _symbol(self, instrument: object) -> str:
        return str(getattr(instrument, "symbol", ""))

    async def open_orders(self, instrument: object) -> list[dict[str, str]]:
        symbol = self._symbol(instrument)
        self.asked.append(symbol)
        if symbol in self.blows:
            raise RuntimeError('400 {"label":"CONTRACT_NOT_FOUND"}')
        return [{"id": f"o-{symbol}"}] if symbol == "SPCX_USDT" else []

    async def open_stops(self, instrument: object) -> list[dict[str, str]]:
        symbol = self._symbol(instrument)
        if symbol in self.blows:
            raise RuntimeError('400 {"label":"CONTRACT_NOT_FOUND"}')
        return [{"id": "s-1", "trigger_price": "139.31"}] if symbol == "SPCX_USDT" else []

    async def position_snapshot(self, instrument: object) -> dict[str, str]:
        symbol = self._symbol(instrument)
        if symbol in self.blows:
            raise RuntimeError('400 {"label":"CONTRACT_NOT_FOUND"}')
        return {"size": "1408", "entry_price": "139.84"} if symbol == "SPCX_USDT" else {}


SYMBOLS = (
    "BTC_USDT",
    "ETH_USDT",
    "SOL_USDT",
    "XRP_USDT",
    "DOGE_USDT",
    "TSLAX_USDT",
    "SPCX_USDT",
    "SNDK_USDT",
    "SKHY_USDT",
)
"""시험용 종목들 — 옛 `TRACKED` 와 같은 아홉 개 (순회 계약을 재는 데 필요한 것은 "여럿" 뿐이다)."""


async def live(adapter: Any) -> dict[str, list[dict[str, str]]]:
    """`_all_live` 한 번."""
    # 2026-08-26: tracked 는 이제 파생 인자다 — 테스트는 종목 순회 계약(전 종목 조회 ·
    # 부분 실패 허용)만 검증한다. 2026-09-22: 서버의 하드코딩 튜플(TRACKED)이 사라져
    # 같은 아홉 종목을 여기 시험 자료로 둔다.
    return await api._all_live(adapter, "GATE", SYMBOLS)  # pyright: ignore[reportPrivateUsage]


class TestAllSymbols:
    async def test_a_stop_on_another_contract_is_not_invisible(self) -> None:
        """🔴 이것이 안 보여서 멀쩡한 손절을 "무방비" 로 읽었다."""
        got = await live(Adapter())

        assert [row["symbol"] for row in got["stops"]] == ["SPCX_USDT"]
        assert got["stops"][0]["trigger_price"] == "139.31"

    async def test_every_row_says_which_contract(self) -> None:
        """🔴 종목이 없으면 화면이 남의 종목을 내 것으로 읽는다."""
        got = await live(Adapter())

        for name in ("orders", "stops", "positions"):
            assert all("symbol" in row for row in got[name]), f"{name} 에 종목이 없다"

    async def test_empty_positions_are_left_out(self) -> None:
        """⚠️ 빈 줄이 종목 수만큼 오면 진짜 포지션이 그 안에 묻힌다."""
        got = await live(Adapter())

        assert len(got["positions"]) == 1
        assert got["positions"][0]["size"] == "1408"

    async def test_one_broken_contract_does_not_empty_the_screen(self) -> None:
        """⚠️ 하나 때문에 전체가 비면 정작 급한 것을 못 본다 (규칙 #8-1 과 같은 방향).

        SNDK·SKHY 는 testnet 에 계약이 없어 실제로 400 이 온다.
        """
        got = await live(Adapter(blows=("SNDK_USDT", "SKHY_USDT")))

        assert [row["symbol"] for row in got["positions"]] == ["SPCX_USDT"]
        assert len(got["stops"]) == 1

    async def test_it_asks_every_tracked_symbol(self) -> None:
        """⛔ 받은 종목을 **하나도 빼지 않고** 묻는다 — 빠진 종목의 포지션은 안 보인다."""
        adapter = Adapter()
        await live(adapter)

        assert set(adapter.asked) == set(SYMBOLS)
