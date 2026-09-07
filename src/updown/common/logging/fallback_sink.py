"""DB 적재 실패 시의 폴백 싱크 — JSON Lines (spec §1.2.1, plan D-13).

**집행된 행동은 반드시 어딘가에 기록된다.** 손절을 집행했는데 기록이 어디에도 없으면
감사 추적에 구멍이 생기고, 손실 귀속(§4.14)이 불가능해진다.

JSON Lines 를 택한 이유: **append 만으로 유효한 파일이 유지된다.** JSON 배열이면 닫는
괄호를 써야 완성되므로, 프로세스가 중간에 죽으면 파일 전체가 파싱 불가가 된다.
한 줄이 한 레코드면 마지막 줄이 깨져도 앞의 레코드는 모두 살아남는다.
"""

import json
from collections.abc import Iterator
from pathlib import Path

from updown.common.logging.event_sink import EventRecord
from updown.common.paths import under

DEFAULT_FALLBACK_PATH = under("event_logs_fallback.jsonl")
"""기본 폴백 파일 경로.

`logs/` 는 `.gitignore` 대상이다 — 감사 로그에는 종목·수량·계좌 정보가 들어가므로
커밋되면 안 된다 (spec §8).
"""


class FallbackSinkError(RuntimeError):
    """폴백 기록조차 실패했다.

    Note:
        여기까지 실패하면 남은 경로는 stderr 뿐이다. `AuditLogger` 가 그 처리를 한다 —
        **그래도 리스크 감소 행동은 집행한다** (spec §1.2.1). 기록 실패가 손절을 막는
        것이 이 정책이 막으려는 바로 그 상황이다.
    """


class FallbackSink:
    """이벤트를 로컬 JSON Lines 파일에 append 한다.

    Note:
        동기 I/O 를 쓴다. 폴백이 동작하는 상황은 이미 DB 가 죽은 비정상 상태이고,
        그때 필요한 것은 **확실한 기록**이다. async 파일 I/O 를 얹으면 이벤트 루프가
        막힌 상황에서 폴백까지 함께 막힐 수 있다.
    """

    def __init__(self, path: Path = DEFAULT_FALLBACK_PATH) -> None:
        """폴백 싱크를 만든다.

        Args:
            path: JSON Lines 파일 경로. 상위 디렉터리는 필요 시 생성한다.
        """
        self._path = path

    @property
    def path(self) -> Path:
        """폴백 파일 경로."""
        return self._path

    def append(self, record: EventRecord) -> None:
        """레코드를 한 줄 추가한다.

        Args:
            record: 기록할 이벤트.

        Raises:
            FallbackSinkError: 디렉터리 생성·쓰기 실패.

        Note:
            매 호출마다 `flush()` 한다. 버퍼에 남은 채 프로세스가 죽으면 기록이 사라지고,
            폴백의 존재 이유가 없어진다.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.to_json_dict(), ensure_ascii=False, default=repr)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
        except OSError as exc:
            raise FallbackSinkError(f"폴백 파일 기록 실패({self._path}): {exc}") from exc

    def read_all(self) -> list[EventRecord]:
        """폴백에 쌓인 레코드를 전부 읽는다.

        Returns:
            복원된 레코드 목록. 파일이 없으면 빈 리스트.

        Note:
            **깨진 줄은 건너뛰지 않고 예외를 낸다.** 조용히 넘기면 이관 배치가 일부
            레코드를 잃은 채 파일을 비워 감사 추적이 사라진다 (spec §7).
        """
        return list(self.iter_records())

    def iter_records(self) -> Iterator[EventRecord]:
        """폴백 레코드를 순회한다.

        Yields:
            복원된 레코드.

        Raises:
            FallbackSinkError: 줄 파싱 실패 — 어느 줄인지 함께 알린다.
        """
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as handle:
            for lineno, raw in enumerate(handle, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    yield EventRecord.from_json_dict(json.loads(stripped))
                except (ValueError, KeyError) as exc:
                    raise FallbackSinkError(
                        f"폴백 파일 {self._path}:{lineno} 파싱 실패 — 수동 확인이 필요하다: {exc}"
                    ) from exc

    def is_empty(self) -> bool:
        """비어 있는가.

        Returns:
            파일이 없거나 0 바이트면 True. 이관을 건너뛸지 정하는 값이다.
        """
        return not self._path.exists() or self._path.stat().st_size == 0

    def clear(self) -> None:
        """폴백 파일을 비운다.

        Note:
            **이관이 성공한 뒤에만 부른다.** 순서가 뒤바뀌면 이관 실패 시 레코드가
            영구 유실된다. 반대 순서(비우기 → 이관)는 절대 하지 않는다.
        """
        if self._path.exists():
            self._path.write_text("", encoding="utf-8")
