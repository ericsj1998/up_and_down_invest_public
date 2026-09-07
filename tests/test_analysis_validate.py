"""사람이 고친 계획이 **RiskManager 를 지난다** — `/analysis/validate`.

## 🔴 왜 이 문이 있나

사용자 요구 2026-08-30: 차트에서 선을 끌어 손절·익절을 정하고 그대로 주문을 낸다.

그런데 절대 규칙 #4 는 *"손절/익절의 SSoT 는 RiskManager. 분석은 제안만, 집행은 값을
못 바꾼다"* 이다. 사람이 끈 선이 그대로 주문이 되면 그 규칙이 깨진다.

⇒ 끈 선은 `LlmProposal` 과 **같은 범주**로 둔다 (규칙 #2 v2.5 단서의 사상) —
  표시·기록되고, **확정은 RiskManager 가 한다.**

## 이 시험이 지키는 것

    ① β 없는 고배율을 **거절**한다     T144: 6x β0 은 청산이 난다
    ② 손절을 청산 안쪽으로 **당긴다**   T120: 청산난 판의 81~84%가 "손절이 청산 밖"
    ③ 당겼으면 **말한다**              사람 값이 그대로 나갈 것처럼 보이면 거짓말이다

⚠️ 특히 ③ — RiskManager 가 값을 바꿨는데 화면이 사람이 낸 값을 계속 보여 주면,
사람은 자기가 정한 손절이 걸린 줄 안다. 그 오해는 청산이 나야 드러난다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from updown.apps.api.analysis import validate


async def _ask(**payload: Any) -> dict[str, Any]:
    """엔드포인트를 직접 부른다 — 문(guard)은 `test_auth_guard` 가 본다."""
    base: dict[str, Any] = {
        "entry": "100",
        "stop": "97",
        "first": "112",
        "target": "120",
        "leverage": "3",
        "market": "BINANCE",
    }
    base.update(payload)
    return await validate(base)


class TestTheRiskManagerConfirms:
    @pytest.mark.asyncio
    async def test_a_sane_plan_passes(self) -> None:
        got = await _ask()
        assert got["ok"] is True
        assert got["reasons"] == []

    @pytest.mark.asyncio
    async def test_it_reports_what_it_confirmed(self) -> None:
        """⚠️ 확정 손절을 **돌려준다** — 화면이 그것을 보여 줘야 한다."""
        got = await _ask()
        assert Decimal(got["stop"]) > 0
        assert "stop_pct" in got
        assert "liq_pct" in got

    @pytest.mark.asyncio
    async def test_a_stop_beyond_liquidation_is_pulled_in(self) -> None:
        """🔴 T120: 청산난 판의 **81~84%** 가 "손절이 청산보다 바깥" 인 판이었다.

        20배에서 청산은 5% 인데 손절을 50% 밖에 두면, 그 손절은 영원히 안 걸린다.
        """
        got = await _ask(leverage="20", stop="50")
        assert got["moved"] is True
        # 당겨진 손절은 원래 값보다 진입에 가깝다.
        assert Decimal(got["stop"]) > Decimal(50)
        assert any("당겼다" in item for item in got["reasons"])

    @pytest.mark.asyncio
    async def test_pulling_the_stop_does_not_fail_the_plan(self) -> None:
        """⚠️ 당기는 것은 **고쳐 주는 것**이지 거절이 아니다 — 그것까지 막으면 아무것도 못 낸다."""
        got = await _ask(leverage="20", stop="50")
        assert got["ok"] is True

    @pytest.mark.asyncio
    async def test_a_thin_stop_is_named_but_not_refused(self) -> None:
        """🔴 하한 0.5% — T173 에서 BTC 1h 계획의 83%가 이 아래였다.

        ⚠️ **말은 하되 막지는 않는다** (2026-08-30 에 바꿨다). 처음에는 막았는데,
        차트 주문의 주체는 사람이고 좁은 손절이 **의도**일 수 있다 (스캘핑).

        ⭐ 그리고 막을 필요가 없다 — 비용 산수가 알아서 잡는다. 손절이 좁으면 비용이
        R 을 통째로 먹어 필요 승률이 치솟고, 100% 를 넘는 순간 아래 시험이 막는다.
        **딱딱한 문은 산수이고, 무른 문은 판단이다.**
        """
        got = await _ask(stop="99.9")
        assert got["ok"] is True
        assert any("하한" in item for item in got["reasons"])

    @pytest.mark.asyncio
    async def test_an_impossible_win_rate_is_named(self) -> None:
        """⛔ 필요 승률 100% 초과 = 산술적으로 이길 수 없다 (5m 이 그랬다)."""
        got = await _ask(stop="90", first="100.0001")
        assert got["ok"] is False
        assert any("필요 승률" in item for item in got["reasons"])

    @pytest.mark.asyncio
    async def test_a_thin_stop_with_a_thin_target_is_refused_by_arithmetic(self) -> None:
        """🔴 **좁은 손절을 실제로 막는 것은 산수다** (2026-08-30).

        손절 0.2% 에 왕복 비용 0.157% 면 비용이 **R 의 0.785 배**다. 그 상태에서 1차
        익절까지 좁으면 필요 승률이 100% 를 넘고, 그때는 판단이 아니라 사실이라 막는다.

        ⚠️ 이것이 5m 을 폐기한 계산과 **같은 모양**이다 — 그때도 RR 은 그럴듯했고
        (2.47xATR) 손절이 좁아서 죽었다.
        """
        got = await _ask(stop="99.8", first="100.02", target="100.1")
        assert got["ok"] is False
        assert any("산술적으로 이길 수 없다" in item for item in got["reasons"])

    @pytest.mark.asyncio
    async def test_a_stop_above_entry_is_refused(self) -> None:
        got = await _ask(stop="101")
        assert got["ok"] is False


class TestItRefusesBadInput:
    @pytest.mark.asyncio
    async def test_a_missing_field_is_a_400(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await validate({"entry": "100"})

    @pytest.mark.asyncio
    async def test_a_zero_entry_is_a_400(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await _ask(entry="0")

    @pytest.mark.asyncio
    async def test_it_never_places_an_order(self) -> None:
        """⛔ 이 입구는 *"내면 무엇이 되나"* 만 답한다 — 주문 경로가 없어야 한다.

        누군가 여기에 주문을 붙이면 절대 규칙 #4 의 문을 우회하는 길이 생긴다.
        """
        from pathlib import Path

        source = Path("src/updown/apps/api/analysis.py").read_text(encoding="utf-8")
        assert "submit_order" not in source
        assert "OrderGateway" not in source
