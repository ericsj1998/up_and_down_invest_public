"""**보유 비용** — 펀딩 · 야간 스프레드 · 테일 페널티 (T151 · 계획서 §0-2, §0-4).

## 🔴 규칙으로 막지 않고 비용으로 센다

계획서 §0-4: 일중 종료를 **선호**하되 하드 룰로 만들지 않는다.

    "모든 트레이드를 당일 청산한다" 는 규칙을 강제하지 않는다
    대신 오버나이트 보유의 **실제 비용을 정확히 반영**한다
    비용이 정확하면 최적화가 알아서 일중 종료로 수렴한다
    수렴하지 않는다면, 그 신호는 오버나이트를 감당할 만큼 엣지가 크다는 뜻이다

⇒ 그래서 이 모듈은 판단하지 않는다. **세기만 한다.**

## ⭐ 펀딩은 시간 비례가 아니라 **경계 통과 횟수**다

`common/costs.MarketCosts.funding_cost_pct` 는 보유 시간에 비례해 센다. 그 독스트링이
스스로 근사라고 밝혀 두었다:

    "정산 시각을 지나는 횟수로 세는 것이 정확한데, 여기서는 비례로 센다"

7시간 55분을 보유해도 경계를 안 지났으면 **0원**이고, 10분을 보유해도 08:00 을
지났으면 **한 번 낸다**. 단타에서는 이 차이가 비용의 전부다 — 비례식은 짧은 보유에
있지도 않은 비용을 매기고, 경계에 걸친 보유는 놓친다.

⇒ 여기서는 **실제 정산 이력**(`logs/funding/*.json`)으로 경계를 센다.

## ⚠️ 야간 가산과 테일 페널티는 **아직 정해진 값이 없다**

계획서 §9 미결 사항: *"오버나이트 리스크 페널티의 구체 수치화 방식"*. 사용자가 정할
값이므로 **기본값을 만들지 않는다** — 임의의 기본값은 조용히 성적을 만들고, 그 성적이
누구의 가정인지 나중에 알 수 없다 (CLAUDE.md: *"스펙에 없는 기술 결정을 임의로
만들지 않는다"*).

⇒ `Overnight` 는 값을 **직접 받는다**. 가산 없이 재려면 `Overnight.none()` 을
  **명시적으로** 고르고, 그 사실이 결과에 `has_night_model=False` 로 남는다.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import cast

from updown.common.costs import MarketCosts
from updown.orchestration.discovery.fill import Exit
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "BOUNDARY_HOURS",
    "FUNDING_ROOT",
    "THIN_HOURS_UTC",
    "Charges",
    "Cost",
    "Funding",
    "FundingUnknownError",
    "Overnight",
    "load_funding",
]

FUNDING_ROOT = Path("logs/funding")
"""펀딩 정산 이력이 사는 곳 — `scripts/research/fetch_funding.py` 가 채운다.

형식은 `[[epoch_seconds, "요율"], ...]` 이고 8시간 경계마다 한 줄이다.
"""

BOUNDARY_HOURS = (0, 8, 16)
"""펀딩 정산 시각 (UTC).

⚠️ 이 값을 쓰는 것은 **이력이 없을 때뿐**이다. 이력이 있으면 경계도 이력이 정한다 —
거래소가 정산 주기를 바꾼 구간이 있으면 이 상수는 틀리고 이력은 맞다.
"""

THIN_HOURS_UTC = frozenset({19, 20, 21, 22, 23})
"""**실측**으로 나온 저유동 시간대 (UTC) = KST 04~08.

🔴 **내가 처음 적어 둔 "UTC 17~22" 는 틀렸다.** *"아시아 새벽"* 이라는 말만 보고
KST 02~07 을 옮긴 값이었고, 데이터는 다르게 말한다
(`scripts/research/discovery/hours.py` · 240일 x 8종 · 2026-08-30):

    UTC  KST   거래량  봉폭   폭/거래량   ← 이것이 충격 대용치
    17    2     1.22   1.21     0.99      ← 내가 "새벽" 이라고 적은 시간. **평균이다**
    19    4     0.92   1.03     1.12
    21    6     0.70   0.87     1.24      ← 최대
    22    7     0.78   0.97     1.25      ← 최대
    23    8     0.80   0.90     1.13
    14   23     1.94   1.58     0.82      ← 최소 (미국 장 열릴 때)

⭐ **거래량이 아니라 폭/거래량으로 골랐다.** 거래가 적어도 가격이 안 밀리면 비용이
안 오른다. 우리가 찾는 것은 *"거래가 적은 시간"* 이 아니라 *"같은 거래에 가격이 더
밀리는 시간"* 이다.

🔴 **그런데 이 시간대가 스프레드와는 안 맞았다** (2026-08-31 실측).

`spread_history.py` 로 2021~2026 중 24일을 뽑아 실효 스프레드를 복원해 보니:

    BCHUSDT  야간(UTC 19~23)/주간 = **0.99배**
    BTCUSDT  야간/주간 = **1.22배**
    가장 넓은 시간 = UTC 14~18 (BTC 1.27~1.49배)  ← **미국 장**, 거래량이 가장 많은 때

⇒ 스프레드는 **거래량이 적을 때가 아니라 변동성이 클 때** 넓어진다. 폭/거래량
  대용치는 *"같은 거래에 가격이 얼마나 밀리나"* 를 재는데, 그것과 최우선 호가 폭은
  다른 것이다.

⚠️ **그러므로 이 상수를 야간 스프레드 가산에 쓰지 않는다.** 남겨 두는 이유는
저유동 구간이 다른 의미(체결 지연·깊이)에서 여전히 쓸모가 있어서다.
"""


class FundingUnknownError(FileNotFoundError):
    """이 종목의 펀딩 이력이 없다.

    Note:
        🔴 **0 으로 넘어가지 않는다** (절대 규칙 #8). 펀딩이 0 인 백테스트는 보유가
        길수록 유리해지고, 그 오류는 오버나이트 전략에서만, 그것도 라이브에서만
        드러난다. 지금 없는 종목이 있으면 (예: BCH) `scripts/research/fetch_funding.py` 로
        먼저 채운다.
    """


@dataclass(frozen=True)
class Funding:
    """한 종목의 펀딩 정산 이력.

    Attributes:
        symbol: 종목 (예: `BTC_USDT`).
        schedule: 정산 시각 → 요율. 요율은 **부호가 있다** (음수면 롱이 받는다).

    Note:
        ⚠️ 요율은 `Decimal` 이다. 4년치를 더하면 float 오차가 눈에 보이는 자리까지
        올라오고, 이 값은 손익에 직접 더해진다.

        ⚠️ `slots` 를 쓰지 않는다 — `moments` 가 `cached_property` 이고 그것은
        `__dict__` 를 요구한다 (`signals.Board` 와 같은 이유).
    """

    symbol: str
    schedule: Mapping[datetime, Decimal]

    @cached_property
    def moments(self) -> list[datetime]:
        """정산 시각들, 오름차순 — **이분 탐색용**.

        Note:
            🔴 이것이 없으면 `crossings` 가 매번 전체 이력(4년 x 3회/일 = 5,000건)을
            훑는다. 스캔은 이 함수를 **수십만 번** 부르므로 그것만으로 죽는다 —
            같은 종류의 사고를 오늘 두 번 겪었다 (macd 히스토그램 · 위생 리포트의
            `max()`).
        """
        return sorted(self.schedule)

    def crossings(self, entry: datetime, exit_at: datetime) -> list[datetime]:
        """이 보유가 **지나간** 정산 시각들.

        Args:
            entry: 진입 시각 (UTC aware).
            exit_at: 청산 시각 (UTC aware).

        Returns:
            정산 시각 목록, 오름차순.

        Note:
            🔴 경계 조건은 `진입 < 정산 <= 청산` 이다. 정산 시각에 **들어간** 것은
            그 정산에 안 걸리고(포지션이 아직 없었다), 정산 시각에 **나간** 것은
            걸린다(그 순간까지 들고 있었다).

            ⚠️ 양쪽을 다 포함(`<=` … `<=`)하면 연속 매매에서 같은 정산을 두 번 센다.
            양쪽을 다 배제하면 8시간 정각에 딱 걸친 보유가 공짜가 된다.
        """
        found = self.moments
        start = bisect_right(found, entry)
        stop = bisect_right(found, exit_at)
        return found[start:stop]

    def cost_pct(self, entry: datetime, exit_at: datetime, direction: Direction) -> Decimal:
        """이 보유의 펀딩 **비용**(%). 음수면 받은 것이다.

        Args:
            entry: 진입 시각.
            exit_at: 청산 시각.
            direction: 롱/숏.

        Returns:
            비용(%). 롱은 요율 부호 그대로 내고, 숏은 반대로 받는다.

        Note:
            ⭐ 부호를 `Direction.sign` 으로 접는다. 롱/숏 분기를 손익·손절·펀딩 세
            군데에 흩으면 한 곳만 안 고쳐도 부호가 조용히 틀린다.
        """
        total = sum((self.schedule[ts] for ts in self.crossings(entry, exit_at)), start=Decimal(0))
        return total * direction.sign * 100


def load_funding(symbol: str, *, root: Path = FUNDING_ROOT) -> Funding:
    """펀딩 이력을 읽는다.

    Args:
        symbol: 종목 (예: `BTC_USDT`).
        root: 이력 폴더.

    Returns:
        이력.

    Raises:
        FundingUnknownError: 파일이 없다. **조용히 0 으로 넘어가지 않는다.**
        ValueError: 형식이 깨졌다.
    """
    path = root / f"{symbol}.json"
    if not path.exists():
        raise FundingUnknownError(
            f"{symbol} 의 펀딩 이력이 없다 ({path}). "
            "scripts/research/fetch_funding.py 로 먼저 채운다"
            " — 0 으로 두면 보유가 길수록 유리한 백테스트가 된다"
        )
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} 형식이 [[epoch, '요율'], ...] 이 아니다")
    rows = cast(list[object], raw)
    schedule: dict[datetime, Decimal] = {}
    for row in rows:
        if not isinstance(row, list):
            raise ValueError(f"{path} 의 한 줄이 [epoch, '요율'] 이 아니다: {row!r}")
        pair = cast(list[object], row)
        if len(pair) != 2:
            raise ValueError(f"{path} 의 한 줄이 [epoch, '요율'] 이 아니다: {pair!r}")
        epoch, rate = pair
        # ⚠️ 값의 **타입까지** 본다. `int(문자열)` 은 조용히 성공하는 경우가 많고,
        #    한 번 어긋난 시각은 8시간 경계 계산을 통째로 틀리게 한다.
        if not isinstance(epoch, int) or not isinstance(rate, str | int | float):
            raise ValueError(f"{path} 의 한 줄이 [epoch, '요율'] 이 아니다: {pair!r}")
        schedule[datetime.fromtimestamp(epoch, tz=UTC)] = Decimal(str(rate))
    return Funding(symbol=symbol, schedule=schedule)


@dataclass(frozen=True, slots=True)
class Overnight:
    """야간 보유의 추가 비용 — **값은 사용자가 정한다** (계획서 §9 미결 사항).

    Attributes:
        hours_utc: 저유동 시간대 (UTC 시각). 실측값은 `THIN_HOURS_UTC` 에 있다.
        spread_multiple: 그 시간대의 스프레드 배수. 1.0 이면 가산 없음.
        tail_pct_per_day: 보유 하루당 테일 리스크 페널티(%).

    Raises:
        ValueError: 배수가 1 미만이거나 페널티가 음수인 경우. 야간이 **더 싸다**는
            모델은 이 프로젝트가 아직 근거를 갖고 있지 않다.

    Note:
        🔴 **기본값을 두지 않는다.** 계획서 §9 가 *"오버나이트 리스크 페널티의 구체
        수치화 방식"* 을 미결로 남겼다. 임의의 기본값을 넣으면 그 값이 성적을 만들고,
        나중에 그 성적이 누구의 가정인지 알 수 없다.

        ⚠️ 과거 스프레드는 **측정할 수 없다** (`common/costs.py` 모듈 docstring).
        그래서 배수는 실측이 아니라 가정이며, 판정의 최종 근거는 이 숫자가 아니라
        **손익분기 대비 여유 배수**여야 한다.
    """

    hours_utc: frozenset[int]
    spread_multiple: float
    tail_pct_per_day: float

    def __post_init__(self) -> None:
        """가정이 유리한 쪽으로 기울지 않았는지 확인한다."""
        if self.spread_multiple < 1:
            raise ValueError(f"야간 스프레드 배수는 1 이상이어야 한다: {self.spread_multiple}")
        if self.tail_pct_per_day < 0:
            raise ValueError(f"테일 페널티는 음수일 수 없다: {self.tail_pct_per_day}")
        if any(hour < 0 or hour > 23 for hour in self.hours_utc):
            raise ValueError(f"시간은 0~23 이어야 한다: {sorted(self.hours_utc)}")

    @classmethod
    def none(cls) -> Overnight:
        """가산 **없음** — 명시적으로 고를 때만 쓴다.

        Returns:
            아무것도 더하지 않는 모델.

        Note:
            ⚠️ 이것을 쓰면 오버나이트 전략이 실제보다 좋아 보인다. 그래서 결과의
            `has_night_model` 이 거짓으로 남고, 리포트는 그 사실을 함께 실어야 한다
            (CLAUDE.md 관측 규약: 값을 만드는 규칙은 분포를 함께 싣는다).
        """
        return cls(hours_utc=frozenset(), spread_multiple=1.0, tail_pct_per_day=0.0)

    @property
    def active(self) -> bool:
        """실제로 무언가를 더하는 모델인가."""
        widens = bool(self.hours_utc) and self.spread_multiple > 1
        return widens or self.tail_pct_per_day > 0

    def is_night(self, moment: datetime) -> bool:
        """이 시각이 저유동 구간인가.

        Args:
            moment: 시각 (UTC aware).

        Returns:
            저유동 구간이면 참.

        Raises:
            ValueError: naive datetime — 시간대를 모르면 "새벽"을 판정할 수 없다.
        """
        if moment.tzinfo is None:
            raise ValueError(f"야간 판정은 UTC aware 를 요구한다: {moment!r}")
        return moment.astimezone(UTC).hour in self.hours_utc


@dataclass(frozen=True, slots=True)
class Cost:
    """한 매매의 비용 분해 — **합계만 주지 않는다**.

    Attributes:
        fee_pct: 수수료 + 거래세 왕복(%).
        slippage_pct: 슬리피지 왕복(%), 야간 가산 포함.
        funding_pct: 펀딩(%). 음수면 받은 것이다.
        tail_pct: 보유 시간 테일 페널티(%).
        crossings: 지나간 펀딩 정산 횟수.
        overnight: 야간 구간에 걸쳤나.
        has_night_model: 야간 모델이 실제로 켜져 있었나.

    Note:
        🔴 분해를 남기는 이유는 관측 규약(§1-0s)이다. 합계만 남기면 *"오버나이트가
        손익에 기여하는가"* 를 나중에 물을 수 없고, 그 질문이 §0-4 의 결론을 정한다.
    """

    fee_pct: Decimal
    slippage_pct: Decimal
    funding_pct: Decimal
    tail_pct: Decimal
    crossings: int
    overnight: bool
    has_night_model: bool

    @property
    def total_pct(self) -> Decimal:
        """왕복 총비용(%)."""
        return self.fee_pct + self.slippage_pct + self.funding_pct + self.tail_pct


@dataclass(frozen=True, slots=True)
class Charges:
    """비용 계산기 — 시장 요율 + 펀딩 이력 + 야간 모델.

    Attributes:
        market: 시장 비용 (`config/costs.yml` 의 한 블록).
        funding: 펀딩 이력. 현물처럼 펀딩이 없는 시장이면 `None`.
        overnight: 야간 가산 모델.

    Note:
        ⚠️ 수수료·슬리피지를 **다시 만들지 않는다.** `MarketCosts.round_trip_by` 가
        이미 체결 유형별로 센다. 여기서 더하는 것은 그 표에 **없는 것**뿐이다 —
        경계 기반 펀딩 · 야간 스프레드 가산 · 테일 페널티.
    """

    market: MarketCosts
    funding: Funding | None
    overnight: Overnight

    def of(
        self,
        *,
        entry: datetime,
        exit_at: datetime,
        direction: Direction,
        outcome: Exit,
        entry_is_maker: bool,
    ) -> Cost:
        """이 매매의 비용.

        Args:
            entry: 진입 시각 (UTC aware).
            exit_at: 청산 시각 (UTC aware).
            direction: 롱/숏.
            outcome: 어떻게 끝났나 — **청산 다리의 체결 유형을 정한다**.
            entry_is_maker: 진입이 지정가로 채워졌나.

        Returns:
            분해된 비용.

        Raises:
            ValueError: 청산이 진입보다 앞선 경우.

        Note:
            🔴 **결말이 비용을 바꾼다** (`MarketCosts.round_trip_for` 의 논거).
            익절은 걸어 둔 지정가라 메이커이고, 손절·청산은 발동 시 시장가라
            테이커다. 하나로 뭉개면 손절로 끝난 거래를 과소 계상한다.
        """
        if exit_at < entry:
            raise ValueError(f"청산이 진입보다 앞선다: {entry!r} → {exit_at!r}")

        exit_is_maker = outcome is Exit.TARGET
        both = self.market.round_trip_by(entry_is_maker=entry_is_maker, exit_is_maker=exit_is_maker)
        slippage = self.market.slippage_pct_one_way
        # `round_trip_by` 안에 이미 슬리피지 x2 가 들어 있다 — 분해해서 보여 주려고
        # 다시 빼낸다. 두 번 세지 않기 위한 뺄셈이지 새 계산이 아니다.
        fee = both - slippage * 2

        extra = Decimal(str(self.overnight.spread_multiple)) - 1
        legs = (entry, exit_at)
        night_legs = sum(1 for leg in legs if self.overnight.is_night(leg))
        slippage_total = slippage * 2 + slippage * extra * night_legs

        funding_pct = Decimal(0)
        crossings = 0
        if self.funding is not None:
            crossings = len(self.funding.crossings(entry, exit_at))
            funding_pct = self.funding.cost_pct(entry, exit_at, direction)

        held = exit_at - entry
        tail = Decimal(str(self.overnight.tail_pct_per_day)) * _days(held)

        return Cost(
            fee_pct=fee * 100,
            slippage_pct=slippage_total * 100,
            funding_pct=funding_pct,
            tail_pct=tail,
            crossings=crossings,
            overnight=night_legs > 0 or _crosses_midnight(entry, exit_at),
            has_night_model=self.overnight.active,
        )


def _days(held: timedelta) -> Decimal:
    """보유 시간을 **일** 단위 Decimal 로."""
    return Decimal(str(held.total_seconds())) / Decimal(86400)


def _crosses_midnight(entry: datetime, exit_at: datetime) -> bool:
    """날짜 경계를 넘겼나 — *"오버나이트 보유 비율"* 지표(§0-4)가 쓰는 정의.

    Note:
        ⚠️ 24시간 시장에는 "종가" 가 없으므로 UTC 날짜가 바뀌었는지로 센다. 임의의
        기준이지만 **한 곳에 적어 두면** 리포트끼리는 같은 뜻이 된다.
    """
    return entry.astimezone(UTC).date() != exit_at.astimezone(UTC).date()
