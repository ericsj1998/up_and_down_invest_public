"""파라미터를 **그 판에서만** 바꿔 본다 (타입 A 편집).

## 타입 A 와 타입 B

화면에서 겉보기가 비슷한 두 가지가 성격이 정반대다.

    (A) 파라미터를 바꾼다   규칙이 **다시 계산해서** 다시 그린다. 화면의 모든 선은
                            여전히 코드가 만든 것이다. → 이 모듈
    (B) 그려진 것을 손으로   화면이 일부는 계산, 일부는 손그림이 된다. 그 선을 만든
        고친다              규칙이 없어 다음 봉에서 아무도 다시 못 긋는다. → `objection.py`

(A) 는 코드로 환원된다 — 사실 **그 자체가 코드**다(설정값). 그래서 절대 규칙 #11 의
기준("그 판단이 규칙으로 환원되어 코드에 남는가")을 자동으로 통과한다.

## ⛔ 드래프트다. 설정 파일에 쓰지 않는다

여기서 바꾼 값이 `config/structures.yml` 이 되는 경로는 **만들지 않았다**. 있으면
차트가 "보기 좋을 때까지" 밀리게 되고 그것이 §5.6.2 가 금지하는 자동조율이다.

판정값을 바꾸려면 축 후보로 올려 out-of-sample 이 정한다 (절대 규칙 #12).

## 🔴 범위를 벗어나면 **자르지 않고 거부한다**

조용히 클램프하면 사용자는 자기가 넣은 값으로 보고 있다고 믿는다. 그 상태로 "이 값에서
추세선이 3개네"를 관찰하면 관찰 자체가 거짓이다 (절대 규칙 #8). 이 도구의 목적이 신뢰
회복이므로 여기서 뭉개면 도구가 무의미해진다.
"""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal

from updown.analysis.structures.balance import ZIGZAG_ATR_MULTIPLE
from updown.analysis.structures.params import AnchorSource, StructureParams

if TYPE_CHECKING:
    from collections.abc import Mapping

ANCHOR_KEY = "trendline.anchor_source"
"""축 J5 — 0 프랙탈(현행) / 1 ZigZag. 열거형이지만 화면 입력칸을 하나로 유지한다."""

ZIGZAG_KEY = "zigzag.deviation"
"""ZigZag 편차는 `StructureParams` 밖에 산다 — 값은 따로 돌려준다."""


class OverrideError(ValueError):
    """모르는 키이거나 범위를 벗어난 값이다."""


@dataclass(frozen=True, slots=True)
class Editable:
    """바꿔 볼 수 있는 파라미터 하나.

    Attributes:
        key: `<절>.<이름>` 형태의 식별자.
        flag: 이 값이 바꾸는 플래그 id. 화면이 파라미터를 플래그 밑에 붙일 때 쓴다.
        kind: `int` 또는 `decimal`.
        low: 허용 하한 (포함).
        high: 허용 상한 (포함).
        note: 이 값이 무엇을 하는지. 화면이 그대로 보여 준다.
    """

    key: str
    flag: str
    kind: Literal["int", "decimal"]
    low: Decimal
    high: Decimal
    note: str


EDITABLE: tuple[Editable, ...] = (
    Editable(
        "swing.left_bars",
        "structure.swing",
        "int",
        Decimal(1),
        Decimal(10),
        "프랙탈 왼쪽 봉 수. 넓히면 스윙이 줄어 추세선 접점이 부족해진다 (표준 2)",
    ),
    Editable(
        "swing.right_bars",
        "structure.swing",
        "int",
        Decimal(1),
        Decimal(10),
        "프랙탈 오른쪽 봉 수 (표준 2)",
    ),
    Editable(
        "trendline.min_touches",
        "structure.trendline",
        "int",
        Decimal(2),
        Decimal(6),
        "2점으로 긋고 3번째 접점이 유효성을 증명한다는 고전 표준이 3 이다",
    ),
    Editable(
        "trendline.touch_atr_multiple",
        "structure.trendline",
        "decimal",
        Decimal("0.02"),
        Decimal(3),
        "접점 허용 오차 (그 봉의 ATR 배수). 크게 잡으면 아무 선이나 3접점이 된다",
    ),
    Editable(
        "trendline.max_body_violations",
        "structure.trendline",
        "int",
        Decimal(0),
        Decimal(5),
        "몸통이 선을 넘어도 되는 횟수. §6.5 는 '몸통은 선 위'라 0 이다",
    ),
    Editable(
        "trendline.min_anchor_distance_bars",
        "structure.trendline",
        "int",
        Decimal(1),
        Decimal(60),
        "두 앵커 사이 최소 간격. 붙어 있는 두 스윙을 이은 선은 추세가 아니다",
    ),
    Editable(
        "channel.min_opposite_touches",
        "structure.channel",
        "int",
        Decimal(1),
        Decimal(5),
        "반대편 경계에 닿아야 하는 횟수",
    ),
    Editable(
        "box.cluster_atr_multiple",
        "structure.box",
        "decimal",
        Decimal("0.02"),
        Decimal(3),
        "피벗을 한 박스로 묶는 허용 오차 (ATR 배수)",
    ),
    Editable(
        "box.min_touches",
        "structure.box",
        "int",
        Decimal(2),
        Decimal(6),
        "박스로 인정할 최소 접점",
    ),
    Editable(
        ANCHOR_KEY,
        "structure.trendline",
        "int",
        Decimal(0),
        Decimal(1),
        "축 J5 — 앵커를 어디서: 0 프랙탈(현행) · 1 ZigZag. 1 이면 선이 135→5개로 준다",
    ),
    Editable(
        "trendline.zigzag_deviation",
        "structure.trendline",
        "decimal",
        Decimal("0.5"),
        Decimal(10),
        "축 J5 앵커용 ZigZag 편차. 2.5 이상은 선이 0~1개가 되어 반증 조건에 걸렸다",
    ),
    Editable(
        ZIGZAG_KEY,
        "structure.balance",
        "decimal",
        Decimal("0.5"),
        Decimal(10),
        "마디를 끊는 편차 (ATR 배수). 낮추면 잘게 쪼개져 전환 신호가 잦아진다 (축 V)",
    ),
)
"""바꿔 볼 수 있는 값 전부.

⛔ **여기 없는 것은 못 바꾼다.** 임의의 설정 키를 열어 두면 결정론 코어의 값까지
화면에서 흔들 수 있게 되고, 그러면 "무엇으로 계산한 그림인가"를 응답만 보고 알 수 없다.
"""

_BY_KEY = {item.key: item for item in EDITABLE}

_ALSO_MOVED_BY: dict[str, tuple[str, ...]] = {
    "structure.swing_trendline": (ZIGZAG_KEY,),
    "structure.leg_channel": (ZIGZAG_KEY,),
    # 프랙탈 전부(추세선 앵커)는 스윙과 **같은 탐지**에서 나온다 — 좌우 봉 수가 그대로
    # 움직인다. 소유자는 `structure.swing` 이라 이 표가 없으면 못 찾는다.
    "structure.pivot": ("swing.left_bars", "swing.right_bars"),
}
"""`Editable.flag` 만으로는 못 찾는 의존.

🔴 `Editable.flag` 와 "그 플래그를 움직이는 값"은 **같은 질문이 아니다.**

    Editable.flag   이 값의 입력칸을 화면 어느 플래그 밑에 붙일까 — **소유자** 표시
    여기            이 플래그를 실제로 움직이는 값이 무엇인가 — **의존** 관계

실측으로 갈라졌다: `structure.swing_trendline` 은 소유 파라미터가 하나도 없어서 재현
탐색이 *"탐색할 것이 없다"* 만 냈다. 그런데 그 선의 앵커는 마디 전환점이고 마디는
`zigzag.deviation` 이 끊는다 — **움직이는데 목록에 없었다.** 그 상태로는 사람이 선을
고쳐 내도 시스템이 되물을 것이 없고, 그러면 이의제기가 규칙으로 환원될 길이 막힌다
(절대 규칙 #11 의 기준).

⛔ 여기에 "혹시 영향이 있을지도"를 넣지 않는다. 탐색 격자가 넓어지면 무엇이든 재현되고,
그러면 "재현됐다"가 아무 정보도 아니게 된다 (`reproduce.py` 서두).
"""


def keys_for(flag: str) -> tuple[str, ...]:
    """그 플래그를 **실제로 움직이는** 파라미터 키들.

    Args:
        flag: 플래그 id.

    Returns:
        훑을 키들. 소유 파라미터 다음에 의존 파라미터가 온다. 순서를 고정하는 이유는
        절대 규칙 #5 다 — 순서가 흔들리면 같은 이의제기가 다른 후보를 낸다.
    """
    owned = tuple(item.key for item in EDITABLE if item.flag == flag)
    extra = tuple(key for key in _ALSO_MOVED_BY.get(flag, ()) if key not in owned)
    return owned + extra


@dataclass(frozen=True, slots=True)
class Applied:
    """덮어쓴 결과.

    Attributes:
        params: 적용된 구조물 파라미터.
        deviation: 적용된 ZigZag 편차.
        changed: 실제로 기본값과 달라진 것들 (`key -> 문자열 값`).

    Note:
        `changed` 를 응답에 실어 화면이 **"지금 기본값이 아니다"** 를 말하게 한다.
        말하지 않으면 사용자는 며칠 뒤 그 화면을 다시 보고 판정 설정이라 믿는다
        (절대 규칙 #8).
    """

    params: StructureParams
    deviation: Decimal
    changed: dict[str, str]


def _coerce(spec: Editable, raw: str) -> Decimal:
    """문자열 하나를 검증해 숫자로.

    Args:
        spec: 이 키의 명세.
        raw: 들어온 값.

    Returns:
        검증된 값.

    Raises:
        OverrideError: 숫자가 아니거나, 정수여야 하는데 소수이거나, 범위를 벗어나면.
    """
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise OverrideError(f"{spec.key}: 숫자가 아니다 ({raw!r})") from exc
    if not value.is_finite():
        raise OverrideError(f"{spec.key}: 유한한 값이 아니다 ({raw!r})")
    if spec.kind == "int" and value != value.to_integral_value():
        raise OverrideError(f"{spec.key}: 정수여야 한다 ({raw!r})")
    if not (spec.low <= value <= spec.high):
        raise OverrideError(
            f"{spec.key}: {spec.low}~{spec.high} 범위를 벗어났다 ({value}). "
            f"조용히 자르지 않는다 — 넣은 값과 다른 값으로 본 그림은 관찰이 아니다"
        )
    return value


def apply_overrides(
    raw: "Mapping[str, str | None]", base: StructureParams | None = None
) -> Applied:
    """덮어쓸 값들을 검증해 파라미터에 반영한다.

    Args:
        raw: `key -> 값` 매핑. 빈 문자열과 `None` 은 무시한다 — 화면의 빈 입력칸이고,
            JSON 으로 오면 `null` 이 된다. 0 으로 읽으면 안 된다.
        base: 바탕 파라미터. None 이면 표준값.

    Returns:
        적용 결과. 아무것도 안 바뀌었으면 `changed` 가 빈 dict 다.

    Raises:
        OverrideError: 모르는 키이거나 값이 잘못됐으면.

    Note:
        ⛔ 이 함수는 설정 파일을 건드리지 않는다. 반환값은 **이번 호출에서만** 쓰인다.
    """
    settings = base or StructureParams()
    deviation = ZIGZAG_ATR_MULTIPLE
    changed: dict[str, str] = {}

    swing = settings.swing
    trendline = settings.trendline
    channel = settings.channel
    box = settings.box

    for key, text in raw.items():
        if text is None or str(text).strip() == "":
            continue
        spec = _BY_KEY.get(key)
        if spec is None:
            raise OverrideError(
                f"바꿀 수 없는 키다: {key!r} — 열려 있는 것은 {sorted(_BY_KEY)} 뿐이다"
            )
        value = _coerce(spec, str(text).strip())
        section, _, name = key.partition(".")
        if key == ZIGZAG_KEY:
            deviation = value
        elif key == ANCHOR_KEY:
            trendline = replace(
                trendline,
                anchor_source=AnchorSource.ZIGZAG if value else AnchorSource.FRACTAL,
            )
        elif section == "swing":
            swing = replace(swing, **{name: int(value)})
        elif section == "trendline":
            casted = int(value) if spec.kind == "int" else value
            trendline = replace(trendline, **{name: casted})
        elif section == "channel":
            channel = replace(channel, **{name: int(value)})
        elif section == "box":
            casted = int(value) if spec.kind == "int" else value
            box = replace(box, **{name: casted})
        changed[key] = str(value)

    return Applied(
        params=replace(settings, swing=swing, trendline=trendline, channel=channel, box=box),
        deviation=deviation,
        changed=changed,
    )


def override_defaults() -> dict[str, str]:
    """지금 표준값 — 화면이 입력칸의 placeholder 로 쓴다.

    Returns:
        `key -> 문자열 값`.
    """
    base = StructureParams()
    return {
        "swing.left_bars": str(base.swing.left_bars),
        "swing.right_bars": str(base.swing.right_bars),
        "trendline.min_touches": str(base.trendline.min_touches),
        "trendline.touch_atr_multiple": str(base.trendline.touch_atr_multiple),
        "trendline.max_body_violations": str(base.trendline.max_body_violations),
        "trendline.min_anchor_distance_bars": str(base.trendline.min_anchor_distance_bars),
        ANCHOR_KEY: "0" if base.trendline.anchor_source is AnchorSource.FRACTAL else "1",
        "trendline.zigzag_deviation": str(base.trendline.zigzag_deviation),
        "channel.min_opposite_touches": str(base.channel.min_opposite_touches),
        "box.cluster_atr_multiple": str(base.box.cluster_atr_multiple),
        "box.min_touches": str(base.box.min_touches),
        ZIGZAG_KEY: str(ZIGZAG_ATR_MULTIPLE),
    }
