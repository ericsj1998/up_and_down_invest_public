"""추세 상태 머신 + 히스테리시스 (P1-4-7 · spec §4.16).

## 전이는 비대칭이다 ⚠️ — 이것이 히스테리시스이자 안전장치다

| 전이 | 요구 조건 | 이유 |
|------|----------|------|
| **DOWN -> UP** | 3단계 전부 (CHoCH + BOS + 200MA) | 가짜 반등 위험이 가장 큰 구간 |
| **SIDEWAYS -> UP** | 구조 HH/HL + MA 정배열 + 200일선 상회 | §4.16 입력 1+2 동시 충족 |
| **→ DOWN** | 저점 이탈 + 구조가 HH/HL 아님 + MA 가 상승 미지지 | 입력 1이 DOWN 을 부정한다 |

## 입력 1과 입력 2가 충돌하면 **SIDEWAYS** 다 ⚠️ (실측 근거)

§4.16 은 판정 입력을 **독립 카테고리**로 열거하고 상태 전이에 "복수의 확정 조건"을
요구한다. 그렇다면 **어느 한 입력이 단독으로 상태를 확정할 수 없다.**

처음에는 DOWN 판정에 시장 구조(입력 2)만 썼다. 그러자 상승 픽스처의 판정 구간에서
**8봉 연속 DOWN** 이 나왔는데, 그 구간은:

| 실제 상태 | 값 |
|---|---|
| MA 배열 | **정배열** (입력 1) |
| 200일선 | **상회** (입력 1) |
| 가격 | 175.8M → 177.9M (**+1.2% 상승**) |
| 시장 구조 | `lower` → `mixed` (입력 2, 2봉 프랙탈의 순간적 판독) |

MA 가 정배열이고 200일선 위인데 DOWN 이라고 판정하면 **입력 1을 무시한 것**이고, §5.4
게이트 2 가 **상승 추세의 정상 눌림에서 모든 진입을 막는다** — 정작 진입하고 싶은 구간이다.

→ **MA 정배열 + 200일선 상회는 DOWN 을 부정한다.** 두 입력이 반대를 말하면 `SIDEWAYS`
("아직 모른다")가 정답이며, 그것이 상태를 3개로 둔 이유다.

### UP 진입 경로가 **둘**인 이유 (실측 근거)

§4.16 의 3단계 확인은 제목 그대로 **"전환 판정 방법론 (DOWN→UP)"** 이다. 그런데 그 경로만
두면 **하락을 겪지 않은 상승 종목은 영원히 UP 이 되지 못한다** — 꾸준히 오르는 시장에는
깨뜨릴 LH(낮아진 고점)가 없어 CHoCH 가 성립할 수 없기 때문이다.

실제로 상승 픽스처(+12.29%)에서 **UP 이 0봉**이고 SIDEWAYS/DOWN 만 나왔다. 그 상태로는
§5.4 게이트 2(역추세 차단)가 **상승장에서 모든 진입을 막는다.**

→ `SIDEWAYS → UP` 은 §4.16 **판정 입력 1(MA 구조: 정배열 + 200일선 상회)** 과
**입력 2(시장 구조: HH/HL)** 를 동시에 요구한다. "복수의 확정 조건"(§4.16)을 만족하며,
3단계 경로를 우회하는 것이 아니다 — `DOWN` 에서 올라오는 길은 그대로 3단계다.

방향은 spec §4.15·§1.2.1·§5.3 이 일관되게 쓰는 원칙과 맞다 — **틀렸을 때 결과가 "거래를
덜 함"인 쪽을 택한다.**

### DOWN 조건에 구조 확인을 **함께** 요구하는 이유 (실측 근거)

처음에는 "직전 저점 종가 이탈" 하나로 DOWN 을 판정했다. 그러자 **상승 픽스처(구조가
`higher`)에서 1봉 간격 `SIDEWAYS↔DOWN` 왕복**이 나왔다 — 2봉 프랙탈에서는 미세 스윙
로우가 촘촘해 상승 중 눌림마다 "저점 이탈"이 성립한다.

구조가 여전히 HH/HL 이면 저점 하나가 깨진 것은 **눌림이지 추세 전환이 아니다.**
새 파라미터를 만들지 않고 이미 있는 구조 판정을 조건에 넣어 해결했다.

### 이것이 청산을 늦추지 않는다

`TrendState` 는 **신규 진입 게이트**다 (§5.4 게이트 2, §4.8 전환 게이트). 보유 포지션의
청산은 RiskManager 의 손절이 담당하며(절대 규칙 #4) 추세 상태와 무관하게 동작한다.
그래서 DOWN 판정이 한 박자 신중해도 **리스크 축소가 지연되지 않는다.**

## 최소 유지 봉 수 — 비대칭만으로는 부족했다 ⚠️ (1년치 실측이 반박했다)

처음에는 "들어가기 어렵고 나오기 쉬우면 왕복이 불가능하다"는 구조적 비대칭만으로
히스테리시스가 충분하다고 판단했다. 골든 픽스처(240~288봉)에서는 왕복 최소 간격이
5봉 이상이어서 그 판단이 맞아 보였다.

**1년치(8,625봉 판정)로 재검증하니 왕복 최소 간격이 1봉이었다.** 원인은 시장 구조 판독이
봉마다 뒤집힐 수 있다는 것 — 2봉 프랙탈에서 새 스윙이 확정되면 HH/HL 판정이 즉시 바뀐다.

→ **`MIN_HOLD_BARS` 를 도입한다.** 파라미터를 늘리는 것이 싫었지만, 측정이 주장을
반박했으면 주장을 고치는 것이 맞다. 이 값은 **성과를 보고 정한 값이 아니라 깜빡임(DoD 2)을
막는 값**이므로 §5.6.2 가 금지한 미세조정이 아니다.

**적용도 비대칭이다**: `UP`·`SIDEWAYS` 로의 전이만 유지 봉 수를 요구하고 **`DOWN` 전이는
즉시 허용**한다. DOWN 을 늦추면 진입 게이트가 계속 열려 있는 셈이므로 안전 방향과 어긋난다.

## 200일선 하나로는 UP 을 떠나지 않는다

`UP` 이탈 조건에 200일선을 넣지 않은 것은 의도다. 가격이 200일선 근처를 오가면 종가가
봉마다 위아래를 넘나들 수 있고, 그것을 이탈 조건으로 쓰면 **정확히 DoD 2 가 금지한 1봉
단위 진동**이 된다. 구조(HL)는 스윙 확정이 필요하므로 그런 진동을 만들지 않는다.

## SIDEWAYS 는 "아직 모른다"다

하락 구조가 깨졌지만 3단계가 안 된 상태가 `SIDEWAYS` 다. §4.16 이 상태를 3개로 둔 이유가
이것이며, `DOWN` 으로 남겨 두면 전환 초입의 리스크 절반 진입(§4.16 표)을 설명할 수 없다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.analysis.trend.choch_bos import TransitionReading
from updown.common.domain.candle import Candle
from updown.common.domain.reports import TrendDirection
from updown.common.domain.trend import StructurePattern, TrendStage

MIN_HOLD_BARS = 5
"""`UP`·`SIDEWAYS` 로 전이하기 전 현재 상태를 유지해야 하는 최소 봉 수.

⛔ **이 값은 안정성 파라미터다 — 성과를 보고 바꾸는 것이 금지된다** (spec §5.6.5).

목적은 **판정 깜빡임(노이즈) 제거**이고 성과 최적화가 아니다. 값을 조절하면 진입 횟수와
타이밍이 바뀌어 백테스트 성과가 움직이므로, **"노이즈 제거를 개선했다"는 설명으로 성과
튜닝을 정당화할 수 있다** — 그것이 §5.6 이 막으려는 과최적화의 가장 그럴듯한 뒷문이다.

| 바꾸는 동기 | 판정 |
|---|---|
| 판정이 봉마다 뒤집힌다 (관측 가능한 결함) | ⭕ 정당 — **바꾸게 만든 관측을 함께 남긴다** |
| 백테스트 승률·손익이 나아진다 | ⛔ 금지 (spec §5.6.2·§5.6.5 위반) |

근거: 스윙 확정에 `right_bars`(표준값 2) 봉이 필요하고, 구조 판정(HH/HL)에는 같은 종류
스윙이 2개 더 필요하다. 정당한 구조 변화 사이에는 그 정도 간격이 물리적으로 존재한다.

도입 자체가 관측의 결과였다 — 골든 픽스처(240봉)에서는 왕복 최소 간격이 5봉 이상이었는데
**1년치(8,625봉)에서 1봉**이었다 (`docs/rules/trend_rules.md` §5.5).

`DOWN` 전이에는 적용하지 않는다 — 모듈 docstring 의 비대칭 참조.
"""


@dataclass(frozen=True, slots=True)
class Decision:
    """한 봉에서의 상태 판정 결과.

    Attributes:
        state: 판정된 상태.
        changed: 직전 상태에서 바뀌었는가.
        reason: 전이 근거 요약 (없으면 빈 문자열).
    """

    state: TrendDirection
    changed: bool
    reason: str = ""


def structure_broken_down(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
) -> bool:
    """직전 HL 을 **종가로** 하향 이탈했는가 — DOWN 전이 조건.

    Args:
        candles: `ts` 오름차순 캔들. 마지막 봉이 판정 기준이다.
        swings: `prior_swings()` 결과.

    Returns:
        이탈했으면 True. 저점이 2개 미만이면 False (판정 불가는 이탈이 아니다).

    Note:
        **가장 최근 스윙 로우**를 기준으로 한다. 그 저점이 깨지면 상승 구조의 전제
        (저점이 높아진다)가 무너진 것이다. 꼬리 이탈은 세지 않는다 — 모듈
        `choch_bos` 의 돌파 정의와 같은 규칙이며, 꼬리 이탈은 스윕일 수 있다.
    """
    lows = [swing for swing in swings if swing.kind is SwingKind.LOW]
    if not lows or not candles:
        return False
    return candles[-1].close < lows[-1].price


def decide(
    previous: TrendDirection,
    transition: TransitionReading,
    structure: StructurePattern,
    broke_down: bool,
    *,
    ma_bullish: bool = False,
    above_ma200: bool | None = None,
    bars_in_state: int = MIN_HOLD_BARS,
    recent_downside_halt: bool = False,
) -> Decision:
    """다음 상태를 판정한다 (spec §4.16).

    Args:
        previous: 직전 상태.
        transition: 3단계 전환 판정.
        structure: 시장 구조 패턴.
        broke_down: 직전 저점 하향 종가 이탈 여부. **단독으로는 DOWN 을 만들지 않는다** —
            구조가 HH/HL 이면 눌림으로 본다 (모듈 docstring).
        ma_bullish: 이동평균이 정배열인가 (§4.16 판정 입력 1). `SIDEWAYS → UP` 경로에 쓴다.
        above_ma200: 종가가 200일선 위인가. 산출 불가면 None 이며 **UP 전환을 허용하지
            않는다** — 200일선을 모르는 상태로 UP 을 확정하면 입력 1이 빠진 반쪽이다.
        bars_in_state: 현재 상태를 유지한 봉 수. `MIN_HOLD_BARS` 미만이면 `UP`·`SIDEWAYS`
            로 전이하지 않는다 (모듈 docstring). 기본값은 제약이 없는 상태이며, 순수
            함수 단위 테스트가 유지 조건과 무관하게 규칙을 검증할 수 있게 한 것이다.
        recent_downside_halt: 최근 **하방** 거래소 조치 이력이 있는가. True 면 3단계를
            통과해도 UP 전환을 확정하지 않는다 (spec §4.16).

    Returns:
        판정 결과.

    Note:
        `recent_downside_halt` 는 지금 항상 False 다 — `MarketSession` 에 조치 **방향**
        필드가 없어(P2 차단 항목 C2-1) 상방/하방을 구분할 수 없다. 코인은 VI 개념이
        없어 무해하며, 미국·국내 주식을 붙이는 P2-9 전에 C2-1 과 함께 배선한다.
        인자를 미리 둔 이유는 배선 지점을 코드에 남겨 잊지 않기 위해서다.

        **상방 조치는 UP 근거로 쓰지 않는다** (§4.16 비대칭) — 그래서 이 함수에 상방
        조치 인자가 아예 없다. 없는 것이 규칙의 표현이다.
    """
    # 입력 1(MA 구조)이 상승을 지지하는가 — DOWN 을 부정하고 UP 을 지지하는 조건이다.
    ma_supports_up = ma_bullish and above_ma200 is True

    if not recent_downside_halt:
        # DOWN 에서 올라오는 길 — 3단계 전부 (§4.16 전환 판정 방법론)
        if transition.stage is TrendStage.MA_RECLAIM:
            return _settle(previous, TrendDirection.UP, "three_stage_confirmed", bars_in_state)
        # 하락을 겪지 않은 상승 — 판정 입력 1+2 동시 충족 (모듈 docstring)
        if (
            previous is not TrendDirection.DOWN
            and structure is StructurePattern.HIGHER
            and (ma_supports_up)
        ):
            return _settle(previous, TrendDirection.UP, "ma_and_structure_aligned", bars_in_state)

    # DOWN 은 세 조건을 함께 요구한다 — 저점 이탈 + 구조가 상승이 아님 + MA 가 상승을
    # 지지하지 않음. MA 가 지지하면 입력 충돌이므로 SIDEWAYS 로 보류한다.
    if broke_down and structure is not StructurePattern.HIGHER and not ma_supports_up:
        return _settle(previous, TrendDirection.DOWN, "structure_break_down", bars_in_state)

    if previous is TrendDirection.UP:
        # 이탈 조건이 없으면 UP 을 유지한다 — 200일선 흔들림으로 나가지 않는다.
        return Decision(TrendDirection.UP, changed=False)

    if transition.stage is TrendStage.BOS or structure is StructurePattern.HIGHER or ma_supports_up:
        return _settle(previous, TrendDirection.SIDEWAYS, "downtrend_structure_lost", bars_in_state)

    if previous is TrendDirection.DOWN:
        return Decision(TrendDirection.DOWN, changed=False)
    if structure is StructurePattern.LOWER and not ma_supports_up:
        return _settle(previous, TrendDirection.DOWN, "lower_structure", bars_in_state)
    return Decision(previous, changed=False)


def _settle(
    previous: TrendDirection,
    target: TrendDirection,
    reason: str,
    bars_in_state: int = MIN_HOLD_BARS,
) -> Decision:
    """목표 상태로 정착시킨다.

    Args:
        previous: 직전 상태.
        target: 목표 상태.
        reason: 전이 근거.
        bars_in_state: 현재 상태 유지 봉 수.

    Returns:
        판정. 이미 목표 상태면 `changed=False` 다.

    Note:
        `DOWN` 이 아닌 상태로의 전이는 `MIN_HOLD_BARS` 를 요구한다. 미달이면 **전이하지
        않고 현재 상태를 유지**한다 (모듈 docstring 의 비대칭).
    """
    if previous is target:
        return Decision(target, changed=False)
    if target is not TrendDirection.DOWN and bars_in_state < MIN_HOLD_BARS:
        return Decision(previous, changed=False)
    return Decision(target, changed=True, reason=reason)


def initial_state(structure: StructurePattern) -> TrendDirection:
    """시작 상태 — 이력이 없을 때의 판정.

    Args:
        structure: 시장 구조 패턴.

    Returns:
        구조가 명확하면 그 방향, 아니면 `SIDEWAYS`.

    Note:
        시작 상태를 `UP` 으로 두지 않는다. 이력 없이 UP 에서 시작하면 3단계 확인을
        건너뛰고 진입이 열린다 — 시작 조건이 안전 원칙의 구멍이 되면 안 된다.
    """
    if structure is StructurePattern.LOWER:
        return TrendDirection.DOWN
    return TrendDirection.SIDEWAYS


def transition_timestamp(candles: Sequence[Candle]) -> datetime:
    """전이 시각 — 마지막 봉의 **봉 시각**이다 (벽시계가 아니다).

    Args:
        candles: 판정에 쓴 캔들.

    Returns:
        마지막 봉의 `ts`.

    Raises:
        ValueError: 캔들이 빈 경우.

    Note:
        벽시계를 쓰면 백테스트가 같은 값을 재현하지 못한다 (원칙 P1). P1-1 의
        `structures.created_at` 과 같은 결정이다.
    """
    if not candles:
        raise ValueError("빈 캔들에서는 전이 시각을 정할 수 없다")
    return candles[-1].ts
