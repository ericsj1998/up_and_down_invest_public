"""유니버스 심볼 → 도메인 `Instrument` (P0-8-1 · spec §9).

**"어떤 종목을 다루는가"의 단일 출처는 `config/backfill.yml` 의 `universe` 다.**
이 모듈은 그 심볼 목록을 도메인 객체로 만드는 방법만 안다.

처음에는 시드 스크립트가 `Instrument` 목록을 직접 들고 있었는데, 그러면 설정과 스크립트에
같은 목록이 두 벌 생긴다. 갈라지는 순간 "설정에는 있는데 시드에 없는 종목"을 수집하려 들고
`InstrumentNotFoundError` 가 난다 — 원인이 코드가 아니라 **두 목록의 불일치**라서 찾기 어렵다.
"""

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market

#: 업비트 KRW 마켓 심볼 → 표시용 종목명.
#:
#: 이름만 여기 둔다. **어떤 종목을 수집할지는 `config/backfill.yml` 이 정한다** — 이 표에
#: 항목이 있어도 설정에 없으면 수집하지 않는다.
#:
#: 업비트 `/market/all` 의 `korean_name` 을 쓰면 이 표가 필요 없지만, 그러면 시드가
#: 네트워크에 의존한다. 종목명은 거래 판단에 쓰이지 않는 표시값이라 하드코딩이 낫다.
UPBIT_KRW_NAMES: dict[str, str] = {
    "KRW-BTC": "비트코인",
    "KRW-ETH": "이더리움",
    "KRW-XRP": "리플",
    "KRW-SOL": "솔라나",
    "KRW-DOGE": "도지코인",
    "KRW-ADA": "에이다",
}


#: Gate 무기한 선물 심볼 → 표시용 종목명 (T23).
#:
#: 🔴 **판단할 시장의 봉을 받으려고 추가한다.** 지금까지 백테스트는 전부 업비트 현물이었고
#: 주문은 Gate 선물이었다 — *"어느 거래소의 값인지가 곧 그 값의 뜻이다"* 를 어기고 있었다.
#:
#: ⚠️ **Gate 는 과거를 멀리 안 준다** (2026-08-21 실측). 한 번에 2000봉, 최대 10000봉이다:
#:
#: ```
#: 15m   104일      ← 주력 축인데 가장 짧다
#: 1h    416일
#: 4h   1666일
#: 1d    상장일까지
#: ```
#:
#: 🔴 **그 창은 롤링이다.** 오늘 안 받으면 가장 오래된 하루가 내일 사라진다 — 그래서
#: 이 적재는 미룰수록 손해다. 거꾸로 한 번 받아 두면 DB 에 남아 창이 계속 넓어진다.
GATE_USDT_NAMES: dict[str, str] = {
    "BTC_USDT": "비트코인 무기한",
    "ETH_USDT": "이더리움 무기한",
    "XRP_USDT": "리플 무기한",
    "SOL_USDT": "솔라나 무기한",
    "DOGE_USDT": "도지코인 무기한",
    "ADA_USDT": "에이다 무기한",
    "NEAR_USDT": "니어 무기한",  # 실계좌 펀드 바스켓(2026-09) — 라이브 구간 대조용
    "BCH_USDT": "BCH 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "PI_USDT": "PI 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "BIO_USDT": "BIO 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "AVAX_USDT": "AVAX 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "CFX_USDT": "CFX 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "PENGU_USDT": "PENGU 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "PUMP_USDT": "PUMP 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "LTC_USDT": "LTC 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "FARTCOIN_USDT": "FARTCOIN 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "LINK_USDT": "LINK 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "ENA_USDT": "ENA 무기한",  # 102차 사전 우주(2025-08 거래대금 상위 20 · 2026-09-17)
    "WLD_USDT": "WLD 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "PEPE_USDT": "PEPE 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "SUI_USDT": "SUI 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "TAO_USDT": "TAO 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "ARB_USDT": "ARB 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "TRUMP_USDT": "TRUMP 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "XAUT_USDT": "XAUT 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "UNI_USDT": "UNI 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "BR_USDT": "BR 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "BNB_USDT": "BNB 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "HYPE_USDT": "HYPE 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "LSK_USDT": "LSK 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
    "ZEC_USDT": "ZEC 무기한",  # 98차 통발 확장 후보(Gate 거래대금 상위 20 · 2026-09-17)
}


#: 주식 심볼 → `(시장, 종목명, 통화)` — 방향 전환(P1 §1-0j)으로 추가된 검증 대상.
#:
#: 🔴 **시장을 여기서 정하는 이유**: 심볼 형식으로는 NASDAQ 과 NYSE 를 구분할 수 없다.
#: `AAPL` 이 어느 거래소인지는 형식이 아니라 **사실**이고, 사실은 추측하지 않고 적어 둔다.
#: 잘못 적으면 `MarketDataProvider` 가 어댑터를 잘못 고르는 게 아니라(토스가 셋 다 담당)
#: 나중에 마켓 캘린더·거래시간·수수료가 어긋난다.
STOCK_SPECS: dict[str, tuple[Market, str, Currency]] = {
    "005930": (Market.KRX, "삼성전자", Currency.KRW),
    "000660": (Market.KRX, "SK하이닉스", Currency.KRW),
    "AAPL": (Market.NASDAQ, "애플", Currency.USD),
    "NVDA": (Market.NASDAQ, "엔비디아", Currency.USD),
    "MSFT": (Market.NASDAQ, "마이크로소프트", Currency.USD),
    # ⭐ 2026-08-15 추가 — 대형·고유동성 5종.
    #
    # 고른 기준은 **시가총액 + 거래대금**이다. 유동성이 낮으면 슬리피지가 지배해서
    # 셋업 성과와 체결 비용을 못 가르고, 그러면 측정이 전략을 재는 것이 아니라
    # 호가창을 재는 것이 된다 (`config/costs.yml` 이 "가장 비싼 종목"을 채택값으로
    # 삼은 것과 같은 이유).
    #
    # ⚠️ 전부 **미국 대형 기술주**라 서로 상관이 높다. 코인 6종과 같은 한계이며,
    #    "독립 표본 8개"가 아니라는 것을 알고 쓴다 (§12.9).
    "GOOGL": (Market.NASDAQ, "알파벳 A", Currency.USD),
    "AMZN": (Market.NASDAQ, "아마존", Currency.USD),
    "META": (Market.NASDAQ, "메타", Currency.USD),
    "TSLA": (Market.NASDAQ, "테슬라", Currency.USD),
    # 🔴 오라클은 **NYSE** 다. 형식으로 시장을 추측하면 여기서 틀린다 —
    #    티커 모양이 나스닥 종목과 구분되지 않기 때문이다.
    "ORCL": (Market.NYSE, "오라클", Currency.USD),
}


class UnknownSymbolError(ValueError):
    """이름을 모르는 심볼이다.

    Note:
        조용히 심볼 문자열을 이름으로 쓰지 않는다. 오타(`KRW-BTV`)가 그대로 종목명이 되어
        DB 에 들어가면, 나중에 그것이 오타인지 실제 종목인지 구분할 수 없다 (spec §7).
    """


def to_instrument(symbol: str) -> Instrument:
    """유니버스 심볼을 `Instrument` 로 만든다.

    Args:
        symbol: 업비트 `KRW-BTC` 형식, KRX 6자리 숫자(`005930`), 또는 미국 티커(`AAPL`).

    Returns:
        도메인 종목.

    Raises:
        UnknownSymbolError: 등록되지 않은 심볼.

    Note:
        코인은 `KRW-` 접두사로 알아보고, 나머지는 `STOCK_SPECS` 에서 찾는다.
        **형식으로 시장을 추측하지 않는다** — 6자리 숫자가 KRX 라는 것은 규칙이지만
        `AAPL` 이 NASDAQ 인지 NYSE 인지는 형식으로 알 수 없다 (`STOCK_SPECS` 주석).

        업비트는 KRW 마켓만 받는다. BTC 마켓(`BTC-ETH`)은 정산 통화가 BTC 라
        `Currency` 에 대응 값이 없고, 원화 계좌 기준 손익 계산도 달라진다.
    """
    if symbol.startswith("KRW-"):
        name = UPBIT_KRW_NAMES.get(symbol)
        if name is None:
            raise UnknownSymbolError(
                f"이름을 모르는 심볼이다: {symbol!r}. "
                "marketdata/ingest/universe.py 의 UPBIT_KRW_NAMES 에 추가하라"
            )
        return Instrument(
            market=Market.UPBIT,
            symbol=symbol,
            name=name,
            asset_type=AssetType.COIN,
            currency=Currency.KRW,
        )

    if symbol.endswith("_USDT"):
        # ⭐ **접미사로 알아본다.** 코인의 `KRW-` 와 같은 규칙이고, 주식처럼 거래소를
        #   추측해야 하는 문제가 없다 — `_USDT` 무기한은 Gate 하나만 다룬다.
        perp = GATE_USDT_NAMES.get(symbol)
        if perp is None:
            raise UnknownSymbolError(
                f"이름을 모르는 심볼이다: {symbol!r}. "
                "marketdata/ingest/universe.py 의 GATE_USDT_NAMES 에 추가하라"
            )
        return Instrument(
            market=Market.GATE,
            symbol=symbol,
            name=perp,
            asset_type=AssetType.COIN,
            # ⚠️ 정산 통화가 USDT 다. `Currency` 에 USDT 가 없어 USD 를 쓴다 —
            #    라이브 경로(`apps/api/exchange.py`)가 이미 같은 선택을 했으므로 맞춘다.
            currency=Currency.USD,
        )

    spec = STOCK_SPECS.get(symbol)
    if spec is None:
        if "-" in symbol:
            raise UnknownSymbolError(
                f"KRW 마켓만 지원한다: {symbol!r}. BTC 마켓은 정산 통화가 달라 "
                "Currency 확장이 먼저 필요하다"
            )
        raise UnknownSymbolError(
            f"이름을 모르는 심볼이다: {symbol!r}. "
            "marketdata/ingest/universe.py 의 STOCK_SPECS 에 추가하라"
        )
    market, name, currency = spec
    return Instrument(
        market=market,
        symbol=symbol,
        name=name,
        asset_type=AssetType.STOCK,
        currency=currency,
    )


def resolve_universe(symbols: tuple[str, ...]) -> tuple[Instrument, ...]:
    """심볼 목록을 `Instrument` 목록으로 만든다.

    Args:
        symbols: `BackfillScope.universe` 값.

    Returns:
        도메인 종목 목록, 입력 순서 유지.

    Raises:
        UnknownSymbolError: 하나라도 해석할 수 없는 경우.

    Note:
        **하나라도 실패하면 전체를 거부한다.** 일부만 시드하면 나머지 종목의 백필이
        `InstrumentNotFoundError` 로 죽는데, 그때는 원인이 시드 시점에서 멀어져 있다.
    """
    return tuple(to_instrument(symbol) for symbol in symbols)
