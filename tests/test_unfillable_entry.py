"""계약을 **못 사는** 진입은 원장을 거둔다 (T286 · 2026-09-19 실계좌 실측).

## 무엇이 문제였나

계약은 정수이고 계약 하나의 명목이 종목마다 다르다 — Gate 실측(2026-09-19):

    DOGE  0.87 USDT      SOL  111.55 USDT      (128배)

실계좌 373 USDT · 자리 6 이면 자리 예산이 **62 USDT** 다. 낙폭 브레이크가 크기를 절반으로
줄이고 기울기까지 0.5 면 유효 1배 = 62 USDT 명목인데, **SOL 계약 하나가 112 USDT** 라 0개다.

그대로 두면 `contracts_for` 가 예외를 내는데 그때는 **원장에 "보유중" 이 써진 뒤**다
(`Session._enter` 가 `ledger.add` 를 먼저 한다). 주문은 안 나갔으므로 원장에는 있고 거래소에는
없는 상태가 남는다 — 돈은 안 잃지만 **원장이 거짓말**을 하고 자가 점검이 고아로 올린다.

## 왜 크기를 키워 맞추지 않나

156차에서 정수 계약의 대가를 쟀다: 4.47년 복리에서 **-0.4%** 다(자본이 커지면 계약 수가 늘어
저절로 사라진다 · SOL 96.9% → 99.8%). 0.4% 를 쫓아 자리 예산을 넘기면 총 명목 상한 회계가
어긋난다. **그 거래를 안 하는 것**이 옳고, 자리는 다음 신호에 다시 쓴다.
"""

from __future__ import annotations

from decimal import Decimal

from updown.orchestration.walkforward.order_mapping import (
    OrderMappingError,
    can_size,
    contracts_for,
)

# Gate 실측 2026-09-19 — 계약 승수와 그때 가격.
SOL_MULT, SOL_PX = Decimal("1"), Decimal("111.55")
DOGE_MULT, DOGE_PX = Decimal("10"), Decimal("0.08686")
SLOT = Decimal("62.1667")  # 실계좌 373 USDT ÷ 자리 6


class TestCanSizeAnswersWithoutThrowing:
    """`contracts_for` 는 옳게 던지지만, **원장을 쓰기 전에** 물을 자리가 필요했다."""

    def test_sol_cannot_be_bought_at_one_x(self) -> None:
        # 유효 1배 = 62 USDT 명목 · SOL 계약 하나 111.55 → 0개.
        assert can_size(SLOT, Decimal(1), SOL_PX, SOL_MULT) is False

    def test_sol_can_be_bought_at_two_x(self) -> None:
        # 브레이크만 걸린 경우(4x x 기울기 1.0 x 0.5) = 124 USDT → 1개.
        assert can_size(SLOT, Decimal(2), SOL_PX, SOL_MULT) is True

    def test_doge_is_fine_even_at_the_floor(self) -> None:
        # 계약이 0.87 USDT 라 62 USDT 로 71개 산다.
        assert can_size(SLOT, Decimal(1), DOGE_PX, DOGE_MULT) is True

    def test_zero_size_min_still_needs_one_contract(self) -> None:
        """🔴 Gate 가 `order_size_min` 을 **0 으로 주는 종목이 있다** (실측: SOL).

        `n >= size_min` 으로 보면 **0계약이 통과한다** — 이 프로젝트의 프로브가 처음에 그렇게
        틀렸다. 0계약은 주문이 아니다.
        """
        assert can_size(SLOT, Decimal(1), SOL_PX, SOL_MULT, size_min=0) is False

    def test_it_agrees_with_contracts_for(self) -> None:
        """둘이 갈라지면 가드가 있으나 마나다 — 같은 자를 쓰는지 본다."""
        for lever in (Decimal(1), Decimal(2), Decimal(4), Decimal(6)):
            for px, mult in ((SOL_PX, SOL_MULT), (DOGE_PX, DOGE_MULT)):
                ok = can_size(SLOT, lever, px, mult)
                try:
                    got = contracts_for(SLOT, lever, px, mult, size_min=1)
                except OrderMappingError:
                    got = 0
                assert ok == (got >= 1), f"배율 {lever} · 계약 {px * mult}: 가드 {ok} vs 실제 {got}"

    def test_garbage_input_is_false_not_an_exception(self) -> None:
        # 묻는 함수라 던지지 않는다 — 호출자는 "못 산다" 로 읽으면 된다.
        assert can_size(Decimal(0), Decimal(4), SOL_PX, SOL_MULT) is False
        assert can_size(SLOT, Decimal(4), Decimal(0), SOL_MULT) is False
        assert can_size(SLOT, Decimal(4), SOL_PX, Decimal(0)) is False


class TestRoundToNearestRescuesSmallAccounts:
    """🔴 **반올림** — 157차 실측으로 켠 것 (사용자 승인 2026-09-19).

    자리 예산이 작으면 비싼 계약을 하나도 못 사서 신호를 통째로 놓친다. 실계좌 373 USDT 기준:

        내림    배율 6.2% 손실 · 신호의 2.8% 를 건너뜀
        반올림  배율 0.1% 손실 · 건너뛰는 신호 0%

    자본 1,000 이상에서는 둘 다 1% 안쪽이라 차이가 없다(156차) — 작은 자본에서만 값이 있다.
    """

    ENVELOPE = Decimal(4) * Decimal("1.5")  # 선언 배율 4x x 크기 승수 천장 1.5

    def test_sol_at_one_x_is_rescued(self) -> None:
        # 62 USDT 로 111.55 짜리 계약 → 0.56개. 내림은 0(못 산다), 반올림은 1개.
        assert can_size(SLOT, Decimal(1), SOL_PX, SOL_MULT) is False
        assert (
            can_size(
                SLOT,
                Decimal(1),
                SOL_PX,
                SOL_MULT,
                round_to_nearest=True,
                max_leverage=self.ENVELOPE,
            )
            is True
        )

    def test_below_half_a_contract_is_still_refused(self) -> None:
        """0.5계약 **미만**은 안 올린다 — 반올림이지 올림이 아니다."""
        tiny = SLOT / 4  # 0.14개어치
        assert (
            can_size(
                tiny,
                Decimal(1),
                SOL_PX,
                SOL_MULT,
                round_to_nearest=True,
                max_leverage=self.ENVELOPE,
            )
            is False
        )

    def test_the_envelope_blocks_the_dangerous_lift(self) -> None:
        """🔴 **크게 사려던 자리에서는 안 올린다** — 청산선이 가까워지는 경우다.

        선언 봉투는 4x x 1.5 = 6배다. 올림했을 때 그 위로 가면 내림으로 남는다.
        """
        # 계약 하나가 자리 예산의 1.79배(SOL) — 6배를 의도하면 3.34개 → 3개(내림).
        # 만약 3.6개였다면 올림이 4개 = 7.16배라 봉투를 넘는다. 그 경우를 직접 만든다.
        slot = SOL_PX * Decimal("1.5") / Decimal("1.5")  # 계약 하나 = 자리 예산
        # 5.6개어치를 의도 → 올리면 6개 = 6배(봉투 안) · 5.7 이면 6개여도 6배로 같다.
        got_capped = contracts_for(
            slot,
            Decimal("5.6"),
            SOL_PX,
            SOL_MULT,
            size_min=1,
            round_to_nearest=True,
            max_leverage=Decimal(6),
        )
        assert got_capped == 6, "6배 봉투 안이면 올린다"
        # 6.6개어치를 의도 → 올리면 7배라 봉투를 넘는다 → 내림(6개).
        got_blocked = contracts_for(
            slot,
            Decimal("6.6"),
            SOL_PX,
            SOL_MULT,
            size_min=1,
            round_to_nearest=True,
            max_leverage=Decimal(6),
        )
        assert got_blocked == 6, "봉투를 넘는 올림은 막는다 — 청산선이 가까워진다"

    def test_guard_and_sizer_never_disagree(self) -> None:
        """가드와 실제 계산이 **같은 인자로 같은 답**을 내야 한다 (둘이 갈리면 가드가 무의미)."""
        for lever in (Decimal("0.5"), Decimal(1), Decimal(2), Decimal(4), Decimal(6)):
            for px, mult in ((SOL_PX, SOL_MULT), (DOGE_PX, DOGE_MULT)):
                ok = can_size(
                    SLOT, lever, px, mult, round_to_nearest=True, max_leverage=self.ENVELOPE
                )
                try:
                    got = contracts_for(
                        SLOT,
                        lever,
                        px,
                        mult,
                        size_min=1,
                        round_to_nearest=True,
                        max_leverage=self.ENVELOPE,
                    )
                except OrderMappingError:
                    got = 0
                assert ok == (got >= 1), f"배율 {lever} · 계약 {px * mult}"

    def test_floor_stays_the_default(self) -> None:
        """⛔ 진입 경로에만 켠다 — 재레버·청산은 재본 적이 없어 기본은 내림 그대로다."""
        assert contracts_for(SLOT, Decimal(1), SOL_PX, SOL_MULT, size_min=0) == 0


class TestTheLiveAccountShape:
    """지금 실계좌(373 USDT · 자리 6 · 4x)에서 어느 칸이 막히나 — 숫자를 못 박는다."""

    def test_only_sol_is_at_risk_today(self) -> None:
        # 브레이크(x0.5) + 기울기 0.5 = 유효 1배. 여섯 종목 중 SOL 만 못 산다.
        per_contract = {  # 2026-09-19 실측 · 계약 하나의 명목
            "BTC": Decimal("8.12"),
            "ETH": Decimal("26.33"),
            "XRP": Decimal("14.11"),
            "SOL": Decimal("111.55"),
            "DOGE": Decimal("0.87"),
            "ADA": Decimal("2.23"),
        }
        blocked = [
            sym
            for sym, per in per_contract.items()
            if not can_size(SLOT, Decimal(1), per, Decimal(1))
        ]
        assert blocked == ["SOL"], f"막히는 종목이 달라졌다: {blocked}"

    def test_a_bigger_slot_unblocks_sol(self) -> None:
        """자본이 커지면 저절로 풀린다 — 이것이 156차가 "고치지 말라" 고 한 이유다."""
        assert can_size(Decimal(200), Decimal(1), SOL_PX, SOL_MULT) is True
