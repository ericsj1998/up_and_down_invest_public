"""플래그 점검기 API — 관리자가 **그 시점의 화면**을 부르는 자리.

## 인증이 없다 (지금은 그것이 맞다)

이 API 는 지금 인증을 요구하지 않는다. API 전체가 그렇고, 단일 사용자 로컬 배포이기
때문이다. 여기에만 인증 체계를 세우면 **다른 라우터는 여전히 열려 있는데** 여기만
잠긴, 보안이 아니라 착시를 만든다.

⚠️ 외부에 노출되는 시점(P2)에 API 전체를 한 번에 잠근다. 그때까지 이 라우터는 조회와
계산만 하고 **아무것도 바꾸지 않는다** — 실주문 경로는 `OrderGateway` 가 물리적으로
막고 있고(절대 규칙 #0), 이 라우터는 그 근처에도 가지 않는다.

## 🔴 여기서 나가는 값은 판정에 흘러가지 않는다

`deviation` 은 보기 배수이고, 나중에 붙을 파라미터 편집도 드래프트다. 화면에서 조정한
값이 실거래 설정이 되는 경로는 만들지 않는다 — 만들면 차트가 "보기 좋을 때까지" 밀리고
그것이 눈으로 정답지를 만드는 것이다 (절대 규칙 #11 · §5.6.2).
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from random import SystemRandom
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from updown.analysis.context.guard import AsOfSequence
from updown.analysis.structures.balance import ZIGZAG_ATR_MULTIPLE
from updown.analysis.trend.service import evaluate as trend_evaluate
from updown.apps.api.analysis import MAX_BARS
from updown.common.domain.candle import Candle
from updown.common.domain.capabilities import capabilities_of
from updown.common.domain.instrument import (
    AssetType,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.domain.trend import TrendState
from updown.common.oos import OOS_BOUNDARY
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.inspection import (
    EDITABLE,
    FrameView,
    MomentUnavailableError,
    OverrideError,
    Snapshot,
    apply_overrides,
    available_flags,
    build_frame,
    expand,
    override_defaults,
    pick_moment,
    warmup_for,
)
from updown.orchestration.inspection import setups as setup_layers
from updown.orchestration.inspection.objection import (
    Objection,
    load_all,
    mark_seen,
    save,
    saw_outcome_at,
)
from updown.orchestration.inspection.outcome import forward
from updown.orchestration.inspection.reproduce import (
    count_of,
    search_for_count,
    search_for_line,
)
from updown.orchestration.rule_docs import RuleDocError, list_rule_docs

HTTP_BAD_REQUEST = 400
HTTP_UNAVAILABLE = 503

TREND_WARMUP_BARS = 400
"""추세 판정을 위해 창 **앞으로** 더 받는 봉 수.

🔴 이것이 없으면 `trend/service.evaluate()` 가 `None` 을 낸다 — 197봉으로는 판정이
안 되고, 그러면 국면 게이트를 가진 셋업이 화면에서 **영원히 0건**이 된다.

⚠️ 화면에 그리는 것은 `bars` 그대로다. 이 봉들은 **추세 계산 재료로만** 쓰인다.
as-of 이후는 안 받으므로 미래 참조가 아니다.
"""

router = APIRouter(prefix="/admin", tags=["admin-inspection"])

DEFAULT_TIMEFRAMES = "15m,1h,4h,1d"
"""기본으로 함께 보는 시간축.

사용자 요구는 *"추세 분석은 각 타임라인 봉들에서 다 볼 수 있어야 한다"* 였다. 5m 을
기본에서 뺀 것은 **5m 단독 진입이 폐기**됐기 때문이고(비용 산수), 필요하면 인자로
넣을 수 있다 — 목록에서 지운 것이 아니다.
"""

DEFAULT_BARS = 300
DEFAULT_EARLIEST = datetime(2021, 1, 1, tzinfo=UTC)
"""시점을 뽑을 구간의 시작 기본값.

⚠️ 이 값이 실제 적재 범위보다 이르면 뽑힌 시점에서 봉이 모자랄 수 있다. 그때는 화면이
"봉이 N개뿐이다"라고 **말한다** — 조용히 빈 화면을 주지 않으므로, 다시 뽑으면 된다
(절대 규칙 #8).
"""


def instrument_of(symbol: str, market: Market) -> Instrument:
    """종목 코드에서 `Instrument`.

    Args:
        symbol: 종목 코드.
        market: 시장.

    Returns:
        종목.

    Note:
        🔴 **갈래는 `MarketGroup` 이 정한다** — 여기서 시장을 나열하지 않는다.
        예전에는 `market is Market.UPBIT` 로 코인을 가렸는데, `Market.GATE` 를 더한
        순간 Gate 무기한이 **주식으로 분류**됐다 (실측 2026-08-17: 세션 생성이 500).

        ⚠️ 통화는 여전히 시장별이다. 업비트는 KRW 정산, Gate 는 USDT(=USD 표기)라
        갈래로는 못 가른다 — 둘 다 코인이다.
    """
    group = MarketGroup.of(market)
    coin = group is MarketGroup.COIN
    return Instrument(
        market=market,
        symbol=symbol,
        name=symbol,
        asset_type=AssetType.COIN if coin else AssetType.STOCK,
        currency=capabilities_of(market).quote_currency,  # 능력표 (T269 #6)
    )


def _frames(raw: str) -> tuple[Timeframe, ...]:
    """쉼표로 이어진 시간축 문자열을 파싱한다.

    Args:
        raw: `15m,1h` 형태.

    Returns:
        시간축들. 성긴 것이 뒤로 가도록 정렬하지 않는다 — 화면 순서를 사람이 정한다.

    Raises:
        HTTPException: 모르는 시간축이 있으면 400.
    """
    out: list[Timeframe] = []
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        try:
            out.append(Timeframe(token))
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTP_BAD_REQUEST, detail=f"알 수 없는 시간축: {token}"
            ) from exc
    if not out:
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail="시간축을 하나 이상 골라야 한다")
    return tuple(out)


def rules_config() -> dict[str, bool]:
    """룰 레지스트리의 `rule_id -> enabled`.

    Returns:
        룰 목록.

    Raises:
        HTTPException: 설정을 읽을 수 없으면 503. **빈 목록으로 넘기지 않는다** —
            룰이 하나도 없는 화면은 "룰이 없다"로 읽히고, 그것은 거짓이다.
    """
    try:
        return {doc.rule_id: doc.enabled for doc in list_rule_docs()}
    except RuleDocError as exc:
        raise HTTPException(status_code=HTTP_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/flags")
async def flags() -> dict[str, Any]:
    """점검할 수 있는 플래그 전부 — 화면의 선택 트리가 이것을 읽는다.

    Returns:
        `{groups, flags}`. 각 플래그에 `enabled` 와 파라미터 목록이 붙는다.

    Raises:
        HTTPException: 룰 설정을 읽을 수 없으면 503.

    Note:
        ⭕ 꺼진 룰도 포함한다. **왜 껐는지 눈으로 확인할 자리**가 점검기이므로,
        목록에서 빼면 "과탐지라서 껐다"는 판단을 다시는 검증할 수 없다.
    """
    rules = rules_config()
    docs = {doc.rule_id: doc for doc in list_rule_docs()}
    known = available_flags(rules)
    rows: list[dict[str, Any]] = []
    for flag in known:
        rule_id = flag.id.removeprefix("setup.") if flag.id.startswith("setup.") else None
        doc = docs.get(rule_id) if rule_id else None
        rows.append(
            {
                "id": flag.id,
                "label": flag.label,
                "group": flag.group,
                "note": flag.note,
                # 구조물·지표는 항상 계산된다 — 켜고 끄는 개념이 없다.
                "enabled": True if doc is None else doc.enabled,
                "params": (
                    []
                    if doc is None
                    else [
                        {
                            "name": param.name,
                            "value": param.value,
                            "description": param.description,
                            "inherited": param.inherited,
                        }
                        for param in doc.params
                    ]
                ),
            }
        )
    # 🔴 바꿔 볼 수 있는 값은 **여기 있는 것뿐**이다. 화면이 이 목록으로 입력칸을
    #    만들고, 서버는 목록 밖 키를 거부한다 — 양쪽이 같은 출처를 본다.
    current = override_defaults()
    editable = [
        {
            "key": item.key,
            "flag": item.flag,
            "kind": item.kind,
            "low": str(item.low),
            "high": str(item.high),
            "note": item.note,
            "default": current[item.key],
        }
        for item in EDITABLE
    ]
    return {
        "groups": list(dict.fromkeys(flag.group for flag in known)),
        "flags": rows,
        "editable": editable,
    }


def _as_of(
    raw: str | None, seed: int | None, frames: tuple[Timeframe, ...], bars: int
) -> tuple[datetime, int | None]:
    """시점을 정한다 — 명시값이 있으면 그것, 없으면 시드로 뽑는다.

    Args:
        raw: `2025-06-23T10:00:00Z` 형태. None 이면 랜덤.
        seed: 시드. None 이면 새로 만든다.
        frames: 볼 시간축들.
        bars: 시간축마다 볼 봉 수.

    Returns:
        `(시점, 시드 또는 None)`. 명시 시점이면 시드는 None 이다.

    Raises:
        HTTPException: 시각을 못 읽으면 400, 뽑을 구간이 없으면 400.

    Note:
        시드를 여기서 만드는 것은 결정론 위반이 아니다 (절대 규칙 #5). 금지는 **결정론
        코어**가 시각·난수를 참조하는 것이고, `pick_moment` 는 시드를 인자로 받는 순수
        함수다. 만든 시드는 응답에 실어 그 화면을 다시 부를 수 있게 한다.
    """
    if raw:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTP_BAD_REQUEST, detail=f"시각을 읽을 수 없다: {raw}"
            ) from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if moment >= OOS_BOUNDARY:
            raise HTTPException(
                status_code=HTTP_BAD_REQUEST,
                detail=(
                    f"봉인 구간이다 ({OOS_BOUNDARY.date()} 이후) — "
                    f"눈으로 보는 것도 오염이라 열 수 없다 (§1-0t T10)"
                ),
            )
        return moment, None

    chosen = SystemRandom().randrange(2**31) if seed is None else seed
    try:
        picked = pick_moment(
            seed=chosen,
            earliest=DEFAULT_EARLIEST,
            latest=OOS_BOUNDARY,
            warmup=warmup_for(frames, bars),
            step=max(interval(frame) for frame in frames),
        )
    except MomentUnavailableError as exc:
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail=str(exc)) from exc
    return picked.at, picked.seed


def _parse_overrides(raw: str | None) -> dict[str, str | None]:
    """`키=값,키=값` 을 매핑으로.

    Args:
        raw: 쉼표로 이은 문자열. None 이면 빈 매핑.

    Returns:
        `키 -> 값`.

    Raises:
        HTTPException: `=` 가 없는 토큰이 있으면 400. 조용히 버리면 사용자가 넣은
            값이 안 먹은 채로 화면만 그럴듯해진다.
    """
    if not raw:
        return {}
    out: dict[str, str | None] = {}
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        if "=" not in token:
            raise HTTPException(
                status_code=HTTP_BAD_REQUEST,
                detail=f"파라미터 형식이 아니다: {token!r} — `키=값` 이어야 한다",
            )
        key, _, value = token.partition("=")
        out[key.strip()] = value.strip()
    return out


@router.get("/inspect")
async def inspect(
    symbol: str,
    flags: str,
    timeframes: str = DEFAULT_TIMEFRAMES,
    bars: int = DEFAULT_BARS,
    market: Market = Market.UPBIT,
    seed: int | None = None,
    as_of: str | None = None,
    deviation: float | None = None,
    overrides: str | None = None,
) -> dict[str, Any]:
    """랜덤(또는 지정) 시점에서 고른 플래그를 계산해 돌려준다.

    Args:
        symbol: 종목 코드.
        flags: 플래그 id 와 그룹 이름을 쉼표로 이은 선택. 그룹은 소속 전부로 펼쳐진다.
        timeframes: 함께 볼 시간축들.
        bars: 시간축마다 볼 봉 수.
        market: 시장.
        seed: 시점 시드. 주면 그 화면이 재현된다.
        as_of: 시점을 직접 지정 (UTC). 주면 `seed` 는 무시된다.
        deviation: **보기 전용** ZigZag 편차. `overrides` 에 `zigzag.deviation` 이
            있으면 그쪽이 이긴다.
        overrides: `키=값` 을 쉼표로 이은 **드래프트** 파라미터. 예:
            `trendline.min_touches=4,swing.left_bars=3`.
            ⛔ 설정 파일에 쓰이지 않는다 (§5.6.2 자동조율 금지).

    Returns:
        스냅샷 dict. `seed` 가 실려 있어 같은 화면을 다시 부를 수 있다.

    Raises:
        HTTPException: 인자가 잘못됐으면 400.

    Note:
        🔴 **미래는 한 봉도 안 보인다.** 조회를 `as_of` 로 끊는 것만으로는 부족해서
        (그날 일봉의 종가는 13시간 뒤의 값이다) `AsOfSequence.until` 로 **닫힌 봉만**
        남긴다. 그 규칙의 주인은 `analysis/context/guard.py` 이고 여기서 다시 정의하지
        않는다.
    """
    frames = _frames(timeframes)
    if bars < 1:
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail=f"봉 수가 1 미만이다: {bars}")
    bars = min(bars, MAX_BARS)  # 보안 점검 #8 — `/analysis/frame` 과 같은 상한

    chosen = expand(
        [item.strip() for item in flags.split(",") if item.strip()], available_flags(rules_config())
    )
    if not chosen:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST,
            detail="고른 플래그가 없다 — 없는 id 만 넘겼거나 선택이 비었다",
        )

    moment, used_seed = _as_of(as_of, seed, frames, bars)
    try:
        # ⛔ 판정은 언제나 고정 배수다. 보기 배수는 화면 스케일만 바꾼다.
        view = Decimal(str(deviation)) if deviation and deviation > 0 else ZIGZAG_ATR_MULTIPLE
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"편차를 읽을 수 없다: {deviation}"
        ) from exc

    try:
        tuned = apply_overrides(_parse_overrides(overrides))
    except OverrideError as exc:
        # 🔴 조용히 무시하면 사용자는 자기 값으로 보고 있다고 믿는다 (절대 규칙 #8).
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail=str(exc)) from exc
    if "zigzag.deviation" in tuned.changed:
        view = tuned.deviation

    instrument = instrument_of(symbol, market)
    views: list[FrameView] = []
    loaded: dict[Timeframe, list[Candle]] = {}
    trend_states: dict[Timeframe, TrendState] = {}
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        for frame in frames:
            # 🔴 **거래량 기준선은 창을 넘어선다.** "같은 요일·같은 시각" 표본 4개가
            #    필요해서 코인 15m 이면 4주치(약 2,700봉)가 있어야 한다. 창만 받으면
            #    배수가 전부 None 이라 꼬리 박스가 하나도 안 나오고, 그러면 화면에
            #    뭔가를 보려고 6,000봉을 띄워야 한다 — 눈으로 검증할 수 없는 상태다.
            #
            #    ⇒ **앞으로만 더 받는다.** as-of 이후는 여전히 안 받으므로 미래 참조가
            #      아니고, 화면에 그리는 것은 `visible` 그대로다.
            got = await adapter.get_candles(
                instrument,
                frame,
                moment - interval(frame) * (bars + TREND_WARMUP_BARS),
                moment,
            )
            # 🔴 닫힌 봉만. 형성 중인 봉의 종가는 미래다.
            closed = list(AsOfSequence.until(got, moment, frame))
            visible = closed[-bars:]
            # 🔴 추세는 **워밍업까지** 써서 계산한다. 창만으로는 None 이 나온다.
            history = trend_evaluate(instrument, frame, closed)
            if history.states and history.states[-1] is not None:
                trend_states[frame] = history.states[-1]
            views.append(build_frame(visible, frame, chosen, params=tuned.params, deviation=view))
            # 셋업 탐지가 **같은 봉**을 봐야 한다. 다시 조회하면 그 사이에 데이터가
            # 달라질 수 있고, 그러면 구조물과 셋업이 어긋난 화면이 나온다.
            loaded[frame] = visible

    # 🔴 셋업은 **한 컨텍스트**에서 돌린다. 시간축마다 단일 TF 컨텍스트를 만들면
    #    화면에 그려지는 셋업이 실전이 만드는 것과 달라진다 (`setups.py`).
    run = setup_layers.detect(
        instrument,
        moment,
        loaded,
        {view.timeframe: view.bundle for view in views if view.bundle is not None},
        chosen,
        trend_states,
    )
    views = [view.with_layers(run.layers.get(view.timeframe, [])) for view in views]

    body = Snapshot(
        symbol=symbol,
        as_of=moment,
        seed=used_seed,
        flags=chosen,
        frames=tuple(views),
    ).to_dict()
    # 🔴 무엇을 바꿔서 나온 그림인지 응답이 말한다. 말하지 않으면 며칠 뒤 이 화면을
    #    판정 설정이라 믿게 된다.
    body["overrides"] = tuned.changed
    # 룰이 터졌으면 화면이 안다 — 조용히 빈 결과로 넘기지 않는다 (절대 규칙 #8).
    body["setup_failures"] = run.failures
    return body


DEFAULT_AMOUNT = Decimal(1_000_000)
"""보유 손익을 계산할 기본 금액 (원).

⛔ 이 숫자는 **전략 성과가 아니다.** 그 시점에 사서 창 끝까지 들고 있었으면 얼마인가
일 뿐이고, 진입·손절·익절이 없다. 성과로 인용하면 §1-0s 가 경고한 그 실수다.
"""

DEFAULT_FORWARD_BARS = 120
"""결과를 볼 때 앞으로 볼 봉 수."""


@router.get("/objections")
async def objections() -> dict[str, Any]:
    """쌓인 이의제기 전부 (최신순).

    Returns:
        `{count, objections}`.

    Note:
        ⛔ **집계를 내지 않는다.** "정확도 = 이의제기 안 받은 비율" 같은 지표를 만드는
        순간 눈이 정답지가 된다 (절대 규칙 #11). 여기서 나가는 것은 원본 목록뿐이고,
        그 목록의 쓸모는 **어느 플래그가 반복해서 지적받는가**를 사람이 보는 것이다.
    """
    items = load_all()
    return {"count": len(items), "objections": [item.to_dict() for item in items]}


@router.post("/objections")
async def add_objection(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """이의제기 하나를 기록한다.

    Args:
        payload: `{symbol, as_of, timeframe, flag, kind, shape, original?, comment?,
            overrides?}`.

    Returns:
        저장된 이의제기.

    Raises:
        HTTPException: 필수 항목이 없거나 시각을 못 읽으면 400.

    Note:
        🔴 결과를 **이미 본** 시점이면 `saw_outcome=True` 가 자동으로 붙는다. 사람이
        고르는 값이 아니다 — 자진 신고에 맡기면 기록이 낙관적으로 기운다.
    """
    missing = [
        key for key in ("symbol", "as_of", "timeframe", "flag", "kind") if not payload.get(key)
    ]
    if missing:
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail=f"빠진 항목: {missing}")
    try:
        moment = datetime.fromisoformat(str(payload["as_of"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"시각을 읽을 수 없다: {payload['as_of']}"
        ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if payload["kind"] not in {"added", "removed", "moved"}:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST,
            detail=f"kind 는 added·removed·moved 중 하나여야 한다: {payload['kind']!r}",
        )

    item = Objection(
        symbol=str(payload["symbol"]),
        as_of=moment,
        timeframe=str(payload["timeframe"]),
        flag=str(payload["flag"]),
        kind=payload["kind"],
        shape=payload.get("shape") or {},
        original=payload.get("original"),
        comment=str(payload.get("comment") or ""),
        overrides=payload.get("overrides") or {},
        # 🔴 좌표(`index`)는 **창 안에서 몇 번째**라는 뜻이다. 창 크기를 안 남기면
        #    다시 열 때 같은 번호가 다른 봉을 가리킨다 (`Objection` 의 Note).
        bars=int(payload.get("bars") or 0),
        # 🔴 자진 신고가 아니다. 이미 결과를 본 시점이면 무조건 붙는다.
        saw_outcome=saw_outcome_at(str(payload["symbol"]), moment),
    )
    save(item)
    return item.to_dict()


@router.get("/outcome")
async def outcome(
    symbol: str,
    as_of: str,
    timeframe: str = "1h",
    bars: int = DEFAULT_FORWARD_BARS,
    market: Market = Market.UPBIT,
    amount: float = float(DEFAULT_AMOUNT),
) -> dict[str, Any]:
    """시점 **이후** 실제 전개 — 봉인된 절반.

    Args:
        symbol: 종목 코드.
        as_of: 점검 시점.
        timeframe: 시간축.
        bars: 앞으로 볼 봉 수.
        market: 시장.
        amount: 보유 손익 계산에 쓸 금액.

    Returns:
        `{outcome, sealed_note}`. 봉이 없으면 `outcome` 이 None 이다.

    Raises:
        HTTPException: 인자가 잘못됐으면 400.

    Note:
        🔴 **이것을 열면 기록에 남는다.** 이후 같은 시점에 낸 이의제기는
        `saw_outcome=True` 로 표시된다 — 결과를 알고 그은 선은 항상 잘 맞기 때문이다.

        하드 블록을 걸지 않은 이유는 `outcome.py` 에 적어 뒀다: 막으면 우회하고,
        우회한 기록은 남지 않는다.

        ⛔ `hold_pnl` 은 **전략 성과가 아니다** — 그 시점에 사서 들고 있었으면이다.
    """
    try:
        frame = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"알 수 없는 시간축: {timeframe}"
        ) from exc
    moment, _ = _as_of(as_of, None, (frame,), bars)
    if bars < 1:
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail=f"봉 수가 1 미만이다: {bars}")
    bars = min(bars, MAX_BARS)  # 보안 점검 #8 — `/analysis/frame` 과 같은 상한

    instrument = instrument_of(symbol, market)
    span = interval(frame) * bars
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        got = await adapter.get_candles(instrument, frame, moment, moment + span)
    # 시점 **이후** 봉만. 시점에 걸친 봉은 그때 이미 보던 것이다.
    ahead = [candle for candle in got if candle.ts >= moment]
    result = forward(ahead, timeframe, moment, Decimal(str(amount)))
    # 🔴 **연 사실을 남긴다.** 이것이 없으면 오염 표시가 영원히 안 켜진다 — 처음에
    #    이의제기 목록에서 찾게 만들었다가 순환이라 첫 불이 안 붙는 것을 실측으로
    #    확인했다 (`objection.REVEALS`).
    already = mark_seen(symbol, moment)
    return {
        "symbol": symbol,
        "as_of": moment.isoformat(),
        "outcome": None if result is None else result.to_dict(),
        "sealed_note": (
            "⛔ hold_pnl 은 전략 성과가 아니다 — 그 시점에 사서 창 끝까지 들고 "
            "있었으면이다. 진입·손절·익절이 없고 비용도 안 뺐다"
        ),
        # 이번 호출 **전에** 이미 봤는가. 처음 여는 것과 다시 여는 것은 다르다.
        "already_seen": already,
    }


@router.get("/reproduce")
async def reproduce(
    symbol: str,
    as_of: str,
    flag: str,
    target: int | None = None,
    line: str | None = None,
    timeframe: str = "1h",
    bars: int = DEFAULT_BARS,
    market: Market = Market.UPBIT,
) -> dict[str, Any]:
    """사람이 기대한 개수를 **만들어내는 파라미터가 있는지** 찾는다.

    Args:
        symbol: 종목 코드.
        as_of: 점검 시점.
        flag: 대상 플래그.
        target: 사람이 기대한 도형 수. `line` 이 있으면 무시된다.
        line: 사람이 그린 선 `봉번호,가격,봉번호,가격`. 🔴 **이쪽이 본선이다** —
            개수는 완전히 다른 선으로도 맞기 때문이다.
        timeframe: 시간축.
        bars: 볼 봉 수.
        market: 시장.

    Returns:
        `{current, search}`. `search.reproduced` 가 답이다.

    Raises:
        HTTPException: 인자가 잘못됐으면 400.

    Note:
        이것이 손그림을 **정답지로 만들지 않는 장치**다 (절대 규칙 #11 의 환원 기준).

            찾으면   → 파라미터 문제. 축 후보로 올려 out-of-sample 이 판정 (규칙 #12)
            못 찾으면 → 🔴 정의 자체가 다르다. 알고리즘을 봐야 한다

        ⛔ 찾은 값을 **채택하지 않는다.** 한 장에 맞춘 값은 한 장에서만 이긴다.

        🔴 `line` 을 주면 **좌표로** 잰다 — 겹치는 봉 구간에서 두 선의 평균 가격차를
        ATR 로 나눈 값이고, 0.25xATR 이내면 같은 선으로 본다(추세선 접점 허용 오차와
        같은 기준). 개수 탐색은 "선이 N개"만 맞추므로 엉뚱한 선이 통과할 수 있다.
    """
    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"알 수 없는 시간축: {timeframe}"
        ) from exc
    drawn = _parse_line(line)
    if drawn is None and (target is None or target < 0):
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST,
            detail="`line`(좌표) 또는 `target`(개수) 중 하나는 있어야 한다",
        )
    moment, _ = _as_of(as_of, None, (tf,), bars)

    instrument = instrument_of(symbol, market)
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        got = await adapter.get_candles(instrument, tf, moment - interval(tf) * bars, moment)
    visible = list(AsOfSequence.until(got, moment, tf))
    # 🔴 좌표가 있으면 좌표로 잰다. 개수는 다른 선으로도 맞기 때문이다.
    search = (
        search_for_line(visible, tf, flag, drawn)
        if drawn is not None
        else search_for_count(visible, tf, flag, target or 0)
    )
    return {
        "flag": flag,
        "target": target,
        "matched_by": "line" if drawn is not None else "count",
        "current": count_of(visible, tf, flag),
        "search": search.to_dict(),
    }


def _parse_line(raw: str | None) -> list[tuple[float, float]] | None:
    """`봉번호,가격,봉번호,가격` 을 두 점으로.

    Args:
        raw: 쉼표로 이은 네 숫자. None 이면 None.

    Returns:
        두 점. 없으면 None.

    Raises:
        HTTPException: 숫자가 넷이 아니거나 숫자가 아니면 400. 조용히 버리면 좌표로
            잰 줄 알았는데 개수로 재고 있게 된다.
    """
    if not raw:
        return None
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if len(parts) != 4:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST,
            detail=f"line 은 `봉번호,가격,봉번호,가격` 네 값이어야 한다: {raw!r}",
        )
    try:
        numbers = [float(item) for item in parts]
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"line 에 숫자가 아닌 값이 있다: {raw!r}"
        ) from exc
    return [(numbers[0], numbers[1]), (numbers[2], numbers[3])]
