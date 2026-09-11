"""차트 주문 탭의 **분석 입구** — 지금 시세로 구조물을 그린다 (2026-08-30 신규).

## 왜 `/admin/inspect` 를 못 쓰나

사용자 실측:

    400 {"detail":"봉인 구간이다 (2026-01-01 이후) — 눈으로 보는 것도 오염이라
         열 수 없다 (§1-0t T10)"}

⛔ **그 봉인은 옳고, 건드리지 않는다.** `/admin/inspect` 는 **연구용 점검기**다 —
out-of-sample 구간을 눈으로 보면 그 뒤의 판정이 오염되고, 오염된 판정은 되돌릴 수 없다
(절대 규칙 #11 의 사상).

## 그런데 매매 화면은 다른 일을 한다

여기서 보는 것은 **지금 사고팔 자리**이고, 백테스트 판정에 쓰이지 않는다. 그리고
같은 값을 RUN 상세 차트가 이미 보여 주고 있다 — 새로 여는 창이 아니다.

⇒ 목적이 다르므로 **입구를 나눈다.** 봉인은 연구 경로에 그대로 두고, 매매 경로는
  지금 시세를 본다. 한 입구에 예외를 뚫으면 그 예외가 곧 연구 경로의 구멍이 된다.

## ⚠️ 여기서 판정하지 않는다

구조물·셋업은 **판정이 쓰는 그 코드**로 잰다 (`build_frame`·`setup_layers`). 화면용
계산을 따로 만들면 사람이 보는 그림과 매매가 갈린다 (규칙 #9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from updown.analysis.detectors.rules import load_rules
from updown.analysis.indicators.atr import atr
from updown.analysis.levels import Level, useful
from updown.analysis.plan import propose
from updown.apps.api.quotes import stored_quotes
from updown.common.cache import TtlCache
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Market, Timeframe
from updown.common.logging.setup import get_logger
from updown.common.wire import candle_json
from updown.decision.risk.manual import Confirmed, confirm
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.inspection import setups as setup_layers
from updown.orchestration.inspection.catalog import available_flags, expand
from updown.orchestration.inspection.overlays import (
    DEFAULT_MA_PERIOD,
    adx_overlay,
    ma_layer,
)
from updown.orchestration.inspection.snapshot import FrameView, build_frame

_logger = get_logger("api.analysis")

router = APIRouter(prefix="/analysis", tags=["analysis"])

HTTP_BAD_REQUEST = 400

DEFAULT_BARS = 400
MAX_BARS = 1000
"""한 번에 볼 수 있는 봉 수 상한.

⚠️ 상한이 없으면 화면 한 번이 거래소 호출 여러 번이 된다 — 요율 한도 사고를 겪었다
(2026-08-29: 한도의 156%).
"""

WARMUP_BARS = 200
"""구조물이 창 앞을 봐야 하는 만큼 — 창만 주면 추세·이평이 전부 `None` 이다."""

FORMING_TTL = 0.7
"""진행 중 봉을 거래소에 다시 묻기까지의 최소 간격(초).

🔴 **화면이 얼마나 자주 부르든 조회는 이 간격을 넘지 않는다.** 꼬리가 흔들리는 것을
보려면 초 단위로 봐야 하는데, 그 요청이 그대로 거래소로 나가면 요율 한도에 걸리고
**판정용 조회까지 같이 막힌다** — 이 프로젝트가 IP 밴까지 간 사고가 그 모양이었다.

⚠️ 0.7 인 이유는 화면 폴링(1초)보다 **조금 짧아야** 매 폴링이 새 값을 받기 때문이다.
같거나 길면 두 번에 한 번씩 같은 값이 와서 화면이 멈춘 것처럼 보인다.

⭐ 러너의 `FORMING_TTL` 과 같은 값이다 — 같은 이유로 같은 값을 쓴다.
"""

_FORMING = TtlCache[Candle | None]("analysis.forming", FORMING_TTL)
"""`시장:종목:축 -> (받은 시각, 봉)`.

⚠️ 프로세스 안에만 있고 판마다 갈리지 않는다 — 같은 종목을 두 창에서 봐도 거래소에는
한 번만 나간다. 그것이 이 기억통의 존재 이유다.
"""

OFFERED: tuple[Timeframe, ...] = (
    Timeframe.S10,
    Timeframe.M1,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
)
"""차트 주문이 **고를 수 있게 보여 주는** 축들 (사용자 요구 2026-08-30: 10초·1분 추가).

🔴 **거래소가 다 주는 것은 아니다.** Binance 무기한은 10s·30s 를 안 준다 (T62 P2b).
그래서 이 목록을 화면에 그대로 박지 않고, `supported_frames` 로 **거래소에게 물어서**
줄인 것을 응답에 싣는다 (`frames`) — 어느 축이 있는지는 거래소가 아는 사실이지
화면이 외울 사실이 아니다 (T63 ②).

⛔ **진입 축으로 쓰라는 뜻이 아니다.** 하위 축은 보기 전용이고, 5m 단독 진입을
폐기한 이유는 표본이 아니라 **비용**이다 (필요 승률 100.7~100.9%). 계획을 세울 때는
`required_win_rate` 가 그 산수를 그대로 보여 준다.
"""


@router.get("/frame")
async def frame(
    symbol: str,
    flags: str,
    timeframe: str = "4h",
    market: Market = Market.BINANCE,
    bars: int = DEFAULT_BARS,
    ma_period: int = DEFAULT_MA_PERIOD,
) -> dict[str, Any]:
    """지금 시세로 한 축을 작도한다 — 차트 주문 탭이 읽는다.

    Args:
        symbol: 종목 코드 (`BTC_USDT`).
        flags: 플래그 id 를 쉼표로 이은 선택. 그룹 이름도 받는다 (`expand` 가 편다).
        timeframe: 시간축.
        market: 거래소.
        bars: 볼 봉 수.
        ma_period: 그릴 이동평균 기간. 기본값은 도는 판들이 쓰는 값이다.

    Returns:
        `{timeframe, candles, layers, note}` — RUN 차트와 **같은 모양**이라 화면이
        같은 컴포넌트로 그린다.

    Raises:
        HTTPException: 축·플래그를 못 읽거나 봉이 없으면 400.

    Note:
        🔴 **닫힌 봉만 그린다.** 형성 중인 봉의 종가는 아직 사실이 아니고, 그것으로
        구조물을 잡으면 봉이 닫힐 때마다 그림이 바뀐다.

        ⚠️ **워밍업을 앞으로 더 받는다.** 창만 받으면 추세·이평이 전부 `None` 이라
        화면에 아무것도 안 그려지고, 사람은 그것을 *"고장"* 으로 읽는다.

        ⛔ 봉이 없으면 **빈 화면을 주지 않는다** — 왜 없는지 말한다 (규칙 #8).
    """
    try:
        only = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"모르는 시간축이다: {timeframe}") from exc
    wanted = [item.strip() for item in flags.split(",") if item.strip()]
    if not wanted:
        raise HTTPException(HTTP_BAD_REQUEST, "켤 분석을 하나 이상 골라야 한다")
    # ⚠️ `available_flags` 는 `rule_id -> enabled` 를 받는다 — 설정 객체를 그대로
    #    넘기면 타입이 안 맞고, 그때 조용히 빈 목록이 되면 아무것도 안 그려진다.
    catalog = {name: rule.enabled for name, rule in load_rules().items()}
    chosen = expand(wanted, available_flags(catalog))
    window = max(1, min(int(bars), MAX_BARS))

    now = datetime.now(UTC)
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        # 🔴 **어느 축이 있는지는 거래소가 말한다** (T63 ②). 화면이 목록을 외우면
        #    거래소마다 다른 사실이 한 벌로 굳고, 없는 축을 눌렀을 때 원인이 안 보인다.
        # ⭐ 구체 클래스를 나열하지 않고 **계약**으로 묻는다 (T63 §2b) — 새 거래소는
        #    `QuoteAdapter` 를 지키는 순간 여기서 자동으로 통과한다.
        served: tuple[Timeframe, ...] = (
            tuple(adapter.supported_frames(OFFERED))
            if isinstance(adapter, QuoteAdapter)
            # ⚠️ 계약을 안 지키는 어댑터면 **줄이지 않는다.** 못 주는 축은 아래
            #    `get_candles` 가 빈 결과로 말하고, 그때 이유가 화면에 그대로 나간다.
            else OFFERED
        )
        if only not in served:
            # ⛔ 조용히 다른 축으로 떨어지지 않는다 — 그러면 화면의 축 이름과 그림이
            #   갈리고, 사람은 자기가 고른 축을 보고 있다고 믿는다 (규칙 #8).
            raise HTTPException(
                HTTP_BAD_REQUEST,
                f"{market.value} 는 {timeframe} 봉을 주지 않는다 — "
                f"고를 수 있는 축: {', '.join(item.value for item in served)}",
            )
        try:
            # ⭐ DB 먼저(`StoredCandles`) — 브로커를 직접 부르면 토스 시장은 요청마다 1분 원봉
            #    2만여 개를 새로 받아 1h 400봉이 60초를 넘겼고 화면이 포기했다(2026-09-11 신고 ·
            #    SPY 1h 499). 저장소가 붙어 있으면 빈 곳만 받고, 없으면(시험) 브로커 그대로다.
            rows = await stored_quotes(provider, market).get_candles(
                _instrument(symbol, market),
                only,
                now - interval(only) * (window + WARMUP_BARS),
                now,
            )
        except ValueError as exc:
            # ⭐ 종목 규칙 위반(KRX 에 AAPL · 토스 `TossMappingError` 는 ValueError)은 사람이 고칠
            #    입력이라 400 이다 — 500 으로 흘리면 화면에 이유가 안 보인다 (2026-09-10 신고).
            raise HTTPException(HTTP_BAD_REQUEST, f"{market.value} {symbol}: {exc}") from exc
    # 🔴 마지막은 **형성 중**이라 버린다 — 그 종가로 잡은 구조물은 봉마다 달라진다.
    closed: list[Candle] = list(rows[:-1]) if rows else []
    if not closed:
        raise HTTPException(
            HTTP_BAD_REQUEST,
            f"{market.value} {symbol} 의 {timeframe} 봉을 못 받았다 — 종목 이름을 확인한다",
        )
    visible = closed[-window:]
    view: FrameView = build_frame(visible, only, chosen)
    run = setup_layers.detect(
        _instrument(symbol, market),
        visible[-1].ts,
        {only: visible},
        {only: view.bundle} if view.bundle is not None else {},
        chosen,
        None,
    )
    # 🔴 **RUN 상세와 같은 겹칩선을 그린다** (사용자 2026-08-30: *"이평선은 오히려
    #    있어야 할 것 같은데"* · *"공유할 수 있는 부분들까지 하드코딩할 필요는 없다"*).
    #
    #    전에는 이 두 줄이 `apps/api/walkforward.py` 안에만 있어서, 같은 봉을 보는 두
    #    화면이 **다른 것을 보여 줬다** — 그리고 화면에는 그 이유가 어디에도 없었다.
    #
    # ⚠️ 여기 못 오는 것들이 있다 (`slope_layer`·`vol_layer`·`stance_layer`·게이트 선):
    #    전부 **플레이북·세션을 알아야** 그릴 수 있는데, 분석 화면에는 아직 판이 없다.
    #    억지로 끌고 오면 판 없이는 아무것도 못 그리게 된다.
    view = view.with_layers(
        [*run.layers.get(only, []), ma_layer(view, ma_period), adx_overlay(view)]
    )
    # 🔴 **여기서 거른다** (사용자 요구 2026-08-30: *"막 모든 저항을 찾아주는 것은 옳지
    #    않다"*). 작도는 30개쯤 내는데 그중 쓸 수 있는 것은 서너 개다 — 그 고르는 일을
    #    화면이 대신 해 주지 않으면 아무 일도 안 한 것이다.
    #
    # ⚠️ 거르기와 계획은 **서버가 한다.** 화면이 하면 판정과 다른 규칙이 두 벌이 되고,
    #    그 어긋남은 조용하다 (규칙 #9).
    cost = load_cost_table(DEFAULT_CONFIG_PATH).for_market(market).round_trip_pct
    span = atr([c.high for c in visible], [c.low for c in visible], [c.close for c in visible])[-1]
    picked: list[Level] = []
    made = None
    if span is not None and span > 0 and view.bundle is not None:
        picked = useful(view.bundle.boxes, visible, span=span, round_trip=cost)
        made = propose(picked, visible[-1].close, span=span, round_trip=cost)
    _logger.info(
        "analysis_frame",
        payload={
            "market": market.value,
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": len(visible),
            "flags": list(chosen),
        },
    )
    return {
        "timeframe": only.value,
        # ⭐ **이 거래소가 주는 축들** — 화면은 이것으로 단추를 그린다.
        "frames": [item.value for item in served],
        "note": view.note,
        "bars": len(visible),
        "candles": [candle_json(candle) for candle in visible],
        "layers": [
            {"flag": layer.flag, "note": layer.note, "shapes": list(layer.shapes)}
            for layer in view.layers
        ],
        # ⭐ **거른 레벨** — 원본 수도 같이 낸다. 몇 개를 버렸는지 화면이 말해야
        #    사람이 "이게 전부" 로 읽지 않는다 (규칙 #8).
        "levels": [
            {
                "low": str(item.low),
                "high": str(item.high),
                "support": item.support,
                "touches": str(item.touches),
                "away_pct": f"{item.away_pct:.2f}",
                "merged": str(item.merged),
            }
            for item in picked
        ],
        "levels_raw": str(len(view.bundle.boxes) if view.bundle is not None else 0),
        "atr": "" if span is None else str(span),
        "round_trip_pct": f"{cost * 100:.3f}",
        # 🔴 **계획은 제안이다** — 사람이 고칠 수 있고, 집행값의 SSoT 는 RiskManager 다
        #    (절대 규칙 #4). `null` 이 흔한 것이 정상이다: 비용을 못 갚는 자리가 많다.
        "plan": None
        if made is None
        else {
            "long": made.long,
            "entry": str(made.entry),
            "stop": str(made.stop),
            "first": str(made.first),
            "target": str(made.target),
            "rr": f"{made.rr:.2f}",
            # ⚠️ 화면이 이 값을 **반드시** 같이 보여야 한다 — RR 만 보면 속는다.
            "need_pct": f"{made.need_pct:.1f}",
            "stop_pct": f"{(made.entry - made.stop) / made.entry * 100:.2f}",
            "why": made.why,
        },
    }


@router.get("/tick")
async def tick(
    symbol: str,
    timeframe: str = "1m",
    market: Market = Market.BINANCE,
) -> dict[str, Any]:
    """**지금 만들어지고 있는 봉** 하나 — 꼬리가 실시간으로 흔들리게.

    Args:
        symbol: 종목 코드.
        timeframe: 보고 있는 시간축.
        market: 거래소.

    Returns:
        `{frame, at, bar}`. 아직 못 받았으면 `bar` 가 `null` 이다.

    Raises:
        HTTPException: 축을 못 읽으면 400.

    Note:
        🔴 **사용자 신고 2026-08-30**: *"꼬리가 안보이는게 너무 답답하네. 10초봉에서
        라이브로 보이는 느낌이 아니라, 그냥 10초마다 받아오는 느낌이야."*

        원인은 하나였다 — `/analysis/frame` 은 **마감된 봉까지만** 준다(`rows[:-1]`).
        마감된 봉은 이미 다 그려진 봉이라 꼬리가 자랄 일이 없고, 그래서 축 주기마다
        그림이 통째로 갈리는 것처럼 보인다. RUN 세부에는 이 입구가 이미 있었는데
        (`/walkforward/live/{key}/tick`) **판이 있어야만** 부를 수 있었다.

        🔴 **응답이 작아야 한다.** `/analysis/frame` 은 봉 400개 + 도형을 싣고 오므로
        초 단위로 칠 수 없다 — 이 응답은 봉 **하나**다.

        ⛔ **판정에 쓰이지 않는다.** 미마감 봉이 구조물 계산에 들어가면 같은 상황에서
        매 틱 다른 답이 난다 (절대 규칙 #5). 이 값은 화면에만 간다.

        ⚠️ **없으면 `null` 이다** — 0 이나 마지막 마감 봉으로 채우지 않는다. 못 받은
        것과 "안 움직였다" 는 완전히 다른 사실이고, 화면이 그것을 갈라야 한다.
    """
    try:
        only = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"모르는 시간축이다: {timeframe}") from exc

    key = f"{market.value}:{symbol}:{only.value}"
    kept = _FORMING.fresh(key)  # 값이 None(봉 없음)일 수 있어 fresh() — TtlCache (T269 #3)
    if kept is not None:
        bar = kept[1]
    else:
        span = interval(only)
        now = datetime.now(UTC)
        try:
            async with MarketDataProvider() as provider:
                adapter = provider.adapter_for(market)
                rows = await adapter.get_candles(
                    _instrument(symbol, market), only, now - span * 3, now
                )
            bar = rows[-1] if rows else None
        except Exception as exc:
            # ⚠️ **실패를 기억하지 않는다.** 기억하면 한 번의 요율 제한이 TTL 만큼
            #    이어진다 — 고치려던 것이 원인이 된다 (`speccache` 와 같은 규칙).
            _logger.warning(
                "analysis_tick_unreadable",
                payload={"key": key, "error": str(exc)[:160]},
            )
            return {"frame": only.value, "at": datetime.now(UTC).isoformat(), "bar": None}
        _FORMING.put(key, bar)
    return {
        "frame": only.value,
        "at": datetime.now(UTC).isoformat(),
        "bar": None if bar is None else candle_json(bar),
    }


def _instrument(symbol: str, market: Market) -> Any:
    """종목 객체 — 관리자 화면과 **같은 만드는 법**을 쓴다.

    ⚠️ 여기서 따로 만들면 통화·자산군이 갈리고, 그러면 비용표가 다른 줄을 읽는다.
    """
    from updown.apps.api.admin import instrument_of

    return instrument_of(symbol, market)


@router.post("/validate")
async def validate(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """사람이 고친 계획을 **RiskManager 에 걸어 본다** — 주문은 내지 않는다.

    Args:
        payload: `{entry, stop, first, target, leverage, market, short}`.

    Returns:
        `{ok, stop, moved, reasons, blocked, beta, liq_pct, stop_pct, rr, need_pct}`.
        `stop` 은 **확정된 값**이고, 사람이 낸 값과 다를 수 있다 (`moved`).

    Raises:
        HTTPException: 값을 못 읽으면 400.

    Note:
        🔴 **여기에 판정 로직이 없다.** 전부 `decision/risk/manual.confirm` 이 한다 —
        같은 함수를 주문 경로(`POST /walkforward/live/custom`)도 부른다.

        ⚠️ **그것이 이 함수의 존재 이유다.** 화면이 "낼 수 있다"고 말했는데 주문
        경로가 다른 계산으로 막으면 사람은 왜 막혔는지 알 수 없다. 확정을 두 벌로
        두면 언젠가 두 벌이 갈리고, 갈린 것을 알아채는 자리는 **돈이 걸린 뒤**다
        (절대 규칙 #4).

        ⛔ **주문을 만들지 않는다.** 이 입구는 *"내면 무엇이 되나"* 만 답한다.
    """
    try:
        entry = Decimal(str(payload["entry"]))
        stop = Decimal(str(payload["stop"]))
        first = Decimal(str(payload["first"]))
        # ⚠️ 최종 목표를 안 주면 1차와 같다고 본다 — 사다리 없이 한 번에 나가는 계획이다.
        target = Decimal(str(payload.get("target") or first))
        leverage = Decimal(str(payload.get("leverage", 1)))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"값을 못 읽었다: {exc}") from exc
    market = Market(str(payload.get("market", Market.BINANCE.value)))
    try:
        got = confirm(
            entry=entry,
            stop=stop,
            first=first,
            target=target,
            leverage=leverage,
            short=bool(payload.get("short")),
            round_trip=load_cost_table(DEFAULT_CONFIG_PATH).for_market(market).round_trip_pct,
            settings=load_risk_settings(),
        )
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, str(exc)) from exc
    return as_json(got)


def as_json(got: Confirmed) -> dict[str, Any]:
    """확정 결과를 화면 형식으로 — **주문 경로와 같은 모양**을 쓴다.

    Args:
        got: 확정 결과.

    Returns:
        화면이 그대로 읽는 사전.

    Note:
        ⚠️ 숫자를 문자열로 낸다. `Decimal` 을 float 로 내리면 화면이 반올림한 값을
        다시 보내고 그 값으로 주문이 나간다 — 값이 오가며 **조용히 달라진다**.
    """
    return {
        # ⚠️ `ok` 는 *"확정을 통과했다"* 이지 *"주문이 나갔다"* 가 아니다.
        "ok": got.ok,
        "stop": str(got.stop),
        "moved": got.moved,
        "reasons": list(got.reasons),
        "blocked": list(got.blocked),
        "beta": "" if got.beta is None else str(got.beta),
        "liq_pct": f"{got.liq_pct:.2f}",
        "stop_pct": f"{got.stop_pct:.2f}",
        "rr": f"{got.rr:.2f}",
        "need_pct": f"{got.need_pct:.1f}",
    }
