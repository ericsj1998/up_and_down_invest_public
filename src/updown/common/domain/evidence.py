"""근거 자료형 — `Evidence(family, grade)` (judgement.md §0).

## 왜 새로 만드나 — 이름이 비슷한 함정

`analysis/evaluation/evidence.py` 가 **이미 있지만** 그것은 한계 기여(lift)를 재는
**측정 원장**이다. 여기는 "지금 이 근거가 몇 점인가"를 내는 **판단 자료형**이다.
둘은 층이 다르다 — "있네" 하고 넘어가면 판단 레이어가 통째로 안 생긴다.

## 계열은 두 가지 용도로 쓴다

```
채점할 때        계열로 묶어 최댓값        (중복 접기)
플레이북 구성    계열로 묶어 전부 열거      (툴박스 조회)
```

같은 분류, 두 용도다. 그래서 **조회용 태그 축을 따로 만들지 않는다** — 축을 늘리면
플래그를 추가할 때마다 다 맞춰야 하고, 하나만 안 맞으면 조회에서 조용히 빠진다
(절대 규칙 #8). 상세는 `docs/strategy/playbooks.md`.

## ⛔ 주력/보조는 여기 없다

`SignalTier`(PRIMARY/SUPPORTING)가 `evaluation/consensus.py` 에 **전역 값**으로 있는데
그것이 잘못이다. 추세선은 박스권 매매에서 보조이고 돌파 매매에서 주력이다 —
**(플레이북, 플래그) 쌍의 속성**이라 플래그에 붙일 수 없다. 플레이북 선언이 갖는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum, StrEnum


class Family(StrEnum):
    """근거 계열 — **같은 계열은 합산하지 않는다** (judgement.md §0).

    Attributes:
        TREND: 방향과 정렬. 주 추세·이평 정렬·추세선 기울기.
        LEVEL: 가격이 어떤 자리에 있나. 박스·오더블록·넥라인·매물대·VWAP·구름.
        MOMENTUM: 힘의 변화. RSI 다이버전스·마디 이동폭 감쇠·후행스팬.
        VOLATILITY: 폭의 확장/수축. ATR 변화·볼린저 밴드폭·쐐기·구름 두께.
        VOLUME: 참여의 크기. 거래량 비율·돌파 거래량.
        PATTERN: 완성된 모양. 더블바텀·깃발·역헤드앤숄더.

    Note:
        🔴 `MARKET_REGIME` 이 없는 것이 의도다. 국면은 **계열이 아니라 게이트**이며,
        근거로도 쓰면 같은 사실을 두 번 센다. `analysis/regime/` 이 별도 축으로 든다.

        🔴 `FUNDAMENTAL` 도 없다. 옛 `EvidenceCategory` 에 있었지만 쓰이지 않았고,
        쓰지 않는 칸을 들고 있으면 "왜 비어 있나"를 매번 다시 묻게 된다 (P3 에서 다시 만든다).
    """

    TREND = "TREND"
    LEVEL = "LEVEL"
    MOMENTUM = "MOMENTUM"
    VOLATILITY = "VOLATILITY"
    VOLUME = "VOLUME"
    PATTERN = "PATTERN"


class Grade(IntEnum):
    """근거의 세기와 방향 — **확인이 끝났는가**로 매긴다 (judgement.md §0).

    Attributes:
        STRONG_BEAR: -3. 구조가 무너졌다.
        MEDIUM_BEAR: -2.
        WEAK_BEAR: -1.
        NEUTRAL: 0. **근거를 안 내는 것과 같다** (아래).
        WEAK_BULL: +1. 후보 단계.
        MEDIUM_BULL: +2.
        STRONG_BULL: +3. 확정 사건이 일어났다.

    Note:
        `IntEnum` 인 이유는 **더하고 비교해야** 하기 때문이다. `StrEnum` 이면 점수를
        낼 때마다 변환이 끼고, 그 변환이 한 곳이라도 빠지면 조용히 틀린다.

        ⛔ **`NEUTRAL` 을 점수에 넣지 않는다.** "방향 미정"은 근거가 아니다. 넣으면
        중립 근거를 많이 만들수록 계열이 채워진 것처럼 보인다.
    """

    STRONG_BEAR = -3
    MEDIUM_BEAR = -2
    WEAK_BEAR = -1
    NEUTRAL = 0
    WEAK_BULL = 1
    MEDIUM_BULL = 2
    STRONG_BULL = 3


@dataclass(frozen=True, slots=True)
class Evidence:
    """관측된 근거 하나.

    Attributes:
        source: 무엇이 냈나 (`structure.box.support` · `indicator.ma_stack` …).
            **충돌 내역과 결정론 정렬에 쓰이므로 유일해야 한다.**
        family: 계열.
        grade: 세기와 방향.
        detail: 사람이 읽는 설명. 리포트와 AI 근거 요약(§5.3)이 쓴다.
        price: 이 근거가 가리키는 가격. 합류 판정에 쓰며, 가격이 없는 근거는 None.
        suppressed_by: 합류 가산에서 접혔으면 접은 근거의 `source`.

    Note:
        🔴 **접힌 근거를 지우지 않고 표시만 한다.** 로그와 리포트에 "무엇과 무엇이
        겹쳤나"가 남아야 나중에 원인을 되짚는다 (절대 규칙 #8).

        `price` 가 `Decimal | None` 인 이유: 추세 근거처럼 특정 가격을 안 가리키는
        것이 있다. 0 으로 채우면 그 근거가 가격 0 에서 합류하는 것으로 잡힌다.
    """

    source: str
    family: Family
    grade: Grade
    detail: str = ""
    price: Decimal | None = None
    suppressed_by: str | None = None
