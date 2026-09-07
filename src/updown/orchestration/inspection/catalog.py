"""점검할 수 있는 **플래그 목록** — 화면의 선택 트리가 이것을 그대로 읽는다.

## 왜 목록을 코드에 두는가

사용자 요구는 *"내가 분석에 사용하는 모든 요소들을 테스트"* 다. 그 "모든"이 화면에
하드코딩되면, 새 구조물을 붙였을 때 **점검기에서만 조용히 빠진다** — 그리고 안 보이는
것은 없는 것과 구분되지 않는다 (절대 규칙 #8).

여기 한 줄이 곧 화면의 한 항목이고, `snapshot.py` 의 계산 한 개다. 셋이 어긋나면
테스트가 깨진다 (`test_inspection_catalog`).

## 그룹은 사용자의 머릿속 분류다

큰 범위(구조물·게이트)에서도 고르고 작은 범위(추세선 하나)에서도 고를 수 있어야
한다는 요구를 그룹으로 푼다 — 그룹을 켜면 소속 플래그가 전부 켜진다.

## ⛔ 여기 있는 것은 "볼 수 있는 것"이지 "판정에 쓰이는 것"이 아니다

목록에 있다고 그 플래그가 실전에서 도는 것은 아니다. `trendline_channel` · `fvg` 는
과탐지로 **관찰 전용**(`enabled: false`)이고, 점검기는 그것들도 보여 준다 — 왜 껐는지
눈으로 확인할 자리가 필요하기 때문이다. 켜짐 여부는 `enabled` 로 함께 낸다.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

STRUCTURE = "구조물"
INDICATOR = "지표"
TREND = "추세"
SETUP = "셋업"


@dataclass(frozen=True, slots=True)
class Flag:
    """점검 가능한 요소 하나.

    Attributes:
        id: 식별자. `<그룹키>.<이름>` 형태이며 API·화면·계산이 이 문자열로 만난다.
        label: 화면에 보일 한국어 이름.
        group: 묶음 이름.
        note: 이 플래그를 볼 때 알아야 할 것. 화면이 그대로 보여 준다.
    """

    id: str
    label: str
    group: str
    note: str = ""


CATALOG: tuple[Flag, ...] = (
    # ── 구조물 ────────────────────────────────────────────────────────────────
    Flag(
        "structure.swing",
        "스윙 (전고점·전저점)",
        STRUCTURE,
        "Bill Williams 5봉 프랙탈(좌2/우2)을 뽑아 **하이·로우가 번갈아 나오도록 정리**한 것. "
        "전저점 판정·오더블록 손절 기준(게이트 ④)이 이것을 본다. "
        "⚠️ 추세선(구)·박스가 쓰는 것은 정리 **전**이다 → 아래 「프랙탈 전부」",
    ),
    Flag(
        "structure.pivot",
        "프랙탈 전부 (추세선 앵커)",
        STRUCTURE,
        "정리 **전** 5봉 프랙탈 — 추세선(구)·박스가 실제로 앵커로 쓰는 점들. "
        "위 「전고점·전저점」의 상위 집합이라 함께 켜면 링만 있는 점이 정리에서 빠진 것이다. "
        "🔴 이것을 안 그리던 동안 추세선 56개 중 25개가 **안 보이는 접점**으로 그어졌다",
    ),
    Flag(
        "structure.trendline",
        "추세선 (구 · 3접점 탐색)",
        STRUCTURE,
        "⛔ 400봉에 135개가 나온다 — 모든 피벗 쌍 6,105가지 중 우연히 3접점이 맞는 선. "
        "P1-8 비교 기준선이라 남겨 두지만 **보려면 아래 새 추세선을 쓴다** (축 J)",
    ),
    Flag(
        "structure.swing_trendline",
        "추세선 (신 · 스윙·로그·면적)",
        STRUCTURE,
        "사용자 명세 — 볼록 껍질로 **공간이 최소인 선**만. 많이 닿게 → 길게 → "
        "공간 좁게 순. 로그 공간이라 선형 차트에선 곡선이다. 최대 4개",
    ),
    Flag("structure.channel", "채널", STRUCTURE, "추세선 + 반대편 2접점"),
    Flag(
        "structure.leg_channel",
        "마디 채널 (위·아래 2선)",
        STRUCTURE,
        "축 J6 — 마디마다 회귀 채널 위·아래 딱 2선. 탐색이 없어 과탐지가 구조적으로 불가능",
    ),
    Flag(
        "structure.trend_channel",
        "큰 흐름 채널 (창 전체)",
        STRUCTURE,
        "창 전체를 하나로 본 회귀 채널 — 마디 채널이 **국면**을 보여 준다면 이건 **흐름**이다. "
        "방향은 회귀 기울기가 정한다 (사람이 안 고른다). 창당 1개, 자유 파라미터 0개",
    ),
    Flag("structure.box", "박스 (수평 지지·저항)", STRUCTURE, "피벗 군집. 추세선과 같은 허용오차"),
    Flag(
        "structure.box_range",
        "박스권 (상단·하단·내부)",
        STRUCTURE,
        "**정확히 2~3개만** 낸다 — 상단 경계 1 + 하단 경계 1 + 내부 레벨 0~1. "
        "경계는 구간 극단값에서 역으로 만든 **넓은 밴드**이고, 내부는 접점 4회 이상 + "
        "지지↔저항 **역할 전환**을 요구하는 **좁은 띠**다. "
        "접점은 전환점 개수가 아니라 **원본 캔들**에서 세며, 0.8xATR 이상 밀려나야 1회다. "
        "🔴 추세장이면 게이트가 막는다 — 그때도 경계는 그리되 `note` 가 사유를 말한다. "
        "위 「박스」(피벗 군집)·오더블록과 **다른 물건**이다",
    ),
    Flag(
        "structure.balance",
        "밸런스 · 임밸런스",
        STRUCTURE,
        "ZigZag(3xATR) 골격. 임밸런스 = 주 추세 방향, 밸런스 = 반대 방향 파동",
    ),
    # ── 지표 ──────────────────────────────────────────────────────────────────
    Flag("indicator.atr", "ATR(14)", INDICATOR, "허용오차·손절폭의 자다. 이게 틀리면 전부 틀린다"),
    Flag("indicator.ma", "이동평균", INDICATOR, "SMA·EMA 표준 기간"),
    Flag("indicator.rsi", "RSI(14)", INDICATOR, ""),
    Flag("indicator.volume", "거래량 비율", INDICATOR, "평균 대비 배수"),
    # ── 추세 ──────────────────────────────────────────────────────────────────
    Flag(
        "trend.structure",
        "주 추세 (구조 기반)",
        TREND,
        "마디의 가격 이동폭 합. ⚠️ trend/service.py(SSoT)와 다를 수 있어 출처를 함께 낸다",
    ),
    Flag(
        "trend.break",
        "구조 이탈",
        TREND,
        "직전 밸런스 기준선을 종가로 깼는가 — 지금 추세의 전제가 이미 깨졌는지",
    ),
)
"""점검 가능한 플래그 전부.

⚠️ 셋업(`setup.*`)은 여기 없다. 룰 레지스트리가 이미 목록의 주인이고
(`config/rules/*.yml` 이 없으면 부팅이 멈춘다), 사본을 만들면 둘이 갈라진다.
`available_flags()` 가 레지스트리를 읽어 합친다.
"""


def setup_flag(rule_id: str, *, enabled: bool) -> Flag:
    """룰 하나를 플래그로 바꾼다.

    Args:
        rule_id: 룰 식별자.
        enabled: 실전에서 도는가.

    Returns:
        플래그.

    Note:
        꺼진 룰도 목록에 남긴다 — **왜 껐는지 눈으로 확인할 자리**가 점검기다.
        빼 버리면 "과탐지라서 껐다"는 판단을 다시는 검증할 수 없다.
    """
    return Flag(
        id=f"setup.{rule_id}",
        label=rule_id,
        group=SETUP,
        note="" if enabled else "⛔ 관찰 전용 (enabled: false) — 실전에서는 돌지 않는다",
    )


def available_flags(rules: "Mapping[str, bool]") -> tuple[Flag, ...]:
    """카탈로그와 룰 레지스트리를 합친 **전체 목록**.

    Args:
        rules: `rule_id -> enabled`. 레지스트리에서 온다.

    Returns:
        구조물·지표·추세 다음에 셋업이 오는 고정 순서.

    Note:
        셋업을 레지스트리에서 읽는 이유는 목록의 주인이 하나여야 하기 때문이다.
        여기 사본을 두면 룰을 추가했을 때 점검기에서만 빠지고, 그 상태로 "이 룰은
        아무것도 못 찾네"를 보게 된다.
    """
    return (*CATALOG, *(setup_flag(rule, enabled=on) for rule, on in sorted(rules.items())))


def group_of(flag_id: str) -> str:
    """플래그 id 에서 그룹 이름을 얻는다.

    Args:
        flag_id: 플래그 식별자.

    Returns:
        그룹 이름. 모르는 id 면 빈 문자열.
    """
    if flag_id.startswith("setup."):
        return SETUP
    for flag in CATALOG:
        if flag.id == flag_id:
            return flag.group
    return ""


def expand(selection: "Sequence[str]", known: "Sequence[Flag]") -> tuple[str, ...]:
    """그룹 이름이 섞인 선택을 플래그 id 목록으로 편다.

    Args:
        selection: 플래그 id 와 그룹 이름이 섞인 선택.
        known: 지금 유효한 플래그 전부 (`available_flags()` 결과).

    Returns:
        중복 없는 플래그 id, `known` 의 순서대로.

    Note:
        순서를 `known` 기준으로 고정하는 이유는 절대 규칙 #5 다. 선택 순서를 따르면
        같은 선택이 클릭 순서에 따라 다른 응답을 낸다.
    """
    wanted = set(selection)
    return tuple(flag.id for flag in known if flag.id in wanted or flag.group in wanted)
