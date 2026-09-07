"""**계획서 §0-3 체크리스트를 시험으로 만든다** (T151).

주석은 사람이 지켜야 하지만 시험은 코드가 지킨다. 계획서의 여섯 줄:

    [ ] 지표 계산에 미래 봉이 들어가지 않는가
    [ ] 리페인팅 지표를 쓰고 있지 않은가
    [ ] 신호 봉 종가에 체결하고 있지 않은가                    ← Stage 0 에서 잠근다
    [ ] 정규화·스케일링을 전체 기간 통계로 하고 있지 않은가
    [ ] 스윙 고저 판정에 확정 안 된 봉을 쓰고 있지 않은가
    [ ] 상위 TF 봉을 미완성 상태에서 완성된 것으로 쓰고 있지 않은가  ← Stage 0 에서 잠근다

## ⚠️ 지금 잠글 수 있는 것은 둘뿐이다

나머지 넷은 **지표가 아직 없어서** 잠글 대상이 없다. 그것을 *"통과"* 로 적으면
체크리스트가 거짓말을 한다.

⇒ 대신 **새 모듈이 들어오는 순간 걸리게** 해 둔다. `discovery/` 에 파일을 하나
  더하면 아래 `KNOWN` 이 어긋나고, 그때 이 파일이 *"이 모듈의 미래 참조 시험을
  써라"* 라고 말한다. 체크리스트가 사람의 기억이 아니라 **CI 에 산다**.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.clock import Moment, align, is_confirmed
from updown.orchestration.discovery.fill import Entry, NoEntry, Plan, enter
from updown.orchestration.walkforward.ledger import Direction

DISCOVERY = Path("src/updown/orchestration/discovery")

KNOWN = {
    "__init__.py",
    "clock.py",
    "combine.py",
    "cluster.py",
    "frames.py",
    "metrics.py",
    "parameters.py",
    "robust.py",
    "scan.py",
    "states.py",
    "stats.py",
    "tally.py",
    "hygiene.py",
    "independence.py",
    "costs.py",
    "fill.py",
    "liquidation.py",
    # ⭐ 봉을 안 읽는다. 위험은 **날짜 짝짓기**이고 그 시험은
    #    tests/test_discovery_benchmark.py 에 있다 (T159 §1-A).
    "benchmark.py",
    # ⭐ 200일선과 기울기가 앞을 보면 국면별 성적이 통째로 거짓이 된다. 그 시험은
    #    tests/test_discovery_regime.py 에 있다 — 잘라서 비교한다 (T159 §1-G).
    "regime.py",
    # ⭐ 미래 참조가 아니라 **조용한 낡음**이 위험이다. 그 시험은
    #    tests/test_discovery_cache.py 에 있다 (오더 4).
    "cache.py",
    # ⭐ 방아쇠 시각만 보고 자리를 정한다 — 가격을 안 본다. 그 시험은
    #    tests/test_discovery_solo.py 에 있다 (오더 1-D).
    "solo.py",
    # ⭐ 미래를 보는 것이 **목적**인 모듈이다. 그 시험은
    #    `test_discovery_signals.TestEverySignalIsCausal` 에 있다 —
    #    여덟 가짜가 전부 잡히는지 확인한다 (T158 §0-B).
    "cheats.py",
}
"""지금까지 미래 참조 검토를 마친 모듈들.

🔴 여기 없는 파일이 생기면 이 시험이 깨진다. **고치는 방법은 이름을 더하는 것이
아니라, 그 모듈의 미래 참조 시험을 먼저 쓰는 것**이다.
"""


class TestChecklistItem3:
    """*"신호 봉 종가에 체결하고 있지 않은가"*."""

    def test_no_fill_can_land_on_the_signal_bar(self) -> None:
        """🔴 시장가든 지정가든, 어떤 인자를 줘도 신호 봉에는 안 들어간다."""
        plan_market = Plan(direction=Direction.LONG, stop=90.0, target=110.0, leverage=5.0)
        plan_limit = Plan(
            direction=Direction.LONG, stop=90.0, target=110.0, leverage=5.0, limit=99.0
        )
        open_ = [100.0, 100.0, 100.0, 100.0]
        high = [105.0, 105.0, 105.0, 105.0]
        low = [95.0, 95.0, 95.0, 95.0]

        for signal in range(len(open_) - 1):
            for plan in (plan_market, plan_limit):
                got = enter(open_, high, low, signal, plan, queue_miss=0.0)
                if isinstance(got, Entry):
                    assert got.index > signal, f"{plan.limit=} · {signal=}"

    def test_a_signal_on_the_last_bar_cannot_trade(self) -> None:
        plan = Plan(direction=Direction.LONG, stop=90.0, target=110.0, leverage=5.0)
        assert enter([100.0], [105.0], [95.0], 0, plan) is NoEntry.NO_BAR


class TestChecklistItem6:
    """*"상위 TF 봉을 미완성 상태에서 완성된 것으로 쓰고 있지 않은가"*.

    계획서가 **최다 발생 오류**라고 적어 둔 항목이다.
    """

    @pytest.mark.parametrize(
        ("lower", "higher"),
        [
            (Timeframe.M1, Timeframe.M15),
            (Timeframe.M1, Timeframe.H4),
            (Timeframe.M5, Timeframe.H1),
            (Timeframe.M15, Timeframe.D1),
            (Timeframe.H1, Timeframe.H4),
        ],
    )
    def test_every_pair_only_ever_reads_closed_bars(
        self, lower: Timeframe, higher: Timeframe
    ) -> None:
        """🔴 격자의 **모든 칸**에서 성립해야 한다 — 한 칸만 새도 그 칸만 이긴다."""
        start = datetime(2026, 8, 30, tzinfo=UTC)
        lows = [start + timedelta(minutes=7) * i for i in range(300)]
        highs = [start + timedelta(minutes=13) * i for i in range(300)]
        # 축과 무관한 임의 간격을 일부러 쓴다 — 경계가 딱 맞아떨어질 때만 맞는
        # 구현을 잡기 위해서다.
        #
        # ⚠️ **시가 시점**으로 본다. 종가 시점으로 보려면 이 시험이 하위 봉 간격을
        #    다시 계산해야 하고, 그러면 구현과 같은 산수를 시험이 또 하게 된다 —
        #    같이 틀리면 같이 통과한다.
        got = align(lows, lower, highs, higher, moment=Moment.OPEN)

        for index, ts in enumerate(lows):
            picked = got[index]
            if picked is None:
                assert not is_confirmed(highs[0], higher, at=ts)
                continue
            assert is_confirmed(highs[picked], higher, at=ts)
            if picked + 1 < len(highs):
                assert not is_confirmed(highs[picked + 1], higher, at=ts), (
                    "하나 더 읽을 수 있는데 안 읽었다면 그것도 버그다"
                )


class TestTheChecklistCannotRot:
    def test_every_discovery_module_has_been_reviewed(self) -> None:
        """🔴 새 모듈이 검토 없이 들어오는 것을 막는다.

        나머지 네 항목(지표·리페인팅·정규화·스윙)은 잠글 대상이 아직 없다. 지표가
        들어오는 순간 여기서 걸리고, 그때 그 항목들의 시험을 쓴다.
        """
        found = {path.name for path in DISCOVERY.glob("*.py")}
        new = found - KNOWN
        assert not new, (
            f"미래 참조 검토를 안 거친 모듈이 있다: {sorted(new)}."
            " KNOWN 에 이름만 더하지 말고, 그 모듈의 시험을 먼저 쓴다"
        )
        assert not KNOWN - found, f"사라진 모듈이 KNOWN 에 남아 있다: {sorted(KNOWN - found)}"
