"""봉으로 체결을 판정하는 모형 — 백테스트의 거래소 (T19 ②).

⛔ **후하게 치지 않는다.** 백테스트만 잘 나오고 라이브에서 안 되는 것이 이 프로젝트가
5m 단독 진입에서 겪은 함정이고, 그 판정의 근거가 여기 한 곳에 모여 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.orchestration.walkforward.fill_protocol import Fill


@dataclass(frozen=True, slots=True)
class _Resting:
    """걸어 둔 표 하나."""

    price: Decimal
    ratio: Decimal
    long: bool


class SealedFiller:
    """봉이 지정가를 **뚫었을 때만** 채운다.

    Note:
        🔴 **닿기만 한 것은 안 친다** (`<` 이지 `<=` 가 아니다). 저가가 지정가와 정확히
        같으면 실제로는 앞에 줄 선 주문이 먼저 채워지고 나는 못 사는 경우가 많다 —
        거래소는 가격이 아니라 **줄**로 채우기 때문이다.

        ⚠️ 그 한 글자가 백테스트를 낙관으로 만든다. 실전에서 안 채워질 자리를 채워진
        것으로 세면, 그 자리들의 성적이 통째로 가짜가 된다.

        🔴 **체결가는 정확히 지정가다.** 봉이 더 내려갔다고 더 좋은 값을 주지 않는다 —
        우리 주문은 그 가격에 걸려 있었고 거기서 채워진다. 저가를 주면 백테스트가
        **매번 바닥에 사는** 것이 되어 실전과 갈린다.

        ⛔ **부분 체결을 흉내 내지 않는다.** 봉 데이터로는 알 수 없고, 지어내면 그
        숫자가 뜻을 가진 것처럼 보인다 (절대 규칙 #8). 다리 단위로만 채운다.
    """

    def __init__(self) -> None:
        """빈 장부로 시작한다."""
        self._open: dict[str, _Resting] = {}
        self.filled = 0
        """채워진 표 수 — 체결률의 분자다."""

        self.cancelled = 0
        """거둔 표 수 — **역선택을 보는 값**이다.

        🔴 지정가는 **가격이 계속 불리하게 갈 때 제일 잘 채워진다.** 좋은 자리는
        놓치고 나쁜 자리만 잡힐 수 있으므로, 체결률과 이 값을 **같이** 봐야 한다.
        """

    def place(self, ticket: str, *, price: Decimal, ratio: Decimal, long: bool) -> None:
        """지정가 하나를 건다.

        Args:
            ticket: 주문 이름.
            price: 지정가.
            ratio: 채워지면 계획의 얼마인가.
            long: 롱인가.

        Note:
            ⚠️ 같은 이름을 다시 걸면 **덮어쓴다.** 계획이 바뀌어 다시 거는 것이
            정상 경로이고, 쌓아 두면 한 매매가 여러 번 채워진다.
        """
        self._open[ticket] = _Resting(price=price, ratio=ratio, long=long)

    def rejected(self, ticket: str) -> bool:
        """봉인 급전은 거절이 없다.

        Args:
            ticket: 표 이름 (읽지 않는다).

        Returns:
            항상 False.
        """
        del ticket
        return False

    def poll(self, ticket: str, bar: Candle) -> Fill | None:
        """이 봉이 그 지정가를 뚫었나.

        Args:
            ticket: 주문 이름.
            bar: 방금 닫힌 봉.

        Returns:
            채워졌으면 그 조각. 아직이면 None.
        """
        resting = self._open.get(ticket)
        if resting is None:
            return None
        # 🔴 **뚫어야 한다.** 닿기만 한 것(`==`)은 줄에서 밀렸다고 본다.
        through = bar.low < resting.price if resting.long else bar.high > resting.price
        if not through:
            return None
        # ⚠️ **표를 지운다.** 남겨 두면 다음 봉에서 또 채워져 비중이 두 배가 된다.
        del self._open[ticket]
        self.filled += 1
        return Fill(price=resting.price, ratio=resting.ratio)

    def cancel(self, ticket: str) -> None:
        """건 것을 거둔다.

        Args:
            ticket: 주문 이름.
        """
        if self._open.pop(ticket, None) is not None:
            self.cancelled += 1

    def resting(self) -> tuple[str, ...]:
        """지금 걸려 있는 표 이름들.

        Returns:
            이름 목록.

        Note:
            ⚠️ 감사용이다. 판정이 이것을 보고 결정을 바꾸면 안 된다 — 그러면 체결
            여부가 아니라 *"몇 개 걸려 있나"* 가 판단에 섞인다.
        """
        return tuple(self._open)
