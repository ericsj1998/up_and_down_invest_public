"""펀드의 진입 문 — 세션들의 사실을 모아 decision 의 포트폴리오 규칙에 묻는다 (T279 P3).

세션 하나는 다른 종목을 모른다. 동시 보유 상한(§23)과 같은 날 연속 손절 정지(§24)는 **펀드
전체**의 상태라, 펀드가 세션들을 묶을 때 이 문을 만들어 각 세션의 `entry_gate` 에 끼운다.
세션은 사기 직전 `blocks(at)` 만 부른다. 판단은 `decision.portfolio_rules` 가 하고 여기서는
포트에서 숫자를 모아 넘기기만 한다(orchestration 입주 조건).

결정론(규칙 #5): 원장의 기록만 읽는다 — 별도 카운터가 없어 재시작·되읽기에도 어긋나지 않는다.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from updown.analysis.playbook.types import BrakeRelease, ClusterRiskCap, EntryRiskGuard
from updown.common.logging.setup import get_logger
from updown.decision.portfolio_rules import (
    Grant,
    cluster_risk_room,
    day_halted,
    drawdown_scale,
    notional_room,
    peer_crowded,
    slot_free,
    stop_past_slot_margin,
)

_logger = get_logger("rebalancer.gate")


class PositionPort(Protocol):
    """문이 세션에 대고 묻는 두 가지 — 열린 수, 청산 목록. `SessionBridge` 가 구현한다."""

    def open_count(self) -> int:
        """지금 체결돼 보유 중인 매매 수.

        Returns:
            보유 중 기록 수.
        """
        ...

    def exits(self) -> list[tuple[datetime, bool]]:
        """확정된 청산 `(청산 시각, 손절이었나)`.

        Returns:
            시스템 매매의 닫힌 기록.
        """
        ...

    def open_exposure(self) -> Decimal:
        """보유 중 매매의 노출(명목/증거금 · 기록 leverage) 합.

        Returns:
            열린 기록의 leverage 합. 없으면 0.
        """
        ...


@runtime_checkable
class BreadthAware(Protocol):
    """**시장 전체 돌파**를 셀 수 있는 포트 (T289). 없는 포트는 폭에 0 을 보탠다.

    `PositionPort` 와 따로 둔 이유: 폭은 선택 기능이다. 필수 프로토콜에 넣으면 폭을 안 쓰는
    펀드·시험용 포트까지 전부 이 메서드를 가져야 한다(`LeverageAware` 와 같은 패턴).
    """

    def band_breaks(self, at: datetime) -> int:
        """직전 **3개 마감 봉** 안에 종가가 BB(20,2) 상단 밖에서 마감했으면 1, 아니면 0.

        Args:
            at: 진입하려는 봉의 시각(UTC).

        Returns:
            포트가 든 종목 수만큼의 합 — 종목 하나짜리 세션이면 0 또는 1.
        """
        ...


@runtime_checkable
class EntryLogged(Protocol):
    """시스템 진입 시각을 줄 수 있는 포트 (452차 · 신규 진입 수 상한의 입력).

    `PositionPort` 와 따로 둔 이유는 `BreadthAware` 와 같다 — 상한을 안 쓰는 포트까지
    강제하지 않는다.
    """

    def entries(self) -> list[datetime] | None:
        """취소 안 된 시스템 매매의 주문 시각(`placed_at`) — 열린 것 · 닫힌 것 모두. 모르면 None."""
        ...


@runtime_checkable
class EntryRiskLogged(Protocol):
    """시스템 진입의 크기와 손절 폭을 줄 수 있는 포트 (T1004 · 같은 때 든 진입의 위험 합 입력).

    `EntryLogged` 와 따로 둔 이유는 같다 — 이 한도를 안 쓰는 포트까지 강제하지 않는다.
    """

    def entry_risks(self) -> list[tuple[datetime, Decimal, Decimal]] | None:
        """취소 안 된 시스템 매매의 (주문 시각, 허용 노출, 처음 손절 폭 %) — 모르면 None."""
        ...


@runtime_checkable
class BreadthTimed(Protocol):
    """폭을 세는 축의 **마지막으로 받은 마감 봉 시각**을 말해 줄 수 있는 포트 — 관측 전용."""

    def breadth_bar_at(self) -> datetime | None:
        """마지막으로 받은 마감 봉의 시작 시각. 폭을 안 세거나 봉이 없으면 None."""
        ...


@runtime_checkable
class ReleaseAware(Protocol):
    """브레이크 풀기에 쓰는 **시장 폭**을 줄 수 있는 포트 (T985). 못 주는 포트는 모름으로 친다."""

    def release_breadth(self) -> Decimal | None:
        """전체 종목 가운데 이평 위 비율(마지막 닫힌 UTC 날까지 · 러너 주입) — 모르면 None."""
        ...


@runtime_checkable
class OpenBefore(Protocol):
    """**이 시각 앞서** 연 보유 수를 줄 수 있는 포트 (T400 · 혼합 3.3 숏 열림 키우기)."""

    def open_count_before(self, at: datetime) -> int:
        """`at` 보다 먼저 연(같은 시각 제외) 보유 중 매매 수."""
        ...


def open_before(port: PositionPort, at: datetime) -> int:
    """`at` 앞서 연 보유 수 — 시각을 못 주는 포트는 0.

    키우기는 리스크 증가라 모르면 안 키운다(#8-1).
    """
    return port.open_count_before(at) if isinstance(port, OpenBefore) else 0


@dataclass(slots=True)
class SlotGate:
    """P3 진입 문 — 자리(slot)가 다 찼거나 그날 연속 손절 정지면 막는다.

    Attributes:
        ports: `{종목: 포트}` — 펀드가 소유한 세션들. 펀드가 종목을 넣고 빼면 같은 매핑을 본다.
        slots: 동시 보유 상한(0 = 없음).
        halt_after_stops: 같은 날 연속 손절 문턱(0 = 없음).
    """

    ports: Mapping[str, PositionPort]
    slots: int = 0
    halt_after_stops: int = 0
    notional_cap: Decimal | None = None
    """**총 명목 상한**(93차 V2 · 자본 배수). 열린 노출 합 + 새 자리 노출 을 자리 수로 나눈 값
    (= 명목/자본)이 이 값을 넘는 진입은 막는다. None = 없음. 급락 갭에서 계좌가 한 번에 끝나는
    날을 없애는 손잡이(파산 4.2 → 0.5%)."""

    notional_fit: bool = False
    """상한에 걸릴 때 **남은 여유만큼 줄여서** 진입할지 (T286 · A 구성 · 2026-09-19).
    False = 지금까지처럼 그 진입을 통째로 건너뛴다. `notional_cap` 이 있어야 뜻이 있다."""

    min_grant: Decimal = Decimal(0)
    """줄여서 진입할 때의 **허용 하한** — 남은 여유가 이보다 작으면 줄이지 않고 건너뛴다.
    연구 걸음의 `min_room = 0.25 x 선언 배율`. 0 이면 하한 없음.
    ⚠️ 하한이 없으면 거래소 최소 주문에 못 미치는 부스러기 진입이 생긴다."""

    drawdown: Callable[[], Decimal] | None = None
    """지금 **고점 대비 낙폭**(0~1)을 주는 함수 — 펀드가 끼운다. None = 브레이크 없음.
    출처는 펀드 장부의 TWR 지수 낙폭이라 **입출금 중립**이다(입금이 낙폭을 메우지 않는다)."""

    brake_at: Decimal = Decimal(0)
    """낙폭 브레이크 문턱(0.12 = 12% · 144차에서 1% 단위로 쓸어 고른 고원의 가운데). 0 = 없음."""

    brake_scale: Decimal = Decimal(1)
    """브레이크가 걸렸을 때 신규 진입에 곱할 배수(0.5). 걸려도 건너뛰지 않고 **크기만** 줄인다."""

    brake_release: BrakeRelease | None = None
    """브레이크를 **그 진입에만** 푸는 조건(T985) — None = 없음.

    동시 돌파 폭(`breadth(at)`)이 넓고 시장 폭(포트의 `release_breadth()`)이 좁으면 그 진입은
    브레이크 배수를 안 맞는다. 🔴 풀기는 위험을 늘리는 쪽이라 **시장 폭을 모르면 안 푼다**(#8-1)."""

    brake_releases: int = 0
    """브레이크를 풀어 준 진입 수 — **관측 전용**(판정에 안 쓴다 · §1-0s 관측 규약)."""

    halt_dd_at: Decimal = Decimal(0)
    """펀드 낙폭이 이 값 **이상**이면 신규 진입을 **막는다** — 0 = 없음.

    T304 #1 · 혼합 2.0.0 MACD 다리 · 311차.

    브레이크(크기만 줄임)와 다른 장치다 — 옆 다리를 펀드 낙폭 동안 통째로 끈다. 연구(`t296_wave62`
    `third_dd_mult=0`)와 같은 `>=` 다. `drawdown` 이 있어야 뜻이 있다."""

    breadth_min: int = 0
    """**시장 전체 돌파**로 보는 폭의 문턱 (T289 · 177차 등록값 4). 0 = 조건부 상한 없음.

    폭 = 포트들의 `band_breaks(at)` 합 = 직전 3개 마감 봉 안에 상단 밖에서 마감한 종목 수
    (신호를 낸 종목 자신 포함). 진입 시점에 아는 값이다."""

    breadth_cap: Decimal | None = None
    """폭이 `breadth_min` 이상일 때 **그 진입에만** 쓰는 총 명목 상한(177차 등록값 3.0).

    🔴 왜: 여러 종목이 같이 밴드를 뚫는 순간 `notional_cap` 이 차서 뒤쪽 신호가 통째로 버려진다 —
    그런데 그 자리의 건당 EV 가 가장 높다(176차). 크기를 키우거나 혼자 돌파를 거르는 것은 세 창 모두
    ⛔ 였고, **상한만** 올리는 것이 통과했다(177~179차 · 급락 주입 파산 0.07%).
    ⚠️ `notional_cap` 보다 작으면 시장 전체 돌파에서 오히려 조인다 — 선언 파서가 막는다."""

    entry_limit: int = 0
    """직전 `entry_window_hours` 시간 안에 허용하는 **신규 진입 수**(452차 · 펀드 전체 · 0 = 없음).

    🔴 왜: 같은 날 여러 알트에 동시에 난 MACD 롱은 결과가 같이 움직여(24시간 안 쌍 상관
    +0.31) 여섯 자리가 독립 베팅 2.4 개 몫이다 — 크기는 6 배를 싣고 분산은 2.4 배만 얻는다.
    연구 원장에서 1건으로 묶자 평가 MDD 41.7 → 36.8%(k 1 · 2 · 3 · 절반 모두 통과).
    센 것 = 포트들의 `entries()`(원장 기록 · 취소 제외) — 별도 카운터가 없어 재시작해도 같다."""

    entry_window_hours: int = 24
    """`entry_limit` 의 창(시간)."""

    entry_cap: Decimal | None = None
    """한 건 **처음 노출**의 상한(명목 ÷ 자리 예산 · 452차 C75) — 넘으면 이 값으로 줄인다.

    None = 없음.

    브레이크 뒤 · 총 명목 여유 앞에 건다(연구 원장이 낙폭 줄임 뒤의 크기에 상한을 걸었다).
    불타기(`grant_add`)엔 안 건다 — 추가는 처음 실제 노출의 비율이라 이미 같이 줄어 있다."""

    peer_ports: Mapping[str, PositionPort] | None = None
    """**짝 다리의 눈으로 본** 펀드 포트들 (512차 N4) — `peer_open_max` 가 센다. None = 없음."""

    peer_open_max: int = 0
    """짝 다리 보유가 이 값 **이상**이면 이 다리는 새로 안 든다(512차 N4 · 0 = 없음).

    원장의 기록만 센다(`open_count`) — 별도 카운터가 없어 재시작해도 같다."""

    boost_ports: tuple[Mapping[str, PositionPort], ...] = ()
    """**짝 다리들의 눈으로 본** 펀드 포트들 (T400 · 혼합 3.3) — `boost_open_min` 이 센다.

    비면 없음."""

    boost_open_min: int = 0
    """짝 다리들 보유(이 진입 **앞서** 연 것) 합 문턱 — 넘으면 신규 진입 x `boost_mult`(0 = 없음).

    T400 BS15: 돌파 롱 진입 앞서 삼각 숏 · MACD 숏이 하나라도 열려 있으면 x1.5.
    🔴 같은 시각에 든 짝은 안 센다(`open_count_before`) — 연구 원장은 같은 시각이면 롱을 먼저
    처리했다(판 963 미래 참조 점검). 브레이크 · 한 건 상한 · 총 명목 여유 **앞에서** 곱한다
    (연구 원장 순서 — 크기를 정한 뒤 문이 자른다)."""

    boost_mult: Decimal = Decimal(1)
    """짝 다리 보유 문턱을 넘었을 때 신규 진입에 곱할 배수(1.5). 불타기(`grant_add`)엔 안 건다."""

    risk_guard: EntryRiskGuard | None = None
    """손절 폭이 허용 크기의 자리 증거금 청산 거리 이상이면 그 진입을 막는다 (T1004 · None = 없음).

    크기를 다 정한 **뒤**(브레이크 · 상한 · 총 명목 여유 · `cluster_risk` 뒤)의 허용 크기로 잰다 —
    줄어든 크기는 청산 거리가 멀어져 통과할 수 있다. 손절 폭을 넘긴 물음(`stop_pct`)에만 건다.
    불타기(`grant_add`)엔 안 건다."""

    cluster_risk: ClusterRiskCap | None = None
    """같은 때 든 진입들의 손절 기준 위험 합(계좌 %) 상한 (T1004 · None = 없음).

    위험 = 허용 크기 ÷ `slots` x 손절 폭 %. 창 안 `min_n` 번째 이상 진입이 상한을 넘기면 남은
    몫까지 줄이고(`cluster_risk`), `min_grant` 보다 작아지면 막는다. `slots` 가 0 이면 위험을 잴
    분모가 없어 만들 때 예외다. 불타기(`grant_add`)엔 안 건다."""

    risk_guard_blocks: int = 0
    """`risk_guard` 가 막은 횟수 — 관측 전용."""

    cluster_risk_fits: int = 0
    """`cluster_risk` 가 줄인 횟수 — 관측 전용."""

    cluster_risk_blocks: int = 0
    """`cluster_risk` 가 막은 횟수(남은 몫이 최소 진입 미만) — 관측 전용."""

    def __post_init__(self) -> None:
        """선언끼리 어긋나면 만들 때 멈춘다 — 조용히 꺼진 한도를 두지 않는다 (#8)."""
        if self.cluster_risk is not None and self.slots <= 0:
            raise ValueError("cluster_risk 는 slots 가 있어야 한다 — 위험(계좌 %)의 분모다")

    def blocks(self, at: datetime, exposure: Decimal = Decimal(0)) -> str | None:
        """지금 새 자리를 열면 안 되는 이유 — 없으면 None.

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 노출(명목/증거금). 총 명목 상한 판단에만 쓴다.

        Returns:
            `"slots"`(자리 없음) · `"notional"`(총 명목 상한) · `"day_halt"`(그날 정지) ·
            `"fund_dd"`(펀드 낙폭 동안 이 다리 끔 · 크기를 넘긴 물음에서만) ·
            `"peer_open"`(짝 다리가 몰려 있음 · 512차 N4) ·
            None(열어도 됨).

        Note:
            `grant` 의 사유만 꺼내 쓴다 — 두 벌로 두면 조용히 갈라진다. 줄여서 진입이 켜져 있으면
            상한에 걸려도 **막지 않으므로** 여기서도 None 이다(크기가 줄 뿐이다).
        """
        return self.grant(at, exposure).blocked

    def grant(self, at: datetime, exposure: Decimal, *, stop_pct: Decimal | None = None) -> Grant:
        """이 크기로 열어도 되는지 묻고 **허용 크기**를 돌려준다 (T286).

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 **실제** 노출(명목/증거금 = 배율 x 크기 승수).
            stop_pct: 계획 손절 폭(진입가의 %) — 넘기면 `cluster_risk` · `risk_guard` 를
                크기를 정한 뒤에 건다(T1004). None(기본)이면 그 둘을 안 본다 — 손절을 모르는
                물음(`blocks` · 연구 걸음)은 예전과 같다.

        Returns:
            `Grant` — 허용 크기 · 막은 사유 · 줄인 장치.

        Note:
            순서는 연구 걸음 `t279_leaderboard_bn.walk` 과 같다 — **자리·정지로 거르고 → 낙폭
            브레이크로 줄이고 → 총 명목 여유로 자른다**. 브레이크가 먼저인 이유는 줄어든 크기가
            상한에 안 걸릴 수 있기 때문이고, 순서를 뒤집으면 다른 매매법이 된다.

            ⚠️ 호출자는 **실제로 쓸 노출**을 넘겨야 한다. 예전 `blocks` 호출부는 선언 배율을
            넘기고 실제 노출은 뒤에서 따로 계산해 둘이 어긋났다(위험 기반 사이징일 때).
        """
        open_count = sum(port.open_count() for port in self.ports.values())
        if not slot_free(open_count, self.slots):
            return Grant(Decimal(0), "slots")
        exits = [item for port in self.ports.values() for item in port.exits()]
        if day_halted(exits, at, self.halt_after_stops):
            return Grant(Decimal(0), "day_halt")
        if self.entry_limit > 0:
            recent = self._recent_entries(at)
            if recent is None:
                # 🔴 진입 시각을 못 세는 포트가 섞였다 — 상한을 지킬 수 없으면 막는다.
                #    신규 진입은 리스크 증가 행동이고 분류가 불분명하면 기본값은 보류다(#8-1).
                return Grant(Decimal(0), "entry_log")
            if recent >= self.entry_limit:
                return Grant(Decimal(0), "entry_limit")
        if self.peer_ports is not None and self.peer_open_max > 0:
            peer_open = sum(port.open_count() for port in self.peer_ports.values())
            if peer_crowded(peer_open, self.peer_open_max):
                return Grant(Decimal(0), "peer_open")
        boost = self._boost(at) if exposure > 0 else None
        if boost is None:
            given = self._sized(at, exposure, cap=self.entry_cap)
        else:
            given = self._sized(at, exposure * boost, cap=self.entry_cap)
            if given.blocked is None:
                # §1-0s — 키운 것도 장치로 적는다(`gate:fit:boost`) · 브레이크 · 상한이 뒤에서
                #    다시 줄인다.
                given = Grant(
                    given.size, None, "boost" if given.shrunk is None else f"boost+{given.shrunk}"
                )
        if stop_pct is None or given.blocked is not None or given.size <= 0:
            return given
        return self._risk_checked(at, given, stop_pct)

    def _risk_checked(self, at: datetime, given: Grant, stop_pct: Decimal) -> Grant:
        """크기를 정한 뒤 손절 기준 위험으로 한 번 더 본다 (T1004) — 선언이 없으면 그대로.

        Args:
            at: 진입하려는 봉의 시각(UTC).
            given: 자리 · 정지 · 브레이크 · 상한 · 총 명목 여유를 지난 허용 크기.
            stop_pct: 계획 손절 폭(진입가의 %).

        Returns:
            그대로 · 줄인 것(`cluster_risk`) · 막은 것(`cluster_risk` · `risk_guard` · `risk_log`).

        Note:
            순서는 **위험 합 상한(줄임) → 청산 거리 문(막음)** 이다 — 줄어든 크기는 청산 거리가
            멀어지므로 뒤집으면 다른 규칙이 된다. 측정(`t296_wave62.run`)도 이 순서다.
        """
        size = given.size
        shrunk = given.shrunk
        rule = self.cluster_risk
        if rule is not None:
            seen = self._cluster_used(at, rule.window_minutes)
            if seen is None:
                # 🔴 앞선 진입의 위험을 못 세는 포트가 섞였다 — 한도를 지킬 수 없으면 막는다(#8-1).
                return Grant(Decimal(0), "risk_log")
            used, count = seen
            room = cluster_risk_room(
                size,
                stop_pct,
                self.slots,
                used=used,
                count=count,
                cap_pct=rule.cap_pct,
                min_n=rule.min_n,
            )
            if room is not None:
                _logger.info(
                    "fund_gate_cluster_risk",
                    payload={
                        "at": at.isoformat(),
                        "used_pct": str(used),
                        "count": count,
                        "stop_pct": str(stop_pct),
                        "want": str(size),
                        "room": str(room),
                        "cap_pct": str(rule.cap_pct),
                    },
                )
                if room <= 0 or room < self.min_grant:
                    self.cluster_risk_blocks += 1
                    return Grant(Decimal(0), "cluster_risk")
                self.cluster_risk_fits += 1
                size = room
                shrunk = "cluster_risk" if shrunk is None else f"{shrunk}+cluster_risk"
        guard = self.risk_guard
        if guard is not None and stop_past_slot_margin(size, stop_pct, guard.maint_pct):
            self.risk_guard_blocks += 1
            _logger.info(
                "fund_gate_risk_guard",
                payload={
                    "at": at.isoformat(),
                    "size": str(size),
                    "stop_pct": str(stop_pct),
                    "maint_pct": str(guard.maint_pct),
                },
            )
            return Grant(Decimal(0), "risk_guard")
        if size == given.size:
            return given
        return Grant(size, None, shrunk)

    def _cluster_used(self, at: datetime, window_minutes: int) -> tuple[Decimal, int] | None:
        """직전 창(`at` · 창 시작 둘 다 포함) 안 진입들의 위험 합(계좌 %)과 건수.

        Args:
            at: 진입하려는 봉의 시각(UTC).
            window_minutes: 창 길이(분).

        Returns:
            (위험 합, 건수). 진입 위험을 못 주는 포트가 하나라도 있으면 None(모름).

        Note:
            창 시작을 **포함**한다 — 측정(`t296_wave62` 의 `x_[0] >= lo_r`)과 같다. 30 분 창이면
            반 시간 앞 조기 진입이 정시 진입의 창에 든다.
        """
        since = at - timedelta(minutes=window_minutes)
        used = Decimal(0)
        count = 0
        for port in self.ports.values():
            got = port.entry_risks() if isinstance(port, EntryRiskLogged) else None
            if got is None:
                return None
            for placed, size, stop in got:
                if since <= placed <= at:
                    used += size / Decimal(self.slots) * stop
                    count += 1
        return used, count

    def _boost(self, at: datetime) -> Decimal | None:
        """짝 다리들 보유(`at` 앞서 연 것)가 문턱 이상이면 키울 배수 — 아니면 None (T400)."""
        if self.boost_open_min <= 0 or not self.boost_ports or self.boost_mult == 1:
            return None
        held = sum(open_before(port, at) for ports in self.boost_ports for port in ports.values())
        return self.boost_mult if held >= self.boost_open_min else None

    def _recent_entries(self, at: datetime) -> int | None:
        """직전 `entry_window_hours` 시간(`at` 포함 · 창 시작 제외) 안의 신규 진입 수.

        Args:
            at: 진입하려는 봉의 시각(UTC).

        Returns:
            진입 수. 진입 시각을 못 주는 포트가 하나라도 있으면 None(모름).
        """
        since = at - timedelta(hours=self.entry_window_hours)
        total = 0
        for port in self.ports.values():
            got = port.entries() if isinstance(port, EntryLogged) else None
            if got is None:
                return None
            total += sum(1 for placed in got if since < placed <= at)
        return total

    def grant_add(self, at: datetime, exposure: Decimal) -> Grant:
        """열린 매매에 **더 싣는**(불타기) 크기를 묻는다 (T308 · 368 ~ 371차).

        Args:
            at: 추가하려는 시각(UTC) — 확인 봉이 마감된 시각.
            exposure: 더 실으려는 노출(명목/자리 예산 = 처음 실제 노출 x 추가 비율).

        Returns:
            `Grant` — 허용 크기 · 막은 사유 · 줄인 장치. 크기가 0 이하인 물음은 `"size"` 로 막는다.

        Note:
            🔴 **자리와 같은 날 정지는 안 본다** — 추가는 새 자리가 아니라 이미 연 자리의 크기다.
            측정(368 ~ 371차 원장 `t296_wave62.run(long_adds=…)`)이 그렇게 쟀고, 다르게 하면
            다른 매매법이 된다. 펀드 낙폭 끔 · 브레이크 · 총 명목 여유는 진입과
            **같은 순서 · 같은 자**다.
            증거금 여유는 여기서 안 본다 — 거래소 지갑의 사실이라 러너가 주문 직전에 본다.
        """
        if exposure <= 0:
            return Grant(Decimal(0), "size")
        return self._sized(at, exposure)

    def _sized(self, at: datetime, exposure: Decimal, cap: Decimal | None = None) -> Grant:
        """펀드 낙폭 끔 → 브레이크 → 한 건 상한 → 총 명목 여유 — 진입 · 불타기가 같이 쓰는 크기.

        Args:
            at: 묻는 시각(UTC). 조건부 상한의 폭을 이 시각으로 센다.
            exposure: 요청 노출. 0 이면 "자리 · 정지만 보자" 는 `blocks(at)` 물음이다.
            cap: 한 건 처음 노출 상한(`entry_cap` · 452차) — 신규 진입만 넘긴다(불타기는 None).

        Returns:
            `Grant`.
        """
        if (
            exposure > 0
            and self.drawdown is not None
            and self.halt_dd_at > 0
            and self.drawdown() >= self.halt_dd_at
        ):
            # T304 #1 — 펀드 낙폭 동안 이 다리를 끈다. `exposure > 0` 인 이유는 브레이크 흡수 검사와
            # 같다 — 크기 없이 묻는 `blocks(at)` 은 "자리·정지만 보자" 는 뜻이다.
            return Grant(Decimal(0), "fund_dd")
        want = exposure
        shrunk: list[str] = []
        if self.drawdown is not None and self.brake_at > 0:
            scale = drawdown_scale(self.drawdown(), self.brake_at, self.brake_scale)
            if scale != 1 and exposure > 0 and self._released(at):
                # T985 — 풀기 조건이 맞은 진입은 브레이크 배수를 안 맞는다(불타기도 같은 길).
                # 줄인 것이 아니므로 `shrunk` 에 적지 않고 따로 센다.
                self.brake_releases += 1
            elif scale != 1:
                want *= scale
                shrunk.append("brake")
        if exposure > 0 and want <= 0:
            # 브레이크 배수가 0 이면 **흡수 상태**가 된다(진입이 없으면 실현 잔고가 안 움직여
            # 고점을 영영 못 회복한다 · 143차). 선언 파서가 막지만 문에서도 막는다.
            # ⚠️ `exposure > 0` 조건이 있어야 한다 — 크기 없이(0) 묻는 `blocks(at)` 호출을
            #    브레이크로 막으면 안 된다(그 경로는 "자리·정지만 보자" 는 뜻이다).
            return Grant(Decimal(0), "brake")
        if cap is not None and want > cap:
            # 452차 C75 — 한 건 처음 노출 상한(브레이크 뒤 · 총 명목 여유 앞 · 연구 원장 순서).
            want = cap
            shrunk.append("entry_cap")
        held = sum((port.open_exposure() for port in self.ports.values()), Decimal(0))
        room = notional_room(held, self.slots, self._cap_at(at))
        if room is not None and want > room:
            if not self.notional_fit or room <= 0 or room < self.min_grant:
                return Grant(Decimal(0), "notional")
            want = room
            shrunk.append("notional")
        return Grant(want, None, "+".join(shrunk) if shrunk else None)

    def breadth(self, at: datetime) -> int:
        """지금의 **폭** — 직전 3개 마감 봉 안에 상단 밖에서 마감한 종목 수 (T289).

        Args:
            at: 진입하려는 봉의 시각(UTC).

        Returns:
            `BreadthAware` 포트들의 `band_breaks` 합. 폭을 못 세는 포트는 0 을 보탠다.
        """
        return sum(
            port.band_breaks(at) for port in self.ports.values() if isinstance(port, BreadthAware)
        )

    def _released(self, at: datetime) -> bool:
        """이 진입은 브레이크를 안 맞나 — 풀기 선언이 있고 조건 둘이 다 참일 때만 (T985).

        Args:
            at: 진입하려는 봉의 시각(UTC).

        Returns:
            풀면 True. 선언이 없거나 시장 폭을 아는 포트가 없으면 False(모름 = 안 푼다 · #8-1).

        Note:
            시장 폭은 다리의 판들이 같은 날 값을 받는다. 자정 앞뒤로 판마다 다른 날 값을 들고
            있으면 **가장 넓은 값**으로 본다(풀기에 불리한 쪽 = 위험을 덜 늘리는 쪽).
        """
        rule = self.brake_release
        if rule is None:
            return False
        known = [
            value
            for port in self.ports.values()
            if isinstance(port, ReleaseAware) and (value := port.release_breadth()) is not None
        ]
        if not known:
            return False
        breadth, market = self.breadth(at), max(known)
        freed = rule.releases(breadth, market)
        if freed:
            # 🔴 관측이 진입을 막으면 안 된다 — 로그를 적다 난 예외는 삼킨다(판정은 위에서 끝났다).
            with contextlib.suppress(Exception):
                _logger.info(
                    "fund_gate_brake_release",
                    payload={
                        "at": at.isoformat(),
                        "breadth": breadth,
                        "min": rule.breadth_min,
                        "market_breadth": str(market),
                        "below": str(rule.alt_breadth_below),
                    },
                )
        return freed

    def _cap_at(self, at: datetime) -> Decimal | None:
        """이 진입에 쓸 총 명목 상한 — 시장 전체 돌파면 `breadth_cap`, 아니면 `notional_cap`.

        Note:
            🔴 **센 폭을 적는다** (2026-09-21 · §1-0s 관측 규약). 폭은 형제 세션들이 받아 둔 봉에서
            나오는데, 묻는 순간 형제의 마지막 봉이 아직 안 도착했으면 폭이 **조용히 1 적게** 센다
            (상한이 3x 대신 2x 로 남는다). 얼마나 자주인지 잰 적이 없다 — 종목별 값과 마지막 봉
            시각을 같이 적어 두면 로그만으로 센다. 진입할 때만 불리므로 양은 적다.
        """
        if self.breadth_cap is None or self.breadth_min <= 0 or self.notional_cap is None:
            return self.notional_cap
        counts = {
            name: port.band_breaks(at)
            for name, port in self.ports.items()
            if isinstance(port, BreadthAware)
        }
        wide = sum(counts.values()) >= self.breadth_min
        # 🔴 관측이 진입을 막으면 안 된다 — 로그를 적다 난 예외는 삼킨다(판정은 위에서 끝났다).
        with contextlib.suppress(Exception):
            self._note_breadth(at, counts, wide=wide)
        return self.breadth_cap if wide else self.notional_cap

    def _note_breadth(self, at: datetime, counts: Mapping[str, int], *, wide: bool) -> None:
        """센 폭과 종목별 마지막 봉 시각을 로그로 남긴다 — 관측 전용."""
        if not any(isinstance(port, BreadthTimed) for port in self.ports.values()):
            # 연구 걸음·시험의 포트는 봉 시각을 모른다 — 수만 번 부르는 걸음에 로그를 쏟지 않는다.
            return
        _logger.info(
            "fund_gate_breadth",
            payload={
                "at": at.isoformat(),
                "breadth": sum(counts.values()),
                "min": self.breadth_min,
                "cap": str(self.breadth_cap if wide else self.notional_cap),
                "breaks": counts,
                "last_bar": {
                    name: None if (seen := port.breadth_bar_at()) is None else seen.isoformat()
                    for name, port in self.ports.items()
                    if isinstance(port, BreadthTimed)
                },
            },
        )


@runtime_checkable
class LegSource(Protocol):
    """다리 보기가 세션 다리에 묻는 것 — 귀속 키로 거른 사실들. `SessionBridge` 가 구현한다."""

    def open_count_of(self, leg: str) -> int:
        """그 다리(귀속 키)의 보유 중 매매 수."""
        ...

    def exits_of(self, leg: str) -> list[tuple[datetime, bool]]:
        """그 다리의 확정된 청산 `(청산 시각, 손절이었나)`."""
        ...

    def open_exposure_of(self, leg: str) -> Decimal:
        """그 다리의 보유 중 노출 합."""
        ...

    def band_breaks(self, at: datetime) -> int:
        """폭의 입력 — `BreadthAware` 와 같다."""
        ...


@runtime_checkable
class OpenBeforeSource(Protocol):
    """다리별 "이 시각 앞서 연 보유 수" 를 주는 세션 다리 (T400 · `SessionBridge`)."""

    def open_count_before_of(self, leg: str, at: datetime) -> int:
        """그 다리의 보유 중 매매 중 `at` 보다 먼저 연 것의 수."""
        ...


@runtime_checkable
class EntryLogSource(Protocol):
    """다리별 진입 시각을 줄 수 있는 세션 다리 (452차) — `SessionBridge` 가 구현한다."""

    def entries_of(self, leg: str) -> list[datetime]:
        """그 다리의 취소 안 된 시스템 매매 주문 시각."""
        ...


@runtime_checkable
class EntryRiskSource(Protocol):
    """다리별 진입 위험을 줄 수 있는 세션 다리 (T1004) — `SessionBridge` 가 구현한다."""

    def entry_risks_of(self, leg: str) -> list[tuple[datetime, Decimal, Decimal]]:
        """그 다리의 취소 안 된 시스템 매매의 (주문 시각, 허용 노출, 처음 손절 폭 %)."""
        ...


@dataclass(slots=True, frozen=True)
class LegPort:
    """세션 하나를 **다리 하나의 눈으로** 본 포트 (T291).

    한 세션에 두 다리(1H 롱 · 4H 숏)가 실리면 세션 전체의 열린 수·청산·노출은 두 다리가 섞인
    값이다. 다리의 문은 자기 매매만 세야 하므로(측정이 다리마다 자리 6 이었다) 귀속 키로 거른다.

    Attributes:
        source: 원본 세션 다리.
        leg: 이 다리의 귀속 키(`Playbook.attribution` = `TradeRecord.playbook`).
        in_scope: 이 종목이 그 다리의 종목 범위 안인가 — 밖이면 폭에 0 을 보탠다.
    """

    source: LegSource
    leg: str
    in_scope: bool = True

    def open_count(self) -> int:
        """이 다리의 보유 중 매매 수."""
        return self.source.open_count_of(self.leg)

    def exits(self) -> list[tuple[datetime, bool]]:
        """이 다리의 확정된 청산."""
        return self.source.exits_of(self.leg)

    def open_exposure(self) -> Decimal:
        """이 다리의 보유 중 노출 합."""
        return self.source.open_exposure_of(self.leg)

    def open_count_before(self, at: datetime) -> int:
        """이 다리의 보유 중 매매 중 `at` 앞서 연 것의 수 — 원본이 못 주면 0 (T400)."""
        source = self.source
        if isinstance(source, OpenBeforeSource):
            return source.open_count_before_of(self.leg, at)
        return 0

    def entries(self) -> list[datetime] | None:
        """이 다리의 진입 시각 — 원본이 못 주면 None(문이 모름으로 보고 막는다)."""
        source = self.source
        return source.entries_of(self.leg) if isinstance(source, EntryLogSource) else None

    def entry_risks(self) -> list[tuple[datetime, Decimal, Decimal]] | None:
        """이 다리의 진입 위험 — 원본이 못 주면 None(문이 모름으로 보고 막는다 · T1004)."""
        source = self.source
        return source.entry_risks_of(self.leg) if isinstance(source, EntryRiskSource) else None

    def band_breaks(self, at: datetime) -> int:
        """폭의 입력 — 다리의 종목 범위 밖이면 0."""
        return self.source.band_breaks(at) if self.in_scope else 0

    def breadth_bar_at(self) -> datetime | None:
        """마지막으로 받은 마감 봉 시각 — 범위 밖이거나 원본이 모르면 None (관측 전용)."""
        source = self.source
        if not self.in_scope or not isinstance(source, BreadthTimed):
            return None
        return source.breadth_bar_at()

    def release_breadth(self) -> Decimal | None:
        """브레이크 풀기의 시장 폭 — 범위 밖이거나 원본이 모르면 None (T985)."""
        source = self.source
        if not self.in_scope or not isinstance(source, ReleaseAware):
            return None
        return source.release_breadth()


@dataclass(slots=True)
class LegPorts(Mapping[str, PositionPort]):
    """펀드의 포트 매핑을 **다리 하나의 눈으로** 본 매핑 — 원본이 바뀌면 같이 바뀐다 (T291).

    Attributes:
        ports: 펀드 조정자의 `{종목: 포트}` — 복사하지 않는다(종목을 넣고 빼면 문도 따라간다).
        leg: 다리의 귀속 키.
        scope: 다리의 종목 범위.
    """

    ports: Mapping[str, object]
    leg: str
    scope: frozenset[str]

    def __getitem__(self, key: str) -> PositionPort:
        """그 종목을 이 다리의 눈으로 본 포트 — 다리별 사실을 못 주는 포트는 없는 것으로 친다."""
        port = self.ports[key]
        if not isinstance(port, LegSource):
            raise KeyError(key)
        return LegPort(port, self.leg, in_scope=key in self.scope)

    def __iter__(self) -> Iterator[str]:
        """다리별 사실을 줄 수 있는 포트의 종목들."""
        return (key for key, port in self.ports.items() if isinstance(port, LegSource))

    def __len__(self) -> int:
        """다리별 사실을 줄 수 있는 포트 수."""
        return sum(1 for _ in self)


@runtime_checkable
class LegAware(Protocol):
    """진입을 **낸 다리**를 알려 주면 그 다리의 문으로 답하는 문 (T291)."""

    def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        """그 다리의 문에 묻는다."""
        ...

    def grant_add_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        """그 다리의 문에 불타기 크기를 묻는다 (T308)."""
        ...


@dataclass(slots=True)
class LegGate:
    """다리마다 자기 문을 가진 펀드 문 (T291 · 이종 합성 매매법).

    Attributes:
        legs: `{귀속 키: 그 다리의 SlotGate}`.

    Note:
        🔴 **모르는 다리는 막는다**(`"leg"`). 신규 진입은 리스크 증가 행동이고 분류가 불분명하면
        기본값은 보류다(절대 규칙 #8-1). 다리를 모르고 묻는 `grant`/`blocks` 도 같은 이유로 막는다 —
        세션은 `LegAware` 를 알아보고 `grant_for` 로 묻는다.
    """

    legs: Mapping[str, SlotGate]

    def grant_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        """그 다리의 문에 묻는다 — 없는 다리면 막는다."""
        gate = self.legs.get(leg)
        if gate is None:
            return Grant(Decimal(0), "leg")
        return gate.grant(at, exposure)

    def grant_stop_for(
        self, leg: str, at: datetime, exposure: Decimal, stop_pct: Decimal | None
    ) -> Grant:
        """그 다리의 문에 **손절 폭과 함께** 묻는다 (T1004) — 없는 다리면 막는다.

        Args:
            leg: 진입을 낸 매매법의 귀속 키.
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 실제 노출.
            stop_pct: 계획 손절 폭(진입가의 %). 모르면 None.

        Returns:
            `Grant`. 그 다리에 위험 검사가 선언돼 있는데 손절 폭을 모르면 `"risk_stop"` 으로
            막는다 — 지킬 수 없는 한도는 보류다(#8-1). 선언이 없으면 `grant_for` 와 같다.
        """
        gate = self.legs.get(leg)
        if gate is None:
            return Grant(Decimal(0), "leg")
        if stop_pct is None:
            if gate.risk_guard is not None or gate.cluster_risk is not None:
                return Grant(Decimal(0), "risk_stop")
            return gate.grant(at, exposure)
        return gate.grant(at, exposure, stop_pct=stop_pct)

    def grant_add_for(self, leg: str, at: datetime, exposure: Decimal) -> Grant:
        """그 다리의 문에 불타기 크기를 묻는다 (T308) — 없는 다리면 막는다."""
        gate = self.legs.get(leg)
        if gate is None:
            return Grant(Decimal(0), "leg")
        return gate.grant_add(at, exposure)

    def grant(self, at: datetime, exposure: Decimal) -> Grant:
        """다리를 모르고 물으면 막는다."""
        _ = (at, exposure)
        return Grant(Decimal(0), "leg")

    def grant_add(self, at: datetime, exposure: Decimal) -> Grant:
        """다리를 모르고 불타기를 물으면 막는다 — 리스크 증가 · 분류 불명은 보류다(#8-1)."""
        _ = (at, exposure)
        return Grant(Decimal(0), "leg")

    def blocks(self, at: datetime, exposure: Decimal = Decimal(0)) -> str | None:
        """다리를 모르고 물으면 막는다."""
        _ = (at, exposure)
        return "leg"
