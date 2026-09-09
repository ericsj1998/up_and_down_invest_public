"""리스크 정책 로딩 + 슬라이더 보간 (P1-7-1 · spec §4.6 v1.9).

## 슬라이더는 앵커를 없애지 않는다

§4.6 은 프리셋 3단 고정을 **연속 슬라이더**로 확장하면서도 앵커 3개를 유지하라고 했다.
이유는 두 가지다 — 프리셋 이름이 사용자에게 의미를 주고("나는 보수적"), 백테스트·관리자
설정에 **기준점**이 필요하다. 슬라이더는 그 사이를 메우는 것이다.

## 반올림은 항상 안전한 쪽으로 떨어진다

| 파라미터 | 보간 | 근거 |
|---|---|---|
| 회당 리스크·최소 RR·일일 손실 한도 | **선형** | 연속값이다 |
| 동시 포지션 수 | 선형 후 **내림(floor)** | 올림하면 사용자가 의도한 성향보다 **공격적**이 된다 |

§4.6: "반올림 오차는 항상 안전한 쪽으로 떨어뜨린다."

**최소 RR 은 방향이 반대다.** 앵커에서 보수 2.0 → 공격 1.5 로 **내려가므로**, 슬라이더를
올릴 때 min_rr 은 감소한다. 선형 보간이 그것을 자동으로 처리하지만, "안전한 쪽 내림"을
min_rr 에 적용하면 **오히려 위험해진다** — 그래서 내림은 정수 파라미터에만 쓴다.

## 안전장치는 슬라이더로 끌 수 없다

§4.6 불변 원칙이다. 스탑 불변(§6.9)·구조 기반 익절(§6.10)·서킷 브레이커·국면 필터(§4.15)는
슬라이더 어느 위치에서도 동일 적용된다. **공격성 = 리스크 %를 더 쓰는 것**이지 안전장치를
푸는 것이 아니다.

이 모듈이 그 원칙을 지키는 방식: **끌 수 있는 스위치를 애초에 만들지 않는다.** 보간
결과에는 안전장치 on/off 필드가 없다.

## 트레일링은 성향이 아니라 버킷의 성질이다

§6.9 가 "단타 기본 활성 / 장투 비활성"으로 지정했다. 슬라이더와 무관하게 버킷으로
결정된다 — 장투에 트레일링을 걸면 정상적인 조정에서 털린다.

## ⛔ 성과로 조정하지 않는다

이 값들은 사용자의 **성향 선언**이다 (§5.6.2). "수익이 안 나서 리스크를 올린다"는 것은
전략 개선이 아니라 도박이며, 앵커 수치를 백테스트 결과로 움직이면 그것이 된다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import cast

import yaml

from updown.common.domain.instrument import Bucket
from updown.common.domain.proposal import RiskPolicy, RiskPresetName
from updown.common.numeric import fixed_context

DEFAULT_CONFIG_PATH = Path("config/risk.yml")
"""기본 리스크 설정 파일 경로."""

DEFAULT_SLIDER = Decimal("0.5")
"""기본 슬라이더 위치 — §4.6 이 표준(중간점)을 기본값으로 지정했다."""

_ANCHOR_POSITIONS: tuple[tuple[Decimal, RiskPresetName], ...] = (
    (Decimal(0), RiskPresetName.CONSERVATIVE),
    (Decimal("0.5"), RiskPresetName.STANDARD),
    (Decimal(1), RiskPresetName.AGGRESSIVE),
)
"""슬라이더 위치 → 앵커 (spec §4.6 표).

⛔ **위치는 스펙이 못박은 값이라 설정으로 빼지 않았다.** 0.5 를 0.6 으로 옮기면 "표준"이
슬라이더의 중간이 아니게 되고, 같은 슬라이더 위치가 다른 뜻이 된다.
"""


class RiskConfigError(ValueError):
    """리스크 설정을 읽을 수 없다.

    Note:
        기본값으로 조용히 넘어가지 않는다 (절대 규칙 #8). 리스크 정책이 깨진 채로
        수량이 계산되면 **의도보다 큰 포지션**이 나갈 수 있고, 그것이 이 프로젝트에서
        가장 비싼 조용한 실패다.
    """


@dataclass(frozen=True, slots=True)
class RiskAnchor:
    """슬라이더 앵커 하나 (spec §4.6 표의 한 행).

    Attributes:
        risk_pct: 1회 거래 허용 리스크 비율.
        min_rr: 최소 손익비.
        max_positions: 최대 동시 포지션 수.
        daily_loss_limit_pct: 일일 누적 손실 한도 (음수).
    """

    risk_pct: Decimal
    min_rr: Decimal
    max_positions: int
    daily_loss_limit_pct: Decimal

    def __post_init__(self) -> None:
        """앵커 자체의 불변식 — 설정 오타가 정책으로 흘러가지 않게 한다."""
        if self.risk_pct <= 0:
            raise RiskConfigError(f"risk_pct 는 0 보다 커야 한다: {self.risk_pct}")
        if self.min_rr <= 0:
            raise RiskConfigError(f"min_rr 은 0 보다 커야 한다: {self.min_rr}")
        if self.max_positions < 1:
            raise RiskConfigError(f"max_positions 는 1 이상이어야 한다: {self.max_positions}")
        if self.daily_loss_limit_pct >= 0:
            raise RiskConfigError(
                f"daily_loss_limit_pct 는 **음수**여야 한다 (손실 한도다): "
                f"{self.daily_loss_limit_pct}"
            )


@dataclass(frozen=True, slots=True)
class RiskSettings:
    """설정 파일 전체 (spec §4.6).

    Attributes:
        anchors: 프리셋별 앵커.
        risk_pct_hard_cap: 회당 리스크 절대 상한. 슬라이더가 넘을 수 없다.
        trailing_enabled_buckets: 트레일링 스탑을 켜는 버킷들.
        atr_stop_multiple_candidates: ATR 손절 배수 k **후보들** (§6.1 범위 1.5~3.0).
            **하나로 확정하지 않는다** — 그 안의 위치는 형태 논거로 정해지지 않으므로
            성과가 고르게 한다 (`docs/rules/rule_candidates.md` 축 G-k). k 의 **유일한 자리**
            이며 룰 설정(`config/rules/*.yml`)에 두지 않는다 — 셋업별 k 는 조작 통로다.

    Note:
        `risk_pct_hard_cap` 을 앵커와 **별도로** 두는 이유: 앵커 수치를 나중에 조정해도
        이 상한은 남아야 한다. 안전장치는 프리셋으로 끌 수 없다 (§4.6 불변 원칙).
    """

    anchors: Mapping[RiskPresetName, RiskAnchor]
    risk_pct_hard_cap: Decimal
    trailing_enabled_buckets: frozenset[Bucket]
    atr_stop_multiple_candidates: tuple[Decimal, ...] = ()
    stop_liquidation_cap_ratio: Decimal | None = None
    """β — 손절을 **청산거리의 이 비율 안쪽**으로 당긴다 (T120~T146). None 이면 끔.

    🔴 **3x 를 넘기려면 반드시 켠다.** 청산난 판의 81~84% 가 "손절이 청산보다 바깥"인
    판이었다 (T120). 6x 면 청산이 16.2% 인데 SMA200 트레일이 20% 밖에 서면 손절은
    장식이고 청산이 먼저 온다.

    ⭐ 값은 **경로가 골랐다**: 6x 에서 4h 격자로는 β 넷(0.40~0.85)이 다 청산 0 이었는데
    15m 경로로 풀면 **0.40 만** 청산 0 을 지켰다 (T146). 조일수록 경로에 강하다.

    ⛔ 여기가 β 의 **유일한 자리**다 — 룰 설정(`config/rules/*.yml`)에 두지 않는다.
    셋업별 β 는 `atr_stop_k` 와 똑같은 조작 통로다 (§6.1 · `test_risk_policy.py`).
    """
    stop_min_pct: Decimal | None = None
    """손절거리가 이 값보다 **가까우면 그 자리는 가지 않는다** (T147~T150). None 이면 끔.

    🔴 진단이 가리킨 곳 (T147 · 1.2.0 @ 6x · 1,325건):

    | | 손절난 판 | 이긴 판 | t |
    |---|---|---|---|
    | 손절거리 | 중앙 **1.52%** | 중앙 **6.47%** | **-18.1** |
    | ADX | 31.3 | 31.9 | -1.3 (**안 가른다**) |

    SMA200 바로 옆에서 들어가면 손절이 1.5% 자리에 서고 **정상 잡음이 그걸 털어 간다.**
    손실의 원천이 거기다 — `hard_sl` 905건이 -2,238,106 이고 롱 241건은 승률 0% 다.
    지금 게이트(ADX)로는 이 판들이 안 걸러진다.

    ⭐ **`stop_liquidation_cap_ratio`(β) 와 짝이다** — β 가 상한, 이것이 하한이라
    손절거리를 `[하한, β x 청산거리]` 로 가둔다.

    실측 (겹치지 않는 1년 창 8칸): **0.5% 가 8/8** · 창별 수익차 중앙 +8.45%p ·
    최악 칸 +2.79%p · **MDD 차 중앙 -0.81%p**. 0.75%·1.0% 는 7/8 이고 최악 칸이
    마이너스다. 1.5% 부터 두 거래소가 함께 나빠진다 — 고원의 가장자리다.

    ⛔ 여기가 유일한 자리다 — 룰 설정에 두지 않는다 (셋업별이면 조작 통로다).
    """
    stop_protect_ratio: Decimal | None = None
    """보호 손절 — `stop_mode: close` 매매법의 라이브가 거래소에 거는 조건부 자리 (T233 ②).

    청산 거리의 이 비율 자리에 건다. 정상 손절은 세션이 봉 마감 몸통으로 판정해 시장가로 나가고,
    이 조건부는 **봉이 마감되기 전에 청산가까지 밀리는 사고**만 막는다. 그래서
    β(`stop_liquidation_cap_ratio`)보다 **커야** 한다 — β 자리에 걸면 β 에 걸린 손절이 터치로
    나가 close 가 아니다.

    ⚠️ [결정 필요] 기본 0.70: 6x 면 청산 16.2% 의 70% = 11.3% (β 6.5% 와 청산의 중간보다
    조금 안쪽).
    None 이면 close 매매법을 라이브로 못 띄운다 (`apply_playbook_knobs` 가 막는다).
    """
    leverage_needing_stop_cap: Decimal = Decimal(3)
    """이 배율을 **넘으면** β 없이 못 간다 — `require_stop_cap()` 이 강제한다.

    3x 의 청산거리는 32.8% 라 SMA200 손절이 거의 항상 안쪽이고, 실제로 β 는 3x 에서
    아무 일도 안 했다 (T124 · 10칸 중 2칸 · 대부분 차이가 정확히 0). 그 위부터
    의미가 생기고, **동시에 필수가 된다.**
    """

    #: 예산 합이 계좌를 넘어도 **이만큼(예산 합 대비 비율)까지는** 새 진입을 막지 않는다
    #: (2026-09-06). 예산 합은 판을 띄울 때의 자본이고 계좌는 수수료·미실현 손익으로 매 봉
    #: 움직인다 — 자본을 전부 예산으로 쓴 펀드는 첫 수수료 한 푼에 "예산 합이 계좌보다 0.00
    #: 많다" 로 막혔다(실계좌 실측 · 0.0049 USDT). 막아야 할 부족은 Gate 가 새 주문을
    #: `LIQUIDATE_IMMEDIATELY` 로 거절할 크기 — 예산 합의 몇 % 부터다.
    funding_shortfall_tolerance_pct: Decimal = Decimal("0.02")

    def __post_init__(self) -> None:
        """앵커 3개가 전부 있어야 보간이 성립한다."""
        missing = set(RiskPresetName) - set(self.anchors)
        if missing:
            raise RiskConfigError(
                f"앵커가 빠졌다: {sorted(preset.value for preset in missing)} — "
                f"3개가 있어야 슬라이더 보간이 성립한다 (§4.6)"
            )
        floor = self.stop_min_pct
        if floor is not None and not (Decimal(0) < floor < Decimal(1)):
            raise RiskConfigError(
                f"stop_min_pct 가 {floor} 다 — 0 초과 1 미만이어야 한다 (0.005 = 0.5%)"
            )
        ratio = self.stop_liquidation_cap_ratio
        if ratio is not None and not (Decimal(0) < ratio <= Decimal(1)):
            raise RiskConfigError(
                f"stop_liquidation_cap_ratio 가 {ratio} 다 — 0 초과 1 이하여야 한다. "
                f"1 을 넘으면 손절이 청산 **밖**이라 상한이 아니라 구멍이다"
            )
        protect = self.stop_protect_ratio
        if protect is not None:
            if not (Decimal(0) < protect <= Decimal(1)):
                raise RiskConfigError(
                    f"stop_protect_ratio 가 {protect} 다 — 0 초과 1 이하여야 한다 (청산거리 비율)"
                )
            if ratio is not None and protect <= ratio:
                raise RiskConfigError(
                    f"stop_protect_ratio {protect} 가 β {ratio} 이하다 — 보호 손절이 정상 손절보다 "
                    f"안쪽이면 "
                    f"close 판정이 터치로 바뀐다"
                )
        for multiple in self.atr_stop_multiple_candidates:
            if not Decimal("1.5") <= multiple <= Decimal("3.0"):
                raise RiskConfigError(
                    f"ATR 손절 배수 {multiple} 가 §6.1 범위(1.5~3.0)를 벗어났다 — "
                    f"범위 밖 값은 스펙 개정이 먼저다"
                )
        for preset, anchor in self.anchors.items():
            if anchor.risk_pct > self.risk_pct_hard_cap:
                raise RiskConfigError(
                    f"{preset.value} 앵커의 risk_pct {anchor.risk_pct} 가 절대 상한 "
                    f"{self.risk_pct_hard_cap} 을 넘는다 — 상한이 무의미해진다"
                )


def interpolate(
    settings: RiskSettings,
    slider: Decimal,
    *,
    user_id: str,
    bucket: Bucket,
) -> RiskPolicy:
    """슬라이더 위치에서 정책을 만든다 (spec §4.6 보간 규칙).

    Args:
        settings: 앵커 설정.
        slider: 슬라이더 위치. **[0, 1] 로 클램프**된다 (§4.6 "끝점을 넘어갈 수 없다").
        user_id: 소유 사용자.
        bucket: 적용 버킷.

    Returns:
        보간된 정책. `preset` 은 **가장 가까운 앵커 이름**이며 표시용이다.

    Note:
        `t ≤ 0.5` 는 보수↔표준, `t > 0.5` 는 표준↔공격 구간이다 (§4.6).

        **정수 파라미터는 내림한다** — 올림하면 사용자가 의도한 성향보다 공격적이 된다.
        연속 파라미터에는 내림을 쓰지 않는다: `min_rr` 은 슬라이더가 올라갈 때 **내려가는**
        값이라 내림이 오히려 위험한 쪽으로 떨어진다.

        `risk_pct` 는 보간 후 **절대 상한으로 한 번 더 클램프**한다. 앵커가 상한 안에 있음을
        `RiskSettings` 가 이미 검증하므로 보간 결과도 안전하지만, 상한을 여기서 다시 적용해
        **앵커 수치가 나중에 바뀌어도 상한이 살아남게** 한다 (§4.6 불변 원칙).
    """
    with fixed_context():
        position = min(max(slider, Decimal(0)), Decimal(1))
        low, high, ratio = _segment(position)
        start, end = settings.anchors[low], settings.anchors[high]

        risk_pct = min(
            _lerp(start.risk_pct, end.risk_pct, ratio),
            settings.risk_pct_hard_cap,
        )
        return RiskPolicy(
            user_id=user_id,
            bucket=bucket,
            preset=_nearest_preset(position),
            risk_pct=risk_pct,
            min_rr=_lerp(start.min_rr, end.min_rr, ratio),
            daily_loss_limit_pct=_lerp(start.daily_loss_limit_pct, end.daily_loss_limit_pct, ratio),
            max_positions=_lerp_floor(start.max_positions, end.max_positions, ratio),
            trailing_enabled=bucket in settings.trailing_enabled_buckets,
        )


def _segment(position: Decimal) -> tuple[RiskPresetName, RiskPresetName, Decimal]:
    """슬라이더 위치가 속한 앵커 구간과 그 안에서의 비율.

    Args:
        position: 클램프된 슬라이더 위치.

    Returns:
        `(시작 앵커, 끝 앵커, 구간 내 비율 0~1)`.
    """
    for index in range(len(_ANCHOR_POSITIONS) - 1):
        low_at, low_name = _ANCHOR_POSITIONS[index]
        high_at, high_name = _ANCHOR_POSITIONS[index + 1]
        if position <= high_at:
            span = high_at - low_at
            return low_name, high_name, (position - low_at) / span
    # 위 루프는 position <= 1 이면 반드시 반환한다. 여기 오면 클램프가 깨진 것이다.
    raise RiskConfigError(f"슬라이더 위치가 앵커 범위를 벗어났다: {position}")


def _nearest_preset(position: Decimal) -> RiskPresetName:
    """표시용 프리셋 이름 — 가장 가까운 앵커.

    Note:
        보간 결과가 앵커와 정확히 같지 않아도 사용자에게는 이름이 필요하다("나는 보수적").
        **동거리면 더 보수적인 쪽**을 고른다 — 이름이 실제보다 공격적으로 보이지 않게 한다.
    """
    best = _ANCHOR_POSITIONS[0]
    for anchor in _ANCHOR_POSITIONS[1:]:
        if abs(position - anchor[0]) < abs(position - best[0]):
            best = anchor
    return best[1]


def _lerp(start: Decimal, end: Decimal, ratio: Decimal) -> Decimal:
    """선형 보간 — 연속 파라미터용."""
    return start + (end - start) * ratio


def _lerp_floor(start: int, end: int, ratio: Decimal) -> int:
    """선형 보간 후 **내림** — 정수 파라미터용 (spec §4.6).

    Note:
        올림하면 사용자가 의도한 성향보다 공격적이 된다. 예: 보수(3)↔표준(5) 중간에서
        4.0 이 아니라 4 로 떨어지는 것은 같지만, 3.9 가 나오면 **3** 이어야 한다.
    """
    exact = Decimal(start) + Decimal(end - start) * ratio
    return int(exact.to_integral_value(rounding=ROUND_FLOOR))


def _decimal(value: object, field: str) -> Decimal:
    """설정 스칼라를 Decimal 로 바꾼다.

    Note:
        `str()` 을 거치는 것이 핵심이다. YAML `0.005` 는 float 로 파싱되고
        `Decimal(float)` 은 이진 오차를 그대로 가져온다 — 리스크 비율에 그 오차가 들어가면
        수량 계산에 번진다 (`structures/params.py` 와 같은 이유).
    """
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise RiskConfigError(f"{field} 를 수로 읽을 수 없다: {value!r}") from exc


def parse_settings(raw: Mapping[str, object]) -> RiskSettings:
    """파싱된 매핑에서 설정을 만든다.

    Args:
        raw: YAML 파싱 결과.

    Returns:
        설정.

    Raises:
        RiskConfigError: 키 누락·타입 불일치·알 수 없는 버킷·불변식 위반.

    Note:
        **알 수 없는 키를 무시하지 않는다.** 오타 난 키를 조용히 넘기면 "설정을 바꿨는데
        동작이 안 바뀐다"가 되고, 리스크 정책에서 그것은 의도보다 큰 포지션으로 이어진다.
    """
    anchors_raw = raw.get("anchors")
    if not isinstance(anchors_raw, dict):
        raise RiskConfigError(f"`anchors` 는 매핑이어야 한다 — 받은 값: {anchors_raw!r}")
    block = cast(Mapping[str, object], anchors_raw)

    unknown = set(block) - {preset.value for preset in RiskPresetName}
    if unknown:
        raise RiskConfigError(
            f"`anchors` 에 알 수 없는 프리셋이 있다: {sorted(unknown)} — "
            f"허용: {sorted(preset.value for preset in RiskPresetName)}"
        )

    anchors: dict[RiskPresetName, RiskAnchor] = {}
    for preset in RiskPresetName:
        entry = block.get(preset.value)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise RiskConfigError(f"`anchors.{preset.value}` 는 매핑이어야 한다")
        fields = cast(Mapping[str, object], entry)
        allowed = {"risk_pct", "min_rr", "max_positions", "daily_loss_limit_pct"}
        stray = set(fields) - allowed
        if stray:
            raise RiskConfigError(
                f"`anchors.{preset.value}` 에 알 수 없는 키: {sorted(stray)} — "
                f"허용: {sorted(allowed)}"
            )
        try:
            anchors[preset] = RiskAnchor(
                risk_pct=_decimal(fields["risk_pct"], f"{preset.value}.risk_pct"),
                min_rr=_decimal(fields["min_rr"], f"{preset.value}.min_rr"),
                max_positions=int(str(fields["max_positions"])),
                daily_loss_limit_pct=_decimal(
                    fields["daily_loss_limit_pct"], f"{preset.value}.daily_loss_limit_pct"
                ),
            )
        except KeyError as exc:
            raise RiskConfigError(f"`anchors.{preset.value}` 에 {exc} 가 없다") from exc
        except ValueError as exc:
            if isinstance(exc, RiskConfigError):
                raise
            raise RiskConfigError(f"`anchors.{preset.value}` 해석 실패: {exc}") from exc

    buckets_raw = raw.get("trailing_enabled_buckets", [])
    if not isinstance(buckets_raw, list):
        raise RiskConfigError("`trailing_enabled_buckets` 는 목록이어야 한다")
    try:
        buckets = frozenset(Bucket(str(item)) for item in cast(list[object], buckets_raw))
    except ValueError as exc:
        raise RiskConfigError(f"알 수 없는 버킷이 있다: {exc}") from exc

    candidates_raw = raw.get("atr_stop_multiple_candidates", [])
    if not isinstance(candidates_raw, list):
        raise RiskConfigError("`atr_stop_multiple_candidates` 는 목록이어야 한다")
    candidates = tuple(
        _decimal(item, "atr_stop_multiple_candidates")
        for item in cast(list[object], candidates_raw)
    )

    return RiskSettings(
        anchors=anchors,
        risk_pct_hard_cap=_decimal(raw.get("risk_pct_hard_cap", "0.03"), "risk_pct_hard_cap"),
        trailing_enabled_buckets=buckets,
        atr_stop_multiple_candidates=candidates,
        stop_liquidation_cap_ratio=(
            None
            if raw.get("stop_liquidation_cap_ratio") is None
            else _decimal(raw["stop_liquidation_cap_ratio"], "stop_liquidation_cap_ratio")
        ),
        stop_min_pct=(
            None
            if raw.get("stop_min_pct") is None
            else _decimal(raw["stop_min_pct"], "stop_min_pct")
        ),
        stop_protect_ratio=(
            None
            if raw.get("stop_protect_ratio") is None
            else _decimal(raw["stop_protect_ratio"], "stop_protect_ratio")
        ),
        leverage_needing_stop_cap=_decimal(
            raw.get("leverage_needing_stop_cap", "3"), "leverage_needing_stop_cap"
        ),
        funding_shortfall_tolerance_pct=_decimal(
            raw.get("funding_shortfall_tolerance_pct", "0.02"), "funding_shortfall_tolerance_pct"
        ),
    )


def funding_shortfall(
    settings: RiskSettings, *, budgets: Decimal, account: Decimal
) -> Decimal | None:
    """열린 판들의 예산 합이 계좌를 **허용치 너머로** 넘었는가.

    Args:
        settings: 리스크 설정 (`funding_shortfall_tolerance_pct`).
        budgets: 열린 판 예산(증거금)의 합.
        account: 계좌 총액 = 가용 + 포지션 증거금 + 대기 주문 증거금.

    Returns:
        허용치를 넘은 부족액(USDT). 허용치 안이면 None — "막을 일이 아니다".

    Raises:
        RiskConfigError: 허용치가 0 이상 1 미만이 아니면.

    Note:
        ⛔ 예산을 줄이지 않는다 — 어느 판을 깎을지는 사람이 정한다 (`check_funding`).
        이 함수는 판정만 한다.
        허용치 0 은 옛 동작(한 푼이라도 넘으면 막는다)이다.
    """
    tol = settings.funding_shortfall_tolerance_pct
    if tol < 0 or tol >= 1:
        raise RiskConfigError(
            f"funding_shortfall_tolerance_pct 가 {tol} 다 — 0 이상 1 미만이어야 한다 (0.02 = 2%)"
        )
    short = budgets - account
    if short <= budgets * tol:
        return None
    return short


def require_stop_cap(settings: RiskSettings, leverage: Decimal) -> Decimal | None:
    """이 배율로 갈 수 있는지 확인하고 쓸 β 를 돌려준다.

    Args:
        settings: 리스크 설정.
        leverage: 판이 쓰려는 배율.

    Returns:
        적용할 β. 배율이 문턱 이하이고 설정도 없으면 None (상한 없음).

    Raises:
        RiskConfigError: 문턱을 넘는 배율인데 β 가 없는 경우.

    Note:
        🔴 **이 함수가 이 기능의 안전장치다.** 측정이 말하는 것은 *"6x 가 좋다"* 가
        아니라 **"β 를 켠 6x 가 좋다"** 이다 — 6x β0 은 청산이 난다 (T144).
        둘을 따로 켤 수 있게 두면 언젠가 반쪽만 켜지고, 그 반쪽이 하필 위험한 쪽이다.
        그래서 **설정 단계에서 묶는다** (절대 규칙 #8 — 조용한 실패를 만들지 않는다).
    """
    if leverage <= settings.leverage_needing_stop_cap:
        return settings.stop_liquidation_cap_ratio
    if settings.stop_liquidation_cap_ratio is None:
        raise RiskConfigError(
            f"배율 {leverage} 는 {settings.leverage_needing_stop_cap} 을 넘는데 "
            f"`stop_liquidation_cap_ratio`(β) 가 없다 — β 없이 배율만 올리면 "
            f"청산이 난다 (T144: 6x β0 은 청산 25건). config/risk.yml 에 β 를 넣어라"
        )
    return settings.stop_liquidation_cap_ratio


def load_settings(path: Path | None = None) -> RiskSettings:
    """설정을 파일에서 읽는다.

    Args:
        path: 설정 파일 경로. None 이면 `DEFAULT_CONFIG_PATH`.

    Returns:
        설정.

    Raises:
        RiskConfigError: 파일이 없거나 파싱·검증에 실패한 경우.

    Note:
        ⚠️ **구조물 파라미터와 다르게 파일 부재를 허용하지 않는다.**
        `structures/params.py` 는 없으면 표준값으로 동작하지만, 리스크 정책은 "없으면
        기본값"이 **의도보다 큰 포지션**으로 이어질 수 있다. 안전 기본값이라는 것이
        존재하지 않으므로 즉시 중단한다 (절대 규칙 #8, §7).
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        raise RiskConfigError(
            f"{target} 가 없다 — 리스크 정책에는 '안전한 기본값'이 없으므로 "
            f"기본값으로 진행하지 않는다 (절대 규칙 #8)"
        )
    try:
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RiskConfigError(f"{target} 를 읽을 수 없다: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RiskConfigError(f"{target} 최상위는 매핑이어야 한다 — 받은 값: {type(parsed)}")
    return parse_settings(cast(Mapping[str, object], parsed))
