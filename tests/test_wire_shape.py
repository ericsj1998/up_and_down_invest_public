"""화면으로 나가는 봉의 모양은 **한 곳에서** 짓는다 (2026-08-30).

사용자 질문: *"지금 우리가 쓰는 모든 차트가 다 제대로 수정 및 배선이 완료된 거 맞지?
막 코드 하드코딩되어있고 다 따로 쓰고 그러고 있는 거 아니지?"*

세어 보니 봉 하나를 사전으로 만드는 코드가 **여섯 곳**에 있었고, 여섯 개가 글자까지
똑같았다:

    ai_analysis.py   analysis.py x2   live_stream.py   walkforward.py x2

## ⚠️ "지금 같으니 괜찮다" 가 아니다

같게 **유지될 이유가 없다.** 한 곳에 필드를 더하면 나머지 다섯은 안 따라오고, 화면은
같은 봉을 어디서 받았느냐에 따라 다르게 읽는다. 이 프로젝트는 같은 판단을 여섯 벌로
쓰다가 한 번에 여섯 곳이 틀린 적이 있다 (`common/numeric.zero` · 유령 포지션 107개).

## 🔴 첫 시험이 **`apps/api` 만 봤다** — 그래서 제일 큰 곳을 놓쳤다

여섯 곳을 모으고 시험까지 붙였는데, 그 시험의 훑는 범위가 `apps/api` 였다. 정작
**RUN 차트에 800봉을 보내는** `orchestration/inspection/snapshot.py` 가 손으로 짓고
있었고 시험은 통과했다 (사용자 감사 2026-08-30 에 걸렸다).

⇒ 이제 `src/updown` **전체**를 훑는다. 범위가 좁은 시험은 *"지킨다"* 가 아니라
  *"지키는 것처럼 보인다"* 이고, 그 둘은 다르다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.wire import candle_json

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인", AssetType.COIN, Currency.USD)
CANDLE_DICT = re.compile(r'"ts":[^}]{0,200}?"open":\s*str\(', re.S)
"""**차트용 봉 사전**을 손으로 짓는 모양 — 시각과 사가가 한 사전에 같이 있는 것."""

MARKS_CLOSED = re.compile(r"candle_json\([^)]*closed=")
"""`candle_json` 에 마감 여부를 같이 넘기는 곳."""

ROOT = Path("src/updown")
API = ROOT / "apps" / "api"
HOME = ROOT / "common" / "wire.py"


def _bar() -> Candle:
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M1,
        ts=datetime(2026, 1, 1, 12, 30, tzinfo=UTC),
        open=Decimal("100.5"),
        high=Decimal("110.25"),
        low=Decimal("99.125"),
        close=Decimal("108"),
        volume=Decimal("3.75"),
    )


class TestTheShape:
    def test_it_carries_every_field_the_chart_draws(self) -> None:
        got = candle_json(_bar())
        assert set(got) == {"ts", "open", "high", "low", "close", "volume"}

    def test_numbers_are_strings(self) -> None:
        """🔴 `Decimal` 을 float 로 내리면 화면이 반올림한 값을 다시 보내고, 그 값으로
        주문이 나간다 — 값이 오가며 **조용히 달라진다**."""
        got = candle_json(_bar())
        for name in ("open", "high", "low", "close", "volume"):
            assert isinstance(got[name], str), name
        assert got["high"] == "110.25", "정밀도가 깎이면 안 된다"
        assert got["low"] == "99.125"

    def test_the_time_is_utc_iso(self) -> None:
        """⚠️ 표시만 KST 다 (절대 규칙 #7) — 여기서 바꾸면 받는 쪽이 어느 시각인지 모른다."""
        got = candle_json(_bar())
        assert got["ts"] == "2026-01-01T12:30:00+00:00"

    def test_closed_is_absent_unless_asked(self) -> None:
        """⛔ 안 준 곳에 `True` 를 박으면 "마감됐다" 가 사실이 아닌 곳에서도 사실이 된다."""
        assert "closed" not in candle_json(_bar())
        assert candle_json(_bar(), closed=False)["closed"] is False
        assert candle_json(_bar(), closed=True)["closed"] is True


class TestOnlyOnePlaceBuildsIt:
    """🔴 여섯 벌이었다. 다시 늘어나면 화면이 입구마다 다른 봉을 받는다."""

    def test_nothing_anywhere_builds_the_dict_by_hand(self) -> None:
        """⚠️ **`src/updown` 전체**를 훑는다 — 좁은 범위가 `snapshot.py` 를 놓쳤다.

        ⚠️ *"`open` 을 문자열로 쓴다"* 만 보면 봉이 아닌 사전까지 걸린다
        (`ingest/integrity.py` 의 위반 상세). **시각 + 사가**가 한 사전에 같이 있는
        것만 봉으로 센다 — 넓은 그물은 잡아야 할 것과 아닌 것을 같이 잡는다.
        """
        guilty = [
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*.py")
            if path != HOME and CANDLE_DICT.search(path.read_text(encoding="utf-8"))
        ]
        assert guilty == [], f"봉 사전을 손으로 짓는 곳이 남아 있다: {guilty}"

    def test_the_biggest_path_uses_it(self) -> None:
        """🔴 RUN 차트에 **800봉**을 보내는 입구 — 여기가 빠지면 모은 뜻이 없다."""
        source = (ROOT / "orchestration" / "inspection" / "snapshot.py").read_text(encoding="utf-8")
        assert "candle_json(candle) for candle in frame.candles" in source

    def test_the_chart_endpoints_use_the_helper(self) -> None:
        """⚠️ 없앴는지만 보면 **아무도 안 쓰는 것**도 통과한다 — 쓰는지도 본다."""
        for name in ("analysis.py", "live_stream.py", "walkforward.py"):
            source = (API / name).read_text(encoding="utf-8")
            assert "candle_json(" in source, name
            assert "from updown.common.wire import candle_json" in source, name

    def test_it_lives_low_enough_for_everyone_to_reach(self) -> None:
        """🔴 처음에 `apps/api/` 에 뒀다가 옮겼다.

        `orchestration/` 은 `apps/` 를 import 할 수 없어서(의존 방향은 CI 가 강제한다)
        **가장 큰 경로가 못 썼다.** 전선 위의 모양은 모두가 쓰므로 가장 아래 층이다.
        """
        assert HOME.exists(), "wire 는 common/ 에 있어야 한다"
        assert not (API / "wire.py").exists(), "apps/api 에 남아 있으면 아래 층이 못 쓴다"

    def test_the_stream_is_the_only_one_that_marks_closed(self) -> None:
        """마감 여부는 **실시간 스트림에서만** 뜻이 있다 — 나머지는 이미 마감된 것만 준다."""
        users = [
            path.name
            for path in ROOT.rglob("*.py")
            if MARKS_CLOSED.search(path.read_text(encoding="utf-8"))
        ]
        assert users == ["live_stream.py"], f"마감 표시를 쓰는 곳: {users}"
