"""펀드 복구가 **한 번 실패하면 끝나던** 결함 (2026-08-29 실측).

## 무슨 일이 있었나

`make rebuild` 직후 기동 순간에 거래소 호출이 몰렸고 Binance 가 요율 제한을 걸었다:

    418 {"code":-1003,"msg":"Way too many requests; IP(...) banned until ..."}

하필 **펀드 복구 중**이었다:

    fund_restore_failed: fund466ab5b0.json ... 418 ...
    funds_restored: 1                        ← Gate 것만 살았다

그리고 거기서 끝이었다. BINANCE 펀드는 다음 재기동까지 **없는 것**이 됐고, 그 판 6개는
예산을 못 받은 채 계속 돌았다 — 원장이 저마다 시드(계좌 전액 4,956)를 자기 것으로 여겨:

    판 6개 원장 합 20,324.96 > 계정 총액 4,956.24 (75.6% 초과)   ← wallet_drift

실측 확인 (BN 6판):

    ETH  250.00 / 예산 250.00     ← 붙었다
    BTC  250.00 / 예산 250.00     ← 붙었다
    ADA  4,956.24 / 예산 125.00   🔴 시드가 계좌 전액
    XRP · NEAR · DOGE  같음       🔴

## 🔴 결함은 418 이 아니다

요율 제한·네트워크 끊김은 **정상 범주**다. 결함은 그 일시적 실패를 **영구 반쪽 상태**로
만든 것 — 경고 한 줄 남기고 스스로 다시 붙지 못한 것이다 (밴은 2분 만에 풀렸다).

⇒ 못 붙인 것을 `PENDING_FUNDS` 에 남기고 붙을 때까지 다시 한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from updown.apps.api import rebalancer as mod

# fixture 는 pytest 가 이름으로 부른다 — 코드에서 호출하지 않는 것이 정상이다.
# pyright: reportUnusedFunction=false


@pytest.fixture(autouse=True)
def _clean() -> Any:
    """모듈 전역을 시험마다 비운다 — 안 그러면 앞 시험의 실패가 뒤로 샌다."""
    mod.PENDING_FUNDS.clear()
    yield
    mod.PENDING_FUNDS.clear()


def _plant(root: Path, *names: str) -> None:
    for name in names:
        (root / f"{name}.json").write_text(json.dumps({"fund_id": name}), encoding="utf-8")


class TestAFailedFundIsNotForgotten:
    @pytest.mark.asyncio
    async def test_a_transient_failure_is_remembered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 예전에는 경고 한 줄이 전부였다 — 그 펀드는 다음 재기동까지 사라졌다."""
        _plant(tmp_path, "good", "rate_limited")
        monkeypatch.setattr(mod, "FUNDS_ROOT", tmp_path)

        async def flaky(data: dict[str, Any]) -> None:
            if data["fund_id"] == "rate_limited":
                raise RuntimeError("418 Way too many requests")

        monkeypatch.setattr(mod, "_restore_one", flaky)

        assert await mod.restore_funds() == 1
        assert {"rate_limited.json"} == mod.PENDING_FUNDS

    @pytest.mark.asyncio
    async def test_the_next_try_clears_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """밴은 2분 만에 풀렸다 — 다시 하면 붙는다."""
        _plant(tmp_path, "rate_limited")
        monkeypatch.setattr(mod, "FUNDS_ROOT", tmp_path)
        tries: list[str] = []

        async def flaky(data: dict[str, Any]) -> None:
            tries.append(data["fund_id"])
            if len(tries) == 1:
                raise RuntimeError("418 Way too many requests")

        monkeypatch.setattr(mod, "_restore_one", flaky)

        await mod.restore_funds()
        assert {"rate_limited.json"} == mod.PENDING_FUNDS
        await mod.restore_funds()
        assert set() == mod.PENDING_FUNDS, "붙었으면 목록에서 빠져야 한다"

    @pytest.mark.asyncio
    async def test_one_bad_fund_does_not_stop_the_others(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⚠️ 이건 원래도 됐다 — 고치면서 깨지 않았는지 잠근다."""
        _plant(tmp_path, "a", "b", "c")
        monkeypatch.setattr(mod, "FUNDS_ROOT", tmp_path)

        async def flaky(data: dict[str, Any]) -> None:
            if data["fund_id"] == "b":
                raise RuntimeError("깨짐")

        monkeypatch.setattr(mod, "_restore_one", flaky)
        assert await mod.restore_funds() == 2
        assert {"b.json"} == mod.PENDING_FUNDS


class TestTheRetryLoop:
    @pytest.mark.asyncio
    async def test_it_waits_before_the_first_try(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """🔴 **먼저 잔다.** 요율 제한으로 실패한 것을 즉시 두드리면 밴이 연장된다 —
        고치려는 것이 원인을 더한다.
        """
        order: list[str] = []

        async def sleeping(_seconds: float) -> None:
            order.append("sleep")
            raise _StopError

        async def restoring() -> int:
            order.append("restore")
            return 0

        monkeypatch.setattr(mod.asyncio, "sleep", sleeping)
        monkeypatch.setattr(mod, "restore_funds", restoring)
        mod.PENDING_FUNDS.add("x.json")
        with pytest.raises(_StopError):
            await mod.fund_retry_loop(every=0)
        assert order == ["sleep"]

    @pytest.mark.asyncio
    async def test_nothing_pending_means_no_exchange_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 붙일 것이 없으면 거래소를 안 부른다 — 이 루프가 요율 제한의 원인이 되면 안 된다."""
        called: list[int] = []
        rounds = iter([None, None])

        async def sleeping(_seconds: float) -> None:
            if next(rounds, "done") == "done":
                raise _StopError

        async def restoring() -> int:
            called.append(1)
            return 0

        monkeypatch.setattr(mod.asyncio, "sleep", sleeping)
        monkeypatch.setattr(mod, "restore_funds", restoring)
        with pytest.raises(_StopError):
            await mod.fund_retry_loop(every=0)
        assert called == []

    def test_the_gap_is_not_shorter_than_the_measured_ban(self) -> None:
        """실측 밴이 2분이었다 — 그보다 자주 두드리면 밴을 연장한다."""
        assert mod.RETRY_EVERY >= 120


class _StopError(Exception):
    """시험에서 무한 루프를 끊는 신호."""
