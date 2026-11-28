"""큰 공개 파일을 임시 파일로 흘려 받기 — 메모리에 통째로 담지 않는다 (T1057).

짧게 사는 받기 프로세스(`apps.supply_fetch`)가 쓴다. 표준 라이브러리만 쓴다 — 그 프로세스가 무거운
모듈을 싣지 않게(1 GB 서버). 응답을 256 KB 씩 읽어 gzip 이면 바로 풀어 쓴다(`Accept-Encoding:
gzip` 만 청한다 · 표준 라이브러리는 br 을 못 푼다).

🔴 조용히 넘기지 않는다(규칙 #8) — 200 이 아니거나 형식이 JSON 이 아니면(유료 안내문 · 막힘 페이지)
예외를 던진다. 도중에 끊긴 응답(길이 모자람 · gzip 끝 없음 · 연결 끊김)은 전송 실패로 보고 다시
묻는다. 부르는 쪽이 그 파일을 실패로 센다. 개인 정보를 싣지 않는다(일반 User-Agent).
"""

from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "Mozilla/5.0"
"""일반 이름 — 개인 정보 · 제품 이름을 싣지 않는다."""
READ_BLOCK = 1 << 18
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
"""다시 물을 만한 응답(잠깐 막힘 · 서버 쪽 오류). 나머지 4xx 는 다시 물어도 같은 답이다."""


class FileFetchError(RuntimeError):
    """파일을 받지 못했다 — 200 아님 · JSON 아님 · 전송 실패(재시도 뒤).

    Attributes:
        status: 마지막 응답 상태(전송 실패면 None).
        url: 요청 주소.
    """

    def __init__(self, message: str, *, url: str, status: int | None = None) -> None:
        """메시지 · 주소 · 상태를 담는다."""
        super().__init__(message)
        self.url = url
        self.status = status


@dataclass(frozen=True, slots=True)
class FetchInfo:
    """받기 한 번의 기록(관측 · 나중에 일정이 바뀌었는지 잴 재료).

    Attributes:
        url: 주소.
        status: 응답 상태.
        content_type: 형식 머리.
        etag: `ETag` 머리(없으면 빈 문자열).
        last_modified: `Last-Modified` 머리(없으면 빈 문자열).
        wire_bytes: 선 위 바이트(압축된 채).
        bytes: 풀어 쓴 바이트.
        seconds: 걸린 초.
    """

    url: str
    status: int
    content_type: str
    etag: str
    last_modified: str
    wire_bytes: int
    bytes: int
    seconds: float


def fetch_to(
    url: str,
    dst: Path,
    *,
    timeout_s: float,
    tries: int = 3,
    backoff_s: float = 2.0,
    accept_json: bool = True,
) -> FetchInfo:
    """`url` 을 `dst` 에 흘려 받는다.

    Args:
        url: 주소.
        dst: 쓸 파일(덮어쓴다).
        timeout_s: 요청 하나의 제한 시간(초).
        tries: 전송 실패 · 잠깐 막힘(`RETRY_STATUS`)일 때 모두 몇 번 묻나.
        backoff_s: 다시 묻기 전 쉼(초 · 번마다 두 배).
        accept_json: 참이면 형식 머리에 `json` 이 없을 때 실패로 본다(유료 안내문 · 막힘 페이지).

    Returns:
        받기 기록.

    Raises:
        FileFetchError: 200 이 아님 · JSON 아님 · 재시도 뒤에도 전송 실패.
    """
    last: FileFetchError | None = None
    for attempt in range(max(1, tries)):
        if attempt:
            time.sleep(backoff_s * (2 ** (attempt - 1)))
        try:
            return _fetch_once(url, dst, timeout_s=timeout_s, accept_json=accept_json)
        except FileFetchError as exc:
            last = exc
            if exc.status is not None and exc.status not in RETRY_STATUS:
                break
    assert last is not None
    raise last


def _fetch_once(url: str, dst: Path, *, timeout_s: float, accept_json: bool) -> FetchInfo:
    t0 = time.perf_counter()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp, dst.open("wb") as fh:
            status = int(resp.status)
            ctype = str(resp.headers.get("Content-Type") or "")
            enc = str(resp.headers.get("Content-Encoding") or "")
            if status != 200:
                raise FileFetchError(f"응답 {status}", url=url, status=status)
            if accept_json and "json" not in ctype.lower():
                head = resp.read(300).decode("utf-8", "replace")
                raise FileFetchError(
                    f"JSON 아님({ctype or '형식 없음'}) — 유료 안내문 · 막힘 의심: {head[:120]!r}",
                    url=url,
                    status=status,
                )
            unzip = zlib.decompressobj(16 + zlib.MAX_WBITS) if enc == "gzip" else None
            wire = written = 0
            while True:
                block = resp.read(READ_BLOCK)
                if not block:
                    break
                wire += len(block)
                if unzip is not None:
                    block = unzip.decompress(block)
                fh.write(block)
                written += len(block)
            if unzip is not None:
                tail = unzip.flush()
                fh.write(tail)
                written += len(tail)
                if not unzip.eof:
                    raise FileFetchError("전송 실패: gzip 끝이 없다(도중에 끊김)", url=url)
            declared = str(resp.headers.get("Content-Length") or "")
            if declared.isdigit() and wire < int(declared):
                # `read(n)` 은 길이가 모자란 채 연결이 끊겨도 예외 없이 빈 덩이를 준다 — 직접 맞댄다
                raise FileFetchError(
                    f"전송 실패: 받은 {wire} < 머리 길이 {declared}(도중에 끊김)", url=url
                )
            return FetchInfo(
                url=url,
                status=status,
                content_type=ctype,
                etag=str(resp.headers.get("ETag") or ""),
                last_modified=str(resp.headers.get("Last-Modified") or ""),
                wire_bytes=wire,
                bytes=written,
                seconds=round(time.perf_counter() - t0, 3),
            )
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(300).decode("utf-8", "replace")
        except Exception:  # 본문을 못 읽어도 상태는 안다
            body = ""
        raise FileFetchError(
            f"응답 {exc.code}: {body[:120]!r}", url=url, status=int(exc.code)
        ) from exc
    except FileFetchError:
        raise
    except (
        urllib.error.URLError,
        http.client.HTTPException,  # IncompleteRead(길이 모자람) · 머리 꼴 오류 — 다시 물을 만하다
        TimeoutError,
        OSError,
        zlib.error,
    ) as exc:
        raise FileFetchError(f"전송 실패: {type(exc).__name__}: {exc}"[:200], url=url) from exc


__all__ = ["USER_AGENT", "FetchInfo", "FileFetchError", "fetch_to"]
