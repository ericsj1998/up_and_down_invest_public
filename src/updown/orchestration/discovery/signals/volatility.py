"""A-4 밴드 계열 — BND-02·03·04 와 OSC-10 (T153).

## 🔴 BND-02 가 오늘 **유일하게 살아남은** 신호다

2026-08-30, 1분봉 36판 중 압축→확장만 Gross 양수였다:

    개념        Gross      역방향     해석
    박스 이탈   -0.015%   +0.015%   방향성 없음
    큰 몸통     -0.014%   +0.003%   방향성 없음
    압축→확장   **+0.077%**  **-0.097%**  진짜 신호 (역방향이 대칭으로 음수)

칸별로 쪼개도 7칸 중 6칸 Gross 양수였고 손잡이(눌림 정도)도 단조로 움직였다.
⚠️ 그런데 그 측정은 **임시 코드**였고 계획서 Stage 0 규칙을 여러 개 어겼다.
여기서 제대로 다시 잰다.

## ⭐ 문턱은 **찾기 전에** 적는다

`SQUEEZE_LOOKBACK` · `SQUEEZE_PERCENTILE` 은 사전등록의 일부다. 결과를 보고 조이면
그것이 곡선 맞추기다 — 손잡이를 돌리는 것은 Stage 6(고원 확인)의 일이고, 그때는
**이웃 값도 같이 좋아야** 통과다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.orchestration.discovery.metrics import percentile
from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = ["AtrSurge", "BandTouch", "BandWalk", "Squeeze"]

SQUEEZE_LOOKBACK = 100
"""압축 여부를 재는 창(봉). 100봉이면 15m 에서 하루가 조금 넘는다."""

SQUEEZE_PERCENTILE = 0.20
"""창 안에서 이 백분위 **아래**면 압축으로 본다.

⚠️ **사전등록값이다.** 오늘 임시 측정에서 눌림 0.3 → 0.2 → 0.1 로 조일수록 성적이
좋아졌지만(+0.061 → +0.077 → +0.167%), 그 관찰로 값을 정하면 곡선 맞추기다.
가운데인 0.2 를 적어 두고, 손잡이 민감도는 Stage 6 에서 **고원**으로 확인한다.
"""


@dataclass(frozen=True, slots=True)
class Squeeze:
    """BND-02 — 볼밴 스퀴즈 후 확장.

    Attributes:
        lookback: 압축을 재는 창.
        pinch: 압축 판정 백분위.

    Note:
        🔴 **방향은 중심선 기준**이다. 확장하는 봉의 몸통 방향으로 잡으면 도지 한 개에
        방향이 뒤집힌다. 종가가 중심선 위면 롱, 아래면 숏이다.

        ⚠️ 확장은 *"직전 봉보다 넓다"* 로만 본다. 배수 문턱을 두면 손잡이가 하나 더
        늘고, 손잡이는 적을수록 좋다 (Stage 6 이 전부 확인해야 한다).
    """

    lookback: int = SQUEEZE_LOOKBACK
    pinch: float = SQUEEZE_PERCENTILE

    @property
    def name(self) -> str:
        """BND-02."""
        return "BND-02"

    def fire(self, board: Board) -> list[Trigger]:
        """압축이 풀리는 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        bands = board.bands
        width = bands.width
        middle = bands.middle
        close = board.close
        found: list[Trigger] = []
        for index in range(self.lookback, len(width)):
            now, before = width[index], width[index - 1]
            centre = middle[index]
            if now is None or before is None or centre is None:
                continue
            window = [float(one) for one in width[index - self.lookback : index] if one is not None]
            if len(window) < self.lookback // 2:
                continue
            edge = percentile(window, self.pinch)
            # 직전 봉이 압축 상태였고, 이번 봉에서 벌어졌다.
            if float(before) <= edge < float(now):
                direction = Direction.LONG if close[index] > centre else Direction.SHORT
                found.append(Trigger(index=index, direction=direction))
        return found


@dataclass(frozen=True, slots=True)
class BandTouch:
    """BND-03 — 밴드 상·하단을 벗어났다가 **되돌아오는** 순간 (평균회귀).

    Note:
        ⚠️ 터치 자체가 아니라 **복귀**다. 터치로 세면 밴드워킹(BND-04) 구간에서
        같은 추세를 수십 번 역행 진입하게 되고, 그 둘은 정반대 전략이다.
    """

    @property
    def name(self) -> str:
        """BND-03."""
        return "BND-03"

    def fire(self, board: Board) -> list[Trigger]:
        """밴드 밖에서 안으로 돌아온 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        position = board.bands.position
        found: list[Trigger] = []
        for index in range(1, len(position)):
            before, now = position[index - 1], position[index]
            if before is None or now is None:
                continue
            if before < 0 <= now:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif before > 1 >= now:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class BandWalk:
    """BND-04 — 밴드워킹 시작 (추세 지속).

    Attributes:
        bars: 연속으로 밴드 밖에 머물러야 하는 봉 수.

    Note:
        🔴 BND-03 과 **정반대 전략**이다. 같은 터치를 하나는 되돌림으로, 하나는
        지속으로 읽는다 — 어느 쪽이 맞는지는 국면이 정하고, 그것을 가르는 것이
        Stage 3(조합)의 일이다. 여기서는 둘 다 재기만 한다 (계획서 §1-1).
    """

    bars: int = 3

    @property
    def name(self) -> str:
        """BND-04."""
        return "BND-04"

    def fire(self, board: Board) -> list[Trigger]:
        """연속으로 밴드 밖에 머문 것이 확인된 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        position = board.bands.position
        found: list[Trigger] = []
        for index in range(self.bars, len(position)):
            window = position[index - self.bars + 1 : index + 1]
            if any(one is None for one in window):
                continue
            values = [one for one in window if one is not None]
            if all(one > 1 for one in values):
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif all(one < 0 for one in values):
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class AtrSurge:
    """OSC-10 — ATR 급증 (변동성 확장).

    Attributes:
        lookback: 비교 창.
        multiple: 창 중앙값의 몇 배부터 급증으로 볼지.

    Note:
        ⚠️ **방향이 없는 신호다.** 변동성이 커진 것은 방향을 말하지 않는다. 그래서
        진행 방향(직전 봉 대비 종가)을 붙이는데, 그 선택 자체가 가설이다 —
        Tier 표에 이 신호가 0 근처로 나오면 *"방향 붙이기가 틀렸다"* 일 수도 있고
        *"변동성에 방향 정보가 없다"* 일 수도 있다. 둘을 가르는 것은 Stage 3 이다.
    """

    lookback: int = 100
    multiple: float = 2.0

    @property
    def name(self) -> str:
        """OSC-10."""
        return "OSC-10"

    def fire(self, board: Board) -> list[Trigger]:
        """ATR 이 급증한 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.atr14
        close = board.close
        threshold = Decimal(str(self.multiple))
        found: list[Trigger] = []
        for index in range(self.lookback, len(series)):
            now = series[index]
            if now is None:
                continue
            window = [
                float(one) for one in series[index - self.lookback : index] if one is not None
            ]
            if len(window) < self.lookback // 2:
                continue
            middle = Decimal(str(percentile(window, 0.5)))
            if middle > 0 and now > middle * threshold:
                direction = Direction.LONG if close[index] > close[index - 1] else Direction.SHORT
                found.append(Trigger(index=index, direction=direction))
        return found


for _signal in (Squeeze(), BandTouch(), BandWalk(), AtrSurge()):
    register(_signal)

declare(
    SignalMeta("BND-02", 0, "bar_close"),
    SignalMeta("BND-03", 0, "bar_close"),
    SignalMeta("BND-04", 0, "bar_close"),
    SignalMeta("OSC-10", 0, "bar_close"),
)
