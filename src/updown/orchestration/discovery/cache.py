"""**방아쇠 캐시** — 신호 계산과 성과 측정을 가른다 (오더 4).

## 왜 방아쇠인가 — 실측이 예상을 뒤집었다

캐시를 짓기 전에 시간을 쟀다 (DOGEUSDT · 5분봉 645,729봉):

    1분봉 JSON 읽기    1.9s
    열 분해            1.4s
    접기(5m)           1.6s
    **신호 40종 발화   88.7s**   ← 95%
    ────────────────────────
    합계               93.6s / 종목·축   →  40조합이면 **62분**

프레임을 캐시할 뻔했다. 실제로는 프레임이 5% 이고 **발화가 95%** 다.

⇒ 방아쇠(`index` · `direction`)만 저장한다. 측정 항목을 하나 더 넣고 싶을 때
**62분을 다시 쓰지 않아도 된다** — 성과 계산만 다시 돌린다.

## 🔴 낡은 캐시가 조용히 옛 로직을 내주면 안 된다

이 프로젝트가 하루에 두 번 당한 것이 *"조용히 틀린 값"* 이다. 그래서 열쇠에
**코드 지문**을 넣는다:

    signals/ · analysis/indicators/ · analysis/structures/ 의 **전체 소스 해시**

거칠다 — 볼린저를 고치면 RSI 신호의 캐시까지 날아간다. 그래도 이렇게 하는 이유는,
신호 클래스의 소스만 해시하면 **지표를 고쳤을 때 안 날아가기** 때문이다. 그것이
정확히 조용히 틀리는 모양이다.

⚠️ 데이터가 늘어도 방아쇠가 바뀐다. 그래서 봉 수와 처음·끝 시각도 열쇠에 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import TYPE_CHECKING, cast

from updown.orchestration.discovery.signals.base import Board, Signal
from updown.orchestration.walkforward.ledger import Direction

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["ROOT", "Fired", "Hits", "fingerprint", "triggers"]

ROOT = Path("cache/triggers")
"""캐시가 사는 곳. `.gitignore` 대상이다 — 재현 가능한 파생물이지 원본이 아니다."""

WATCHED = (
    Path("src/updown/orchestration/discovery/signals"),
    Path("src/updown/analysis/indicators"),
    Path("src/updown/analysis/structures"),
)
"""지문에 넣을 소스 나무들 — 여기 무엇이든 바뀌면 방아쇠가 바뀔 수 있다."""

_fingerprint_cache: str | None = None


@dataclass(frozen=True, slots=True)
class Fired:
    """방아쇠 하나 — 봉 번호 · 방향 · **시각**.

    Attributes:
        index: 봉 번호.
        direction: 롱/숏.
        ts: 그 봉의 시각(ms).

    Note:
        🔴 **시각을 같이 저장한다.** 동시 포지션 제약을 정하려면 여러 종목의
        방아쇠를 시간순으로 합쳐야 하는데, 봉 번호만 있으면 그러려고 프레임을
        다시 접어야 한다 (종목·축마다 5초). 8B 를 더 쓰고 그것을 없앤다.
    """

    index: int
    direction: Direction
    ts: int


@dataclass(slots=True)
class Hits:
    """캐시 적중 집계 (오더 8: 리포트에 히트율을 싣는다).

    Attributes:
        hit: 캐시에서 읽은 횟수.
        miss: 새로 계산한 횟수.
    """

    hit: int = 0
    miss: int = 0

    @property
    def rate(self) -> float:
        """적중률. 아무것도 안 물었으면 0."""
        total = self.hit + self.miss
        return self.hit / total if total else 0.0

    def __str__(self) -> str:
        """`히트 12/40 (30.0%)`."""
        return f"히트 {self.hit}/{self.hit + self.miss} ({self.rate:.1%})"


def fingerprint() -> str:
    """방아쇠를 바꿀 수 있는 **모든 소스**의 해시.

    Returns:
        16자 지문.

    Note:
        🔴 신호 클래스의 소스만 해시하면 **지표를 고쳤을 때 캐시가 안 날아간다.**
        그것이 정확히 조용히 틀리는 모양이라, 거칠더라도 나무 전체를 센다.

        ⭐ 한 번만 계산하고 들고 있는다 — 파일 수백 개를 매번 읽을 이유가 없다.
    """
    global _fingerprint_cache
    if _fingerprint_cache is not None:
        return _fingerprint_cache
    digest = blake2b(digest_size=8)
    for tree in WATCHED:
        for path in sorted(tree.rglob("*.py")):
            digest.update(path.as_posix().encode())
            digest.update(path.read_bytes())
    _fingerprint_cache = digest.hexdigest()
    return _fingerprint_cache


def _key(board: Board, name: str) -> Path:
    """이 (신호 · 종목 · 축 · 데이터) 조합의 캐시 파일.

    Note:
        ⚠️ **데이터도 열쇠다.** 적재가 늘면 방아쇠가 늘어나므로 봉 수와 처음·끝
        시각을 넣는다. 안 넣으면 백필 뒤에 옛 방아쇠가 조용히 나온다.
    """
    frame = board.frame
    stamp = f"{len(frame)}:{frame.ts[0] if frame.ts else 0}:{frame.ts[-1] if frame.ts else 0}"
    tag = blake2b(stamp.encode(), digest_size=4).hexdigest()
    safe = name.replace("/", "_").replace("(", "_").replace(")", "")
    return ROOT / fingerprint() / f"{board.symbol}_{board.timeframe.value}_{safe}_{tag}.parquet"


def triggers(signal: Signal, board: Board, *, hits: Hits | None = None) -> list[Fired]:
    """이 신호의 방아쇠 — 캐시가 있으면 읽고, 없으면 계산하고 남긴다.

    Args:
        signal: 신호.
        board: 봉.
        hits: 적중 집계기. 리포트에 히트율을 실으려면 넘긴다.

    Returns:
        방아쇠들 — 오름차순.

    Note:
        🔴 **캐시가 깨졌으면 조용히 넘기지 않고 다시 계산한다.** 읽다 실패한 것을
        빈 목록으로 돌려주면 *"이 신호는 안 터졌다"* 가 되고, 그것이 표에서는
        멀쩡해 보인다.

        ⭐ 성과 측정은 이 함수 **바깥**에 있다. 측정 항목을 늘려도 여기는 안 바뀌고,
        그것이 캐시를 나눈 이유다.
    """
    path = _key(board, signal.name)
    if path.exists():
        found = _read(path)
        if found is not None:
            if hits is not None:
                hits.hit += 1
            return found

    if hits is not None:
        hits.miss += 1
    stamps = board.frame.ts
    made = [
        Fired(index=one.index, direction=one.direction, ts=stamps[one.index])
        for one in signal.fire(board)
    ]
    _write(path, made)
    return made


def _read(path: Path) -> list[Fired] | None:
    """캐시 한 장. 못 읽으면 `None` (다시 계산하라는 뜻).

    Note:
        ⚠️ `ts` 열이 없으면 **옛 형식**이다. 그때도 `None` 을 내 다시 계산하게 한다 —
        없는 열을 0 으로 채우면 시간순 정렬이 통째로 무너진다.
    """
    try:
        import pyarrow.parquet as pq

        read = cast("Callable[[Path], object]", pq.read_table)  # pyright: ignore[reportUnknownMemberType]
        table = cast("dict[str, list[object]]", read(path).to_pydict())  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    except (OSError, ValueError):
        return None
    if "ts" not in table:
        return None
    return [
        Fired(
            index=int(cast("int", index)),
            direction=Direction(str(way)),
            ts=int(cast("int", stamp)),
        )
        for index, way, stamp in zip(table["index"], table["direction"], table["ts"], strict=True)
    ]


def _write(path: Path, found: list[Fired]) -> None:
    """캐시 한 장을 남긴다 — 실패해도 계산은 이미 끝났으므로 조용히 넘어간다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    array = cast("Callable[[list[object]], object]", pa.array)  # pyright: ignore[reportUnknownMemberType]
    make = cast("Callable[[dict[str, object]], object]", pa.table)  # pyright: ignore[reportUnknownMemberType]
    write = cast("Callable[[object, Path], None]", pq.write_table)  # pyright: ignore[reportUnknownMemberType]

    path.parent.mkdir(parents=True, exist_ok=True)
    columns: dict[str, object] = {
        "index": array([one.index for one in found]),
        "direction": array([one.direction.value for one in found]),
        "ts": array([one.ts for one in found]),
    }
    temporary = path.with_suffix(".partial")
    try:
        write(make(columns), temporary)
        temporary.replace(path)
    except OSError:
        # ⚠️ 캐시는 편의다. 못 써도 이번 실행은 정상이며, 다음에 다시 계산한다.
        temporary.unlink(missing_ok=True)
