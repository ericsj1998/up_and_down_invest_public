"""금고 한도를 **금액 또는 잔액 비율**로 (사용자 요구 2026-08-20).

Note:
    🔴 **비율은 판이 뜨는 순간에 한 번 풀린다.** 걸음마다 지금 잔액으로 다시 풀면
    한도가 계속 움직이고, 원장은 걸음마다 처음부터 다시 걸어가므로(`_walk_wallet`)
    **과거 충전까지 새 기준으로 다시 세어진다** — 같은 입력에 다른 출력이다 (규칙 #5).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from updown.apps.api.walkforward import cap_text, resolve_cap

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from httpx import Response


def save(client: TestClient, value: str) -> Response:
    """한도를 저장한다 — **타입 없는 경계가 여기서 끝난다**.

    Args:
        client: 테스트 클라이언트.
        value: 사람이 적은 값.

    Returns:
        응답.

    Note:
        🔴 `starlette` 의 `TestClient` 는 반환 타입이 없다. 호출부마다 그대로 쓰면
        `Unknown` 이 번져 `status_code` 조차 미상이 되고, CI 의 pyright 가 터진다
        (`test_api_admin.fetch` 와 같은 이유). 호출부마다 무시 주석을 다는 것은
        **경고를 끄는 것**이고 이쪽은 **경계를 좁히는 것**이다.
    """
    got = client.put(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        "/walkforward/vault", json={"refill_cap": value}
    )
    return cast("Response", got)


def read(client: TestClient) -> Response:
    """지금 걸린 한도를 읽는다.

    Args:
        client: 테스트 클라이언트.

    Returns:
        응답.
    """
    got = client.get("/walkforward/vault")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return cast("Response", got)


class TestCapText:
    """사람이 적은 것을 저장할 모양으로 다듬는다."""

    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("300", "300"),
            ("300.5", "300.5"),
            ("30%", "30%"),
            ("30 %", "30%"),
            ("  30%  ", "30%"),
            ("0.5%", "0.5%"),
        ],
    )
    def test_it_keeps_both_units(self, raw: str, want: str) -> None:
        assert cap_text(raw) == want

    @pytest.mark.parametrize("raw", ["", "   ", None, "0", "0%", "-5", "-5%", "abc", "%"])
    def test_nothing_means_no_limit(self, raw: object) -> None:
        """⚠️ **0 을 "0원 한도" 로 읽지 않는다** — 그러면 판이 즉시 멈춘다.

        칸을 비우면 빈 문자열이 오고, 그것은 *"안 걸었다"* 는 뜻이다.
        """
        assert cap_text(raw) == ""

    def test_a_rate_never_exceeds_the_whole_wallet(self) -> None:
        """⚠️ 잔액보다 많이 꺼낼 수는 없다 — 넘겨 적으면 "제한 없음" 과 같은 뜻이 된다."""
        assert cap_text("250%") == "100%"


class TestResolveCap:
    """저장된 값을 금액으로 푼다."""

    def test_an_amount_ignores_the_wallet(self) -> None:
        assert resolve_cap("300", Decimal(9999)) == Decimal(300)
        assert resolve_cap("300", None) == Decimal(300)

    def test_a_rate_reads_the_wallet(self) -> None:
        """⭐ 사용자 정의: *"퍼센트로 하면 잔액의 퍼센트로 측정된다"*."""
        assert resolve_cap("30%", Decimal(1000)) == Decimal(300)

    def test_no_limit_stays_no_limit(self) -> None:
        assert resolve_cap("", Decimal(1000)) is None

    @pytest.mark.parametrize("wallet", [None, Decimal(0), Decimal(-5)])
    def test_a_rate_without_a_wallet_cannot_be_resolved(self, wallet: Decimal | None) -> None:
        """🔴 잔액을 모르면 **0 으로 만들지 않는다** — 판이 즉시 멈춘다.

        ⚠️ None 은 여기서 *"못 풀었다"* 이고, 부르는 쪽(판 시작)은 잔액을 늘 안다.
        """
        assert resolve_cap("30%", wallet) is None

    def test_the_rate_is_frozen_at_start_not_recomputed(self) -> None:
        """🔴 같은 글이 잔액에 따라 다른 금액이 된다 — 그래서 **한 번만** 푼다.

        판이 뜬 뒤 잔액이 두 배가 돼도 그 판의 한도는 안 바뀐다. 안 그러면 원장이
        과거 충전까지 새 기준으로 다시 세어 결정론이 깨진다 (규칙 #5).
        """
        assert resolve_cap("30%", Decimal(1000)) == Decimal(300)
        assert resolve_cap("30%", Decimal(2000)) == Decimal(600)


class TestEndpoint:
    """화면이 실제로 부르는 경로 — 저장하고 되읽는다."""

    def test_the_api_round_trips_both_units(self) -> None:
        """🔴 다듬기 전 값을 되비추면 사람은 `30 %` 를 저장했다고 믿는다.

        ⚠️ 저장소가 없으면 `PUT` 은 503 이고 `GET` 은 *"제한 없음"* 이다 — 그 상태를
        조용히 넘기지 않는다 (규칙 #8).
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from updown.apps.api import walkforward as api

        kept: dict[str, str] = {}

        class Fake:
            async def get(self, key: str) -> str:
                return kept.get(key, "")

            async def put(self, key: str, value: str, *, by: str = "") -> None:
                kept[key] = value
                del by  # T266-4: the real store writes this into the event log

        # ⚠️ 라우터가 이미 `/walkforward` 접두를 든다 — 여기서 또 붙이면 두 겹이 된다.
        app = FastAPI()
        app.include_router(api.router)
        before = api._settings  # pyright: ignore[reportPrivateUsage]
        api._settings = Fake()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
        try:
            with TestClient(app) as client:
                for typed, want, unit in [
                    ("30 %", "30%", "percent"),
                    ("3000", "3000", "amount"),
                    ("", "", "amount"),
                ]:
                    put = save(client, typed)
                    assert put.status_code == 200, put.text
                    assert put.json() == {"refill_cap": want, "unit": unit}
                    assert read(client).json() == {"refill_cap": want, "unit": unit}
        finally:
            api._settings = before  # pyright: ignore[reportPrivateUsage]


class TestRoundTrip:
    """화면 → 저장 → 판 시작이 한 줄로 이어지는가."""

    @pytest.mark.parametrize(
        ("typed", "wallet", "want"),
        [
            ("30%", Decimal("811.76"), Decimal("243.528")),
            ("3000", Decimal("811.76"), Decimal(3000)),
            ("", Decimal("811.76"), None),
        ],
    )
    def test_what_the_user_types_is_what_the_run_gets(
        self, typed: str, wallet: Decimal, want: Decimal | None
    ) -> None:
        assert resolve_cap(cap_text(typed), wallet) == want
