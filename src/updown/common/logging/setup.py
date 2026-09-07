"""structlog 설정 — JSON · UTC · 고정 필드 (spec §2.1, §12.3, §4.14).

표준 필드를 **고정**한다: `ts / level / module / event_type / trace_id / payload`.
필드 이름이 로그마다 다르면 검색·집계가 불가능해지고, 그러면 §4.14 손실 귀속 리포트를
만들 수 없다.

`ts` 는 ISO8601 **UTC** 다 (spec §12.3, 절대 규칙 #7). 로컬 시각으로 찍으면 컨테이너
타임존에 따라 로그 순서가 뒤바뀐다.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, TextIO, cast

import structlog

from updown.common.logging.context import get_trace_id
from updown.common.logging.files import DEFAULT_CAP_BYTES, RotatingJsonlFile

_file_sink: RotatingJsonlFile | None = None
"""지금 붙어 있는 파일 싱크 — 재설정 시 닫으려고 들고 있는다."""


class _TeeLogger:
    """structlog 가 렌더한 JSON 한 줄을 stderr 와 파일에 같이 쓴다.

    structlog 의 `PrintLogger` 는 스트림 하나에만 쓴다. 파일을 더하려고 structlog 를 stdlib
    경유로 바꾸면 처리 체인·시험(`stream=` 캡처)이 전부 흔들린다 — 로거 팩토리만 바꾸는 것이
    가장 작은 변경이다.
    """

    def __init__(self, primary: TextIO, sink: RotatingJsonlFile | None) -> None:
        self._primary = primary
        self._sink = sink

    def msg(self, message: str) -> None:
        """렌더된 JSON 한 줄을 스트림에 쓰고, 파일 싱크가 있으면 거기에도 쓴다.

        Args:
            message: `JSONRenderer` 가 만든 한 줄. 개행은 여기서 붙인다.

        Note:
            스트림을 매 줄 flush 하는 이유는 프로세스가 죽는 순간의 마지막 줄이 가장
            중요하기 때문이다 (원인이 거기 있다). 파일 싱크 실패는 싱크가 스스로 삼킨다 —
            로그 때문에 주 로직이 멈추면 안 된다 (규칙 #8-1).
        """
        self._primary.write(message + "\n")
        self._primary.flush()
        if self._sink is not None:
            self._sink.write_line(message)

    # structlog 의 filtering bound logger 가 레벨별 메서드를 부른다 — 전부 msg 로 보낸다.
    log = debug = info = warning = warn = error = critical = exception = fatal = msg


class _TeeLoggerFactory:
    """structlog `logger_factory` 자리 — 호출될 때마다 같은 스트림·싱크에 묶인 로거를 준다."""

    def __init__(self, primary: TextIO, sink: RotatingJsonlFile | None) -> None:
        self._primary = primary
        self._sink = sink

    def __call__(self, *_args: Any) -> _TeeLogger:
        return _TeeLogger(self._primary, self._sink)


class _FileSinkHandler(logging.Handler):
    """stdlib 브리지용 — 포매터(JSON)가 만든 줄을 같은 파일 싱크에 쓴다."""

    def __init__(self, sink: RotatingJsonlFile) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        """레코드를 JSON 으로 포맷해 파일 싱크에 쓴다.

        Args:
            record: stdlib 로거(uvicorn · SQLAlchemy)가 낸 레코드.

        Note:
            실패는 `handleError` 로 넘긴다 — stdlib 관례대로 stderr 에 한 줄 남기고 계속 간다.
            파일 로그가 죽었다고 라이브러리 로그까지 막을 이유가 없다 (규칙 #8-1).
        """
        try:
            self._sink.write_line(self.format(record))
        except Exception:
            self.handleError(record)


#: 우리가 고정하는 최상위 필드. 그 외 키는 전부 `payload` 로 들어간다.
RESERVED_FIELDS = frozenset({"ts", "level", "module", "event_type", "trace_id", "payload"})


def _inject_trace_id(_logger: object, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """ContextVar 의 trace_id 를 이벤트에 넣는다 (spec §4.14).

    Note:
        값이 없으면 `None` 으로 남긴다. 없는 것을 없다고 적어야 "이 로그는 요청·잡
        밖에서 났다"를 나중에 알 수 있다.
    """
    event_dict.setdefault("trace_id", get_trace_id())
    return event_dict


def _normalize_shape(_logger: object, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """이벤트를 고정 스키마로 정규화한다.

    structlog 의 관례는 메시지를 `event` 키에 넣는 것인데, 우리 규약은 `event_type`
    이다 (spec §9 `event_logs.event_type`). 여기서 옮기고, 예약 필드가 아닌 키는
    전부 `payload` 아래로 모은다 — 최상위가 자유롭게 늘어나면 스키마가 무의미해진다.

    Note:
        **`_` 로 시작하는 키는 건드리지 않는다.** structlog 의 `ProcessorFormatter` 가
        서드파티 레코드에 `_record`·`_from_structlog` 메타키를 심어 두고 렌더 직전에
        `remove_processors_meta` 로 걷어내는데, 그것을 payload 로 옮겨 버리면
        `KeyError: '_record'` 로 **라이브러리 로그가 전부 유실된다.**
    """
    if "event" in event_dict and "event_type" not in event_dict:
        event_dict["event_type"] = event_dict.pop("event")
    event_dict.pop("event", None)

    payload = event_dict.get("payload")
    extras = {
        k: v for k, v in event_dict.items() if k not in RESERVED_FIELDS and not k.startswith("_")
    }
    if extras:
        merged: dict[str, Any] = {}
        if isinstance(payload, dict):
            merged.update(cast("dict[str, Any]", payload))
        merged.update(extras)
        event_dict["payload"] = merged
        for key in extras:
            del event_dict[key]
    elif payload is None:
        event_dict["payload"] = {}

    return event_dict


def configure_logging(
    level: str = "INFO",
    *,
    stream: TextIO | None = None,
    file_dir: Path | None = None,
    proc: str = "app",
    cap_bytes: int = DEFAULT_CAP_BYTES,
) -> None:
    """구조화 로깅(structlog)과 표준 logging 을 설정한다.

    Args:
        level: 로그 레벨 문자열. `Settings.log_level` 값을 넘긴다.
        stream: 출력 스트림. 기본은 stderr. 테스트가 캡처용으로 바꿀 수 있다.
        file_dir: 주면 그 디렉터리에 `<proc>-YYYY-MM-DD.jsonl` 로도 남긴다 (T211).
            `None` 이면 스트림만 — 시험은 파일을 안 만든다. 환경변수 `UPDOWN_LOG_FILES=0`
            이면 주어도 무시한다 (디스크가 없는 자리에서 끄는 스위치).
        proc: 파일명 앞자리 (`api` · `engine`).
        cap_bytes: 파일 합계 상한 — 넘으면 오래된 날짜부터 지운다.

    Note:
        🔴 **파일 싱크는 절대 기동을 막지 않는다** (규칙 #8-1). 디렉터리를 못 만들면 stderr
        로 알리고 스트림만으로 간다. 로그 파일 때문에 손절 감시가 안 뜨는 일은 없어야 한다.

        stderr 는 유지한다 — Docker 가 받는 경로이고 `docker logs` 가 계속 작동한다.
        파일은 그 **복사본**이다 (내려받기용 · T211).

        표준 `logging` 도 같은 JSON 렌더러를 타게 브릿지한다 — 라이브러리(uvicorn,
        SQLAlchemy)가 내는 로그가 평문으로 섞이면 로그 수집기가 파싱에 실패하고,
        그 라인들만 조용히 유실된다.

        `default=repr` 로 직렬화 불가 객체를 처리한다. 이것이 **시크릿 방어선**이기도
        하다 — `SecretStr` 의 `repr` 은 `**********` 이라 평문이 새지 않는다 (spec §8).
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    primary = stream or sys.stderr

    global _file_sink
    if _file_sink is not None:
        _file_sink.close()
        _file_sink = None
    sink: RotatingJsonlFile | None = None
    if file_dir is not None and os.environ.get("UPDOWN_LOG_FILES", "1") != "0":
        try:
            file_dir.mkdir(parents=True, exist_ok=True)
            sink = RotatingJsonlFile(file_dir, proc, cap_bytes=cap_bytes)
            _file_sink = sink
        except Exception as exc:
            notice = {
                "level": "warning",
                "event_type": "log_file_sink_unavailable",
                "payload": {"dir": str(file_dir), "error": repr(exc)},
            }
            print(json.dumps(notice, ensure_ascii=False), file=sys.stderr, flush=True)
            sink = None

    shared_processors: list[Any] = [
        # ⭐ **태스크에 묶인 맥락을 모든 줄에 붙인다** (T76 · 2026-08-28).
        #    라이브 러너가 `run()` 첫머리에 판 식별자를 묶으면, 그 태스크에서
        #    나가는 **어댑터 로그까지**(`gate_stop_placed` 등) 어느 판인지 달고
        #    나온다. 아무것도 안 묶여 있으면 아무 일도 안 한다 — 무해하다.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _inject_trace_id,
        _normalize_shape,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(default=repr, ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=_TeeLoggerFactory(primary, sink),
        cache_logger_on_first_use=False,
    )

    # 표준 logging → structlog 렌더러
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        # `remove_processors_meta` 가 반드시 앞에 와야 한다 — ProcessorFormatter 가
        # 내부 메타키(`_record`)를 심어 두고, 렌더러 전에 그것을 걷어내지 않으면
        # `KeyError: '_record'` 로 서드파티 로그가 **조용히 유실된다.**
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(default=repr, ensure_ascii=False),
        ],
    )
    handler = logging.StreamHandler(primary)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)  # ⚠️ [0] 이어야 한다 — test_logging 이 handlers[0] 의 포매터를 본다
    if sink is not None:
        file_handler = _FileSinkHandler(sink)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    root.setLevel(numeric_level)


def get_logger(module: str) -> structlog.stdlib.BoundLogger:
    """모듈 이름이 묶인 로거를 준다.

    Args:
        module: 로그의 `module` 필드에 들어갈 이름. 도메인 경계를 알 수 있게
            `execution.order_service` 처럼 적는다.

    Returns:
        바인딩된 로거.

    Note:
        🔴 **`.bind()` 로 만들지 않는다** (2026-09-04 실측). `get_logger().bind(...)` 는 그
        순간의 structlog 설정으로 **구체 로거를 즉시 만든다.** 모듈 로드 때(= `configure_logging`
        전) 만든 `_logger` 는 기본 ConsoleRenderer 에 묶여, 뒤에 JSON 으로 설정해도 평문으로
        stdout 에 찍히고 **파일 싱크(T211)·JSON 검색을 전부 비켜 갔다** — `live_run_resumed`
        `ratelimit_close` 같은 핵심 이벤트가 그랬다. `get_logger(module=...)` 는 초기값을 들고
        있는 **지연 프록시**라 (`cache_logger_on_first_use=False`) 매 호출에 현재 설정을 읽는다.
    """
    return structlog.get_logger(module=module)  # pyright: ignore[reportReturnType]
