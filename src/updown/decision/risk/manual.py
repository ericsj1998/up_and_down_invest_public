"""**사람이 손으로 그은 계획**을 집행 가능한 값으로 확정한다 (차트 주문 · 2026-08-30).

## 이 파일이 있는 이유

사용자 확정:

> *"그냥 완전히 커스텀이라고 생각했고, 그냥 사람이 정하는대로 다 들어가는 거야.
>  run이지만 사실 그냥 추적을 위한 깡통을 생각하긴 했어. 즉, 해당 RUN의 주체는
>  그냥 사용자인거야."*

⇒ **판단은 전부 사람이 한다.** 러너는 셋업도 트레일도 재레버도 없다 (`config/playbooks.yml`
의 `custom`). 그런데 그렇다고 **아무 값이나 그대로 나갈 수는 없다** — 값이 물리적으로
성립하지 않으면 그것은 "사람의 판단"이 아니라 **못 채워지는 주문**이기 때문이다.

## 🔴 그래서 여기서 하는 일은 **딱 세 가지**다

    ① require_stop_cap   이 배율로 가려면 β 가 있어야 한다 (없으면 **거절**)
    ② capped_stop        손절이 청산 밖이면 안쪽으로 **당긴다** — 조이는 방향만
    ③ 기하 검사          손절이 진입 반대편에 있나 · 익절이 진입 너머에 있나

⛔ **이것 말고는 아무것도 안 바꾼다.** 진입가·익절가·1차 익절은 사람이 낸 값이
그대로 간다. RR 이 낮아도, 필요 승률이 높아도 **경고만** 한다 — 그것은 판단이고,
판단은 사람 것이다.

## ⚠️ ② 는 왜 "사람 뜻 무시"가 아닌가

청산 거리 밖의 손절은 **절대 체결되지 않는다.** 3x 면 32.8%, 6x 면 16.2% 에서
거래소가 먼저 포지션을 가져간다. 그보다 먼 손절을 "존중"하는 것은 손절이 없는
것과 같고, 실제로 그렇게 죽었다 — T120 실측 **청산된 판의 81~84% 가 "손절이 청산
밖"** 이었다.

⇒ ② 는 사람의 뜻을 바꾸는 것이 아니라 **닿을 수 없는 값을 닿는 값으로 옮기는**
  것이다. 그리고 항상 **조이는 방향**이라 절대 규칙 #3 과 같은 사상이다.

⚠️ 옮겼으면 **반드시 말한다** (`moved`). 사람이 낸 값이 그대로 나갈 것처럼 보이면
그것이 거짓말이다 (절대 규칙 #8).

## 🪞 양방향이다

절대 규칙 #10 개정(2026-08-17)으로 숏이 열렸다. 부호 하나로 갈리는 것을 두 벌로
쓰면 한쪽만 고쳐진다 — 방향을 받아 **한 벌**로 쓴다.

⚠️ 방향을 `Direction` 이 아니라 **`short: bool`** 로 받는다. `Direction` 은
`orchestration/walkforward/ledger.py` 에 있어 이 층(`decision/`)이 import 할 수 없다
(의존 방향은 CI 가 강제한다). 바로 아래의 `capped_stop` 도 같은 이유로 `short: bool` 이다 —
층을 지키려고 억지로 맞춘 것이 아니라, **경계가 그렇게 서 있다는 신호**다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.plan import MIN_STOP_PCT, required_win_rate
from updown.decision.risk.policy import RiskConfigError, RiskSettings, require_stop_cap
from updown.decision.sizing import capped_stop, liquidation_distance


@dataclass(frozen=True, slots=True)
class Confirmed:
    """확정 결과 — **주문에 쓸 값과, 왜 그런지**.

    Attributes:
        ok: 이 계획으로 주문을 낼 수 있나. 거짓이면 `reasons` 에 막은 이유가 있다.
        stop: 🔴 **확정 손절.** 사람이 낸 값과 다를 수 있다 (`moved` 참조).
        moved: 손절이 옮겨졌나. 참이면 화면은 **바뀐 값**을 보여 줘야 한다.
        blocked: 주문을 막은 이유들 — 하나라도 있으면 `ok` 는 거짓이다.
        warnings: 막지는 않지만 사람이 알아야 하는 것들 (RR·필요 승률·손절폭).
        beta: 적용한 β. 없으면 상한 없음.
        liq_pct: 청산 거리 (%).
        stop_pct: 확정 손절폭 (%).
        rr: 1차 익절 기준 손익비.
        need_pct: 비용을 갚는 데 필요한 승률 (%).
    """

    ok: bool
    stop: Decimal
    moved: bool
    blocked: tuple[str, ...]
    warnings: tuple[str, ...]
    beta: Decimal | None
    liq_pct: Decimal
    stop_pct: Decimal
    rr: Decimal
    need_pct: Decimal

    @property
    def reasons(self) -> tuple[str, ...]:
        """막은 이유 + 경고를 **막은 것 먼저** 이어 붙인다 (화면 표시용)."""
        return self.blocked + self.warnings


def confirm(
    *,
    entry: Decimal,
    stop: Decimal,
    first: Decimal,
    target: Decimal,
    leverage: Decimal,
    short: bool,
    round_trip: Decimal,
    settings: RiskSettings,
) -> Confirmed:
    """손으로 그은 계획을 확정한다 — **집행값의 SSoT** (절대 규칙 #4).

    Args:
        entry: 사람이 정한 진입가.
        stop: 사람이 정한 손절가.
        first: 사람이 정한 1차 익절가.
        target: 사람이 정한 최종 익절가.
        leverage: 배율.
        short: 숏인가. 거짓이면 롱이다.
        round_trip: 왕복 비용 비율 (0.00157 = 0.157%).
        settings: 리스크 설정.

    Returns:
        확정 결과. `ok` 가 거짓이면 **주문을 내면 안 된다**.

    Raises:
        ValueError: 진입가나 배율이 0 이하인 경우 — 값이 아니라 입력 오류다.

    Note:
        ⛔ **RR 이 낮다고 막지 않는다.** 낮은 RR 로 매매할지는 사람의 판단이고,
        이 판의 주체는 사람이다. 다만 **필요 승률이 100% 를 넘으면 막는다** —
        그것은 판단이 아니라 **산수**이고, 어떤 실력으로도 이길 수 없다.
    """
    if entry <= 0:
        raise ValueError(f"진입가는 0 보다 커야 한다: {entry}")
    if leverage <= 0:
        raise ValueError(f"배율은 0 보다 커야 한다: {leverage}")

    sign = Decimal(-1) if short else Decimal(1)
    blocked: list[str] = []
    warnings: list[str] = []

    # ① β 가 없는 고배율은 **거절**한다. 이 프로젝트의 청산 원인이었다 —
    #    T144 실측 6배 β 없이 **청산 25건**. 조용히 넘기지 않는다 (절대 규칙 #8).
    try:
        beta = require_stop_cap(settings, leverage)
    except RiskConfigError as exc:
        return Confirmed(
            ok=False,
            stop=stop,
            moved=False,
            blocked=(str(exc),),
            warnings=(),
            beta=None,
            liq_pct=liquidation_distance(leverage) * 100,
            stop_pct=Decimal(0),
            rr=Decimal(0),
            need_pct=Decimal(100),
        )

    # ⚠️ **기하부터 본다.** 손절이 진입 반대편에 있어야 β 계산이 뜻을 가진다 —
    #    뒤집힌 계획에 `capped_stop` 을 먹이면 말이 안 되는 값이 나온다.
    if (stop >= entry) if not short else (stop <= entry):
        side = "숏인데 손절이 진입 아래" if short else "롱인데 손절이 진입 위"
        blocked.append(f"{side}다 — 그 손절은 즉시 체결된다")
        return Confirmed(
            ok=False,
            stop=stop,
            moved=False,
            blocked=tuple(blocked),
            warnings=(),
            beta=beta,
            liq_pct=liquidation_distance(leverage) * 100,
            stop_pct=Decimal(0),
            rr=Decimal(0),
            need_pct=Decimal(100),
        )

    # ② 청산 안쪽으로 당긴다 — **조이는 방향만** (절대 규칙 #3 과 같은 사상).
    confirmed = stop
    if beta is not None:
        confirmed = capped_stop(entry=entry, stop=stop, leverage=leverage, ratio=beta, short=short)
    moved = confirmed != stop
    liq_pct = liquidation_distance(leverage) * 100
    if moved:
        warnings.append(
            f"손절을 {stop} → {confirmed} 로 당겼다 — 원래 값은 "
            f"청산({liq_pct:.1f}%) 밖이라 **체결되지 않는다** (β{beta})"
        )

    risk = (entry - confirmed) * sign
    stop_pct = risk / entry * 100
    # ③ 손절폭 하한 — 그보다 좁으면 **노이즈가 먼저 죽인다** (T173: BTC 1h 계획의
    #    83% 가 이 아래였고, 그 계획들의 RR 3~8 은 전부 종잇조각이었다).
    #
    # ⚠️ **막지는 않는다.** 스캘핑이면 좁은 손절이 의도일 수 있고, 그 판단은 사람 것이다.
    #
    # 🔴 그런데 **아래의 비용 산수가 이 함정을 대신 잡아 주지 않는다.** 목표를 그대로
    #    두고 손절만 당기면 RR 이 비용보다 빨리 커져서 필요 승률이 **내려간다**:
    #
    #      손절 2.0% · RR 3   →  26.96%
    #      손절 0.2% · RR 30  →   5.76%   ← 숫자는 계속 좋아진다
    #
    #    산수가 잡는 것은 손절과 목표가 **같이** 좁을 때다 (5m 이 죽은 모양).
    #    ⇒ *"목표는 멀리, 손절만 바짝"* 을 말하는 것은 **이 경고뿐**이다. 지우면 없다.
    if stop_pct < MIN_STOP_PCT * 100:
        warnings.append(
            f"손절폭 {stop_pct:.2f}% 가 하한 {MIN_STOP_PCT * 100:.1f}% 안이다 — "
            "이 폭은 방향이 맞아도 노이즈에 먼저 죽는다 (T173)"
        )

    # 🔴 **기하가 뒤집힌 익절은 매매가 아니다** (2026-08-18: 롱인데 1차 익절이 진입
    #    아래인 계획 4건이 나가 -4.48% 로 끝났다). 러너의 `geometry_fault` 가 어차피
    #    막지만, **돈을 걸기 전 화면에서** 막는 것이 유일하게 싼 지점이다.
    for name, value in (("1차 익절", first), ("최종 익절", target)):
        if (value <= entry) if not short else (value >= entry):
            where = "위" if short else "아래"
            blocked.append(f"{name}({value})이 진입가({entry}) {where}다 — 이익이 날 수 없다")
    if (target - first) * sign < 0:
        blocked.append(f"최종 익절({target})이 1차 익절({first})보다 앞이다 — 순서가 뒤집혔다")

    reward = (first - entry) * sign
    rr = reward / risk if reward > 0 and risk > 0 else Decimal(0)
    # 🔴 비용은 **R 단위**로 들어간다 — 손절폭을 같이 넘긴다. 가격 대비로 넣으면
    #    손절이 좁을수록 필요 승률을 **과소평가**한다 (`required_win_rate` 참고).
    need = required_win_rate(rr, round_trip, risk / entry) if rr > 0 else Decimal(100)
    # 🔴 **산수는 판단이 아니다.** 필요 승률이 100% 를 넘으면 어떤 실력으로도 못 갚는다 —
    #    5m 단독 진입을 폐기한 것이 정확히 이 계산이었다 (필요 승률 100.7~100.9%).
    if need >= 100:
        blocked.append(
            f"필요 승률 {need:.1f}% — 손절폭 {stop_pct:.2f}% 에 왕복 비용 "
            f"{round_trip * 100:.3f}% 를 넣으면 100% 넘게 맞아야 한다. "
            "산술적으로 이길 수 없다"
        )
    elif need >= 50:
        warnings.append(f"필요 승률 {need:.1f}% — 절반 넘게 맞아야 본전이다 (RR {rr:.2f})")

    return Confirmed(
        ok=not blocked,
        stop=confirmed,
        moved=moved,
        blocked=tuple(blocked),
        warnings=tuple(warnings),
        beta=beta,
        liq_pct=liq_pct,
        stop_pct=stop_pct,
        rr=rr,
        need_pct=need,
    )
