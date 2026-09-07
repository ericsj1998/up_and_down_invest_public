"""A-5 레벨 — LVL-03/09/10/11/12 (4순위 · 자유도 0인 것들만).

## 🔴 왜 이것부터인가 — **과탐지가 원리적으로 불가능하다**

추세선·오더블록이 무너진 원인은 **조합**이었다. 스윙 40개에서 선 쌍 780가지가
나오고 그중 우연한 3접점이 잔뜩 생겨, 합류가 필터 기능을 잃었다
([rule_candidates.md](../../../../docs/rules/rule_candidates.md) 축 J).

여기 있는 레벨들은 그 함정이 **구조적으로 없다**:

    전일 고가는 하루에 **하나**다. 고를 여지가 없으므로 많이 만들 수도 없다.

즉 *"몇 개나 나오는가"* 를 우리가 못 정한다. 그래서 귀무모형 없이도 시작할 수 있고,
LVL-01/02(수평 지지/저항)·볼륨 프로파일처럼 **개수를 우리가 정하는** 것들은
귀무모형을 갖춘 뒤에 붙인다.

## ⭐ *"레벨에 반응했다"* 의 정의 — 뚫고 **되돌아 닫는다**

    지지  직전 종가가 레벨 위 · 이번 봉 저가가 레벨 **아래** · 종가는 다시 **위**
    저항  직전 종가가 레벨 아래 · 이번 봉 고가가 레벨 **위** · 종가는 다시 **아래**

⛔ *"근처에 왔다"* 로 잡지 않는다. 근처는 허용 오차를 정해야 하고, 허용 오차를
정하는 순간 그것이 **개수를 정하는 손잡이**가 되어 과탐지 함정이 돌아온다
(허용 오차 ATR 전환으로도 안 풀렸던 것이 축 I 다).

⚠️ 대신 이 정의는 **꼬리 하나로 판정**한다 — 봉 안에서 정말 그 순서로 움직였는지는
모른다. 저가가 종가보다 먼저 왔다는 보장이 없다. 그래서 방아쇠는 **그 봉의 종가로
확정**되고 진입은 다음 봉이다 (`fill.enter` 가 강제한다).

## ⚠️ 레벨은 **끝난 기간**에서만 온다

전일 고가는 그 날이 끝나야 알고, 전주 고가는 그 주가 끝나야 안다. 진행 중인 기간의
극값을 쓰면 미래 참조다 — 여기서 한 봉만 어긋나도 성적이 통째로 거짓이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "AnchoredVwap",
    "DailyOpen",
    "PreviousDay",
    "RoundNumber",
    "WeeklyMonthly",
    "reactions",
]


def reactions(board: Board, levels: list[list[float]]) -> list[Trigger]:
    """레벨 목록을 받아 **뚫고 되돌아 닫은** 봉들을 낸다.

    Args:
        board: 봉.
        levels: 봉마다 그 시점에 **이미 알고 있는** 레벨들. 길이는 봉 수와 같다.

    Returns:
        방아쇠들 — 오름차순.

    Note:
        🔴 봉마다 다른 레벨 묶음을 받는다. 전역 목록 하나를 받으면 *"그 시점에
        알 수 있었나"* 를 호출부가 지켜야 하는데, 그 책임을 여기로 올리면 신호마다
        같은 실수를 반복할 자리가 생긴다.

        ⚠️ 한 봉이 여러 레벨을 동시에 뚫으면 **한 번만** 센다. 레벨이 촘촘한 구간이
        방아쇠 수를 부풀리면 그것이 곧 과탐지다.
    """
    close = board.frame.close
    high = board.frame.high
    low = board.frame.low
    found: list[Trigger] = []
    for index in range(1, len(close)):
        before = close[index - 1]
        way: Direction | None = None
        for level in levels[index]:
            if level <= 0:
                continue
            if before > level and low[index] <= level < close[index]:
                way = Direction.LONG
                break
            if before < level and high[index] >= level > close[index]:
                way = Direction.SHORT
                break
        if way is not None:
            found.append(Trigger(index=index, direction=way))
    return found


def _period(moment: int, unit: str) -> tuple[int, ...]:
    """이 시각이 속한 기간의 열쇠 (UTC).

    Args:
        moment: 봉 시각(ms).
        unit: `"day"` · `"week"` · `"month"`.

    Returns:
        같은 기간이면 같은 값.

    Raises:
        ValueError: 모르는 단위.
    """
    when = datetime.fromtimestamp(moment / 1000, tz=UTC)
    if unit == "day":
        return (when.year, when.month, when.day)
    if unit == "week":
        iso = when.isocalendar()
        return (iso.year, iso.week)
    if unit == "month":
        return (when.year, when.month)
    raise ValueError(f"모르는 기간 단위: {unit}")


def _closed(board: Board, unit: str) -> list[tuple[float, float, float, float] | None]:
    """봉마다 **직전에 끝난** 기간의 (시가, 고가, 저가, 종가). 없으면 None.

    Note:
        🔴 진행 중인 기간은 절대 안 넘긴다. 이 함수가 미래 참조를 막는 유일한
        지점이라, 신호마다 다시 세지 않고 여기 하나로 모은다.
    """
    frame = board.frame
    out: list[tuple[float, float, float, float] | None] = []
    done: tuple[float, float, float, float] | None = None
    key = _period(frame.ts[0], unit) if frame.ts else ()
    o, h, low, c = frame.open[0], frame.high[0], frame.low[0], frame.close[0]
    for index in range(len(frame)):
        here = _period(frame.ts[index], unit)
        if here != key:
            done = (o, h, low, c)
            key = here
            o = frame.open[index]
            h = frame.high[index]
            low = frame.low[index]
            c = frame.close[index]
        else:
            h = max(h, frame.high[index])
            low = min(low, frame.low[index])
            c = frame.close[index]
        out.append(done)
    return out


@dataclass(frozen=True, slots=True)
class PreviousDay:
    """LVL-09 — 전일 고가 · 저가 · 종가.

    Note:
        ⭐ 카탈로그가 *"TF 무관 · 절대 레벨"* 로 분류한 것이다. 만드는 규칙이 없어
        **개수가 하루 셋으로 고정**이고, 그래서 과탐지가 안 생긴다.
    """

    @property
    def name(self) -> str:
        """LVL-09."""
        return "LVL-09"

    def fire(self, board: Board) -> list[Trigger]:
        """전일 고·저·종에서 되돌아 닫은 봉들.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        closed = _closed(board, "day")
        levels = [[] if one is None else [one[1], one[2], one[3]] for one in closed]
        return reactions(board, levels)


@dataclass(frozen=True, slots=True)
class DailyOpen:
    """LVL-10 — 당일 시가.

    Note:
        ⚠️ 전일 것이 아니라 **오늘의 시가**다. 그 값은 오늘 첫 봉이 열리는 순간
        확정되므로 미래 참조가 아니다 — `_closed` 를 쓰지 않는 유일한 신호다.
    """

    @property
    def name(self) -> str:
        """LVL-10."""
        return "LVL-10"

    def fire(self, board: Board) -> list[Trigger]:
        """당일 시가에서 되돌아 닫은 봉들.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        levels: list[list[float]] = []
        key = _period(frame.ts[0], "day") if frame.ts else ()
        anchor = frame.open[0] if frame.open else 0.0
        for index in range(len(frame)):
            here = _period(frame.ts[index], "day")
            if here != key:
                key = here
                anchor = frame.open[index]
            levels.append([anchor])
        return reactions(board, levels)


@dataclass(frozen=True, slots=True)
class WeeklyMonthly:
    """LVL-11 — 주간 / 월간 고저점.

    Note:
        ⚠️ 5분봉에서는 한 주가 2,016봉이라 레벨이 **아주 드물게** 갱신된다. 방아쇠가
        적게 나오는 것이 정상이고, 그것을 표본 부족으로 읽으면 안 된다 — 표본을
        늘리려고 기간을 짧게 잡는 것이 곧 규칙을 바꾸는 것이다.
    """

    @property
    def name(self) -> str:
        """LVL-11."""
        return "LVL-11"

    def fire(self, board: Board) -> list[Trigger]:
        """전주·전월 고저에서 되돌아 닫은 봉들.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        weekly = _closed(board, "week")
        monthly = _closed(board, "month")
        levels: list[list[float]] = []
        for week, month in zip(weekly, monthly, strict=True):
            here: list[float] = []
            if week is not None:
                here += [week[1], week[2]]
            if month is not None:
                here += [month[1], month[2]]
            levels.append(here)
        return reactions(board, levels)


@dataclass(frozen=True, slots=True)
class RoundNumber:
    """LVL-12 — 라운드 넘버 (심리적 가격대).

    Note:
        🔴 자릿수를 **가격에서 뽑는다.** BTC 60,000 의 라운드는 1,000 단위이고
        DOGE 0.15 의 라운드는 0.01 단위다. 고정 간격을 쓰면 한쪽에서는 레벨이
        한 개도 없고 다른 쪽에서는 봉마다 있다 — 그것이 곧 종목별로 다른 신호다.

        ⚠️ *"한 자리 아래"* 라는 선택 하나는 남는다. 이것이 이 파일에서 유일한
        손잡이이고, 고원 확인 대상이다.
    """

    @property
    def name(self) -> str:
        """LVL-12."""
        return "LVL-12"

    def fire(self, board: Board) -> list[Trigger]:
        """라운드 넘버에서 되돌아 닫은 봉들.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        import math

        frame = board.frame
        levels: list[list[float]] = []
        for index in range(len(frame)):
            price = frame.close[index]
            if price <= 0:
                levels.append([])
                continue
            step = 10 ** (math.floor(math.log10(price)) - 1)
            base = math.floor(price / step) * step
            levels.append([base, base + step])
        return reactions(board, levels)


@dataclass(frozen=True, slots=True)
class AnchoredVwap:
    """LVL-03 — 일간 앵커드 VWAP 이탈 / 회귀.

    Note:
        ⭐ 카탈로그가 *"1분봉 단타의 핵심 도구"* 로 꼽은 것이고, `judgement.md` 가
        요구한 신규 15종 중 하나이기도 하다 (앵커드 VWAP).

        ⚠️ 코인은 장 구분이 없어 **UTC 자정**을 앵커로 쓴다. 세션 앵커를 쓰려면
        시장 캘린더가 필요하고 그것은 C2-4 이라 아직 없다.

        🔴 거래량이 0 인 구간에서는 값이 갱신되지 않는다 — 0 으로 나누지 않고
        직전 값을 그대로 들고 간다. 조용히 0 을 내면 그 봉에서 가격이 VWAP 을
        무조건 뚫은 것으로 보인다.
    """

    @property
    def name(self) -> str:
        """LVL-03."""
        return "LVL-03"

    def fire(self, board: Board) -> list[Trigger]:
        """VWAP 에서 되돌아 닫은 봉들.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        levels: list[list[float]] = []
        key = _period(frame.ts[0], "day") if frame.ts else ()
        weighted = 0.0
        volume = 0.0
        vwap = 0.0
        for index in range(len(frame)):
            here = _period(frame.ts[index], "day")
            if here != key:
                key = here
                weighted = 0.0
                volume = 0.0
            typical = (frame.high[index] + frame.low[index] + frame.close[index]) / 3
            weighted += typical * frame.volume[index]
            volume += frame.volume[index]
            if volume > 0:
                vwap = weighted / volume
            levels.append([vwap])
        return reactions(board, levels)


for _signal in (PreviousDay(), DailyOpen(), WeeklyMonthly(), RoundNumber(), AnchoredVwap()):
    register(_signal)

declare(
    SignalMeta("LVL-03", 0, "bar_close"),
    SignalMeta("LVL-09", 0, "bar_close"),
    SignalMeta("LVL-10", 0, "bar_close"),
    SignalMeta("LVL-11", 0, "bar_close"),
    SignalMeta("LVL-12", 0, "bar_close"),
)
