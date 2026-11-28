"""토큰 공급 일정 파일 — 목록 · 프로젝트별 일정을 흘려 받아 필요한 칸만 남긴다 (T1057).

파일 꼴(2026-11 실측 · 375 개 모두): 맨 앞이 `{"documentedData":{"data":[` 이고, 구역마다
`{"label":…,"data":[{"timestamp":N,<풀린 양 칸>:X,…},…]}` 가 이어진 뒤 `],"tokenAllocation":` 로
일정 부분이 끝난다(칸 이름은 자료의 것 그대로 — `_TOK`). 그 뒤 `},"metadata":{…}` 부터
파일 끝까지(사건 목록 · 토큰 주소 · `gecko_id`)는 작다.

큰 파일(16 MB)을 통째로 `json.loads` 하면 파이썬 객체가 파일의 몇 배가 된다 — 그래서 1 MB 씩 흘려
읽으며 정규식으로 구역 이름 · 점(시각 · 풀린 양)만 뽑고, 꼬리(metadata 부터)만 JSON 으로 푼다
(최대 약 50 MB · T1051 §2 실측 · 결과는 `json.loads` 와 같음).

🔴 꼴이 다르면 조용히 넘기지 않는다(규칙 #8): 머리가 다름 · 일정 부분 끝(`tokenAllocation`)이 없음 ·
`metadata` 가 없음 · 점 꼴이 다름(시각 칸 수 ≠ 뽑은 점 수 — 칸 순서가 바뀌면 여기서 걸린다)이면
`ScheduleFeedError`. 숫자 · 이름을 못 푸는 것(깨진 파일 · 도중 끊긴 파일)도 같은 예외로
바꾼다 — 부르는 쪽이 그 파일을 실패로 센다.

무엇이 사건이고 크기를 어떻게 재는지는 모른다 — 그것은 `analysis.supply_schedule` 이 한다.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from updown.marketdata.file_fetch import FetchInfo, FileFetchError, fetch_to

BASE = "https://defillama-datasets.llama.fi"
LIST_PATH = "/emissionsProtocolsList"
FILE_PATH = "/emissions/"
CHUNK = 1 << 20
MARGIN = 4096
"""덩이 끝 이만큼은 다음 덩이와 함께 읽는다 — 점 하나(약 80 바이트)가 덩이 사이에서 잘리지 않게."""
HEAD = b'{"documentedData":{"data":['
EVENT_KEYS = ("category", "unlockType", "noOfTokens", "timestamp")
"""사건 줄에서 남기는 칸(나머지 설명 글 등은 버린다)."""

_TOK = re.compile(
    rb'\{"label":"(?P<lab>(?:[^"\\]|\\.)*)","data":\['
    rb'|\{"timestamp":(?P<ts>-?\d+),"unlocked":(?P<u>-?[0-9][0-9.eE+-]*|null)'
    rb'|(?P<ta>\],"tokenAllocation":)'
    rb'|(?P<md>\},"metadata":\{)'
)
_STAMP = re.compile(rb'"timestamp":')


class ScheduleFeedError(RuntimeError):
    """일정 파일 · 목록을 쓸 수 없다 — 받기 실패 · 꼴이 다름 · 막힘."""


@dataclass(frozen=True, slots=True)
class Section:
    """일정 구역 하나 — 시각 오름차순.

    Attributes:
        label: 구역 이름.
        ts: 점 시각(UTC 초).
        released: 그 시각까지 풀린 양(누적). 값이 null 이면 0.
    """

    label: str
    ts: array[int]
    released: array[float]


@dataclass(frozen=True, slots=True)
class Schedule:
    """프로젝트 하나의 공급 일정(필요한 칸만).

    Attributes:
        slug: 목록 이름.
        sections: 점이 있는 구역들(파일 차례).
        events: `metadata.events` 줄들 — `EVENT_KEYS` 칸만 받은 그대로(값 해석은 분석 쪽).
        token: `metadata.token`(예: `coingecko:<id>` · `<체인>:<주소>`) — 없으면 빈 문자열.
        gecko_id: 맨 위 `gecko_id` — 없으면 None.
        points: 뽑은 점 수(관측).
    """

    slug: str
    sections: tuple[Section, ...]
    events: tuple[Mapping[str, Any], ...]
    token: str
    gecko_id: str | None
    points: int


def _sorted_section(label: str, ts: array[int], released: array[float]) -> Section:
    """시각이 엄격히 오르지 않으면 (시각, 양) 쌍으로 다시 줄 세운다 — `sorted(pts)` 와 같은 차례."""
    if any(ts[i] >= ts[i + 1] for i in range(len(ts) - 1)):
        pairs = sorted(zip(ts, released, strict=True))
        ts = array("q", [a for a, _b in pairs])
        released = array("d", [b for _a, b in pairs])
    return Section(label=label, ts=ts, released=released)


def parse_schedule(path: Path, slug: str) -> Schedule:
    """일정 파일 하나를 흘려 읽어 줄인다.

    Args:
        path: 받은 파일(풀린 JSON).
        slug: 목록 이름.

    Returns:
        줄인 일정.

    Raises:
        ScheduleFeedError: 꼴이 다르다(모듈 설명) · 숫자 · 이름을 못 푼다 · 파일을 못 읽는다.
    """
    try:
        return _parse_schedule(path, slug)
    except ScheduleFeedError:
        raise
    except (ValueError, OverflowError, OSError, UnicodeDecodeError) as exc:
        raise ScheduleFeedError(f"{slug}: 못 푼다: {type(exc).__name__}: {exc}"[:200]) from exc


def _parse_schedule(path: Path, slug: str) -> Schedule:
    raw_secs: list[tuple[str, array[int], array[float]]] = []
    cur: tuple[str, array[int], array[float]] | None = None
    in_series = True
    md_off = -1
    points = stamps = 0
    with path.open("rb") as fh:
        if fh.read(len(HEAD)) != HEAD:
            raise ScheduleFeedError(f"{slug}: 머리가 다르다(documentedData 로 시작하지 않음)")
        fh.seek(0)
        pos = 0
        buf = b""
        eof = False
        while not eof and md_off < 0:
            block = fh.read(CHUNK)
            eof = not block
            buf += block
            cut = len(buf) if eof else len(buf) - MARGIN
            last = 0
            counting = in_series
            series_end: int | None = None
            for m in _TOK.finditer(buf):
                if m.start() >= cut:
                    break
                last = m.end()
                if m.group("md") is not None:
                    md_off = pos + m.start() + 2  # '"metadata"' 의 따옴표 자리
                    if in_series:
                        raise ScheduleFeedError(f"{slug}: 일정 부분 끝(tokenAllocation)이 없다")
                    break
                if not in_series:
                    continue
                lab = m.group("lab")
                if lab is not None:
                    label = json.loads(b'"' + lab + b'"')
                    cur = (str(label), array("q"), array("d"))
                    raw_secs.append(cur)
                elif m.group("ts") is not None:
                    if cur is None:
                        raise ScheduleFeedError(f"{slug}: 구역 이름 앞에 점이 있다")
                    u = m.group("u")
                    cur[1].append(int(m.group("ts")))
                    cur[2].append(0.0 if u == b"null" else (float(u) or 0.0))
                    points += 1
                elif m.group("ta") is not None:
                    in_series = False
                    series_end = m.start()
            keep = max(cut, last)
            if counting:
                stamps += len(_STAMP.findall(buf, 0, keep if series_end is None else series_end))
            if md_off >= 0:
                break
            pos += keep
            buf = buf[keep:]
        if md_off < 0:
            raise ScheduleFeedError(f"{slug}: metadata 가 없다")
        if stamps != points:
            raise ScheduleFeedError(
                f"{slug}: 점 꼴이 다르다(시각 칸 {stamps} · 뽑은 점 {points}) — 칸 순서가 바뀌었나"
            )
        fh.seek(md_off)
        tail = fh.read()
    try:
        top = json.loads(b"{" + tail)
    except ValueError as exc:
        raise ScheduleFeedError(f"{slug}: metadata 꼬리를 못 푼다: {exc}"[:200]) from exc
    if not isinstance(top, dict):
        raise ScheduleFeedError(f"{slug}: metadata 꼬리가 객체가 아니다")
    body = cast("dict[str, Any]", top)
    meta = body.get("metadata")
    if not isinstance(meta, dict):
        raise ScheduleFeedError(f"{slug}: metadata 가 객체가 아니다")
    md = cast("dict[str, Any]", meta)
    raw_events: object = md.get("events") or []
    if not isinstance(raw_events, list):
        raise ScheduleFeedError(f"{slug}: metadata.events 가 배열이 아니다")
    events: list[Mapping[str, Any]] = []
    for ev in cast("list[Any]", raw_events):
        if isinstance(ev, dict):
            item = cast("dict[str, Any]", ev)
            events.append({k: item[k] for k in EVENT_KEYS if k in item})
    gid = body.get("gecko_id")
    sections = tuple(
        _sorted_section(label, ts, released) for label, ts, released in raw_secs if len(ts)
    )
    return Schedule(
        slug=slug,
        sections=sections,
        events=tuple(events),
        token=str(md.get("token") or ""),
        gecko_id=None if gid is None else str(gid),
        points=points,
    )


def parse_protocol_list(path: Path) -> tuple[str, ...]:
    """목록 파일 → 이름들.

    Raises:
        ScheduleFeedError: 배열이 아니거나 이름이 문자열이 아니다(유료 안내문 · 막힘 꼴).
    """
    try:
        raw = json.loads(path.read_bytes())
    except ValueError as exc:
        raise ScheduleFeedError(f"목록이 JSON 이 아니다: {exc}"[:200]) from exc
    if not isinstance(raw, list):
        raise ScheduleFeedError(f"목록이 배열이 아니다: {type(raw).__name__}")
    names = cast("list[Any]", raw)
    if not all(isinstance(x, str) and x for x in names):
        raise ScheduleFeedError("목록에 이름이 아닌 칸이 있다")
    return tuple(cast("list[str]", names))


class EmissionsSource:
    """공급 일정 공개 파일 읽기(표준 라이브러리 · 동기 — 짧게 사는 받기 프로세스 전용)."""

    def __init__(
        self,
        *,
        base: str = BASE,
        timeout_s: float = 120.0,
        pace_s: float = 0.1,
        tries: int = 3,
    ) -> None:
        """주소 · 제한 시간 · 간격을 받는다.

        Args:
            base: 데이터셋 주소.
            timeout_s: 요청 하나의 제한 시간(초).
            pace_s: 파일 사이 쉼(초).
            tries: 전송 실패 · 잠깐 막힘일 때 모두 몇 번 묻나.
        """
        self._base = base.rstrip("/")
        self._timeout_s = timeout_s
        self._pace_s = pace_s
        self._tries = tries
        self.calls = 0
        self.wire_bytes = 0

    def _get(self, url: str, dst: Path) -> FetchInfo:
        if self.calls and self._pace_s > 0:
            time.sleep(self._pace_s)
        self.calls += 1
        try:
            info = fetch_to(url, dst, timeout_s=self._timeout_s, tries=self._tries)
        except FileFetchError as exc:
            raise ScheduleFeedError(str(exc)) from exc
        self.wire_bytes += info.wire_bytes
        return info

    def protocol_list(self, tmp: Path) -> tuple[tuple[str, ...], FetchInfo]:
        """목록(프로젝트 이름들).

        Args:
            tmp: 임시 파일을 둘 폴더(받은 뒤 지운다).

        Returns:
            `(이름들, 받기 기록)`.

        Raises:
            ScheduleFeedError: 받기 실패 · 꼴이 다름.
        """
        dst = tmp / "_list.json"
        try:
            info = self._get(self._base + LIST_PATH, dst)
            return parse_protocol_list(dst), info
        finally:
            dst.unlink(missing_ok=True)

    def schedule(self, slug: str, tmp: Path) -> tuple[Schedule, FetchInfo]:
        """프로젝트 하나의 일정.

        Args:
            slug: 목록 이름.
            tmp: 임시 파일을 둘 폴더(받아 읽은 뒤 바로 지운다 — 원본을 쌓지 않는다).

        Returns:
            `(줄인 일정, 받기 기록)`.

        Raises:
            ScheduleFeedError: 받기 실패 · 꼴이 다름.
        """
        dst = tmp / "_file.json"
        try:
            info = self._get(self._base + FILE_PATH + urllib.parse.quote(slug, safe=""), dst)
            return parse_schedule(dst, slug), info
        finally:
            dst.unlink(missing_ok=True)


__all__ = [
    "BASE",
    "EmissionsSource",
    "Schedule",
    "ScheduleFeedError",
    "Section",
    "parse_protocol_list",
    "parse_schedule",
]
