"""거래 비용 모델 (spec §12.7) — 수수료 · 세금 · 슬리피지, 그리고 **손익분기 슬리피지**.

## 왜 `common/` 인가

비용 테이블은 백테스트 · 페이퍼 · RiskManager 가 **같은 값을 봐야** 한다 (spec §12.7
"백테스트와 동일 테이블 공유"). 셋 중 하나에 두면 나머지가 복사본을 갖게 되고, 그 복사본이
갈라지는 순간 "백테스트에서는 되는데 실계좌에서는 안 되는" 전략이 만들어진다.

## 비용의 분해 — bid-ask bounce 를 두 번 세지 않는다

```
편도 비용 = 수수료 + (체결가 - mid) = 수수료 + 스프레드/2 + 깊이충격
```

체결 틱으로 "N초 후 가격 이동"을 재면 중앙값이 스프레드와 거의 같게 나온다. 우연이 아니다 —
연속 체결이 매수·매도를 번갈아 때리므로 **어떤 간격으로 재도** `|가격 변화| ≈ 스프레드` 다
(bid-ask bounce). 그것을 폴링 지연 슬리피지로 더하면 스프레드를 두 번 센다.

이 모듈이 그 오류를 구조적으로 막는 방식: **모든 것을 `mid` 기준으로 잰다.** 체결가 사이의
차이는 어디에도 쓰지 않는다. 폴링 지연이 더하는 것은 bounce 가 아니라 **방향성 드리프트**이며,
그것은 별도 항이고 호가창 스냅샷으로는 측정되지 않는다.

## 과거 스프레드는 측정할 수 없다 — 그래서 프레이밍을 뒤집는다

업비트 공개 API 는 **현재 호가만** 준다. 백테스트 구간(2025-08~2026-08)의 실제 스프레드는
어떤 방법으로도 알 수 없다. 즉 "비용 실측"은 가정을 **현재 시장 측정치**로 바꾸는 것이고,
그것을 과거 구간에 적용하는 것 자체가 새 가정이다.

그래서 판정 질문을 뒤집는다:

> 측정된 이행률이 P 라면, 그 전략이 견딜 수 있는 **최대 슬리피지는 얼마인가?**

이 역산(`breakeven_slippage`)에는 슬리피지 가정이 들어가지 않는다. 답은 "가정이 맞았나"가
아니라 **"현재 실측치 대비 여유가 몇 배인가"** 가 되고, 그것은 가정값에 의존하지 않는다.

## ⛔ 비용을 낮춰 전략을 살리지 않는다

이 파일의 수치는 거래소·세제가 정하는 **사실**이다 (§5.6.2 와 같은 정신). 필요 승률이
안 나온다고 슬리피지 가정을 낮추면 그것은 전략 개선이 아니라 분모 조작이다. 값을 바꾸려면
`config/costs.yml` 에 **출처와 측정 시각**을 함께 남겨야 한다.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import cast

import yaml

from updown.common.domain.instrument import Market, Side
from updown.common.domain.market import OrderBook
from updown.common.numeric import fixed_context

DEFAULT_CONFIG_PATH = Path("config/costs.yml")
"""기본 비용 테이블 경로."""

BPS = Decimal(10_000)
"""1 = 10,000bp. 비용은 bp 로 말하는 것이 관례라 환산 상수를 둔다."""


class CostConfigError(ValueError):
    """비용 테이블을 읽을 수 없다.

    Note:
        기본값으로 조용히 넘어가지 않는다 (절대 규칙 #8). 비용이 0 으로 떨어진 백테스트는
        **모든 전략이 성립하는 것처럼** 보이고, 그 오류는 실거래에서만 드러난다.
    """


class CostMeasurementError(ValueError):
    """호가창 스냅샷에서 비용을 산출할 수 없다.

    Note:
        교차 호가(bid ≥ ask)·0 이하 가격이 여기 해당한다. 장시간 샘플링을 죽이지 않도록
        **표본 단위 예외**로 두고, 호출부가 개수를 세어 리포트에 남긴다 — 조용히 버리면
        "측정이 잘 됐다"와 "절반이 버려졌다"가 구분되지 않는다.
    """


class SlippageSource(StrEnum):
    """슬리피지 수치의 출처.

    Attributes:
        MEASURED: 호가창 실측에서 왔다.
        ASSUMED: 근거 있는 가정이다 (D1-6 편도 10bp 등).

    Note:
        **이 필드가 없으면 가정이 실측으로 승격되는 것을 막을 수 없다.** 수치만 남기면
        6개월 뒤에 그것이 어디서 왔는지 아무도 모르고, 그때 "실측했으니 믿을 만하다"는
        말이 근거 없이 나온다.
    """

    MEASURED = "measured"
    ASSUMED = "assumed"


@dataclass(frozen=True, slots=True)
class MarketCosts:
    """시장 하나의 비용 구성 (spec §12.7).

    Attributes:
        market: 대상 시장.
        fee_pct: **편도** 수수료율. 0.0005 면 5bp.
        tax_pct_sell: 매도 시 거래세율. 코인은 0, KRX 는 0.0015 (거래세+농특세).
        slippage_pct_one_way: **편도** 슬리피지율. 스프레드/2 + 깊이충격이다.
        slippage_source: 위 값이 실측인지 가정인지.
        measured_at: 실측 시각 (UTC). 가정이면 None.
        source: 출처 문자열 — 어디서 온 수치인지 사람이 읽을 설명.

    Note:
        수수료를 편도로 두는 이유는 **왕복이 항상 2배가 아니기** 때문이다. 거래세는 매도
        한 번에만 붙고, 해외주식 양도세는 연 단위 정산이라 아예 다른 층이다. 왕복을
        기본 단위로 잡으면 그 비대칭을 표현할 자리가 사라진다.
    """

    market: Market
    fee_pct: Decimal
    tax_pct_sell: Decimal
    slippage_pct_one_way: Decimal
    slippage_source: SlippageSource
    measured_at: datetime | None
    source: str
    taker_fee_pct: Decimal | None = None
    maker_fee_pct: Decimal | None = None
    funding_pct_per_8h: Decimal | None = None
    spec_ticks: Mapping[str, Decimal] = field(default_factory=dict[str, Decimal])
    """**종목별 거래소 최소 호가 단위** (`order_price_round`) — T18 ①.

    🔴 **종목마다 10,000배 다르다.** BTC 0.1 · ETH 0.01 · XRP 0.0001 · DOGE 0.00001.
    시장 하나에 눈금 하나를 두면 그중 어느 종목에서는 반드시 틀린다 — XRP 2.1 에
    0.5 를 쓰면 가격의 **24%** 다.

    ⚠️ 이 값은 눈금 그 자체가 아니라 **눈금의 최소 단위**다. 실제 눈금은
    `tick_for(price, spec)` 이 `TICK_RATIO` 를 얹어 정한다 — 우리가 원하는 것은
    거래소 최소값이 아니라 **노이즈를 접을 만큼 굵은** 값이다.

    ⛔ **선언 안 된 종목으로 라이브를 띄우지 않는다.** 없으면 `DEFAULT_TICK`(500원)으로
    떨어지고, 그 경로가 2026-08-18 의 버그였다.
    """

    liquidity: Mapping[str, Decimal] = field(default_factory=dict[str, Decimal])
    """**들어가기 전에 나올 수 있는지** 보는 문턱 — `max_gap_pct` · `depth_multiple`.

    🔴 **이것이 없어서 SPCX 에 증거금 420 이 묶였다** (2026-08-20). 화면의 모든 숫자가
    멀쩡했다 — 가격·손익·청산가가 전부 **표시가**(지수 기반) 기준이었기 때문이다.
    정작 체결은 호가창에서 나는데, 그쪽은 이랬다:

    ```
    표시가      139.06
    최고 매수   108      ← 팔려면 여기를 때려야 한다
    최저 매도   138      ← 사기는 쉬웠다
    ```

    ⚠️ **한쪽만 두꺼운 시장**이라 들어가긴 쉽고 나오긴 불가능했다. 깊이 **총합**을 재면
    아주 건강해 보인다(매도 33만 계약) — 양쪽을 따로 봐야 한다.

    ⛔ 비어 있으면 검사를 안 한다. 기존 시장 블록의 판정값이 안 움직여야 한다 (§5.6.2).
    """

    price_tick: Decimal | None = None
    """시장 전체에 쓰는 **고정** 눈금 — 종목별 값이 없을 때의 덮어쓰기.

    ⚠️ `spec_ticks` 가 있으면 그쪽이 이긴다. 이 필드는 종목이 하나뿐인 시장이나
    실험용 덮어쓰기를 위해 남긴다.

    🔴 **500 은 원화 상수였다** (2026-08-18 실측으로 잡았다). 업비트 BTC 9,010만원에서
    500 은 0.00056% 지만, **Gate BTC_USDT 64,300 에서는 0.78%** 다 — 5,000배 굵다.

    그 결과 숏 계획이 통째로 무너졌다. 상단 스마트 띠 폭이 중앙값 44.4 인데 눈금이
    500 이라 띠 전체가 눈금 한 칸에 들어가고, 1차 익절(띠 아랫변)과 2차(띠 윗변)가
    **같은 값**이 되어 `plan_for` 가 계획을 거부한다:

    ```
    최근 60봉 · 숏 방아쇠 21건 → 계획이 선 것 1건
    ```

    ⛔ **반올림 방향의 문제가 아니다.** 올림으로 바꿔도 1건이다 (실측). 눈금이 띠보다
    굵으면 어느 쪽으로 굴려도 두 변이 같은 칸에 떨어진다.

    ⚠️ **롱은 막히지 않았지만 계획 가격이 뭉개졌다.** 2026-08-18 라이브 롱 4건의 계획
    평단이 전부 `64,000`·손절 `63,500` 이었고 실제 체결은 64,077~64,130 이었다.
    """

    def __post_init__(self) -> None:
        """음수 비용과 출처 없는 실측을 막는다.

        Raises:
            CostConfigError: 비용이 음수이거나, `measured` 인데 측정 시각이 없는 경우.

        Note:
            **음수 비용은 "거래할수록 돈이 생긴다"는 뜻**이다. 메이커 리베이트가 있는
            거래소가 실제로 존재하지만 업비트·토스는 아니고, 오타로 들어온 음수를
            통과시키면 백테스트가 조용히 부풀려진다.

            `MEASURED` 인데 `measured_at` 이 없으면 그 수치는 재현할 수 없다 — 언제의
            시장인지 모르는 실측은 가정과 구별되지 않으므로 실측이라 부르지 않는다.
        """
        for name, value in (
            ("fee_pct", self.fee_pct),
            ("tax_pct_sell", self.tax_pct_sell),
            ("slippage_pct_one_way", self.slippage_pct_one_way),
        ):
            if value < 0:
                raise CostConfigError(
                    f"{self.market.value}.{name} 가 음수다: {value} — "
                    "거래할수록 돈이 생긴다는 뜻이 되고 백테스트가 조용히 부풀려진다"
                )
        if self.slippage_source is SlippageSource.MEASURED and self.measured_at is None:
            raise CostConfigError(
                f"{self.market.value}: slippage_source=measured 인데 measured_at 이 없다 — "
                "언제의 시장인지 모르는 실측은 가정과 구별되지 않는다"
            )
        if self.measured_at is not None and self.measured_at.tzinfo is None:
            raise CostConfigError(
                f"{self.market.value}.measured_at 이 naive 다 (spec §12.3): {self.measured_at!r}"
            )
        if not self.source.strip():
            raise CostConfigError(
                f"{self.market.value}.source 가 비어 있다 — 출처 없는 비용은 "
                "나중에 근거를 잃는다 (§5.6.2)"
            )

    @property
    def fee_and_tax_round_trip_pct(self) -> Decimal:
        """슬리피지를 뺀 **확정 비용**의 왕복 합 — 수수료 x2 + 매도 거래세.

        Note:
            이 값에는 가정이 없다. 거래소·세제가 공표한 수치이므로 **실측이 필요 없는
            하한**이며, 손익분기 슬리피지 역산에서 예산의 고정 지출로 빠진다.
        """
        with fixed_context():
            return self.fee_pct * 2 + self.tax_pct_sell

    @property
    def taker(self) -> Decimal:
        """테이커 편도 수수료 — 없으면 `fee_pct`.

        Note:
            ⭐ 대체하는 이유는 **기존 시장 블록을 그대로 두기** 위해서다. 업비트는
            메이커·테이커가 같은 0.05% 라서 나눌 것이 없고, KRX·NASDAQ 도 그렇다.
        """
        return self.fee_pct if self.taker_fee_pct is None else self.taker_fee_pct

    @property
    def maker(self) -> Decimal:
        """메이커 편도 수수료 — 없으면 `fee_pct`.

        Note:
            ⚠️ **음수일 수 있다** (리베이트). 그래서 `__post_init__` 의 음수 금지에서
            이 칸만 빼 뒀다 — 다른 칸의 음수는 오타이지만 이건 실재하는 값이다.

            ⛔ 그런데 낙관하지 않는다. Gate 계약 명세는 -0.0001(리베이트)인데 **실제 계정
            요율은 +0.0002**(내는 돈)였다 (실측 2026-08-17). 명세를 믿으면 청산 다리가
            돈을 받는 것으로 잡힌다 — 주문 응답의 `mkfr` 이 진짜다.
        """
        return self.fee_pct if self.maker_fee_pct is None else self.maker_fee_pct

    def round_trip_for(self, *, exit_is_maker: bool) -> Decimal:
        """청산 다리가 메이커인지에 따른 왕복 비용.

        Args:
            exit_is_maker: 청산이 지정가로 채워졌는가.

        Returns:
            왕복 비용 비율 (슬리피지·거래세 포함).

        Note:
            🔴 **결과가 비용을 바꾼다.** 0.1 의 다리 구성:

                진입        시장가          →  늘 테이커
                반익·목표   지정가          →  메이커
                손절        발동 시 시장가  →  **테이커**
                전환 익절   시장가          →  **테이커**

            그래서 같은 거래도 어떻게 끝났는지에 따라 비용이 다르다. 하나로 뭉개면
            손절로 끝난 거래를 과소, 목표로 끝난 거래를 과대 계상한다.

            ⚠️ **슬리피지는 양쪽 다리에 같게 붙인다.** 지정가는 스프레드를 건너지 않아
            이론상 0 이지만, 그 자리를 **미체결 위험**이 대신하고 그것은 슬리피지가 아니라
            다른 층이다. 낙관하지 않으려고 보수적으로 둔다.

            ⛔ 판정에 쓸지는 별도 결정이다. `round_trip_pct` 가 전부 테이커로 계산하며
            그것이 지금 판정의 기준이다 (§5.6.2 · 매매 로직 동결).
        """
        with fixed_context():
            exit_fee = self.maker if exit_is_maker else self.taker
            return self.taker + exit_fee + self.tax_pct_sell + self.slippage_pct_one_way * 2

    def round_trip_by(self, *, entry_is_maker: bool, exit_is_maker: bool) -> Decimal:
        """**양쪽 다리의 체결 유형**으로 센 왕복 비용 (T42 ④).

        Args:
            entry_is_maker: 진입이 지정가로 채워졌는가 (`limit_entry` 경로).
            exit_is_maker: 청산이 지정가로 채워졌는가 (목표 익절).

        Returns:
            왕복 비용 비율 (슬리피지·거래세 포함).

        Note:
            🔴 `round_trip_for` 는 진입을 늘 테이커로 본다 — 0.1 의 구조였다. 0.5 이후는
            진입도 지정가라 그 가정이 **실제의 2배**를 물린다 (실측 2026-08-22: 모델
            0.150% vs 현실 0.04~0.07%). 이 값은 *"비용을 낮춰 살리는 것"* 이 아니라
            **체결 유형을 사실대로 세는 것**이다 — 그래도 판정값이 움직이므로 옛 표와
            한 줄에 섞지 않는다 (§5.6.2). 기본 경로(`fill_cost` 꺼짐)는 그대로다.

            ⚠️ 슬리피지는 여전히 양쪽에 같게 붙인다 (`round_trip_for` 와 같은 이유).
        """
        with fixed_context():
            entry_fee = self.maker if entry_is_maker else self.taker
            exit_fee = self.maker if exit_is_maker else self.taker
            return entry_fee + exit_fee + self.tax_pct_sell + self.slippage_pct_one_way * 2

    @property
    def entry_taker_round_trip_pct(self) -> Decimal:
        """진입만 테이커, 청산은 메이커일 때의 왕복 비용.

        Note:
            🔴 돌파 진입형 플레이북의 실제 구조다 — 진입은 *"돌파 즉시 시장가"*(테이커
            필연)이고 반익·목표는 지정가(메이커)다.

            ⛔ **판정에 아직 쓰지 않는다.** `round_trip_pct` 가 전부 테이커로 계산하며
            그것이 보수적이다. 이 값으로 판정을 바꾸는 것은 별도 결정이고, 바꾸는 순간
            지금까지의 실측이 무엇의 성적인지 알 수 없게 된다 (§5.6.2).

            ⚠️ **손절은 테이커다** (발동 시 시장가). 그래서 이 값은 손절로 끝난 거래에는
            낙관적이다 — 그 경우는 `round_trip_for(exit_is_maker=False)` 를 쓴다.
        """
        return self.round_trip_for(exit_is_maker=True)

    def funding_cost_pct(self, hours: Decimal) -> Decimal:
        """보유 시간만큼의 펀딩 비용.

        Args:
            hours: 보유 시간.

        Returns:
            비율. 펀딩이 없는 시장(현물)은 0 이다.

        Note:
            🔴 **업비트 현물에는 없던 비용이다.** 8시간마다(00·08·16 UTC) 부과되므로
            보유가 길면 왕복에 더해진다.

            ⚠️ **정산 시각을 지나는 횟수**로 세는 것이 정확한데, 여기서는 비례로 센다 —
            시각 기반으로 세려면 진입·청산 시각이 필요하고 그건 원장이 안다.
            근사임을 여기 적어 둔다 (절대 규칙 #8).
        """
        if self.funding_pct_per_8h is None:
            return Decimal(0)
        with fixed_context():
            return self.funding_pct_per_8h * hours / Decimal(8)

    @property
    def round_trip_pct(self) -> Decimal:
        """왕복 총비용 — 수수료 x2 + 거래세 + 슬리피지 x2.

        Note:
            RR·Trade Card 의 **순손익 병기**(spec §12.7)가 쓰는 값이다. 단타에서 비용은
            RR 을 뒤집는 주범이므로 총손익만 보여주는 화면을 만들지 않는다.
        """
        with fixed_context():
            return self.fee_and_tax_round_trip_pct + self.slippage_pct_one_way * 2


@dataclass(frozen=True, slots=True)
class CostTable:
    """시장별 비용 테이블 (spec §12.7 "설정으로 관리").

    Attributes:
        markets: 시장 → 비용 구성.

    Note:
        `dict` 를 그대로 돌리지 않고 감싸는 이유는 **없는 시장을 조용히 넘기지 않기**
        위해서다. `table.get(market)` 이 None 을 주면 호출부가 0 으로 취급하기 쉽고,
        비용 0 은 모든 전략을 성립시킨다.
    """

    markets: Mapping[Market, MarketCosts]

    def for_market(self, market: Market) -> MarketCosts:
        """시장의 비용 구성을 얻는다.

        Args:
            market: 대상 시장.

        Returns:
            비용 구성.

        Raises:
            CostConfigError: 해당 시장이 테이블에 없는 경우.
        """
        costs = self.markets.get(market)
        if costs is None:
            raise CostConfigError(
                f"{market.value} 의 비용이 테이블에 없다 — 비용 0 으로 진행하지 않는다 "
                f"(등록된 시장: {sorted(key.value for key in self.markets)})"
            )
        return costs


# ---------------------------------------------------------------------------
# 설정 로딩
# ---------------------------------------------------------------------------

_MARKET_FIELDS = frozenset(
    {
        "fee_pct",
        "tax_pct_sell",
        "slippage_pct_one_way",
        "slippage_source",
        "measured_at",
        "source",
        # ⭐ 아래 셋은 **선택**이다. 없으면 `fee_pct` 로 대체되므로 기존 시장 블록이
        #   그대로 통과한다 (판정값이 안 움직인다).
        "taker_fee_pct",
        "maker_fee_pct",
        "funding_pct_per_8h",
        # ⭐ **호가 단위도 시장 속성이다** (2026-08-18). 없으면 `DEFAULT_TICK`(500)
        #   이므로 기존 시장 블록은 그대로 통과한다 — 업비트 실측값이 안 움직인다.
        "price_tick",
        # ⭐ **종목별 최소 호가** (T18 ①). 시장 하나에 눈금 하나면 어느 종목에서는
        #   반드시 틀린다 — 종목마다 10,000배 다르다.
        "spec_ticks",
        # ⭐ **들어가기 전에 나올 수 있는지 보는 문턱** (2026-08-20). 없으면 검사를
        #   안 한다 — 기존 시장 블록이 그대로 통과한다 (판정값이 안 움직인다).
        "liquidity",
    }
)

DEFAULT_TICK = Decimal(500)
"""**원화 시장의** 호가 라운딩 단위 (원).

🔴 **이 이름의 '기본값' 은 원화 기본값이다** (T18 ① 정정). 예전에는 모든 시장이
이 값을 함께 썼고, Gate BTC_USDT 64,300 에서 500 은 **가격의 0.78%** 였다 —
숏 계획이 21건 중 1건만 섰다. 지금은 `resolve_tick` 이 **통화를 보고** 이 값에
떨어질지 정하며, 비원화 시장이 선언 없이 여기 오면 터진다.

🔴 **이것이 없으면 같은 박스가 매번 다른 셋업이 된다.** 레벨 폭이 꼬리에서 나오는데
봉이 하나 지날 때마다 값이 소수점 아래로 흔들린다. 실측에서 관측 396건 중 **286건이
서로 다른 셋업**으로 세어졌고, 차이는 이랬다:

```
126,628,849.4615200000000000000
126,628,767.7112800000000000000
```

⇒ 표본이 부풀려져 **§12.9 표본 30건 규칙이 무의미해진다.** 그리고 소수점 18자리 가격은
애초에 낼 수 없는 주문이다.

⚠️ **임시값이다.** 업비트 KRW 마켓은 가격대별 호가 표가 있고 BTC 가격대는 1,000원으로
알려져 있다. 실제 호가 표는 P2(페이퍼/라이브)에서 거래소별로 받아 온다.
"""


TICK_RATIO = Decimal("0.0000075")
"""**가격 대비 눈금 비율** — 0.00075% (T18 ① · 얼린 상수).

```
tick = ceil(price x TICK_RATIO / spec) x spec
```

🔴 **BTC 에서 고른 0.5 가 이 비율의 정의다.** 2026-08-18 에 사용자 승인으로 넣은 값이
공식에서 그대로 나와야 한다:

```
64,300 x 0.0000075 = 0.482  →  0.1 단위로 올림  →  0.5   ✅
```

같은 비율을 다른 종목에 얹으면 (명세 틱을 넘지 못하면 명세 틱 그대로다):

```
ETH   3,000  → 0.03      SOL  150   → 0.01
XRP   2.1    → 0.0001    DOGE 0.24  → 0.00001
```

전부 왕복 비용(0.157%)보다 **두 자릿수 작다** — 계획 가격을 뭉개지 않으면서 소수점
아래 흔들림만 접는다. 굵을 때 무슨 일이 났는지는 `DEFAULT_TICK` 문서를 본다.

⛔ **성과를 보고 조정하지 않는다.** 이 값이 굵어지면 계획이 죽고 얇아지면 같은 박스가
매번 다른 셋업으로 세어져 표본이 부풀려진다 (§12.9 가 무의미해진다). 바꾸려면 축을
하나만 열고 out-of-sample 로 판정한다 (§5.6.7).
"""


class TickUnknownError(CostConfigError):
    """그 종목의 호가 눈금을 정할 수 없다 (T18 ①).

    Note:
        🔴 **조용히 기본값으로 떨어지지 않는다.** `DEFAULT_TICK`(500원)으로 낙하하던
        경로가 2026-08-18 의 버그였다 — Gate BTC 에서 0.78% 짜리 눈금이 되어 숏 계획이
        21건 중 1건만 섰다. 모르면 **판을 안 띄우는 것**이 맞다.
    """


def resolve_tick(costs: MarketCosts, symbol: str, price: Decimal, *, krw: bool) -> Decimal:
    """그 종목·그 가격에서 쓸 호가 눈금 (T18 ①).

    Args:
        costs: 시장 비용 블록.
        symbol: 종목 코드.
        price: 지금 가격.
        krw: 원화 시장인가. 선언이 없을 때 옛 기본값을 쓸지 가른다.

    Returns:
        눈금.

    Raises:
        TickUnknownError: 선언이 없는 **비원화** 시장인 경우.

    Note:
        해석 순서:

        ```
        ① spec_ticks[종목]  → tick_for(가격, 명세) — 종목별·가격별
        ② price_tick        → 그 값 (시장 전체 덮어쓰기)
        ③ 원화 시장         → DEFAULT_TICK(500원) — 업비트 백테스트 재현을 지킨다
        ④ 그 외             → 터진다
        ```

        🔴 **③을 남긴 것은 재현 때문이다.** 500 은 원화 상수로서는 맞다 (업비트 BTC
        9,010만원에 0.00056%). 지우면 4년치 실측을 다시 낼 수 없다.

        ⛔ **④가 이 함수의 요점이다.** 버그는 "Gate 가 아무것도 선언 안 했는데 원화
        상수로 떨어진 것" 이었다 — 통화를 보면 그 경로가 닫힌다.
    """
    spec = costs.spec_ticks.get(symbol)
    if spec is not None:
        return tick_for(price, spec)
    if costs.price_tick is not None:
        return costs.price_tick
    if krw:
        return DEFAULT_TICK
    raise TickUnknownError(
        f"{costs.market.value}:{symbol} 의 호가 눈금을 모른다 — "
        "config/costs.yml 의 spec_ticks 에 선언한다. "
        "원화 기본값(500)으로 떨어뜨리지 않는다: Gate BTC 에서 그것은 가격의 0.78% 이고, "
        "그 눈금으로 숏 계획이 21건 중 1건만 섰다 (2026-08-18)"
    )


def tick_for(price: Decimal, spec: Decimal) -> Decimal:
    """그 가격에서 쓸 **호가 눈금** — 명세 틱의 배수로 올린다 (T18 ①).

    Args:
        price: 지금 가격.
        spec: 거래소 최소 호가 단위 (`order_price_round`).

    Returns:
        눈금. 최소 `spec` 이다.

    Raises:
        ValueError: `spec` 이 0 이하인 경우. 눈금 없이 가격을 적을 수는 없다.

    Note:
        🔴 **명세 틱보다 굵게 잡는 것이 목적이다.** 거래소 최소값을 그대로 쓰면 레벨
        폭이 꼬리에서 나와 봉마다 소수점 아래로 흔들리고, 같은 박스가 매번 다른
        셋업으로 세어진다 (실측 396건 중 286건).

        ⚠️ **올림이다.** 내림이면 작은 종목에서 0 이 나오고, 0 으로 나누는 라운딩은
        가격을 통째로 지운다.

        ⛔ **가격을 안 보고 정하지 않는다.** 같은 0.5 가 BTC 에서는 0.0008% 지만
        XRP 2.1 에서는 24% 다 — 그 한 줄이 숏 계획을 21건에서 1건으로 만들었다.
    """
    if spec <= 0:
        raise ValueError(f"명세 호가 단위가 0 이하다 ({spec}) — 가격을 적을 수 없다")
    want = price * TICK_RATIO
    steps = int(want / spec)
    if Decimal(steps) * spec < want:
        steps += 1
    return spec * max(steps, 1)


LIQUIDITY_KEYS = ("max_gap_pct", "depth_multiple", "order_deviation_pct")
"""유동성 문턱의 칸 이름 — 오타를 **조용히 무시하지 않으려고** 목록으로 둔다."""


def _liquidity(market: Market, raw: object) -> dict[str, Decimal]:
    """유동성 문턱을 읽는다 (2026-08-20).

    Args:
        market: 대상 시장 — 오류 문구에 쓴다.
        raw: 설정에서 온 값. 없으면 None.

    Returns:
        `{max_gap_pct, depth_multiple}`. 선언이 없으면 빈 표(= 검사 안 함).

    Raises:
        CostConfigError: 매핑이 아니거나, 모르는 칸이 있거나, 값이 0 이하인 경우.

    Note:
        🔴 **모르는 칸을 조용히 넘기지 않는다.** `max_gap` 처럼 한 글자 틀리면 검사가
        기본값으로 돌면서 **켠 줄 알고 안 켜진** 상태가 된다 — 이 프로젝트가 가장
        비싸게 겪는 종류의 실패다 (절대 규칙 #8).

        ⛔ **0 이하를 막는다.** 구멍 0% 는 어떤 호가창도 통과 못 하고, 깊이 배수 0 은
        검사를 껐다는 뜻인데 그것은 블록을 지워서 표현해야 한다.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CostConfigError(f"{market.value}.liquidity 가 매핑이 아니다: {type(raw).__name__}")
    fields = cast("dict[object, object]", raw)
    unknown = {str(key) for key in fields} - set(LIQUIDITY_KEYS)
    if unknown:
        raise CostConfigError(
            f"{market.value}.liquidity 에 모르는 칸이 있다: {sorted(unknown)} — "
            f"허용: {list(LIQUIDITY_KEYS)}. 오타면 검사가 조용히 기본값으로 돈다"
        )
    made: dict[str, Decimal] = {}
    for key in LIQUIDITY_KEYS:
        if key not in fields:
            continue
        value = _decimal(fields[key], f"{market.value}.liquidity.{key}")
        if value <= 0:
            raise CostConfigError(
                f"{market.value}.liquidity.{key} 가 0 이하다 ({value}) — "
                "검사를 끄려면 블록을 지운다"
            )
        made[key] = value
    return made


def _spec_ticks(market: Market, raw: object) -> dict[str, Decimal]:
    """종목별 최소 호가 표를 읽는다 (T18 ①).

    Args:
        market: 대상 시장 — 오류 문구에 쓴다.
        raw: 설정에서 온 값. 없으면 None.

    Returns:
        `{종목: 최소 호가}`. 선언이 없으면 빈 표.

    Raises:
        CostConfigError: 표가 매핑이 아니거나 값이 0 이하인 경우.

    Note:
        ⛔ **0 이하를 통과시키지 않는다.** 0 으로 라운딩하면 가격이 통째로 지워지고,
        그 실패는 "계획이 안 선다" 로만 보인다 (절대 규칙 #8).
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CostConfigError(f"{market.value}.spec_ticks 가 매핑이 아니다: {type(raw).__name__}")
    made: dict[str, Decimal] = {}
    for symbol, value in cast("dict[object, object]", raw).items():
        tick = _decimal(value, f"{market.value}.spec_ticks.{symbol}")
        if tick <= 0:
            raise CostConfigError(
                f"{market.value}.spec_ticks.{symbol} 가 0 이하다 ({tick}) — "
                "0 으로 라운딩하면 가격이 통째로 지워진다"
            )
        made[str(symbol)] = tick
    return made


def _decimal(value: object, field: str) -> Decimal:
    """설정 스칼라를 Decimal 로 바꾼다.

    Args:
        value: 원본 값.
        field: 필드 이름 (오류 메시지용).

    Returns:
        Decimal 값.

    Raises:
        CostConfigError: 수로 읽을 수 없는 경우.

    Note:
        `str()` 을 거치는 것이 핵심이다. YAML `0.0005` 는 float 로 파싱되고
        `Decimal(float)` 은 이진 오차를 그대로 가져온다 (`risk/policy.py` 와 같은 이유).
    """
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise CostConfigError(f"{field} 를 수로 읽을 수 없다: {value!r}") from exc


def _measured_at(value: object, field: str) -> datetime | None:
    """`measured_at` 을 UTC aware datetime 으로 만든다.

    Args:
        value: YAML 이 준 값. `datetime` 또는 ISO 문자열.
        field: 필드 이름 (오류 메시지용).

    Returns:
        UTC aware datetime. 값이 없으면 None.

    Raises:
        CostConfigError: 파싱할 수 없는 경우.

    Note:
        PyYAML 은 `2026-08-06T05:20:00Z` 를 datetime 으로 파싱하지만 오프셋 표기가 없으면
        **naive** 로 준다. naive 를 UTC 로 가정하면 설정 실수가 조용히 통과하므로
        거부한다 (절대 규칙 #7).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CostConfigError(f"{field} 를 시각으로 읽을 수 없다: {value!r}") from exc
    else:
        raise CostConfigError(f"{field} 는 시각이어야 한다 — 받은 값: {value!r}")
    if moment.tzinfo is None:
        raise CostConfigError(
            f"{field} 에 타임존이 없다: {value!r} — `...Z` 처럼 UTC 를 명시한다 (spec §12.3)"
        )
    return moment.astimezone(UTC)


def parse_cost_table(raw: Mapping[str, object]) -> CostTable:
    """파싱된 매핑에서 비용 테이블을 만든다.

    Args:
        raw: YAML 파싱 결과.

    Returns:
        비용 테이블.

    Raises:
        CostConfigError: 키 누락·타입 불일치·알 수 없는 시장/필드·불변식 위반.

    Note:
        **알 수 없는 키를 무시하지 않는다.** 오타 난 키를 조용히 넘기면 "비용을 바꿨는데
        결과가 안 바뀐다"가 되고, 비용에서 그것은 성립하지 않는 전략을 성립한다고
        판정하는 것으로 이어진다.
    """
    markets_raw = raw.get("markets")
    if not isinstance(markets_raw, dict):
        raise CostConfigError(f"`markets` 는 매핑이어야 한다 — 받은 값: {markets_raw!r}")
    block = cast(Mapping[str, object], markets_raw)

    known = {market.value for market in Market}
    unknown = set(block) - known
    if unknown:
        raise CostConfigError(
            f"`markets` 에 알 수 없는 시장이 있다: {sorted(unknown)} — 허용: {sorted(known)}"
        )

    markets: dict[Market, MarketCosts] = {}
    for market in Market:
        entry = block.get(market.value)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise CostConfigError(f"`markets.{market.value}` 는 매핑이어야 한다")
        fields = cast(Mapping[str, object], entry)
        stray = set(fields) - _MARKET_FIELDS
        if stray:
            raise CostConfigError(
                f"`markets.{market.value}` 에 알 수 없는 키: {sorted(stray)} — "
                f"허용: {sorted(_MARKET_FIELDS)}"
            )
        missing = {"fee_pct", "slippage_pct_one_way", "slippage_source", "source"} - set(fields)
        if missing:
            raise CostConfigError(f"`markets.{market.value}` 에 {sorted(missing)} 가 없다")
        try:
            source_kind = SlippageSource(str(fields["slippage_source"]))
        except ValueError as exc:
            raise CostConfigError(
                f"`markets.{market.value}.slippage_source` 가 알 수 없는 값이다: "
                f"{fields['slippage_source']!r} — 허용: "
                f"{sorted(item.value for item in SlippageSource)}"
            ) from exc
        markets[market] = MarketCosts(
            market=market,
            fee_pct=_decimal(fields["fee_pct"], f"{market.value}.fee_pct"),
            # ⭐ 선택 칸 — 없으면 None 이고 접근자가 `fee_pct` 로 대체한다.
            taker_fee_pct=(
                None
                if "taker_fee_pct" not in fields
                else _decimal(fields["taker_fee_pct"], f"{market.value}.taker_fee_pct")
            ),
            maker_fee_pct=(
                None
                if "maker_fee_pct" not in fields
                else _decimal(fields["maker_fee_pct"], f"{market.value}.maker_fee_pct")
            ),
            funding_pct_per_8h=(
                None
                if "funding_pct_per_8h" not in fields
                else _decimal(fields["funding_pct_per_8h"], f"{market.value}.funding_pct_per_8h")
            ),
            price_tick=(
                None
                if "price_tick" not in fields
                else _decimal(fields["price_tick"], f"{market.value}.price_tick")
            ),
            spec_ticks=_spec_ticks(market, fields.get("spec_ticks")),
            liquidity=_liquidity(market, fields.get("liquidity")),
            tax_pct_sell=_decimal(fields.get("tax_pct_sell", 0), f"{market.value}.tax_pct_sell"),
            slippage_pct_one_way=_decimal(
                fields["slippage_pct_one_way"], f"{market.value}.slippage_pct_one_way"
            ),
            slippage_source=source_kind,
            measured_at=_measured_at(fields.get("measured_at"), f"{market.value}.measured_at"),
            source=str(fields["source"]),
        )

    if not markets:
        raise CostConfigError("`markets` 가 비어 있다 — 비용 없는 테이블은 쓸모가 없다")
    return CostTable(markets=markets)


def load_cost_table(path: Path | None = None) -> CostTable:
    """비용 테이블을 파일에서 읽는다.

    Args:
        path: 설정 파일 경로. None 이면 `DEFAULT_CONFIG_PATH`.

    Returns:
        비용 테이블.

    Raises:
        CostConfigError: 파일이 없거나 파싱·검증에 실패한 경우.

    Note:
        ⚠️ **파일 부재를 허용하지 않는다** (`risk/policy.py` 와 같은 판단). 비용에는
        "안전한 기본값"이 없다 — 0 으로 떨어지면 모든 전략이 성립하는 것처럼 보이고,
        그것이 이 프로젝트에서 가장 비싼 조용한 실패다 (절대 규칙 #8).
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        raise CostConfigError(
            f"{target} 가 없다 — 비용에는 '안전한 기본값'이 없으므로 기본값으로 "
            f"진행하지 않는다 (절대 규칙 #8)"
        )
    # ⭐ **수정 시각을 열쇠에 넣어 캐시한다.** 봉마다 부르는 경로가 생기면서 매 걸음
    #    YAML 을 다시 파싱하게 됐다 — 판정 결과는 같은데 비용만 든다.
    #
    #    ⛔ 경로만으로 캐시하면 파일을 고쳐도 **안 읽는다.** 비용 테이블은 사람이
    #      고치는 값이고, 고친 것이 안 먹으면 그게 조용한 실패다 (절대 규칙 #8).
    return _cached_table(target, target.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _cached_table(target: Path, _mtime: int) -> CostTable:
    """파싱 결과 캐시 — 열쇠는 `(경로, 수정 시각)`.

    Args:
        target: 설정 파일.
        _mtime: 수정 시각(ns). **열쇠로만 쓴다** — 파일이 바뀌면 새로 읽게 한다.

    Returns:
        비용 테이블.

    Raises:
        CostConfigError: 파싱·검증 실패.
    """
    try:
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CostConfigError(f"{target} 를 읽을 수 없다: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CostConfigError(f"{target} 최상위는 매핑이어야 한다 — 받은 값: {type(parsed)}")
    return parse_cost_table(cast(Mapping[str, object], parsed))


# ---------------------------------------------------------------------------
# 손익분기 산수 — `EV = P*RR - (1-P) - c > 0`  ⇒  `P > (1+c)/(1+RR)`
# ---------------------------------------------------------------------------


def required_win_rate(
    tp_multiple: Decimal, k: Decimal, cost_pct: Decimal, atr_pct: Decimal
) -> Decimal:
    """손익분기 승률 — `P > (1+c)/(1+RR)` (spec §P1-8-0b Q1).

    Args:
        tp_multiple: 익절 거리 / ATR (실측값 t).
        k: ATR 손절 배수. `SL = k·ATR` 이다.
        cost_pct: 왕복 비용 (가격 대비 비율).
        atr_pct: ATR / 가격.

    Returns:
        손익분기 승률. **1 을 넘으면 그 조합은 산술적으로 불가능**하다 — 어떤 셋업도,
        어떤 승률로도 갚을 수 없다.

    Raises:
        ValueError: `k` 또는 `atr_pct` 가 0 이하인 경우 (`c` 의 분모다).

    Note:
        1차 익절 50% 부분청산(spec §6.3)을 전량으로 계산하므로 이 값은 **낙관적**이다.
        실제 필요 승률은 이보다 높다.

        `RR = t/k`, `c = 비용 / (k·ATR)` 이다. 비용을 R 단위로 환산하는 것이 핵심인데,
        비용은 가격에 대해 고정인 반면 R 은 시간축에 비례해 커지기 때문이다 — 그래서
        같은 비용률이 5m 에서는 치명적이고 4h 에서는 무시할 만하다.
    """
    if k <= 0:
        raise ValueError(f"ATR 손절 배수는 0 보다 커야 한다: {k}")
    if atr_pct <= 0:
        raise ValueError(f"ATR/가격 비는 0 보다 커야 한다: {atr_pct}")
    with fixed_context():
        rr = tp_multiple / k
        cost_in_r = cost_pct / (k * atr_pct)
        return (1 + cost_in_r) / (1 + rr)


@dataclass(frozen=True, slots=True)
class BreakevenSlippage:
    """손익분기 슬리피지 역산 결과.

    Attributes:
        win_rate: 입력한 승률(이행률) P.
        round_trip_budget_pct: 이 승률이 감당하는 **왕복 총비용 예산** (가격 대비 비율).
        fee_and_tax_round_trip_pct: 그중 확정 지출 (수수료 x2 + 거래세).
        slippage_one_way_pct: 남은 예산을 왕복 2회로 나눈 **편도 슬리피지 한도**.
        is_feasible: 슬리피지 예산이 0 이상인가. False 면 **수수료만으로 이미 적자**다.

    Note:
        `is_feasible=False` 는 "슬리피지를 줄이면 된다"가 아니라 **"이 승률·RR 조합은
        수수료조차 갚지 못한다"** 는 뜻이다. 5m 이 정확히 그 상태였다.
    """

    win_rate: Decimal
    round_trip_budget_pct: Decimal
    fee_and_tax_round_trip_pct: Decimal
    slippage_one_way_pct: Decimal
    is_feasible: bool

    def headroom(self, measured_one_way_pct: Decimal) -> Decimal | None:
        """실측 편도 슬리피지 대비 여유 배수.

        Args:
            measured_one_way_pct: 실측 편도 슬리피지 (가격 대비 비율).

        Returns:
            `한도 / 실측`. 1 보다 크면 여유가 있다. 실측이 0 이하면 None.

        Note:
            **이 배수가 이 모듈의 최종 산출물**이다. "가정이 맞았나"는 과거 스프레드를
            알 수 없어 답할 수 없지만(모듈 docstring), "현재 시장 대비 몇 배 여유인가"는
            답할 수 있고 그것이 판정에 필요한 전부다.
        """
        if measured_one_way_pct <= 0:
            return None
        with fixed_context():
            return self.slippage_one_way_pct / measured_one_way_pct


def breakeven_slippage(
    win_rate: Decimal,
    tp_multiple: Decimal,
    k: Decimal,
    atr_pct: Decimal,
    fee_and_tax_round_trip_pct: Decimal,
) -> BreakevenSlippage:
    """승률 P 를 주고 **견딜 수 있는 최대 슬리피지**를 역산한다.

    `required_win_rate` 의 역함수다:

    ```
    P > (1+c)/(1+RR)   ⇒   c < P·(1+RR) - 1        (c 는 R 단위 왕복 비용)
    왕복 비용 예산 = c_max · k · ATR%
    편도 슬리피지 한도 = (예산 - 수수료·세금 왕복) / 2
    ```

    Args:
        win_rate: 측정된 이행률 P (0~1).
        tp_multiple: 익절 거리 / ATR (실측값 t).
        k: ATR 손절 배수.
        atr_pct: ATR / 가격.
        fee_and_tax_round_trip_pct: 확정 비용의 왕복 합 (`MarketCosts` 에서 온다).

    Returns:
        역산 결과. 슬리피지 한도는 **음수일 수 있다** — 그 경우 `is_feasible=False` 다.

    Raises:
        ValueError: `k`·`atr_pct` 가 0 이하이거나 `win_rate` 가 [0, 1] 밖인 경우.

    Note:
        **슬리피지 가정이 입력에 없다.** 그것이 이 함수의 존재 이유다 (모듈 docstring
        "과거 스프레드는 측정할 수 없다"). 결과를 현재 실측치와 비교하면 판정이
        가정값에서 독립된다.

        `required_win_rate` 와 같은 낙관 편향을 갖는다 (1차 익절 부분청산을 전량으로
        계산). 즉 실제 한도는 여기서 나온 값보다 **작다**.
    """
    if k <= 0:
        raise ValueError(f"ATR 손절 배수는 0 보다 커야 한다: {k}")
    if atr_pct <= 0:
        raise ValueError(f"ATR/가격 비는 0 보다 커야 한다: {atr_pct}")
    if not Decimal(0) <= win_rate <= Decimal(1):
        raise ValueError(f"승률은 0~1 이어야 한다: {win_rate}")

    with fixed_context():
        rr = tp_multiple / k
        cost_in_r = win_rate * (1 + rr) - 1
        budget = cost_in_r * k * atr_pct
        slippage_one_way = (budget - fee_and_tax_round_trip_pct) / 2
        return BreakevenSlippage(
            win_rate=win_rate,
            round_trip_budget_pct=budget,
            fee_and_tax_round_trip_pct=fee_and_tax_round_trip_pct,
            slippage_one_way_pct=slippage_one_way,
            is_feasible=slippage_one_way >= 0,
        )


# ---------------------------------------------------------------------------
# 호가창 실측
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SlippageSample:
    """호가창 스냅샷 하나에서 잰 슬리피지.

    Attributes:
        symbol: 종목 코드.
        as_of: 스냅샷 시각 (UTC).
        notional: 측정에 쓴 주문 금액.
        mid: 중간가 — 모든 bp 의 기준점.
        spread_bps: 스프레드 / mid.
        half_spread_bps: 스프레드/2 — **깊이와 무관한 하한**이다.
        buy_one_way_bps: 매수 편도 슬리피지 = 스프레드/2 + 매수 깊이충격.
        sell_one_way_bps: 매도 편도 슬리피지.
        buy_depth_impact_bps: 매수 깊이충격 (편도 - 스프레드/2).
        sell_depth_impact_bps: 매도 깊이충격.
        levels_consumed: 소진한 호가 단계 수 (매수·매도 중 큰 쪽).
        buy_is_complete: 매수 방향이 `notional` 을 다 채웠는가.
        sell_is_complete: 매도 방향이 `notional` 을 다 채웠는가.

    Note:
        **스프레드/2 와 깊이충격을 쪼개 두는 이유**: 규모를 키웠을 때 무엇이 늘어나는지가
        보인다. 스프레드/2 는 금액과 무관한 고정 비용이고, 깊이충격만 금액에 비례한다.
        합쳐 두면 "1천만에서 0, 5천만에서 0.5bp" 같은 사실이 묻힌다.

        **매수·매도 체결 완료를 따로 두는 이유도 같다.** 실측에서 KRW-BTC 는 30단계
        매도호가에 3억원이 쌓여 있는데 매수호가에는 4,700만원뿐이었다 — 즉 **살 수는
        있어도 같은 금액을 팔 수는 없는** 비대칭이 있다. 하나로 합치면 그 사실이
        "깊이 부족"이라는 한 단어로 뭉개지고, 청산 리스크가 보이지 않는다 (spec §7).
    """

    symbol: str
    as_of: datetime
    notional: Decimal
    mid: Decimal
    spread_bps: Decimal
    half_spread_bps: Decimal
    buy_one_way_bps: Decimal
    sell_one_way_bps: Decimal
    buy_depth_impact_bps: Decimal
    sell_depth_impact_bps: Decimal
    levels_consumed: int
    buy_is_complete: bool
    sell_is_complete: bool

    @property
    def is_complete(self) -> bool:
        """양방향 모두 채웠는가."""
        return self.buy_is_complete and self.sell_is_complete

    @property
    def round_trip_slippage_bps(self) -> Decimal:
        """왕복 슬리피지 = 매수 편도 + 매도 편도.

        Note:
            **수수료가 들어 있지 않다.** 수수료는 `MarketCosts` 소관이고, 섞으면 어느
            쪽이 실측이고 어느 쪽이 공표 수치인지 구분이 사라진다.
        """
        return self.buy_one_way_bps + self.sell_one_way_bps

    @property
    def average_one_way_bps(self) -> Decimal:
        """매수·매도 편도의 평균 — 비용 테이블의 `slippage_pct_one_way` 로 들어갈 값."""
        with fixed_context():
            return (self.buy_one_way_bps + self.sell_one_way_bps) / 2


def sample_slippage(book: OrderBook, notional: Decimal) -> SlippageSample:
    """호가창 스냅샷에서 편도 슬리피지를 잰다.

    Args:
        book: 호가창 스냅샷.
        notional: 시장가로 채울 금액 (호가 통화 기준).

    Returns:
        측정 표본.

    Raises:
        CostMeasurementError: 교차 호가(bid ≥ ask)이거나 mid 가 0 이하인 경우.
        ValueError: `notional <= 0`.

    Note:
        **체결가끼리 비교하지 않는다** — 전부 `mid` 기준이다 (모듈 docstring, §5-1 함정 1).
        연속 체결 사이의 가격 변화는 bid-ask bounce 라서 어떤 간격으로 재도 스프레드가
        나오고, 그것을 더하면 스프레드를 두 번 센다.

        호가창 깊이가 모자라면 예외가 아니라 `is_complete=False` 다. 못 채운 것도
        **우리 규모가 이 종목에서 감당 가능한지**에 대한 답이므로 표본으로 남긴다.
    """
    if book.best_bid <= 0 or book.best_ask <= 0:
        raise CostMeasurementError(
            f"호가가 0 이하다: {book.instrument.symbol} bid={book.best_bid} ask={book.best_ask}"
        )
    if book.best_bid >= book.best_ask:
        raise CostMeasurementError(
            f"교차 호가다 (bid >= ask): {book.instrument.symbol} "
            f"bid={book.best_bid} ask={book.best_ask} @ {book.as_of.isoformat()} — "
            "이 표본으로 계산하면 슬리피지가 음수로 나온다"
        )

    buy = book.walk(notional, Side.BUY)
    sell = book.walk(notional, Side.SELL)

    with fixed_context():
        mid = book.mid
        # 스프레드/2 를 **편도와 같은 식**으로 구한다: `(최우선호가 - mid) / mid`.
        #
        # `스프레드/mid/2` 로 따로 구하면 수학적으로 같은 값인데도 반올림 순서가 달라
        # 1e-32 만큼 어긋난다. 그러면 1호가에서 전량 체결된 주문의 깊이충격이 정확히 0 이
        # 아니라 **-0.00000...1bp** 로 나오고, "편도 >= 스프레드/2" 라는 불변식이 깨진다.
        # 리포트에 `-0.00bp` 가 찍히는 것도 문제지만, 그 불변식이 함정 1(bounce 이중 계산)의
        # 방어선이라 정확히 성립해야 한다.
        half_spread_bps = (book.best_ask - mid) / mid * BPS
        spread_bps = half_spread_bps * 2
        buy_one_way = (buy.average_price - mid) / mid * BPS
        sell_one_way = (mid - sell.average_price) / mid * BPS
        return SlippageSample(
            symbol=book.instrument.symbol,
            as_of=book.as_of,
            notional=notional,
            mid=mid,
            spread_bps=spread_bps,
            half_spread_bps=half_spread_bps,
            buy_one_way_bps=buy_one_way,
            sell_one_way_bps=sell_one_way,
            buy_depth_impact_bps=buy_one_way - half_spread_bps,
            sell_depth_impact_bps=sell_one_way - half_spread_bps,
            levels_consumed=max(buy.levels_consumed, sell.levels_consumed),
            buy_is_complete=buy.is_complete,
            sell_is_complete=sell.is_complete,
        )
