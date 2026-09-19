"""화면이 *"지금 다 닫으면 얼마인가"* 를 낼 수 있는 재료를 갖고 있나 (2026-09-19).

## 왜 필요했나

거래소가 말하는 `계좌 총액` 은 **지갑**이라 미실현이 안 들어 있다. 실계좌에서 총액 298.03 ·
미실현 +10.26 이 따로 떨어져 있었고, *"지금 정리하면 얼마"* 를 사람이 머리로 더하고 있었다.

그래서 더해 봤더니 이번엔 **수수료만큼 덜 들어왔다** (사용자: *"생각보다 살짝 덜 들어왔네?"*).
실측으로 갈랐다 — 예상 307.07 vs 실제 306.90 의 0.17 은 절반씩이었다:

    0.093  프로브와 청산 사이의 가격 이동 (스냅샷의 숙명 · 못 뺀다)
    0.082  **수수료** — 청산 명목 163.7 x 0.0499%

역산한 0.0499% 가 선언 테이커 0.05% 와 한 자리까지 같았다. **수수료는 예측 가능하다** — 그래서
화면이 뺀다. 이 시험은 그 요율이 **측정 비용표에서 오고**, 화면 산식이 실측을 재현하는지를 본다.
"""

from decimal import Decimal

from updown.common.costs import load_cost_table
from updown.common.domain.instrument import Market


class TestTheTakerRateComesFromTheMeasuredTable:
    """요율을 화면에 박지 않는다 — 비용표가 단일 출처다 (§4.3.1)."""

    def test_gate_taker_is_a_fraction_not_a_percent(self) -> None:
        """🔴 **단위를 못 박는다.** 이 값은 분수다(0.0005 = 0.05%).

        이름을 `taker_pct` 로 적었다가 화면이 100 으로 한 번 더 나눠 수수료가 **0 이 된**
        적이 있다(2026-09-19, 이 시험이 잡았다). 그래서 단위 자체를 시험한다 — 누가 언젠가
        비용표를 퍼센트로 바꿔 적으면 화면 수수료가 조용히 100배가 된다.
        """
        taker = load_cost_table().for_market(Market.GATE).taker
        assert taker > 0, "테이커 요율이 없으면 화면이 수수료를 못 뺀다"
        assert taker < Decimal("0.01"), (
            f"{taker} 는 분수가 아니라 퍼센트로 보인다 — 0.05% 는 0.0005 로 적는다"
        )
        # 실계좌 실측(2026-09-19 청산): 수수료 0.081567 ÷ 명목 163.7 = 0.000498.
        assert abs(taker - Decimal("0.0005")) < Decimal("0.0001"), (
            f"선언 {taker} 가 실측 0.000498 과 크게 다르다 — 둘 중 하나가 낡았다"
        )

    def test_every_live_market_can_price_an_exit(self) -> None:
        # 화면은 시장을 가리지 않는다 — 한 시장이라도 요율이 없으면 그 탭에서 칸이 빈다.
        for market in (Market.GATE, Market.BINANCE, Market.UPBIT):
            assert load_cost_table().for_market(market).taker > 0, f"{market} 테이커 없음"


class TestTheCloseoutArithmeticReproducesTheMeasurement:
    """화면 산식이 2026-09-19 실계좌 청산을 재현하나.

    화면 산식:
        청산 명목 = Σ(증거금 x 배율) + 미실현
        수수료    = 청산 명목 x 테이커율   (분수 · 100 으로 나누지 않는다)
        다 닫으면 = 지갑 + 미실현 - 수수료
    """

    # 실측값 — 프로브 시각의 포지션 둘.
    WALLET = Decimal("298.026709")
    POSITIONS = (  # (증거금, 배율, 미실현)
        (Decimal("1.2763177"), Decimal(6), Decimal("0.47984")),
        (Decimal("29.4208761"), Decimal(5), Decimal("8.568")),
    )
    ACTUAL_FEE = Decimal("0.081567")  # 장부의 fee 합 (세 조각: 0.004052+0.007382+0.070133)

    def _closeout(self, taker: Decimal) -> tuple[Decimal, Decimal]:
        notional = sum((m * lev for m, lev, _ in self.POSITIONS), Decimal(0))
        unreal = sum((u for _, _, u in self.POSITIONS), Decimal(0))
        fee = (notional + unreal) * taker  # 분수 — 나누지 않는다
        return self.WALLET + unreal - fee, fee

    def test_the_estimated_fee_matches_what_the_exchange_charged(self) -> None:
        taker = load_cost_table().for_market(Market.GATE).taker
        _total, fee = self._closeout(taker)
        # 1 센트 안쪽이면 화면이 "덜 들어왔다" 를 만들지 않는다.
        assert abs(fee - self.ACTUAL_FEE) < Decimal("0.01"), (
            f"예상 수수료 {fee:.6f} vs 실제 {self.ACTUAL_FEE} — 산식이 실측과 어긋난다"
        )

    def test_the_remaining_gap_is_price_movement_not_a_bug(self) -> None:
        """남는 차이는 **가격 이동**이라 산식으로 못 없앤다 — 그 사실을 못 박는다.

        실제 잔액 306.900402 는 청산 **시점**의 미실현(8.95526)으로 계산된 값이다.
        프로브 시점 미실현(9.04784)으로 낸 예상과는 0.093 차이가 나고, 그것이 전부다.
        """
        taker = load_cost_table().for_market(Market.GATE).taker
        estimate, _fee = self._closeout(taker)
        actual = Decimal("306.900402")
        drift = sum((u for _, _, u in self.POSITIONS), Decimal(0)) - Decimal("8.95526")
        assert abs(estimate - actual - drift) < Decimal("0.02"), (
            f"예상 {estimate:.4f} - 실제 {actual} 가 가격 이동 {drift:.4f} 으로 설명되지 않는다"
        )
