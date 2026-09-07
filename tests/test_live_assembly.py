"""**조립**을 시험한다 — 부품이 아니라 (T15-5).

🔴 2026-08-18 하루에 나온 결함 넷 중 **단위 테스트가 잡은 것은 0개**다. 부품 테스트는
각자 맞다고 답했고, 조립했을 때만 틀렸다. 그래서 늘 사람 눈이 먼저 봤다:

| 증상 | 실제 원인 |
|---|---|
| 10초봉이 안 움직인다 | 웹소켓이 진입 축 하나만 구독 — 나머지는 시드 뒤 동결 |
| 매매 4건이 진입가·시각 동일 | 진입가 축(5m)이 동결 → 죽은 봉으로 4시간 판정 |
| 롱인데 1차 익절이 진입 아래 | 계획은 15m 가격, 진입가는 5m 종가 — 한 계획에 두 시점 |
| 축 진행이 멈췄다 | 판정용 보기로 화면을 그렸다 (커서가 진입 축에 묶여 있다) |

⚠️ 내가 만든 감사조차 판정용 보기로 재고 있어서 **막힌 눈으로** 보고 있었다. 그래서 이
파일의 절반은 *"감사가 사실을 보는가"* 를 잠근다.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

from structlog.testing import capture_logs

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.logging.setup import get_logger
from updown.orchestration.walkforward.ledger import Funding
from updown.orchestration.walkforward.live_feed import LiveFeed
from updown.orchestration.walkforward.live_runner import STALL_BARS, LiveRunner
from updown.orchestration.walkforward.session import STEP_FRAME

BTC = Instrument(
    market=Market.GATE,
    symbol="BTC_USDT",
    name="BTC_USDT",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
ORIGIN = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
ENTRY = Timeframe.M15


SECONDS = {Timeframe.S10: 10, Timeframe.M5: 300, Timeframe.M15: 900}


def at(frame: Timeframe, offset: int, *, price: str = "64000") -> Candle:
    """ORIGIN 에서 `offset` 초 지점의 봉."""
    value = Decimal(price)
    return Candle(
        instrument=BTC,
        timeframe=frame,
        ts=ORIGIN + timedelta(seconds=offset),
        open=value,
        high=value,
        low=value,
        close=value,
        volume=Decimal(1),
    )


def bar(frame: Timeframe, index: int, *, price: str = "64000") -> Candle:
    """그 축의 index 번째 봉."""
    return at(frame, SECONDS[frame] * index, price=price)


SPAN = 900
"""진입 축 한 봉의 초. 세 축이 **같은 구간**을 덮게 만드는 기준이다."""


def feed_with(*, entry_bars: int = 4, step_bars: int = 12, ten_bars: int = 6) -> LiveFeed:
    """세 축을 든 라이브 급전 — 시드는 전부 마감 봉이다.

    Args:
        entry_bars: 진입 축(15m) 봉 수. 커서는 이 끝에 선다.
        step_bars: 진입가 축(5m) 봉 수. **적게 주면 그 축이 동결된 상황**이다.
        ten_bars: 10초봉 수. 커서에 딱 맞춰 끝난다.

    Returns:
        급전.

    Note:
        🔴 **10초봉을 0 부터 깔면 안 된다.** 커서는 진입 축 끝(60분)에 서므로, 0 부터
        깐 10초봉 6개는 전부 커서보다 **한 시간 전**이고 `judged` 에 다 들어온다 —
        그러면 "커서가 화면을 막는다" 를 재는 테스트가 아무것도 안 재게 된다.

        ⇒ 커서에 **딱 맞춰** 끝내야, 그 뒤에 밀어 넣는 봉이 커서 밖으로 나간다.
    """
    end = entry_bars * SPAN
    return LiveFeed(
        {
            ENTRY: [bar(ENTRY, i) for i in range(entry_bars)],
            STEP_FRAME: [bar(STEP_FRAME, i) for i in range(step_bars)],
            Timeframe.S10: [at(Timeframe.S10, end - 10 * (ten_bars - i)) for i in range(ten_bars)],
        },
        ENTRY,
    )


class Empty:
    """원장이 **빈** 세션 대역.

    Note:
        ⭐ 감사의 보유 관련 항목(기하·손절·원장 대조)을 지나가게 하려면 원장이 필요한데,
        이 파일이 재는 것은 **급전 계약**이다. 진짜 세션을 세우면 플레이북·봉인·비용까지
        끌려 오고, 그 픽스처가 깨질 때 무엇이 틀렸는지 알 수 없다.
    """

    class _Book:
        records: ClassVar[list[object]] = []
        # ⭐ 감사가 돈 상태도 본다 (T14-2). 백테스트 모형이면 지갑 대조를 건너뛴다 —
        #    이 파일이 재는 것은 급전 계약이지 회계가 아니다.
        funding = Funding.SEED_REFILL
        halted_at: ClassVar[str | None] = None
        tripped_at: ClassVar[str | None] = None

    ledger: ClassVar[_Book] = _Book()
    # ⭐ 진입가 축 (T17 ③). None 이면 STEP_FRAME — 0.1 과 같다.
    price_frame = None


class Blind:
    """조건부·포지션을 **모르는** 어댑터.

    Note:
        ⭐ 일부러 `StopAware` · `PositionAware` 가 아니다. 감사의 손절·원장 항목을 빼고
        **급전 계약만** 재려는 것이고, 그 둘을 흉내내면 이 테스트가 무엇을 재는지
        흐려진다.
    """


def assembled(feed: LiveFeed, *, steps: int = 1) -> LiveRunner:
    """급전만 물린 러너 — 스트림·거래소는 안 쓴다.

    Args:
        feed: 라이브 급전.
        steps: 이미 판정한 걸음 수.

    Returns:
        감사를 부를 수 있는 러너.

    Note:
        ⚠️ `__new__` 로 만든다. 진짜 생성자는 웹소켓 스트림과 Gate 어댑터를 요구하는데,
        이 테스트가 재는 것은 **급전과 감사의 계약**이다 — 네트워크를 끌어들이면 조립
        테스트가 통합 테스트가 되고, 그러면 CI 에서 안 돈다.
    """
    made = LiveRunner.__new__(LiveRunner)
    made._feed = feed  # pyright: ignore[reportPrivateUsage]
    made._session = Empty()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made._orders = Blind()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made.steps = steps
    made._bars_at_step = len(feed.observed(ENTRY))  # pyright: ignore[reportPrivateUsage]
    # ⭐ 호가는 멀쩡하다고 둔다 — 이 파일이 재는 것은 **봉과 감사의 계약**이고, 호가
    #   판정은 `test_book_watch.py` 의 몫이다 (`_keep_watching_book` 이 채우는 값).
    made.dry = None
    # ⭐ 예산은 넉넉하다고 둔다 — 이 파일이 재는 것은 **봉과 감사의 계약**이고,
    #   예산 합 판정은 `check_funding` 의 몫이다.
    made.short_by = None
    return made


def codes(found: list[dict[str, str]]) -> set[str]:
    """발견 코드만."""
    return {item["code"] for item in found}


class TestTwoViews:
    def test_the_cursor_does_not_block_the_screen(self) -> None:
        """🔴 커서는 진입 축에 묶여 있다 — 그것으로 화면을 그리면 10초봉이 15분 멈춘다."""
        feed = feed_with()
        end = 4 * SPAN
        seen = len(feed.observed(Timeframe.S10))
        judged = len(feed.judged(Timeframe.S10))
        for step in range(6):
            feed.push(Timeframe.S10, at(Timeframe.S10, end + 10 * step), closed=True)

        # ⭐ 화면용 보기는 **흘렀다** — 봉은 들어오고 있었다.
        assert len(feed.observed(Timeframe.S10)) == seen + 6
        # ⛔ 판정용 보기는 커서에 막혀 있다 — 그리고 그것이 맞다 (절대 규칙 #5).
        assert len(feed.judged(Timeframe.S10)) == judged

    def test_the_receive_clock_moves_when_the_cursor_does_not(self) -> None:
        """🔴 시계가 하나면 정상 대기 중인 판이 죽은 것처럼 보인다 (T15-2)."""
        feed = feed_with()
        cursor = feed.cursor
        moved = feed.push(Timeframe.S10, at(Timeframe.S10, 4 * SPAN), closed=True)

        assert moved is False, "진입 축이 아니므로 커서는 안 움직인다"
        assert feed.cursor == cursor
        assert feed.received_at > cursor, "수신 시계는 움직여야 한다"
        assert feed.received_for(Timeframe.S10) is not None

    def test_a_forming_bar_never_reaches_judgement(self) -> None:
        """⛔ 진행 중 봉이 판정에 새면 같은 상황에서 다른 결론이 난다 (절대 규칙 #5)."""
        feed = feed_with()
        judged = len(feed.judged(ENTRY))
        feed.push(ENTRY, bar(ENTRY, 4, price="99999"), closed=False)

        assert len(feed.judged(ENTRY)) == judged
        assert feed.pending(ENTRY) is not None
        assert feed.received_for(ENTRY) is None, "미마감 봉은 수신으로 안 센다"


class TestAuditSeesFacts:
    async def test_a_frozen_side_frame_is_caught(self) -> None:
        """🔴 5분봉이 시드 뒤로 안 움직이면 판정이 죽은 봉을 본다."""
        # 진입 축만 계속 오고 진입가 축은 그대로다.
        feed = feed_with(step_bars=2)
        found = await assembled(feed).audit()
        assert "price_frame_behind" in codes(found)

    async def test_a_healthy_feed_is_quiet_about_prices(self) -> None:
        """⚠️ 두 축이 나란히 흐르면 아무 말도 없어야 한다 — 거짓 경보는 무해하지 않다."""
        feed = feed_with()
        made = assembled(feed)
        assert made.price_drift() == ""
        assert "price_frame_behind" not in codes(await made.audit())

    async def test_stalled_judgement_is_caught(self) -> None:
        """🔴 봉은 느는데 걸음이 안 늘면 잡는다 (T15-4 새 항목)."""
        feed = feed_with()
        made = assembled(feed)
        for index in range(4, 4 + STALL_BARS):
            feed.push(ENTRY, bar(ENTRY, index), closed=True)
            feed.push(STEP_FRAME, bar(STEP_FRAME, 12 + index), closed=True)
        # ⛔ `_bars_at_step` 을 일부러 갱신하지 않는다 — 그것이 "판정이 안 돌았다" 다.
        assert "judgement_stalled" in codes(await made.audit())

    async def test_one_bar_behind_is_not_a_stall(self) -> None:
        """⚠️ 방금 마감된 봉을 아직 판정하기 전인 순간이 늘 있다 — 그것은 정상이다."""
        feed = feed_with()
        made = assembled(feed)
        feed.push(ENTRY, bar(ENTRY, 4), closed=True)
        feed.push(STEP_FRAME, bar(STEP_FRAME, 12), closed=True)
        assert "judgement_stalled" not in codes(await made.audit())

    async def test_a_fresh_run_is_not_a_stall(self) -> None:
        """⛔ 걸음이 0 인 갓 뜬 판을 정지로 부르지 않는다 — 그것은 대기다."""
        feed = feed_with()
        made = assembled(feed, steps=0)
        made._bars_at_step = 0  # pyright: ignore[reportPrivateUsage]
        assert "judgement_stalled" not in codes(await made.audit())


class TestTheAuditIsNotBlind:
    def test_the_audit_never_reads_the_judged_view(self) -> None:
        """🔴 **이번 사고의 진원을 소스로 잠근다** (T15-4).

        Note:
            내가 만든 감사조차 `judged()` 로 재고 있었다. 그 보기는 커서까지만 주고
            커서는 진입 축 봉이 마감될 때만 움직이므로, **축이 동결됐는데 감사는 정상
            이라고 답했다.** 같은 계약을 쓰는 한 감사도 같이 눈이 먼다.

            ⚠️ 소스를 읽는 테스트는 보통 나쁘지만 여기서는 값이 아니라 **어느 계약을
            쓰는가**를 지키는 것이고, 그것을 행동으로 재려면 동결을 흉내내는 픽스처가
            필요한데 그 픽스처가 곧 위 `TestAuditSeesFacts` 다. 둘 다 있어야 한다 —
            행동 테스트는 지금 틀린 것을 잡고, 이 테스트는 **다시 틀리는 것**을 막는다.
        """
        for name in ("audit", "frame_ages", "price_drift"):
            source = inspect.getsource(getattr(LiveRunner, name))
            assert ".judged(" not in source, (
                f"{name} 이 판정용 보기를 쓴다 — 커서에 막혀 동결을 못 본다. "
                "사실을 재는 것은 observed() 다"
            )


class TestGapCounting:
    def test_a_hole_is_counted_not_hidden(self) -> None:
        """⚠️ 빈 봉은 "거래 없음" 과 구별되지 않는다 — 세어야 메울 수 있다."""
        feed = feed_with()
        assert feed.gaps == 0
        # 12 를 건너뛰고 15 를 보낸다 → 12·13·14 가 빈다.
        feed.push(STEP_FRAME, bar(STEP_FRAME, 15), closed=True)
        assert feed.gaps == 3

    def test_backfill_closes_the_hole(self) -> None:
        """스트림이 끊겼다 붙으면 REST 가 그 사이를 메운다."""
        feed = feed_with()
        feed.push(STEP_FRAME, bar(STEP_FRAME, 15), closed=True)
        added = feed.backfill(STEP_FRAME, [bar(STEP_FRAME, i) for i in (12, 13, 14)])
        assert added == 3
        rows = feed.observed(STEP_FRAME)
        assert [item.ts for item in rows] == sorted(item.ts for item in rows)
        assert len(rows) == 16


class TestTheChartIsNotBlind:
    def test_the_live_chart_never_reads_the_judged_view(self) -> None:
        """🔴 **이 회귀를 실제로 냈다** (2026-08-19).

        Note:
            차트는 `hasattr(feed, "display")` 로 라이브인지 묻고 있었다. T15 에서 그
            메서드를 `observed` 로 **이름만 바꾸자 검사가 조용히 항상 거짓**이 됐고,
            차트가 다시 커서에 막혔다 — 커서는 진입 축(15m) 봉이 마감될 때만 움직이므로
            하위 축이 최대 15분 동안 얼어 보인다. **고친 그 버그가 그대로 돌아왔다.**

            ⇒ 능력 검사를 없앴다. `observed` 는 두 급전에 다 있고 봉인 급전은 `judged`
            와 같은 값을 낸다 — 분기가 없으면 이름을 바꿔도 조용히 안 깨진다.

            ⚠️ 되감기(`at`)만 `judged` 를 쓴다. 그때는 정확히 그 시점의 그림이어야 한다.
        """
        source: str = Path("src/updown/apps/api/walkforward.py").read_text(encoding="utf-8")
        after = source.split("views: list[FrameView] = []")[1]
        block = after.split("entry_frame =")[0]
        assert 'hasattr(session.feed, "display")' not in block, (
            "능력 검사가 돌아왔다 — 이름을 바꾸면 조용히 거짓이 된다"
        )
        assert "session.feed.observed(frame)" in block, "라이브 차트가 사실 보기를 안 쓴다"
        assert "if at is None" in block, "되감기와 라이브가 안 갈렸다"


class TestTheTriggerFrameWakesJudgement:
    """🔴 **T17 이 실제로 하려던 것** (2026-08-19).

    방아쇠 축을 10s 로 내려도 **판정이 진입 축 마감에만 돌면 아무것도 안 빨라진다** —
    실제로 그 상태였다. 띠에 닿은 뒤 되돌아간 만큼을 통째로 손해 보고 들어간다:

    ```
    상단 스마트 띠  64,924.7 ~ 64,952.4
    15m 고가        64,995.0    ← 이때 닿았다
    15m 종가        64,751.3    ← 여기서 팔았다 (0.31% 낮게)
    ```
    """

    def test_the_frozen_version_wakes_only_on_the_entry_frame(self) -> None:
        """⛔ 0.1 은 한 줄도 안 달라진다 — 기본값이 진입 축 하나다 (§5.6.2)."""
        feed = feed_with()
        assert feed.judge_on == {ENTRY}
        # 하위 축 봉이 와도 판정할 것이 없다.
        feed.push(Timeframe.S10, at(Timeframe.S10, 4 * SPAN), closed=True)
        assert feed.advance(ENTRY) is False

    def test_the_trigger_frame_wakes_it(self) -> None:
        """✅ 0.4 — 10초봉이 마감되면 한 걸음 돈다."""
        feed = feed_with()
        feed.judge_on = {ENTRY, Timeframe.S10}
        feed.push(Timeframe.S10, at(Timeframe.S10, 4 * SPAN), closed=True)
        assert feed.advance(ENTRY) is True

    def test_the_cursor_still_belongs_to_the_entry_frame(self) -> None:
        """🔴 **커서는 진입 축만 민다.**

        Note:
            방아쇠 축으로 커서를 밀면 판정용 보기가 10초마다 넓어져, 15분봉이 아직
            마감 안 됐는데 **마감된 것으로 보인다** — 그것이 미래 참조다 (규칙 #5).
        """
        feed = feed_with()
        feed.judge_on = {ENTRY, Timeframe.S10}
        cursor = feed.cursor
        feed.push(Timeframe.S10, at(Timeframe.S10, 4 * SPAN), closed=True)
        assert feed.cursor == cursor

    def test_one_wake_is_consumed_once(self) -> None:
        """⛔ 플래그를 안 내리면 같은 봉을 두 번 판정한다 — 주문이 두 번 난다."""
        feed = feed_with()
        feed.judge_on = {ENTRY, Timeframe.S10}
        feed.push(Timeframe.S10, at(Timeframe.S10, 4 * SPAN), closed=True)
        assert feed.advance(ENTRY) is True
        assert feed.advance(ENTRY) is False

    def test_a_forming_trigger_bar_does_not_wake_it(self) -> None:
        """⛔ 미마감 봉으로 판정하면 같은 상황에서 다른 결론이 난다 (규칙 #5)."""
        feed = feed_with()
        feed.judge_on = {ENTRY, Timeframe.S10}
        feed.push(Timeframe.S10, at(Timeframe.S10, 4 * SPAN), closed=False)
        assert feed.advance(ENTRY) is False


# ── T76 · 로그가 **어느 판인지** 말한다 ──────────────────────────────


def test_runner_log_is_bound_before_init_runs() -> None:
    """생성자를 우회해 조립하는 더블도 로그를 남길 수 있어야 한다.

    🔴 로깅이 실패해서 판정이 죽는 것은 본말전도다 — 클래스 기본값을 둔 이유다.
    """
    double = LiveRunner.__new__(LiveRunner)
    double._log.info("t76_probe", payload={})  # pyright: ignore[reportPrivateUsage]


def test_identify_binds_the_run_key() -> None:
    """`identify()` 뒤에는 이 러너의 로그에 판 식별자가 **실제로 실려 나간다** (T76).

    내부 구조를 들여다보지 않고 **나온 줄**을 본다 — 묶는 방식이 바뀌어도
    "판이 찍히는가"는 그대로 지켜진다.
    """
    double = LiveRunner.__new__(LiveRunner)
    double.run_key = ""
    double.identify("live7c37a84d")
    assert double.run_key == "live7c37a84d"
    with capture_logs() as caught:
        double._log.error("t76_probe", payload={})  # pyright: ignore[reportPrivateUsage]
    assert caught and caught[0].get("run") == "live7c37a84d", (
        f"로그에 판 식별자가 없다 — 11개 판의 오류가 구분 없이 섞인다: {caught}"
    )


def test_every_runner_log_goes_through_the_bound_logger() -> None:
    """러너 안에서 모듈 로거를 직접 부르면 **어느 판인지가 사라진다**.

    🔴 사용자 지적 2026-08-28: *"어느 펀드에서 어떤 오류가 남았는지 로그에 잘
    안찍히는 상황 같네."* 원인은 `_logger.` 직접 호출이었다. 새 코드가 실수로
    그 습관을 되살리면 여기서 잡는다.

    ⚠️ 모듈 함수 하나(`stop_live` 의 취소 로그)는 러너 밖이라 예외다 — 그것은
    판에 속하지 않는다.
    """
    lines = Path(inspect.getfile(LiveRunner)).read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("class LiveRunner"))
    end = next(
        (
            i
            for i, ln in enumerate(lines[start + 1 :], start + 1)
            if ln and not ln[0].isspace() and not ln.startswith(")")
        ),
        len(lines),
    )
    # 로거를 **만드는** 줄(bind)은 정상이다 — 내보내는 호출만 본다.
    emit = re.compile(r"(?<![\w.])_logger\.(info|warning|error|debug|critical)\(")
    stray = [ln.strip() for ln in lines[start:end] if emit.search(ln)]
    assert stray == [], f"러너 안에서 모듈 로거 직접 호출 — self._log 를 쓴다: {stray}"


def test_contextvars_are_merged_so_adapter_logs_carry_the_run() -> None:
    """어댑터 로그(`gate_stop_placed` 등)도 판을 달고 나와야 한다 (T76 ②).

    러너 자신의 로그만 고치면 정작 **오류가 나는 곳**(거래소 어댑터)은 여전히
    익명이다. `merge_contextvars` 가 처리기 목록에 있어야 태스크에 묶은 값이 흐른다.
    """
    setup = Path(inspect.getfile(get_logger)).read_text(encoding="utf-8")
    assert "structlog.contextvars.merge_contextvars" in setup, (
        "merge_contextvars 가 빠지면 러너가 묶은 판 식별자가 어댑터 로그에 안 붙는다"
    )
