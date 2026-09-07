"""합류 가산 — `LEVEL` 이 붐비는 문제의 해법 (judgement.md §0).

## 문제

`LEVEL` 에 박스·오더블록·넥라인·거래량 프로파일·앵커드 VWAP·일목 구름이 전부 들어간다.
계열 최댓값 규칙 때문에 **박스가 이미 +2 면 나머지 기여가 0** 이다.

그렇다고 계열을 쪼개면 같은 사실("이 가격대가 중요하다")을 여러 번 센다.
**둘 다 피해야 한다.**

## 해법 — 개수가 아니라 **등급을 올린다**

```
서로 **다른 종류**의 LEVEL 근거가 0.5xATR 안에 겹치면
  → 가장 강한 하나의 등급을 한 단계 올린다 (상한 +3)
  → 나머지는 접되 지우지 않는다 (suppressed_by 로 표시)
```

## ⛔ 충돌이 있는 계열에는 적용하지 않는다

합류의 전제는 **"여러 근거가 같은 자리를 가리킨다"** 이다. 근거가 갈렸다면 그 전제가
이미 깨진 것이라, 거기에 가산을 얹으면 **갈린 상태를 더 확신하는 꼴**이 된다.

⚠️ 이 규칙의 근거는 논리뿐이고 측정이 아니다. 반대 결과가 나오면 축으로 올려
out-of-sample 로 판정한다 (절대 규칙 #12) — ⛔ 성과를 보고 조용히 바꾸지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from updown.common.domain.evidence import Evidence, Family, Grade

ZONE_ATR = Decimal("0.5")
"""같은 자리로 볼 거리 — `confluence.zone_atr_multiple` 과 **같은 값**이다.

⭐ 새 숫자를 만들지 않는다. "합류"의 정의가 이미 설정에 있고, 여기서 따로 두면 같은
개념이 두 값을 갖게 된다.
"""


def _kind(source: str) -> str:
    """근거 이름에서 **종류**를 뽑는다.

    Args:
        source: `structure.box.support` 같은 이름.

    Returns:
        `structure.box` — 마지막 마디를 뗀 것.

    Note:
        🔴 **같은 종류끼리는 합류가 아니다.** 박스 두 개가 겹치는 것은 "여러 증거"가
        아니라 같은 작도가 두 번 나온 것이다. `structure.box.support` 와
        `structure.box.resistance` 는 같은 종류로 본다.
    """
    return source.rsplit(".", 1)[0] if "." in source else source


def apply_confluence(evidences: Sequence[Evidence], atr: Decimal | None) -> list[Evidence]:
    """겹치는 `LEVEL` 근거를 하나로 올리고 나머지를 접는다.

    Args:
        evidences: 관측된 근거 전체. `LEVEL` 이 아닌 것은 그대로 통과한다.
        atr: 지금 ATR. 없으면 아무것도 하지 않는다 (거리를 잴 자가 없다).

    Returns:
        가산·접힘이 반영된 목록. 길이는 그대로다 — **접어도 지우지 않는다.**

    Note:
        🔴 **양수 근거끼리만 묶는다.** 지지(+2)와 이탈(-3)이 가격상 가까운 것은
        합류가 아니라 **충돌**이고, 그것은 `score.py` 가 따로 다룬다.

        ⛔ 충돌이 있는 계열에는 적용하지 않는다 (모듈 docstring).

        ⚠️ 클러스터링은 **가격 오름차순**으로 돈다. 순서가 바뀌면 묶이는 조합이
        달라져 결정론이 깨진다 (절대 규칙 #5).
    """
    if atr is None or atr <= 0:
        return list(evidences)

    levels = [e for e in evidences if e.family is Family.LEVEL and e.price is not None]
    if any(e.grade < 0 for e in levels) and any(e.grade > 0 for e in levels):
        # 충돌 계열 — 합류의 전제가 깨졌다.
        return list(evidences)

    bulls = sorted(
        (e for e in levels if e.grade > 0),
        key=lambda e: (e.price or Decimal(0), e.source),
    )
    if len(bulls) < 2:
        return list(evidences)

    tolerance = atr * ZONE_ATR
    changed: dict[str, Evidence] = {}
    group: list[Evidence] = []

    def close_group() -> None:
        """모인 무리를 처리한다 — 종류가 둘 이상일 때만 가산한다."""
        if len(group) < 2 or len({_kind(e.source) for e in group}) < 2:
            return
        best = max(group, key=lambda e: (e.grade, e.source))
        raised = Grade(min(int(best.grade) + 1, int(Grade.STRONG_BULL)))
        kinds = ", ".join(sorted({_kind(e.source) for e in group}))
        changed[best.source] = replace(
            best,
            grade=raised,
            detail=f"{best.detail} (합류 {len(group)}종: {kinds})",
        )
        for item in group:
            if item.source != best.source:
                changed[item.source] = replace(item, suppressed_by=best.source)

    for evidence in bulls:
        price = evidence.price or Decimal(0)
        if group and price - (group[0].price or Decimal(0)) > tolerance:
            close_group()
            group = []
        group.append(evidence)
    close_group()

    return [changed.get(e.source, e) for e in evidences]
