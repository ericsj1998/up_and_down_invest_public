"""종목·시간축·전략 버킷의 기본 열거형과 `Instrument` 값 객체 (spec §9, §12.8).

`Instrument` 는 spec §9 `instruments` 테이블과 1:1 대응한다. 자연키는 `(market, symbol)`
이며, DB 의 `id` 는 영속화 계층의 대리키이므로 도메인 객체에 두지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Market(StrEnum):
    """거래소·시장 식별자 (spec §9 `instruments.market`).

    Note:
        시장 추가는 이 열거형에 한 줄을 더하는 일이다. 문자열로 두면 오타가
        런타임까지 살아남으므로 열거형으로 고정한다.
    """

    KRX = "KRX"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    BINANCE = "BINANCE"
    """바이낸스 USDT-M **무기한 선물** (T62 · 2026-08-25).

    Gate 와 같은 갈래(COIN)·같은 성질(숏·레버리지·펀딩)이지만 **다른 상품**이다 —
    같은 심볼이라도 가격·수수료·펀딩이 달라 캔들을 한 시리즈로 섞지 않는다.
    교차 검증(같은 전략을 두 거래소 시세로)이 이 시장을 들인 이유의 절반이다.
    """

    UPBIT = "UPBIT"
    GATE = "GATE"
    """Gate.io **무기한 선물** (USDT 정산).

    🔴 업비트와 갈래는 같지만(`COIN`) **성질이 다르다** — 여기서만 숏·레버리지가
    열린다 (절대 규칙 #10 개정 2026-08-17). 업비트는 현물이라 롱 온리이고 조회
    전용이다.

    ⚠️ 같은 `BTC` 라도 **다른 상품**이다. 업비트는 KRW 현물, 여기는 USDT 무기한이라
    가격·수수료·펀딩이 전부 다르다. 두 시장의 캔들을 한 시리즈로 섞지 않는다.
    """


class AssetType(StrEnum):
    """자산군 (spec §9 `instruments.asset_type`).

    원칙 P6(자산 추상화)에 따라 주식과 코인은 같은 `Instrument` 로 다루고,
    차이는 이 필드와 어댑터의 `capabilities`(spec §4.2)로만 드러낸다.
    """

    STOCK = "stock"
    COIN = "coin"


class MarketGroup(StrEnum):
    """운용 갈래 — **코인 / 국내주식 / 해외주식**.

    Attributes:
        COIN: 코인 (업비트).
        DOMESTIC_STOCK: 국내주식 (KRX).
        FOREIGN_STOCK: 해외주식 (NASDAQ·NYSE).

    Note:
        `AssetType` 과 **다른 축이다.** `AssetType` 은 `STOCK`/`COIN` 둘뿐이라
        국내와 해외를 못 가르는데, 그 둘은 실제로 다르게 취급된다:

        | | 국내주식 | 해외주식 | 코인 |
        |---|---|---|---|
        | 거래세 | 매도 0.20% | 없음 (양도세는 다른 층) | 없음 |
        | 정산 통화 | KRW | **USD — 환손익 분리 필요** | KRW |
        | 휴장일·서머타임 | 휴장일만 | **둘 다** (C2-3·C2-4) | 해당 없음 |
        | 갭 | 있다 | 있다 | **없다** |

        🔴 **"국내/해외"는 시장의 성질이 아니라 계좌 기준이다.** 한국 거주자 기준으로
        KRX 가 국내다. 나중에 다른 나라 사용자가 생기면 이 매핑은 계좌 설정에서 와야
        한다 — 지금은 1인 사용이라 고정으로 둔다.
    """

    COIN = "코인"
    DOMESTIC_STOCK = "국내주식"
    FOREIGN_STOCK = "해외주식"

    @classmethod
    def of(cls, market: Market) -> MarketGroup:
        """시장이 어느 갈래인가.

        Args:
            market: 시장.

        Returns:
            운용 갈래.

        Raises:
            ValueError: 매핑에 없는 시장. 조용히 어느 한쪽으로 떨어뜨리면 비용·통화·
                휴장일 취급이 통째로 틀린 채 그럴듯해 보인다 (절대 규칙 #8).
        """
        found = _MARKET_GROUPS.get(market)
        if found is None:
            raise ValueError(
                f"{market} 의 운용 갈래가 정해지지 않았다 — 기본값으로 떨어뜨리지 "
                f"않는다. MarketGroup._MARKET_GROUPS 에 추가하라"
            )
        return found


_MARKET_GROUPS: dict[Market, MarketGroup] = {
    Market.UPBIT: MarketGroup.COIN,
    # 바이낸스 무기한 (T62) — Gate 와 같은 이유로 갈래는 코인이다.
    Market.BINANCE: MarketGroup.COIN,
    # ⭐ 선물이지만 운용 갈래는 코인이다 — 갈래는 **자금 배분 단위**이고, 같은 BTC 를
    #    현물과 선물로 나눠 들 이유가 없다 (레버리지는 갈래가 아니라 계획이 정한다).
    Market.GATE: MarketGroup.COIN,
    Market.KRX: MarketGroup.DOMESTIC_STOCK,
    Market.NASDAQ: MarketGroup.FOREIGN_STOCK,
    Market.NYSE: MarketGroup.FOREIGN_STOCK,
}
"""시장 → 운용 갈래.

⛔ `Market` 에 값을 더할 때 여기도 채운다. 안 채우면 `MarketGroup.of()` 가 **예외를
던진다** — 그것이 의도다. 조용히 넘어가면 새 시장이 비용·통화 취급 없이 섞인다.
"""


class Currency(StrEnum):
    """정산 통화 (spec §9 `instruments.currency`).

    Note:
        USD 자산의 KRW 환산과 **환손익 분리**는 Unified Portfolio 의 책임이다
        (spec §4.18). 여기서는 표기만 한다.
    """

    KRW = "KRW"
    USD = "USD"


class Timeframe(StrEnum):
    """캔들 시간축 (plan D-8 확정).

    주봉(`1w`)은 P3 장투 시점에 추가한다. spec §4.13 이 주봉을 언급하므로
    현재의 부재는 **의도된 미완성**이다.

    Note:
        이 문자열은 P0-8 백필 CLI 의 `--timeframe` 인자와 **같은 값**이어야 한다.
        어긋나면 "문서상 되는데 CLI 는 안 되는" 상태가 된다.

        ⭐ **하위 축(10s~1m)과 30m·8h 는 보기 전용으로 추가했다** (사용자 확정 2026-08-18:
        *"어차피 보는 것만 그런거고, 진입 축으로는 안쓸거야"*). Gate 선물이 전부 준다
        (2026-08-18 실측 — 10s·30s·1m·5m·15m·30m·1h·4h·8h·1d 모두 200).

        ⛔ **진입 축으로 쓰지 않는다.** 5m 단독 진입 폐기 이유가 표본이 아니라 **비용**이고
        (필요 승률 100.7% = 산술적 불가), 봉이 짧아지면 익절 거리가 줄어 그 산수가 더
        나빠진다. 30초·1분은 Gate 의 10,000봉 제한 때문에 과거가 3.5~6.9일뿐이라
        백테스트도 성립하지 않는다.

        ⚠️ **업비트·토스는 이 간격을 다 주지 않는다.** 지원하지 않는 축을 물으면 어댑터가
        명확한 예외를 던진다 — 조용히 빈 봉을 주지 않는다 (절대 규칙 #8).
    """

    S10 = "10s"
    S30 = "30s"
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    H8 = "8h"
    D1 = "1d"


class Bucket(StrEnum):
    """전략 버킷 (spec §4.7).

    버킷은 자금 배분 단위이자 리스크 정책·손익 귀속의 단위다. 버킷 간 이동은
    Position Transition 의 판단을 거치며, 손절 회피 목적의 전환은 금지된다
    (spec §4.8 유형 A).
    """

    SCALP = "scalp"
    SWING = "swing"
    LONGTERM = "longterm"


class Side(StrEnum):
    """포지션 방향 (spec §12.8 — **롱 온리**).

    `SELL` 은 신규 진입이 아니라 **보유 포지션의 청산**만 의미한다. 공매도는
    지원하지 않으며, 하락형 구조물은 청산·회피 근거로만 쓴다.
    """

    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Instrument:
    """거래 대상 종목 (spec §9 `instruments`).

    Attributes:
        market: 거래소·시장.
        symbol: 시장 내 종목 코드. KRX 는 `005930`, 업비트는 `KRW-BTC` 형식이다.
        name: 표시용 종목명.
        asset_type: 자산군.
        currency: 정산 통화.

    Note:
        spec §4.3 의 JSON 예시가 쓰는 `"KRX:005930"` 형태는 이 객체의 **직렬화 표현**이며,
        문자열 조립은 표현 계층의 책임이다.
    """

    market: Market
    symbol: str
    name: str
    asset_type: AssetType
    currency: Currency


@dataclass(frozen=True, slots=True)
class MarketListing:
    """종목 선택 화면에 필요한 한 줄 (Phase 5 §5-6).

    Attributes:
        symbol: 종목 코드 (`KRW-BTC`).
        korean_name: 한글 종목명 (`비트코인`). 없으면 빈 문자열.
        english_name: 영문 종목명.
        last_price: 현재가. 시세를 못 받았으면 None.
        change_rate: 전일 대비 등락률 (부호 있음, 0.012 = +1.2%).
        turnover_24h: 24시간 누적 거래대금. **정렬 키**다.

    Note:
        🔴 `Instrument` 와 다른 타입인 이유: `Instrument` 는 **거래 대상의 정체**이고
        (spec §9 `instruments` 테이블), 이것은 **고르기 위한 한 시점의 스냅샷**이다.
        현재가·등락률을 `Instrument` 에 넣으면 종목 정의가 시세에 따라 달라지는 꼴이 되고,
        그러면 캐시하거나 DB 에 넣는 순간 낡은 가격이 종목의 일부가 된다.

        `None` 을 0 으로 때우지 않는다 — "거래대금 0" 과 "거래대금을 모른다"는 다르다
        (절대 규칙 #8).
    """

    symbol: str
    korean_name: str
    english_name: str
    last_price: Decimal | None
    change_rate: Decimal | None
    turnover_24h: Decimal | None
