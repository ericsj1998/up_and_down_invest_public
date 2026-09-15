"""공용 리테스트 판정 — `RETEST_CONFIRM` 의 단일 구현 (P1-6-0 · spec §4.3.2).

## 왜 `structures/` 가 소유하는가

§4.3.2 가 명시적으로 지정했다. 리테스트 로직은 원래 세 곳에 흩어져 있었다 —
채널 복귀 봉마감(§6.5), 컵앤핸들 넥라인(§6.7), IFVG S/R Flip(§6.6). 같은 개념인데 구현이
셋이면 **"채널에서는 통과하는데 컵에서는 안 되는" 불일치**가 생긴다.

> 기준 레벨은 셋업이 제공하고, **판정은 공용 로직이 한다.** 셋업이 바꿀 수 있는 것은
> 파라미터(되돌림 허용 범위, 지지 확인에 필요한 봉 수)뿐이다. (§4.3.2)

새 셋업은 **기준 레벨만 넘기면** `RETEST_CONFIRM` 을 자동 상속한다 — 그것이 "탐지 파일
1개 + 레지스트리 1줄"(§4.3.1)이 리테스트에도 성립하는 조건이다.

## 판정 4단계 (전부 충족해야 확인)

| 단계 | 판정 | 여기서의 구현 |
|------|------|--------------|
| ① 돌파 | 기준 레벨을 **봉마감 기준으로** 넘는다 | `close > level` |
| ② 되돌림 | 가격이 기준 레벨로 돌아온다 (허용 범위 = **ATR 배수**) | `low <= level + kxATR` |
| ③ 지지 확인 | 레벨이 이번엔 **지지로 작동** (S/R Flip). 이탈하면 실패 | `close >= level` |
| ④ 봉마감 | 지지 확인이 **봉마감으로 확정** | `confirm_bars` 봉 연속 종가 |

## 돌파 이후는 두 갈래다 — ③만 갈린다 (spec §4.3.2, v2.4)

| 모드 | 시장이 한 일 | ③ 판정 | 베팅 |
|------|------------|--------|------|
| `HOLD` | 돌파를 **지켰다** | `close >= level` | 추세 지속 |
| `RECLAIM` | 돌파에 **실패했다** | `close < level` (안쪽 복귀) | 반전 (페이크아웃 역이용) |

**가르는 기준은 부등호 하나**라 측정 가능하고 사람 재량이 없다 (§5.6.6). ①②를 공유하고
③만 모드로 가르는 이유는, 두 함수로 나누면 **"돌파"의 정의가 갈라지기** 때문이다 —
§4.3.2 가 애초에 통합을 지시한 이유가 그것이다.

`RECLAIM` 에서 다시 레벨 위로 마감하는 것은 **실패가 아니다.** 돌파는 여전히 살아 있고
복귀를 기다리는 상태로 돌아간다. `HOLD` 의 이탈만 "소멸"이다 (§6.5 추격 금지).

⚠️ **같은 "안쪽 복귀"라도 포지션 보유 중이면 청산 신호**이며 그것은 `decision` 소관이다
(§6.5, 절대 규칙 #4). 이 모듈은 **사실만** 판정하고 진입/청산 해석은 소비처가 한다.

**②의 허용 오차만 ATR 배수다.** ①③④는 종가 부등호라 허용 오차가 없다 — 넣으면 "약간
못 넘었지만 넘은 걸로 친다"가 되고, 그 폭이 곧 은닉 파라미터가 된다.

②가 ATR 배수인 것은 §4.3.2 가 지정한 값이며, 구조물 허용 오차를 ATR 로 옮기려던 계획
(`docs/rules/structure_rules.md` §9.2)의 첫 적용이기도 하다.

## ③ 이탈은 **종가** 기준이다 — 꼬리는 세지 않는다

리테스트 자리는 유동성이 몰린 곳이라 **꼬리가 레벨을 찌르는 것은 정상 동작**이다.
꼬리 이탈로 실패 판정하면 정상적인 리테스트가 거의 다 탈락한다. 종가가 레벨 아래로
마감할 때 전제가 무너진다 — `choch_bos`·`order_block._mitigated` 와 같은 규칙이다.

## 실패는 소멸이다 — 추격하지 않는다

> 실패 처리: ③에서 이탈하면 그 레그는 **미체결 소멸**한다. 추격하지 않는다 (§6.5)

실패해도 **나중에 새 돌파가 나오면 새 시도로 센다.** 같은 레벨이 여러 번 시험받는 것은
정상이기 때문이다. 다만 그것이 실패를 지워버리면 안 되므로 `failed_attempts` 로 남긴다 —
"세 번 실패하고 네 번째에 성공한 레벨"과 "한 번에 성공한 레벨"은 다른 사실이다.

## 미래를 보지 않는다

판정은 `candles` 의 **마지막 봉까지만** 본다. 호출부가 as-of 창을 넘기면 그 창이 곧
판정 시점이다. 되돌림을 언제까지 기다릴지는 **여기서 정하지 않는다** — 셋업의 만료
규칙(오더블록 `max_age_bars`, 채널 유효 구간)이 창을 자르는 방식으로 이미 표현돼 있고,
여기에 또 기한 파라미터를 만들면 같은 개념이 두 곳에 생긴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.candle import Candle
from updown.common.numeric import fixed_context


class RetestMode(StrEnum):
    """돌파 이후 무엇을 기다리는가 (spec §4.3.2, v2.4).

    Attributes:
        HOLD: **진짜 돌파** — 되돌림 후 종가가 레벨 **위**면 확정 (`close >= level`).
            레벨이 지지로 뒤집힌 것이고 추세 지속에 베팅한다.
        RECLAIM: **가짜 돌파** — 종가가 레벨 **아래로 복귀**하면 확정 (`close < level`).
            돌파가 실패한 것이고 반전(페이크아웃 역이용)에 베팅한다.

    Note:
        **①②(돌파·되돌림)는 공유하고 ③만 갈린다.** 두 함수로 나누면 "돌파"의 정의가
        갈라지고, 그것이 §4.3.2 가 애초에 통합을 지시한 이유다.

        `RECLAIM` 은 ②가 ③에 흡수된다 — "레벨 아래로 마감"이 곧 되돌아온 것이므로
        되돌림 허용 오차를 따로 보지 않는다.
    """

    HOLD = "HOLD"
    RECLAIM = "RECLAIM"


class RetestStage(StrEnum):
    """리테스트 진행 상태 (spec §4.3.2).

    Attributes:
        NO_BREAKOUT: ① 미충족 — 기준 레벨을 종가로 넘은 적이 없다.
        AWAITING_PULLBACK: ① 충족, ② 대기 — 돌파했으나 아직 안 돌아왔다.
        AWAITING_CONFIRM: ② 충족, ③④ 대기 — 되돌아왔고 지지 확인 중이다.
        CONFIRMED: 4단계 전부 충족 — **진입 가능**.
        FAILED: ③ 이탈 — 이 시도는 소멸했다. 추격하지 않는다.
    """

    NO_BREAKOUT = "NO_BREAKOUT"
    AWAITING_PULLBACK = "AWAITING_PULLBACK"
    AWAITING_CONFIRM = "AWAITING_CONFIRM"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RetestParams:
    """리테스트 판정 파라미터 — 셋업이 조절할 수 있는 **유일한** 부분 (spec §4.3.2).

    Attributes:
        pullback_atr_multiple: ② 되돌림 인정 범위 (ATR 배수).
        confirm_bars: ③④ 지지 확인에 필요한 **연속 봉마감** 수.

    Note:
        기본값 `0.5xATR` 은 "되돌림이 레벨 근처에 왔다"를 판정하는 값이다. 1.0 이상으로
        두면 레벨에서 한참 떨어진 자리도 되돌림으로 세고, 0 으로 두면 레벨을 정확히
        찍어야 해서 실제 차트에서 거의 안 나온다.

        `confirm_bars` 기본값 1 은 §4.3.2 ④의 최소 형태다 — **되돌림 봉 자신이 레벨 위로
        마감**하면 확인이다. 이것이 고전적인 "리테스트 캔들이 지지 위에서 마감" 형태이며,
        2 이상은 더 보수적인 셋업이 선택하는 값이다.

        ⛔ **성과로 조정하지 않는다** (spec §5.6.2). 값을 바꾸면 진입 시점이 바뀌므로
        수익 곡선을 보며 돌리는 순간 그것이 자동조율이다.
    """

    pullback_atr_multiple: Decimal = Decimal("0.5")
    confirm_bars: int = 1

    def __post_init__(self) -> None:
        """확인 봉이 0 이면 ④ 봉마감 조건 자체가 사라진다.

        Raises:
            ValueError: `confirm_bars` 가 1 미만이거나 `pullback_atr_multiple` 이 음수인 경우.
        """
        if self.confirm_bars < 1:
            raise ValueError(
                f"retest.confirm_bars 는 1 이상이어야 한다 — 0 이면 §4.3.2 ④ 봉마감 확정이 "
                f"사라져 봉 중간 반등이 진입이 된다. 받은 값: {self.confirm_bars}"
            )
        if self.pullback_atr_multiple < 0:
            raise ValueError(
                f"retest.pullback_atr_multiple 은 0 이상이어야 한다 — "
                f"받은 값: {self.pullback_atr_multiple}"
            )


@dataclass(frozen=True, slots=True)
class RetestResult:
    """리테스트 판정 결과.

    Attributes:
        stage: 마지막 봉 기준 진행 상태.
        level: 판정에 쓴 기준 레벨.
        breakout_index: ① 돌파 봉. 없으면 None.
        pullback_index: ② 되돌림 봉. 없으면 None.
        confirmed_index: ④ 확정 봉 — **이 봉의 종가에 진입 신호가 선다**. 없으면 None.
        failed_attempts: 이 구간에서 실패한 시도 수 (모듈 docstring).

    Note:
        `confirmed_index` 는 신호가 **확정된** 봉이지 체결 봉이 아니다. 봉마감 확정이므로
        실제 진입은 다음 봉부터 가능하다 (spec §4.2).
    """

    stage: RetestStage
    mode: RetestMode
    level: Decimal
    breakout_index: int | None
    pullback_index: int | None
    confirmed_index: int | None
    failed_attempts: int

    @property
    def entry_allowed(self) -> bool:
        """진입해도 되는가 — `CONFIRMED` 일 때만 True 다."""
        return self.stage is RetestStage.CONFIRMED


def evaluate_retest(
    candles: Sequence[Candle],
    level: Decimal,
    atr_series: Sequence[Decimal | None],
    params: RetestParams | None = None,
    start: int = 0,
    mode: RetestMode = RetestMode.HOLD,
) -> RetestResult:
    """§4.3.2 의 4단계를 판정한다 — 모드에 따라 ③이 갈린다.

    Args:
        candles: `ts` 오름차순 캔들. **마지막 봉이 판정 시점(as-of)** 이다.
        level: 기준 레벨 — 채널 경계·넥라인·FVG 경계·추세선 값. 셋업이 제공한다.
        atr_series: 캔들과 **길이가 같은** ATR 계열 (`indicators.atr`). 워밍업은 None.
        params: 판정 파라미터.
        start: 이 봉부터 본다. 구조물이 생성된 봉을 넘긴다.
        mode: `HOLD`(진짜 돌파 리테스트) / `RECLAIM`(가짜 돌파 복귀).

    Returns:
        마지막 봉 기준 판정.

    Raises:
        ValueError: `atr_series` 길이가 캔들과 다른 경우. 어긋나면 엉뚱한 봉의 ATR 로
            되돌림을 판정하면서 **아무 예외도 나지 않는다** (`analysis/README.md` 경고).

    Note:
        **되돌림 허용 오차는 되돌림 봉 자신의 ATR** 로 잰다. 돌파 봉 ATR 로 재면 변동성이
        커진 구간에서 옛 값으로 판정하게 된다.

        ATR 이 None(워밍업)이면 허용 오차 0 으로 본다 — 레벨을 정확히 닿아야 되돌림이다.
        임의의 대체값을 쓰면 그 값이 판정을 좌우한다 (절대 규칙 #8).
    """
    if len(atr_series) != len(candles):
        raise ValueError(
            f"atr_series 길이가 캔들과 다르다: {len(atr_series)} vs {len(candles)} — "
            f"워밍업을 잘라내지 말고 None 으로 채워 넘긴다"
        )
    settings = params or RetestParams()

    stage = RetestStage.NO_BREAKOUT
    breakout: int | None = None
    pullback: int | None = None
    confirmed: int | None = None
    holds = 0
    failures = 0

    for position in range(max(start, 0), len(candles)):
        candle = candles[position]

        if stage in (RetestStage.NO_BREAKOUT, RetestStage.FAILED):
            # ① 돌파 — 종가 기준. 실패 후에도 새 돌파는 새 시도다 (모듈 docstring).
            if candle.close > level:
                stage = RetestStage.AWAITING_PULLBACK
                breakout, pullback, confirmed, holds = position, None, None, 0
            continue

        if stage is RetestStage.AWAITING_PULLBACK:
            if mode is RetestMode.RECLAIM:
                # RECLAIM 은 ②가 ③에 흡수된다 — "레벨 아래 마감"이 곧 되돌아온 것이다.
                if candle.close >= level:
                    continue
                pullback = position
                stage = RetestStage.AWAITING_CONFIRM
                holds = 0
            else:
                # ② 되돌림 — 레벨 + kxATR 안으로 저가가 들어오면 돌아온 것이다.
                atr_now = atr_series[position]
                with fixed_context():
                    tolerance = (
                        settings.pullback_atr_multiple * atr_now
                        if atr_now is not None
                        else Decimal(0)
                    )
                    reached = candle.low <= level + tolerance
                if not reached:
                    continue
                pullback = position
                stage = RetestStage.AWAITING_CONFIRM
                holds = 0
                # 되돌림 봉 자신이 ③④를 만족할 수 있다 — 아래로 흘러 판정한다.

        if stage is RetestStage.AWAITING_CONFIRM:
            # ③ 확인 + ④ 봉마감. 모드가 갈리는 유일한 지점이다.
            if mode is RetestMode.RECLAIM:
                if candle.close >= level:
                    # 다시 레벨 위로 마감했다 — 복귀가 아직 확정되지 않았다.
                    # **실패가 아니다**: 돌파는 여전히 살아 있고 시도가 이어진다.
                    stage = RetestStage.AWAITING_PULLBACK
                    pullback = None
                    holds = 0
                    continue
            elif candle.close < level:
                # 지지 확인 실패 — 이 시도는 소멸한다 (§6.5 "추격 금지").
                stage = RetestStage.FAILED
                failures += 1
                holds = 0
                continue
            holds += 1
            if holds >= settings.confirm_bars:
                stage = RetestStage.CONFIRMED
                confirmed = position
                break

    return RetestResult(
        stage=stage,
        mode=mode,
        level=level,
        breakout_index=breakout,
        pullback_index=pullback,
        confirmed_index=confirmed,
        failed_attempts=failures,
    )
