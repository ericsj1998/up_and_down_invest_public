"""구조물 파라미터 로딩 (P1-1 · spec §4.3.1, §5.6).

## 왜 설정 파일인가

두 가지가 겹친다. spec §4.3.1 은 "임계값은 코드가 아닌 설정(DB/YAML)에서 주입"을
요구하고, spec §5.6.1 ②는 그 값이 **표준 이론값으로 고정**되어야 한다고 못박는다.
설정 파일은 이 둘을 동시에 만족시킨다 — 값이 코드 밖에 있어 관리자 화면에서 보이고,
파일에 근거 주석이 남아 "왜 이 값인가"를 잃지 않는다.

## 왜 기본값을 코드에도 두는가

`config/structures.yml` 이 없어도 dataclass 기본값으로 동작한다. 이것은 조용한 실패가
아니다 — 로더는 파일이 **있는데 깨진** 경우에만 예외를 던진다. 순수 함수 테스트가
설정 파일 없이 돌아야 하고, 그 테스트가 쓰는 값이 곧 표준값이기 때문이다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml

from updown.analysis.structures.tolerance import (
    TOUCH_ATR_MULTIPLE,
    ZONE_ATR_MULTIPLE,
)

type RawMapping = dict[str, object]
"""YAML 파싱 결과의 한 계층.

값 타입을 `object` 로 두는 이유: YAML 스칼라는 이종이고, 좁히는 일은 각 필드의
`Caster` 가 한다. `Any` 를 쓰면 오타 키가 타입 검사를 통과한다 (CLAUDE.md 규약 3).
"""

type Caster = Callable[[object], object]
"""설정 스칼라 하나를 최종 타입으로 바꾸는 함수 (`int`, `_decimal` 등)."""

DEFAULT_CONFIG_PATH = Path("config/structures.yml")
"""기본 파라미터 파일 경로."""

DEFAULT_RULE_VERSION = "structures@1.0.0"
"""구조물 룰 버전 — 성과 귀속 단위 (spec §4.3.1, §4.14)."""


class StructureConfigError(ValueError):
    """파라미터 설정을 읽을 수 없다.

    Note:
        기본값으로 조용히 넘어가지 않는다 (spec §7, 절대 규칙 #8). 설정이 깨진 채로
        구조물이 탐지되면 "룰이 이렇게 판단했다"와 "설정을 못 읽어 다른 값으로
        돌았다"를 구분할 수 없고, 그 구조물에 근거한 주문까지 오염된다.
    """


@dataclass(frozen=True, slots=True)
class SwingParams:
    """스윙 포인트 탐지 파라미터 (spec §6.4).

    Attributes:
        left_bars: 좌측 비교 봉 수.
        right_bars: 우측 비교 봉 수.

    Note:
        기본값 2/2 는 Bill Williams 5봉 프랙탈의 표준값이다. **성과를 보고 조정하지
        않는다** (spec §5.6.2) — 좌우를 넓히면 스윙이 줄어 추세선 접점이 부족해지고,
        좁히면 노이즈가 스윙이 된다. 어느 쪽도 백테스트 수치로 정할 문제가 아니다.
    """

    left_bars: int = 2
    right_bars: int = 2

    def __post_init__(self) -> None:
        """양쪽 비교 봉이 최소 1개는 있어야 극값 판정이 성립한다."""
        if self.left_bars < 1 or self.right_bars < 1:
            raise StructureConfigError(
                f"swing.left_bars/right_bars 는 1 이상이어야 한다 — "
                f"받은 값: {self.left_bars}/{self.right_bars}"
            )


class AnchorSource(StrEnum):
    """축 J5 — 추세선 앵커를 어디서 뽑는가 (`docs/rules/rule_candidates.md` 축 J 갈래 2).

    Attributes:
        FRACTAL: 5봉 프랙탈 피벗 전부. **기본값이자 비교 기준선**이다.
        ZIGZAG: ZigZag 전환점 근처 피벗만 — **조합 폭발의 뿌리를 친다**.

    Note:
        🔴 과탐지의 원인은 조합이다. 400봉 1h 에서 피벗 111개 → 선 쌍 **6,105가지**가
        되고, 그러면 3접점 조건은 필터가 아니라 확률 문제가 된다 (실측 135선, 그중
        71% 가 3접점이며 그 3개 중 2개는 앵커 자신이다).

        ZigZag 는 이미 밸런스 구조가 쓰는 것이라 **새 자유 파라미터가 0개**다 —
        편차는 축 V 가 정하는 값을 그대로 쓴다.

        실측 (2026-08-13 · 창 3개, 편차 1.85xATR):

        | 창 | 현행 | ZigZag |
        |---|---|---|
        | BTC 1h | 135 | **5** |
        | BTC 15m | 79 | **5** |
        | ETH 1h | 106 | **11** |

        ⛔ 편차 2.50·3.00 은 **측정 전에 선언한 반증 조건**("창당 3개 미만이면 탈락")에
        걸려 떨어졌다 (0·3·1 / 0·1·1개). 과탐지를 없앤 것이 아니라 개념을 지운 것이다.

        ⛔ **기본값이 `FRACTAL` 인 것이 의도다.** 후보를 켠 채로 출하하면 P1-8 비교
        기준선이 사라진다 (`OverdetectionGuard` 와 같은 이유).
    """

    FRACTAL = "fractal"
    ZIGZAG = "zigzag"


class OverdetectionGuard(StrEnum):
    """축 J4 — 우연 3접점 배제 후보 (`docs/rules/rule_candidates.md`).

    Attributes:
        NONE: 끔. **기본값이자 비교 기준선**이다.
        SLOPE_RANGE: **J4-D** — 선이 자기 창 폭만큼 연장했을 때 관측 가격 범위를
            벗어나면 버린다.
        WICK_PIERCE: **J4-H** — 앵커 구간 안에서 꼬리가 접점 허용 오차 **밖으로**
            선을 뚫고 지나간 봉이 있으면 버린다.
        BOTH: **J4-DH** — 둘 다.

    Note:
        셋 다 **새 자유 파라미터가 0개**다. J4-D 는 창 자신의 고저폭과 비교하고,
        J4-H 는 "걸린다"의 정의로 이미 쓰고 있는 `touch_atr_multiple` 을 그대로 쓴다.
        J1(상위 N개)이 탈락한 이유가 "N 을 근거로 답할 수 없다"였고 여기는 그 문제가 없다.

        ⛔ **기본값이 `NONE` 인 것이 의도다.** 후보를 켠 채로 출하하면 P1-8 비교
        기준선이 사라진다 — 같은 룰로 잰 매트릭스 위에서 개선을 증명해야 한다.

        측정 근거 (2026-08-07, 창 13개):

        | | 창당 추세선 | 창 범위를 벗어나는 선 |
        |---|---|---|
        | 005930 15m | 7 | **45%** |
        | KRW-BTC 15m | **101** | **44%** |
    """

    NONE = "none"
    SLOPE_RANGE = "slope_range"
    WICK_PIERCE = "wick_pierce"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class TrendlineParams:
    """추세선 작도 파라미터 (spec §6.5).

    Attributes:
        min_touches: 유효 추세선으로 인정할 최소 접점 수.
        touch_atr_multiple: 접점 인정 허용 오차 (**그 봉의 ATR 배수**).
        max_body_violations: 허용할 몸통 이탈 봉 수.
        min_anchor_distance_bars: 두 앵커 사이 최소 봉 간격.

    Note:
        `min_touches` 기본값 3 은 고전 기술적분석의 표준이다 — 2점으로 선을 긋고
        **3번째 접점이 그 선의 유효성을 증명**한다. 2로 낮추면 아무 두 점이나 이은
        선이 모두 추세선이 된다.

        `max_body_violations` 가 0 인 것은 spec §6.5 의 직접 결과다: "추세선 위에
        캔들 몸통이 있고 아래로 꼬리가 걸리는" 형태가 진입이므로, 몸통이 선을 넘은
        선은 애초에 그 추세선이 아니다.
    """

    min_touches: int = 3
    touch_atr_multiple: Decimal = TOUCH_ATR_MULTIPLE
    max_body_violations: int = 0
    min_anchor_distance_bars: int = 5
    overdetection_guard: OverdetectionGuard = OverdetectionGuard.NONE
    anchor_source: AnchorSource = AnchorSource.FRACTAL
    zigzag_deviation: Decimal = Decimal("1.85")
    anchor_slack_bars: int = 2

    def __post_init__(self) -> None:
        """2점 미만으로는 직선이 정의되지 않는다."""
        if self.min_touches < 2:
            raise StructureConfigError(
                f"trendline.min_touches 는 2 이상이어야 한다 (직선은 2점으로 정의된다) — "
                f"받은 값: {self.min_touches}"
            )
        if self.touch_atr_multiple < 0:
            raise StructureConfigError(
                f"trendline.touch_atr_multiple 은 0 이상이어야 한다 — "
                f"받은 값: {self.touch_atr_multiple}"
            )


@dataclass(frozen=True, slots=True)
class ChannelParams:
    """평행 채널 작도 파라미터 (spec §6.5).

    Attributes:
        min_opposite_touches: 반대편 평행선이 닿아야 하는 최소 횟수.

    Note:
        기본값 2 의 근거: 반대편이 한 번만 닿았다면 그것은 채널이 아니라 "추세선
        하나 + 어쩌다 찍은 고점"이다. spec §6.5 의 "채널 상단 도달 시 절반 익절"은
        상단이 반복 작동하는 레벨임을 전제한다.
    """

    min_opposite_touches: int = 2


@dataclass(frozen=True, slots=True)
class BoxParams:
    """수평 박스(지지·저항 레벨) 작도 파라미터.

    Attributes:
        cluster_atr_multiple: 같은 레벨로 묶을 허용 오차 (**ATR 배수**).
        min_touches: 레벨로 인정할 최소 접점 수.
    """

    cluster_atr_multiple: Decimal = TOUCH_ATR_MULTIPLE
    min_touches: int = 2

    def __post_init__(self) -> None:
        """1점은 레벨이 아니다 — 최소 2번은 닿아야 지지·저항이라 부를 수 있다."""
        if self.min_touches < 2:
            raise StructureConfigError(
                f"box.min_touches 는 2 이상이어야 한다 (1점은 레벨이 아니다) — "
                f"받은 값: {self.min_touches}"
            )


@dataclass(frozen=True, slots=True)
class ConfluenceParams:
    """합류 점수 파라미터 (spec §4.3.1).

    Attributes:
        zone_atr_multiple: "이 가격대에 걸렸다"고 볼 허용 오차 (**ATR 배수**).

    Note:
        **구조물 종류별 가중치 필드가 없는 것이 의도다** (spec §5.6.4). 가중치는
        백테스트 산출값이어야 하고(§5.5) 표본 30건 전에는 균등이다(§12.9). 지금
        숫자를 넣으면 그것이 곧 근거 없는 과최적화다.
    """

    zone_atr_multiple: Decimal = ZONE_ATR_MULTIPLE


@dataclass(frozen=True, slots=True)
class StructureParams:
    """구조물 모듈 전체 파라미터.

    Attributes:
        rule_version: 이 설정으로 만들어진 구조물에 찍히는 `rule_id@version`.
        swing: 스윙 탐지.
        trendline: 추세선 작도.
        channel: 채널 작도.
        box: 박스 작도.
        confluence: 합류 점수.
    """

    rule_version: str = DEFAULT_RULE_VERSION
    swing: SwingParams = field(default_factory=SwingParams)
    trendline: TrendlineParams = field(default_factory=TrendlineParams)
    channel: ChannelParams = field(default_factory=ChannelParams)
    box: BoxParams = field(default_factory=BoxParams)
    confluence: ConfluenceParams = field(default_factory=ConfluenceParams)

    @classmethod
    def from_mapping(cls, raw: RawMapping) -> "StructureParams":
        """YAML 파싱 결과에서 만든다.

        Args:
            raw: 파싱된 매핑.

        Returns:
            파라미터 묶음.

        Raises:
            StructureConfigError: 알 수 없는 키가 있거나 값 타입이 맞지 않는 경우.

        Note:
            **알 수 없는 키를 무시하지 않는다.** 오타 난 키를 조용히 넘기면
            "설정을 바꿨는데 동작이 안 바뀐다"가 되고, 그 원인은 찾기 어렵다.
        """
        try:
            return cls(
                rule_version=str(raw.get("rule_version", DEFAULT_RULE_VERSION)),
                swing=_build(SwingParams, raw, "swing", {"left_bars": _int, "right_bars": _int}),
                trendline=_build(
                    TrendlineParams,
                    raw,
                    "trendline",
                    {
                        "min_touches": _int,
                        "touch_atr_multiple": _decimal,
                        "max_body_violations": _int,
                        "min_anchor_distance_bars": _int,
                    },
                ),
                channel=_build(ChannelParams, raw, "channel", {"min_opposite_touches": _int}),
                box=_build(
                    BoxParams,
                    raw,
                    "box",
                    {"cluster_atr_multiple": _decimal, "min_touches": _int},
                ),
                confluence=_build(
                    ConfluenceParams, raw, "confluence", {"zone_atr_multiple": _decimal}
                ),
            )
        except (TypeError, ValueError, ArithmeticError) as exc:
            if isinstance(exc, StructureConfigError):
                raise
            raise StructureConfigError(f"구조물 파라미터를 해석할 수 없다: {exc}") from exc


def _decimal(value: object) -> Decimal:
    """YAML 스칼라를 Decimal 로 바꾼다.

    Note:
        `str()` 을 한 번 거치는 것이 핵심이다. YAML 의 `0.001` 은 float 로 파싱되고
        `Decimal(float)` 은 이진 오차를 그대로 가져온다 — 그 오차가 골든 스냅샷을
        플랫폼마다 다르게 만든다.
    """
    return Decimal(str(value))


def _int(value: object) -> int:
    """YAML 스칼라를 int 로 바꾼다.

    Note:
        `int` 를 그대로 `Caster` 로 쓸 수 없다 — 내장 `int` 는 `object` 를 받는
        오버로드가 없다. 얇은 래퍼가 타입 계약을 맞춘다.
    """
    return int(str(value))


def _build[T](
    target: type[T],
    raw: RawMapping,
    section: str,
    fields: dict[str, Caster],
) -> T:
    """설정의 한 섹션을 dataclass 로 만든다 — 알 수 없는 키는 거부한다."""
    candidate: object = raw.get(section) or {}
    if not isinstance(candidate, dict):
        raise StructureConfigError(f"`{section}` 섹션은 매핑이어야 한다 — 받은 값: {candidate!r}")
    block = cast(RawMapping, candidate)
    unknown = set(block) - set(fields)
    if unknown:
        raise StructureConfigError(
            f"`{section}` 에 알 수 없는 키가 있다: {sorted(unknown)} — "
            f"허용: {sorted(fields)} (오타를 조용히 넘기지 않는다)"
        )
    return target(**{key: caster(block[key]) for key, caster in fields.items() if key in block})


def load_params(path: Path | None = None) -> StructureParams:
    """파라미터를 파일에서 읽는다.

    Args:
        path: 설정 파일 경로. None 이면 `DEFAULT_CONFIG_PATH`.

    Returns:
        파라미터 묶음. 파일이 없으면 표준값 기본치.

    Raises:
        StructureConfigError: 파일이 있으나 파싱·검증에 실패한 경우.

    Note:
        파일 부재는 허용하고 파일 손상은 거부한다. 전자는 "순수 함수 테스트"라는
        정상 경로이고, 후자는 조용히 넘기면 안 되는 고장이다 (절대 규칙 #8).
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        return StructureParams()
    try:
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise StructureConfigError(f"{target} 를 읽을 수 없다: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StructureConfigError(f"{target} 최상위는 매핑이어야 한다 — 받은 값: {type(parsed)}")
    return StructureParams.from_mapping(cast(RawMapping, parsed))
