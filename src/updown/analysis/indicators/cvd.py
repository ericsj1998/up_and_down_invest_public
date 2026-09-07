"""CVD (누적 거래량 델타) — 봉 델타를 누적한 값 (T28 · spec §6.9 델타).

CVD 는 매수 체결량 - 매도 체결량을 봉마다 **누적**한 것이다. 돌파 확인에 쓰는 이유:
가격이 신고가인데 CVD 도 신고가면 실제로 사서 올린 것(진짜), CVD 가 못 따라오면
위에서 팔고 있는 것(흡수·가짜)이다.

🔴 **문턱을 지어내지 않는다** — *"CVD 가 얼마 이상"* 이 아니라 *"CVD 가 가격과 같이
신고가를 만들었나"* 라 새 상수가 0개다 (§5.6.4).

⚠️ **as-of 규약**: 봉 `i` 의 CVD 에는 `[:i+1]` 만 쓴다. 누적이라 미래 봉이 섞이면
과거 CVD 가 바뀐다 — 돌파 판정이 미래를 당겨쓰게 된다 (절대 규칙 #5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.trade_tick import BarDelta


@dataclass(frozen=True, slots=True)
class CvdSeries:
    """봉별 누적 델타.

    Attributes:
        values: 봉 인덱스별 CVD. `Candle` 배열과 **같은 좌표계·같은 길이**가 되도록
            부르는 쪽이 정렬한다 (없는 봉은 None).

    Note:
        길이가 캔들과 같아야 인덱스로 맞물린다 (`indicators.series` 계약과 같은 규칙).
        델타 파일에 구멍이 있으면 그 봉은 None 이고, `confirmed_upto` 가 None 을
        만나면 **판정을 포기**한다 — 없는 값으로 돌파를 확인한 척하지 않는다.
    """

    values: tuple[Decimal | None, ...]

    def confirmed_upto(self, index: int, lookback: int) -> bool | None:
        """봉 `index` 의 CVD 가 직전 `lookback` 봉 구간의 **신고가**인가.

        Args:
            index: 판정할 봉.
            lookback: 되돌아볼 봉 수 (돌파의 저항 창과 같은 값을 쓴다).

        Returns:
            신고가면 True, 아니면 False. 구간에 CVD 구멍(None)이 있으면 **None**
            (판정 불가) — 없는 값으로 확인한 척하지 않는다.

        Note:
            "신고가" = 구간 최대가 **현재 봉**에 있다. 흡수면 가격만 신고가이고 CVD 는
            앞선 봉이 더 높아 False 가 된다.
        """
        position = index if index >= 0 else len(self.values) + index
        start = max(0, position - lookback)
        window = self.values[start : position + 1]
        if not window or any(value is None for value in window):
            return None
        here = window[-1]
        assert here is not None  # any(None) 위에서 걸러졌다
        return all(here >= value for value in window if value is not None)

    def confirmed_downto(self, index: int, lookback: int) -> bool | None:
        """🪞 하단 이탈용 거울상 — CVD 가 구간 **신저가**인가.

        Args:
            index: 판정할 봉.
            lookback: 되돌아볼 봉 수.

        Returns:
            신저가면 True. 매도가 실제로 만든 하락이라는 뜻이다 (T40 숏 입장 후보).
            구멍이 있으면 None.
        """
        position = index if index >= 0 else len(self.values) + index
        start = max(0, position - lookback)
        window = self.values[start : position + 1]
        if not window or any(value is None for value in window):
            return None
        here = window[-1]
        assert here is not None
        return all(here <= value for value in window if value is not None)


def cvd_from_deltas(deltas: Sequence[BarDelta]) -> Decimal:
    """봉 델타들을 누적해 마지막 CVD 값을 낸다.

    Args:
        deltas: **시각 오름차순** 봉 델타. 부르는 쪽이 as-of 구간까지만 넘긴다.

    Returns:
        누적 델타. 빈 입력이면 0.
    """
    return sum((item.delta for item in deltas), Decimal(0))
