"""**쓸 수 있는 지지·저항만 남긴다** — 다 찾아 주는 것은 정보가 아니다 (2026-08-30).

## 왜 생겼나

사용자 요구: *"유의미한 가치는 막 모든 저항을 찾아주고, 그런 게 옳지 않다는 거야.
실제 트레이딩에서 유효하게 쓸 수 있는 정도의 정보를 제공해야 해."*

실측(BTC 4h · 사용자 캡처): 띠가 **32개** 나왔다. 그중 상당수가 거의 같은 자리다 —

    저항 64,216~64,358 · 5회
    저항 64,400~64,530 · 5회
    저항 64,988~65,090 · 4회

셋은 사람 눈에 **한 자리**다. 32개를 늘어놓으면 사람은 그중 무엇을 볼지 다시 골라야
하고, 그 고르는 일을 화면이 대신 해 주지 않으면 아무 일도 안 한 것이다.

## 🔴 무엇이 "유의미" 인가 — 규칙으로 정한다

⛔ **눈으로 고르지 않는다** (절대 규칙 #11). 아래는 전부 **셀 수 있는 것**이고, 그래서
성과로 검증할 수 있다:

    ① 뭉친다        ATR 안에 있는 레벨은 **한 자리**다 (위 셋이 그 경우다)
    ② 접점          3회 이상. 2회는 우연일 수 있다
    ③ 살아 있나     마지막 접점이 너무 오래됐으면 시장이 잊은 자리다
    ④ 안 뚫렸나     그 뒤로 결정적으로 관통했으면 그것은 **지지가 아니라 지나간 곳**이다
    ⑤ 비용을 갚나   지금 가격에서 너무 가까우면 왕복 비용도 못 갚는다 — 못 쓰는 자리다

⑤ 가 이 파일의 요점이다. 나머지 넷은 "그럴듯한가" 를 묻지만 ⑤ 는 **"거래가 되나"** 를
묻는다 — 이 프로젝트가 5m 을 버린 것도 같은 산수였다 (필요 승률 100% 초과).

## ⚠️ 여기서 방향을 정하지 않는다

이 모듈은 *"어디가 자리인가"* 만 답한다. 사고팔지는 `plan.py` 가 정하고, 집행값은
언제나 RiskManager 가 확정한다 (절대 규칙 #4).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from updown.analysis.structures.box import Box
    from updown.common.domain.candle import Candle

MERGE_ATR = Decimal("0.5")
"""이 배수의 ATR 안에 있으면 **한 자리로 친다**.

🔴 실측에서 64,216 · 64,400 · 64,988 이 따로 나왔는데 사람 눈에는 한 자리다. ATR 은
그 종목의 하루 진폭이므로, 그 절반 안이면 "같은 자리에서 반응했다" 고 보는 것이 맞다.

⚠️ 고정 %(예: 0.5%)를 쓰지 않는다 — 변동성이 큰 종목에서는 너무 좁고 잔잔한 종목에서는
너무 넓다. 자를 시장에서 가져와야 한다.
"""

MIN_TOUCHES = 3
"""이만큼 닿아야 레벨로 친다.

⚠️ 2회는 **선을 그을 수 있다**는 뜻일 뿐이다 — 아무 두 점이나 이으면 선이 된다.
3회부터 *"시장이 반복해서 인정했다"* 가 된다 (구조물 모듈의 3접점 규칙과 같은 사상).
"""

STALE_BARS = 200
"""마지막 접점이 이보다 오래됐으면 **잊힌 자리**로 본다.

⚠️ 오래된 레벨이 안 듣는다는 뜻이 아니다 — 다만 *"지금 매매에 쓸 정보"* 는 아니다.
연구에서 옛 레벨을 보고 싶으면 이 값을 올려서 부른다.
"""

BROKEN_ATR = Decimal("1.0")
"""이 배수의 ATR 만큼 **종가로** 관통했으면 뚫린 것으로 본다.

🔴 **꼬리가 아니라 종가다.** 꼬리로 한 번 찌른 것은 오히려 그 레벨이 살아 있다는
증거이고, 그것을 "뚫렸다" 로 세면 진짜 지지를 전부 지운다.
"""

COST_COVER = Decimal("2.0")
"""지금 가격에서 **왕복 비용의 이 배수**보다 가까운 레벨은 버린다.

🔴 **이 파일의 요점이다.** 0.05% 떨어진 저항은 차트에 그릴 수는 있어도 거래가 안 된다 —
왕복 비용(코인 0.157%)도 못 갚기 때문이다. 그런 자리를 보여 주면 사람이 그것을 근거로
계획을 세우고, 그 계획은 **산술적으로** 질 수밖에 없다.

⚠️ 이 프로젝트가 5m 을 버린 것도 같은 산수였다 (필요 승률 100.7~100.9%).
"""


@dataclass(frozen=True, slots=True)
class Level:
    """쓸 수 있는 지지·저항 한 자리.

    Attributes:
        low: 띠의 아래.
        high: 띠의 위.
        support: 지지인가 (거짓이면 저항).
        touches: 뭉친 뒤의 총 접점 수.
        last_ts: 가장 최근 접점.
        away_pct: 지금 가격에서 몇 % 떨어져 있나 (부호 없음).
        merged: 몇 개가 뭉쳐 이 자리가 됐나 — 1 이면 원래 하나다.
    """

    low: Decimal
    high: Decimal
    support: bool
    touches: int
    last_ts: datetime
    away_pct: Decimal
    merged: int

    @property
    def mid(self) -> Decimal:
        """대표 가격 — 띠의 가운데."""
        return (self.low + self.high) / 2


def useful(
    boxes: Sequence[Box],
    candles: Sequence[Candle],
    *,
    span: Decimal,
    round_trip: Decimal,
    limit: int = 6,
) -> list[Level]:
    """**실제로 쓸 수 있는** 레벨만 남긴다.

    Args:
        boxes: 작도가 찾은 수평 레벨 전부.
        candles: 창의 봉들 (오름차순). 마지막 종가를 지금 가격으로 쓴다.
        span: ATR — 뭉치기·관통 판정의 자.
        round_trip: 왕복 비용 비율 (`costs.yml`).
        limit: 남길 수.

    Returns:
        가까운 것부터. 아무것도 안 남으면 빈 목록.

    Note:
        🔴 **순수 함수다.** 어느 자리가 쓸 만한지는 성과로 검증해야 하고, 그러려면
        거래소·DB 없이 표로 시험할 수 있어야 한다 (`reconcile.compare` 와 같은 사상).

        ⚠️ **빈 목록도 답이다.** 억지로 채우면 "쓸 수 있는 자리" 라는 말이 뜻을 잃는다 —
        지금 볼 자리가 없는 국면이 실제로 있다.
    """
    return useful_report(boxes, candles, span=span, round_trip=round_trip, limit=limit)[0]


@dataclass(frozen=True, slots=True)
class DropReport:
    """레벨이 어디서 떨어졌나 — "후보 없음" 이 원래 없는 자리인지 화면이 말하게 (2026-09-11).

    Attributes:
        raw: 작도가 찾은 레벨 수.
        stale: 마지막 접점이 `STALE_BARS` 보다 오래된 것.
        broken: 접점 뒤 종가가 관통한 것(뭉친 뒤 기준).
        few_touches: 접점이 `MIN_TOUCHES` 미만.
        too_close: 지금 가격에서 왕복 비용 x `COST_COVER` 안.
        kept: 남은 것(상한 `limit` 적용 전).
    """

    raw: int
    stale: int
    broken: int
    few_touches: int
    too_close: int
    kept: int

    def as_json(self) -> dict[str, int]:
        """화면 모양.

        Returns:
            탈락 이유별 수 — `raw`(작도) · `stale`(잊힘) · `broken`(관통) ·
            `few_touches`(접점 부족) · `too_close`(비용 안) · `kept`(남은 것).
        """
        return {
            "raw": self.raw,
            "stale": self.stale,
            "broken": self.broken,
            "few_touches": self.few_touches,
            "too_close": self.too_close,
            "kept": self.kept,
        }


def useful_report(
    boxes: Sequence[Box],
    candles: Sequence[Candle],
    *,
    span: Decimal,
    round_trip: Decimal,
    limit: int = 6,
) -> tuple[list[Level], DropReport]:
    """`useful` 과 같되 **떨어진 이유의 수**를 같이 준다 (순수).

    Args:
        boxes: 작도가 찾은 수평 레벨 전부.
        candles: 창의 봉들 (오름차순).
        span: ATR.
        round_trip: 왕복 비용 비율.
        limit: 남길 수.

    Returns:
        `(가까운 순 레벨, 보고)`. 봉·ATR 이 없으면 빈 목록과 전부 0 인 보고(원본 수만).
    """
    empty = DropReport(raw=len(boxes), stale=0, broken=0, few_touches=0, too_close=0, kept=0)
    if not candles or span <= 0:
        return [], empty
    price = candles[-1].close
    if price <= 0:
        return [], empty
    fresh = _recent(boxes, candles)
    merged = _merge(fresh, span)
    alive = [item for item in merged if not _broken(item, candles, span)]
    floor = price * round_trip * COST_COVER
    few = sum(1 for item in alive if item.touches < MIN_TOUCHES)
    near = sum(1 for item in alive if item.touches >= MIN_TOUCHES and abs(item.mid - price) < floor)
    out = [
        _with_distance(item, price)
        for item in alive
        if item.touches >= MIN_TOUCHES and abs(item.mid - price) >= floor
    ]
    out.sort(key=lambda item: item.away_pct)
    report = DropReport(
        raw=len(boxes),
        stale=len(boxes) - len(fresh),
        broken=len(merged) - len(alive),
        few_touches=few,
        too_close=near,
        kept=len(out),
    )
    return out[:limit], report


@dataclass(frozen=True, slots=True)
class _Raw:
    """뭉치는 중간 표현 — 거리는 아직 모른다."""

    low: Decimal
    high: Decimal
    support: bool
    touches: int
    last_ts: datetime
    merged: int

    @property
    def mid(self) -> Decimal:
        return (self.low + self.high) / 2


def _recent(boxes: Sequence[Box], candles: Sequence[Candle]) -> list[_Raw]:
    """③ **잊힌 자리를 턴다** — 마지막 접점이 `STALE_BARS` 안인 것만."""
    if not candles:
        return []
    edge = candles[max(0, len(candles) - STALE_BARS)].ts
    from updown.analysis.structures.swing import SwingKind

    return [
        _Raw(
            low=box.price_range.low,
            high=box.price_range.high,
            support=box.kind is SwingKind.LOW,
            touches=box.touch_count,
            last_ts=box.last_ts,
            merged=1,
        )
        for box in boxes
        if box.last_ts >= edge
    ]


def _merge(rows: list[_Raw], span: Decimal) -> list[_Raw]:
    """① ATR 안에 있는 것은 **한 자리다**.

    🔴 실측에서 64,216 · 64,400 · 64,988 이 따로 나왔는데 사람 눈에는 한 자리다.
    안 뭉치면 화면이 같은 말을 세 번 하고, 접점 수도 셋으로 쪼개져 **약해 보인다.**

    ⚠️ 지지와 저항은 **안 섞는다.** 같은 가격이라도 역할이 다르면 다른 자리다 —
    뚫린 저항이 지지가 되는 것은 그 자체가 규칙이고, 여기서 뭉개면 그 사실을 잃는다.
    """
    out: list[_Raw] = []
    for row in sorted(rows, key=lambda item: (item.support, item.mid)):
        last = out[-1] if out else None
        if (
            last is not None
            and last.support == row.support
            and row.mid - last.mid <= span * MERGE_ATR
        ):
            out[-1] = _Raw(
                low=min(last.low, row.low),
                high=max(last.high, row.high),
                support=last.support,
                # ⭐ 접점을 **더한다** — 쪼개져 있던 것이 합쳐지면 그만큼 센 자리다.
                touches=last.touches + row.touches,
                last_ts=max(last.last_ts, row.last_ts),
                merged=last.merged + row.merged,
            )
            continue
        out.append(row)
    return out


def _broken(row: _Raw, candles: Sequence[Candle], span: Decimal) -> bool:
    """④ **뚫렸나** — 마지막 접점 뒤로 종가가 결정적으로 관통했으면 참.

    🔴 **꼬리가 아니라 종가로 본다.** 꼬리로 한 번 찌른 것은 오히려 그 레벨이 살아
    있다는 증거다 — 그것을 관통으로 세면 진짜 지지를 전부 지운다.

    ⚠️ 관통 폭에 ATR 을 쓴다. 고정 %는 변동성이 큰 종목에서 너무 쉽게 "뚫림" 이 된다.
    """
    edge = span * BROKEN_ATR
    after = [candle for candle in candles if candle.ts > row.last_ts]
    if row.support:
        return any(candle.close < row.low - edge for candle in after)
    return any(candle.close > row.high + edge for candle in after)


def _with_distance(row: _Raw, price: Decimal) -> Level:
    """지금 가격에서 얼마나 떨어져 있나 — 가까운 것이 먼저 쓰인다."""
    return Level(
        low=row.low,
        high=row.high,
        support=row.support,
        touches=row.touches,
        last_ts=row.last_ts,
        away_pct=abs(row.mid - price) / price * 100,
        merged=row.merged,
    )
