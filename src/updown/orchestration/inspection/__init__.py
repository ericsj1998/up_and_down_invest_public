"""플래그 점검기 — **사람이 규칙 명세를 검토하는 자리** (절대 규칙 #11 ⭕ 조항).

## 이 패키지가 답하는 질문

    "플래그들이 적합하게 계산되고 있는가"

이것은 **정합성** 질문이지 튜닝 질문이 아니다. 그리고 백테스트 숫자는 여기에 원리적으로
답하지 못한다 — 기대값이 나빠도 "규칙이 나쁜 것"과 "규칙이 잘못 구현된 것"이 구분되지
않기 때문이다. 이 프로젝트의 실제 사고가 전부 후자였다:

    dominant() 가 가격 대신 봉 개수를 셌다        → 전부 밸런스로 판정됐다
    체결 모델이 낙관적이었다                       → v11 까지 모든 R 값이 무효 (T19)
    게이트 ① 이 91% 를 기각하고 있었다             → T15 계측을 따로 만들 때까지 몰랐다
    고정 % 허용오차가 시간축 간 4.1배 다른 뜻이었다 → 별도 실측으로 발견 (§1-0h)

**백테스트 성과표가 잡아낸 것은 하나도 없다.**

## 규칙 #11 을 완화하지 않는다

#11 은 눈으로 정답지 만드는 것을 금지하면서 ⭕ *"규칙 명세가 타당한지 사람이 검토하는
것은 필수"* 라고 적어 뒀다. 그 조항이 지금까지 구현된 적이 없었고, 이 패키지가 그것이다.
구분 기준도 #11 이 정해 놨다 — **"그 판단이 규칙으로 환원되어 코드에 남는가"**.

## 화면에서 하는 두 가지는 성격이 다르다

    (A) 파라미터를 바꾼다   규칙이 다시 계산해서 다시 그린다. 화면의 모든 선은
                            여전히 코드가 만든 것이다. 안전하다.

    (B) 그려진 것을 손으로   화면이 일부는 계산, 일부는 손그림이 된다. 그 선을 만든
        고친다              규칙이 없으므로 다음 봉에서 아무도 다시 못 긋는다.

(B) 를 버리지 않되 **정답지가 아니라 「이의제기」로** 기록한다. 좌표가 붙은 버그
리포트다. 그리고 시스템이 되묻는다 — *이 그림을 만들어내는 파라미터가 존재하는가?*

    존재한다     → 파라미터 문제다. 축 후보로 올려 out-of-sample 이 판정한다
                   (절대 규칙 #12 그대로. 눈은 후보를 **제안**했을 뿐이다)
    존재 안 한다 → 🔴 더 값진 발견이다. 어떤 파라미터로도 그 그림이 안 나온다
                   = 정의 자체가 사람이 생각하는 규칙과 다르다

## 🔴 이 설계를 지탱하는 제약 넷 — 하나라도 빠지면 정답지가 된다

1. **이의제기 로그는 채점 분모가 될 수 없다.** "정확도 = 이의제기 안 받은 비율" 같은
   지표를 만드는 순간 눈이 정답이 된다. 그런 집계 API 를 만들지 않는다.
2. **이의제기는 미래를 모르는 상태에서만 받는다.** 손익·이후 전개는 **제출 후** 공개한다.
   결과를 알고 그은 선은 항상 잘 맞고, 이것은 의지가 아니라 인지의 문제라 눈으로 못
   이긴다. → `window.closed_bars`
3. **화면에서 바꾼 파라미터는 실거래로 가지 않는다.** 드래프트로만 남고 승격 경로는
   축 후보 → out-of-sample 뿐이다 (§5.6.2 자동조율 금지).
4. **손절·익절 값을 직접 못 고친다.** 파라미터만 바꾸고 RiskManager 가 재계산한다
   (절대 규칙 #4). P1 엔 주문이 없어 무해하지만, 지금 긋지 않으면 P2 에서 샌다.

## 구성

    moment.py   점검 시점을 뽑는다 (결정론 · 봉인 준수)
    window.py   창 크기 (as-of 절단은 `analysis/context/guard.py` 가 이미 한다)
    catalog.py  볼 수 있는 플래그 목록 — 화면의 선택 트리
    snapshot.py 고른 플래그를 그 시점 기준으로 계산 → 그릴 수 있는 모양
    overrides.py 파라미터를 그 판에서만 바꿔 본다 (타입 A · 드래프트)
"""

from updown.orchestration.inspection.catalog import (
    CATALOG,
    Flag,
    available_flags,
    expand,
    group_of,
)
from updown.orchestration.inspection.moment import Moment, MomentUnavailableError, pick_moment
from updown.orchestration.inspection.overrides import (
    EDITABLE,
    Applied,
    Editable,
    OverrideError,
    apply_overrides,
    override_defaults,
)
from updown.orchestration.inspection.snapshot import (
    FrameView,
    Layer,
    Snapshot,
    build_frame,
)
from updown.orchestration.inspection.window import warmup_for

__all__ = [
    "CATALOG",
    "EDITABLE",
    "Applied",
    "Editable",
    "Flag",
    "FrameView",
    "Layer",
    "Moment",
    "MomentUnavailableError",
    "OverrideError",
    "Snapshot",
    "apply_overrides",
    "available_flags",
    "build_frame",
    "expand",
    "group_of",
    "override_defaults",
    "pick_moment",
    "warmup_for",
]
