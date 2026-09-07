"""점검 시점을 **뽑는다** — 사람이 고르지 않는다.

## 왜 랜덤인가

사람이 시점을 고르면 **기억나는 구간**만 본다. 큰 상승, 큰 폭락, "그때 그 자리".
거기서 얻은 인상은 시장의 성질이 아니라 기억의 성질이고, 그 인상으로 규칙을 고치면
과거에 인상적이었던 구간에 맞춘 규칙이 된다.

랜덤은 그 편향을 없애 주지는 못하지만 **한쪽으로 쏠리지 않게** 한다.

## 왜 시드를 입력으로 받는가

절대 규칙 #5 (같은 입력 → 같은 출력). 시드를 함께 돌려주므로 "아까 그 화면"을 다시
부를 수 있다 — 이의제기를 검토할 때 그 화면이 재현되지 않으면 검토가 불가능하다.

⚠️ 난수를 쓰지만 결정론은 깨지지 않는다. 금지된 것은 **현재 시각이나 전역 난수를
직접 참조**하는 것이고, 시드가 인자로 들어오면 이 함수는 순수 함수다.

## ⛔ 봉인 구간은 뽑지 않는다

`OOS_BOUNDARY` 뒤는 P1-8 최종 판정용이다. 코드가 안 읽는 것만으로는 부족하고 **사람이
보는 것도 오염**이므로 (`common/oos.py`), 뽑을 구간에서 아예 제외한다.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random

from updown.common.oos import OOS_BOUNDARY

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class MomentUnavailableError(RuntimeError):
    """뽑을 수 있는 시점이 없다.

    조용히 아무 시점이나 돌려주지 않는다 (절대 규칙 #8) — 워밍업이 모자란 화면은
    "지표가 안 잡힌다"로 보이고, 그것을 알고리즘 결함으로 오해하게 된다.
    """


@dataclass(frozen=True, slots=True)
class Moment:
    """뽑힌 점검 시점 하나.

    Attributes:
        at: 점검 시점 (UTC). 이 시점에 닫혀 있던 봉까지만 보여 준다.
        seed: 이 시점을 만든 시드. **응답에 반드시 실어 재현 가능하게 한다.**
        earliest: 뽑을 수 있었던 구간의 시작 (워밍업 반영 후).
        latest: 뽑을 수 있었던 구간의 끝 (봉인 반영 후).
    """

    at: datetime
    seed: int
    earliest: datetime
    latest: datetime


def _floor(moment: datetime, step: timedelta) -> datetime:
    """`step` 격자에 맞춰 내림한다 — 시점이 봉 경계에 앉게.

    Args:
        moment: 자를 시각.
        step: 격자 간격.

    Returns:
        격자 위의 시각.

    Note:
        기준을 epoch 으로 잡는다. 구간 시작을 기준으로 잡으면 격자가 구간마다 달라져
        같은 시드가 데이터 범위에 따라 다른 시점을 내놓는다.
    """
    units = (moment - _EPOCH) // step
    return _EPOCH + step * units


def pick_moment(
    *,
    seed: int,
    earliest: datetime,
    latest: datetime,
    warmup: timedelta,
    step: timedelta,
) -> Moment:
    """구간 안에서 점검 시점 하나를 결정론적으로 뽑는다.

    Args:
        seed: 난수 시드. 같은 시드는 같은 시점을 준다.
        earliest: 데이터가 시작하는 시각 (UTC aware).
        latest: 데이터가 끝나는 시각 (UTC aware). 봉인 경계와 함께 더 이른 쪽이 쓰인다.
        warmup: 시점 앞에 확보해야 할 기간 (`window.warmup_for`).
        step: 시점 격자. 보통 가장 성긴 시간축의 봉 간격이다.

    Returns:
        뽑힌 시점.

    Raises:
        ValueError: 시각이 naive 이거나 `step` 이 0 이하이면.
        MomentUnavailableError: 워밍업과 봉인을 빼고 나면 뽑을 구간이 남지 않을 때.

    Note:
        ⛔ 상한은 `min(latest, OOS_BOUNDARY)` 다. 호출부가 `latest` 를 잘못 넘겨도
        봉인은 여기서 다시 지켜진다 — 봉인을 호출부 성실성에 맡기지 않는다.
    """
    for name, value in (("earliest", earliest), ("latest", latest)):
        if value.tzinfo is None:
            raise ValueError(f"{name} 이 naive 다: {value!r} — UTC aware 여야 한다 (절대 규칙 #7)")
    if step <= timedelta(0):
        raise ValueError(f"step 이 0 이하다: {step!r}")

    low = _floor(earliest + warmup, step)
    high = _floor(min(latest, OOS_BOUNDARY), step)
    slots = (high - low) // step
    if slots < 1:
        raise MomentUnavailableError(
            f"뽑을 시점이 없다 — 워밍업({warmup}) 뒤 {low.date()} 부터 "
            f"봉인 전 {high.date()} 까지가 봉 {max(slots, 0)}개다. "
            f"보는 봉 수를 줄이거나 적재 구간을 늘려야 한다"
        )
    return Moment(
        at=low + step * Random(seed).randrange(slots),
        seed=seed,
        earliest=low,
        latest=high,
    )
