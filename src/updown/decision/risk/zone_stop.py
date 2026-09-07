"""손절·익절을 **합류대에서 도출**한다 — 축 K (spec §5.1 · 절대 규칙 #4 · P1 §1-0m).

## 손절폭은 결과이지 입력이 아니다

`stop_resolver.StructuralAtrStopResolver` 는 `stop = entry - max(구조적, kxATR)` 이라는
**공식**이다. 공식은 손절을 *얼마나 멀리* 둘지 답하지만, 실제 문제는 *어디에* 둘지다.

축 G-k 실측(005930 15m)이 그 한계를 보였다:

| k | 승률 | MDD | 총수익률 |
|---|---|---|---|
| 1.5 | 50% | — | 5.45% |
| 2.0 | 50% | — | 3.04% |
| 2.5 | 50% | 2.2% | 1.62% |

**승률은 40 → 50% 에서 멈췄고 수익률만 단조 감소했다.** 손절을 넓히면 노이즈 손절은
줄지만 1R 이 커져 같은 이동이 더 작은 R 이 된다 — 순수한 트레이드오프다. 거리를 조절해서는
이 곡선을 벗어날 수 없다.

⇒ 손절을 **지지가 있는 자리 아래**로 옮긴다. 그러면 손절폭은 배치의 **결과**가 된다:
지지가 두껍게 겹치면 가까워도 안전하고, 아무것도 없으면 멀리 둔다.

## 새 자유 파라미터를 만들지 않는다

여유(buffer)는 이미 있는 `ZONE_ATR_MULTIPLE`(0.5xATR)이다 — 그 띠를 "닿았다"고 판정할
때 쓰는 오차와 **같은 값**이어야 일관된다. 새 상수 0개 (§5.6.7).

## 폴백을 **숨기지 않는다**

합류 지지대가 없으면 기존 resolver 로 넘어가되 그 사실을 `Resolution.fell_back` 에
남긴다. 조용히 넘어가면 "축 K 가 효과 없다"는 결론이 사실은 "대부분 폴백이었다"일 수
있고, 그 둘은 완전히 다른 답이다 (절대 규칙 #8).

## ⛔ 여기서 정하지 않는 것

- **호가 라운딩** — 브로커별이라 어댑터 책임이다 (§4.2, §12.2). 라운딩 뒤 RR 재검증도 그쪽
- **손절 상향/트레일링** — 절대 규칙 #3. 이 함수는 진입 시점 확정만 한다
- **투영 대상** — 수평 띠만 쓰는 것은 `zone_map` 의 전제이며 여기서 흔들지 않는다
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.analysis.structures.zone_map import (
    ConfluentZone,
    resistance_above,
    support_below,
)
from updown.common.domain.setup import TradeSetup
from updown.common.numeric import fixed_context
from updown.decision.risk.stop_resolver import (
    StopBasis,
    StopResolutionError,
    StructuralAtrStopResolver,
)


class ZoneStopChoice(StrEnum):
    """합류대 선택 해석 — 축 K 후보 3개 (`docs/rules/rule_candidates.md`).

    Attributes:
        NEAREST_WITH_BUFFER: **K1** — 가장 가까운 지지대 하단에서 `0.5xATR` 아래.
            잠정 기본값이다.
        NEAREST_TIGHT: ⛔ **K2 — 퇴역.** 최소 손절폭이 들어온 뒤 K1 과 **같은 답**을
            낸다 (둘 다 하한까지 넓혀지므로 `0.5xATR` 차이가 사라진다). 성과로 진 것이
            아니라 **코드 변경으로 축이 사라진 것**이라, 후보에서 빼되 값은 남긴다 —
            지운 값으로 잰 과거 리포트를 읽을 수 없게 되기 때문이다.
        DEEPEST: **K3** — 가장 **깊은**(최다 TF) 지지대 아래. 거리를 무시하고 깊이만 본다.
        NEAREST_CONFLUENT: **K4** — **깊이 2 이상**인 지지대 중 가장 가까운 것.
            축 M2 와 **대칭**이며, K2 가 비운 자리에 들어간다.

    Note:
        셋 다 "어디에 두는가"의 **정의**로 답하며 새 자유 파라미터가 없다. 승자는
        out-of-sample 이 정하고(절대 규칙 #12), 표본 30건 미달이면 기존 정의를 유지한다.

        ⚠️ **K3 은 "깊을수록 좋다"를 가정하지 않는다 — 시험한다.** 그 명제가 참이면
        K3 이 이기고, 거짓이면 가까운 지지가 이긴다. 어느 쪽이든 답이 나온다.
    """

    NEAREST_WITH_BUFFER = "nearest_with_buffer"
    NEAREST_TIGHT = "nearest_tight"
    DEEPEST = "deepest"
    NEAREST_CONFLUENT = "nearest_confluent"


class ZoneTargetChoice(StrEnum):
    """익절 목표 해석 — 축 M (`docs/rules/rule_candidates.md`).

    Attributes:
        NEAREST: **M1** — 가장 가까운 합류 저항대 하단 - 여유. 잠정 기본값.
        CONFLUENT: **M2** — **깊이 2 이상**인 저항대 중 가장 가까운 것. 얕은 띠는
            건너뛴다.
        MEASURED: **M3** — 구조물을 무시하고 셋업의 계측 목표(`tp_ladder[0]`)를 쓴다.

    Note:
        ## 왜 이 축이 필요한가 — RR 이 구조적으로 눌린다

        멀티 TF 띠가 창당 31~48개, 합류대가 36~66개다 (실측). 그만큼 촘촘하면 **진입가
        바로 위에도 거의 항상 저항이 있고**, 가장 가까운 것을 목표로 잡으면 계획 RR 이
        0 근처까지 내려간다 — 실측에서 `order_block` 은 RR 0.02 짜리 거래가 익절로
        세어졌고, 돌파 재시험 셋업은 99건 탐지 중 **56건이 RR 미달로 기각**됐다.

        ## M2 는 손절 쪽 논리와 **대칭**이다

        손절을 "중첩된 지지 아래"에 두면서 익절은 "아무 저항 앞"에 두는 것은 비대칭이다.
        근거의 두께가 손절 위치를 정한다면 익절 위치도 같은 잣대를 받아야 한다.

        ## M3 이 대조군이다

        구조물 목표가 계측 목표(박스 높이·급등 고점)보다 나은지를 확인하려면 구조물을
        **끈** 팔이 있어야 한다. 셋이 다 구조물 기반이면 "구조물 목표가 옳은가"에
        답할 수 없다.

        ⛔ 셋 다 정의로 답하며 **새 자유 파라미터가 0개**다. 깊이 2 는 "중첩"의 정의
        그 자체이지 조정 값이 아니다 (`ConfluentZone.depth` — 서로 다른 TF 둘 이상).
    """

    NEAREST = "nearest"
    CONFLUENT = "confluent"
    MEASURED = "measured"


class TightStopPolicy(StrEnum):
    """구조 손절이 **최소 손절폭보다 좁을 때** 무엇을 하는가 — 축 P.

    ## 사용자 지적이 비대칭을 잡았다 (2026-08-09)

    > *"왜 하한가를 계속 고정된 파라미터로 하려 하지? 구체적인 전저점에서 추세를 보고
    > **아래 추세선 중 적합한 곳**에 하한가를 찍어야 하는 거 아닌가?"*

    맞다. 익절 쪽은 이미 이렇게 돼 있다:

    > *"익절은 구조가 정하게 두고, 손익비가 안 나오면 목표를 미는 것이 아니라 **진입을
    > 포기한다.** 억지로 민 목표는 근거 없는 숫자다."*

    **그런데 손절은 억지로 밀고 있었다.** 구조 손절이 하한보다 좁으면 하한까지 넓혀
    그대로 진입한다. 그 손절가(`진입 - 2x비용`)는 **시장이 지키는 자리가 아니다** —
    가격 구조와 아무 관계가 없는 숫자다.

    실측(v2)에서 그렇게 만들어진 손절이 **74~94%** 였다. 즉 대부분의 거래에서 손절이
    축 K 가 고른 자리가 아니었고, 그 상태로 "어느 손절 위치가 나은가"를 재고 있었다.

    ## 후보 셋 — 전부 새 파라미터 0개

    | | 구조 손절이 하한보다 좁을 때 | 손절의 근거 |
    |---|---|---|
    | **P1** | 하한까지 **넓힌다** (현행) | 🔴 없음 |
    | **P2** | **진입을 포기**한다 | — (익절 쪽과 대칭) |
    | **P3** | **하한을 만족하는 다음 지지**를 찾는다 | ✅ 구조 (더 아래 전저점) |

    P3 이 사용자가 말한 것이다. 지금 코드는 지지대를 **하나만** 고르고(축 K 규칙) 그것이
    좁으면 하한으로 때운다 — **더 아래 전저점을 보지 않는다.**

    ## P3 은 축 K 를 대체하지 않는다

    P3 은 후보 목록을 **거른 뒤** 축 K 규칙을 적용한다. K1(가장 가까운)이면 "하한을
    만족하는 것 중 가장 가까운", K4(깊이 2+)면 "하한을 만족하면서 깊이 2+ 인 것 중
    가장 가까운" 이다. 두 축이 직교한다 (§5.6.7 한 번에 한 축).

    끝까지 만족하는 지지가 없으면 **포기한다** (P2 와 같아진다) — 거기서 넓히면 다시
    근거 없는 숫자가 된다.

    **반증 조건 (측정 전 선언)**: 표본 30건 이상에서 P3 의 기대 R 이 P1 보다 높지 않고
    P2 보다도 낮으면, "구조적 손절 자리를 더 찾는 것"이 답이 아니라는 뜻이다. 그때는
    그 시장·시간축에서 **비용을 갚는 구조가 없다**고 결론짓는다 (5m 폐기와 같은 처리).
    """

    WIDEN = "widen"
    """P1 — 하한까지 넓히고 진입한다. **현행**이며 손절에 구조적 근거가 없다."""

    SKIP = "skip"
    """P2 — 진입을 포기한다. 익절 쪽 처리와 **대칭**이다."""

    NEXT_SUPPORT = "next_support"
    """P3 — 하한을 만족하는 **다음 지지**를 찾는다. 없으면 포기한다."""


class TooTightError(StopResolutionError):
    """구조 손절이 최소 손절폭보다 좁아 진입을 포기한다 (축 P 의 P2·P3).

    Note:
        `StopResolutionError` 를 상속하는 이유는 호출부가 이미 그것을 "이 셋업은
        건너뛴다"로 처리하고 있기 때문이다. 다만 **사유가 다르므로** 별도 타입으로
        둔다 — 원래 것은 "손절이 진입가 위"(롱 온리 위반)이고 이것은 "너무 좁다"이며,
        섞으면 깔때기에서 둘이 같은 칸으로 세어진다 (절대 규칙 #8).
    """


class StopFloorBasis(StrEnum):
    """최소 손절폭 하한 중 **어느 쪽이 물었는가** (P1 §1-0t T9).

    ## 왜 갈라야 하는가 — 처방이 정반대다

    하한은 `max(1xATR, 2x왕복비용)` 두 개다. 실측(v2 회차)에서 하한이 손절을 **74~94%**
    덮어썼는데, 둘을 참/거짓 하나로 세고 있어 **어느 쪽인지 알 수 없었다.** 그런데 둘은
    완전히 다른 사실을 말한다:

    | 물은 하한 | 뜻 | 해야 할 일 |
    |---|---|---|
    | **ATR** | 구조가 **봉 하나보다 촘촘하다** | 시간축을 올리거나 더 큰 구조물을 쓴다 |
    | **비용** | 구조가 **비용을 못 갚는다** | 그 시장·시간축은 **산술적으로 불가능**하다 |

    전자는 설정을 바꾸면 풀리고, 후자는 **어떤 승률로도 풀리지 않는다.** 뭉쳐 세면
    "하한이 자주 걸린다"까지만 알고 어느 쪽 문제인지 모른 채로 남는다 (절대 규칙 #8).

    ## 둘 다 넘을 때는 **더 큰 쪽**이 물은 것이다

    `max()` 가 고른 쪽이 실제로 손절 위치를 정했다. 작은 쪽은 어차피 만족되므로
    "둘 다"라는 칸을 두지 않는다 — 그 칸은 원인을 다시 흐린다.
    """

    STRUCTURE = "structure"
    """하한이 물지 않았다 — 손절은 축 K 가 고른 자리다. **이것만 축 K 를 잰 것이다.**"""

    ATR = "atr"
    """`1xATR` 하한이 물었다 — 구조가 봉 하나보다 촘촘했다."""

    COST = "cost"
    """`2x왕복비용` 하한이 물었다 — 구조가 비용을 못 갚았다."""


@dataclass(frozen=True, slots=True)
class Resolution:
    """손절·익절 확정 결과와 **그 근거**.

    Attributes:
        stop: 확정 손절가.
        target: 확정 1차 익절가. 저항 합류대가 없으면 셋업 제안을 그대로 쓴다.
        support: 손절 근거가 된 합류대. 폴백이면 None.
        resistance: 익절 근거가 된 합류대. 없으면 None.
        fell_back: 합류 지지대가 없어 ATR 공식으로 넘어갔는가.
        final_target: **최종 익절선** — 진입 판단에 쓴 창에서 가장 높은 저항대의 상단.
            1차 익절(`target`)이 "먼저 만나는 벽"이라면 이것은 "그 구간에서 갈 수 있는
            끝"이다. 저항대가 없으면 None.
        floor_basis: 최소 손절폭 하한 중 **어느 쪽이 손절을 정했는가**.
            `STRUCTURE` 면 하한이 물지 않은 것이고, 그때만 손절이 축 K 가 고른 자리다.
        widened_target: 최소 익절폭 하한이 실제로 목표를 밀었는가.

    Note:
        하한 개입을 남기는 이유는 **얼마나 자주 개입했는지**가 곧 원래 값이 얼마나
        나빴는지이기 때문이다. 조용히 고치면 "고칠 필요가 있었나"에 답할 수 없다
        (절대 규칙 #8).

        🔴 참/거짓이 아니라 **어느 하한인지**를 든다 (§1-0t T9). v2 회차에서 하한률이
        74~94% 였는데 참/거짓만으로는 "시간축을 올리면 되는 문제"와 "산술적으로 불가능한
        문제"가 구분되지 않았다.

    Note:
        `depth`·`rr` 을 **여기서 계산해 노출**한다. 호출부가 각자 계산하면 리포트와
        측정이 다른 값을 쓸 수 있고, 그러면 "깊이 3 의 승률"이 무엇을 센 것인지
        알 수 없어진다.
    """

    stop: Decimal
    target: Decimal
    support: ConfluentZone | None
    resistance: ConfluentZone | None
    fell_back: bool
    final_target: Decimal | None = None
    floor_basis: StopFloorBasis = StopFloorBasis.STRUCTURE
    widened_target: bool = False

    @property
    def widened_stop(self) -> bool:
        """하한이 손절을 넓혔는가 — **어느 하한인지는 `floor_basis` 가 든다**.

        Note:
            참이면 이 거래의 손절은 축 K 가 고른 자리가 **아니다**. 깊이 표를 축 K 로
            읽으려면 이 비율이 낮아야 한다 (`depth.floor_rate`).
        """
        return self.floor_basis is not StopFloorBasis.STRUCTURE

    @property
    def depth(self) -> int:
        """손절 근거의 중첩 깊이. 폴백이면 **0** — "근거 없음"과 "깊이 1"은 다르다."""
        return 0 if self.support is None else self.support.depth

    def rr(self, entry: Decimal) -> Decimal | None:
        """손익비 — **결과값**이다.

        Args:
            entry: 계획 평단.

        Returns:
            `(익절 - 진입) / (진입 - 손절)`. 손절폭이 0 이하면 None.

        Note:
            RR 을 입력으로 두지 않는 것이 이 모듈의 요점이다. 배치가 정하고 우리는
            읽는다 — 원하는 RR 을 먼저 정하면 손절·익절이 그 숫자를 맞추려고 움직인다.
        """
        with fixed_context():
            risk = entry - self.stop
            if risk <= 0:
                return None
            return (self.target - entry) / risk

    def ladder_rr(self, entry: Decimal, first_ratio: Decimal) -> Decimal | None:
        """**사다리 전체**의 손익비 — 1차와 최종을 비중으로 가중한다 (spec §6.9).

        Args:
            entry: 계획 평단.
            first_ratio: 1차 익절에서 청산할 비중 (`tp_ladder[0].ratio`).

        Returns:
            가중 RR. 손절폭이 0 이하면 None. 최종 목표가 없으면 `rr()` 과 같다.

        Note:
            🔴 **`rr()` 로 게이트를 걸면 안 된다.** §6.9 반익반본은 1차에서 **절반만**
            던지고 나머지는 최종까지 가져가는 계획인데, `rr()` 은 1차 목표 하나로만
            판정한다. 그러면 "1차가 가깝지만 최종이 먼" 계획이 통째로 기각된다.

            실측이 그 결과를 보였다 — 최소 손절폭을 넣자 005930 15m 눌림목이
            **진입 11 → 0** 이 됐다. 비용 0.394% x2 = 손절 0.788% 에 `min_rr` 1.75 면
            1차 목표만으로 1.38% 를 요구하는데, 15m 오더블록의 가장 가까운 저항은
            대개 그보다 가깝다.

            비중을 인자로 받는 이유는 셋업마다 사다리가 다를 수 있어서다. 값을 여기
            박으면 `FIRST_TP_RATIO` 가 두 곳에 생기고 언젠가 갈라진다.
        """
        with fixed_context():
            risk = entry - self.stop
            if risk <= 0:
                return None
            if self.final_target is None:
                return (self.target - entry) / risk
            gain = first_ratio * (self.target - entry) + (Decimal(1) - first_ratio) * (
                self.final_target - entry
            )
            return gain / risk


COST_COVER_MULTIPLE = Decimal(2)
"""손절폭이 왕복 비용의 **몇 배** 이상이어야 하는가 — 최소 손절폭의 근거 ②.

**조정 값이 아니라 손익분기 산수의 경계다.** 비용/1R = 0.5 이면 RR 2 에서 필요 승률이

    P > (1 + c) / (1 + RR) = 1.5 / 3 = 50%

정확히 **동전 던지기**다. 그보다 비싼 자리는 동전보다 잘 맞혀야 본전이므로, 그 지점을
하한으로 삼는다. 5m 을 폐기시킨 것과 **같은 산수**이며(§1-0i 필요 승률표) 여기서는
종목·자리별로 적용한다.

실측이 이 하한을 요구했다 (2026-08-08, 매트릭스 126건):

| 종목 | 손절폭 중앙 | 왕복 비용 | 비용/1R |
|---|---|---|---|
| MSFT | 0.299% | 0.414% | **1.39R** 🔴 |
| AAPL | 0.399% | 0.414% | **1.04R** 🔴 |
| 005930 | 0.671% | 0.394% | 0.59R |
| BTC | 0.452% | 0.157% | 0.35R |

## ⚠️ 이 값은 **생존선이지 수익선이 아니다** (축 T 신설 근거)

배수 2 는 "동전보다 나은가"를 묻는다. 통과해도 **비용이 기대값의 절반**을 가져간다 —
v8 실측에서 눌림목 BTC 는 총기대 0.575R 중 0.382R(66%)을 비용으로 냈다.

사용자 매매 룰북(`docs/planning/invest_trade_my_rule.md` STEP 8-3)은 손절폭 정상
범위를 **1.5~2.5%** 로 못박는다. 업비트 왕복 0.157% 기준으로 **배수 9.5~16** 이다.
즉 우리 하한은 사람이 실제로 쓰는 기준의 **1/5 수준**이었다.

배수와 비용 부담은 역수 관계이므로 후보를 산수로 선언할 수 있다:

| 배수 | 비용/1R | RR 2 에서 필요 승률 | 성격 |
|---|---|---|---|
| **2** | 0.50R | 50.0% | 현행 — 동전 던지기 경계 |
| **5** | 0.20R | 40.0% | 중간 |
| **10** | 0.10R | 36.7% | 룰북 1.5% 에 대응 |

⛔ 성과를 보고 고르지 않는다. 셋 다 **측정 전에 산수로 도출**했고 승자는 표본 30건
(§12.9)을 채운 뒤 out-of-sample 이 정한다 (절대 규칙 #12 · `docs/rules/rule_candidates.md` 축 T).
"""

MIN_FIRST_RR = Decimal(1)
"""1차 익절이 넘어야 하는 최소 손익비.

**손익비 1 미만이면 맞혀도 리스크보다 적게 번다** — 사용자 지적이고 산수 그대로다.
1.0 은 조정 값이 아니라 **손익 대칭점**이며, 그 아래는 이기고도 기대값이 음수가 되는
영역이다 (비용이 붙으면 더).

⚠️ 사다리 RR(`ladder_rr`) 만으로는 이걸 막지 못한다. 최종 익절선이 멀면 가중 평균이
`min_rr` 을 넘어 통과하는데, **1차에서 절반을 던지므로** 그 절반은 1R 로 끝난다.
실측에서 계획 사다리 RR 1.75+ 인 거래의 실현 R 이 **전부 정확히 +1.00** 이었던 것이
그 형태다 — 최종까지 간 거래가 하나도 없었다.

⇒ 두 조건을 **모두** 본다: 1차 RR >= 1.0 **그리고** 사다리 RR >= min_rr.
"""

MIN_ATR_MULTIPLE = Decimal(1)
"""손절폭이 진입 봉 ATR 의 **몇 배** 이상이어야 하는가 — 최소 손절폭의 근거 ①.

**§1-0l 실측의 직접 결과다.** 005930 15m 에서 손절폭 0.353% 대 진입 봉 고저폭 1.157%
= **3.28배**였고, 손절 33% 가 진입 봉 안에서 났다. 손절이 봉 하나보다 좁으면 방향을
맞혀도 노이즈에 털린다.

1.0 인 이유: **봉 하나가 경계**다. 그보다 크게 잡는 것(1.5·2.0·2.5)은 축 G-k 가 이미
흔들어 본 값이고 여기는 **하한**이지 선택이 아니다.
"""

MIN_CONFLUENT_DEPTH = 2
"""M2 가 목표로 인정하는 최소 중첩 깊이.

**조정 값이 아니라 정의다** — `ConfluentZone.depth` 는 서로 다른 출처 TF 수이므로
2 가 곧 "중첩됐다"의 최소 형태다. 1 은 단일 TF 띠라 중첩이 아니고(§5.5), 3 이상을
요구하면 그것은 근거 없는 새 자유도다 (축 J1 이 탈락한 이유와 같다).
"""


def _pick_target(bands: list[ConfluentZone], choice: ZoneTargetChoice) -> ConfluentZone | None:
    """축 M 해석에 맞는 저항 합류대를 고른다.

    Args:
        bands: `resistance_above()` 결과 — 가까운 순으로 정렬돼 있다.
        choice: 축 M 해석.

    Returns:
        고른 합류대. 후보가 없거나 M3(계측 목표)면 None.

    Note:
        M2 에서 깊이 조건을 만족하는 띠가 없으면 **None 을 돌려 계측 목표로 넘어간다.**
        조건을 낮춰 가장 가까운 것을 쓰면 M1 과 M2 가 같은 답을 내는 구간이 생겨
        축이 흐려진다 (§5.6.7 후보는 서로 달라야 한다).
    """
    if choice is ZoneTargetChoice.MEASURED or not bands:
        return None
    if choice is ZoneTargetChoice.CONFLUENT:
        return next((band for band in bands if band.depth >= MIN_CONFLUENT_DEPTH), None)
    return bands[0]


def _pick_support(bands: list[ConfluentZone], choice: ZoneStopChoice) -> ConfluentZone | None:
    """축 K 해석에 맞는 지지 합류대를 고른다.

    Args:
        bands: `support_below()` 결과 — 가까운 순으로 정렬돼 있다.
        choice: 축 K 해석.

    Returns:
        고른 합류대. 후보가 없으면 None.

    Note:
        K3 의 동점 처리는 **가장 가까운 쪽**이다. 깊이가 같으면 K1·K2 와 같은 답이
        되므로, 축이 깊이 하나만 흔들게 된다 (§5.6.7 "한 번에 한 축").
    """
    if not bands:
        return None
    if choice is ZoneStopChoice.DEEPEST:
        # `bands` 가 이미 가까운 순이므로 `max` 의 안정 정렬이 동점을 가까운 쪽으로 푼다.
        return max(bands, key=lambda band: band.depth)
    if choice is ZoneStopChoice.NEAREST_CONFLUENT:
        # 🔴 **K4** — 얕은 띠를 건너뛴다. 띠가 창당 36~66개라 진입가 **바로 아래**에
        #    거의 항상 무언가 있고, 그것을 손절로 잡으면 1R 이 좁아 비용이 엣지를
        #    먹는다 (실측: MSFT 비용 1.39R). 축 M2 와 같은 논리의 손절판이다.
        #
        #    깊이 조건을 만족하는 띠가 없으면 **None** 을 돌려 폴백으로 보낸다 —
        #    조건을 낮춰 가까운 것을 쓰면 K1 과 같은 답이 되어 축이 흐려진다.
        return next((band for band in bands if band.depth >= MIN_CONFLUENT_DEPTH), None)
    return bands[0]


class ConfluenceStopResolver:
    """합류대에서 손절·익절을 도출한다 — 축 K 구현.

    Attributes:
        choice: 축 K 해석.
        fallback: 합류 지지대가 없을 때 쓸 공식 resolver.
        fallback_k: 폴백의 ATR 배수. **고정값이다** — 축 K 측정 중 k 를 함께 흔들면
            어느 축의 효과인지 구분되지 않는다 (§5.6.7).

    Note:
        `stop_resolver.StopResolver` 프로토콜을 **구현하지 않는다.** 그 계약은
        `(setup, atr, k, basis) -> Decimal` 인데 여기는 합류대가 필요하고 익절까지
        돌려주기 때문이다. 억지로 맞추면 합류대를 전역 상태로 넘겨야 하고, 그러면
        순수성(절대 규칙 #5)이 깨진다.
    """

    def __init__(
        self,
        choice: ZoneStopChoice = ZoneStopChoice.NEAREST_WITH_BUFFER,
        fallback_k: Decimal = Decimal("2.0"),
        target: ZoneTargetChoice = ZoneTargetChoice.NEAREST,
        tight: TightStopPolicy = TightStopPolicy.NEXT_SUPPORT,
    ) -> None:
        """해석을 고정한 resolver 를 만든다.

        Args:
            choice: 축 K 해석 (손절 위치).
            fallback_k: 폴백 ATR 배수.
            target: 축 M 해석 (익절 목표).
            tight: 축 P 해석 (구조 손절이 하한보다 좁을 때).

        Note:
            축을 **동시에 흔들지 않는다** (§5.6.7). 하나를 재는 동안 나머지는 기본값으로
            고정하며, 그 사실이 리포트의 프리셋 이름에 남아야 한다.

            🔴 **기본값이 `NEXT_SUPPORT`(P3) 로 바뀌었다** (2026-08-09, v3 판정).
            근거는 **성과가 아니다** — 성과는 무승부였다 (돌파 P1 +0.878R vs P3 +0.722R,
            n≈45 에 표준오차 약 0.15R 이라 1 SE 차이). 반증 조건은 발동하지 않았다.

            바꾼 이유는 셋의 조합이다:
              ① 성과로 안 갈린다
              ② **P1 은 축 K 를 못 재게 한다** — 눌림목에서 손절의 **96.9%** 가 하한이었다
              ③ P1 의 손절(`진입 - max(1xATR, 2x비용)`)은 **시장이 지키는 자리가 아니다**

            ⚠️ 나중에 "P3 가 성과로 이겼다"로 인용하면 안 된다. 성과 판정은
            out-of-sample 에서 한다 (`docs/rules/rule_candidates.md` 축 P).
        """
        self.choice = choice
        self.fallback = StructuralAtrStopResolver()
        self.fallback_k = fallback_k
        self.target = target
        self.tight = tight

    def _stop_from(self, band: ConfluentZone, margin: Decimal) -> Decimal:
        """지지 합류대 하나에서 손절가를 만든다.

        Args:
            band: 지지 합류대.
            margin: 여유 (`ZONE_ATR_MULTIPLE x ATR`).

        Returns:
            손절가.

        Note:
            🔴 **한 곳에서만 정의한다.** 축 P3 은 후보를 거를 때 이 값이 필요하고
            확정 단계도 같은 값을 써야 한다 — 두 곳에서 따로 계산하면 "걸러 놓고 다른
            값으로 확정하는" 어긋남이 조용히 생긴다.
        """
        return band.low if self.choice is ZoneStopChoice.NEAREST_TIGHT else band.low - margin

    def resolve(
        self,
        setup: TradeSetup,
        bands: list[ConfluentZone],
        atr: Decimal,
        margin: Decimal,
        round_trip_pct: Decimal = Decimal(0),
        cost_cover: Decimal = COST_COVER_MULTIPLE,
    ) -> Resolution:
        """진입 시점 손절·익절을 확정한다 (spec §5.1 — 손절의 SSoT).

        Args:
            setup: 탐지가 낸 구조 관측. `avg_entry` 가 기준점이다.
            bands: 이 시점의 멀티 TF 합류대 (`zone_map.merge()` 결과).
            atr: 진입 봉의 ATR. 폴백 경로와 **최소 손절폭**에 쓴다. **양수여야 한다.**
            margin: 여유 (`0.5xATR`). K1 이 지지대 하단에서 이만큼 더 내린다.
            round_trip_pct: 이 시장의 왕복 비용 비율. **최소 손절폭의 두 번째 근거**다
                (`COST_COVER_MULTIPLE`). 0 이면 비용 하한을 걸지 않는다 — 비용을
                모르면서 아는 척하지 않는다 (절대 규칙 #8).
            cost_cover: 손절폭이 왕복 비용의 몇 배 이상이어야 하는가 (**축 T**).
                기본값은 생존선(2배)이며, 측정 스크립트가 후보를 명시로 넘긴다 —
                기본값을 바꿔 승자를 굳히지 않는다 (절대 규칙 #12).

        Returns:
            확정 결과. 근거 합류대와 폴백 여부가 함께 담긴다.

        Raises:
            StopResolutionError: 확정 손절이 계획 평단 이상인 경우 — 롱 온리 전제
                위반이다 (절대 규칙 #10). 폴백도 실패하면 그 예외가 그대로 올라온다.
            ValueError: `atr <= 0`.

        Note:
            **순수 함수다** (절대 규칙 #5) — I/O·현재시각·전역 설정 참조가 없다.
            같은 인자면 언제 불러도 같은 값이며, 그래야 §4.14 성과 귀속이 성립한다.

            익절은 저항 합류대의 **하단 - 여유**다. 닿는 것을 목표로 하면 그 레벨에서
            반사되는 물량에 매번 진다 — 저항은 도달점이 아니라 **나오는 지점**이다.
        """
        if atr <= 0:
            raise ValueError(f"ATR 은 0 보다 커야 한다: {atr} — 폴백 손절폭의 재료다")
        if cost_cover <= 0:
            raise ValueError(f"cost_cover 는 0 보다 커야 한다: {cost_cover}")

        entry = setup.avg_entry
        above = resistance_above(entry, bands)
        # 🔴 하한을 **후보를 고르기 전에** 계산한다 — 축 P3 이 이 값으로 후보를 거른다.
        atr_floor = MIN_ATR_MULTIPLE * atr
        cost_floor = cost_cover * round_trip_pct * entry
        floor_price = entry - max(atr_floor, cost_floor)

        below = support_below(entry, bands)
        if self.tight is TightStopPolicy.NEXT_SUPPORT and below:
            # 🔴 **축 P3** — "가장 가까운 지지가 너무 좁으면 하한으로 때운다"가 아니라
            #    **더 아래 전저점을 본다** (사용자 지적). 거른 뒤 축 K 규칙을 적용하므로
            #    두 축이 직교한다: K1 이면 "하한을 만족하는 것 중 가장 가까운" 이 된다.
            wide_enough = [band for band in below if self._stop_from(band, margin) <= floor_price]
            if not wide_enough:
                # 🔴 여기서 넓히면 다시 근거 없는 숫자가 된다 — 포기가 P3 의 결론이다.
                raise TooTightError(
                    f"{setup.setup_type}: 지지 {len(below)}개 전부가 최소 손절폭보다 좁다 "
                    f"(하한 {floor_price}) — 축 P3 은 구조를 못 찾으면 진입하지 않는다"
                )
            below = wide_enough
        support = _pick_support(below, self.choice)
        if (
            self.tight is TightStopPolicy.SKIP
            and support is not None
            and self._stop_from(support, margin) > floor_price
        ):
            # 🔴 **축 P2** — 익절 쪽과 대칭이다. 억지로 민 손절은 근거 없는 숫자다.
            raise TooTightError(
                f"{setup.setup_type}: 구조 손절 {self._stop_from(support, margin)} 이 "
                f"최소 손절폭 하한 {floor_price} 보다 좁다 — 축 P2 는 진입하지 않는다"
            )
        resistance = _pick_target(above, self.target)
        # 🔴 **최종 익절선** — 진입 판단에 쓴 창에서 가장 높은 저항대의 **상단**이다.
        #
        #   1차 익절 = 먼저 만나는 벽 (거기서 절반 던진다)
        #   최종     = 그 구간에서 갈 수 있는 끝 (전고점 저항의 위쪽)
        #
        # 상단을 쓰는 이유: 하단은 "벽에 닿기 전"이라 1차 익절의 논리이고, 마지막 목표는
        # 그 벽을 **넘어선 뒤**를 노리므로 같은 값을 쓰면 두 단계가 한 자리에 겹친다.
        final_target = max((band.high for band in above), default=None)

        with fixed_context():
            if support is None:
                stop = self.fallback.resolve_stop(
                    setup, atr, self.fallback_k, StopBasis.DISCOVERY_BAR
                )
            else:
                stop = self._stop_from(support, margin)

            # 🔴 **최소 손절폭** — 둘 중 넓은 쪽 아래로는 내려가지 않는다.
            #
            #   ① 봉 하나(1xATR)   — 그보다 좁으면 진입 봉에서 노이즈에 털린다 (§1-0l)
            #   ② 왕복 비용 x2      — 그보다 좁으면 비용/1R > 0.5 라 RR 2 에서도
            #                          필요 승률이 동전 던지기를 넘는다
            #
            # 실측 근거: MSFT 손절폭 중앙 0.299% 에 비용 0.414% → **비용이 1.39R**.
            # 어떤 승률로도 갚을 수 없는 자리를 진입으로 세고 있었다 (5m 폐기와 같은 산수).
            # 🔴 두 하한을 **따로 계산해서 어느 쪽이 이겼는지 기록**한다 (§1-0t T9).
            #    `max()` 결과만 들면 "하한이 물었다"까지만 알고, 시간축을 올려서 풀릴
            #    문제인지 산술적으로 불가능한 문제인지 구분되지 않는다.
            #
            # ⚠️ P2·P3 은 여기 오기 전에 이미 걸러졌다 — 남은 것은 P1(넓힌다)과,
            #    P3 에서 폴백으로 온 경우다.
            floor_basis = StopFloorBasis.STRUCTURE
            if floor_price < stop:
                stop = floor_price
                # 더 큰 쪽이 실제로 손절 위치를 정했다. 작은 쪽은 어차피 만족된다.
                floor_basis = StopFloorBasis.ATR if atr_floor >= cost_floor else StopFloorBasis.COST

            target = setup.tp_ladder[0].price if setup.tp_ladder else entry
            if resistance is not None:
                target = resistance.low - margin

            # 🔴 **익절에는 하한을 걸지 않는다** — 실측이 그 이유를 보였다.
            #
            # 손절과 같은 하한을 익절에도 걸었더니 둘 다 하한에 걸려 **RR 이 전부
            # 정확히 1.00** 이 됐다 (BTC/ETH 1h 전 거래). 구조에서 도출한 값이 둘 다
            # 하한보다 좁았기 때문이고, 그 순간 축 K·M 이 무의미해진다 — 합류대를
            # 안 보고 `진입 ± 하한` 으로 대칭 진입한 셈이다.
            #
            # 게다가 하한이 1xATR 이라 **봉 하나 안에 양쪽이 다 들어갔다** (보유 0봉).
            # 방향과 무관하게 먼저 닿는 쪽이 이기는 동전 던지기가 된다.
            #
            # ⇒ 익절은 **구조가 정하게 두고**, 손익비가 안 나오면 목표를 미는 것이
            #   아니라 **진입을 포기한다**. 억지로 민 목표는 근거 없는 숫자다.
            widened_target = False
            # 최종 익절선은 1차보다 **위**여야 한다. 아니면 사다리가 뒤집힌다.
            if final_target is not None and final_target <= target:
                final_target = None

        if stop >= entry:
            raise StopResolutionError(
                f"{setup.setup_type}: 확정 손절 {stop} 이 계획 평단 {entry} 이상이다 "
                f"(근거 {'폴백' if support is None else support.describe()}) — "
                "롱 온리 전제 위반이다 (절대 규칙 #10)"
            )
        if target <= entry:
            # 저항이 진입가 **바로 위**에 있으면 여유를 뺀 목표가 진입가 아래로 내려간다.
            # 그때는 저항을 근거로 쓰지 않는다 — 음수 RR 을 만들어 자산 곡선을 오염시키느니
            # 셋업 제안으로 되돌리고 그 사실을 근거에서 지운다.
            target = setup.tp_ladder[0].price if setup.tp_ladder else entry
            resistance = None

        return Resolution(
            stop=stop,
            target=target,
            support=support,
            resistance=resistance,
            fell_back=support is None,
            final_target=final_target,
            floor_basis=floor_basis,
            widened_target=widened_target,
        )
