"""회전하는 JSONL 로그 파일 (T211 · 2026-09-04).

## 왜 따로 만드나

앱 로그는 지금까지 **stderr 뿐**이었다 — Docker json-file 드라이버가 받아 무한히 자란다.
사용자 요구는 *"로그를 파일로 만들어 다운로드"* 이고, 그러려면 (1) 날짜별 파일이 있어야
하고 (2) 디스크를 다 먹지 않아야 하며 (3) 로그 실패가 매매를 막으면 안 된다 (규칙 #8-1).

`logging.TimedRotatingFileHandler` 를 안 쓰는 이유: structlog 는 stdlib 핸들러를 거치지
않고 직접 스트림에 쓴다. 파일 하나를 **두 경로**(structlog 직접 · stdlib 브리지)가 공유해야
하는데, 핸들러 둘이 같은 파일을 열면 회전 순간에 서로를 밟는다. 그래서 파일 객체 하나를
두고 둘 다 `write_line` 을 부른다.

## 규칙

```
파일명   <proc>-YYYY-MM-DD.jsonl   (UTC 날짜 · 절대 규칙 #7)
회전     쓰는 순간의 UTC 날짜가 바뀌면 새 파일
상한     같은 <proc> 파일 합계가 cap_bytes 를 넘으면 **오래된 것부터** 지운다
실패     예외를 삼키고 stderr 에 **한 번만** 알린다 — 손절 로그가 파일 때문에 막히면 안 된다
```
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

DEFAULT_CAP_BYTES = 2 * 1024**3
"""`<proc>-*.jsonl` 합계 상한 (2 GiB). 2026-09-05 확정 — 서버 38 GB 의 5% (T219)."""


class RotatingJsonlFile:
    """날짜별로 도는 append 전용 JSONL 파일 — 스레드 안전."""

    def __init__(self, directory: Path, proc: str, *, cap_bytes: int = DEFAULT_CAP_BYTES) -> None:
        """회전 파일을 준비한다 — 아직 열지 않는다 (첫 줄을 쓸 때 연다).

        Args:
            directory: 파일을 둘 디렉터리. 없으면 만든다.
            proc: 파일명 앞자리 (`api` · `engine`). 프로세스마다 다르게 — 같으면 두
                컨테이너가 같은 파일에 쓰다 줄이 섞인다.
            cap_bytes: 이 `proc` 의 파일 합계 상한.
        """
        self.directory = directory
        self.proc = proc
        self.cap_bytes = cap_bytes
        self._lock = threading.Lock()
        self._day = ""
        self._fh: IO[str] | None = None
        self._failed_once = False

    def current_path(self, day: str) -> Path:
        """그 날짜의 파일 경로.

        Args:
            day: `YYYY-MM-DD` (UTC).

        Returns:
            `<directory>/<proc>-<day>.jsonl`. 날짜별 파일이라 상한 정리가 오래된 날부터
            지울 수 있다.
        """
        return self.directory / f"{self.proc}-{day}.jsonl"

    def write_line(self, line: str) -> None:
        """한 줄을 쓴다. 날짜가 바뀌면 파일을 바꾸고 상한을 정리한다.

        Args:
            line: JSON 한 줄. 개행이 없으면 붙인다.

        Note:
            🔴 **절대 던지지 않는다.** 여기서 난 예외가 호출자(손절 집행 경로일 수 있다)를
            막으면 규칙 #8-1 위반이다. 실패는 stderr 에 한 번만 알리고 계속 간다.
        """
        try:
            with self._lock:
                day = datetime.now(UTC).strftime("%Y-%m-%d")
                if self._fh is None or day != self._day:
                    self._rollover(day)
                assert self._fh is not None
                self._fh.write(line if line.endswith("\n") else line + "\n")
                self._fh.flush()
        except Exception as exc:
            if not self._failed_once:
                self._failed_once = True
                notice = {
                    "level": "warning",
                    "event_type": "log_file_sink_failed",
                    "payload": {"proc": self.proc, "error": repr(exc)},
                }
                print(json.dumps(notice, ensure_ascii=False), file=sys.stderr, flush=True)

    def _rollover(self, day: str) -> None:
        """그 날짜의 파일로 갈아탄다 — 이전 파일을 닫고, 디렉터리를 보장하고, 상한을 정리한다.

        Args:
            day: `YYYY-MM-DD` (UTC).

        Note:
            `write_line` 의 락 안에서만 부른다. 예외를 여기서 잡지 않는 것은 의도다 — 삼키는
            자리는 `write_line` 하나여야 "한 번만 알린다" 가 지켜진다.
        """
        if self._fh is not None:
            self._fh.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._fh = self.current_path(day).open("a", encoding="utf-8")
        self._day = day
        self._prune()

    def _prune(self) -> None:
        """합계가 상한을 넘으면 **오래된 파일부터** 지운다. 오늘 파일은 안 지운다."""
        files = sorted(self.directory.glob(f"{self.proc}-*.jsonl"))
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= self.cap_bytes:
                break
            if f == self.current_path(self._day):
                break
            total -= f.stat().st_size
            f.unlink(missing_ok=True)

    def close(self) -> None:
        """열린 파일을 닫는다. 다음 `write_line` 이 다시 연다."""
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
                self._day = ""
