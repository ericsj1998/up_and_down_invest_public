"""라이브 세션을 `SessionPort` 로 감싼다 (T61 M1) — 조정자와 실제 세션의 접점.

매핑(T61)에서 확정: 예산은 `ledger.margin_budget`, 조정자가 읽는 것은 `realized()`(누적 실현 손익 ·
T285). `margin_budget` 은 **다음 진입 사이징에만** 쓰이고, 멤버 원장은 받은 몫(seed_cash)에서
걷으며 매매마다 건 증거금(`margin_used`)으로 세므로 예산 갱신이 과거 손익을 안 건드린다 (설계 B).
`equity()` 는 표시용으로 남는다 — 총자본 합산에는 더 이상 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from updown.analysis.indicators.bands import closed_above_upper
from updown.common.domain.instrument import Timeframe
from updown.orchestration.walkforward import Session
from updown.orchestration.walkforward.ledger import Actor, Outcome
from updown.orchestration.walkforward.sealed import SealBreachError


@dataclass(slots=True)
class SessionBridge:
    """`Session` 하나를 조정자가 다룰 수 있는 포트로. `SessionPort` 프로토콜을 구현한다.

    Attributes:
        session: 감쌀 라이브 세션.
        _last_trusted: 마지막으로 **신뢰할 수 있던** 평가금액 (격리 동결값 · 벽돌 2).
    """

    session: Session
    _last_trusted: Decimal | None = None
    _last_realized: Decimal | None = None
    breadth_bars: int = 0
    """폭을 셀 때 돌아볼 마감 봉 수 (T289). 0 = 이 세션은 폭을 안 센다(`band_breaks` 가 늘 0)."""
    breadth_frame: Timeframe | None = None
    """폭을 세는 시간축 — 매매법의 진입 축. None 이면 안 센다."""

    @property
    def symbol(self) -> str:
        """이 세션이 굴리는 종목."""
        return self.session.instrument.symbol

    def equity(self) -> Decimal:
        """지금 평가금액 — 신뢰할 수 있으면 원장 집계, 아니면 **마지막 신뢰값에 동결** (벽돌 2).

        Returns:
            평가금액. 원장이 거래소와 갈려 있으면 마지막 신뢰값.

        Note:
            🔴 **격리 (2026-09-01 · 사용자 신고).** 원장이 거래소와 갈리면(`reconciled`
            거짓 = 포지션 갈림 · `accounting_ok` 거짓 = 실현손익 부호 반대), 그 세션의
            평가금액은 **허구**다. 그대로 총자본·TWR·배분에 넣으면 없는 이익이 펀드
            헤드라인에 섞이고 배분까지 오염된다. 그래서 신뢰 가능할 때의 마지막 값에
            **동결**해 허구가 더 자라지 못하게 가둔다.

            ⚠️ **왜 거래소 실측 equity 를 직접 안 쓰나**: 공유 계정에서는 `available`
            (미투입 현금)을 세션별로 못 가른다 — 현금만 든 세션은 거래소 margin 이 0 이라
            실측 equity 가 0 으로 나와 **배정된 현금을 지운다**. 동결값은 그 현금을
            보존한다. 서브계정 격리(벽돌 4)가 들어오면 진짜 세션별 실측으로 바꾼다.

            ⭐ 재구성(벽돌 3)으로 원장이 실측에 맞으면 `accounting_ok`·`reconciled` 가
            참으로 돌아오고 동결이 풀려 **진실**에서 이어간다.
        """
        led = self.session.ledger
        trusted = self.session.reconciled and self.session.accounting_ok
        if trusted:
            value = led.equity
            self._last_trusted = value
            return value
        # 갈렸다 — 허구 실현을 안 넣는다. 우선순위:
        #   ① 거래소 실측으로 귀속된 실현손익이 있으면 원장 실현을 그것으로 **갈아끼운다**
        #      (재시작하며 이미 갈린 채로 떠도 실측을 반영 · 벽돌 3 write).
        #   ② 없으면 마지막 신뢰값에 동결.
        #   ③ 그것도 없으면 원장값을 최선으로 (검증할 근거가 아직 없다).
        if self.session.verified_realized is not None:
            # 원장 실현(앵커 이후) → 거래소 실측 실현으로 갈아끼운다.
            anchored = led.realized_cash - self.session.realized_anchor
            return led.equity - anchored + self.session.verified_realized
        return self._last_trusted if self._last_trusted is not None else led.equity

    def realized(self) -> Decimal:
        """누적 실현 손익(USDT) — 조정자가 총자본 증분을 셀 때의 입력 (T285).

        Returns:
            신뢰할 수 있으면 원장 `realized_cash`, 갈렸으면 거래소 실측으로 갈아끼운 값
            (앵커 + 실측),
            그것도 없으면 마지막 신뢰값에 동결 — `equity()` 와 같은 격리 규칙이다.

        Note:
            ⭐ 원장 실현은 펀드 멤버 걷기가 시드에서 시작하고 매매마다 건 증거금(`margin_used`)으로
            세므로 예산 변경·재기동에 흔들리지 않는다 — 그래서 증분이 총자본의 입력이 될 수 있다.
        """
        led = self.session.ledger
        trusted = self.session.reconciled and self.session.accounting_ok
        if trusted:
            value = led.realized_cash
            self._last_realized = value
            return value
        if self.session.verified_realized is not None:
            return self.session.realized_anchor + self.session.verified_realized
        return self._last_realized if self._last_realized is not None else led.realized_cash

    def set_budget(self, budget: Decimal) -> None:
        """다음 진입 예산을 갱신한다.

        Args:
            budget: 새 예산.

        Note:
            🔴 `margin_budget` 만 바꾼다 — `sizing_base` 가 이 값을 쓰고, 손익률은 `seed_cash`
            기준이라 오염되지 않는다. 열린 포지션 크기는 그대로다 (설계 B).
        """
        self.session.ledger.margin_budget = budget

    def open_count(self) -> int:
        """지금 **체결돼 보유 중**인 매매 수 (P3 동시 보유 상한의 입력).

        Returns:
            보유 중 기록 수. 대기(PENDING)는 아직 자리를 안 쓴 것이라 안 센다.
        """
        return sum(1 for item in self.session.ledger.records if item.outcome is Outcome.OPEN)

    def exits(self) -> list[tuple[datetime, bool]]:
        """확정된 청산 `(청산 시각, 손절이었나)` 목록 (P3 같은 날 연속 손절 정지의 입력).

        Returns:
            시스템 매매의 닫힌 기록. 사람 매매는 규칙의 표본이 아니라 뺀다
            (`_consec_loss_scale` 과 같다).
        """
        return [
            (item.closed_at, item.outcome is Outcome.STOP_LOSS)
            for item in self.session.ledger.closed
            if item.closed_at is not None and item.actor is Actor.SYSTEM
        ]

    def open_exposure(self) -> Decimal:
        """보유 중 매매의 노출(명목/증거금) 합 (총 명목 상한의 입력).

        Returns:
            열린 기록의 노출 합. 없으면 0.

        Note:
            🔴 **체결된 것이 있으면 그것을 센다** (`filled_leverage` · T288). `leverage` 는
            의도한 배율이라 정수 계약과 어긋난다 — 덜 산 만큼 방이 묶이고, 반올림으로 **더 산
            만큼은 상한을 넘겨도 안 보인다**. 상한의 뜻이 *"실제로 얼마를 들고 있나"* 이므로
            실측이 있으면 실측이 이긴다.

            모형(백테스트·페이퍼)과 옛 기록은 `filled_leverage` 가 None 이라 `leverage` 를 쓴다.
            ⭐ 불타기(T308)로 더 실은 노출도 센다(`held_exposure`) — 빠지면 추가 뒤 새 진입이 상한을
            넘겨도 모른다(연구 원장은 추가분을 그 자리의 노출에 더했다).
        """
        return sum(
            (
                item.held_exposure
                for item in self.session.ledger.records
                if item.outcome is Outcome.OPEN
            ),
            Decimal(0),
        )

    def breadth_bar_at(self) -> datetime | None:
        """폭을 세는 축에서 **마지막으로 받은 마감 봉**의 시작 시각 — 관측 전용 (2026-09-21).

        Returns:
            폭을 안 세는 세션이거나 봉이 없으면 None.

        Note:
            문이 폭을 셀 때 이 값을 같이 적는다. 묻는 순간 형제의 마지막 봉이 아직 안 도착했으면
            폭이 조용히 1 적게 세어지는데, 이 시각이 다른 형제들보다 한 칸 늦은 것으로 드러난다.
            판정에는 안 쓴다.
        """
        if self.breadth_bars < 1 or self.breadth_frame is None:
            return None
        rows = self.session.feed.observed(self.breadth_frame)
        return rows[-1].ts if rows else None

    def open_count_of(self, leg: str) -> int:
        """그 다리(귀속 키)가 낸 보유 중 매매 수 (T291 · 다리별 문의 입력).

        Args:
            leg: 귀속 키(`Playbook.attribution` = `TradeRecord.playbook`).

        Returns:
            그 다리의 보유 중 기록 수. `open_count` 와 같은 규칙에 귀속 거름 하나만 더했다.
        """
        return sum(
            1
            for item in self.session.ledger.records
            if item.outcome is Outcome.OPEN and item.playbook == leg
        )

    def exits_of(self, leg: str) -> list[tuple[datetime, bool]]:
        """그 다리의 확정된 청산 — `exits` 에 귀속 거름을 더했다 (T291).

        Args:
            leg: 귀속 키.

        Returns:
            `(청산 시각, 손절이었나)` 목록. 사람 매매는 뺀다.
        """
        return [
            (item.closed_at, item.outcome is Outcome.STOP_LOSS)
            for item in self.session.ledger.closed
            if item.closed_at is not None and item.actor is Actor.SYSTEM and item.playbook == leg
        ]

    def open_exposure_of(self, leg: str) -> Decimal:
        """그 다리의 보유 중 노출 합 — `open_exposure` 에 귀속 거름을 더했다 (T291).

        Args:
            leg: 귀속 키.

        Returns:
            열린 기록의 노출 합(체결 실측이 있으면 실측). 없으면 0.
        """
        return sum(
            (
                item.held_exposure
                for item in self.session.ledger.records
                if item.outcome is Outcome.OPEN and item.playbook == leg
            ),
            Decimal(0),
        )

    def band_breaks(self, at: datetime) -> int:
        """이 종목이 최근 몇 봉 안에 **밴드 상단 밖에서 마감**했나 — 1 또는 0 (T289 폭의 입력).

        Args:
            at: 진입하려는 봉의 시각(UTC). 그 시각까지 **마감된** 봉만 본다.

        Returns:
            최근 `breadth_bars` 개 마감 봉 중 하나라도 종가가 밴드 상단 밖이면 1, 아니면 0.
            폭을 안 세는 세션(`breadth_bars` 0 · 축 없음)은 늘 0.

        Note:
            🔴 **판정용 보기(`feed.judged`)로만 읽는다** — 커서 밖을 보면 미래 참조다(절대 규칙 #5).
            펀드 문은 멤버들의 이 값을 **합해** 폭을 낸다(`SlotGate.breadth`). 자기 자신도 센다 —
            연구 정의(176차 `breadth_of`)가 자기 포함이다.
        """
        if self.breadth_bars < 1 or self.breadth_frame is None:
            return 0
        feed = self.session.feed
        try:
            rows = feed.judged(self.breadth_frame, at=at)
        except SealBreachError:
            # 🔴 **묻는 쪽과 답하는 쪽의 시계가 다르다** (2026-09-21 실계좌 실측 · T289 결함).
            #    폭은 진입하려는 세션이 **형제 세션들**에게 묻는다. 정시에 세션들은 차례로 걷는데,
            #    먼저 걸은 세션이 아직 안 걸은 형제에게 물으면 `at` 이 그 형제의 커서보다 미래라
            #    `judged` 가 터지고, 예외가 묻던 세션의 걸음까지 올라가 **진입하려던 봉을 통째로
            #    건너뛴다**(ETH 01:00 · "미래를 요구했다 — 00:50 > 커서 00:00"). 놓친 진입은
            #    로그에 걸음 실패 한 줄로만 남는다.
            #
            #    형제가 **받아 둔 마감 봉** 중 `at` 전의 것을 읽는다 — 묻는 세션의 시각보다 미래를
            #    보지 않으므로 미래 참조가 아니다(형제의 커서가 늦을 뿐 그 봉은 이미 마감됐다).
            #    ⭐ 봉인 급전은 `observed` 가 `judged` 와 같아(커서까지) 백테스트는 안 변한다.
            rows = [bar for bar in feed.observed(self.breadth_frame) if bar.ts < at]
        closes = [bar.close for bar in rows]
        return 1 if closed_above_upper(closes, bars=self.breadth_bars) else 0
