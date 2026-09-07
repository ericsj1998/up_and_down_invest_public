"""손절 확정 (spec §5.1, §6.1 · 절대 규칙 #4) — 손절의 SSoT (P1-7).

## 구성

| | |
|---|---|
| `StopResolver` | **계약**(Protocol) — 시그니처를 타입 검사로 고정한다 |
| `StructuralAtrStopResolver` | 구현 — 구조 손절과 ATR 손절을 병행해 확정한다 |
| `StopInterpretation` | "더 보수적인 쪽"의 해석 — 경쟁 축 G (승자 미정) |

계약을 **먼저** 고정한 이유는 성능이다. 이 함수가 `k` 를 인자로 받는가 아니면 안에서
설정을 읽는가에 따라 P1-8 총 소요가 **2.3시간과 16.7시간**으로 갈린다
([docs/rules/p1_8_runtime_budget.md](../../../../docs/rules/p1_8_runtime_budget.md)).

## 요구 성질 셋

| # | 성질 | 왜 |
|---|---|---|
| 1 | **순수** — 같은 인자 → 같은 값 | 절대 규칙 #5. 재현 불가능하면 성과 귀속(§4.14)이 무너진다 |
| 2 | **`k` 가 인자** — 설정에서 읽지 않는다 | 한 번의 탐지로 k 세 후보를 전부 재생한다 |
| 3 | **탐지 이후에만 호출** | 탐지 결과가 k·손절 기준과 무관해야 재사용된다 |

②가 핵심이다. `resolve_stop()` 이 안에서 `config/risk.yml` 을 읽으면 k 를 바꿀 때마다
**탐지부터 다시** 돌려야 하고, 그 순간 P1-8 이 하룻밤에서 사흘로 늘어난다.

## 성능이 설계 근거가 되는 것을 경계하되, 여기서는 같은 방향이다

손절 확정은 §5.1·절대 규칙 #4 상 **원래 `decision` 계층의 일**이다. 탐지 안에서 굳히는
것은 계층 위반이기도 하며, 성능은 그 위반의 대가를 숫자로 보여줄 뿐이다.

## ⛔ 여기서 정하지 않는 것

- **k 값** — `config/risk.yml` 의 후보 3개(1.5/2.0/2.5)이며 P1-8 이 병기한다 (축 G-k)
- **"더 보수적인 쪽"의 해석** — 잠정 `max(구조, ATR)` 이고 확정은 P1-8 (축 G)
- **손절 하향** — 절대 규칙 #3. 이 함수는 진입 시점 확정만 하고 이후 조정은 트레일링 소관
"""

from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from updown.common.domain.setup import StopCandidate, TradeSetup
from updown.common.numeric import fixed_context
from updown.marketdata.ingest.timeframes import interval


class StopResolutionError(ValueError):
    """손절을 확정할 수 없다.

    Note:
        **조용히 다른 값으로 넘어가지 않는다** (절대 규칙 #8). 상위 봉 손절을 요청했는데
        상위 봉 후보가 없을 때 발견 봉으로 대체하면, 축 ②의 두 셀이 **같은 값을 재게**
        되어 실험이 무의미해진다. 그 상황은 예외로 드러내고 호출부가 세어야 한다.
    """


class StopBasis(StrEnum):
    """손절 기준 — P1-8 축 ② (spec §P1-8-0b).

    Attributes:
        DISCOVERY_BAR: 셋업이 발견된 봉의 구조.
        HIGHER_BAR: 상위 봉 구조 — 손절폭을 넓히는 다른 경로다.

    Note:
        `orchestration.backtest.matrix.StopBasis` 와 **같은 축**이다. 매트릭스 쪽은 실험
        셀을 정의하고 여기는 확정 함수의 인자를 정의한다 — 값이 갈라지지 않도록
        P1-7 구현 시 한쪽을 import 하게 정리한다 (지금은 계층이 반대라 둘 다 둔다:
        `decision` 은 `orchestration` 을 import 할 수 없다).
    """

    DISCOVERY_BAR = "discovery_bar"
    HIGHER_BAR = "higher_bar"


class StopResolver(Protocol):
    """손절 확정 계약 — P1-7 구현이 만족해야 하는 형태.

    Note:
        `Protocol` 인 이유는 구현을 강제하지 않으면서 **시그니처를 고정**하기 위해서다.
        P1-7 이 이 형태를 벗어나면 `orchestration.backtest` 의 재생 경로가 컴파일되지
        않으므로, 성능 계약이 타입 검사로 지켜진다.
    """

    def resolve_stop(
        self,
        setup: TradeSetup,
        atr: Decimal,
        k: Decimal,
        basis: StopBasis,
    ) -> Decimal:
        """진입 시점 손절가를 확정한다 (spec §5.1 — 손절의 SSoT).

        Args:
            setup: 탐지가 낸 구조 관측. `stop_candidates` 가 후보 목록이며 분석은
                **고르지 않는다** (P1-7 확정 사항 2).
            atr: 그 봉의 ATR. §6.1 `stop = entry - kxATR` 의 재료다.
            k: ATR 손절 배수. ⭐ **인자다** — 설정에서 읽지 않는다 (모듈 docstring 성질 2).
            basis: 손절 기준 (축 ②).

        Returns:
            확정 손절가. `ApprovedOrder.stop_loss` 가 되는 단일 값이다.

        Raises:
            ValueError: 확정 손절이 진입가 이상인 경우 — 롱 온리 전제 위반이다
                (절대 규칙 #10, §12.2 라운딩 후 재검증).

        Note:
            **같은 인자면 언제 불러도 같은 값이어야 한다** (절대 규칙 #5). 현재 시각·난수·
            전역 설정을 참조하면 백테스트가 재현 불가능해지고, 그러면 §4.14 성과 귀속이
            무너진다.
        """
        ...


class StopInterpretation(StrEnum):
    """`더 보수적인 쪽` 의 해석 — 경쟁 축 G (`docs/rules/rule_candidates.md`).

    Attributes:
        FARTHER: **G1** — 진입가에서 **더 먼** 쪽 (넓은 손절). 잠정 기본값.
        NEARER: **G2** — 더 **가까운** 쪽 (좁은 손절).
        ATR_ONLY: **G3** — 구조는 참고만 하고 항상 `k x ATR`.

    Note:
        §6.1 이 "전저점 기반 손절과 **병행해 더 보수적인 쪽 선택**"이라고만 쓰고 **어느
        쪽이 보수적인지 정의하지 않았다.** 그래서 해석이 셋이고, 승자는 P1-8
        out-of-sample 이 정한다 (절대 규칙 #12).

        잠정 기본값이 `FARTHER` 인 근거: P1-5-6 실측이 **좁은 손절의 실패**를 보였다 —
        5m 의 1R 이 0.020% 로 왕복 비용의 **1/15** 였다. 다만 이것은 G2 의 실패를 보인
        것이지 **G1 이 이겼다는 증거는 아니다** (§5.6.7 — 측정은 후보를 좁힐 뿐이다).
    """

    FARTHER = "farther"
    NEARER = "nearer"
    ATR_ONLY = "atr_only"


def _pick_structural(setup: TradeSetup, basis: StopBasis) -> StopCandidate:
    """축 ②에 맞는 구조 손절 후보를 고른다.

    Args:
        setup: 탐지가 낸 셋업.
        basis: 손절 기준.

    Returns:
        해당 기준의 구조 후보.

    Raises:
        StopResolutionError: 후보가 없거나, 상위 봉을 요청했는데 상위 봉 후보가 없는 경우.

    Note:
        **발견 봉 = 후보 중 가장 빠른 시간축**으로 정의한다. `stop_loss`(1순위 제안)를
        쓰지 않는 이유는 그것이 **분석의 선택**이기 때문이다 — 확정 2가 "분석은 고르지
        않는다"고 못박았으므로, 확정 단계가 분석의 선택을 그대로 따르면 그 조문이 무의미해진다.
        시간축은 관측 사실이라 그런 문제가 없다.

        상위 봉 후보가 없으면 **발견 봉으로 대체하지 않는다.** 대체하면 축 ②의 두 셀이
        같은 값을 재게 되어 "상위 봉 손절이 더 낫다/못하다"는 질문에 답할 수 없다.
    """
    if not setup.stop_candidates:
        raise StopResolutionError(
            f"{setup.setup_type}: 손절 후보가 비어 있다 — 탐지가 구조를 싣지 않았다"
        )

    # ⚠️ `interval()` 로 비교한다. `Timeframe` 은 `StrEnum` 이라 문자열로 정렬하면
    #    `15m < 1h < 4h < 5m` 처럼 엉뚱해진다 — 5m 이 가장 뒤로 간다.
    discovery = min(setup.stop_candidates, key=lambda item: interval(item.timeframe))
    if basis is StopBasis.DISCOVERY_BAR:
        return discovery

    higher = [
        item
        for item in setup.stop_candidates
        if interval(item.timeframe) > interval(discovery.timeframe)
    ]
    if not higher:
        raise StopResolutionError(
            f"{setup.setup_type}: 상위 봉 손절 후보가 없다 "
            f"(발견 봉 {discovery.timeframe.value}, 후보 시간축 "
            f"{sorted({c.timeframe.value for c in setup.stop_candidates})}). "
            "발견 봉으로 대체하면 축 ②의 두 셀이 같은 값을 재게 된다"
        )
    # 상위 봉이 여럿이면 **가장 상위**를 쓴다 — 축 ②의 의도가 "손절폭을 넓히는 다른
    # 경로"이므로 중간을 고르면 그 의도가 흐려진다.
    return max(higher, key=lambda item: interval(item.timeframe))


class StructuralAtrStopResolver:
    """구조 손절과 ATR 손절을 병행해 확정한다 (spec §6.1 · P1-7).

    Attributes:
        interpretation: 축 G 해석. 기본값은 잠정 기본값 `FARTHER`(G1).

    Note:
        ## 해석을 **생성자**로 받는 이유

        `resolve_stop` 의 시그니처는 성능 계약으로 고정돼 있다 (모듈 docstring). 축 G 를
        인자로 추가하면 그 계약이 흔들리므로, P1-8 이 해석을 바꿔 볼 때는 **다른 해석의
        resolver 를 만들어** 쓴다. 계약은 그대로이고 실험은 가능하다.

        ## 부호를 **거리로** 다룬다

        "더 보수적 = 더 먼 쪽"인데, 롱에서 더 먼 손절은 **더 낮은 가격**이다. 가격으로
        비교하면 `max/min` 이 뒤집혀 읽히고 그것이 조용한 부호 오류의 자리가 된다.
        그래서 전부 **진입가로부터의 거리**로 계산하고 마지막에 한 번만 가격으로 바꾼다.
    """

    def __init__(self, interpretation: StopInterpretation = StopInterpretation.FARTHER) -> None:
        """해석을 고정한 resolver 를 만든다.

        Args:
            interpretation: 축 G 해석.
        """
        self.interpretation = interpretation

    def resolve_stop(
        self,
        setup: TradeSetup,
        atr: Decimal,
        k: Decimal,
        basis: StopBasis,
    ) -> Decimal:
        """진입 시점 손절가를 확정한다 (spec §5.1 — 손절의 SSoT).

        Args:
            setup: 탐지가 낸 구조 관측.
            atr: 그 봉의 ATR. **양수여야 한다.**
            k: ATR 손절 배수. 설정에서 읽지 않고 **인자로 받는다**.
            basis: 손절 기준 (축 ②).

        Returns:
            확정 손절가. `ApprovedOrder.stop_loss` 가 되는 단일 값이다.

        Raises:
            StopResolutionError: 후보 부재·축 ② 불성립, 또는 확정 손절이 진입가 이상인 경우.
            ValueError: `atr <= 0` 또는 `k <= 0`.

        Note:
            **순수 함수다** (절대 규칙 #5) — I/O·현재시각·전역 설정 참조가 없다.

            호가단위 라운딩은 **여기서 하지 않는다.** 틱 사이즈는 브로커별이라 어댑터
            책임이며(§4.2, §12.2), 라운딩 뒤에는 RR 을 **다시 검증**해야 한다. 그 순서를
            지키려면 확정과 라운딩이 분리돼 있어야 한다.
        """
        if atr <= 0:
            raise ValueError(f"ATR 은 0 보다 커야 한다: {atr} — 손절폭 산정의 분모다")
        if k <= 0:
            raise ValueError(f"ATR 손절 배수는 0 보다 커야 한다: {k}")

        entry = setup.avg_entry
        with fixed_context():
            atr_distance = k * atr

            if self.interpretation is StopInterpretation.ATR_ONLY:
                distance = atr_distance
            else:
                candidate = _pick_structural(setup, basis)
                structural_distance = entry - candidate.price
                if structural_distance <= 0:
                    raise StopResolutionError(
                        f"{setup.setup_type}: 구조 손절 {candidate.price} 가 계획 평단 "
                        f"{entry} 이상이다 ({candidate.source}) — 롱 온리 전제 위반이다"
                    )
                distance = (
                    max(structural_distance, atr_distance)
                    if self.interpretation is StopInterpretation.FARTHER
                    else min(structural_distance, atr_distance)
                )

            stop = entry - distance

        if stop >= entry:
            raise StopResolutionError(
                f"{setup.setup_type}: 확정 손절 {stop} 이 계획 평단 {entry} 이상이다 — "
                "롱 온리 전제 위반이다 (절대 규칙 #10)"
            )
        return stop
