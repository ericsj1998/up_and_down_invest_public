"""시장 세션 판정 — **이 봉이 정규장인가** (spec §4.2 · §12.3 · C2-3 · C2-4).

## 왜 이 모듈이 생겼나 — 갭이 사라지고 있었다

적재된 주식 봉은 **24시간 전부에 있다.** AAPL 1h 은 UTC 0~23 시 전부에 존재하고,
005930 은 KST 08:00(장전)부터 20:00(시간외)까지 있다. 그런데 `Candle` 에 세션 필드가
없어서 정규장과 시간외를 구분할 수 없었다.

그 결과 갭이 데이터에서 사라졌다 (실측 · `docs/rules/stock_session_notes.md`):

| 갭 = 시가 ÷ 전일종가 - 1 의 절대값 | 005930 | AAPL |
|---|---|---|
| 정규장 기준 중앙값 | 0.571% | 0.414% |
| 시간외 포함 중앙값 | 0.206% | **0.008%** |

AAPL 은 **52배** 줄어든다. 24시간 봉이 있으니 "연속"이 되고, 갭이라는 사건 자체가
없어진다. `trading_flow.md` ⑦ 갭 검증이 통째로 작동하지 않는다는 뜻이다.

## ⛔ 시각을 코드에 박지 않는다

세션 창은 `config/market_sessions.yml` 에서 온다 (절대 규칙 #7). 그리고 **오프셋이
아니라 tz 이름**을 쓴다 — 미국은 서머타임으로 한 해의 절반이 UTC-4, 나머지가 UTC-5 다.
실측이 그것을 확인했다: AAPL 정규장 UTC 창이 13~20 과 14~21 사이를 오간다.

## 🔴 "모른다"를 "거래 가능"으로 접지 않는다

휴장일 목록이 없으면 `is_tradable()` 은 **`UNKNOWN` 을 돌려준다.** 크리스마스에
"정규장입니다"라고 답하는 것이 이 모듈이 막아야 할 실패다 (절대 규칙 #8).

`session_at()` 은 시계만 보므로 휴장일을 모르고, 그래서 **판정 결과의 이름이 다르다** —
`session_at` 은 "시각으로 보면 무슨 세션인가", `tradability` 는 "정말 거래되는가"다.
둘을 한 함수로 합치면 어느 쪽을 답한 것인지 알 수 없어진다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import yaml

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Market
from updown.common.domain.market import MarketSession

DEFAULT_CONFIG_PATH = Path("config/market_sessions.yml")
"""기본 세션 설정 경로 (`costs.py` 와 같은 규약)."""

_WEEKDAYS: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


class SessionConfigError(ValueError):
    """세션 설정이 성립하지 않는다.

    Note:
        기본값으로 이어가지 않는 이유: 세션 창이 틀리면 정규장 판정이 조용히 틀리고,
        그 위에서 잰 갭·시가·종가가 전부 다른 뜻이 된다 (절대 규칙 #8).
    """


class Tradability(StrEnum):
    """거래 가능 여부 — **3값이다**.

    Attributes:
        OPEN: 거래 가능.
        CLOSED: 거래 불가 (시각이 세션 밖이거나 영업일이 아니다).
        UNKNOWN: **판정 불가.** 휴장일 정보가 없어 영업일인지 알 수 없다.

    Note:
        🔴 `UNKNOWN` 을 `CLOSED` 로 접지 않는 이유는 둘의 **대응이 다르기** 때문이다.
        `CLOSED` 는 "기다린다"이고 `UNKNOWN` 은 "확인하고 나서 움직인다"다. 합치면
        휴장일 데이터가 없다는 사실이 화면에서 사라진다.
    """

    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """현지시각 기준 반열림 구간 `[start, end)`.

    Attributes:
        start: 시작 (포함).
        end: 종료 (**미포함**).

    Note:
        `end` 를 제외하는 이유: 15:30 정각 봉은 정규장 마지막 봉이 아니라 **그다음
        세션의 첫 봉**이다. 포함으로 두면 마감 봉이 두 세션에 걸친다.
    """

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        """이 창 안인가.

        Args:
            moment: 현지시각의 시분초.

        Returns:
            `start <= moment < end` 이면 True.

        Note:
            자정을 넘는 창(`22:00~02:00`)은 지원하지 않는다. 지금 세 시장에 없고,
            지원하는 척하면 실제로 그런 시장이 왔을 때 조용히 틀린다.
        """
        return self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class MarketHours:
    """한 시장의 운영 시각.

    Attributes:
        market: 시장.
        zone: 세션 창을 해석할 시간대. **오프셋이 아니라 tz DB 이름**이다.
        trading_weekdays: 영업 요일 (월=0). 주말 판정에 쓴다.
        pre_open: 장전 창. 없으면 None.
        regular: 정규장 창. `always_open` 이면 None.
        post: 장후 창. 없으면 None.
        always_open: 24시간 장인가 (코인).
        early_regular_end: 조기마감일의 정규장 종료 시각.
        early_post_end: 조기마감일의 장후 종료 시각.
    """

    market: Market
    zone: tzinfo
    trading_weekdays: frozenset[int]
    pre_open: SessionWindow | None
    regular: SessionWindow | None
    post: SessionWindow | None
    always_open: bool
    early_regular_end: time | None
    early_post_end: time | None


@dataclass(frozen=True, slots=True)
class MarketCalendar:
    """세션 창 + 휴장일 — 세션 판정의 단일 출처.

    Attributes:
        hours: 시장별 운영 시각.
        holidays: 시장별 휴장일 (현지 날짜).
        early_closes: 시장별 조기마감일 (현지 날짜).
        known_range: 시장별 `(첫 영업일, 마지막 영업일)`. 휴장일 정보가 **유효한
            구간**이며, 이 밖은 `UNKNOWN` 이다.

    Note:
        `known_range` 가 이 타입의 핵심이다. 휴장일 목록은 언제나 유한한 구간에만
        있는데, 그 사실을 안 들고 다니면 **목록에 없는 미래 날짜가 자동으로 영업일**이
        된다. 그것이 "조용히 틀린 채로 그럴듯한" 상태다.
    """

    hours: Mapping[Market, MarketHours]
    holidays: Mapping[Market, frozenset[date]]
    early_closes: Mapping[Market, frozenset[date]]
    known_range: Mapping[Market, tuple[date, date]]

    def with_trading_days(self, market: Market, days: Iterable[date]) -> MarketCalendar:
        """실제 거래일 목록으로 휴장일과 유효 구간을 채운다.

        Args:
            market: 대상 시장.
            days: 그 시장에 봉이 존재한 **현지 날짜**들.

        Returns:
            휴장일·유효 구간이 채워진 새 달력.

        Note:
            🔴 **휴장일을 목록으로 받지 않고 거꾸로 구한다.** 영업 요일인데 거래일에
            없는 날이 곧 휴장일이다. 휴장일 목록을 직접 받으면 "빠뜨린 휴장일"과
            "정말 영업일"을 구분할 수 없다.

            ⚠️ 이 방법은 **적재 구간 안에서만** 유효하다. 그래서 `known_range` 를
            함께 채우고, 그 밖은 `UNKNOWN` 으로 답한다. 실거래에는 미래 휴장일이
            필요하고 그것은 X-4(마켓 캘린더 데이터 소스) 소관이다.
        """
        known = sorted(set(days))
        if not known:
            return self
        hours = self.hours_for(market)
        first, last = known[0], known[-1]
        traded = set(known)
        holidays = {
            day
            for day in _walk_days(first, last)
            if day.weekday() in hours.trading_weekdays and day not in traded
        }
        return MarketCalendar(
            hours=self.hours,
            holidays={**self.holidays, market: frozenset(holidays)},
            early_closes=self.early_closes,
            known_range={**self.known_range, market: (first, last)},
        )

    def hours_for(self, market: Market) -> MarketHours:
        """시장의 운영 시각.

        Args:
            market: 시장.

        Returns:
            운영 시각.

        Raises:
            SessionConfigError: 설정에 없는 시장. 기본값으로 24시간 장을 가정하면
                주식이 밤에도 열린 것으로 판정된다.
        """
        found = self.hours.get(market)
        if found is None:
            raise SessionConfigError(
                f"{market} 의 세션 설정이 없다 — 기본값을 가정하지 않는다. "
                f"config/market_sessions.yml 에 추가하라"
            )
        return found

    def local(self, market: Market, moment: datetime) -> datetime:
        """UTC 시각을 시장 현지시각으로.

        Args:
            market: 시장.
            moment: UTC aware 시각.

        Returns:
            현지시각.

        Raises:
            SessionConfigError: `moment` 가 naive 인 경우.
        """
        if moment.tzinfo is None:
            raise SessionConfigError(
                "naive 시각은 변환할 수 없다 — 어느 시간대인지 모르는 값을 현지시각으로 "
                "바꾸면 결과가 그럴듯하게 틀린다 (절대 규칙 #7)"
            )
        return moment.astimezone(self.hours_for(market).zone)

    def trading_date(self, market: Market, moment: datetime) -> date:
        """이 시각이 속한 **현지 달력 날짜**.

        Args:
            market: 시장.
            moment: UTC aware 시각.

        Returns:
            현지 날짜.

        Note:
            🔴 UTC 날짜를 쓰면 안 되는 이유가 실측으로 나왔다 — KRX 일봉 511개 중
            **101개가 UTC 기준 토·일**이다. 일봉이 KST 자정(=UTC 15:00 전날)에 찍히기
            때문에 **월요일 봉이 일요일에 떨어진다.** UTC 요일로 거래일을 판정하면
            KRX 월요일이 전부 주말로 분류된다.
        """
        return self.local(market, moment).date()

    def session_at(self, market: Market, moment: datetime) -> MarketSession:
        """**시계만 보고** 무슨 세션인지 답한다.

        Args:
            market: 시장.
            moment: UTC aware 시각.

        Returns:
            세션. 어느 창에도 안 들면 `CLOSED`.

        Note:
            ⚠️ **휴장일을 모른다.** 크리스마스 10:00 에도 `REGULAR` 를 돌려준다.
            "정말 거래되는가"는 `tradability()` 가 답한다 — 둘을 합치지 않은 이유는
            모듈 docstring 에 있다.

            봉을 분류할 때는 이 함수가 맞다. 봉이 존재한다는 것 자체가 그날 장이
            열렸다는 증거이므로 휴장일 판정이 필요 없다.
        """
        hours = self.hours_for(market)
        if hours.always_open:
            return MarketSession.ALWAYS_OPEN

        local = self.local(market, moment)
        if local.weekday() not in hours.trading_weekdays:
            return MarketSession.CLOSED

        clock = local.time()
        early = local.date() in self.early_closes.get(market, frozenset())

        regular = hours.regular
        if regular is not None:
            end = hours.early_regular_end if early and hours.early_regular_end else regular.end
            if SessionWindow(regular.start, end).contains(clock):
                return MarketSession.REGULAR

        if hours.pre_open is not None and hours.pre_open.contains(clock):
            return MarketSession.PRE_OPEN

        post = hours.post
        if post is not None:
            # 조기마감일은 장후도 함께 당겨진다. 정규장만 당기면 앞당겨진 마감과
            # 원래 장후 시작 사이가 빈 채로 남아, 그 시간의 봉이 어디에도 안 속한다.
            start = hours.early_regular_end if early and hours.early_regular_end else post.start
            end = hours.early_post_end if early and hours.early_post_end else post.end
            if start < end and SessionWindow(start, end).contains(clock):
                return MarketSession.POST_CLOSE

        return MarketSession.CLOSED

    def is_regular(self, market: Market, moment: datetime) -> bool:
        """정규장인가 (코인은 항상 True).

        Args:
            market: 시장.
            moment: UTC aware 시각.

        Returns:
            정규장이면 True.

        Note:
            코인이 True 인 이유: 24시간 장에는 "정규장 아닌 시간"이 없다. False 로
            두면 코인 봉이 전부 걸러진다.
        """
        session = self.session_at(market, moment)
        return session in (MarketSession.REGULAR, MarketSession.ALWAYS_OPEN)

    def next_events(
        self, market: Market, moment: datetime
    ) -> tuple[datetime | None, datetime | None]:
        """다음 정규장 **개장·마감** 시각 (UTC) — 화면·장 상태의 `next_open`/`next_close` (T240).

        Args:
            market: 시장.
            moment: UTC aware 시각.

        Returns:
            `(다음 개장, 다음 마감)`. 24시간 장이거나 휴장일 정보가 없는 구간이면 `(None, None)` —
            모르는 것을 그럴듯한 값으로 채우지 않는다 (절대 규칙 #8).

        Note:
            정규장 안이면 "다음 마감" 은 오늘 마감(조기마감일은 당겨진 시각)이고 "다음 개장" 은
            다음 영업일이다. 최대 3주를 걷는다 — 연휴가 그보다 길지는 않다.
        """
        hours = self.hours_for(market)
        regular = hours.regular
        if hours.always_open or regular is None:
            return None, None
        known = self.known_range.get(market)
        local = self.local(market, moment)
        if known is None or not known[0] <= local.date() <= known[1]:
            return None, None
        holidays = self.holidays.get(market, frozenset())
        early_days = self.early_closes.get(market, frozenset())
        next_open: datetime | None = None
        next_close: datetime | None = None
        for offset in range(21):
            day = local.date() + timedelta(days=offset)
            if day.weekday() not in hours.trading_weekdays or day in holidays:
                continue
            end_at = regular.end
            if day in early_days and hours.early_regular_end is not None:
                end_at = hours.early_regular_end
            opens = datetime.combine(day, regular.start, tzinfo=hours.zone)
            closes = datetime.combine(day, end_at, tzinfo=hours.zone)
            if next_close is None and closes > local:
                next_close = closes
            if next_open is None and opens > local:
                next_open = opens
            if next_open is not None and next_close is not None:
                break
        return (
            None if next_open is None else next_open.astimezone(UTC),
            None if next_close is None else next_close.astimezone(UTC),
        )

    def tradability(self, market: Market, moment: datetime) -> tuple[Tradability, str]:
        """**정말 거래되는가** — 이유와 함께.

        Args:
            market: 시장.
            moment: UTC aware 시각.

        Returns:
            `(판정, 이유)`. 이유는 화면과 로그에 그대로 실린다.

        Note:
            🔴 `bool` 이 아닌 이유는 "왜 못 사나"에 답해야 하기 때문이다.
            그리고 3값인 이유는 **휴장일을 모르는 상태**가 실제로 있기 때문이다 —
            지금 `config/market_sessions.yml` 의 `holidays` 는 비어 있다 (X-4 미착수).
        """
        hours = self.hours_for(market)
        if hours.always_open:
            return Tradability.OPEN, "24시간 장"

        local = self.local(market, moment)
        today = local.date()

        if local.weekday() not in hours.trading_weekdays:
            return Tradability.CLOSED, f"영업 요일이 아니다 ({today:%a})"

        known = self.known_range.get(market)
        if known is None:
            return (
                Tradability.UNKNOWN,
                "휴장일 정보가 없다 — 영업일인지 알 수 없으므로 '거래 가능'이라고 "
                "답하지 않는다 (X-4 마켓 캘린더 미착수)",
            )
        if not known[0] <= today <= known[1]:
            return (
                Tradability.UNKNOWN,
                f"휴장일 정보가 {known[0]}~{known[1]} 까지만 있다 — {today} 는 그 밖이다",
            )
        if today in self.holidays.get(market, frozenset()):
            return Tradability.CLOSED, f"휴장일 ({today})"

        session = self.session_at(market, moment)
        if session is MarketSession.REGULAR:
            return Tradability.OPEN, "정규장"
        if session is MarketSession.PRE_OPEN:
            return Tradability.CLOSED, "장전 — 정규장 개시 전이다"
        return Tradability.CLOSED, f"정규장 시간이 아니다 ({local:%H:%M} 현지)"


def _walk_days(first: date, last: date) -> Iterable[date]:
    """`first`~`last` 사이 모든 날짜 (양끝 포함).

    Args:
        first: 시작 날짜.
        last: 끝 날짜.

    Yields:
        날짜.
    """
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


# ---------------------------------------------------------------------------
# 봉 거르기
# ---------------------------------------------------------------------------


def regular_only(candles: Sequence[Candle], calendar: MarketCalendar) -> list[Candle]:
    """정규장 봉만 남긴다.

    Args:
        candles: 봉 목록. 여러 종목이 섞여 있어도 된다 (봉마다 시장을 읽는다).
        calendar: 세션 달력.

    Returns:
        정규장 봉만.

    Note:
        🔴 **이 함수를 안 쓰면 갭이 사라진다.** 시간외 봉이 전일 종가와 당일 시가
        사이를 메워서, AAPL 갭 중앙값이 0.414% → 0.008% 로 줄어든다 (실측).
    """
    return [c for c in candles if calendar.is_regular(c.instrument.market, c.ts)]


def daily_from_regular(
    candles: Sequence[Candle], calendar: MarketCalendar
) -> list[tuple[date, Candle]]:
    """정규장 봉을 **현지 거래일**로 묶어 일봉 재료를 만든다.

    Args:
        candles: 하위 시간축 봉 (`ts` 오름차순). 여러 종목을 섞지 않는다.
        calendar: 세션 달력.

    Returns:
        `(현지 거래일, 그날 첫 봉)` 목록. 시·종·고·저 합성은 호출부가 한다 —
        여기서 하면 이 모듈이 집계 책임까지 갖는다.

    Note:
        🔴 **적재된 KRX 일봉을 그대로 쓰면 안 되는 이유가 이것이다.** 실측에서
        `1d.open` 이 08:00 장전 봉 시가와 199/199 일치했고 정규장 시가와는 7/199 만
        일치했다. 즉 KRX 일봉의 시·종가는 **시간외 값**이다.
    """
    grouped: dict[date, Candle] = {}
    for candle in regular_only(candles, calendar):
        day = calendar.trading_date(candle.instrument.market, candle.ts)
        if day not in grouped:
            grouped[day] = candle
    return sorted(grouped.items())


def trading_days_from_candles(
    candles: Iterable[Candle], calendar: MarketCalendar, market: Market
) -> frozenset[date]:
    """적재된 봉에서 **실제 거래일**을 읽는다.

    Args:
        candles: 봉.
        calendar: 세션 달력.
        market: 대상 시장.

    Returns:
        봉이 존재하는 현지 날짜 집합.

    Note:
        휴장일 목록의 **부트스트랩**이다. 봉이 있다는 것은 그날 장이 열렸다는 뜻이므로,
        영업 요일인데 봉이 없는 날이 곧 휴장일이다.

        ⚠️ **적재 구간 안에서만 유효하다.** 미래 휴장일은 이 방법으로 알 수 없고,
        그래서 `MarketCalendar.known_range` 가 필요하다 (실거래에는 X-4 가 필요하다).
    """
    return frozenset(
        calendar.trading_date(market, c.ts) for c in candles if c.instrument.market is market
    )


# ---------------------------------------------------------------------------
# 설정 읽기
# ---------------------------------------------------------------------------


def _zone(name: str) -> tzinfo:
    """Tz 이름을 시간대로.

    Args:
        name: tz DB 이름.

    Returns:
        시간대.

    Raises:
        SessionConfigError: tz DB 에 없는 경우. 조용히 UTC 로 떨어지면 미국 세션이
            서머타임 구간에서 한 시간 밀린 채 그럴듯해 보인다 (C2-3).
    """
    try:
        return ZoneInfo(name)
    # 넓게 잡는 것이 의도다 — `zoneinfo` 는 tz DB 부재를 플랫폼마다 다른 예외로 낸다.
    except Exception as exc:
        raise SessionConfigError(
            f"시간대 '{name}' 를 찾을 수 없다 — `tzdata` 가 설치돼 있는지 확인하라. "
            f"UTC 로 대신하면 서머타임 구간이 한 시간 밀린 채 맞아 보인다 (C2-3)"
        ) from exc


def _time(raw: object, field: str) -> time:
    """`HH:MM` 문자열을 시각으로.

    Args:
        raw: 원본 값.
        field: 오류 메시지용 필드 이름.

    Returns:
        시각.

    Raises:
        SessionConfigError: 형식이 틀린 경우.
    """
    if not isinstance(raw, str):
        raise SessionConfigError(f"{field} 는 'HH:MM' 문자열이어야 한다 — 받은 값: {raw!r}")
    try:
        return time.fromisoformat(raw)
    except ValueError as exc:
        raise SessionConfigError(f"{field} 를 시각으로 읽을 수 없다: {raw!r}") from exc


def _window(raw: object, field: str) -> SessionWindow:
    """`{start, end}` 매핑을 창으로.

    Args:
        raw: 원본 값.
        field: 오류 메시지용 필드 이름.

    Returns:
        세션 창.

    Raises:
        SessionConfigError: 형식이 틀리거나 `start >= end` 인 경우.
    """
    if not isinstance(raw, dict):
        raise SessionConfigError(f"{field} 는 매핑이어야 한다 — 받은 값: {raw!r}")
    fields = cast(Mapping[str, object], raw)
    start = _time(fields.get("start"), f"{field}.start")
    end = _time(fields.get("end"), f"{field}.end")
    if start >= end:
        raise SessionConfigError(
            f"{field} 의 start({start}) 가 end({end}) 이상이다 — 자정을 넘는 창은 "
            f"지원하지 않는다. 지원하는 척하면 그런 시장이 왔을 때 조용히 틀린다"
        )
    return SessionWindow(start, end)


def _dates(raw: object, field: str) -> frozenset[date]:
    """`YYYY-MM-DD` 목록을 날짜 집합으로.

    Args:
        raw: 원본 값.
        field: 오류 메시지용 필드 이름.

    Returns:
        날짜 집합.

    Raises:
        SessionConfigError: 형식이 틀린 경우.
    """
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise SessionConfigError(f"{field} 는 목록이어야 한다 — 받은 값: {raw!r}")
    out: set[date] = set()
    for item in cast(list[object], raw):
        if isinstance(item, date):
            out.add(item)
            continue
        if not isinstance(item, str):
            raise SessionConfigError(f"{field} 항목은 'YYYY-MM-DD' 여야 한다: {item!r}")
        try:
            out.add(date.fromisoformat(item))
        except ValueError as exc:
            raise SessionConfigError(f"{field} 항목을 날짜로 읽을 수 없다: {item!r}") from exc
    return frozenset(out)


def parse_calendar(raw: Mapping[str, object]) -> MarketCalendar:
    """설정 매핑을 달력으로.

    Args:
        raw: 파싱된 YAML.

    Returns:
        세션 달력.

    Raises:
        SessionConfigError: 필수 항목이 없거나 형식이 틀린 경우.
    """
    markets_raw = raw.get("markets")
    if not isinstance(markets_raw, dict):
        raise SessionConfigError("`markets` 매핑이 없다 — 세션 설정의 본체다")

    hours: dict[Market, MarketHours] = {}
    for key, value in cast(Mapping[str, object], markets_raw).items():
        try:
            market = Market(key)
        except ValueError as exc:
            raise SessionConfigError(
                f"'{key}' 는 알 수 없는 시장이다 — Market 열거형에 없다"
            ) from exc
        if not isinstance(value, dict):
            raise SessionConfigError(f"markets.{key} 는 매핑이어야 한다")
        block = cast(Mapping[str, object], value)

        zone_name = block.get("timezone")
        if not isinstance(zone_name, str):
            raise SessionConfigError(
                f"markets.{key}.timezone 이 없다 — 오프셋이 아니라 tz 이름을 적는다"
            )

        always_open = bool(block.get("always_open", False))
        sessions = cast(Mapping[str, object], block.get("sessions") or {})
        if not always_open and "regular" not in sessions:
            raise SessionConfigError(
                f"markets.{key} 에 정규장 창이 없다 — 24시간 장이면 always_open: true 를 "
                f"명시한다. 빠뜨린 것과 24시간인 것을 구분해야 한다"
            )

        days_raw: object = block.get("trading_days") or []
        if not isinstance(days_raw, list):
            raise SessionConfigError(f"markets.{key}.trading_days 는 목록이어야 한다")
        weekdays: set[int] = set()
        for day in cast(list[object], days_raw):
            index = _WEEKDAYS.get(str(day).lower())
            if index is None:
                raise SessionConfigError(
                    f"markets.{key}.trading_days 에 알 수 없는 요일: {day!r} (mon~sun 중 하나)"
                )
            weekdays.add(index)
        if not always_open and not weekdays:
            raise SessionConfigError(
                f"markets.{key}.trading_days 가 비었다 — 비어 있으면 모든 날이 휴장이 된다"
            )

        early = cast(Mapping[str, object], block.get("early_close") or {})
        hours[market] = MarketHours(
            market=market,
            zone=_zone(zone_name),
            trading_weekdays=frozenset(weekdays),
            pre_open=(
                _window(sessions["pre_open"], f"markets.{key}.sessions.pre_open")
                if "pre_open" in sessions
                else None
            ),
            regular=(
                _window(sessions["regular"], f"markets.{key}.sessions.regular")
                if "regular" in sessions
                else None
            ),
            post=(
                _window(sessions["post"], f"markets.{key}.sessions.post")
                if "post" in sessions
                else None
            ),
            always_open=always_open,
            early_regular_end=(
                _time(early["regular_end"], f"markets.{key}.early_close.regular_end")
                if "regular_end" in early
                else None
            ),
            early_post_end=(
                _time(early["post_end"], f"markets.{key}.early_close.post_end")
                if "post_end" in early
                else None
            ),
        )

    def _per_market(node: object, field: str) -> dict[Market, frozenset[date]]:
        if node is None:
            return {}
        if not isinstance(node, dict):
            raise SessionConfigError(f"`{field}` 는 시장별 매핑이어야 한다")
        out: dict[Market, frozenset[date]] = {}
        for key, value in cast(Mapping[str, object], node).items():
            out[Market(key)] = _dates(value, f"{field}.{key}")
        return out

    holidays = _per_market(raw.get("holidays"), "holidays")
    coverage = _coverage(raw.get("holiday_coverage"))
    for market in holidays:
        if holidays[market] and market not in coverage:
            raise SessionConfigError(
                f"holidays.{market} 는 있는데 holiday_coverage.{market} 가 없다 — 유효 구간을"
                " 같이 적어야 그 밖의 날짜를 UNKNOWN 으로 답할 수 있다 (T238)"
            )
    return MarketCalendar(
        hours=hours,
        holidays=holidays,
        early_closes=_per_market(raw.get("early_close_days"), "early_close_days"),
        known_range=coverage,
    )


def _coverage(node: object) -> dict[Market, tuple[date, date]]:
    """`holiday_coverage` — 시장별 `{from, to}` 를 `known_range` 로 (T238).

    Args:
        node: 파싱된 YAML 의 `holiday_coverage` 값. None 이면 빈 매핑.

    Returns:
        시장 → (첫 날, 마지막 날).

    Raises:
        SessionConfigError: 형식이 틀리거나 `from > to` 인 경우.
    """
    if node is None:
        return {}
    if not isinstance(node, dict):
        raise SessionConfigError("`holiday_coverage` 는 시장별 매핑이어야 한다")
    out: dict[Market, tuple[date, date]] = {}
    for key, value in cast(Mapping[str, object], node).items():
        if not isinstance(value, dict):
            raise SessionConfigError(f"holiday_coverage.{key} 는 {{from, to}} 매핑이어야 한다")
        block = cast(Mapping[str, object], value)
        first = _dates([block.get("from")], f"holiday_coverage.{key}.from")
        last = _dates([block.get("to")], f"holiday_coverage.{key}.to")
        lo, hi = next(iter(first)), next(iter(last))
        if lo > hi:
            raise SessionConfigError(f"holiday_coverage.{key}: from({lo}) 이 to({hi}) 보다 늦다")
        out[Market(key)] = (lo, hi)
    return out


def load_calendar(path: Path | None = None) -> MarketCalendar:
    """세션 달력을 파일에서 읽는다.

    Args:
        path: 설정 경로. None 이면 `DEFAULT_CONFIG_PATH`.

    Returns:
        세션 달력.

    Raises:
        SessionConfigError: 파일이 없거나 파싱·검증에 실패한 경우.

    Note:
        ⚠️ **파일 부재를 허용하지 않는다** (`costs.py` 와 같은 판단). 세션 창에는
        "안전한 기본값"이 없다 — 24시간으로 떨어지면 주식이 밤에도 열린 것으로
        판정되고, 그 위에서 잰 갭·시가·종가가 전부 다른 뜻이 된다.
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        raise SessionConfigError(
            f"{target} 가 없다 — 세션 창에는 '안전한 기본값'이 없으므로 기본값으로 "
            f"진행하지 않는다 (절대 규칙 #8)"
        )
    try:
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SessionConfigError(f"{target} 를 읽을 수 없다: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SessionConfigError(f"{target} 최상위는 매핑이어야 한다 — 받은 값: {type(parsed)}")
    return parse_calendar(cast(Mapping[str, object], parsed))
