"""방아쇠 캐시 — 신호 계산과 성과 측정을 가른다 (오더 4).

🔴 이 모듈의 위험은 미래 참조가 아니라 **조용한 낡음**이다. 코드를 고쳤는데 옛
방아쇠가 나오면 표는 멀쩡해 보이고, 그것이 이 프로젝트가 하루에 두 번 당한 모양이다.

여기서 못 박는 것:

    같은 코드·같은 데이터  →  캐시가 산다 (히트)
    코드가 바뀌면          →  지문이 바뀌어 캐시가 **안** 산다
    데이터가 늘면          →  열쇠가 바뀌어 캐시가 **안** 산다
    캐시가 깨졌으면        →  조용히 빈 목록이 아니라 **다시 계산**
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery import cache
from updown.orchestration.discovery.frames import Frame
from updown.orchestration.discovery.signals import Board, Trigger
from updown.orchestration.walkforward.ledger import Direction


@dataclass
class Counting:
    """몇 번 계산됐는지 세는 가짜 신호."""

    calls: int = 0

    @property
    def name(self) -> str:
        """TEST-01."""
        return "TEST-01"

    def fire(self, board: Board) -> list[Trigger]:
        """짝수 봉마다 방아쇠 — 부를 때마다 센다."""
        self.calls += 1
        return [
            Trigger(index=index, direction=Direction.LONG if index % 4 else Direction.SHORT)
            for index in range(0, len(board.frame), 2)
        ]


def board_of(count: int, symbol: str = "TEST") -> Board:
    """봉 `count` 개짜리 판."""
    frame = Frame(
        timeframe=Timeframe.H1,
        ts=[index * 3_600_000 for index in range(count)],
        open=[100.0] * count,
        high=[101.0] * count,
        low=[99.0] * count,
        close=[100.0] * count,
        volume=[1.0] * count,
        sources=[60] * count,
    )
    return Board(symbol=symbol, timeframe=Timeframe.H1, frame=frame)


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """캐시를 임시 폴더로 보낸다 — 시험이 진짜 캐시를 건드리면 안 된다."""
    monkeypatch.setattr(cache, "ROOT", tmp_path / "triggers")
    return tmp_path


class TestItSavesTheWork:
    def test_the_second_call_does_not_recompute(self) -> None:
        signal = Counting()
        board = board_of(40)
        hits = cache.Hits()

        first = cache.triggers(signal, board, hits=hits)
        second = cache.triggers(signal, board, hits=hits)

        assert first == second
        assert signal.calls == 1, "두 번째에도 계산했다 — 캐시가 안 산다"
        assert hits.hit == 1
        assert hits.miss == 1
        assert hits.rate == pytest.approx(0.5)

    def test_the_directions_survive_a_round_trip(self) -> None:
        """⚠️ 방향이 문자열로 갔다 오므로 되살아나는지 본다 — 부호가 뒤집히면 손익이 뒤집힌다."""
        signal = Counting()
        board = board_of(40)
        first = cache.triggers(signal, board)
        second = cache.triggers(signal, board)
        assert [one.direction for one in first] == [one.direction for one in second]
        assert {one.direction for one in second} == {Direction.LONG, Direction.SHORT}


class TestItGoesStaleOnPurpose:
    """🔴 낡은 캐시가 **안 살아나는** 것이 이 모듈의 존재 이유다."""

    def test_more_data_is_a_different_key(self) -> None:
        signal = Counting()
        cache.triggers(signal, board_of(40))
        cache.triggers(signal, board_of(60))
        assert signal.calls == 2, "데이터가 늘었는데 옛 방아쇠를 내줬다"

    def test_another_symbol_is_a_different_key(self) -> None:
        signal = Counting()
        cache.triggers(signal, board_of(40, symbol="AAA"))
        cache.triggers(signal, board_of(40, symbol="BBB"))
        assert signal.calls == 2

    def test_a_changed_fingerprint_invalidates_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        signal = Counting()
        board = board_of(40)
        cache.triggers(signal, board)
        monkeypatch.setattr(cache, "fingerprint", lambda: "deadbeefdeadbeef")
        cache.triggers(signal, board)
        assert signal.calls == 2, "코드 지문이 바뀌었는데 옛 방아쇠를 내줬다"


class TestABrokenCacheIsNotAnEmptySignal:
    """🔴 못 읽은 것을 빈 목록으로 돌려주면 *"안 터졌다"* 가 되고 표에서는 멀쩡해 보인다."""

    def test_a_corrupt_file_is_recomputed(self) -> None:
        signal = Counting()
        board = board_of(40)
        want = cache.triggers(signal, board)

        for path in (cache.ROOT).rglob("*.parquet"):
            path.write_bytes(b"not a parquet file")

        again = cache.triggers(signal, board)
        assert again == want
        assert signal.calls == 2, "깨진 캐시를 그대로 믿었다"


class TestTheFingerprintWatchesTheRightTrees:
    def test_it_is_stable_within_a_run(self) -> None:
        assert cache.fingerprint() == cache.fingerprint()

    def test_it_covers_indicators_not_just_signals(self) -> None:
        """⚠️ 신호 소스만 해시하면 **지표를 고쳤을 때 캐시가 안 날아간다**."""
        watched = {one.as_posix() for one in cache.WATCHED}
        assert "src/updown/analysis/indicators" in watched
        assert "src/updown/analysis/structures" in watched
