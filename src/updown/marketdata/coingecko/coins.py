"""CoinGecko 코인 목록 — id → 기호 · 주소 → 기호 표 (T1057).

`/coins/list?include_platform=true`(약 4 MB · 코인 1 만 7 천여 개)를 임시 파일로 받아 표 둘로
줄인다. 표를 어떻게 쓰는지(어느 칸을 먼저 보나)는 `analysis.supply_schedule.coin_symbol` 이
정한다 — 여기는 받은 사실만 옮긴다: id 는 마지막 줄이 이기고(id 는 고유) · 주소는 **처음 나온
코인**이 이긴다(같은 주소를 여러 코인이 적은 경우).

🔴 꼴이 다르면(배열 아님 · 코인이 너무 적음 · 유료 안내문) 예외를 던진다(규칙 #8).
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from updown.marketdata.file_fetch import FetchInfo, FileFetchError, fetch_to

BASE = "https://api.coingecko.com/api/v3"
LIST_PATH = "/coins/list"
MIN_COINS = 1000
"""이보다 적으면 정상 목록이 아니다(실측 1 만 7 천여 개 · 막힘 · 잘린 응답)."""


class CoinListError(RuntimeError):
    """코인 목록을 쓸 수 없다."""


@dataclass(frozen=True, slots=True)
class CoinList:
    """코인 목록을 줄인 표.

    Attributes:
        by_id: 코인 id → 기호(대문자 · 기호가 없으면 빈 문자열).
        by_addr: 체인 주소(소문자) → 그 주소를 처음 적은 코인의 기호(대문자).
        coins: 목록의 코인 수(관측).
    """

    by_id: dict[str, str]
    by_addr: dict[str, str]
    coins: int


READ_CHARS = 1 << 18
"""흘려 읽는 덩이(글자) — 코인 하나(수백 바이트)씩 풀고 버린다."""


def _items(path: Path) -> Iterator[object]:
    """배열 파일의 원소를 하나씩 — 통째로 `json.loads` 하지 않는다(받기 프로세스 메모리).

    Raises:
        CoinListError: 배열이 아니거나 JSON 이 깨졌다.
    """
    dec = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as fh:
        buf = fh.read(READ_CHARS)
        eof = not buf
        pos = len(buf) - len(buf.lstrip())
        if pos >= len(buf) or buf[pos] != "[":
            head = buf.strip()[:80]
            raise CoinListError(f"코인 목록이 배열이 아니다: {head!r}")
        pos += 1
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1
            if pos >= len(buf) or (buf[pos] != "]" and not eof and len(buf) - pos < 64):
                more = fh.read(READ_CHARS)
                if not more:
                    if pos >= len(buf):
                        raise CoinListError("코인 목록이 끝나지 않았다(잘린 응답)")
                    eof = True
                buf, pos = buf[pos:] + more, 0
                continue
            if buf[pos] == "]":
                return
            try:
                item, end = dec.raw_decode(buf, pos)
            except json.JSONDecodeError as exc:
                more = "" if eof else fh.read(READ_CHARS)
                if not more:
                    raise CoinListError(f"코인 목록이 JSON 이 아니다: {exc}"[:200]) from exc
                buf, pos = buf[pos:] + more, 0
                continue
            yield item
            pos = end
            if pos > READ_CHARS:
                buf, pos = buf[pos:], 0


def parse_coin_list(path: Path, *, min_coins: int = MIN_COINS) -> CoinList:
    """받은 목록 파일 → 표(원소를 하나씩 흘려 읽는다).

    Args:
        path: 받은 파일.
        min_coins: 이보다 적으면 실패.

    Returns:
        표 — `json.loads` 로 통째로 읽어 차례로 훑은 것과 같다(객체가 아닌 원소는 건너뜀).

    Raises:
        CoinListError: JSON 이 아님 · 배열이 아님 · 코인이 너무 적음.
    """
    by_id: dict[str, str] = {}
    by_addr: dict[str, str] = {}
    n = 0
    for item in _items(path):
        if not isinstance(item, dict):
            continue
        coin = cast("dict[str, Any]", item)
        n += 1
        symbol = str(coin.get("symbol") or "").upper()
        cid = coin.get("id")
        if isinstance(cid, str):  # 문자열이 아닌 id 는 일정 자료의 표기와 맞을 일이 없다
            by_id[cid] = symbol
        platforms: object = coin.get("platforms") or {}
        if isinstance(platforms, dict):
            for addr in cast("dict[str, Any]", platforms).values():
                if isinstance(addr, str) and addr:
                    by_addr.setdefault(addr.lower(), symbol)
    if n < min_coins:
        raise CoinListError(f"코인 목록이 너무 적다: {n} < {min_coins}")
    return CoinList(by_id=by_id, by_addr=by_addr, coins=n)


class CoinListSource:
    """CoinGecko 코인 목록 읽기(표준 라이브러리 · 동기 — 짧게 사는 받기 프로세스 전용)."""

    def __init__(self, *, base: str = BASE, timeout_s: float = 120.0, tries: int = 4) -> None:
        """주소 · 제한 시간 · 재시도 횟수(분당 제한 429 를 넘길 만큼 · 쉼은 2 · 4 · 8 초)."""
        self._base = base.rstrip("/")
        self._timeout_s = timeout_s
        self._tries = tries
        self.calls = 0

    def coin_list(self, tmp: Path) -> tuple[CoinList, FetchInfo]:
        """코인 목록을 받아 줄인다(임시 파일은 바로 지운다).

        Raises:
            CoinListError: 받기 실패 · 꼴이 다름.
        """
        dst = tmp / "_coins.json"
        query = urllib.parse.urlencode({"include_platform": "true"})
        self.calls += 1
        try:
            info = fetch_to(
                f"{self._base}{LIST_PATH}?{query}",
                dst,
                timeout_s=self._timeout_s,
                tries=self._tries,
            )
            return parse_coin_list(dst), info
        except FileFetchError as exc:
            raise CoinListError(str(exc)) from exc
        finally:
            dst.unlink(missing_ok=True)


__all__ = ["CoinList", "CoinListError", "CoinListSource", "parse_coin_list"]
