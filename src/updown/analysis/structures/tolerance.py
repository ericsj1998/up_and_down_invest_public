"""허용 오차 — **ATR 배수**로 정의한다 (P1-6-9 · spec §4.3.2, `Phase01` §1-0h).

## 왜 고정 %를 버렸는가 — 실측이 4.1배 왜곡을 보였다

| 시간축 | ATR/가격 중앙 | 고정 0.2% 의 등가 배수 |
|---|---|---|
| BTC 1h | 0.491% | **0.407xATR** |
| BTC 5m | 0.120% | **1.671xATR** |

**같은 0.2% 가 시간축 사이에서 4.1배 다른 뜻**이고, 한 시간축 안에서도 저·고변동 구간이
1.8~2.2배 벌어진다. P1-6 에서 5m 추세선 터치율(82.5%)이 1h(52.6%)보다 높았던 것이 이
왜곡과 방향이 같다 — 5m 에서는 0.2% 가 1.67xATR, 즉 봉 하나 반 크기의 띠였다.

`Phase01` §1-0e 가 **전제조건 1**로 이미 기록해 둔 전환이다.

## ⚠️ 순수한 단위 교체가 아니다 — 배수는 새 결정이었다

등가 지점이 시간축마다 달라(0.407 vs 1.671) "같은 값의 다른 단위"로 환원되지 않는다.
어떤 배수를 골라도 최소 한 시간축의 수준이 바뀐다. 그래서 배수는 **절차로** 고정했다:

> ① 배수와 근거를 **먼저 선언**(커밋) → ② 측정 → ③ 결과 수치를 그대로 수용.
> 나온 터치율이 마음에 안 든다고 ①로 돌아가지 않는다 (§5.6.2, §5.6.7).

확정값과 탈락 사유는 [rule_candidates.md 축 I](../../../../docs/rules/rule_candidates.md).

## ATR 이 없으면 오차 0 이다

워밍업 구간(ATR `None`)에서 임의 대체값을 쓰면 **그 값이 판정을 좌우한다.** 0 으로 두면
"정확히 닿아야 접점"이 되어 워밍업에서 구조물이 덜 나오고, 그것은 조용한 오답이 아니라
보이는 보수적 동작이다 (절대 규칙 #8). `structures/retest.py` 가 같은 규칙을 쓴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from updown.analysis.indicators.atr import atr
from updown.common.domain.candle import Candle
from updown.common.numeric import fixed_context

TOUCH_ATR_MULTIPLE = Decimal("0.25")
"""접점·군집 판정 허용 오차 배수 (축 I1 확정값).

**형태 논거**: "닿았다"는 판정은 봉 범위의 **1/4 이내**로 본다. 성과와 무관한 근거이며,
그것이 이 값을 고른 이유다.

⛔ **성과로 조정하지 않는다** (spec §5.6.2). 선언 후 측정 결과를 수용했고, 되돌리지 않는다.
"""

ZONE_ATR_MULTIPLE = Decimal("0.5")
"""합류 판정 허용 오차 배수 (축 I1 확정값).

접점의 **2배** — 전환 전 `0.001 : 0.002` 관계를 그대로 보존했다. 합류는 "서로 다른
구조물이 같은 가격대에 있다"를 보므로 접점보다 넓은 것이 맞고, 그 비율을 새로 정하지
않으려고 기존 관계를 유지했다 (새 자유도 최소화).

⛔ **성과로 조정하지 않는다** (spec §5.6.2).
"""


@dataclass(frozen=True, slots=True)
class AtrTolerance:
    """봉별 허용 오차 — `배수 x 그 봉의 ATR`.

    Attributes:
        multiple: ATR 배수.
        atr: 캔들과 **길이가 같은** ATR 계열. 워밍업은 None 이다.

    Note:
        **봉마다 값이 다른 것이 핵심이다.** 고정 %는 변동성이 바뀌어도 같은 폭을 쓰지만,
        같은 5% 움직임이 저변동 구간에서는 큰 사건이고 고변동 구간에서는 노이즈다.

        `atr` 을 잘라내지 않고 None 으로 채워 받는 이유: 봉 번호가 x축이므로 길이가
        어긋나면 **엉뚱한 봉의 ATR 로 판정하면서 예외가 나지 않는다**
        (`analysis/README.md` 경고).
    """

    multiple: Decimal
    atr: tuple[Decimal | None, ...]
    _resolved: tuple[Decimal, ...] = field(init=False, repr=False, compare=False)
    """봉별 허용 오차를 **미리 계산해 둔** 계열. `at()` 이 조회만 하게 만든다.

    P1 성능 실측(2026-08-06): `at()` 이 400봉 창 하나를 작도하는 동안 **373,224회** 불렸고
    (`_count_body_violations` 가 선 x 봉으로 부르기 때문), 매 호출이 `fixed_context()` 로
    decimal 컨텍스트를 새로 열어 `compute_bundle` 의 **54%** 를 차지했다. 값은 `index` 의
    순수 함수이므로 한 번 계산해 두면 같은 결과를 조회로 얻는다.

    `compare=False` 인 이유: 파생값이라 동치 판정에 넣으면 같은 뜻의 두 객체가 다르게
    비교될 여지만 만든다. 동치는 `multiple` 과 `atr` 로 정해진다 — 전환 전과 같다.
    """

    def __post_init__(self) -> None:
        """허용 오차 계열을 굳힌다.

        Note:
            `frozen=True` 이므로 `object.__setattr__` 을 쓴다 — `ScanResult` 가 집계를
            굳히는 방식과 같다. 생성 시점에 한 번만 도는 비용이며, 그 대가로 조회가
            수십만 번 공짜가 된다.
        """
        with fixed_context():
            resolved = tuple(
                Decimal(0) if value is None else self.multiple * value for value in self.atr
            )
        object.__setattr__(self, "_resolved", resolved)

    def at(self, index: int) -> Decimal:
        """그 봉에서의 허용 오차 (절대 가격).

        Args:
            index: 봉 번호.

        Returns:
            `multiple x ATR[index]`. ATR 이 없거나 범위 밖이면 **0** 이다.

        Note:
            범위 밖을 0 으로 돌려주는 것은 관용이 아니다 — 구조물 좌표는 봉 번호이므로
            범위 밖 접근은 "그 봉에 오차가 없다"와 같고, 예외로 막으면 선을 창 밖으로
            연장하는 정상 경로(`Line.price_at`)까지 깨진다.
        """
        if not 0 <= index < len(self._resolved):
            return Decimal(0)
        return self._resolved[index]

    @classmethod
    def zero(cls, multiple: Decimal = TOUCH_ATR_MULTIPLE) -> "AtrTolerance":
        """오차가 항상 0 인 허용 오차 — **정확히 닿아야** 접점이다.

        Args:
            multiple: 기록용 배수. 판정 결과에는 영향이 없다.

        Returns:
            빈 ATR 계열을 가진 허용 오차.

        Note:
            합성 캔들 테스트가 좌표를 정확히 맞춰 쓸 때 필요하다. **운영 경로에서 쓰면
            구조물이 거의 안 나온다** — 그것이 조용한 실패가 아니라 보이는 결과여야
            하므로 이름에 `zero` 를 박았다.
        """
        return cls(multiple=multiple, atr=())


def from_candles(candles: Sequence[Candle], multiple: Decimal) -> AtrTolerance:
    """캔들에서 ATR 을 계산해 허용 오차를 만든다.

    Args:
        candles: `ts` 오름차순 캔들.
        multiple: ATR 배수.

    Returns:
        허용 오차. 캔들이 없으면 오차 0 이다.

    Note:
        **호출부가 이걸 여러 번 부르지 않게 한다.** ATR 계산은 전 구간 순회이므로
        `compute_bundle` 이 한 번 만들어 내려보낸다 (`structures/bundle.py`).
    """
    if not candles:
        return AtrTolerance.zero(multiple)
    series = atr(
        [candle.high for candle in candles],
        [candle.low for candle in candles],
        [candle.close for candle in candles],
    )
    return AtrTolerance(multiple=multiple, atr=tuple(series))


def rescaled(base: AtrTolerance, multiple: Decimal) -> AtrTolerance:
    """같은 ATR 계열에 **다른 배수**를 씌운다.

    Args:
        base: 기준 허용 오차.
        multiple: 새 배수.

    Returns:
        ATR 계열을 공유하는 허용 오차.

    Note:
        접점(0.25)과 합류(0.5)가 같은 ATR 을 쓰므로 계산을 재사용한다. 각자 ATR 을 다시
        구하면 같은 값을 두 번 계산하고, 더 나쁘게는 **두 계열이 어긋날 여지**가 생긴다.
    """
    return AtrTolerance(multiple=multiple, atr=base.atr)
