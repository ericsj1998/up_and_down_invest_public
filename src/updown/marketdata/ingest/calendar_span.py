"""주봉·월봉 — **캘린더 경계**로 묶는다 (P1 §1-0x · spec §12.3 · C2-3).

## 왜 `aggregate.py` 로 안 되는가

`aggregate.py` 는 목표 시간축이 원본의 **정확한 배수**일 것을 요구한다
(`span % source_interval != 0` 이면 거부). 5m→15m 은 3배라 무손실이다.

**달은 28~31일이라 고정 배수가 아니다.** 주도 마찬가지다 — 거래일 수가 주마다 다르다
(공휴일). 즉 이것은 기존 합성기에 인자를 더할 문제가 아니라 **다른 종류의 묶기**다:
고정 간격이 아니라 **달력이 경계를 정한다.**

## 🔴 왜 `Timeframe` 에 `1w`/`1M` 을 넣지 않는가

처음에는 그러려 했는데 잘못된 설계다. `Timeframe` 은 두 가지를 동시에 뜻한다:

1. **DB 에 저장되는 값** (`candles.timeframe` enum)
2. **`interval()` 로 고정 간격을 갖는 값** — `AsOfSequence`·스캔·리테스트가 전부 쓴다

월봉은 **둘 다 아니다.** 고정 간격이 없어서 `interval(MN1)` 은 답이 없고, 저장하지
않을 것이라 DB enum 도 필요 없다. 넣으면 `interval()` 이 예외를 던지는 값이 도메인
전역에 돌아다니게 된다 — **언젠가 조용히 터질 지뢰**다.

그래서 `CandleRow` 와 **같은 해법**을 쓴다: 토스 1m 도 `Timeframe` 에 없고, 라벨 없는
행으로 다루다 목표 시간축으로 승격했다. 여기서는 승격할 곳이 없으므로 `SpanBar` 라는
**표시·조회 전용 타입**으로 끝낸다.

## 경계는 **시장 현지시각**이다 — 다만 오늘은 그것이 무해하다

⚠️ **처음에 근거를 틀리게 적었고 테스트가 반증했다.** "뉴욕 1월 31일 16:00 종가는 UTC
로 2월 1일"이라고 썼는데, 16:00 EST 는 **21:00 UTC 같은 날**이다. 실제로 우리 세 시장의
**정규장 시간은 UTC 날짜와 절대 갈리지 않는다**:

| 시장 | 정규장 (현지) | UTC | 날짜가 갈리나 |
|---|---|---|---|
| KRX | 09:00~15:30 KST | 00:00~06:30 | ❌ |
| NASDAQ·NYSE | 09:30~16:00 ET | 13:30~21:00 | ❌ |
| 업비트 | 24시간 | — | 해당 없음 |

그러면 왜 현지시각으로 자르는가 — **비용이 0 이고, 갈리는 순간이 오면 조용히 틀리기
때문**이다. 갈리는 경우는 이미 존재한다:

- **시간외 거래** — 뉴욕 20:00 ET 는 UTC 로 **다음 날**이다 (아래 테스트가 그 경우다)
- 세션이 UTC 자정을 넘는 시장 (미국 선물의 일요일 18:00 ET 개장 등)

⛔ 개장 시각을 하드코딩하지 않는다 (절대 규칙 #7 · C2-3). `zoneinfo` 로 tz 데이터베이스를
쓰며, `tzdata` 를 의존성에 **명시 선언**했다 — 시스템 tz DB 가 없는 환경(슬림 컨테이너·
윈도우)에서 조용히 UTC 로 떨어지면 서머타임 구간이 한 시간씩 밀린다.

코인은 `UTC` 다. 24시간 장이라 "현지"가 없고, 임의의 거래소 소재지를 고르면 그것이
근거 없는 기준이 된다.

## 저장하지 않는다

이 모듈은 **조회 경로 전용**이다 (§1-0x 제로카피). 그래서 DB 마이그레이션이 없고,
`coverage_check` 대상도 아니다 — 원본(일봉)이 검증되면 이것은 그 함수다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Market


class CalendarSpanError(ValueError):
    """캘린더 묶기를 할 수 없다."""


class CalendarSpan(StrEnum):
    """달력이 경계를 정하는 묶음 단위.

    Attributes:
        WEEK: ISO 주 (월요일 시작).
        MONTH: 달력 월.

    Note:
        `Timeframe` 과 **일부러 분리한** 타입이다 (모듈 docstring). 이름이 비슷해도
        층이 다르다 — 저장되지 않고 `interval()` 을 갖지 않는다.
    """

    WEEK = "1w"
    MONTH = "1M"


#: 시장 → 경계를 자를 시간대. 코인은 24시간 장이라 UTC 다.
#:
#: ⛔ 여기 값을 하드코딩된 오프셋(`+09:00`)으로 적지 않는다. 서머타임이 있는 시장에서
#:    한 해의 절반이 한 시간씩 밀리고, 그것이 월 경계 근처 봉을 옆 달로 보낸다.
MARKET_ZONES: dict[Market, str] = {
    Market.KRX: "Asia/Seoul",
    Market.NASDAQ: "America/New_York",
    Market.NYSE: "America/New_York",
}
DEFAULT_ZONE = "UTC"


@dataclass(frozen=True, slots=True)
class SpanBar:
    """주봉·월봉 하나 — **조회 전용**이라 `Candle` 이 아니다.

    Attributes:
        span: 묶음 단위.
        label: 사람이 읽는 구간 이름 (`2026-01` · `2026-W03`). 시장 현지 기준이다.
        start: 구간 **첫 봉**의 시각 (UTC). 달의 1일이 아니라 실제 첫 거래일이다 —
            없는 날을 지어내지 않는다.
        open/high/low/close: 시가=첫 봉, 종가=마지막 봉, 고·저=구간 극값.
        volume: 합.
        bars: 묶인 원본 봉 수. **완결 판정의 근거**이며 표시할 때 같이 읽는다.
    """

    span: CalendarSpan
    label: str
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    bars: int


def zone_for(market: Market) -> tzinfo:
    """시장의 경계 시간대.

    Args:
        market: 시장.

    Returns:
        시간대. 표에 없으면 UTC (코인 등 24시간 장).

    Raises:
        CalendarSpanError: tz 데이터베이스에 해당 시간대가 없는 경우 — 조용히 UTC 로
            떨어지면 미국 월봉이 하루씩 밀린 채로 맞아 보인다 (절대 규칙 #8).
    """
    name = MARKET_ZONES.get(market, DEFAULT_ZONE)
    try:
        return ZoneInfo(name)
    # 넓게 잡는 것이 의도다 — `zoneinfo` 는 tz DB 부재를 플랫폼마다 다른 예외로 낸다
    # (`ZoneInfoNotFoundError`·`OSError`). 좁게 잡으면 어떤 환경에서만 조용히 샌다.
    except Exception as exc:
        raise CalendarSpanError(
            f"시간대 '{name}' 를 찾을 수 없다 — `tzdata` 가 설치돼 있는지 확인하라. "
            "UTC 로 대신하면 월 경계가 하루 밀린 채 그럴듯해 보인다 (C2-3)"
        ) from exc


def _label(moment: datetime, span: CalendarSpan) -> str:
    """구간 이름 — 이것이 곧 묶는 키다.

    Args:
        moment: **시장 현지시각**으로 변환된 시각.
        span: 묶음 단위.

    Returns:
        `2026-01` 또는 `2026-W03`.
    """
    if span is CalendarSpan.MONTH:
        return f"{moment.year:04d}-{moment.month:02d}"
    iso = moment.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def aggregate_calendar(
    candles: Sequence[Candle],
    span: CalendarSpan,
    market: Market,
) -> list[SpanBar]:
    """일봉을 주봉·월봉으로 묶는다.

    Args:
        candles: 일봉 (`ts` 오름차순). 상위 봉을 넣어도 동작하지만 의미는 호출부 책임이다.
        span: 묶음 단위.
        market: 경계 시간대를 정하는 시장.

    Returns:
        구간 오름차순 봉 목록.

    Raises:
        CalendarSpanError: 입력이 비었거나 `ts` 가 오름차순이 아닌 경우.

    Note:
        **불완전 구간을 버리지 않는다.** 이번 달은 아직 안 끝났지만 그 봉을 빼면 화면에서
        이번 달이 사라진다. `bars` 로 몇 봉이 묶였는지 함께 주고, "완결인가"의 판정은
        달력을 아는 호출부가 한다 — 여기서 "오늘이 말일인가"를 알려면 현재 시각을 봐야
        하고, 그러면 결정론이 깨진다 (원칙 P1).
    """
    if not candles:
        raise CalendarSpanError("빈 캔들은 묶을 수 없다 — 무엇을 묶을지 알 수 없다")

    zone = zone_for(market)
    groups: dict[str, list[Candle]] = {}
    order: list[str] = []
    previous: datetime | None = None
    for candle in candles:
        if previous is not None and candle.ts < previous:
            raise CalendarSpanError(
                f"ts 가 오름차순이 아니다: {previous} 다음에 {candle.ts} — "
                "정렬을 가정하고 시·종가를 뽑으므로 조용히 틀린 봉이 나온다"
            )
        previous = candle.ts
        key = _label(candle.ts.astimezone(zone), span)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(candle)

    bars: list[SpanBar] = []
    for key in order:
        group = groups[key]
        bars.append(
            SpanBar(
                span=span,
                label=key,
                start=group[0].ts,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum((item.volume for item in group), Decimal(0)),
                bars=len(group),
            )
        )
    return bars
