"""룰 이름 → `Evidence` — **옛 `evaluation/catalog.py` 의 후임** (T02 결정 ①·②).

## 무엇이 바뀌었나

```
옛   이름 → (EvidenceCategory, SignalTier)   →  "카테고리당 1표"
새   이름 → (Family, Grade)                  →  계열별 최댓값 + 합류 가산
```

🔴 **`STRUCTURE` 를 쪼갠 것이 이 매핑의 핵심이다.** 오더블록·FVG·채널·박스가 전부
한 칸이라 **셋을 다 살려도 1표**였다. `LEVEL` 과 `PATTERN` 으로 나누면 각각 기여한다.

## ⛔ 국면은 계열이 아니다

`channel_above` 같은 채널 위치는 옛 체계에서 `MARKET_REGIME` 이었다. 새 체계에는 그
계열이 없다 — **국면은 근거가 아니라 게이트**이기 때문이다
([T06](../../../../docs/planning/tasks/T06_regime.md)).

그래서 조용히 버리지 않고 `RegimeSignalError` 로 **터뜨린다.** 근거로 넘기면 국면이
점수에 섞여 "추세가 좋아서 점수가 높다"와 "국면이 좋아서 점수가 높다"를 못 가른다
(절대 규칙 #8).

## 모르는 이름도 예외다

조용히 약한 등급으로 떨어뜨리면 **새 지표가 점수를 못 내는데 아무도 모른다.**
옛 파일이 같은 판단을 했고, 그 이유는 그대로 유효하다.
"""

from __future__ import annotations

from updown.common.domain.evidence import Evidence, Family, Grade

PRIMARY = Grade.MEDIUM_BULL
"""주지표의 기본 등급 (+2).

⚠️ **옛 `SignalTier` 를 등급으로 옮긴 값이다.** 옛 체계에서 층은 "표를 만드는가"를
갈랐는데 새 체계에는 표가 없으므로 **세기**로 옮겼다.

⛔ 성과를 보고 조정하지 않는다 — 지표별 경중은 표본 30건 전에 두지 않는다는 확정이
[T04](../../../../docs/planning/tasks/T04_evidence_engine.md) 에 있다.
"""

SUPPORTING = Grade.WEAK_BULL
"""보조지표의 기본 등급 (+1). 표는 못 만들고 강도만 올리던 것의 후임이다."""

CONFLUENCE_DEPTH = "confluence_depth"
"""합류 깊이 근거의 이름 — 발행처와 등록처가 문자열을 따로 적던 문제를 막는다.

한쪽이 오타를 내면 예외로 터지지만, **아예 안 쓰면 아무 일도 안 일어난다.** 이름을
한 곳에 두면 "쓰이지 않는 상수"로 보인다.
"""

CATALOG: dict[str, tuple[Family, Grade]] = {
    # ── 구조물 → LEVEL. 옛 체계에서 이 넷이 한 칸이라 다 살려도 1표였다 ──────
    "order_block": (Family.LEVEL, PRIMARY),
    "fvg": (Family.LEVEL, PRIMARY),
    "trendline_channel": (Family.LEVEL, PRIMARY),
    CONFLUENCE_DEPTH: (Family.LEVEL, SUPPORTING),
    # ── 거래량 ──────────────────────────────────────────────────────────────
    "volume_surge": (Family.VOLUME, PRIMARY),
    # ── 추세 ────────────────────────────────────────────────────────────────
    "trend_up": (Family.TREND, PRIMARY),
    # ⭐ 매매법 셋업(박스권 계열 · 눌림목 등)은 여기 없다 — 탐지기 모듈이 import 될 때
    #    `register_evidence` 로 등록한다 (T224 · 2026-09-08). 이 표는 매매법을 모르는 이름만 든다.
}
"""이름 → (계열, 등급).

⚠️ **`trendline_channel` 은 룰이 꺼져 있다** (`enabled: false`, 과탐지). 분류와 활성
여부는 다른 문제다 — 여기서는 "켜지면 어느 계열인가"를 선언한다. 분류를 활성 여부에
맞추면 룰을 켤 때마다 이 표를 고쳐야 하고 그 둘이 갈라진다.

⬜ `PATTERN`·`VOLATILITY` 는 아직 비어 있다. 더블바텀·깃발·쐐기·볼린저가 들어올 자리다.
"""

REGIME_SIGNALS = frozenset({"channel_above", "channel_inside", "channel_below", "channel_unknown"})
"""근거가 아니라 **국면**인 이름들 — 옛 `MARKET_REGIME` 층.

🔴 계열로 받지 않는 이유: 국면이 점수에 섞이면 "추세가 좋아서 높다"와 "국면이 좋아서
높다"를 못 가른다. 국면은 점수를 만드는 것이 아니라 **점수를 쓸지 말지를 정한다.**
"""


class UnknownSignalError(KeyError):
    """카탈로그에 없는 근거 이름.

    Note:
        조용히 약한 등급으로 떨어뜨리지 않는 이유: 새 지표가 분류 누락으로 점수를 못
        내면 **아무도 모르는 채 성능이 내려간다** (절대 규칙 #8).
    """


class RegimeSignalError(KeyError):
    """국면 이름을 근거로 쓰려 했다.

    Note:
        `UnknownSignalError` 와 나누는 이유는 **고치는 방법이 다르기** 때문이다.
        모르는 이름은 카탈로그에 등록하면 되지만, 국면은 등록하면 안 되고
        [T06](../../../../docs/planning/tasks/T06_regime.md) 의 게이트로 보내야 한다.
    """


REGIME_KIND = "국면"
"""국면 이름의 라벨 — 계열이 아니므로 `Family` 값이 아니다."""


def register_evidence(name: str, family: Family, grade: Grade) -> None:
    """매매법 셋업 하나를 카탈로그에 등록한다 — 탐지기 모듈이 import 될 때 부른다 (T224).

    Args:
        name: 셋업 이름 (`setup.<name>` 의 뒷부분 · 룰 id).
        family: 계열.
        grade: 등급.

    Note:
        같은 이름을 다른 값으로 두 번 등록하면 `ValueError` — 두 탐지기가 한 이름을 다르게
        주장하는 것은 설정 오류다. 같은 값이면 조용히 지나간다 (모듈이 두 번 import 되는 시험).
    """
    have = CATALOG.get(name)
    if have is not None and have != (family, grade):
        raise ValueError(f"근거 카탈로그에 {name!r} 가 이미 다른 값으로 있다: {have}")
    CATALOG[name] = (family, grade)


def kind_of(name: str) -> str:
    """리포트·화면에 찍을 **분류 라벨**.

    Args:
        name: 룰 이름.

    Returns:
        계열 이름, 또는 국면이면 `REGIME_KIND`.

    Raises:
        UnknownSignalError: 카탈로그에도 국면 목록에도 없는 이름.

    Note:
        🔴 **`evidence_for` 와 나뉘어 있는 이유**: 국면은 근거가 될 수 없지만 **라벨은
        있어야 한다.** 하나로 합치면 라벨을 얻으려고 국면을 근거로 만들게 된다.

        ⚠️ 예전에 호출부마다 `kind` 를 손으로 적어서 **어긋난 적이 있다** —
        `channel_*` 이 리포트에 `structure` 로 찍히는데 분류상으로는 `market_regime`
        이었다. 계산은 맞았지만 **화면이 거짓말했다.** 그래서 한 곳에서만 읽는다.
    """
    key = name.split("@", 1)[0]
    if key in REGIME_SIGNALS:
        return REGIME_KIND
    if key not in CATALOG:
        raise UnknownSignalError(f"{name} 의 분류가 없다 — catalog.CATALOG 에 등록하라")
    return CATALOG[key][0].value


def evidence_for(name: str, detail: str = "") -> Evidence:
    """룰 이름을 `Evidence` 로 바꾼다.

    Args:
        name: 룰 이름. 배수 접미사(`volume_surge@2x`)는 떼고 찾는다.
        detail: 사람이 읽는 설명.

    Returns:
        해당 계열·등급의 근거.

    Raises:
        RegimeSignalError: 국면 이름을 넘긴 경우.
        UnknownSignalError: 카탈로그에 없는 이름.

    Note:
        `@` 뒤를 떼는 이유는 프리셋이다. `volume_surge@2x` 와 `@3x` 는 **같은 지표의
        다른 파라미터**이므로 계열·등급이 같아야 한다 — 따로 등록하면 프리셋을 늘릴
        때마다 카탈로그가 늘어난다.

        ⚠️ **여기서 나온 등급은 기본값이다.** 룰이 자기 관측으로 더 세거나 약한 등급을
        낼 수 있다 (`collect.py` 의 생산자들이 그렇게 한다). 이 함수는 룰 이름만 아는
        측정 코드가 쓰는 입구다.
    """
    key = name.split("@", 1)[0]
    if key in REGIME_SIGNALS:
        raise RegimeSignalError(
            f"{name} 은 국면이지 근거가 아니다 — 점수에 섞으면 '추세가 좋아서 높다'와 "
            f"'국면이 좋아서 높다'를 못 가른다. T06 국면 게이트로 보내라"
        )
    if key not in CATALOG:
        raise UnknownSignalError(
            f"{name} 의 계열 분류가 없다 — 조용히 약한 등급으로 떨어뜨리면 점수를 "
            f"못 내는데 아무도 모른다. catalog.CATALOG 에 등록하라"
        )
    family, grade = CATALOG[key]
    return Evidence(source=name, family=family, grade=grade, detail=detail)
