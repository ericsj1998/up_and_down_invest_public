"""호가창이 **내 규모를 받아 줄 수 있나** (2026-08-20 사고 · SPCX).

## 🔴 왜 `orchestration/` 인가

원래 `apps/api/exchange.py` 에 있었다. 그런데 판을 **띄울 때만** 부르는 자리였고,
사용자가 물었다 — *"말랐을 때 어떻게 대처해야 하는지도 있어야 하는 거 아닌가?"*

들고 있는 동안에도 재려면 러너(`orchestration/`)가 불러야 하는데, 러너는 `apps/` 를
import 할 수 없다 (계층 단방향). 그래서 계산을 여기로 내리고 화면은 이것을 쓴다.

## ⚠️ 어느 거래소의 호가창인가가 곧 그 값의 뜻이다

조회는 라이브 API, 주문은 testnet 이다. 이 검사는 **주문이 나가는 곳**을 봐야 한다:

```
라이브 SPCX    매수 1호가가 표시가에서 0.01%   → "건강하다"
testnet SPCX   매수 1호가가 표시가에서 21.8%   → 팔 곳이 없다
```

라이브를 보면 증거금 420 을 15시간 묶은 바로 그 계약을 *"들어가도 좋다"* 고 답한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Any, cast

from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.instrument import Market
from updown.common.logging.setup import get_logger

if TYPE_CHECKING:
    from updown.common.domain.instrument import Instrument

_logger = get_logger("orchestration.liquidity")

THRESHOLDS = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE).liquidity
"""유동성 문턱(Gate) — 비어 있으면 검사를 안 한다 (`config/costs.yml`). 종목이 있는 자리는
`thresholds_for(market)` 을 쓴다 (T269 #6)."""


def thresholds_for(market: Market) -> Mapping[str, Decimal]:
    """그 시장의 유동성 문턱 — 비용표 블록이 없으면 빈 매핑(검사 없음 · Gate 값을 빌리지 않는다).

    Args:
        market: 시장.

    Returns:
        `max_gap_pct` · `depth_multiple` · `order_deviation_pct` 등.
    """
    try:
        return load_cost_table(DEFAULT_CONFIG_PATH).for_market(market).liquidity
    except (KeyError, ValueError):
        return {}


@dataclass(frozen=True, slots=True)
class Liquidity:
    """호가창이 **내 규모를 받아 줄 수 있나**.

    Attributes:
        symbol: 종목.
        mark: 표시가 — 지수에서 온다.
        bid: 최고 매수호가. 없으면 None.
        ask: 최저 매도호가. 없으면 None.
        bid_gap_pct: 표시가에서 매수호가까지의 거리(%).
        ask_gap_pct: 표시가에서 매도호가까지의 거리(%).
        bid_depth: 문턱 안에 쌓인 매수 명목(USDT).
        ask_depth: 문턱 안에 쌓인 매도 명목(USDT).
        why: 못 쓰는 이유. 쓸 수 있으면 빈 문자열.
        read: 호가창을 **읽었는가**. 거짓이면 위 값들은 뜻이 없다.

    Note:
        🔴 **양쪽을 따로 센다.** 깊이 총합을 재면 SPCX 는 매도 33만 계약이라 아주
        건강해 보인다 — 정작 팔 곳이 없어서 증거금 420 이 묶였다.

        ⚠️ **`read` 와 `ok` 는 다르다.** 못 읽은 것을 "통과" 로만 표현하면 화면이
        *"검사했고 괜찮다"* 로 읽는다 — 실제로는 아무것도 모르는 상태다 (규칙 #8).
    """

    symbol: str
    mark: Decimal
    bid: Decimal | None
    ask: Decimal | None
    bid_gap_pct: Decimal
    ask_gap_pct: Decimal
    bid_depth: Decimal
    ask_depth: Decimal
    why: str
    read: bool = True

    @property
    def ok(self) -> bool:
        """들어가도 되나 (못 읽었으면 막지 않는다)."""
        return not self.why


def _blind(symbol: str, *, mark: Decimal | None = None) -> Liquidity:
    """못 읽었다 — **막지 않는다** (§1.2.1)."""
    zero = Decimal(0)
    return Liquidity(symbol, mark or zero, None, None, zero, zero, zero, zero, "", read=False)


def read_book(
    symbol: str,
    spec: dict[str, Any],
    book: dict[str, Any],
    notional: Decimal,
) -> Liquidity:
    """계약 명세와 호가창으로 **판정만** 한다 — 순수 함수.

    Args:
        symbol: 종목.
        spec: 계약 명세 (`mark_price` · `quanto_multiplier`).
        book: 호가창 (`bids` · `asks`, 각 행은 `{p, s}`).
        notional: 견줄 명목 금액(USDT). **0 이면 깊이는 판정하지 않는다** — 순위
            화면처럼 아직 예산이 정해지지 않은 자리가 그렇다.

    Returns:
        판정.

    Note:
        🔴 **Gate 는 표시가에서 20% 넘게 벗어난 주문을 아예 안 받는다**
        (`deviation-rate limit 0.2`). SPCX 는 걸 수 있는 최저가 111.25 와 유일한 매수자
        108 이 **3.25 차이로 영영 안 만났고**, 증거금 420 이 15시간 묶였다.

        ⭐ 문턱이 5% 인 것은 자의적이지 않다 — Gate 시장가 슬립 방어가 그 값이다
        (실측 거절 문구 `slip ratio 0.05`). 그보다 나쁜 호가는 **시장가로 못 넘는다**.

        ⛔ **네트워크를 안 탄다.** 여기가 순수해야 시험이 SPCX 를 그대로 재현한다.
    """
    zero = Decimal(0)
    if not THRESHOLDS:
        return _blind(symbol)
    max_gap = THRESHOLDS.get("max_gap_pct", Decimal(5))
    need = THRESHOLDS.get("depth_multiple", Decimal(1)) * notional

    mark = Decimal(str(spec.get("mark_price") or spec.get("last_price") or "0"))
    # ⚠️ 잔량은 **계약 수**다 — 명목으로 바꾸려면 승수를 곱한다 (여기서 **한 번만**).
    lot = Decimal(str(spec.get("quanto_multiplier", "1")))
    bids = cast("list[dict[str, Any]]", book.get("bids") or [])
    asks = cast("list[dict[str, Any]]", book.get("asks") or [])
    if mark <= 0:
        return _blind(symbol)
    if not bids or not asks:
        # ⛔ 한쪽이 비었으면 그 자체가 사건이다 — 통과시키지 않는다.
        return Liquidity(
            symbol,
            mark,
            None,
            None,
            zero,
            zero,
            zero,
            zero,
            "호가 한쪽이 비었다 — 들어가면 나올 곳이 없다",
        )

    bid = Decimal(str(bids[0]["p"]))
    ask = Decimal(str(asks[0]["p"]))
    bid_gap = (mark - bid) / mark * 100
    ask_gap = (ask - mark) / mark * 100
    # ⭐ 문턱 **안쪽**만 센다 — 96 에 100만 계약이 있어도 22% 밖이면 없는 것이다.
    bid_depth = sum(
        (
            Decimal(str(row["p"])) * Decimal(str(row["s"])) * lot
            for row in bids
            if (mark - Decimal(str(row["p"]))) / mark * 100 <= max_gap
        ),
        zero,
    )
    ask_depth = sum(
        (
            Decimal(str(row["p"])) * Decimal(str(row["s"])) * lot
            for row in asks
            if (Decimal(str(row["p"])) - mark) / mark * 100 <= max_gap
        ),
        zero,
    )

    why = ""
    if bid_gap > max_gap:
        why = f"매수호가({bid})가 표시가({mark})에서 {bid_gap:.1f}% 떨어졌다 — 팔 곳이 없다"
    elif ask_gap > max_gap:
        why = f"매도호가({ask})가 표시가({mark})에서 {ask_gap:.1f}% 떨어졌다 — 살 곳이 없다"
    elif need > 0 and bid_depth < need:
        why = f"매수 깊이 {bid_depth:.0f} < 필요 {need:.0f} USDT — 내가 나갈 때 다 먹는다"
    elif need > 0 and ask_depth < need:
        why = f"매도 깊이 {ask_depth:.0f} < 필요 {need:.0f} USDT — 들어갈 때 밀린다"
    return Liquidity(symbol, mark, bid, ask, bid_gap, ask_gap, bid_depth, ask_depth, why)


@dataclass(frozen=True, slots=True)
class Escape:
    """**여기서 나가려면 얼마에 걸어야 하나** (사용자 요구 2026-08-20).

    Attributes:
        symbol: 종목.
        long: 보유가 롱인가 — 롱이면 팔아야 하고 숏이면 사야 한다.
        size: 계약 수 (부호 없음).
        entry: 진입 평단.
        mark: 표시가.
        bid: 최고 매수호가.
        ask: 최저 매도호가.
        limit: **거래소가 받아 주는 한계가** — 이보다 불리하게는 못 건다.
        suggested: 권장 자리 — 반대편 1호가를 한 눈금 파고든 값.
        realized: 권장 자리에 체결되면 실현 손익 (**수수료 전**).
        touch: 지금 **즉시** 나가려면 때려야 할 값.
        touch_ok: 그 값을 **지정가로 걸 수 있나** (표시가에서 20% 안).
        touch_realized: 그 값의 실현 손익 (**수수료 전**).
        market_ok: **전량 청산(시장가)이 통과할 것인가** — Gate 슬립 한도(5%) 안인가.
            참이면 이 창이 필요 없다. 그냥 닫으면 된다.

    Note:
        🔴 **값을 기계가 정하지 않는다.** 이 자료형은 *"이 값이면 이만큼 실현된다"* 를
        나란히 놓을 뿐이고, 거는 것은 사람이 누른다 — 실측이 그 이유다:

        ```
        SPCX 를 그때 시장가로 던졌다면   -420
        표시가 아래 지정가로 기다렸더니   -74   (15시간 31분)
        ```

        ⚠️ **수수료는 안 뺀다.** 계약 명세의 요율은 기준값이고 실제 요율은 주문 응답에만
        온다 — 명세로 계산하면 낙관 방향으로 틀린다.
    """

    symbol: str
    long: bool
    size: int
    entry: Decimal
    mark: Decimal
    bid: Decimal | None
    ask: Decimal | None
    limit: Decimal
    suggested: Decimal
    realized: Decimal
    touch: Decimal | None
    touch_ok: bool
    touch_realized: Decimal | None
    market_ok: bool


def _step(price: Decimal, tick: Decimal, *, down: bool) -> Decimal:
    """호가 눈금의 배수로 자른다 — 눈금을 안 맞추면 거래소가 거절한다."""
    if tick <= 0:
        return price
    steps = (price / tick).to_integral_value(rounding=ROUND_FLOOR if down else ROUND_CEILING)
    return steps * tick


def escape_plan(
    symbol: str,
    spec: dict[str, Any],
    book: dict[str, Any],
    held: dict[str, Any],
) -> Escape | None:
    """**나갈 지정가를 계산만** 한다 — 주문은 안 낸다.

    Args:
        symbol: 종목.
        spec: 계약 명세 (`mark_price` · `quanto_multiplier` · `order_price_round`).
        book: 호가창.
        held: 포지션 스냅샷 (`size` · `entry_price`).

    Returns:
        계획. 포지션이 없거나 값을 못 읽으면 None.

    Note:
        🔴 **한계가는 우리 문턱이 아니라 Gate 의 규칙이다** (`order_deviation_pct`).
        표시가에서 그만큼 넘게 벗어난 지정가는 아예 안 받는다 — SPCX 는 걸 수 있는
        최저가 111.25 와 유일한 매수자 108 이 **3.25 차이로 안 만났다.**

        ⭐ **권장 자리는 반대편 1호가를 한 눈금 파고든 값**이다. 롱을 닫으려면 팔아야
        하고, 파는 줄에서 **맨 앞에 서야** 사려는 사람이 왔을 때 내 것이 먼저 채워진다.
        SPCX 를 푼 138.50 이 그 자리였다.

        ⛔ **`touch`(즉시 체결가)를 권장으로 내밀지 않는다.** 그것이 곧 손실 확정이고,
        SPCX 에서는 애초에 규칙에 막혀 불가능했다 — 그 사실을 보여 주는 것이 이 칸의 일이다.
    """
    size = int(Decimal(str(held.get("size", "0") or "0")))
    if size == 0:
        return None
    mark = Decimal(str(spec.get("mark_price") or spec.get("last_price") or "0"))
    if mark <= 0:
        return None
    entry = Decimal(str(held.get("entry_price", "0") or "0"))
    lot = Decimal(str(spec.get("quanto_multiplier", "1")))
    tick = Decimal(str(spec.get("order_price_round") or "0"))
    band = THRESHOLDS.get("order_deviation_pct", Decimal(20)) / 100
    long = size > 0

    bids = cast("list[dict[str, Any]]", book.get("bids") or [])
    asks = cast("list[dict[str, Any]]", book.get("asks") or [])
    bid = Decimal(str(bids[0]["p"])) if bids else None
    ask = Decimal(str(asks[0]["p"])) if asks else None

    def realized(price: Decimal) -> Decimal:
        """그 가격에서 닫았을 때의 실현 손익.

        Args:
            price: 청산가.

        Returns:
            `(청산가 - 진입가) x 계약 수 x 승수`. 부호는 계약 수가 든다 — 숏이면 음수라 뺄셈이
            저절로 뒤집힌다.
        """
        return (price - entry) * Decimal(size) * lot

    # 🔴 **한계가.** 롱은 파는 것이라 아래로 벗어나는 것이 위험하고, 숏은 그 반대다.
    limit = (
        _step(mark * (1 - band), tick, down=False)
        if long
        else _step(mark * (1 + band), tick, down=True)
    )
    # ⭐ **줄 맨 앞에 선다** — 롱이면 최저 매도호가를 한 눈금 밑으로 파고든다.
    #    반대편 호가가 없으면 표시가 옆에 선다 (그 자체가 마른 시장이라는 뜻이다).
    front = (ask or mark) - tick if long else (bid or mark) + tick
    suggested = max(front, limit) if long else min(front, limit)
    suggested = _step(suggested, tick, down=long)

    # ⚠️ **즉시 체결가는 반대편이 아니라 같은 편 호가다** — 롱을 닫으려면 매수호가를
    #    때려야 한다. SPCX 는 그 값이 108 이었고 한계가 111.25 에 막혔다.
    touch = bid if long else ask
    touch_ok = touch is not None and (touch >= limit if long else touch <= limit)
    # 🔴 **시장가와 지정가의 벽이 다르다** — 이 둘을 하나로 세면 SPCX 를 놓친다:
    #
    #      시장가   슬립 5% 초과면 거절   (`slip ratio 0.05`)
    #      지정가   표시가 ±20% 밖이면 거절 (`deviation-rate limit 0.2`)
    #
    #    격차가 6% 면 시장가는 막히고 지정가는 된다 — 그때가 이 창이 필요한 자리다.
    #    격차가 22% 면 둘 다 막히고, 그것이 SPCX 였다.
    slip = THRESHOLDS.get("max_gap_pct", Decimal(5)) / 100
    market_ok = touch is not None and abs(touch - mark) / mark <= slip
    return Escape(
        symbol=symbol,
        long=long,
        size=abs(size),
        entry=entry,
        mark=mark,
        bid=bid,
        ask=ask,
        limit=limit,
        suggested=suggested,
        realized=realized(suggested),
        touch=touch,
        touch_ok=touch_ok,
        touch_realized=None if touch is None else realized(touch),
        market_ok=market_ok,
    )


async def probe_book(orders: Any, instrument: Instrument, notional: Decimal) -> Liquidity:
    """**주문이 나가는 곳**의 호가창을 읽어 판정한다.

    Args:
        orders: 주문 어댑터 — `contract_spec` · `book_here` 를 가진 것.
        instrument: 종목.
        notional: 견줄 명목 금액(USDT). 0 이면 깊이는 판정하지 않는다.

    Returns:
        판정. 못 읽었으면 `read=False` 이고 막지 않는다.

    Note:
        ⛔ **못 읽으면 막지 않는다.** 조회 실패로 판을 못 띄우면 그것대로 사고이고, 이
        검사는 *"명백히 나쁜 것"* 을 거르는 자다 (절대 규칙 #8-1 과 같은 방향).
    """
    symbol = instrument.symbol
    if not THRESHOLDS:
        return _blind(symbol)
    try:
        spec = await orders.contract_spec(instrument)
        book = await orders.book_here(instrument)
    except Exception as exc:
        _logger.warning(
            "liquidity_unreadable",
            payload={"symbol": symbol, "error": str(exc)[:140], "note": "막지 않는다"},
        )
        return _blind(symbol)
    return read_book(symbol, spec, book, notional)
