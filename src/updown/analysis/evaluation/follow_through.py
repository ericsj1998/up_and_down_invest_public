"""후속 이행률 — 탐지가 예측대로 움직였는지 **규칙으로** 잰다 (spec §5.6.6).

## 무엇을 대체하는가

원래 P1-5 DoD 는 "수동 라벨링 구간과 탐지 결과 대조 → 정탐/오탐 리포트"였다(D1-3).
spec §5.6.6 이 그것을 폐기했다 — 정답을 사람 눈으로 만들면 배제하려던 재량이 시스템
근본에 다시 들어온다. 같은 질문("이 탐지가 쓸모 있었나")을 사람 없이 답하는 것이 이
모듈이다.

**정의는 스펙이 못박았다** (§5.6.6):

> 탐지된 셋업의 진입 조건이 충족된 뒤,
> **손절가 도달 전에 1차 익절가 도달 → `이행`**, 반대면 `미이행`,
> `max_hold_bars` 안에 둘 다 미도달이면 `미결`(판정 제외).
> 같은 봉에 둘 다 닿으면 **손절 우선** (D1-4 확정값).

## ⚠️ 이행률은 성과가 아니다 — 둘은 다른 것을 잰다

| | 재는 것 | 모형화하는 것 | 어디서 |
|---|---|---|---|
| **후속 이행률** | 룰이 **예측한 방향**으로 갔나 | 가격 경로만 | 여기 (`analysis/`) |
| **백테스트 성과** | 실제로 **돈을 벌었나** | 체결·수량·수수료·슬리피지 | P1-8 (`orchestration/`) |

이행률은 비용도 수량도 보지 않으므로 **이행률이 높아도 성과가 나쁠 수 있다**
(RR 이 낮으면 승률이 높아도 잃는다). §5.6.6 이 둘을 **모두** 요구한 이유이며, 이행률만
보고 룰을 채택하면 그 자체가 §5.6.7 위반이다.

이행률이 성과보다 먼저 나오는 것은 **의존성이 적기 때문**이다 — 캔들과 셋업만 있으면
되므로 RiskManager·비용 테이블·체결 모형이 없는 P1 에서도 잴 수 있다.

## 🔴 체결 모델 — 무엇을 모형화하고 무엇을 안 하는가 (T19 · §1-0ac)

**여기 적어두는 이유**: 이 가정이 어디에도 없던 탓에 결과만 보는 사람은 §6.3 의
3분할과 §6.9 의 반익반본이 반영된 줄 알았다. 실제로는 둘 다 아니다.

| 항목 | 모형 | 실제 규칙 | 편향 |
|---|---|---|---|
| 진입 트리거 | 첫 레그(박스 상단) 터치 | §6.3 그대로 | ✅ |
| **체결가** | 평단이 그 봉에 닿았으면 평단, 아니면 **첫 레그가** (F1) | 25/25/50 레그별 | 🟢 보수 |
| **청산** | **반익반본** — 1차에서 `ratio` 만큼 청산 + 스탑 본절 상향 | §6.9 그대로 | ✅ |
| 손절 체결 | 정확히 손절가 = -1.000R | 갭 통과 시 더 나쁨 | ✅ 실측 0/200건 |
| 익절 체결 | 정확히 목표가 | 지정가 | ✅ |
| 2차 익절가 | 셋업의 **계획값** | §9.1 상 1차 체결 후 재평가로 재확정 | ⚠️ 재평가 없음 |

**반익반본이 손익 분포를 바꾼다** (§6.9). 1차만 먹고 본절로 밀리면 `-1R` 이 아니라
`0.5 x R1` 이고, 2차까지 가면 `0.5 x R1 + 0.5 x R2` 다. 옛 모형(1차에서 전량)은
순수 승자를 **과대**평가하고 러너를 **과소**평가했다 — 두 방향이라 순효과는 재봐야 안다.

⚠️ `Outcome` 정의는 **안 바뀐다** (§5.6.6: 손절 전에 1차 익절가 도달 = 이행). 1차를
먹고 본절에 밀린 것도 `FOLLOWED` 다 — 이행률과 실현 R 은 다른 질문이다.

🔴 **F1 이전에는 체결가가 항상 계획 평단이었다.** 트리거는 첫 레그에서 걸리는데 손익은
평단(전부 체결된 값)으로 계산했으므로, 상단만 찍고 튀어 오른 거래가 공짜로 좋은 평단을
받았다 — 실측 gap 0.12~0.60R 이고 **셋업 후보마다 4배 달라** 축 판정을 오염시켰다.

⚠️ 손절 체결 가정이 안전한 것은 **단타 만료가 오버나이트 보유를 막기 때문**이다.
장투(P3)로 밤을 넘기면 갭 위험이 살아난다.

## 왜 미래를 보는 코드를 `analysis/` 에 두는가

이 모듈은 **일부러 미래 봉을 본다.** 그것이 측정의 정의다. 그래서 탐지기가 이걸 부르면
곧바로 미래 참조가 되며, `AsOfSequence` 가 막아온 것이 정문으로 들어온다.

`orchestration/` 으로 올리면 탐지기가 import 할 수 없어 안전하지만, 그러면 **비용·체결이
없는 순수 측정**이 백테스트와 같은 층에 놓여 둘의 구분이 흐려진다. 대신 방향 자체를
`.importlinter` 의 `forbidden` 계약으로 막았다 — `analysis.detectors → analysis.evaluation`
은 CI 에서 실패한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.analysis.structures.retest import RetestMode, evaluate_retest
from updown.common.domain.candle import Candle
from updown.common.domain.setup import (
    EntryTrigger,
    TakeProfitStep,
    TpFollowUpAction,
    TradeSetup,
)
from updown.common.numeric import fixed_context
from updown.marketdata.ingest.sessions import SessionCalendar

MIN_SAMPLE = 30
"""판정에 필요한 최소 표본 (spec §12.9, §5.6.7).

⛔ 안정성 파라미터다 (spec §5.6.5). 표본이 모자라면 **수치를 보고하되 판정하지 않는다** —
기준을 낮춰 통과시키는 것이 과최적화의 첫 단추다.
"""

DEFAULT_MAX_HOLD_BARS = 100
"""이행/미이행을 가릴 기한 (봉 수).

⛔ **안정성 파라미터다 — 성과로 조정 금지** (spec §5.6.5).

새 자유 파라미터를 만들지 않으려고 `order_block.max_age_bars` 와 **같은 값**을 썼다.
박스가 100봉이면 시장이 잊었다고 보는데, 그 박스에서 출발한 거래의 판정 기한만 더 길게
잡을 근거가 없다.

**창 길이를 늘리면 이행률이 올라간다** (기다릴수록 익절가에 닿을 확률이 커진다). 그래서
이 값은 숫자를 만드는 손잡이가 될 수 있고, 방어선은 두 가지다:
1. 여기서 성과 근거로 바꾸는 것을 금지한다 (§5.6.5)
2. 리포트가 **민감도(50/100/200봉)를 항상 함께 싣는다** — 유리한 창을 고르면 표에서 보인다
"""


class Outcome(StrEnum):
    """이행 판정 결과 (spec §5.6.6 · 단타 강제 청산은 P1 §1-0k).

    Attributes:
        FOLLOWED: 이행 — 손절 도달 전에 1차 익절가에 닿았다.
        NOT_FOLLOWED: 미이행 — 1차 익절 전에 손절가에 닿았다.
        EXPIRED: **만료 청산** — 둘 다 닿기 전에 보유 기한이 끝나 강제 청산했다.
            손익이 실제로 발생한 **진짜 결과**이므로 판정에 **포함**한다.
        UNRESOLVED: 미결 — 캔들이 모자라 판정할 수 없었다. 판정에서 **제외**한다.

    Note:
        🔴 `EXPIRED` 와 `UNRESOLVED` 는 전혀 다르다.

        | | 뜻 | 손익 | 판정 |
        |---|---|---|---|
        | `EXPIRED` | 장 마감에 털었다 | **있다** | 포함 |
        | `UNRESOLVED` | 데이터가 끝나 모른다 | 없다 | 제외 |

        예전에는 기한 초과가 전부 `UNRESOLVED` 라 판정에서 빠졌는데, 단타는 **기한
        초과가 곧 청산**이라 빼면 안 된다. 만료 거래를 제외하면 "끝까지 기다렸다면"의
        성과를 재게 되고, 그건 우리가 절대 하지 않을 매매다.
    """

    FOLLOWED = "FOLLOWED"
    NOT_FOLLOWED = "NOT_FOLLOWED"
    EXPIRED = "EXPIRED"
    UNRESOLVED = "UNRESOLVED"

    @property
    def is_realized(self) -> bool:
        """손익이 실제로 발생한 결과인가 — 성과 집계 대상 여부."""
        return self is not Outcome.UNRESOLVED


@dataclass(frozen=True, slots=True)
class Verdict:
    """한 셋업의 이행 판정.

    Attributes:
        outcome: 판정 결과.
        bars_held: 판정까지 걸린 봉 수. 진입 봉에서 결판나면 0 이다. 미결이면 None.
        truncated: 미결이 **데이터 부족** 때문인가. 기한이 남았는데 캔들이 끝난 경우다.
        realized_r: 실현 손익 (R 단위, 계획 평단 기준). 미결이면 None.
        mfe_r: **최대 유리 이동** — 판정 전까지 도달한 최고가 (R 단위). 손절된 거래의
            이 값이 "방향은 맞았는데 못 버틴" 크기다. 잴 구간이 없으면 None.
        mae_r: **최대 불리 이동** — 판정 전까지 도달한 최저가 (R 단위, 보통 음수).
            익절된 거래의 이 값이 "손절에 얼마나 스쳤나"이며 손절 위치(축 K·P)의 근거다.

    Note:
        `truncated` 를 따로 두는 이유: 시계열 끝자락의 셋업은 기한을 채우지 못한 채
        미결이 된다(우측 절단). 이걸 "기한 내 미도달"과 섞으면 시장이 애매했던 것인지
        데이터가 짧았던 것인지 구분할 수 없다.

        `realized_r` 이 필요해진 이유는 `EXPIRED` 때문이다. 익절은 +RR, 손절은 -1 로
        값이 정해져 있지만 **만료는 그날 종가에 달렸다** — 그 값을 담을 자리가 없으면
        만료 거래의 손익을 셀 수 없다.

        🔴 `mfe_r`/`mae_r` 은 **진단 전용**이다. 어떤 규칙도 이 값을 읽어 진입·청산을
        정하지 않는다 — 그렇게 하면 미래를 보고 파는 것이고 실거래에서 재현되지 않는다.
        리포트에만 실린다.
    """

    outcome: Outcome
    bars_held: int | None
    truncated: bool
    realized_r: Decimal | None = None
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None


def entry_level(setup: TradeSetup) -> Decimal:
    """진입 트리거가 걸리는 가격 — 분할 진입의 **첫 레그**다.

    Args:
        setup: 판정할 셋업.

    Returns:
        첫 레그 가격.

    Raises:
        ValueError: `entry_plan` 이 비어 있는 경우. "숫자 없는 매수 추천 금지"(§4.3.1)를
            어긴 셋업이며 조용히 넘기면 측정이 통째로 무의미해진다 (절대 규칙 #8).

    Note:
        롱 온리(절대 규칙 #10)라 첫 레그가 가장 높은 가격이고, 가격이 **내려와** 닿는
        것이 진입이다. 계획 평단(`avg_entry`)이 아니라 첫 레그인 이유: 평단은 3분할이
        전부 체결됐을 때의 값이라 "진입이 시작된 시점"이 아니다.
    """
    if not setup.entry_plan:
        raise ValueError(f"{setup.rule_version}: entry_plan 이 비었다 (spec §4.3.1 완결성)")
    return setup.entry_plan[0].price


def find_entry(
    candles: Sequence[Candle],
    setup: TradeSetup,
    after: int,
    atr_series: Sequence[Decimal | None] | None = None,
) -> int | None:
    """진입 조건이 충족된 첫 봉을 찾는다.

    Args:
        candles: `ts` 오름차순 캔들.
        setup: 셋업.
        after: 이 봉 **다음**부터 찾는다. 통상 탐지가 확정된 봉 번호다.
        atr_series: 캔들과 **길이가 같은** ATR 계열. `RETEST_CONFIRM` 판정의 되돌림
            허용 범위가 ATR 배수라 필요하다 (§4.3.2 ②). `TOUCH` 는 쓰지 않는다.

    Returns:
        진입 봉 번호. 끝까지 미충족이면 None — **진입이 없었던 셋업은 판정 대상이
        아니다** (이행도 미이행도 아니다).

    Raises:
        NotImplementedError: 아직 판정 규칙을 정하지 않은 트리거인 경우.
        ValueError: `RETEST_CONFIRM` 인데 `atr_series` 가 없거나 길이가 다른 경우.

    Note:
        ## 두 트리거가 서로 반대 방향이다

        | 트리거 | 진입 조건 | 성격 |
        |---|---|---|
        | `TOUCH` | 가격이 레벨까지 **내려온다** (`low <= level`) | **되돌림 예측**(눌림목) |
        | `RETEST_CONFIRM` | 레벨을 **돌파**하고 되돌아와 **지켜낸다** | **돌파 확인** |

        `TOUCH` 만 있던 동안 시스템은 눌림목 매매만 할 수 있었고, 그 사실이 측정
        결과에 그대로 박혀 있었다 (실측: 10건 전부 위→아래 진입).

        ## `RETEST_CONFIRM` 을 근사하지 않는다

        판정은 `structures/retest.py` 공용 로직에 그대로 위임한다. §4.3.2 가 "기준
        레벨은 셋업이 제공하고 **판정은 공용 로직이 한다**"고 지정했으므로, 여기서
        "종가가 레벨 위" 같은 축약을 쓰면 채널·컵앤핸들·IFVG 와 판정이 갈라진다.

        `confirmed_index` 는 신호가 **확정된 봉**이지 체결 봉이 아니다 — 봉마감 확정이라
        실제 진입은 **다음 봉**이다 (§4.2). 확정 봉에서 체결했다고 세면 봉마감 가격을
        미리 안 셈이 되고, 그것이 lookahead 다.

        ## `CLOSE_CONFIRM` — 눌림목 셋업이 처음 쓰면서 규칙을 확정했다

        **탐지 봉이 곧 확정 봉이다.** 이 트리거를 쓰는 셋업은 "봉마감이 조건을 만족했다"를
        관측한 뒤에만 셋업을 내놓으므로, 확정 판정을 여기서 다시 할 것이 없다 —
        `RETEST_CONFIRM` 처럼 별도 상태기계를 돌리면 **같은 조건을 두 번 세는 것**이다.

        따라서 체결은 **확정 봉의 다음 봉**이다 (§4.2). `RETEST_CONFIRM` 과 같은 규칙이며
        다르게 둘 이유가 없다 — 확정 봉에서 체결했다고 세면 봉마감 가격을 미리 안 셈이고,
        그것이 lookahead 다.

        ⚠️ 계획가(`avg_entry`)는 확정 봉 **종가**인데 체결은 다음 봉이라 그 사이 갭이
        실제 슬리피지다. 그것을 여기서 보정하지 않는다 — 비용은 `common/costs.py` 가
        따로 세며, 두 곳에서 세면 이중 차감이 된다.
    """
    if setup.entry_trigger is EntryTrigger.TOUCH:
        level = entry_level(setup)
        for position in range(max(after + 1, 0), len(candles)):
            if candles[position].low <= level:
                return position
        return None

    if setup.entry_trigger is EntryTrigger.CLOSE_CONFIRM:
        # 탐지 봉이 곧 확정 봉이다 (docstring). 체결은 다음 봉 — 없으면 미진입이다.
        entry = max(after, 0) + 1
        return entry if entry < len(candles) else None

    if setup.entry_trigger in {EntryTrigger.RETEST_CONFIRM, EntryTrigger.RECLAIM_CONFIRM}:
        if atr_series is None:
            raise ValueError(
                f"{setup.entry_trigger} 판정에는 ATR 계열이 필요하다 (§4.3.2 ② 되돌림 "
                "허용 범위가 ATR 배수다) — 없이 근사하면 그 근사가 은닉 파라미터가 된다"
            )
        mode = (
            RetestMode.HOLD
            if setup.entry_trigger is EntryTrigger.RETEST_CONFIRM
            else RetestMode.RECLAIM
        )
        result = evaluate_retest(
            candles, entry_level(setup), atr_series, start=max(after, 0), mode=mode
        )
        if result.confirmed_index is None:
            return None
        # 봉마감 확정이므로 체결은 **다음 봉**이다 (spec §4.2).
        entry = result.confirmed_index + 1
        return entry if entry < len(candles) else None

    raise NotImplementedError(
        f"{setup.entry_trigger} 의 이행 판정 규칙이 아직 없다 — "
        f"근사로 때우면 측정 결과가 그 근사에 좌우된다 (spec §5.6.6)"
    )


def expiry_for_day_trade(
    candles: Sequence[Candle],
    entry_index: int,
    sessions: SessionCalendar,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
) -> int | None:
    """단타의 **강제 청산 봉**을 찾는다 (P1 §1-0k).

    Args:
        candles: `ts` 오름차순 캔들.
        entry_index: 진입 봉 번호.
        sessions: 거래일 달력.
        max_hold_bars: 24시간 장에서 쓰는 보유 상한.

    Returns:
        청산해야 하는 봉 번호. 데이터가 모자라 판정 못 하면 None.

    Note:
        🔴 **두 시장에 같은 규칙을 쓰지 않는다.** 의도("하루 언저리")는 같지만 구현이
        다르다:

        | | 만료 기준 | 왜 |
        |---|---|---|
        | 주식 | **그 거래일의 마지막 봉** | 시장이 정하는 경계다 |
        | 코인 | **진입 후 `max_hold_bars`** | 24시간 장에는 자연 경계가 없다 |

        코인에 UTC 자정 같은 벽시계 경계를 그으면 23:00 진입 거래는 1시간만 받는다 —
        진입 시각에 따라 주어지는 시간이 제멋대로가 된다. 경과 시간 상한이 그 왜곡을
        만들지 않는다.

        원칙 P6("분석 로직은 자산 종류를 모른다")는 **분석**에 대한 규정이고, 거래시간은
        규칙이 아니라 **시장의 사실**이라 여기서 갈리는 것이 P6 위반이 아니다.
    """
    if not 0 <= entry_index < len(candles):
        return None

    if sessions.always_open:
        limit = entry_index + max_hold_bars
        return limit if limit < len(candles) else None

    day = sessions.trading_day(candles[entry_index].ts)
    if day is None:
        # 장이 닫힌 시각의 봉이다. 진입 자체가 성립하지 않으므로 판정을 미룬다.
        return None

    # 같은 거래일의 마지막 봉을 찾는다. 하루치는 짧아 선형 탐색으로 충분하다.
    last_of_day = entry_index
    for position in range(entry_index + 1, len(candles)):
        if sessions.trading_day(candles[position].ts) != day:
            break
        last_of_day = position
    else:
        # 캔들이 거래일 도중에 끝났다 — 그 봉이 마지막인지 알 수 없다.
        return None
    return last_of_day


def _remaining_ladder(setup: TradeSetup, first: Decimal) -> tuple[TakeProfitStep, ...]:
    """판정에 쓸 익절 사다리 — 1차만 RiskManager 확정값으로 갈아 끼운다.

    Args:
        setup: 셋업.
        first: 확정된 1차 익절가 (`target_override` 또는 셋업 값).

    Returns:
        가격이 **단조 증가**하는 사다리.

    Note:
        🔴 확정 1차가 셋업의 2차보다 위로 올라갈 수 있다 (합류 저항이 멀리 잡힌 경우).
        그대로 두면 한 봉에서 2차가 1차보다 **낮은 가격**으로 체결된 것으로 세어져
        손익이 조용히 줄어든다. 뒤집힌 칸은 **버린다** — 없는 익절을 지어내는 것보다
        낫고, 그 물량은 마지막 칸이 흡수한다.

        2차를 확정값으로 갈아 끼우지 않는 이유: §9.1 상 2차는 1차 체결 후 **재평가로
        재확정**하는 값이라 진입 시점에 확정된 것이 아니다. 계획값을 그대로 쓴다.
    """
    rungs = [
        TakeProfitStep(price=first, ratio=setup.tp_ladder[0].ratio, then=setup.tp_ladder[0].then)
    ]
    for rung in setup.tp_ladder[1:]:
        if rung.price > rungs[-1].price:
            rungs.append(rung)
    return tuple(rungs)


def fill_price(candles: Sequence[Candle], setup: TradeSetup, entry_index: int) -> Decimal:
    """체결가 — **닿은 만큼만** 인정한다 (F1 · T19 · §1-0ac).

    Args:
        candles: `ts` 오름차순 캔들.
        setup: 셋업. `entry_plan` 과 `avg_entry` 를 쓴다.
        entry_index: 트리거가 걸린 봉 번호 (`find_entry` 의 반환값).

    Returns:
        계획 평단이 그 봉에서 실제로 닿았으면 `avg_entry`, 아니면 **첫 레그 가격**.

    Raises:
        ValueError: `entry_plan` 이 비었거나 `entry_index` 가 범위를 벗어난 경우.

    Note:
        🔴 **이 함수가 없던 동안 측정이 낙관적이었다** (T19). 트리거는 첫 레그(박스
        상단)에서 걸리는데 손익은 평단(3분할이 **전부** 체결된 값)으로 계산했다.
        25/25/50 이면 평단은 박스 높이의 62.5% 아래라, 가격이 상단만 찍고 튀어 올라도
        전량을 훨씬 좋은 가격에 받은 것으로 쳤다. 실측 gap 이 0.12~0.60R 였고
        **후보마다 4배 달라** 축 판정을 오염시켰다.

        ⚠️ **트리거 봉 하나만 본다.** 이후 봉이 더 내려와 나머지 레그를 채우는 경우
        평단이 개선되지만 그것을 반영하지 않는다 — 반영하면 보유 중에 1R 이 바뀌고,
        그것은 F2(레그별 시뮬레이션)의 일이다. 여기서 안 세는 쪽이 **보수적**이다.

        ⚠️ 중간 레그도 세지 않는다. 저가가 레그1과 평단 사이면 레그1·2 가 체결돼
        평단보다 좋은 값이 나오는데, 그때도 레그1 가격을 쓴다 — 같은 이유로 **낙관
        방향으로 틀리지 않는 것**을 택했다.
    """
    if not setup.entry_plan:
        raise ValueError(f"{setup.rule_version}: entry_plan 이 비었다 (spec §4.3.1 완결성)")
    if not 0 <= entry_index < len(candles):
        raise ValueError(f"entry_index 가 범위를 벗어났다: {entry_index} / {len(candles)}")
    reached = candles[entry_index].low <= setup.avg_entry
    return setup.avg_entry if reached else setup.entry_plan[0].price


def judge(
    candles: Sequence[Candle],
    setup: TradeSetup,
    entry_index: int,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    expiry_index: int | None = None,
    stop_override: Decimal | None = None,
    target_override: Decimal | None = None,
    entry_override: Decimal | None = None,
) -> Verdict:
    """진입 이후 이행 여부를 가린다 (spec §5.6.6 정의 그대로).

    Args:
        candles: `ts` 오름차순 캔들.
        setup: 셋업. `stop_loss` 와 `tp_ladder[0].price` 를 쓴다.
        entry_index: 진입 봉 번호. **이 봉부터** 판정에 포함한다.
        max_hold_bars: 판정 기한 (안정성 파라미터).
        expiry_index: **강제 청산 봉** 번호. 단타는 이 봉의 종가에 무조건 턴다
            (`expiry_for_day_trade`). None 이면 강제 청산 없이 기한까지 기다린다 —
            스윙·장투 버킷과 기존 코인 측정이 그 경로다.
        stop_override: **RiskManager 가 확정한** 손절가. 주면 셋업의 제안 대신 이 값을
            쓴다 (절대 규칙 #4). 셋업을 변조하지 않는 이유는 `stop_candidates` 불변식
            때문이다 — 탐지기가 내놓지 않은 값을 후보로 위장하면 안 된다.
        target_override: **RiskManager 가 확정한** 1차 익절가. 멀티 TF 합류 저항에서
            도출한 값이 여기로 들어온다 (P1 §1-0m 축 K). 손절과 **같은 이유로** 셋업을
            고치지 않고 인자로 받는다 — 익절도 §5.1 상 decision 계층의 확정값이다.
        entry_override: **실제 체결가** (`fill_price`). 주면 `avg_entry` 대신 이 값으로
            1R·손익·MFE/MAE 를 계산한다. 🔴 안 주면 계획 평단을 쓰는데, 그것이
            T19 의 낙관 편향이다 — 새 측정은 **반드시 넘긴다**.

    Returns:
        판정. 강제 청산 시점에 미결이면 `EXPIRED`, 캔들이 모자라면 `UNRESOLVED` 다.

    Raises:
        ValueError: `tp_ladder` 가 비었거나 `entry_index` 가 범위 밖인 경우.

    Note:
        **진입 봉을 포함**한다. 터치 진입은 봉 중간에 체결되므로 그 봉 안에서 손절이
        날 수도, 익절이 날 수도 있다. 진입 봉을 빼면 가장 빠른 손절을 놓친다.

        **같은 봉에 둘 다 닿으면 손절이 이긴다** (D1-4). 봉 안의 순서를 알 수 없을 때
        유리한 쪽을 고르면 측정이 낙관적으로 편향되고, 그것이 백테스트가 실거래보다
        좋게 나오는 대표 원인이다.
    """
    if not setup.tp_ladder:
        raise ValueError(f"{setup.rule_version}: tp_ladder 가 비었다 (spec §4.3.1 완결성)")
    if not 0 <= entry_index < len(candles):
        raise ValueError(f"entry_index 가 범위를 벗어났다: {entry_index} / {len(candles)}")

    # 🔴 셋업의 `stop_loss` 는 **제안**이고 확정은 RiskManager 다 (절대 규칙 #4).
    #    확정값을 셋업에 써넣으면 `stop_candidates` 불변식이 깨진다 — 탐지기가 내놓지
    #    않은 값이 후보인 척하게 되고, "어느 쪽이 진짜인지" 알 수 없어진다.
    stop = stop_override if stop_override is not None else setup.stop_loss
    target = target_override if target_override is not None else setup.tp_ladder[0].price
    # 🔴 체결가는 계획 평단이 아닐 수 있다 (F1 · `fill_price`). 1R·손익·MFE/MAE 가
    #    전부 이 값에서 나오므로 한 군데서 정하고 아래는 그것만 쓴다.
    filled = entry_override if entry_override is not None else setup.avg_entry
    risk = filled - stop
    if risk <= 0:
        raise ValueError(
            f"{setup.rule_version}: 체결가가 손절가 이하다 "
            f"({filled} <= {stop}) — 롱 온리 전제 위반 (절대 규칙 #10)"
        )
    if target <= filled:
        raise ValueError(
            f"{setup.rule_version}: 1차 익절 {target} 이 체결가 {filled} 이하다 "
            "— 진입 즉시 익절로 잡혀 이행률이 부풀려진다 (절대 규칙 #10)"
        )

    # 보유 기한은 둘 중 **먼저 오는 것**이다: 안정성 기한(봉 수) 또는 강제 청산 시점.
    # 단타에서 지배적인 것은 후자다 — 장 마감 전에 100봉을 채우는 일은 거의 없다.
    deadline = entry_index + max_hold_bars
    if expiry_index is not None:
        deadline = min(deadline, expiry_index)
    last = len(candles) - 1

    # 🔴 **진입 봉과 판정 봉을 뺀** 구간에서만 최대 이동을 잰다 (진단 전용).
    #    두 봉은 체결 시점이 봉 안 어디인지 알 수 없다 — 진입 봉의 고가는 진입 **전**에
    #    나왔을 수 있고, 손절 봉의 고가는 손절 **후**에 나왔을 수 있다. 포함하면 "손절
    #    전에 이만큼 이익이었다"가 부풀고, 그건 D1-4(같은 봉이면 손절이 이긴다)로 지켜
    #    온 보수성을 뒷문으로 무너뜨리는 것이다. 여기서 나온 값은 **과소평가**이므로
    #    "그래도 크다"는 결론은 안전한 방향으로만 틀린다.
    best_high: Decimal | None = None
    worst_low: Decimal | None = None

    def excursion() -> tuple[Decimal | None, Decimal | None]:
        """지금까지 쌓인 최고/최저를 R 단위로 환산한다.

        Returns:
            `(최대 유리 이동, 최대 불리 이동)` — 체결가 대비 거리를 리스크(1R)로 나눈 값.
            잰 봉이 없으면 `(None, None)`.
        """
        if best_high is None or worst_low is None:
            return None, None
        with fixed_context():
            return (best_high - filled) / risk, (worst_low - filled) / risk

    # 🔴 **반익반본** (§6.9) — 1차 익절 체결 시 그 비율만 청산하고 스탑을 본절로 올린다.
    #    남은 물량은 2차 목표까지 달리며, 그 뒤로 최악은 본전이다.
    #
    #    `banked` = 이미 확정된 R (수량 가중). `held` = 남은 수량 비율.
    #    스탑은 체결가로 올라가므로 그 뒤 손절은 -1R 이 아니라 **0R** 이다.
    ladder = _remaining_ladder(setup, target)
    banked = Decimal(0)
    held = Decimal(1)
    breakeven = False
    first_at: int | None = None
    step = 0

    for position in range(entry_index, min(deadline, last) + 1):
        candle = candles[position]
        # 같은 봉에 둘 다 닿으면 손절이 이긴다 (D1-4).
        if candle.low <= stop:
            mfe, mae = excursion()
            # 본절 상향 뒤에는 스탑에 닿아도 남은 물량이 0R 이다 — 이미 확정한
            # `banked` 는 그대로 남는다. 그것이 "반익반본"의 뜻이다.
            realized = banked + (Decimal(0) if breakeven else held * Decimal(-1))
            # 🔴 이행 판정의 기준은 **1차 익절가 도달**이지 본절 상향이 아니다
            #    (§5.6.6). `then` 이 없는 사다리에서도 1차를 먼저 찍었으면 이행이다.
            outcome = Outcome.NOT_FOLLOWED if first_at is None else Outcome.FOLLOWED
            return Verdict(outcome, position - entry_index, False, realized, mfe, mae)

        # 한 봉에서 사다리 여러 칸에 닿을 수 있다 — 순서대로 소화한다.
        while step < len(ladder) and candle.high >= ladder[step].price:
            rung = ladder[step]
            # 마지막 칸은 남은 물량 **전부**를 턴다. 비율 합이 1 이 아닐 수 있고
            # (탐지기마다 다르다), 남기면 손익이 조용히 새어나간다.
            portion = held if step == len(ladder) - 1 else min(rung.ratio, held)
            with fixed_context():
                banked += portion * (rung.price - filled) / risk
            held -= portion
            # 🔴 1차 도달은 **`then` 과 무관**하다 (§5.6.6). 본절 상향은 그 뒤에
            #    오는 별개의 조치이며, 둘을 묶으면 `then` 없는 사다리에서 이행이
            #    미이행으로 세어진다.
            if step == 0:
                first_at = position - entry_index
            if rung.then is TpFollowUpAction.MOVE_STOP_TO_BREAKEVEN:
                stop = filled
                breakeven = True
            step += 1

        if held <= 0:
            mfe, mae = excursion()
            return Verdict(Outcome.FOLLOWED, position - entry_index, False, banked, mfe, mae)

        if position > entry_index and position != expiry_index:
            best_high = candle.high if best_high is None else max(best_high, candle.high)
            worst_low = candle.low if worst_low is None else min(worst_low, candle.low)

    if expiry_index is not None and expiry_index <= last:
        # 🔴 만료 청산 — **그 봉의 종가**에 턴다. 고가/저가로 털었다고 하면 봉 안에서
        # 가장 유리한(또는 불리한) 지점을 고르는 것이고, 실제 마감 체결가는 종가다.
        #
        #    ⚠️ 1차를 이미 먹었으면 그 부분은 확정이고, **남은 물량만** 종가에 턴다.
        exit_candle = candles[expiry_index]
        with fixed_context():
            realized = banked + held * (exit_candle.close - filled) / risk
        mfe, mae = excursion()
        # 1차를 먹고 만료된 것은 **이행**이다 (§5.6.6 — 손절 전에 1차 익절가 도달).
        outcome = Outcome.EXPIRED if first_at is None else Outcome.FOLLOWED
        return Verdict(outcome, expiry_index - entry_index, False, realized, mfe, mae)

    # 🔴 1차를 먹었으면 §5.6.6 상 **이미 이행이다** — 남은 물량의 운명과 무관하다.
    #    반익반본 뒤로는 본절 스탑이 지키므로 최악이 0R 이고, 따라서 `banked` 는
    #    **보장된 하한**이다. 미결로 버리면 확정된 이익이 통째로 사라진다.
    #
    #    ⚠️ 하한이지 실현값이 아니다. 남은 절반이 2차까지 갔을 수도 있다 — 모르는 쪽을
    #    0 으로 두는 것이 낙관 방향으로 틀리지 않는 선택이다.
    if first_at is not None:
        mfe, mae = excursion()
        # 남은 물량의 **보장된 최악** — 본절 상향 뒤면 0R, 아니면 -1R. 손절 분기와
        # 같은 식이라 "어느 경로로 끝났나"에 따라 계산이 갈리지 않는다.
        floor = banked + (Decimal(0) if breakeven else held * Decimal(-1))
        return Verdict(
            Outcome.FOLLOWED,
            first_at,
            truncated=deadline > last,
            realized_r=floor,
            mfe_r=mfe,
            mae_r=mae,
        )
    return Verdict(Outcome.UNRESOLVED, None, truncated=deadline > last)


@dataclass(frozen=True, slots=True)
class FollowThroughRecord:
    """측정된 셋업 하나 — 리포트의 원자료.

    Attributes:
        rule_version: `rule_id@version`. 성과 귀속 단위다 (spec §4.3.1).
        detected_index: 셋업이 확정된 봉 번호.
        entry_index: 진입 봉 번호.
        entry_price: 진입 트리거 레벨.
        avg_entry: 계획 평단 — 리스크 기준이다 (spec §4.6, §6.9).
        stop_loss: 손절가.
        first_tp: 1차 익절가.
        rr_ratio: 셋업이 제시한 손익비.
        verdict: 판정.
    """

    rule_version: str
    detected_index: int
    entry_index: int
    entry_price: Decimal
    avg_entry: Decimal
    stop_loss: Decimal
    first_tp: Decimal
    rr_ratio: Decimal
    verdict: Verdict

    @property
    def risk_pct(self) -> Decimal:
        """1R 의 크기 — 계획 평단 대비 손절 거리 비율.

        Returns:
            `(avg_entry - stop_loss) / avg_entry`.

        Note:
            이행률만으로는 룰의 쓸모를 알 수 없다. **1R 이 얼마나 작은지**가 함께 필요한
            이유는 비용이 R 단위로 환산되기 때문이다 — 왕복 비용이 0.3%인데 1R 이
            0.1%면 거래마다 3R 을 비용으로 내는 셈이고, 그러면 이행률이 아무리 좋아도
            성과는 음수다 (D1-6 비용 테이블 · P1-8-4).

            분모가 계획 평단인 것은 §4.6·§6.9 규정이다. 레그별로 계산하면 3분할 리스크가
            과소평가된다.
        """
        with fixed_context():
            return (self.avg_entry - self.stop_loss) / self.avg_entry


@dataclass(frozen=True, slots=True)
class FollowThroughStats:
    """이행률 집계 (spec §5.6.6).

    Attributes:
        followed: 이행 건수.
        not_followed: 미이행 건수.
        unresolved: 미결 건수 (기한 내 둘 다 미도달).
        truncated: 미결 중 **데이터 부족**으로 끝난 건수.
        no_entry: 탐지됐으나 진입 조건이 끝내 충족되지 않은 건수.

    Note:
        `no_entry` 를 세는 이유: 진입 없는 탐지는 이행률 분모에 들어가지 않지만
        **룰의 쓸모를 말해준다**. 탐지 1000건 중 진입 5건이면 그 룰은 사실상 작동하지
        않는 것이고, 이행률만 보면 그 사실이 보이지 않는다.
    """

    followed: int
    not_followed: int
    unresolved: int
    truncated: int
    no_entry: int

    @property
    def decided(self) -> int:
        """판정된 표본 수 — 이행률의 분모다. 미결·미진입은 제외한다 (§5.6.6)."""
        return self.followed + self.not_followed

    @property
    def entered(self) -> int:
        """진입이 발생한 셋업 수."""
        return self.decided + self.unresolved

    @property
    def rate(self) -> Decimal | None:
        """이행률. 판정 표본이 0 이면 **None** 이다.

        Note:
            0 건일 때 `0.0` 을 돌려주면 "이행률 0%"로 읽혀 룰이 나쁜 것처럼 보인다.
            "잴 수 없었다"와 "재보니 나빴다"는 다른 사실이다 (절대 규칙 #8).
        """
        if self.decided == 0:
            return None
        with fixed_context():
            return Decimal(self.followed) / Decimal(self.decided)

    @property
    def sample_sufficient(self) -> bool:
        """판정에 쓸 만큼 표본이 모였는가 (spec §12.9 · 30건)."""
        return self.decided >= MIN_SAMPLE


def summarize(records: Sequence[FollowThroughRecord], no_entry: int = 0) -> FollowThroughStats:
    """기록을 집계한다.

    Args:
        records: 진입이 발생한 셋업들의 판정 기록.
        no_entry: 진입 조건이 끝내 충족되지 않은 셋업 수.

    Returns:
        집계.
    """
    outcomes = [record.verdict.outcome for record in records]
    return FollowThroughStats(
        followed=outcomes.count(Outcome.FOLLOWED),
        not_followed=outcomes.count(Outcome.NOT_FOLLOWED),
        unresolved=outcomes.count(Outcome.UNRESOLVED),
        truncated=sum(1 for record in records if record.verdict.truncated),
        no_entry=no_entry,
    )
