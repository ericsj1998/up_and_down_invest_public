"""거래소 **요율 한도**를 재고 기억한다 — 짐작 대신 측정 (2026-08-29 사고).

## 왜 생겼나

`make rebuild` 직후 Binance 가 IP 밴을 걸었다:

    418 {"code":-1003,"msg":"Way too many requests; IP(...) banned until ...
         Please use the websocket for live updates to avoid bans."}

하필 펀드 복구 중이라 BINANCE 펀드가 통째로 안 붙었고, 그 판 6개가 예산 없이 돌았다.
그리고 **밴이 2분에서 74분으로 늘어났다** — 밴 중에도 계속 두드렸기 때문이다.

## 🔴 우리는 얼마나 쓰는지 몰랐다

실측 분당 200~360건. 그런데 한도는 **건수가 아니라 가중치**다 (`klines` 한 번이
`ping` 열 번일 수 있다). 거래소는 응답 헤더로 지금 소모량을 알려 주는데
**그것을 한 번도 읽지 않았다** — 즉 한계선까지 얼마나 남았는지 모른 채 달리고 있었다.

⇒ 이 모듈은 **헤더를 읽어 남긴다.** 줄이는 작업의 효과도 이걸로만 잴 수 있다.

## ⛔ 여기서 요청을 막지 않는다

세는 것과 막는 것은 다른 일이다. 막는 것을 여기 넣으면 조회 실패가 곧 정지가 되고,
그것은 **리스크 감소 행동까지** 막는다 (§1.2.1 · 절대 규칙 #8-1). 이 모듈은 사실을
기록하고, 무엇을 할지는 부르는 쪽이 정한다.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from updown.common.logging.setup import get_logger

_logger = get_logger("marketdata.ratelimit")

BINANCE_WEIGHT_HEADERS = ("x-mbx-used-weight-1m", "x-mbx-used-weight")
"""Binance 가 **1분 창에 지금까지 쓴 가중치**를 실어 주는 헤더."""

GATE_HEADERS = ("x-gate-ratelimit-remain", "x-gate-ratelimit-limit")
"""Gate 의 남은 횟수 / 한도."""

LOUD_AT = 0.7
"""이 비율을 넘으면 로그를 남긴다 — 넘고 나서 아는 것보다 다가갈 때 아는 것이 낫다."""

_BANNED_UNTIL = re.compile(r"banned until (\d{10,13})")


@dataclass
class Meter:
    """거래소 하나의 소모 현황.

    Attributes:
        used: 마지막으로 본 소모량 (Binance 는 가중치, Gate 는 쓴 횟수).
        limit: 한도. 거래소가 안 알려 주면 0.
        at: 마지막으로 읽은 시각 (epoch 초).
        banned_until: 밴 만료 (epoch 초). 밴이 아니면 0.
    """

    used: int = 0
    limit: int = 0
    at: float = 0.0
    banned_until: float = 0.0

    @property
    def share(self) -> float:
        """한도 대비 소모 비율 (0~1). 한도를 모르면 0."""
        return self.used / self.limit if self.limit > 0 else 0.0

    @property
    def banned(self) -> bool:
        """지금 밴 중인가."""
        return self.banned_until > time.time()


_METERS: dict[str, Meter] = {}
_LOCK = Lock()

PATH_WINDOW_S = 60.0
PATH_REPORT_EVERY_S = 60.0
_PATHS: dict[str, deque[tuple[float, str, int]]] = {}
"""거래소 → `(시각, 경로, weight 증분)` 60초 창 (T217 · 2026-09-04).

🔴 **어느 경로가 먹는지 몰라서** 세 번 짐작으로 깎았는데 중앙값이 89% 에 머물렀다. 짐작을
그만두고 응답마다 경로와 `used` 증분을 센다. 증분은 동시 요청이 겹치면 섞이지만 (다른 요청의
weight 가 이 응답에 묻어 온다) 분 단위 합계로 보면 **누가 큰지**는 정확히 드러난다.
"""
_USED_SEEN: dict[str, deque[tuple[float, int]]] = {}
"""거래소 → `(시각, 헤더 used)` 60초 창 — 창 안 **최댓값**이 그 분의 진짜 소모(IP 전체)다."""
_LAST_REPORT: dict[str, float] = {}

BINANCE_PATH_WEIGHT: dict[str, int] = {
    "/fapi/v1/klines": 2,  # limit ≤499 (klines_limit_for 가 보장 · 시드 802 는 5)
    "/fapi/v2/positionRisk": 5,
    "/fapi/v1/openOrders": 1,  # symbol 있음 (없으면 40)
    "/fapi/v1/openAlgoOrders": 1,
    "/fapi/v1/allOrders": 5,
    "/fapi/v2/account": 5,
    "/fapi/v2/balance": 5,
    "/fapi/v1/income": 30,
    "/fapi/v1/exchangeInfo": 1,
    "/fapi/v1/ticker/price": 1,
    "/fapi/v1/premiumIndex": 1,
    "/fapi/v1/depth": 2,  # limit 20
    "/fapi/v1/order": 1,
    "/fapi/v1/algoOrder": 1,
    "/fapi/v1/leverage": 1,
    "/fapi/v1/time": 1,
}
"""바이낸스 USDT-M 공식 weight 표 (2026-09 문서). 헤더 증분으로 경로별 몫을 재려 했으나 동시
요청이 겹치면 값이 비단조라 쓸 수 없었다 (실측: 분당 9만이 찍혔다). 건수 x 공식 weight 가 정직하다.
표에 없는 경로는 1 로 세고 `?` 표시로 드러낸다."""


def _count_path(venue: str, path: str, used: int, now: float) -> None:
    weight = BINANCE_PATH_WEIGHT.get(path, 1) if venue == "BINANCE" else 1
    with _LOCK:
        box = _PATHS.setdefault(venue, deque())
        box.append((now, path or "?", weight))
        while box and now - box[0][0] > PATH_WINDOW_S:
            box.popleft()
        seen = _USED_SEEN.setdefault(venue, deque())
        seen.append((now, used))
        while seen and now - seen[0][0] > PATH_WINDOW_S:
            seen.popleft()


def peak_used_1m(venue: str) -> int:
    """최근 60초에 헤더가 말한 소모 최댓값.

    Args:
        venue: 거래소 이름.

    Returns:
        가중치 소모 최댓값 — IP 전체 기준이라 이 프로세스 밖 소비까지 포함한다. 기록이 없으면 0.
    """
    now = time.time()
    with _LOCK:
        return max((u for t, u in _USED_SEEN.get(venue, ()) if now - t <= PATH_WINDOW_S), default=0)


def _agg(venue: str) -> list[tuple[str, int, int]]:
    """최근 60초 `(경로, 건수, weight)` — weight 큰 순."""
    now = time.time()
    with _LOCK:
        rows = [r for r in _PATHS.get(venue, ()) if now - r[0] <= PATH_WINDOW_S]
    agg: dict[str, list[int]] = {}
    for _, path, delta in rows:
        cell = agg.setdefault(path, [0, 0])
        cell[0] += 1
        cell[1] += delta
    return sorted(((p, n, w) for p, (n, w) in agg.items()), key=lambda t: (-t[2], -t[1]))


def paths_1m(venue: str) -> list[dict[str, object]]:
    """최근 60초 경로별 호출 집계 (화면·로그용).

    Args:
        venue: 거래소 이름.

    Returns:
        `{path, n, weight}` 행들, weight 큰 순 — 무엇이 요율을 태우는지 바로 보이게.
    """
    return [{"path": p, "n": n, "weight": w} for p, n, w in _agg(venue)]


def _maybe_report(venue: str, now: float) -> None:
    last = _LAST_REPORT.get(venue, 0.0)
    if now - last < PATH_REPORT_EVERY_S:
        return
    _LAST_REPORT[venue] = now
    top = _agg(venue)
    ours = sum(w for _, _, w in top)
    peak = peak_used_1m(venue)
    _logger.info(
        "ratelimit_paths",
        payload={
            "venue": venue,
            "total_n": sum(n for _, n, _ in top),
            "estimated_weight": ours,  # 우리 프로세스 건수 x 공식 weight
            "peak_used": peak,  # 헤더 최댓값 = IP 전체 (밖의 소비 포함)
            "unexplained": max(0, peak - ours),  # 크면 같은 IP 의 다른 클라이언트다
            "top": [{"path": p, "n": n, "weight": w} for p, n, w in top[:8]],
        },
    )


def meter(venue: str) -> Meter:
    """거래소의 계량기 — 없으면 만든다.

    Args:
        venue: 거래소 이름 (`BINANCE` · `GATE`).

    Returns:
        그 거래소의 계량기 (프로세스 전역 · 하나).
    """
    with _LOCK:
        return _METERS.setdefault(venue, Meter())


def observe(venue: str, headers: object, *, limit: int = 0, path: str = "") -> None:
    """응답 헤더에서 소모량을 읽어 기억한다.

    Args:
        venue: 거래소 이름.
        headers: 응답 헤더 (대소문자 무시 매핑이면 무엇이든).
        limit: 거래소가 헤더로 한도를 안 줄 때 쓰는 값. 0 이면 안 덮어쓴다.
        path: 이 응답의 엔드포인트 경로 — 주면 경로별 소모를 센다 (`paths_1m`).

    Note:
        ⛔ **실패해도 던지지 않는다.** 계측이 조회를 막으면 안 된다 (규칙 #8-1) —
        헤더가 없거나 모양이 바뀌어도 조용히 넘긴다. 대신 그때는 `used` 가 안 늘어
        "재고 있지 않다" 는 사실이 화면에 그대로 남는다.
    """
    try:
        got = _pick(headers, BINANCE_WEIGHT_HEADERS + GATE_HEADERS)
        if got is None:
            return
        name, raw = got
        found = meter(venue)
        found.at = time.time()
        if name == "x-gate-ratelimit-remain":
            # ⚠️ Gate 는 **남은 것**을 준다 — 쓴 것으로 뒤집는다. 한도를 모르면 못 뒤집는다.
            cap = _pick(headers, ("x-gate-ratelimit-limit",))
            found.limit = int(cap[1]) if cap else found.limit
            found.used = max(0, found.limit - int(raw)) if found.limit else 0
        else:
            found.used = int(raw)
            if limit:
                found.limit = limit
            if path:
                _count_path(venue, path, found.used, found.at)
                _maybe_report(venue, found.at)
        if found.limit and found.share >= LOUD_AT:
            _logger.warning(
                "ratelimit_close",
                payload={
                    "venue": venue,
                    "used": found.used,
                    "limit": found.limit,
                    "share": f"{found.share * 100:.0f}%",
                    "note": "한도에 가깝다 — 넘으면 IP 밴이고, 밴 중에 계속 부르면 연장된다",
                },
            )
    except Exception:  # 계측이 조회를 막지 않는다 (규칙 #8-1)
        return


def note_ban(venue: str, body: str) -> float:
    """밴 응답에서 **만료 시각**을 읽어 기억한다.

    Args:
        venue: 거래소 이름.
        body: 응답 본문.

    Returns:
        만료 시각 (epoch 초). 못 읽었으면 0.

    Note:
        🔴 **밴 중에 계속 부르면 밴이 연장된다** (2026-08-29 실측: 2분 → 74분).
        그래서 만료 시각을 남긴다 — 재시도하는 쪽이 그때까지 기다릴 수 있게.

        ⚠️ 밀리초로 오므로 초로 바꾼다. 그대로 쓰면 만료가 서기 58000년이 되어
        **영원히 밴** 으로 읽힌다.
    """
    found = _BANNED_UNTIL.search(body or "")
    if not found:
        return 0.0
    raw = int(found.group(1))
    until = raw / 1000 if raw > 1e11 else float(raw)
    box = meter(venue)
    box.banned_until = until
    _logger.error(
        "ratelimit_banned",
        payload={
            "venue": venue,
            "until": until,
            "seconds": f"{max(0.0, until - time.time()):.0f}",
            "note": "밴 중에 계속 부르면 연장된다 — 만료까지 쉬어야 한다",
        },
    )
    return until


def ban_left(venue: str) -> float:
    """이 거래소가 **막혀 있는 남은 시간**(초). 안 막혔으면 0.

    Args:
        venue: 거래소 이름.

    Returns:
        남은 초. 안 막혔으면 0.

    Note:
        ⛔ **이 값으로 요청을 막지 않는다** (2026-08-29 실측으로 되돌림).

        한 번 그렇게 해 봤고 **판 6개가 통째로 죽었다**: 밴 만료를 18:34 로 기억한
        상태에서 17:44 의 계좌 조회가 **실제로는 성공했는데**(`funds_restored: 2`),
        차단을 넣은 뒤로는 그 요청을 아예 안 보내서 러너가 못 붙었다 —
        *"판이 열려 있는데 러너가 없다"* 가 6건.

        ⇒ 거래소가 말한 만료 시각은 **상한이지 약속이 아니다.** 그것을 사실로 믿고
        막으면, 거래소가 답할 준비가 된 뒤에도 우리가 안 물어본다. 그 대가는
        **손절을 관리할 러너가 없는 것**이고, 밴이 좀 길어지는 것보다 훨씬 나쁘다.

        ⇒ 지금은 **진단용**이다 — 화면과 로그가 "얼마나 남았다" 를 말하는 데 쓴다.
        속도를 늦추려면 요청을 막는 것이 아니라 **덜 부르는 쪽**(기억통·공유·좁은 창)을
        더 해야 한다.
    """
    return max(0.0, meter(venue).banned_until - time.time())


def _pick(headers: object, names: tuple[str, ...]) -> tuple[str, str] | None:
    """헤더 뭉치에서 아는 이름 하나를 꺼낸다 — 대소문자를 안 가린다."""
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    for name in names:
        raw = getter(name)
        if raw not in (None, ""):
            return name, str(raw)
    return None


@dataclass
class Snapshot:
    """화면에 내보내는 한 장 — 거래소별 소모 현황."""

    rows: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])


def snapshot() -> dict[str, dict[str, str]]:
    """지금 모든 거래소의 소모 현황 — 화면·진단용.

    Returns:
        `{거래소: {used, limit, share, banned_for}}`.
    """
    now = time.time()
    with _LOCK:
        items = list(_METERS.items())
    return {
        venue: {
            "used": str(box.used),
            "limit": str(box.limit),
            "share": f"{box.share * 100:.0f}%" if box.limit else "?",
            "banned_for": f"{max(0.0, box.banned_until - now):.0f}",
            "age": f"{now - box.at:.0f}" if box.at else "?",
        }
        for venue, box in items
    }
