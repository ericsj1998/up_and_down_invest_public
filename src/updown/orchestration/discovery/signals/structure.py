"""A-6 시장 구조 — STR-01~07 (T160 Wave 2).

## 🔴 이 계열이 사후 교체형 위험이 **가장 높다**

*"구조가 깨졌다"* 는 판정은 깨진 **뒤에** 확정된다. 그리고 무엇을 기준으로 깨졌는지
(직전 스윙 고점)는 교대 정리가 **나중에 교체**할 수 있는 값이다.

⇒ 그래서 여기서는 완성된 스윙 열을 **얻을 수 없다.** `Board.swings` 속성은
제거됐고, 쓸 수 있는 것은 `Board.swing_events()` — **확정 이벤트 스트림**뿐이다.

    봉 k 에서 아는 구조 = known_at <= k 인 마지막 이벤트의 열

`views()` 가 그것을 봉마다 채운다. 모든 STR 신호가 이것만 읽는다.

## ⭐ 자유도를 어디서 없앴나

박스권·추세 판정에 **허용 오차를 안 쓴다.** 오차를 정하는 순간 그것이 개수를
정하는 손잡이가 되고, 추세선이 무너진 이유가 정확히 그것이었다.

    추세    마지막 고점 > 직전 고점 **그리고** 마지막 저점 > 직전 저점   (순서 비교뿐)
    박스    위도 아래도 아닌 나머지                                    (배제로 정의)

⇒ 판정이 **순서 비교**만으로 끝난다. 조정할 숫자가 없다.

## ⚠️ STR-05 는 STR-01·02 와 **겹친다** — 알고 넣는다

카탈로그가 *"STR-04와 반드시 분리"* 라고 요구한 것이 진입 방식의 분리다.
STR-05(즉시 추격)는 STR-01/02 와 같은 봉에 방아쇠를 놓고, STR-04(리테스트)는 뒤에
놓는다. 겹치는 것을 숨기지 않고 **격자에 나란히 올려** 다중검정 분모에 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from updown.analysis.structures.swing import SwingKind
from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "BoxEdge",
    "BreakChase",
    "BreakRetest",
    "ChangeOfCharacter",
    "Fakeout",
    "Level",
    "StructureBreak",
    "StructurePullback",
    "views",
]

DEPTH = 4
"""봉마다 들고 다닐 스윙 수.

교대 열이므로 4개면 **고점 2 · 저점 2** 가 보장되고, 그것이 추세·박스 판정에
필요한 최소한이다 (마지막과 직전을 비교하려면 각 종류가 둘씩 있어야 한다).
"""


@dataclass(frozen=True, slots=True)
class Level:
    """레벨 하나 — 가격과 **어느 스윙에서 왔는지**.

    Attributes:
        price: 가격 (float).
        index: 그 스윙의 봉 번호. 같은 레벨에서 두 번 치지 않으려고 쓴다.

    Note:
        ⚠️ `SwingPoint.price` 는 `Decimal` 이다. 봉마다 `Decimal` 비교를 하면
        70만 봉에서 그 변환이 계산의 대부분이 된다 — 여기서 한 번만 float 로 옮긴다.
    """

    price: float
    index: int


@dataclass(frozen=True, slots=True)
class Structure:
    """한 봉 시점에 알고 있는 구조.

    Attributes:
        high: 마지막 스윙 고점.
        prior_high: 그 앞 고점.
        low: 마지막 스윙 저점.
        prior_low: 그 앞 저점.
        trend: +1 상승 · -1 하락 · 0 그 외(박스).

    Note:
        🔴 `trend` 를 **순서 비교만으로** 정한다. 고점도 저점도 올라가면 상승,
        둘 다 내려가면 하락, 나머지는 전부 0 이다. 허용 오차가 없으므로 조정할
        손잡이도 없다.
    """

    high: Level
    prior_high: Level
    low: Level
    prior_low: Level
    trend: int


def views(board: Board) -> list[Structure | None]:
    """봉마다 **그 시점에 알고 있는** 구조. 아직 모르면 `None`.

    Args:
        board: 봉과 확정 이벤트 스트림.

    Returns:
        길이가 봉 수와 같은 열.

    Note:
        🔴 `board.swing_events()` 만 읽는다. 완성된 스윙 열을 읽으면 교대 정리의
        **사후 교체**가 이미 반영돼 있어, 다이버전스에서 성과를 통째로 만들었던
        바로 그 결함이 된다 (2026-08-31).

        ⭐ 같은 봉에 이벤트가 여럿 나면 **나중 것이 이긴다** — 이벤트는 시간순이고,
        그 봉이 끝날 때 아는 것은 마지막 상태다.

        ⚠️ 스냅샷은 **복사한다.** `swing_events` 가 내주는 열은 살아 있어서, 그대로
        들고 있으면 나중 걸음의 값이 과거 봉에 스며든다 — 그것이 곧 미래 참조다.
    """
    size = len(board.frame)
    known: dict[int, Structure] = {}
    for known_at, kept in board.swing_events():
        if known_at >= size or len(kept) < DEPTH:
            continue
        tail = kept[-DEPTH:]
        highs = [one for one in tail if one.kind is SwingKind.HIGH]
        lows = [one for one in tail if one.kind is SwingKind.LOW]
        if len(highs) < 2 or len(lows) < 2:
            continue
        up = highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price
        down = highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price
        known[known_at] = Structure(
            high=Level(float(highs[-1].price), highs[-1].index),
            prior_high=Level(float(highs[-2].price), highs[-2].index),
            low=Level(float(lows[-1].price), lows[-1].index),
            prior_low=Level(float(lows[-2].price), lows[-2].index),
            trend=1 if up else (-1 if down else 0),
        )

    out: list[Structure | None] = [None] * size
    here: Structure | None = None
    for index in range(size):
        found = known.get(index)
        if found is not None:
            here = found
        out[index] = here
    return out


def _closed_beyond(close: float, level: float, way: int) -> bool:
    """종가가 레벨을 그 방향으로 넘겼나."""
    return (close - level) * way > 0


def _pierced_and_returned(high: float, low: float, close: float, level: float, way: int) -> bool:
    """레벨을 **뚫었다가 되돌아 닫았나**.

    Args:
        high: 이번 봉 고가.
        low: 이번 봉 저가.
        close: 이번 봉 종가.
        level: 레벨 가격.
        way: +1 이면 위로 뚫고 내려 닫는 것, -1 이면 아래로 뚫고 올려 닫는 것.

    Returns:
        참이면 되돌림.

    Note:
        ⛔ *"근처에 왔다"* 로 안 잡는다. 근처는 허용 오차를 요구하고, 허용 오차가
        곧 과탐지 손잡이다 (`levels` 모듈과 같은 논거).

        ⚠️ 꼬리 하나로 판정하므로 **봉 안에서 정말 그 순서였는지는 모른다.**
        방아쇠는 그 봉의 종가로 확정되고 진입은 다음 봉이다.
    """
    if way > 0:
        return high > level >= close
    return low < level <= close


def _once(seen: set[int], key: int) -> bool:
    """이 레벨에서 아직 안 쳤으면 참 — 치고 기록한다.

    Note:
        🔴 같은 레벨에서 봉마다 다시 치면 방아쇠 수가 부풀려지고, 그것이 곧
        과탐지다. 레벨은 **스윙 봉 번호**로 식별한다 — 교체되면 번호도 바뀐다.
    """
    if key in seen:
        return False
    seen.add(key)
    return True


@dataclass(frozen=True, slots=True)
class StructureBreak:
    """STR-01 — BOS. **추세 방향으로** 직전 구조를 깬다.

    Note:
        확정 시점: 종가가 마지막 스윙 고점(저점)을 넘긴 **그 봉의 종가**.
        그 고점은 `swing_events` 로 이미 확정된 것이므로 소급이 없다.

        ⚠️ STR-02(CHoCH)와 **배타적**이다 — 같은 돌파라도 추세 방향이면 BOS,
        반대 방향이면 CHoCH 다. 추세가 0(박스)이면 둘 다 안 친다.
    """

    @property
    def name(self) -> str:
        """STR-01."""
        return "STR-01"

    def fire(self, board: Board) -> list[Trigger]:
        """추세 방향 구조 돌파.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        seen: set[int] = set()
        found: list[Trigger] = []
        for index, here in enumerate(views(board)):
            if here is None or here.trend == 0:
                continue
            level = here.high if here.trend > 0 else here.low
            if not _closed_beyond(frame.close[index], level.price, here.trend):
                continue
            if _once(seen, level.index):
                way = Direction.LONG if here.trend > 0 else Direction.SHORT
                found.append(Trigger(index=index, direction=way))
        return found


@dataclass(frozen=True, slots=True)
class ChangeOfCharacter:
    """STR-02 — CHoCH. **추세 반대로** 구조를 깬다.

    Note:
        확정 시점: STR-01 과 같다. 다른 것은 **어느 쪽 레벨을 깼는가**뿐이다 —
        상승 추세에서 마지막 저점을 깨면 전환 신호다.
    """

    @property
    def name(self) -> str:
        """STR-02."""
        return "STR-02"

    def fire(self, board: Board) -> list[Trigger]:
        """추세 반대 방향 구조 돌파.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        seen: set[int] = set()
        found: list[Trigger] = []
        for index, here in enumerate(views(board)):
            if here is None or here.trend == 0:
                continue
            against = -here.trend
            level = here.high if against > 0 else here.low
            if not _closed_beyond(frame.close[index], level.price, against):
                continue
            if _once(seen, level.index):
                way = Direction.LONG if against > 0 else Direction.SHORT
                found.append(Trigger(index=index, direction=way))
        return found


@dataclass(frozen=True, slots=True)
class StructurePullback:
    """STR-03 — HH-HL 구조 유지 중 눌림목.

    Note:
        확정 시점: 추세 **뒤쪽** 레벨(상승이면 마지막 저점)을 뚫었다가 되돌아 닫은
        봉의 종가.

        ⭐ 추세를 요구하므로 STR-06(페이크아웃)과 안 겹친다 — 저쪽은 추세 **앞쪽**
        레벨이 실패하는 것이다.
    """

    @property
    def name(self) -> str:
        """STR-03."""
        return "STR-03"

    def fire(self, board: Board) -> list[Trigger]:
        """추세 중 되돌림이 구조를 지킨 봉.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        seen: set[int] = set()
        found: list[Trigger] = []
        for index, here in enumerate(views(board)):
            if here is None or here.trend == 0:
                continue
            behind = here.low if here.trend > 0 else here.high
            if not _pierced_and_returned(
                frame.high[index], frame.low[index], frame.close[index], behind.price, -here.trend
            ):
                continue
            if _once(seen, behind.index):
                way = Direction.LONG if here.trend > 0 else Direction.SHORT
                found.append(Trigger(index=index, direction=way))
        return found


@dataclass(frozen=True, slots=True)
class BreakRetest:
    """STR-04 — 돌파 후 **리테스트** 진입.

    Note:
        확정 시점: 돌파한 레벨로 되돌아와 **지켜 낸** 봉의 종가. 돌파 봉이 아니다.

        ⭐ 대기 창을 **봉 수로 안 정한다.** 종가가 레벨 반대편으로 다시 닫히면
        그 돌파는 실패한 것으로 보고 후보에서 지운다 — 시간 상수를 두면 그것이
        또 하나의 손잡이가 된다.

        ⚠️ STR-05 와 **같은 돌파를 쓰고 진입 시점만 다르다.** 카탈로그가 둘을 반드시
        분리하라고 한 것이 이것이며, 겹침을 숨기지 않고 격자에 나란히 올린다.
    """

    @property
    def name(self) -> str:
        """STR-04."""
        return "STR-04"

    def fire(self, board: Board) -> list[Trigger]:
        """돌파한 레벨을 되돌아와 지킨 봉.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        seen: set[int] = set()
        found: list[Trigger] = []
        pending: dict[int, tuple[float, int]] = {}
        for index, here in enumerate(views(board)):
            if here is None:
                continue
            close = frame.close[index]

            for key, (price, way) in list(pending.items()):
                if _closed_beyond(close, price, -way):
                    # 반대편으로 닫혔다 — 이 돌파는 실패했다.
                    del pending[key]
                    continue
                if frame.low[index] <= price <= frame.high[index] and _closed_beyond(
                    close, price, way
                ):
                    del pending[key]
                    if _once(seen, key):
                        found.append(
                            Trigger(
                                index=index,
                                direction=Direction.LONG if way > 0 else Direction.SHORT,
                            )
                        )

            for level, way in ((here.high, 1), (here.low, -1)):
                if level.index in pending or level.index in seen:
                    continue
                if _closed_beyond(close, level.price, way):
                    pending[level.index] = (level.price, way)
        return found


@dataclass(frozen=True, slots=True)
class BreakChase:
    """STR-05 — 돌파 **즉시 추격** 진입.

    Note:
        확정 시점: 돌파 봉의 종가. 진입은 다음 봉 시가다.

        ⚠️ 카탈로그가 *"어느 TF든 승률 낮음"* 으로 적어 둔 것이다. **그래도 넣는다** —
        낮다는 것을 우리 데이터로 확인하는 것도 결과다. 빼면 그 확인이 사라지고,
        나중에 누가 다시 제안한다.

        🔴 STR-01·02 와 같은 봉에 친다. 겹치는 것을 알고 넣으며, 다중검정 분모에
        세 개가 다 들어간다.
    """

    @property
    def name(self) -> str:
        """STR-05."""
        return "STR-05"

    def fire(self, board: Board) -> list[Trigger]:
        """어느 쪽이든 구조 레벨을 종가로 넘긴 봉.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        seen: set[int] = set()
        found: list[Trigger] = []
        for index, here in enumerate(views(board)):
            if here is None:
                continue
            for level, way in ((here.high, 1), (here.low, -1)):
                if not _closed_beyond(frame.close[index], level.price, way):
                    continue
                if _once(seen, level.index):
                    found.append(
                        Trigger(
                            index=index, direction=Direction.LONG if way > 0 else Direction.SHORT
                        )
                    )
        return found


@dataclass(frozen=True, slots=True)
class Fakeout:
    """STR-06 — 페이크아웃 되돌림 (스톱헌트).

    Note:
        확정 시점: 추세 **앞쪽** 레벨을 뚫었다가 되돌아 닫은 봉의 종가.

        ⭐ 상승 추세에서 마지막 고점을 뚫었다가 아래로 닫으면 **실패한 돌파**이고,
        방향은 숏이다. STR-03 과 반대쪽 레벨을 보므로 겹치지 않는다.

        ⚠️ 카탈로그가 *"스톱헌트는 하위 TF에서 보임"* 이라고 적었다 — 1m·5m 에서
        방아쇠가 많이 나오는 것이 정상이며, 그것이 곧 과탐지는 아니다.
    """

    @property
    def name(self) -> str:
        """STR-06."""
        return "STR-06"

    def fire(self, board: Board) -> list[Trigger]:
        """추세 방향 돌파가 실패하고 되돌아 닫은 봉.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        seen: set[int] = set()
        found: list[Trigger] = []
        for index, here in enumerate(views(board)):
            if here is None or here.trend == 0:
                continue
            ahead = here.high if here.trend > 0 else here.low
            if not _pierced_and_returned(
                frame.high[index], frame.low[index], frame.close[index], ahead.price, here.trend
            ):
                continue
            if _once(seen, ahead.index):
                way = Direction.SHORT if here.trend > 0 else Direction.LONG
                found.append(Trigger(index=index, direction=way))
        return found


@dataclass(frozen=True, slots=True)
class BoxEdge:
    """STR-07 — 박스권 상하단.

    Note:
        🔴 **박스를 배제로 정의한다** — 고점도 저점도 같이 오르지 않고, 같이
        내리지도 않는 상태(`trend == 0`)다. 폭이나 유사도 문턱을 안 쓰므로 조정할
        손잡이가 없다.

        상단 = 최근 두 고점 중 높은 쪽 · 하단 = 최근 두 저점 중 낮은 쪽.
        가장자리를 뚫었다가 되돌아 닫으면 반대 방향으로 친다.

        ⭐ 사용자 기록: *"박스권은 곁다리가 아니다"* — 박스로 좁혔더니 매매가 거의
        다 사라졌던 적이 있다. 이 신호의 방아쇠 수가 적은 것은 정상이다.
    """

    @property
    def name(self) -> str:
        """STR-07."""
        return "STR-07"

    def fire(self, board: Board) -> list[Trigger]:
        """박스 가장자리에서 되돌아 닫은 봉.

        Args:
            board: 봉 판.

        Returns:
            방아쇠 목록 (봉 번호 · 방향).
        """
        frame = board.frame
        seen: set[int] = set()
        found: list[Trigger] = []
        for index, here in enumerate(views(board)):
            if here is None or here.trend != 0:
                continue
            top = max(here.high, here.prior_high, key=lambda one: one.price)
            bottom = min(here.low, here.prior_low, key=lambda one: one.price)
            for level, way, direction in (
                (top, 1, Direction.SHORT),
                (bottom, -1, Direction.LONG),
            ):
                if not _pierced_and_returned(
                    frame.high[index], frame.low[index], frame.close[index], level.price, way
                ):
                    continue
                if _once(seen, level.index):
                    found.append(Trigger(index=index, direction=direction))
        return found


for _signal in (
    StructureBreak(),
    ChangeOfCharacter(),
    StructurePullback(),
    BreakRetest(),
    BreakChase(),
    Fakeout(),
    BoxEdge(),
):
    register(_signal)

declare(
    SignalMeta("STR-01", None, "zigzag_events"),
    SignalMeta("STR-02", None, "zigzag_events"),
    SignalMeta("STR-03", None, "zigzag_events"),
    SignalMeta("STR-04", None, "zigzag_events"),
    SignalMeta("STR-05", None, "zigzag_events"),
    SignalMeta("STR-06", None, "zigzag_events"),
    SignalMeta("STR-07", None, "zigzag_events"),
)
