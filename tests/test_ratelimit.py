"""요율 한도 — **재고, 기억하고, 아껴 쓴다** (2026-08-29 사고).

## 무슨 일이 있었나

    418 {"code":-1003,"msg":"Way too many requests; IP(...) banned until ..."}

하필 펀드 복구 중이라 BINANCE 펀드가 안 붙었고 판 6개가 예산 없이 돌았다. 그리고
**밴이 2분에서 74분으로 늘어났다** — 밴 중에도 계속 두드렸기 때문이다.

## 우리는 얼마나 쓰는지 몰랐다

실측 분당 200~360건. 그런데 한도는 **건수가 아니라 가중치**이고, 거래소가 헤더로
알려 주는 그 값을 **한 번도 읽지 않았다.**

낭비가 어디였나 (5분 · BINANCE):

    523  klines
    357  positionRisk   ← 판 6개가 **같은 계좌**를 각자 물었다
    266  account        ← 같음
    130  exchangeInfo   ← 거의 안 바뀌는 값을 걸음마다 물었다
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from updown.marketdata import ratelimit, shared_read, speccache


@pytest.fixture(autouse=True)
def _clean() -> Any:
    speccache.forget()
    shared_read.forget()
    yield
    speccache.forget()
    shared_read.forget()


# fixture 는 pytest 가 이름으로 부른다.
# pyright: reportUnusedFunction=false


class TestTheMeter:
    def test_it_reads_the_binance_weight_header(self) -> None:
        ratelimit.observe("BN_TEST", {"x-mbx-used-weight-1m": "1700"}, limit=2400)
        got = ratelimit.meter("BN_TEST")
        assert got.used == 1700
        assert got.limit == 2400
        assert got.share == pytest.approx(1700 / 2400)

    def test_gate_reports_what_is_left_so_we_flip_it(self) -> None:
        """⚠️ Gate 는 **남은 것**을 준다 — 그대로 쓰면 많이 쓸수록 여유로워 보인다."""
        ratelimit.observe(
            "GT_TEST", {"x-gate-ratelimit-remain": "20", "x-gate-ratelimit-limit": "100"}
        )
        assert ratelimit.meter("GT_TEST").used == 80

    def test_missing_headers_do_not_explode(self) -> None:
        """⛔ 계측이 조회를 막으면 안 된다 (규칙 #8-1) — 헤더가 없어도 조용히 넘긴다."""
        ratelimit.observe("NONE_TEST", {})
        ratelimit.observe("NONE_TEST", None)
        assert ratelimit.meter("NONE_TEST").used == 0

    def test_a_ban_records_when_it_ends(self) -> None:
        """🔴 밴 중에 계속 부르면 연장된다 — 언제까지인지 알아야 쉴 수 있다."""
        soon = int((time.time() + 300) * 1000)
        until = ratelimit.note_ban(
            "BAN_TEST", f'{{"code":-1003,"msg":"... banned until {soon}. Please ..."}}'
        )
        assert until == pytest.approx(soon / 1000)
        assert ratelimit.meter("BAN_TEST").banned is True

    def test_milliseconds_are_not_read_as_seconds(self) -> None:
        """⚠️ 그대로 쓰면 만료가 서기 58000년이 되어 **영원히 밴**으로 읽힌다."""
        soon = int((time.time() + 60) * 1000)
        until = ratelimit.note_ban("MS_TEST", f"banned until {soon}")
        assert until < time.time() + 3600

    def test_a_body_without_a_ban_says_nothing(self) -> None:
        assert ratelimit.note_ban("QUIET_TEST", "그냥 오류") == 0.0
        assert ratelimit.meter("QUIET_TEST").banned is False


class TestTheSpecCache:
    @pytest.mark.asyncio
    async def test_the_second_ask_does_not_hit_the_exchange(self) -> None:
        calls: list[int] = []

        async def fetch() -> dict[str, Any]:
            calls.append(1)
            return {"order_price_round": "0.1"}

        for _ in range(5):
            await speccache.spec("BINANCE", "BTCUSDT", fetch)
        assert calls == [1], "5분에 130회 부르던 것이 한 번이어야 한다"

    @pytest.mark.asyncio
    async def test_venues_do_not_share_a_symbol(self) -> None:
        """🔴 같은 이름이 거래소마다 **다른 명세**다 — 섞이면 호가 단위가 틀린다."""

        async def gate() -> dict[str, Any]:
            return {"who": "GATE"}

        async def binance() -> dict[str, Any]:
            return {"who": "BINANCE"}

        assert (await speccache.spec("GATE", "BTC_USDT", gate))["who"] == "GATE"
        assert (await speccache.spec("BINANCE", "BTC_USDT", binance))["who"] == "BINANCE"

    @pytest.mark.asyncio
    async def test_it_expires(self) -> None:
        """⛔ 영원히 들고 있지 않는다 — 상장 규칙이 바뀌면 옛 호가로 전부 거절된다."""
        calls: list[int] = []

        async def fetch() -> dict[str, Any]:
            calls.append(1)
            return {}

        await speccache.spec("BINANCE", "X", fetch, ttl=10, now=1000.0)
        await speccache.spec("BINANCE", "X", fetch, ttl=10, now=1005.0)
        await speccache.spec("BINANCE", "X", fetch, ttl=10, now=1011.0)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_a_failure_is_not_remembered(self) -> None:
        """🔴 실패를 기억하면 요율 제한 **한 번이 한 시간짜리 공백**이 된다."""
        tries: list[int] = []

        async def flaky() -> dict[str, Any]:
            tries.append(1)
            if len(tries) == 1:
                raise RuntimeError("418")
            return {"ok": "1"}

        with pytest.raises(RuntimeError):
            await speccache.spec("BINANCE", "Y", flaky)
        assert (await speccache.spec("BINANCE", "Y", flaky))["ok"] == "1"


class TestSharedAccountReads:
    @pytest.mark.asyncio
    async def test_six_runs_asking_at_once_becomes_one_call(self) -> None:
        calls: list[int] = []

        async def fetch() -> dict[str, Any]:
            calls.append(1)
            return {"available": "1000"}

        for _ in range(6):
            await shared_read.shared("BINANCE:account", fetch)
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_the_window_is_short(self) -> None:
        """🔴 손절 감시가 이 값을 읽는다 — 오래 들고 있으면 청산된 계좌를 멀쩡하다고 본다."""
        assert shared_read.TTL_S <= 3

    @pytest.mark.asyncio
    async def test_the_next_step_reads_again(self) -> None:
        calls: list[int] = []

        async def fetch() -> dict[str, Any]:
            calls.append(1)
            return {}

        await shared_read.shared("K", fetch, ttl=2, now=1000.0)
        await shared_read.shared("K", fetch, ttl=2, now=1001.0)
        await shared_read.shared("K", fetch, ttl=2, now=1003.0)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_placing_an_order_drops_the_shared_value(self) -> None:
        """🔴 잔고가 방금 바뀌었는데 옛 값을 다음 판이 받으면 **없는 돈으로** 수량을 잰다."""
        calls: list[int] = []

        async def fetch() -> dict[str, Any]:
            calls.append(1)
            return {}

        await shared_read.shared("binance-testnet:account", fetch)
        shared_read.forget("binance-testnet:")
        await shared_read.shared("binance-testnet:account", fetch)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_forgetting_one_venue_keeps_the_other(self) -> None:
        async def one() -> str:
            return "a"

        await shared_read.shared("gate-testnet:account", one)
        await shared_read.shared("binance-testnet:account", one)
        shared_read.forget("gate-testnet:")
        assert shared_read.size() == 1

    @pytest.mark.asyncio
    async def test_a_failure_is_not_remembered(self) -> None:
        """⛔ 실패를 캐시하면 그 사이 손절 감시가 계좌를 못 읽는다."""
        tries: list[int] = []

        async def flaky() -> str:
            tries.append(1)
            if len(tries) == 1:
                raise RuntimeError("418")
            return "ok"

        with pytest.raises(RuntimeError):
            await shared_read.shared("Z", flaky)
        assert await shared_read.shared("Z", flaky) == "ok"


class TestTheBanIsDiagnosticOnly:
    """🔴 **밴 만료로 요청을 막지 않는다** — 한 번 해 봤고 판 6개가 죽었다 (2026-08-29).

    막아 본 결과:

        17:17  418 — banned until 18:34 로 기억
        17:44  계좌 조회가 **실제로는 성공**했다 (`funds_restored: 2`)
        17:57  차단을 넣은 뒤 — 그 요청을 아예 안 보내서 러너가 못 붙었다
               "판이 열려 있는데 러너가 없다" x 6

    ⇒ 거래소가 말한 만료는 **상한이지 약속이 아니다.** 그것을 사실로 믿고 막으면
      거래소가 답할 준비가 된 뒤에도 우리가 안 물어본다. 그 대가는 **손절을 관리할
      러너가 없는 것**이고, 밴이 좀 길어지는 것보다 훨씬 나쁘다 (규칙 #8-1 의 정신).

    ⇒ 속도를 늦추려면 막는 것이 아니라 **덜 부르는 쪽**을 더 한다 (기억통·공유·좁은 창).
    """

    def test_it_reports_time_left_for_diagnosis(self) -> None:
        ratelimit.note_ban("LEFT_TEST", f"banned until {int((time.time() + 120) * 1000)}")
        left = ratelimit.ban_left("LEFT_TEST")
        assert 100 < left <= 120

    def test_no_ban_means_zero(self) -> None:
        assert ratelimit.ban_left("NEVER_BANNED_TEST") == 0.0

    def test_an_expired_ban_means_zero(self) -> None:
        ratelimit.note_ban("OLD_TEST", f"banned until {int((time.time() - 5) * 1000)}")
        assert ratelimit.ban_left("OLD_TEST") == 0.0
        assert ratelimit.meter("OLD_TEST").banned is False

    def test_no_client_refuses_to_send_on_a_ban(self) -> None:
        """⛔ 두 클라이언트 어디에도 **보내기 전 차단**이 없어야 한다.

        되돌린 것을 누군가 다시 넣으면 같은 사고가 난다 — 그때는 조용하다.
        """
        from pathlib import Path

        for name in (
            "src/updown/marketdata/binance/trade_client.py",
            "src/updown/marketdata/gate/trade_client.py",
        ):
            source = Path(name).read_text(encoding="utf-8")
            assert "ban_left" not in source, f"{name} 이 밴으로 요청을 막고 있다"
