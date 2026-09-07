"""차트 주문 — **사람이 주체인 판** (2026-08-30).

## 사용자 확정

> *"그냥 완전히 커스텀이라고 생각했고, 그냥 사람이 정하는대로 다 들어가는 거야.
>  run이지만 사실 그냥 추적을 위한 깡통을 생각하긴 했어. 즉, 해당 RUN의 주체는
>  그냥 사용자인거야."*

## 이 시험이 지키는 것

    ① `custom` 은 **아무것도 판단하지 않는다**   셋업·트레일·재레버가 없다
    ② 사람이 그은 **1차 익절이 그대로** 들어간다  한가운데로 다시 계산하지 않는다
    ③ 확정이 **판을 띄우기 전에** 온다            막힐 계획으로 빈 판을 만들지 않는다
    ④ 원장에 들어가는 손절은 **확정된 값**이다     사람이 낸 원값이 아니다
    ⑤ 셋업 없는 판은 **방아쇠 축을 받는다**        없으면 손절 확인이 4시간에 한 번이다

⚠️ ①이 이 기능의 전부다. 러너가 하나라도 판단하기 시작하면 *"판의 주체가 사람"* 이
아니게 되고, 그러면 이 판의 성적이 사람 것도 시스템 것도 아닌 것이 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session
from updown.orchestration.walkforward.ledger import Actor, Direction

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)

BUY = '@router.post("/buy/{key}")'
"""차트 주문 엔드포인트의 **끝** — 원문 검사가 여기까지만 본다."""


def _book() -> Playbook:
    """선언된 `custom` 플레이북 — 시험이 자기 것을 만들지 않는다.

    Returns:
        `config/playbooks.yml` 의 `custom`.

    Note:
        ⚠️ 손으로 만들면 설정을 바꿔도 시험이 안 따라온다. 그러면 이 시험은
        *"지금 도는 것"* 이 아니라 *"예전에 돌던 것"* 을 지킨다.
    """
    found = {item.playbook_id: item for item in load_playbooks()}
    assert "custom" in found, "차트 주문이 띄울 판이 선언돼 있지 않다"
    return found["custom"]


def _session() -> Session:
    """`custom` 으로 도는 최소 세션 — 봉은 판정에 안 쓰인다 (셋업이 없다)."""
    rows = [
        Candle(
            instrument=BTC,
            timeframe=Timeframe.H4,
            ts=START + timedelta(hours=4) * i,
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(10),
        )
        for i in range(40)
    ]
    seal = Seal(start=START, end=START + timedelta(days=6))
    return Session(
        instrument=BTC,
        playbooks=(_book(),),
        feed=SealedFeed({Timeframe.H4: rows}, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
    )


def _source() -> str:
    """주문 경로의 원문 — 배선은 값이 아니라 **코드의 모양**이라 이렇게 본다."""
    import updown.apps.api.walkforward as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


def _endpoint() -> str:
    """`live_custom` 의 **코드만** — 독스트링과 주석은 뺀다.

    Returns:
        주석·독스트링을 걷어낸 본문.

    Note:
        ⚠️ 처음에는 원문을 그대로 봤는데 **자기 독스트링에 걸렸다** — 주석에
        `contracts_for` 를 안 쓴다고 적어 둔 것을 "쓰고 있다"로 읽었다. 원문 검사는
        설명글을 코드로 오해하기 쉽고, 그렇게 통과/실패한 시험은 아무것도 안 지킨다.
    """
    source = _source()
    body = source[source.index("async def live_custom(") : source.index(BUY)]
    head = body.index('"""')
    body = body[:head] + body[body.index('"""', head + 3) + 3 :]
    keep = [line for line in body.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(keep)


def _buy_call() -> str:
    """엔드포인트가 원장에 적는 호출 — 여기 들어가는 값이 곧 주문이다."""
    body = _endpoint()
    head = body.index("live.session.buy(")
    return body[head : body.index(")", head)]


class TestTheBoardJudgesNothing:
    """① 러너가 판단을 하나라도 하면 판의 주체가 사람이 아니게 된다."""

    def test_it_has_no_setups(self) -> None:
        """셋업이 없다 = **자동 진입이 없다**. 사람이 누를 때만 매매가 생긴다."""
        assert _book().setups == ()

    def test_it_does_not_trail_the_stop(self) -> None:
        """🔴 트레일이 있으면 **사람이 정한 손절이 덮인다** — 그것이 (가)안이었고 버렸다."""
        book = _book()
        assert book.trail_ma is None
        assert not book.trail_stops
        assert not book.ratchet_boxes

    def test_it_does_not_relever(self) -> None:
        """재레버가 돌면 수량도 러너 것이 된다 — 수량은 사람의 예산 x 배율이다."""
        assert not _book().relever

    def test_it_does_not_exit_on_regime(self) -> None:
        """국면으로 청산하면 사람이 정한 익절 전에 러너가 먼저 나간다."""
        book = _book()
        assert book.adx_exit_long is None
        assert book.adx_exit_short is None

    def test_it_is_not_startable_from_the_normal_console(self) -> None:
        """⛔ 계획선 없이 띄우면 **아무것도 안 하는 빈 판**이 된다."""
        assert _book().listed is False

    def test_the_attribution_stays_one_name(self) -> None:
        """🔴 (다)안 — 이름은 하나. 조합마다 이름을 만들면 분모가 쪼개진다 (§5.6.2)."""
        assert _book().attribution == "custom@1.0.0"


class TestTheHumanValuesGoInAsTyped:
    """② 화면에 그려진 선과 원장이 갈리면 안 된다."""

    def test_the_first_target_is_taken_not_derived(self) -> None:
        """🔴 예전에는 진입과 목표의 **한가운데**로 다시 계산했다.

        차트 주문은 1차 익절선을 손으로 끈다 — 여기서 다시 계산하면 사람이 끈 선이
        조용히 버려지고, 화면에는 그 선이 그대로 그려져 있다.
        """
        got = _session().buy(
            price=Decimal(100),
            stop=Decimal(98),
            target=Decimal(120),
            first=Decimal(104),
        )
        assert got.planned_first == Decimal(104), "한가운데(110)로 다시 계산하면 안 된다"

    def test_omitting_the_first_keeps_the_old_midpoint(self) -> None:
        """⚠️ 걸어가기 화면은 1차를 물을 자리가 없다 — 그 경로를 안 깬다."""
        got = _session().buy(price=Decimal(100), stop=Decimal(98), target=Decimal(120))
        assert got.planned_first == Decimal(110)

    def test_it_records_the_human_as_the_actor(self) -> None:
        """⛔ 사람이 누른 것은 정답지가 아니다 (절대 규칙 #11) — 갈라 두는 이유다."""
        got = _session().buy(price=Decimal(100), stop=Decimal(98), target=Decimal(120))
        assert got.actor is Actor.HUMAN

    def test_it_can_go_short(self) -> None:
        """🪞 절대 규칙 #10 개정 — 숏이 열렸다. 기본은 여전히 롱이다."""
        got = _session().buy(
            price=Decimal(100),
            stop=Decimal(102),
            target=Decimal(90),
            first=Decimal(96),
            direction=Direction.SHORT,
        )
        assert got.direction is Direction.SHORT
        assert got.planned_first == Decimal(96)

    def test_one_position_at_a_time(self) -> None:
        session = _session()
        session.buy(price=Decimal(100), stop=Decimal(98), target=Decimal(120))
        with pytest.raises(RuntimeError, match="이미 보유"):
            session.buy(price=Decimal(100), stop=Decimal(98), target=Decimal(120))


class TestTheOrderPathIsWiredInTheRightOrder:
    """③④ 순서와 출처 — 여기가 틀리면 화면과 거래소가 갈린다."""

    def test_it_confirms_before_it_starts_a_board(self) -> None:
        """🔴 막힐 계획으로 판을 먼저 띄우면 **아무것도 안 하는 빈 판**이 남는다.

        그 판은 화면에서 진짜 판과 구별되지 않고, 사람은 자기 주문이 나간 줄 안다.
        """
        body = _endpoint()
        assert body.index("confirm(") < body.index("_live_start("), (
            "확정이 판 띄우기 뒤에 있다 — 막힐 계획으로 빈 판이 생긴다"
        )

    def test_it_buys_with_the_confirmed_stop_not_the_raw_one(self) -> None:
        """🔴 원값을 그대로 쓰면 청산 밖 손절이 원장에 들어간다 (T120)."""
        call = _buy_call()
        assert "stop=got.stop" in call, "확정된 손절로 사야 한다"
        # ⚠️ `confirm(stop=stop, ...)` 은 맞는 코드다 — **원장에 적는 호출**만 본다.
        assert "stop=stop," not in call, "사람이 낸 원값으로 사고 있다"

    def test_it_shares_the_confirm_function_with_the_screen(self) -> None:
        """⚠️ 확정을 두 벌로 두면 화면이 통과시킨 계획을 주문이 막는다 (규칙 #4)."""
        import updown.apps.api.analysis as screen

        wanted = "from updown.decision.risk.manual import"
        assert wanted in _source()
        assert wanted in Path(screen.__file__).read_text(encoding="utf-8")

    def test_it_tells_the_screen_what_was_confirmed(self) -> None:
        """⚠️ 손절이 옮겨졌는데 화면이 원값을 그리면 그것이 거짓말이다 (규칙 #8)."""
        assert 'body["confirm"] = as_json(got)' in _source()

    def test_it_leaves_sizing_to_the_one_place_that_does_it(self) -> None:
        """⚠️ 두 번째 사이징 경로가 생기면 원장 수량과 거래소 수량이 갈린다."""
        assert "contracts_for" not in _endpoint()

    def test_it_refuses_a_blocked_plan(self) -> None:
        """⛔ `ok` 가 거짓인데 사면, 확정기는 있으나 마나다."""
        body = _endpoint()
        assert "if not got.ok:" in body
        assert "HTTPException(400" in body[body.index("if not got.ok:") :]


class TestASetuplessBoardGetsAHeartbeat:
    """⑤ 안 주면 손절 확인이 **4시간에 한 번**이 된다 — 손절이 없는 것과 거의 같다."""

    def test_the_live_path_sets_a_trigger_frame_for_setupless_books(self) -> None:
        source = _source()
        assert "if not book.setups:" in source
        assert "Timeframe.M1.value" in source, "안 주면 1분으로 떨어져야 한다"

    def test_there_is_exactly_one_such_door(self) -> None:
        """⚠️ 룰이 선언한 축을 밖에서 갈아 끼우면 조작 통로다 (T17 ③).

        백테스트 경로에는 안 넣었다 — 셋업 없는 판은 백테스트에서 매매를 한 건도 안
        만들고, 넣어 두면 "쓰이지 않는데 있는 통로"가 된다.
        """
        assert _source().count('payload.get("price_frame")') == 1, (
            "방아쇠 축 통로가 둘 이상이다 — 하나는 라이브 · 셋업 없는 판 전용이다"
        )


class TestTheFlagsSurvive:
    """🔴 *"무엇을 보고 잡았나"* 를 못 물으면 이 기능이 있을 이유가 없다."""

    def test_the_flags_are_written_into_the_run_meta(self) -> None:
        """⚠️ 판 메타는 DB 에 남는다 — 재시작해도 따라온다."""
        assert '"custom_flags": _flag_names(payload)' in _source()

    def test_the_chart_draws_what_the_human_looked_at(self) -> None:
        """셋업이 없으면 `_drawn_flags` 가 빈 목록이라 차트가 아무것도 안 그린다."""
        source = _source()
        assert "if not book.setups and _flag_names(payload):" in source

    def test_both_places_read_the_flags_the_same_way(self) -> None:
        """⚠️ 갈리면 나중에 둘을 대조할 때 어느 쪽이 사실인지 알 수 없다."""
        assert _source().count("_flag_names(payload)") >= 3
