"""국면 → 플레이북 → 셋업 을 **실제로 잇는다**.

## 여기가 없어서 플레이북이 장식이었다

선언(`config/playbooks.yml`)도 있고 선택기(`analysis/playbook/select.py`)도 있었는데
**아무도 부르지 않았다.** 점검기도 측정도 탐지기를 직접 불렀고, 그래서:

```
⛔ 옛 경로   flags → detector.detect()          국면과 무관하게 셋업이 나온다
✅ 이 경로   trend → 플레이북 선택 → 그 플레이북이 선언한 셋업만
```

옛 경로의 실제 피해가 있다 — 명백한 상승장에서 박스권 셋업이 나왔고, 상승장의
눌림목과 횡보장의 왕복이 **한 줄에 섞여** 기록됐다.

## 조립만 한다

판단은 전부 아래층에 있다 — 국면은 추세 서비스(§4.16), 자격은 `Playbook.runs_in`,
자리는 탐지기. 여기서 새로 정하는 값은 **하나도 없다** (`orchestration/` 입주 조건).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from updown.analysis.detectors.registry import SetupRegistry
from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import active_playbooks, load_playbooks
from updown.analysis.playbook.types import (
    ConflictAction,
    ConflictRule,
    ConflictSide,
    Playbook,
)
from updown.analysis.trend.structure_state import structure_state
from updown.common.domain.evidence import Family, Grade
from updown.common.domain.instrument import MarketGroup, Timeframe
from updown.common.domain.setup import TradeSetup
from updown.common.domain.trend import TrendDirection

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from updown.analysis.detectors.base import MarketContext
    from updown.common.domain.trend import TrendState

TF_LADDER = (
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
)
"""시간축 사다리 — 주 추세는 진입 TF 의 **한 단계 위**가 든다.

⭐ **표준 그대로다.** 다중 시간축 분석(Elder 삼중창)은 *"한 단계 위가 방향을 정하고
진입 축이 타이밍을 정한다"* 를 쓴다. 15m 진입이면 1h 가 방향이다.

🔴 **가장 큰 축을 쓰면 안 된다.** 실측에서 1d 가 DOWN 인데 15m·1h·4h 가 전부 UP 인
구간이 나왔고, 1d 로 게이트를 걸었더니 플레이북이 통째로 안 돌았다 — 일봉 하락은 몇
달을 가는데 그 동안 15m 박스는 정상적으로 돈다.

⛔ 진입 TF 자신을 쓰지도 않는다. 그러면 방향과 타이밍을 같은 축이 정해 게이트가
사실상 없는 것과 같다.
"""

TREND_GRADE = {
    TrendDirection.UP: Grade.MEDIUM_BULL,
    TrendDirection.DOWN: Grade.MEDIUM_BEAR,
    TrendDirection.SIDEWAYS: Grade.NEUTRAL,
}
"""주 추세를 근거 등급으로 옮긴다 — 충돌 규칙이 등급으로 쓰여 있기 때문이다.

⚠️ **`MEDIUM`(±2) 이지 `STRONG`(±3) 이 아니다.** 추세 판정 하나만으로 "구조가
무너졌다"고 말하지 않는다. 구조 이탈은 별도 신호이며 그것이 붙어야 ±3 이다.
"""


@dataclass(frozen=True, slots=True)
class Proposal:
    """플레이북이 내놓은 후보 하나.

    Attributes:
        playbook: 이 후보를 낸 매매법.
        setup: 자리.
        conflicts: 걸린 충돌 규칙 설명 (`기록`). **비어 있는 것이 정상**이다.
        blocked: 걸린 **진입 보류** 규칙 설명 (T26 ②). 하나라도 있으면 세션이
            이 후보로 **진입하지 않는다** — 다만 버리지 않고 실어 나른다.

    Note:
        🔴 성과 귀속 키는 `playbook.attribution`(`sample_ma_cross@0.1.0`) 이다. 셋업 룰 버전과
        **따로** 둔다 — 같은 룰이 여러 매매법에 쓰이면 룰 성적으로는 어느 매매법이
        번 것인지 못 가른다.

        ⚠️ `blocked` 를 후보 삭제로 구현하지 않는 이유: 지워 버리면 화면·기록이
        *"자리가 없었다"* 와 *"자리는 있었는데 막았다"* 를 구별할 수 없다 (규칙 #8).
        막힌 후보도 반대 신호 청산의 재료로는 쓰인다 — 그쪽은 리스크 감소다 (§1.2.1).
    """

    playbook: Playbook
    setup: TradeSetup
    conflicts: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()


def _conflicts(
    playbook: Playbook, setup: TradeSetup, trend: TrendDirection | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """이 자리에서 걸린 충돌들.

    Args:
        playbook: 매매법.
        setup: 판정 대상 자리. 근거 목록에서 계열별 등급을 읽는다.
        trend: 주 추세.

    Returns:
        `(기록된 것들, 진입을 막는 것들)`. 규칙의 `action` 이 어느 쪽인지로 갈린다.

    Note:
        ⛔ **v1 은 막지 않는다** (`ConflictAction.RECORD`). 지금 차단하면 표본이 바뀌어
        *"충돌이 승률을 낮추는가"* 를 영영 못 잰다 — 막는 것은 그 답이 나온 뒤다.

        🔴 **셋업의 근거를 그대로 읽는다.** 어휘를 `Family x Grade` 한 벌로 합치기
        전에는 `TREND` 하나만 볼 수 있었다 — 셋업이 옛 `EvidenceCategory` 로 근거를
        달아 등급이 없었기 때문이다.

        ⚠️ 주 추세는 **근거가 아니라 게이트**라 셋업의 근거 목록에 없다. 그래서
        `TREND` 계열만 `ctx.trend` 에서 따로 만들어 넣는다.
    """
    grades: dict[Family, Grade] = {}
    if trend is not None:
        grades[Family.TREND] = TREND_GRADE[trend]
    for item in setup.evidence:
        # 같은 계열이 여럿이면 **가장 약한 쪽**이 충돌 판정의 기준이다.
        # 강한 것만 보면 약한 반대 근거가 조용히 묻힌다.
        prior = grades.get(item.family)
        grades[item.family] = item.grade if prior is None else min(prior, item.grade)
    # 🔴 **방향을 안 보면 상승추세 + 숏이 무사통과한다** (T26 ① · 실측 2026-08-21).
    #
    #    `at_or_below: -2` 는 *"약세 계열이면"* 이라는 뜻이고, 상승추세는 `+2` 라
    #    조건이 거짓이다. 라이브에서 숏이 92.4% 였고 시장은 강한 상승추세였는데
    #    이 그물이 한 번도 안 걸렸다.
    #
    # ⭐ **숏이면 부호를 뒤집는다.** 그러면 같은 `-2` 가 *"추세가 이 방향과 2등급 이상
    #   반대면"* 이 되어 롱·숏에 대칭으로 작동한다.
    #
    # ⛔ `by_direction` 이 거짓인 룰은 한 글자도 안 달라진다 — 동결 버전 보호 (§5.6.2).
    short = setup.stop_loss > setup.avg_entry

    def facing(rule: ConflictRule) -> Grade:
        """규칙이 보는 등급 — 방향성 규칙이면 숏에서 부호를 뒤집는다.

        Args:
            rule: 충돌 규칙.

        Returns:
            그 계열의 등급 (숏이면 뒤집힌 값).
        """
        value = grades[rule.family]
        return Grade(-value) if rule.by_direction and short else value

    # 🔴 **`진입 보류` 도 발동한다** (T26 ②). 예전에는 `RECORD` 만 통과시켜서
    #    HOLD 규칙을 선언해도 **조용히 사라졌다** — 열거형에 값만 있고 배선이 없었다.
    #    동결 버전은 전부 `기록` 만 선언하므로 이 변경으로 한 글자도 안 달라진다 (§5.6.2).
    # 🔴 **방향별 규칙** (T40): `side` 가 이 셋업의 방향과 안 맞으면 그 규칙은 없는 것과
    #    같다. 시장이 우상향 편향이고 추세 판정도 비대칭이라(UP 70% · DOWN 46.5%),
    #    롱과 숏에 같은 문턱을 쓰면 숏이 과대 허용된다 — 코드는 거울상, 전제는 비대칭.
    #    ⛔ `BOTH`(기본)는 지금까지와 똑같이 돈다 (§5.6.2).
    def applies(rule: ConflictRule) -> bool:
        """이 방향의 후보에 규칙이 적용되는가.

        Args:
            rule: 충돌 규칙.

        Returns:
            양방향 규칙이면 True, 아니면 규칙의 방향과 후보 방향이 같을 때만.
        """
        if rule.side is ConflictSide.BOTH:
            return True
        return (rule.side is ConflictSide.SHORT) is short

    fired = [
        (rule, f"{rule.family.value} {facing(rule)} ≤ {rule.at_or_below} → {rule.action.value}")
        for rule in playbook.conflicts
        if applies(rule) and rule.family in grades and facing(rule) <= rule.at_or_below
    ]
    return (
        tuple(text for rule, text in fired if rule.action is ConflictAction.RECORD),
        tuple(text for rule, text in fired if rule.action is ConflictAction.HOLD),
    )


def propose(
    ctx: MarketContext,
    *,
    timeframe: Timeframe,
    has_box: bool,
    playbooks: Sequence[Playbook] | None = None,
    registry: SetupRegistry | None = None,
    trend_override: TrendDirection | None = None,
) -> list[Proposal]:
    """이 자리에서 도는 플레이북들의 후보를 모은다.

    Args:
        ctx: 분석 재료. `ctx.trend` 는 **워밍업까지 써서** 계산된 값이어야 한다.
        timeframe: 지금 보는 시간축. 플레이북 선언과 맞아야 돈다.
        has_box: 유효한 박스가 있는가. 국면 `RANGE` 판정에 쓴다.
        playbooks: 선언. 안 주면 `config/playbooks.yml` 을 읽는다.
        registry: 셋업 레지스트리. 안 주면 기본 룰 설정으로 만든다.
        trend_override: 국면을 **이 값으로** 본다 (T46 · `regime_source: box`). 세션이 박스의
            반응으로 선언한 방향이며, 국면 스위치와 충돌 규칙이 같은 값을 읽는다 —
            판정이 둘로 갈리지 않는다. None 이면 지금처럼 상위 TF 판정기다.

    Returns:
        후보들. **빈 목록이 정상적인 답이다** — "이 자리에서는 아는 매매법이 없다"는
        사실이 결과다.

    Raises:
        RuntimeError: 플레이북이 꺼진 룰을 선언했다 — 조용히 넘기면 "후보 없음" 과 구별이 안 된다.

    Note:
        🔴 **탐지기를 직접 부르지 않는다.** 플레이북이 선언한 셋업만 돈다. 그래서
        국면이 안 맞으면 셋업이 나와도 후보가 아니다.

        ⚠️ 선언에 없는 셋업 id 는 **조용히 넘기지 않고 터진다** (절대 규칙 #8).
        오타 하나로 매매법이 통째로 죽는데 결과는 "후보 없음"이라 구별이 안 된다.
    """
    declared = tuple(playbooks) if playbooks is not None else load_playbooks()
    trend = trend_override if trend_override is not None else major_trend(ctx.trend, timeframe)
    active = active_playbooks(
        declared,
        market_group=MarketGroup.of(ctx.instrument.market),
        timeframe=timeframe,
        trend=trend,
        has_box=has_box,
    )
    if not active:
        return []

    book = registry if registry is not None else SetupRegistry.from_plugins(load_rules())
    found: list[Proposal] = []
    for playbook in active:
        for rule_id in playbook.setups:
            registered = book.get(rule_id)
            # 🔴 **꺼진 룰을 선언한 플레이북은 시끄럽게 죽는다** (T30 ① · 규칙 #8).
            #    enabled 는 판정 경로에서 아무것도 안 막던 장식이었고, 그래서
            #    "꺼짐이라 적힌 룰로 4판이 돌던" 상태가 조용히 성립했다 — 적힌 것과
            #    도는 것이 다르면 플래그를 고치든 선언을 빼든 **사람이 정해야** 한다.
            if not registered.config.enabled:
                raise RuntimeError(
                    f"{playbook.attribution} 이 꺼진 룰 {rule_id} 을 선언했다 — "
                    f"돌릴 것이면 enabled: true 로, 접을 것이면 setups 에서 뺀다"
                )
            for setup in registered.detector.detect(ctx):
                recorded, blocking = _conflicts(playbook, setup, trend)
                # ⭐ T52 ⑨ — 숏의 문을 1h 가 아니라 **구조 상태**로. 직전 스윙 저점을 잃은 뒤가
                #    아니면 숏은 보류 (후보는 남긴다 — 화면이 왜 막혔는지 말해야 한다).
                if playbook.long_gate == "structure" and setup.stop_loss < setup.avg_entry:
                    state = structure_state(list(ctx.candles.get(timeframe, ())), timeframe)
                    if state is TrendDirection.DOWN:
                        blocking = (*blocking, "롱의 문(구조): 직전 스윙 저점을 잃은 뒤다")
                if playbook.short_gate == "structure" and setup.stop_loss > setup.avg_entry:
                    state = structure_state(list(ctx.candles.get(timeframe, ())), timeframe)
                    if state is not TrendDirection.DOWN:
                        blocking = (*blocking, "숏의 문(구조): 직전 스윙 저점을 잃은 뒤가 아니다")
                found.append(Proposal(playbook, setup, recorded, blocking))
    return found


def major_trend(trend: Mapping[Timeframe, TrendState], entry: Timeframe) -> TrendDirection | None:
    """주 추세 하나를 고른다.

    Args:
        trend: 시간축별 추세 상태. `MarketContext.trend` 또는 `Snapshot.trend`.
        entry: 진입 시간축. 이보다 **한 단계 위**를 방향으로 쓴다.

    Returns:
        주 추세. 담긴 것이 없으면 None.

    Note:
        🔴 **한 단계 위가 방향을 정한다** (`TF_LADDER`). 담긴 순서대로 첫 번째를 쓰던
        때가 있었는데, 모의 라이브가 5개 축을 넣으면서 **5분봉이 국면을 정했다.** 그
        다음엔 가장 큰 축으로 고쳤더니 이번엔 **1d 가 DOWN 이라 플레이북이 통째로 안
        돌았다** — 15m·1h·4h 는 전부 UP 인 구간이었다.

        ⚠️ 둘 다 같은 실수의 양극단이다. 방향은 진입 축보다 **한 칸만** 굵어야 한다.

        ⛔ 비어 있는 것을 `SIDEWAYS` 로 보지 않는다. "모른다"와 "방향이 없다"는 다르다.
    """
    if entry in TF_LADDER:
        above = TF_LADDER[TF_LADDER.index(entry) + 1 :]
        for frame in above:
            found = trend.get(frame)
            if found is not None:
                return found.state
    # ⚠️ 위 축이 하나도 없으면 진입 축으로 떨어진다 — 단일 TF 컨텍스트(측정·스모크)가
    #    그 경우다. 없는 것을 SIDEWAYS 로 지어내지는 않는다.
    found = trend.get(entry)
    if found is not None:
        return found.state
    return next((item.state for item in trend.values()), None)
