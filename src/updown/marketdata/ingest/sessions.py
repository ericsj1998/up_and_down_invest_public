"""거래 세션 — 주식의 "정상적인 구멍"과 "결측"을 가른다 (P1 §1-0j).

## 문제

`find_missing_ranges` 는 봉 사이의 모든 구멍을 결측으로 센다. 코인은 24시간 장이라 그것이
맞지만(spec §7), 주식은 **매일 밤과 주말에 정상적으로 구멍이 생긴다**. 그대로 쓰면 3년치
적재가 결측 투성이로 나오고, 진짜 결측이 그 안에 묻힌다.

## 왜 마켓 캘린더 API 로 못 푸는가

토스 `/api/v1/market-calendar/{KR,US}` 는 `today`·`previousBusinessDay`·`nextBusinessDay`
**3일 창**이다. "지금 장이 열렸나"(C2-3·C2-4)에는 답하지만 **"2024년 어느 날이 휴장이었나"
에는 답하지 않는다.**

## 그래서 데이터가 스스로 말하게 한다

개장 시각을 코드에 박는 것은 절대 규칙 #7 위반이고, 서머타임·조기마감 때문에 틀리기도 한다.
대신 **이미 받은 일봉**을 거래일 달력으로 쓴다:

| 층 | 출처 | 성격 |
|---|---|---|
| 어느 날이 거래일인가 | **일봉이 존재하는 날짜** | 브로커가 준 사실 |
| 거래일의 경계 | **일봉 `ts` 자체** | 관측값 |

🔴 **핵심**: 일봉 `ts` 는 그 시장의 **현지 자정**이다
(실측 — docs/platform/toss_api_notes.md 함정 ⑧):

| 종목 | 일봉 `ts` (UTC) | 현지 |
|---|---|---|
| 005930 (KRX) | `2026-08-05T15:00:00Z` | 8/6 00:00 KST |
| AAPL (NASDAQ) | `2026-08-06T04:00:00Z` | 8/6 00:00 EDT |

즉 **일봉 타임스탬프가 곧 거래일 경계**이므로, tz 데이터베이스도 개장 시각도 필요 없다.
서머타임 전환은 브로커가 준 일봉 `ts` 에 이미 반영돼 있다.

⚠️ **UTC 날짜로 묶지 않는 이유**: KRX 장전 동시호가(08:00 KST)는 UTC 로 **전날 23:00** 이고,
미국 애프터마켓(20:00 ET)은 UTC 로 **다음날 00:00** 이다. UTC 날짜로 묶으면 이 봉들이
엉뚱한 날에 붙어 세션 판정이 조용히 틀린다.
"""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Market

#: 거래일 하나의 길이. 일봉 `ts` 가 현지 자정이므로 다음 자정까지가 그 거래일이다.
#:
#: ⚠️ 서머타임 전환일은 현지 기준 23시간 또는 25시간이다. 그래도 24시간을 쓰는 이유는
#: **연속한 일봉 `ts` 가 있으면 그것을 경계로 쓰고**(정확), 마지막 거래일에만 이 값이
#: 쓰이기 때문이다 (`_span_of`).
NOMINAL_TRADING_DAY = timedelta(hours=24)

#: 24시간 장 시장 — 세션 개념이 없다 (spec §7).
ALWAYS_OPEN_MARKETS: frozenset[Market] = frozenset({Market.UPBIT, Market.GATE})


class SessionCalendarError(ValueError):
    """세션 달력을 만들 수 없다.

    Note:
        조용히 "전부 한 세션"으로 처리하지 않는다 — 그러면 주식의 모든 야간 구멍이
        결측으로 잡히고, 그것이 이 모듈이 존재하는 이유다 (절대 규칙 #8).
    """


@dataclass(frozen=True, slots=True)
class SessionCalendar:
    """거래일 경계 목록.

    Attributes:
        boundaries: 거래일 시작 시각들 (오름차순, UTC). 일봉 `ts` 그대로다.

    Note:
        `always_open` 이면 `boundaries` 가 비어 있고 모든 시각이 같은 세션에 속한다 —
        코인의 기존 동작이 그대로 유지된다.
    """

    boundaries: tuple[datetime, ...]
    always_open: bool = False

    @classmethod
    def always(cls) -> "SessionCalendar":
        """24시간 장 달력 — 세션 경계가 없다.

        Returns:
            경계가 빈 달력. 코인은 이것이고, 주식은 마켓 캘린더(C2-4)가 선행이다.
        """
        return cls(boundaries=(), always_open=True)

    @classmethod
    def from_daily(cls, daily: Sequence[Candle]) -> "SessionCalendar":
        """일봉에서 거래일 달력을 만든다.

        Args:
            daily: 일봉 (`ts` 순서는 상관없다 — 여기서 정렬한다).

        Returns:
            거래일 경계 달력.

        Raises:
            SessionCalendarError: 일봉이 비었거나 naive 시각이 섞인 경우.

        Note:
            **일봉이 있는 날 = 거래일**이다. 휴장일에는 일봉이 없으므로 휴장일 목록을
            따로 알 필요가 없다. 이것이 과거 캘린더 API 없이 3년치를 판정하는 방법이다.
        """
        if not daily:
            raise SessionCalendarError(
                "일봉이 없어 거래일 달력을 만들 수 없다 — 분봉 결측 판정에는 "
                "같은 종목·같은 구간의 일봉이 선행되어야 한다 (P1 §1-0j)"
            )
        stamps: list[datetime] = []
        for bar in daily:
            if bar.ts.tzinfo is None:
                raise SessionCalendarError(f"일봉 ts 가 naive 다: {bar.ts!r} (절대 규칙 #7)")
            stamps.append(bar.ts.astimezone(UTC))
        return cls(boundaries=tuple(sorted(set(stamps))))

    def _span_of(self, index: int) -> tuple[datetime, datetime]:
        """`index` 번째 거래일의 `[시작, 끝)`.

        Args:
            index: 경계 인덱스.

        Returns:
            시작·끝 시각.

        Note:
            끝은 **다음 거래일 시작**이다 — 그래야 서머타임 전환일의 23/25시간이 정확히
            반영된다. 마지막 거래일만 명목 24시간을 쓴다 (다음 경계를 모르기 때문이다).

            ⚠️ 다음 경계가 하루보다 멀면(주말·연휴) 24시간으로 자른다. 안 자르면 금요일
            세션이 월요일 개장까지 이어져, 주말 구멍이 "세션 내부"로 잘못 분류된다.
        """
        start = self.boundaries[index]
        if index + 1 < len(self.boundaries):
            return start, min(self.boundaries[index + 1], start + NOMINAL_TRADING_DAY)
        return start, start + NOMINAL_TRADING_DAY

    def trading_day(self, moment: datetime) -> datetime | None:
        """이 시각이 속한 거래일의 시작 시각.

        Args:
            moment: 대상 시각 (UTC aware).

        Returns:
            거래일 시작 시각. 거래일 밖(야간·주말·휴장)이면 None.

        Note:
            None 은 오류가 아니라 **"장이 닫혀 있었다"** 는 답이다.
        """
        if self.always_open:
            return _EPOCH
        if not self.boundaries:
            return None
        index = bisect_right(self.boundaries, moment.astimezone(UTC)) - 1
        if index < 0:
            return None
        start, end = self._span_of(index)
        return start if start <= moment.astimezone(UTC) < end else None

    def same_session(self, first: datetime, second: datetime) -> bool:
        """두 시각이 **같은 거래일 세션 안**에 있는가.

        Args:
            first: 앞 시각.
            second: 뒤 시각.

        Returns:
            둘 다 거래일 안이고 같은 날이면 True.

        Note:
            이것이 결측 판정의 기준이다 — 같은 세션 안의 구멍만 결함이다. 세션이 다르면
            그 사이는 **정상적인 마감**이므로 결측이 아니다.
        """
        left = self.trading_day(first)
        if left is None:
            return False
        return left == self.trading_day(second)

    def trading_days_in(self, start: datetime, end: datetime) -> tuple[datetime, ...]:
        """`[start, end]` 안의 거래일 시작 시각들.

        Args:
            start: 구간 시작.
            end: 구간 끝.

        Returns:
            거래일 시작 시각들 (오름차순).

        Note:
            "일봉은 있는데 분봉이 하나도 없는 날"을 찾는 데 쓴다 — 그것은 세션 내부
            구멍이 아니라서 `same_session` 판정에 걸리지 않으므로 따로 봐야 한다.
        """
        if self.always_open:
            return ()
        lower = start.astimezone(UTC)
        upper = end.astimezone(UTC)
        return tuple(day for day in self.boundaries if lower <= day <= upper)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""24시간 장의 단일 세션 식별자.

값 자체에 의미는 없다 — `same_session` 이 "둘 다 같은 세션"이라고 답하게 하는 상수다.
"""


def calendar_for(market: Market, daily: Sequence[Candle] | None) -> SessionCalendar:
    """시장에 맞는 세션 달력을 만든다.

    Args:
        market: 대상 시장.
        daily: 일봉. 24시간 장이면 무시된다.

    Returns:
        세션 달력.

    Raises:
        SessionCalendarError: 주식인데 일봉이 없는 경우.

    Note:
        코인에 대해 일봉을 요구하지 않는 이유는 **필요가 없어서**다 — 휴장이 없으므로
        모든 구멍이 결함이고, 그것이 기존 동작이다. 여기서 분기해 두면 호출부가
        `if market == UPBIT` 을 쓰지 않아도 된다 (`Capability` 와 같은 이유).
    """
    if market in ALWAYS_OPEN_MARKETS:
        return SessionCalendar.always()
    return SessionCalendar.from_daily(daily or ())
