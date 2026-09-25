"""원장 계획 → 브로커 주문 사상 (T13 · spec §4.10).

## 🔴 원장에는 수량이 없다

`TradeRecord` 는 가격·손절·익절만 들고 손익을 **퍼센트로** 계산한다
(`gain_pct = (raw - cost) * leverage`). 즉 **매매마다 자본 전액**을 쓴다고 가정한다.

주문에는 수량이 필요하다. 그 수량을 여기서 만들어야 하는데, 만드는 순간 두 가지가
걸린다.

1. **절대 규칙 #4** — 손절/익절/수량의 SSoT 는 RiskManager 다. 집행은 값을 못 바꾼다.
   그런데 워크포워드 경로에는 RiskManager 가 배선돼 있지 않다.
2. **거래소 제약** — 계약은 정수이고 최소·최대가 있다. 반올림하면 원장이 가정한
   "자본 전액" 과 **어긋난다.**

⇒ 그래서 수량 계산을 **이 파일 하나에 모으고**, 어긋난 양을 `slippage_from_rounding`
  으로 되돌려 준다. 숨기면 페이퍼 성적과 백테스트 성적이 다른 이유를 영원히 모른다
  (절대 규칙 #8 · §1-0s 관측 규약).

⚠️ **이것은 RiskManager 가 아니다.** RiskManager 가 배선되면 이 계산은 거기로 올라가고
   이 파일은 사상만 남는다. 그때까지의 임시 대역임을 이름과 문서에 남긴다.

## 멱등키

규격이 정해져 있다 (§4.10 · §9):

    idempotency_key = f"{root}:{order_kind}:{leg_index}"

⛔ 승인 단위로 하나만 두면 브로커가 2·3번 레그를 중복으로 보고 거부한다.
   `order_kind` 까지 넣어야 진입 0번과 익절 1단계의 키 충돌도 막힌다.

## 체인 뿌리

`approved_order_id` 는 `ApprovedOrder` 의 id 인데, 승인 게이트는 **범위 밖**이다(T00).
워크포워드에서 그 자리를 채우는 것은 `TradeRecord.trade_id` 다 — 원장에서 이 계획을
가리키는 유일한 id 이고, 로그 체인이 끊기지 않는다.
"""

from decimal import ROUND_DOWN, Decimal

from updown.common.domain.instrument import Instrument, Side
from updown.common.domain.order import OrderKind, OrderRequest, OrderType
from updown.orchestration.walkforward.ledger import Direction, TradeRecord

RUN_CHARS = 6
"""주문 이름에 넣는 **판 표식** 길이 (T18 ⑤)."""

TRADE_CHARS = 8
"""주문 이름에 넣는 **매매 id** 길이 (원본은 12).

⚠️ 12 를 다 넣으면 30자를 넘겨 해시로 접히고, 접힌 이름은 사람이 못 읽는다.
8 이면 한 판 안에서 충돌 확률이 사실상 0 이다 (16^8 = 43억).
"""

KIND_CODE = {
    "entry": "en",
    "take_profit": "tp",
    "stop_loss": "sl",
    "close": "cl",
}
"""주문 종류의 두 글자 코드 — **이름 길이를 위해서만** 쓴다 (T18 ⑤).

⛔ 도메인 값(`OrderKind`)을 바꾸지 않는다. 이것은 브로커로 나가는 이름의 축약이고,
도메인이 브로커 제약에 맞춰 휘면 다른 브로커를 붙일 때 또 휘어야 한다.
"""


def run_tag(run: str) -> str:
    """판 id 를 주문 이름에 넣을 **6자 표식**으로 (T18 ⑤).

    Args:
        run: 판 id (`livecd3642fc`).

    Returns:
        뒤 6자 (`3642fc`). 비었으면 빈 문자열.

    Note:
        🔴 **뒤에서 자른다.** 앞은 `live` 접두라 판마다 같아서, 앞 6자를 쓰면 모든 판이
        `livecd` 처럼 겹친다 — 표식의 목적이 통째로 사라진다.
    """
    return run[-RUN_CHARS:] if run else ""


def order_root(record_id: str, run: str = "") -> str:
    """멱등키의 뿌리 — 판이 있으면 **판을 앞에 둔다** (T18 ⑤).

    Args:
        record_id: 매매 id.
        run: 판 id. 없으면 옛 형식 그대로다.

    Returns:
        `3642fc-863ce363` 또는 `863ce36336a2`.

    Note:
        🔴 **거래소 콘솔이 어느 판의 주문인지 알아야 한다** (사용자 요구 2026-08-19:
        *"거래소 콘솔에서도 RUN 기준으로 매매를 분류해주는 작업이 좀 필요할 것 같네"*).
        DB 매핑보다 이쪽이 싸고 안전하다 — 거래소 자체가 판을 들고 있게 된다.

        자릿수 (`t-` 접두 포함):

        ```
        옛   t-<trade 12>-take_profit-1   = 28자   ← 한계 30 에 2자 남음
        새   t-<run 6>-<trade 8>-tp1      = 21자   ← 9자 여유
        ```

        ⚠️ **옛 형식으로 나간 주문이 아직 떠 있을 수 있다.** 읽을 때는 둘 다 받아들인다
        (`live_runner.adopted_id`). 새로 낼 때만 새 형식을 쓴다.

        🔴 **그래도 `ao-` 는 못 엮는다.** 조건부가 발동해 Gate 가 만든 주문이라 `text`
        가 우리 것이 아니다 — 어느 방법으로도 이름으로는 못 잇는다.
    """
    tag = run_tag(run)
    if not tag:
        return record_id
    return f"{tag}-{record_id[:TRADE_CHARS]}"


def order_key(record_id: str, kind: str, leg: int, run: str = "") -> str:
    """멱등키 하나 — `{뿌리}:{종류}:{다리}` (§4.10 · §9 규격 그대로).

    Args:
        record_id: 매매 id.
        kind: `OrderKind` 값.
        leg: 레그 번호.
        run: 판 id.

    Returns:
        멱등키.

    Note:
        ⛔ **규격을 바꾸지 않는다.** 콜론은 주문 단위를 가르는 구분자이고, 바뀐 것은
        `root` 안쪽과 `kind` 의 축약뿐이다.
    """
    return f"{order_root(record_id, run)}:{KIND_CODE.get(kind, kind)}:{leg}"


class OrderMappingError(ValueError):
    """계획을 주문으로 옮길 수 없다.

    Note:
        조용히 0 수량 주문을 만들지 않는다. 0 은 거래소가 거부하거나(400) 통과해도
        아무 일이 안 일어나는데, 원장은 진입한 것으로 적는다 — 그 불일치가 이 예외가
        막는 실패다.
    """


MAX_SIZE_MULT = Decimal("1.5")
"""탐지기 **크기 승수의 천장** — S3 기울기가 0.5 / 1.0 / 1.5 를 낸다 (T279 83차).

`round_to_nearest` 의 안전 상한을 만드는 데만 쓴다: 올림한 계약이 *"이 매매법이 가장 크게 살 때"*
보다 커지면 안 된다. 승수가 더 큰 탐지기가 생기면 이 상한이 **더 자주 걸릴 뿐**이라 실패 방향이
안전하다(덜 사게 된다).
"""


def contracts_for(
    equity: Decimal,
    leverage: Decimal,
    price: Decimal,
    multiplier: Decimal,
    *,
    size_min: int = 1,
    size_max: int | None = None,
    round_to_nearest: bool = False,
    max_leverage: Decimal | None = None,
) -> int:
    """자본으로 살 수 있는 **계약 수**.

    Args:
        equity: 굴리는 자본 (정산 통화 · USDT).
        leverage: 레버리지 배율.
        price: 진입가.
        multiplier: 계약 승수 — BTC_USDT 는 `0.0001` (1계약 = 0.0001 BTC).
        size_min: 거래소 최소 계약 수 (`order_size_min`).
        size_max: 거래소 최대 계약 수. None 이면 상한 없음.
        round_to_nearest: 참이면 **반올림**(0.5계약 이상이면 올린다). 기본 거짓 = 내림.
        max_leverage: `round_to_nearest` 의 안전 상한 — 올림한 결과의 실제 배율이 이 값을
            넘으면 올리지 않는다. None 이면 상한 없음(올림을 켤 때는 주는 것이 옳다).

    Returns:
        계약 수 (정수).

    Raises:
        OrderMappingError: 값이 0 이하이거나, 최소 계약 수를 못 채우는 경우.

    Note:
        🔴 **기본은 내림이다.** 올리면 자본보다 큰 포지션이 열리고, 격리 마진에서 그것은
        청산선이 계획보다 가까워진다는 뜻이다 — 반대 방향 오차가 계좌를 태운다.

        ⭐ **그런데 자본이 작으면 내림의 대가가 크다** (157차 · 2026-09-19 실측). 계약 하나의
        명목이 종목마다 달라(Gate: DOGE 0.87 · SOL 111.55 USDT · 128배) 자리 예산이 작으면
        비싼 계약을 **하나도 못 산다**. 실계좌 373 USDT 기준:

            내림    배율 6.2% 손실 · 신호의 2.8% 는 아예 건너뜀
            반올림  배율 0.1% 손실 · 건너뛰는 신호 0%

        자본 1,000 이상에서는 둘 다 1% 안쪽이라 차이가 없다(156차) — 작은 자본에서만 값이 있다.

        🔴 **그래서 상한이 필요하다.** 올림이 커지는 자리는 **원래 작게 사려던 자리**(낙폭
        브레이크가 절반으로 줄인 칸)라 절대 배율이 낮아 안전하다. 하지만 **크게 사려던 자리**에서
        한 계약을 얹으면 청산선이 실제로 가까워진다(6배 → 7.2배면 청산 거리 16% → 12%).
        `max_leverage` 가 그 경우를 막는다 — *"선언한 배율 봉투를 넘지 않는다"* 가 규칙이다.

        🔴 **최소를 못 채우면 예외다.** 0 을 돌려주면 원장은 진입한 것으로 적고
        거래소에는 아무것도 없는 상태가 된다. 자본이 모자란 것은 **사건**이다.

        ⚠️ 승수를 상수로 박지 않는다 — 계약마다 다르고 거래소가 바꿀 수 있다
        (`GateAdapter.contract_spec` 에서 읽는다).
    """
    for name, value in (("equity", equity), ("leverage", leverage), ("price", price)):
        if value <= 0:
            raise OrderMappingError(f"{name} 가 {value} 다 — 수량을 계산할 수 없다")
    if multiplier <= 0:
        raise OrderMappingError(f"계약 승수가 {multiplier} 다 — 0 이하면 수량이 무한이 된다")

    notional = equity * leverage
    per = price * multiplier  # 계약 1개의 명목
    exact = notional / per
    size = int(exact.to_integral_value(rounding=ROUND_DOWN))
    if round_to_nearest and exact - size >= Decimal("0.5"):
        # 올림했을 때 실제로 몇 배가 되나 — 봉투를 넘으면 올리지 않는다.
        lifted = (size + 1) * per / equity
        if max_leverage is None or lifted <= max_leverage:
            size += 1
    if size < size_min:
        raise OrderMappingError(
            f"계약 수 {size} 가 최소 {size_min} 에 못 미친다 "
            f"(자본 {equity} x {leverage}배 / 가격 {price} / 승수 {multiplier}). "
            "자본이 모자란 것은 사건이다 — 0 으로 넘기면 원장만 진입한 것으로 적는다"
        )
    if size_max is not None and size > size_max:
        size = size_max
    return size


def can_size(
    equity: Decimal,
    leverage: Decimal,
    price: Decimal,
    multiplier: Decimal,
    *,
    size_min: int = 1,
    round_to_nearest: bool = False,
    max_leverage: Decimal | None = None,
) -> bool:
    """`contracts_for` 가 계약을 **만들 수 있나** — 던지지 않고 묻는다 (T286 · 2026-09-19).

    Args:
        equity: 이 자리에 쓸 증거금.
        leverage: 배율.
        price: 진입가.
        multiplier: 계약 승수.
        size_min: 거래소 최소 계약 수. 0 으로 오는 종목이 있어 **1 을 하한으로 본다**.
        round_to_nearest: `contracts_for` 에 넘길 값과 **같아야 한다**.
        max_leverage: 위와 같다.

    Returns:
        계약이 1개 이상(그리고 `size_min` 이상) 나오면 True.

    Note:
        🔴 **호출자가 원장을 쓰기 전에 물어야 한다.** `contracts_for` 의 예외는 옳지만,
        라이브 경로에서는 그 예외가 **원장에 "보유중" 이 써진 뒤**에 난다 — 주문은 없고
        기록만 남아 자가 점검이 고아로 올린다. 값을 미리 물어 그 거래를 **안 하면** 된다.

        🔴 **두 함수가 같은 답을 내야 한다.** 인자가 갈리면 가드가 "못 산다" 고 접은 자리를
        `contracts_for` 는 살 수 있었거나(진입을 잃는다) 그 반대가 된다(가드가 무의미해진다).
        그래서 계산을 `contracts_for` 에 **위임**한다 — 두 벌로 두면 조용히 갈라진다.

        ⚠️ Gate 는 일부 종목의 `order_size_min` 을 **0 으로 준다**. 0계약은 주문이 아니므로
        `max(size_min, 1)` 로 본다 — 실측(2026-09-19)에서 SOL 이 그렇게 왔다.
    """
    if equity <= 0 or leverage <= 0 or price <= 0 or multiplier <= 0:
        return False
    try:
        contracts_for(
            equity,
            leverage,
            price,
            multiplier,
            size_min=max(size_min, 1),
            round_to_nearest=round_to_nearest,
            max_leverage=max_leverage,
        )
    except OrderMappingError:
        return False
    return True


def rounding_drift_pct(
    contracts: int,
    equity: Decimal,
    leverage: Decimal,
    price: Decimal,
    multiplier: Decimal,
) -> Decimal:
    """반올림 때문에 원장 가정과 어긋난 비율.

    Args:
        contracts: 실제 주문 계약 수.
        equity: 굴리는 자본.
        leverage: 레버리지.
        price: 진입가.
        multiplier: 계약 승수.

    Returns:
        `(실제 노셔널 - 계획 노셔널) / 계획 노셔널`. 내림했으므로 보통 음수다.

    Raises:
        OrderMappingError: 계획 노셔널이 0 이하다 — 비율을 낼 수 없다.

    Note:
        🔴 **원장은 자본 전액을 가정한다.** 계약이 정수라 실제로는 조금 모자라게 사고,
        그만큼 페이퍼 손익이 원장 계산보다 작다.

        ⚠️ 이 값을 재는 이유는 **페이퍼와 백테스트가 다를 때 그 차이가 어디서 왔는지**
        가리기 위해서다. 안 재면 "라이브가 백테스트보다 나쁘다" 만 남고, 그것이 전략
        탓인지 반올림 탓인지 못 가른다 (§1-0s 관측 규약).

        ⭐ BTC_USDT 는 1계약 = 0.0001 BTC ≈ $6 이라 1천만원 규모에서 드리프트가
        0.1% 미만이다. 하지만 자본이 작거나 승수가 큰 계약에서는 커진다.
    """
    planned = equity * leverage
    actual = Decimal(contracts) * price * multiplier
    if planned <= 0:
        raise OrderMappingError("계획 노셔널이 0 이하다 — 비율을 낼 수 없다")
    return (actual - planned) / planned


def _entry_side(direction: Direction) -> Side:
    """진입 방향 → 매수/매도.

    Args:
        direction: 롱인가 숏인가.

    Returns:
        롱 진입은 `BUY`, 숏 진입은 `SELL`.

    Note:
        🔴 **숏 진입이 `SELL` 이다.** 절대 규칙 #10 개정(양방향 허용) 전에는 `SELL` 이
        "보유 청산" 만 뜻했다 — 그 전제로 읽으면 숏 진입을 청산으로 착각한다.
        구분은 `order_kind` 가 한다 (`ENTRY` 인가 `CLOSE` 인가).
    """
    return Side.BUY if direction is Direction.LONG else Side.SELL


def _exit_side(direction: Direction) -> Side:
    """청산 방향 — 진입의 반대다."""
    return Side.SELL if direction is Direction.LONG else Side.BUY


def limit_entry_order(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    price: Decimal,
    *,
    leg: int = 0,
    run: str = "",
    post_only: bool = False,
) -> OrderRequest:
    """진입 주문 — **지정가** (T19 ④).

    Args:
        record: 원장 계획.
        instrument: 대상 종목.
        contracts: 이 다리의 계약 수.
        price: 지정가.
        leg: 다리 번호 — 멱등키를 가른다.
        run: 판 표식 (T18 ⑤).
        post_only: 참이면 **크로스 시 거부**(Gate poc) — 메이커 요율 보장 (T60 축④).
            일반 지정가는 호가를 넘는 순간 테이커로 체결되므로, 이 플래그 없이는
            지정가라도 수수료 이득이 없다. 거부되면 다음 판정에서 재시도한다.

    Returns:
        진입 주문.

    Raises:
        OrderMappingError: 계약 수가 0 이하이거나 가격이 0 이하인 경우.

    Note:
        🔴 **시장가와 다른 함수다.** 돌파는 *"돌파된 그 순간 최대한 빨리"* 라 시장가가
        맞고(순간), 지지 반등은 *"하단 레벨 안에서 받는다"* 라 지정가가 맞다(자리).
        한 함수에 스위치를 달면 어느 규칙으로 나간 주문인지 기록에서 안 보인다.

        ⭐ **메이커다.** 진입 다리가 테이커(0.05%)에서 메이커(0.02%)로 바뀐다 —
        2026-08-19 실측에서 손실의 80.6% 가 회전 비용이었고, 그중 진입 쪽이 이 값이다.

        ⚠️ **`reduce_only` 가 아니다.** 진입이므로 포지션을 늘린다 — 어댑터가
        `order_kind` 로 판별하며, 여기서 손대면 진입이 조용히 감축이 된다.
    """
    if contracts <= 0:
        raise OrderMappingError(f"계약 수가 {contracts} 다 — 진입할 수 없다")
    if price <= 0:
        raise OrderMappingError(f"지정가가 {price} 다 — 걸 수 없다")
    return OrderRequest(
        instrument=instrument,
        side=_entry_side(record.direction),
        order_kind=OrderKind.ENTRY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(contracts),
        price=price,
        idempotency_key=order_key(record.trade_id, OrderKind.ENTRY.value, leg, run),
        approved_order_id=record.trade_id,
        leg_index=leg,
        revision_id=None,
        post_only=post_only,
    )


def entry_order(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    *,
    run: str = "",
) -> OrderRequest:
    """진입 주문 — **시장가**.

    Args:
        record: 원장 계획.
        instrument: 대상 종목.
        contracts: 계약 수.
        run: 판 표식 (T18 ⑤). 주면 주문 이름 앞에 6자가 붙어 거래소 콘솔에서
            판을 가를 수 있다. 비우면 옛 형식 그대로다.

    Returns:
        진입 주문.

    Raises:
        OrderMappingError: 계약 수가 0 이하인 경우.

    Note:
        🔴 **시장가다.** 0.1 의 진입 규칙이 *"돌파된 시점 그때 그냥 최대한 빨리 매수"*
        이므로 지정가로 걸면 그 규칙이 아니다 (사용자 확정 · 플레이북 문서 ⑪).

        ⚠️ 그래서 진입 다리는 **늘 테이커**다 (수수료 0.075%). 청산 다리는 지정가라
        메이커(-0.01%)이고, 비용표는 지금 보수적으로 전부 테이커로 잡고 있다.
    """
    if contracts <= 0:
        raise OrderMappingError(f"계약 수가 {contracts} 다 — 진입할 수 없다")
    return OrderRequest(
        instrument=instrument,
        side=_entry_side(record.direction),
        order_kind=OrderKind.ENTRY,
        order_type=OrderType.MARKET,
        quantity=Decimal(contracts),
        # 🔴 시장가는 가격이 None 이다. 계획 진입가를 넣으면 지정가가 되고, 안 채워지면
        #    원장만 진입한 것으로 적는다.
        price=None,
        idempotency_key=order_key(record.trade_id, OrderKind.ENTRY.value, 0, run),
        approved_order_id=record.trade_id,
        leg_index=0,
        # 최초 승인대로 나가는 주문은 개정이 없다.
        revision_id=None,
    )


def resize_order(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    *,
    grow: bool,
    seq: int,
    run: str = "",
) -> OrderRequest:
    """재레버 — 보유 계약을 목표 노출로 **늘리거나**(진입 방향) **줄인다**(reduce_only).

    Args:
        record: 보유 중인 원장 기록.
        instrument: 대상 종목.
        contracts: 조정할 계약 수 (절대값).
        grow: 참이면 늘림(ENTRY · 일반 주문), 거짓이면 줄임(CLOSE · reduce_only).
        seq: 봉 시각 기반 순번 — **재시작해도 같은 봉이면 같은 키**라 서버 멱등이 중복을
            막는다 (규칙 #6).
        run: 판 표식.

    Returns:
        시장가 조정 주문.

    Raises:
        OrderMappingError: 계약 수가 0 이하인 경우.

    Note:
        0.8.0 (설계 B 개정): 격자(봉마다 재레버)와 라이브(계약 고정)의 노출 의미론
        갭을 닫는 주문이다. 늘림이 ENTRY 인 이유는 reduce_only 면 거래소가 거부하기
        때문이고, 줄임이 CLOSE 인 이유는 reduce_only 로 포지션 초과를 막기 위해서다.
    """
    if contracts <= 0:
        raise OrderMappingError(f"계약 수가 {contracts} 다 — 조정할 것이 없다")
    return OrderRequest(
        instrument=instrument,
        side=_entry_side(record.direction) if grow else _exit_side(record.direction),
        order_kind=OrderKind.ENTRY if grow else OrderKind.CLOSE,
        order_type=OrderType.MARKET,
        quantity=Decimal(contracts),
        price=None,
        idempotency_key=order_key(record.trade_id, "resize", seq, run),
        approved_order_id=record.trade_id,
        leg_index=0,
        revision_id=f"{record.trade_id}:resize{seq}",
    )


def add_order(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    *,
    run: str = "",
) -> OrderRequest:
    """불타기 — 열린 포지션과 **같은 방향**으로 한 번 더 싣는 시장가 주문 (T308 ⑤).

    Args:
        record: 보유 중인 원장 기록(불타기 판정이 적힌 것).
        instrument: 대상 종목.
        contracts: 추가 계약 수.
        run: 판 표식.

    Returns:
        시장가 추가 주문.

    Raises:
        OrderMappingError: 계약 수가 0 이하인 경우.

    Note:
        ⭐ 멱등키는 매매마다 **하나**(`:add:0`)다 — 한 매매에 불타기는 한 번뿐이라, 재시작 · 중복
        걸음에도 거래소가 같은 이름을 두 번 받지 않는다(규칙 #6). 진입과 같은 ENTRY 라
        `reduce_only` 가 아니다 — 늘리는 주문이다.
    """
    if contracts <= 0:
        raise OrderMappingError(f"계약 수가 {contracts} 다 — 더할 것이 없다")
    return OrderRequest(
        instrument=instrument,
        side=_entry_side(record.direction),
        order_kind=OrderKind.ENTRY,
        order_type=OrderType.MARKET,
        quantity=Decimal(contracts),
        price=None,
        idempotency_key=add_key(record.trade_id, run),
        approved_order_id=record.trade_id,
        leg_index=0,
        revision_id=f"{record.trade_id}:add",
    )


def add_key(record_id: str, run: str = "") -> str:
    """불타기 주문의 멱등키 — 재시작 뒤 거래소에서 이 이름으로 체결을 찾는다 (T308 ⑤).

    Args:
        record_id: 매매 id.
        run: 판 id.

    Returns:
        `{뿌리}:add:0`.
    """
    return order_key(record_id, "add", 0, run)


SENTINEL_RR = Decimal(50)
"""이 배수(리스크 대비) 이상의 익절 목표는 **주문하지 않는다** (2026-08-26 실사고).

추세 추종 계열은 `RIDE_R = 100` — "익절 사실상 없음, 청산은 SMA 트레일" — 을
목표가에 센티널로 적는다. 그 값을 실주문으로 내보내면 절대 안 닿는 지정가가
거래소에 남는데, 바이낸스는 reduce-only 지정가에도 주문 증거금을 계상해서
`availableBalance` 가 0 이 됐다 (실측: 지갑 4,986 USDT · 가용 0 → 신규 진입 거부
위험 + 콘솔 총자산 -39% 착시. Gate 콘솔엔 BTC 133만 달러 매도가 떠 있었다).
익절 없는 계획은 익절 다리를 **생략**한다 — 포지션은 손절(트레일)이 지킨다.
"""


def _is_sentinel_target(record: TradeRecord, price: Decimal) -> bool:
    """계획가가 "익절 없음" 센티널인지 — 리스크 대비 SENTINEL_RR 배 이상 멀면 참."""
    risk = abs(record.entry - record.planned_stop)
    if record.entry <= 0 or risk <= 0:
        return False
    return abs(price - record.entry) / risk >= SENTINEL_RR


def take_profit_orders(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    *,
    run: str = "",
    half: Decimal = Decimal("0.5"),
) -> list[OrderRequest]:
    """익절 주문 둘 — 1차(반익) · 2차(목표), 둘 다 **지정가**.

    Args:
        record: 원장 계획.
        instrument: 대상 종목.
        contracts: 진입 계약 수.
        run: 판 표식 (T18 ⑤). 주면 주문 이름 앞에 6자가 붙어 거래소 콘솔에서
            판을 가를 수 있다. 비우면 옛 형식 그대로다.
        half: 1차에서 덜어낼 비율.

    Returns:
        `[1차, 2차]`. 1차 익절가가 0 이면 2차만 돌려준다.

    Raises:
        OrderMappingError: 계약 수가 1차를 나눌 수 없을 만큼 작은 경우.

    Note:
        🔴 **1차 수량을 내림하고 2차가 나머지를 받는다.** 둘 다 내리면 잔량이 남아
        포지션이 안 닫히고, 둘 다 올리면 없는 수량을 팔려 해서 거부된다.

        ⚠️ **`reduce_only` 로 나가야 한다** — 어댑터가 `order_kind` 를 보고 붙인다
        (`GatePaperAdapter`). 빼면 반대 포지션이 새로 열려 위험이 두 배가 된다.

        ⭐ 지정가이므로 **메이커**다. 0.1 의 비용 구조에서 유리한 쪽이다.
    """
    if contracts <= 1:
        raise OrderMappingError(
            f"계약 수가 {contracts} 라 반익을 나눌 수 없다 — 1차와 2차가 같은 수량이 되면 "
            "2차가 없는 수량을 팔려 한다"
        )
    side = _exit_side(record.direction)
    first_size = int((Decimal(contracts) * half).to_integral_value(rounding=ROUND_DOWN))
    out: list[OrderRequest] = []
    if (
        record.planned_first > 0
        and first_size > 0
        and not _is_sentinel_target(record, record.planned_first)
    ):
        out.append(
            OrderRequest(
                instrument=instrument,
                side=side,
                order_kind=OrderKind.TAKE_PROFIT,
                order_type=OrderType.LIMIT,
                quantity=Decimal(first_size),
                price=record.planned_first,
                idempotency_key=order_key(record.trade_id, OrderKind.TAKE_PROFIT.value, 1, run),
                approved_order_id=record.trade_id,
                leg_index=1,
                revision_id=None,
            )
        )
    rest = contracts - (first_size if out else 0)
    if _is_sentinel_target(record, record.planned_target):
        return out  # 목표가 센티널이다 — 2차를 걸지 않는다 (없는 익절을 흉내내지 않는다)
    out.append(
        OrderRequest(
            instrument=instrument,
            side=side,
            order_kind=OrderKind.TAKE_PROFIT,
            order_type=OrderType.LIMIT,
            quantity=Decimal(rest),
            price=record.planned_target,
            idempotency_key=order_key(record.trade_id, OrderKind.TAKE_PROFIT.value, 2, run),
            approved_order_id=record.trade_id,
            leg_index=2,
            # ⚠️ 2차는 1차 체결 후 재평가로 바뀔 수 있다 (spec §9.1). 바뀌면 그때
            #    `revision_id` 가 근거를 가리킨다 — 지금은 최초 계획대로다.
            revision_id=None,
        )
    )
    return out


def stop_order(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    *,
    run: str = "",
    revision: int = 0,
) -> OrderRequest:
    """손절 주문 — **발동 시점에 시장가**.

    Args:
        record: 원장 계획.
        instrument: 대상 종목.
        contracts: 남은 계약 수.
        run: 판 표식 (T18 ⑤). 주면 주문 이름 앞에 6자가 붙어 거래소 콘솔에서
            판을 가를 수 있다. 비우면 옛 형식 그대로다.
        revision: 손절 개정 번호. 본절 상향처럼 손절을 옮길 때마다 올린다.

    Returns:
        손절 주문.

    Raises:
        OrderMappingError: 계약 수가 0 이하인 경우.

    Note:
        🔴 **브로커에 미리 걸지 않는다.** `GateAdapter.capabilities` 에
        `CONDITIONAL_ORDERS` 를 넣지 않았다 — Gate 가 스탑을 제공하지만 **써 보지
        않았고**, 능력표에 적으면 상위가 "서버 다운 중에도 브로커측 손절이 돈다" 고
        믿게 된다. 지금은 우리가 감시하고 발동 시 시장가로 보낸다.

        ⚠️ **그래서 서버가 죽으면 손절이 안 걸린다.** 이것이 지금 구조의 가장 큰
        구멍이고, 브로커측 스탑 검증(§7 · §12.6)이 그것을 메운다. 페이크머니 구간에
        해야 할 일이다.

        🔴 **손절 레그는 `leg_index` 가 개정 번호다** (진입·익절과 다르다 · spec §9).
        본절 상향이 개정 1 이므로 멱등키가 달라지고, 브로커가 새 주문으로 받는다 —
        같은 키를 쓰면 상향이 조용히 무시된다.

        ⛔ 손절을 **내리는** 개정은 없다 (절대 규칙 #3). 방향 검증은 RiskManager 몫이고
        여기서는 옮기기만 한다.
    """
    if contracts <= 0:
        raise OrderMappingError(f"계약 수가 {contracts} 다 — 손절할 것이 없다")
    return OrderRequest(
        instrument=instrument,
        side=_exit_side(record.direction),
        order_kind=OrderKind.STOP_LOSS,
        order_type=OrderType.MARKET,
        quantity=Decimal(contracts),
        price=None,
        idempotency_key=order_key(record.trade_id, OrderKind.STOP_LOSS.value, revision, run),
        approved_order_id=record.trade_id,
        leg_index=revision,
        revision_id=None if revision == 0 else f"{record.trade_id}:rev{revision}",
    )


def close_limit_order(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    price: Decimal,
    *,
    run: str = "",
    revision: int = 0,
) -> OrderRequest:
    """신호 청산을 **지정가(post-only)** 로 낸다 — 메이커 청산 (T126 · 1.1.0).

    같은 `OrderKind.CLOSE` 라 원장 귀속은 시장가 청산과 동일하다. 다른 것은
    체결 방식뿐이다: 테이커 0.09% 대신 메이커 0.02% 를 문다.

    Args:
        record: 원장 계획.
        instrument: 대상 종목.
        contracts: 남은 계약 수.
        price: 지정가. 롱 청산이면 현재가보다 **위**, 숏 청산이면 **아래**여야
            post-only 가 거절되지 않는다 (즉시 크로스하면 거절이다).
        run: 판 표식.
        revision: 개정 번호 — 멱등키에 들어간다. 재시도마다 올린다.

    Returns:
        post-only 지정가 청산 주문.

    Raises:
        OrderMappingError: 계약 수나 가격이 0 이하인 경우.

    Note:
        🔴 **이 주문은 안 채워질 수 있다.** 호출부가 반드시 만료를 감시하고
        시장가로 마무리해야 한다 (`maker_exit_bars` 봉). 안 그러면 나가려던
        포지션이 조용히 남는다 — 청산 신호가 뜬 뒤의 보유는 계획에 없는 위험이다.

        ⭐ 기다리는 동안에도 **거래소측 조건부 손절은 그대로 걸려 있다** —
        미체결 구간이 무방비가 아니다.
    """
    if contracts <= 0:
        raise OrderMappingError(f"계약 수가 {contracts} 다 — 청산할 것이 없다")
    if price <= 0:
        raise OrderMappingError(f"지정가가 {price} 다 — 걸 수 없다")
    return OrderRequest(
        instrument=instrument,
        side=_exit_side(record.direction),
        order_kind=OrderKind.CLOSE,
        order_type=OrderType.LIMIT,
        quantity=Decimal(contracts),
        price=price,
        idempotency_key=order_key(record.trade_id, OrderKind.CLOSE.value, revision, run),
        approved_order_id=record.trade_id,
        leg_index=revision,
        revision_id=None if revision == 0 else f"{record.trade_id}:rev{revision}",
        post_only=True,
    )


def close_order(
    record: TradeRecord,
    instrument: Instrument,
    contracts: int,
    *,
    run: str = "",
    revision: int = 0,
) -> OrderRequest:
    """전량 청산 — 전환 익절·강제 정리에 쓴다 (시장가).

    Args:
        record: 원장 계획.
        instrument: 대상 종목.
        contracts: 남은 계약 수.
        run: 판 표식 (T18 ⑤). 주면 주문 이름 앞에 6자가 붙어 거래소 콘솔에서
            판을 가를 수 있다. 비우면 옛 형식 그대로다.
        revision: 개정 번호.

    Returns:
        청산 주문.

    Raises:
        OrderMappingError: 계약 수가 0 이하인 경우.

    Note:
        0.2 의 **전환 익절**(반대 신호가 뜨면 전량 정리)이 이 주문이다. 익절과 다른
        `order_kind` 를 쓰는 이유는 원장이 두 사건을 구별하기 때문이다 — 섞으면
        "목표에 닿아서 나온 것" 과 "신호에 털려서 나온 것" 이 한 줄이 된다.
    """
    if contracts <= 0:
        raise OrderMappingError(f"계약 수가 {contracts} 다 — 청산할 것이 없다")
    return OrderRequest(
        instrument=instrument,
        side=_exit_side(record.direction),
        order_kind=OrderKind.CLOSE,
        order_type=OrderType.MARKET,
        quantity=Decimal(contracts),
        price=None,
        idempotency_key=order_key(record.trade_id, OrderKind.CLOSE.value, revision, run),
        approved_order_id=record.trade_id,
        leg_index=revision,
        revision_id=None if revision == 0 else f"{record.trade_id}:rev{revision}",
    )
