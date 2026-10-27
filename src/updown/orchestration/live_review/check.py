"""라이브 점검 — 지금 · 최근 7일 · 신호를 **판단 재료**로 (live-check · 순수).

사용자 2026-10-11 *"라이브가 정상인지 확인하는 일이 많다 · 기능으로 · 클로드 코드가
판단할 수 있는 재료를 출력"*. 판정을 대신하지 않는다 — 항목마다 상태(✅ ⚠️ ⛔ ·) ·
값 · 기준을 같이 적어 읽는 쪽(사람 · Claude Code)이 근거를 보고 판단하게 한다.

재료는 라이브 매매 분석기(T444) 스냅샷 + 서버 상태 한 장(`health.txt`) + 보유 종목의
공개 봉(호출부가 받아 넘긴다). 이 모듈은 네트워크 · 파일 · 현재 시각을 직접 안 만진다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from updown.orchestration.live_review.attribution import TradeMoney, fill_basis, money_of
from updown.orchestration.live_review.health import gate_of, why_no_entry
from updown.orchestration.live_review.snapshot import Snapshot, Trade, dec, when

KST = timezone(timedelta(hours=9))
MARK = {"ok": "✅", "warn": "⚠️", "bad": "⛔", "info": "·"}
REQUIRED = ("web", "postgres", "redis", "proxy")
"""API 말고 반드시 떠 있어야 하는 컨테이너(이름 `updown_live-<이름>-1`)."""
AUDIT_RECENT = timedelta(hours=2)
"""자가 점검 문제가 이 안에 났고 아직 안 풀렸으면 ⛔."""
STOP_TRIGGER_TOL = Decimal("0.005")
"""걸린 손절과 원장 계획 손절의 허용 차(비율) — 호가 라운딩 · 손절 상향(T19) 너머만 본다."""
SLIP_STOP_WARN = Decimal("0.3")
"""손절 체결이 계획 손절보다 이만큼(%) 넘게 불리하면 ⚠️."""
FILL_BP_WARN = 30.0
"""진입 체결가가 원장 진입가와 이만큼(bp) 넘게 다르면 ⚠️ — 10bp 안팎은 정상(T372)."""
LEV_WARN = Decimal("0.25")
"""체결 배율이 계획 배율과 이 비율 넘게 다르면 ⚠️."""
EQUITY_TOL = Decimal("0.5")
"""펀드 장부 equity 와 거래소 지갑 총액의 허용 차(USDT)."""
AWAITING_FUND_GRACE = timedelta(hours=4)
"""`awaiting_fund` 로 막힌 후보가 정상인 창 — 판 기동(배포 · 재기동) 뒤 이 안 (T562 · T293).

펀드는 판을 다 되살린 뒤에 붙으므로(약 100초 · 실패하면 120초 단위로 늘어난다) 기동 직후의
첫 4H 봉 하나까지는 기다림이 날 수 있다. 그 밖에 나오면 펀드가 안 붙은 판이 있다는 뜻이다.
"""
MEM_AVAIL_WARN, SWAP_WARN, STEAL_WARN, DISK_WARN = 120, 1600, 20, 85


def kst(at: datetime | None) -> str:
    """UTC 시각 → `MM-DD HH:MM KST`(보고는 KST)."""
    return "—" if at is None else at.astimezone(KST).strftime("%m-%d %H:%M KST")


def _f(v: Decimal | float | None, nd: int = 2, sign: bool = False) -> str:
    if v is None:
        return "—"
    return f"{float(v):+,.{nd}f}" if sign else f"{float(v):,.{nd}f}"


def _dict(v: object) -> dict[str, Any]:
    return cast("dict[str, Any]", v) if isinstance(v, dict) else {}


def _rows_of(x: object) -> list[dict[str, Any]]:
    if not isinstance(x, list):
        return []
    return [cast("dict[str, Any]", r) for r in cast("list[object]", x) if isinstance(r, dict)]


def _int(v: object) -> int | None:
    try:
        return int(str(v).strip())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Row:
    """판단 재료 한 줄."""

    section: str
    item: str
    status: str
    """`ok` · `warn` · `bad` · `info`."""
    value: str
    basis: str


# ── 서버 상태 한 장 ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Health:
    """`health.txt`(서버 덤프 7단계)를 읽은 것. 없는 칸은 None."""

    at: datetime | None = None
    api: str = ""
    containers: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])
    env: dict[str, str] = field(default_factory=dict[str, str])
    leader_pttl: int | None = None
    leader_health: bool | None = None
    api_started: datetime | None = None
    halt: str = ""
    halt_at: datetime | None = None
    mem: tuple[int, int, int] | None = None
    """(전체, 사용, 가용) MB."""
    swap: tuple[int, int] | None = None
    cpu: tuple[int, int, int] | None = None
    """(user, idle, steal) %."""
    disk_pct: int | None = None


def _ints(parts: Sequence[str], n: int) -> tuple[int, ...] | None:
    vals = [_int(x) for x in parts[:n]]
    if len(vals) < n or any(v is None for v in vals):
        return None
    return tuple(cast("list[int]", vals))


def parse_health(text: str) -> Health:
    """`키|값|…` 줄들을 읽는다 — 모르는 줄은 건너뛴다(덤프가 늘어도 안 깨지게)."""
    kw: dict[str, Any] = {"containers": {}, "env": {}}
    for line in text.splitlines():
        head, *rest = line.strip().split("|")
        if not rest:
            continue
        first = rest[0].strip()
        if head == "at":
            kw["at"] = when(first)
        elif head == "api":
            kw["api"] = first
        elif head == "container" and len(rest) >= 2:
            kw["containers"][first] = (rest[1], rest[2] if len(rest) > 2 else "")
        elif head == "env" and len(rest) >= 2:
            kw["env"][first] = rest[1].strip()
        elif head == "leader_pttl":
            kw["leader_pttl"] = _int(first)
        elif head == "leader_health":
            kw["leader_health"] = {"True": True, "False": False}.get(first)
        elif head == "api_started" and first:
            kw["api_started"] = when(first[:26] + "Z")
        elif head == "halt":
            kw["halt"] = first
            kw["halt_at"] = when(rest[1]) if len(rest) > 1 else None
        elif head == "mem":
            kw["mem"] = _ints(rest, 3)
        elif head == "swap":
            kw["swap"] = _ints(rest, 2)
        elif head == "cpu":
            kw["cpu"] = _ints(rest, 3)
        elif head == "disk":
            kw["disk_pct"] = _int(first.rstrip("%"))
    return Health(**kw)


# ── 거래소 대조 ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PositionCheck:
    """거래소 포지션 한 줄 ↔ 원장 열린 매매 ↔ 걸린 손절."""

    symbol: str
    size: int
    entry: Decimal | None
    mark: Decimal | None
    upnl: Decimal | None
    margin: Decimal | None
    leverage: str
    liq: Decimal | None
    trade: Trade | None
    stop_trigger: Decimal | None
    stop_size: int | None
    problems: tuple[str, ...]


def open_trades(snap: Snapshot) -> list[Trade]:
    """원장에 열려 있는 실계좌 매매(체결됨 · 안 닫힘 · 판도 열림)."""
    closed_runs = {r.id for r in snap.runs if r.closed_at is not None}
    return [
        t
        for t in snap.trades
        if t.opened_at is not None and t.closed_at is None and t.run_id not in closed_runs
    ]


def _stop_of(order: Mapping[str, Any]) -> tuple[str, Decimal | None, int | None]:
    """조건부 주문 한 건 → (계약, 발동가, 수량)."""
    init, trig = _dict(order.get("initial")), _dict(order.get("trigger"))
    contract = str(init.get("contract") or order.get("contract") or "")
    return contract, dec(trig.get("price")), _int(init.get("size", ""))


def _problems(
    trade: Trade | None, size: int, stop: tuple[Decimal | None, int | None] | None
) -> list[str]:
    out: list[str] = []
    if trade is None:
        out.append("원장에 열린 매매가 없다(주인 없는 포지션 · 수동 주문이었나)")
    elif trade.is_long != (size > 0):
        out.append("원장 방향과 거래소 방향이 반대다")
    if stop is None:
        out.append("거래소에 걸린 손절이 없다")
        return out
    trig, ssize = stop
    if ssize not in (None, 0, -size):
        out.append(f"손절 수량 {ssize} 이 포지션 {size} 을 다 못 닫는다")
    plan = trade.planned_stop if trade is not None else None
    if plan and trig is not None and abs(trig - plan) / plan > STOP_TRIGGER_TOL:
        out.append(f"걸린 손절 {trig} ≠ 원장 계획 손절 {plan}")
    return out


def position_checks(snap: Snapshot) -> tuple[list[PositionCheck], list[str]]:
    """거래소 포지션마다 원장 매매 · 걸린 손절을 붙이고 어긋남을 적는다.

    Returns:
        (포지션 점검들, 거래소에 없는 원장 열린 매매 설명들).
    """
    ex = snap.exchange
    positions = [p for p in _rows_of(ex.get("positions")) if (_int(p.get("size", 0)) or 0) != 0]
    stops: dict[str, tuple[Decimal | None, int | None]] = {}
    for o in _rows_of(ex.get("price_orders")):
        contract, trig, ssize = _stop_of(o)
        stops.setdefault(contract, (trig, ssize))
    by_sym: dict[str, Trade] = {}
    for t in open_trades(snap):
        by_sym.setdefault(t.symbol, t)
    out: list[PositionCheck] = []
    for p in positions:
        sym = str(p.get("contract", ""))
        size = _int(p.get("size", 0)) or 0
        trade = by_sym.get(sym)
        stop = stops.get(sym)
        out.append(
            PositionCheck(
                symbol=sym,
                size=size,
                entry=dec(p.get("entry_price")),
                mark=dec(p.get("mark_price")),
                upnl=dec(p.get("unrealised_pnl")),
                margin=dec(p.get("margin")),
                leverage=str(p.get("leverage", "")),
                liq=dec(p.get("liq_price")),
                trade=trade,
                stop_trigger=stop[0] if stop else None,
                stop_size=stop[1] if stop else None,
                problems=tuple(_problems(trade, size, stop)),
            )
        )
    held = {c.symbol for c in out}
    ghosts = [
        f"{t.symbol} {t.leg}({kst(t.opened_at)} 진입) — 거래소엔 포지션이 없다"
        for t in by_sym.values()
        if t.symbol not in held
    ]
    return out, ghosts


# ── 1. 지금 라이브 ────────────────────────────────────────────────────────


def active_audits(
    events: Sequence[Mapping[str, Any]], since: datetime | None = None
) -> dict[tuple[str, str], datetime | None]:
    """아직 안 풀린 자가 점검 문제 — (판 · 종목, 코드)마다 마지막 사건이 found 인 것.

    Args:
        events: 사건 줄들.
        since: API 기동 시각. 러너는 기동하면 점검 상태를 새로 시작하고 옛 문제의 cleared 를
            남기지 않으므로, 그 전 found 는 버린다(안 버리면 풀린 문제가 영영 남아 보인다).
    """
    last: dict[tuple[str, str], tuple[str, datetime | None]] = {}
    for ev in events:
        kind = str(ev.get("event_type", ""))
        if kind not in ("live_audit_found", "live_audit_cleared"):
            continue
        pl = _dict(ev.get("payload"))
        key = (str(pl.get("run") or pl.get("symbol") or ""), str(pl.get("code", "?")))
        at = when(ev.get("ts"))
        if since is not None and (at is None or at < since):
            continue
        prev = last.get(key)
        if prev is None or (at is not None and (prev[1] is None or at >= prev[1])):
            last[key] = (kind, at)
    return {k: at for k, (kind, at) in last.items() if kind == "live_audit_found"}


def fund_symbols(fund: Mapping[str, Any]) -> set[str]:
    """펀드 다리들이 선언한 종목의 합집합."""
    out: set[str] = set()
    for leg in _rows_of(fund.get("legs")):
        out.update(str(s) for s in cast("list[object]", leg.get("symbols") or []))
    return out


def twr_drawdown(block: object) -> Decimal | None:
    """원장 블록(`twr` · `core_twr`)의 고점 대비 낙폭 %."""
    b = _dict(block)
    tw, pk = dec(b.get("twr")), dec(b.get("twr_peak"))
    if tw is None or not pk:
        return None
    return (1 - tw / pk) * 100


def _container_rows(health: Health) -> Row:
    bad: list[str] = []
    api_state = health.containers.get(health.api, ("", ""))[0]
    names = [(health.api or "api", api_state)] + [
        (n, health.containers.get(f"updown_live-{n}-1", ("", ""))[0]) for n in REQUIRED
    ]
    for name, st in names:
        if not st.startswith("Up") or "unhealthy" in st:
            bad.append(f"{name}: {st or '없음'}")
    ok_value = f"{health.api} {api_state} · 기동 {kst(health.api_started)}"
    return Row(
        "now",
        "컨테이너",
        "bad" if bad else "ok",
        " · ".join(bad) if bad else ok_value,
        "api · web · postgres · redis · proxy 가 Up(healthy)",
    )


def _error_counts(
    events: Sequence[Mapping[str, Any]], since: datetime
) -> tuple[Counter[str], Counter[str]]:
    errs: Counter[str] = Counter()
    warns: Counter[str] = Counter()
    for ev in events:
        at = when(ev.get("ts"))
        if at is None or at < since:
            continue
        lvl = str(ev.get("level", "")).lower()
        kind = str(ev.get("event_type", ""))
        if lvl == "error" and kind != "live_audit_found":
            errs[kind] += 1
        elif lvl == "warning" and kind not in ("live_step_slow", "preview_sweep_slow"):
            warns[kind] += 1
    return errs, warns


def _last_traces(events: Sequence[Mapping[str, Any]]) -> dict[str, datetime]:
    fills = ("live_entry_filled", "live_order_filled", "live_close_filled")
    out: dict[str, datetime] = {}
    for ev in events:
        kind = str(ev.get("event_type", ""))
        at = when(ev.get("ts"))
        if at is None:
            continue
        group = (
            "진입 문 사건"
            if kind.startswith("session_entry")
            else ("체결" if kind in fills else "")
        )
        if group and (group not in out or at > out[group]):
            out[group] = at
    return out


def _resource_row(health: Health) -> Row:
    res: list[str] = []
    st = "ok"
    if health.mem:
        res.append(f"가용 {health.mem[2]} MB")
        st = "warn" if health.mem[2] < MEM_AVAIL_WARN else st
    if health.swap:
        res.append(f"스왑 {health.swap[1]} MB")
        st = "warn" if health.swap[1] > SWAP_WARN else st
    if health.cpu:
        res.append(f"CPU 쉼 {health.cpu[1]}% · steal {health.cpu[2]}%")
        st = "warn" if health.cpu[2] > STEAL_WARN else st
    if health.disk_pct is not None:
        res.append(f"디스크 {health.disk_pct}%")
        st = "warn" if health.disk_pct > DISK_WARN else st
    basis = (
        f"가용<{MEM_AVAIL_WARN}MB · 스왑>{SWAP_WARN}MB · steal>{STEAL_WARN}% · 디스크>{DISK_WARN}%"
    )
    return Row("now", "서버 자원", st if res else "info", " · ".join(res) or "—", basis + " 면 ⚠️")


def now_rows(
    snap: Snapshot,
    health: Health,
    checks: Sequence[PositionCheck],
    ghosts: Sequence[str],
    now: datetime,
    latest_tag: str = "",
) -> list[Row]:
    """1절 — 지금 라이브가 도는가(컨테이너 · 리더 · 스위치 · 판 · 점검 · 대조 · 자원)."""
    s = "now"
    rows: list[Row] = []
    tag = health.env.get("IMAGE_TAG", "")
    rows.append(
        Row(
            s,
            "배포 버전",
            "info" if not latest_tag or latest_tag == tag else "warn",
            f"서버 {tag or '—'} · 저장소 마지막 태그 {latest_tag or '—'}",
            "다르면 배포 안 된 태그가 있거나 서버가 옛 판이다",
        )
    )
    lo = health.env.get("LIVE_ORDERS", "")
    rows.append(
        Row(
            s,
            "실주문 스위치",
            "ok" if lo == "1" else "warn",
            f"LIVE_ORDERS={lo or '—'}",
            "1 이어야 실주문",
        )
    )
    rows.append(_container_rows(health))
    lead_ok = health.leader_health is True and (health.leader_pttl or 0) > 0
    rows.append(
        Row(
            s,
            "거래 리더",
            "ok" if lead_ok else "bad",
            f"trading_leader={health.leader_health} · 락 남은 {health.leader_pttl} ms",
            "리더가 없으면 판이 조용히 멈춘다(화면은 healthy) — 09-29 엔 재기동이 답",
        )
    )
    halted = health.halt not in ("", "0")
    rows.append(
        Row(
            s,
            "전 판 정지 스위치",
            "warn" if halted else "ok",
            f"{health.halt or '없음'} · 바뀐 시각 {kst(health.halt_at)}",
            "1 이면 신규 진입 정지(손절 · 청산은 돈다)",
        )
    )
    want = fund_symbols(snap.fund)
    have = {r.symbol for r in snap.runs if r.closed_at is None}
    miss, extra = sorted(want - have), sorted(have - want)
    runs_value = f"열린 판 {len(have)} · 펀드 종목 {len(want)}"
    runs_value += f" · 판 없음 {miss[:8]}" if miss else ""
    runs_value += f" · 선언 밖 판 {extra[:8]}" if extra else ""
    runs_ok = bool(want) and not miss and not extra
    rows.append(
        Row(
            s,
            "열린 판 · 펀드 선언",
            "ok" if runs_ok else "warn",
            runs_value,
            "다리 선언 종목마다 판 하나",
        )
    )
    active = active_audits(snap.events, health.api_started)
    recent = [at for at in active.values() if at is not None and now - at <= AUDIT_RECENT]
    codes = Counter(code for (_, code) in active)
    audit_value = " · ".join(f"{c} {n}" for c, n in codes.most_common(6)) or "없음"
    audit_value += f" · 최근 2시간 {len(recent)}" if recent else ""
    rows.append(
        Row(
            s,
            "자가 점검(안 풀린 문제)",
            "bad" if recent else ("warn" if active else "ok"),
            audit_value,
            "러너가 스스로 찾은 문제(판정 멈춤 · 봉 굳음 · 손절 없음 · 어긋남) 중 안 풀린 것",
        )
    )
    errs, warns = _error_counts(snap.events, now - timedelta(days=1))
    err_text = " · ".join(f"{k} {v}" for k, v in errs.most_common(5)) or "0"
    warn_text = " · ".join(f"{k} {v}" for k, v in warns.most_common(5)) or "0"
    rows.append(
        Row(
            s,
            "오류 · 경고 사건(24시간)",
            "warn" if errs else "ok",
            f"오류 {err_text} / 경고 {warn_text}",
            "느린 걸음 · 예비 신호 느림은 뺀 수 — 무엇인지는 사건 이름으로 판단",
        )
    )
    traces = _last_traces(snap.events)
    rows.append(
        Row(
            s,
            "마지막 판정 흔적",
            "info",
            " · ".join(f"{k} {kst(v)}" for k, v in sorted(traces.items())) or "기간 안 사건 없음",
            "후보가 없으면 사건도 없다 — 멈춤 판단은 자가 점검(judgement_stalled)으로",
        )
    )
    probs = [f"{c.symbol}: {' / '.join(c.problems)}" for c in checks if c.problems]
    stops = sum(1 for c in checks if c.stop_trigger is not None)
    match_value = f"포지션 {len(checks)} · 손절 걸림 {stops}"
    match_value += " · " + " · ".join([*probs, *ghosts]) if probs or ghosts else ""
    rows.append(
        Row(
            s,
            "거래소 대조(포지션 · 손절)",
            "bad" if probs or ghosts else "ok",
            match_value,
            "포지션마다 원장 매매 · 다 닫는 손절 · 계획 손절과 같은 값",
        )
    )
    opens = _rows_of(snap.exchange.get("open_orders"))
    open_value = f"{len(opens)}" + (
        " · " + " · ".join(str(o.get("contract")) for o in opens[:6]) if opens else ""
    )
    rows.append(
        Row(s, "대기 주문", "info", open_value, "진입 지정가가 걸려 있으면 배포하지 않는다(런북)")
    )
    total = dec(_dict(snap.exchange.get("account")).get("total"))
    eq = dec(_dict(snap.fund.get("twr")).get("equity"))
    diff = None if total is None or eq is None else eq - total
    rows.append(
        Row(
            s,
            "펀드 장부 ↔ 거래소 지갑",
            "ok" if diff is not None and abs(diff) <= EQUITY_TOL else "warn",
            f"장부 {_f(eq)} · 지갑 {_f(total)} USDT · 차 {_f(diff, sign=True)}",
            f"앵커 = 지갑 총액 · 차 ≤ {EQUITY_TOL}(정시 틱 전엔 조금 다를 수 있다)",
        )
    )
    core_dd = twr_drawdown(snap.fund.get("core_twr"))
    fund_dd = twr_drawdown(snap.fund.get("twr"))
    at_s = str(_dict(snap.fund.get("drawdown_brake")).get("at", ""))
    at_v = dec(at_s)
    braking = core_dd is not None and at_v is not None and core_dd >= at_v * 100
    rows.append(
        Row(
            s,
            "낙폭 브레이크",
            "warn" if braking else "info",
            f"본 다리 원장 {_f(core_dd)}% · 펀드 원장 {_f(fund_dd)}% · 문턱 {at_s or '—'}",
            "브레이크는 본 다리 원장을 본다 — 넘으면 신규 크기 줄임",
        )
    )
    rows.append(_resource_row(health))
    return rows


def trend_supply(
    closes: Mapping[str, Sequence[Decimal]],
    lookback: int,
    up_ratio: Decimal,
    back: int = 0,
    min_symbols: int = 5,
) -> tuple[float | None, int]:
    """+100% 알트 몫 — 알트마다 닫힌 일봉 종가 `lookback` 일 수익 ≥ `up_ratio` 인 비율.

    Args:
        closes: 종목 → 닫힌 일봉 종가(시각 순 · 마지막이 가장 최근 닫힌 봉). BTC 는 호출부가 뺀다.
        lookback: 수익을 재는 일 수(90).
        up_ratio: 문턱(1.0 = +100%).
        back: 마지막 봉에서 이만큼 앞의 날로 잰다("30일 전" 값).
        min_symbols: 잴 수 있는 종목이 이보다 적으면 None.

    Returns:
        (몫, 잰 종목 수). 상장한 지 `lookback + back` 일이 안 된 종목은 뺀다.
    """
    hit = n = 0
    for cs in closes.values():
        if len(cs) < lookback + back + 1:
            continue
        end = cs[len(cs) - 1 - back]
        start = cs[len(cs) - 1 - back - lookback]
        if not start:
            continue
        n += 1
        hit += end / start - 1 >= up_ratio
    return (hit / n if n >= min_symbols else None), n


def supply_row(
    now_share: float | None,
    prev_share: float | None,
    n: int,
    reference: Mapping[str, float],
    lookback: int,
    back: int,
) -> Row:
    """1절 관찰 줄 — 지금 · `back` 일 전 몫과 해마다 참고 값(판정 아님 · 상태는 항상 참고)."""
    ref = " · ".join(f"{k} {v:.0%}" for k, v in reference.items())

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v:.1%}"

    return Row(
        "now",
        f"+100% 알트 몫({lookback}일)",
        "info",
        f"지금 {pct(now_share)} · {back}일 전 {pct(prev_share)} · 알트 {n}종",
        f"참고(해 평균) {ref} — 낮으면 일봉 채널 · MACD 롱이 쉬는 장(관찰 · 판정 아님)",
    )


def blocked_row(snap: Snapshot, health: Health, now: datetime, days: int) -> Row:
    """1절 한 줄 — 기간 안 막힌 후보 수 · 코드별 (T562 · `session_candidate_blocked`).

    Args:
        snap: 스냅샷 — `blocked` 가 None 이면 옛 덤프다.
        health: 서버 상태 — 기록에 러너 기동 시각이 없으면 API 기동 시각으로 견준다.
        now: 보고 시각(UTC).
        days: 기간(일).

    Returns:
        상태 · 값 · 기준. `awaiting_fund` 가 러너 기동 뒤 `AWAITING_FUND_GRACE` 밖에서 나왔으면
        ⚠️(펀드가 안 붙은 판), 기동 시각을 모르면 밖으로 센다(확인 쪽으로). 나머지 코드(문 ·
        캔들 · 몫 반대 …)는 정해진 규칙이 막은 것이라 수만 싣는다.

    Note:
        깔때기(3-5)는 개수, 이 줄은 **기록**에서 센다 — 기록이 있으면 어느 종목 · 봉 · 다리 ·
        계획값이었는지 `event_logs` 에서 바로 찾을 수 있다.
    """
    item = f"막힌 후보({days}일)"
    grace = f"{int(AWAITING_FUND_GRACE.total_seconds() // 3600)}H"
    basis = (
        f"awaiting_fund 는 판 기동(배포 · 재기동) 뒤 {grace} 안에서만 정상 — 그 밖이면 ⚠️ · "
        "나머지 코드는 규칙이 막은 수(참고)"
    )
    # 🔴 T605(2026-10-27) — 막힘 **기록**(blocked.tsv)이 비어도 펀드가 **죽은 판 키**를 묶고 있으면
    #    그 종목의 지금 판은 모든 봉에서 `awaiting_fund` 로 막힌다(후보가 나기 전에 막혀 기록을 안
    #    남긴다). 10-16 전환이 반쪽으로 끝나 핵심 8 종이 그 상태였는데 이 줄이 0 건 ✅ 로 가렸다.
    #    깔때기 수는 누적(재기동을 넘어 쌓임)이라 시각 판정에 못 쓰고, 펀드 `runs` ↔ 열린 판 키
    #    대조가
    #    사실이다.
    unbound = unbound_members(snap)
    tail = (
        f" · 🔴 펀드가 묶은 판이 열린 판이 아님 {len(unbound)}종({', '.join(unbound[:8])})"
        if unbound
        else ""
    )
    if snap.blocked is None:
        return Row(
            "now",
            item,
            "warn" if unbound else "info",
            "자료 없음(옛 덤프 · 기록 전 서버)" + tail,
            basis,
        )
    since = now - timedelta(days=days)
    rows = [r for r in snap.blocked if (when(r.get("ts")) or when(r.get("at")) or since) >= since]
    codes: Counter[str] = Counter(str(r.get("code") or "?") for r in rows)
    late: list[str] = []
    for r in rows:
        if r.get("code") != "awaiting_fund":
            continue
        boot = when(r.get("runner_started")) or health.api_started
        noted = when(r.get("ts")) or when(r.get("at"))
        if boot is None or noted is None or noted - boot > AWAITING_FUND_GRACE:
            late.append(f"{r.get('symbol') or '?'} {kst(noted)}")
    value = f"{len(rows)}건"
    if codes:
        value += " · " + " · ".join(f"{c} {n}" for c, n in codes.most_common(6))
    if late:
        value += f" · awaiting_fund 기동 {grace} 밖 {len(late)}건({', '.join(late[:4])})"
    value += tail
    return Row("now", item, "warn" if late or unbound else "ok", value, basis)


def unbound_members(snap: Snapshot) -> list[str]:
    """펀드 `runs`(종목 → 묶은 판 키)가 가리키는 판이 **열린 판이 아닌** 멤버 종목 (T605).

    Args:
        snap: 스냅샷(`fund["runs"]` · `runs`).

    Returns:
        `종목(묶은 키 → 열린 키)` 목록 — 종목 순. 그 종목에 열린 판이 없으면 "열린 판 · 펀드 선언"
        줄이
        따로 말하므로 여기서는 뺀다. 펀드 파일에 `runs` 가 없으면 빈 목록.
    """
    bound = snap.fund.get("runs") if isinstance(snap.fund, dict) else None
    if not isinstance(bound, dict):
        return []
    open_key = {r.symbol: r.key for r in snap.runs if r.closed_at is None}
    out: list[str] = []
    for symbol, key in sorted(cast("dict[str, object]", bound).items()):
        have = open_key.get(str(symbol))
        if have is None or have == str(key):
            continue
        out.append(f"{symbol}({str(key)[-6:]}→{have[-6:]})")
    return out


# ── 2. 최근 7일 매매 ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradeCheck:
    """매매 한 건과 그 점검."""

    money: TradeMoney
    fill_bp: float | None
    stop_slip: Decimal | None
    flags: tuple[str, ...]


def _trade_flags(
    t: Trade, m: TradeMoney, fill_bp: float | None
) -> tuple[list[str], Decimal | None]:
    flags: list[str] = []
    if fill_bp is not None and abs(fill_bp) > FILL_BP_WARN:
        flags.append(f"진입 체결가가 원장보다 {fill_bp:+.0f}bp")
    slip = None
    if t.closed and t.planned_stop and t.exit_price is not None and "손절" in t.outcome:
        raw = (t.exit_price / t.planned_stop - 1) * 100
        slip = -raw if t.is_long else raw
        if slip > SLIP_STOP_WARN:
            flags.append(f"손절 체결이 계획보다 {float(slip):.2f}% 불리")
    if t.leverage and t.filled_leverage and abs(t.filled_leverage / t.leverage - 1) > LEV_WARN:
        flags.append(f"체결 배율 {_f(t.filled_leverage)} · 계획 {_f(t.leverage)}")
    if t.closed and t.fee_actual is None:
        flags.append("거래소 수수료 기록 없음(원장 모형값)")
    if t.closed and m.source != "exchange":
        flags.append("거래소 닫힌 포지션 기록과 못 맞춤(원장 추정)")
    if t.actor not in ("시스템", "system", ""):
        flags.append(f"행위자 {t.actor}")
    return flags, slip


def recent_trades(snap: Snapshot, since: datetime) -> list[TradeCheck]:
    """최근 매매(진입이 기간 안이거나 아직 열림)마다 돈 · 체결 · 손절 · 배율 · 수수료를 본다."""
    closes = _rows_of(snap.exchange.get("position_close"))
    finished = _rows_of(snap.exchange.get("finished_orders"))
    out: list[TradeCheck] = []
    for t in snap.trades:
        if t.opened_at is None or (t.opened_at < since and t.closed_at is not None):
            continue
        m = money_of(t, closes)
        fb = fill_basis(t, finished)
        fill_bp = float((fb[0] / t.entry - 1) * 10000) if fb is not None and t.entry else None
        flags, slip = _trade_flags(t, m, fill_bp)
        out.append(TradeCheck(m, fill_bp, slip, tuple(flags)))
    return sorted(out, key=lambda c: c.money.trade.opened_at or since)


# ── 3. 신호 · 이유 ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Bar1:
    """보유 이야기에 쓰는 봉 — 시작 시각 · 고 · 저 · 종."""

    at: datetime
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class Story:
    """보유 매매 하나의 지금 — 손익 · 경로 · 시장 · 추세 재료."""

    check: PositionCheck
    held_h: float | None
    sym_pct: Decimal | None
    """종목 가격 변화(방향 무관 · 진입가 → 지금가 %)."""
    ret_pct: Decimal | None
    """포지션 방향으로 본 가격 손익 %."""
    mfe_pct: Decimal | None
    mae_pct: Decimal | None
    r_now: Decimal | None
    to_stop_pct: Decimal | None
    btc_pct: Decimal | None
    upnl_of_margin: Decimal | None
    sma_notes: tuple[str, ...]
    macd_note: str


def _ema(xs: Sequence[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out: list[float] = []
    for x in xs:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def macd_hist(closes: Sequence[Decimal]) -> tuple[float, float] | None:
    """MACD(12 · 26 · 9) 선 - 신호선 — (지금, 한 봉 전). 35봉 미만이면 None(참고 · 자체 구현)."""
    if len(closes) < 35:
        return None
    xs = [float(c) for c in closes]
    line = [a - b for a, b in zip(_ema(xs, 12), _ema(xs, 26), strict=True)]
    sig = _ema(line, 9)
    return line[-1] - sig[-1], line[-2] - sig[-2]


def _path(
    check: PositionCheck, entry: Decimal | None, bars: Sequence[Bar1]
) -> tuple[Decimal | None, Decimal | None]:
    if not entry or not bars:
        return None, None
    up = (max(b.high for b in bars) / entry - 1) * 100
    dn = (min(b.low for b in bars) / entry - 1) * 100
    return (up, dn) if check.size > 0 else (-dn, -up)


def _sma_notes(closes_by_tf: Mapping[str, Sequence[Decimal]]) -> tuple[str, ...]:
    notes: list[str] = []
    for tf in ("1h", "4h", "1d"):
        cs = list(closes_by_tf.get(tf, []))
        if len(cs) >= 20:
            sma = sum(cs[-20:], Decimal(0)) / 20
            side = "위" if cs[-1] >= sma else "아래"
            notes.append(f"{tf} 종가 {_f(cs[-1], 4)} {side} SMA20 {_f(sma, 4)}")
    return tuple(notes)


def story_of(
    check: PositionCheck,
    bars_1h: Sequence[Bar1],
    btc_1h: Sequence[Bar1],
    closes_by_tf: Mapping[str, Sequence[Decimal]],
    now: datetime,
) -> Story:
    """보유 포지션 하나의 이야기 — 진입 뒤 1H 경로 · BTC 같은 기간 · 이평 · MACD(참고).

    Args:
        check: 거래소 대조 한 줄(원장 매매가 붙어 있으면 손절 · 진입 시각을 쓴다).
        bars_1h: 진입 뒤 그 종목 1H 봉(닫힌 봉 · 시각 순).
        btc_1h: 같은 기간 BTC 1H 봉.
        closes_by_tf: `{"1h": […], "4h": […], "1d": […]}` 닫힌 종가(이평 · MACD 참고용).
        now: 지금(UTC).
    """
    t = check.trade
    entry = check.entry or (t.entry if t else None)
    long_ = check.size > 0
    held = (now - t.opened_at).total_seconds() / 3600 if t and t.opened_at else None
    mv = ret = None
    if entry and check.mark:
        mv = (check.mark / entry - 1) * 100
        ret = mv if long_ else -mv
    mfe, mae = _path(check, entry, bars_1h)
    r_now = to_stop = None
    if t and t.planned_stop and entry and check.mark:
        d = abs(entry - t.planned_stop)
        if d:
            r_now = ((check.mark - entry) if long_ else (entry - check.mark)) / d
        to_stop = abs(check.mark / t.planned_stop - 1) * 100
    btc = (btc_1h[-1].close / btc_1h[0].close - 1) * 100 if len(btc_1h) >= 2 else None
    mh = macd_hist(list(closes_by_tf.get("4h", [])))
    macd = "" if mh is None else f"4H MACD 선-신호선 {mh[0]:+.5f}(한 봉 전 {mh[1]:+.5f})"
    upm = None if check.upnl is None or not check.margin else check.upnl / check.margin * 100
    return Story(
        check, held, mv, ret, mfe, mae, r_now, to_stop, btc, upm, _sma_notes(closes_by_tf), macd
    )


@dataclass(frozen=True, slots=True)
class HeldLine:
    """막힌 후보 한 건(사건) — 시각 · 종목 · 문 · 값."""

    at: datetime | None
    symbol: str
    gate: str
    owners: str
    detail: str


_TEXT_KEYS = ("run", "market", "symbol", "note", "why", "by", "reason")


def held_lines(
    events: Sequence[Mapping[str, Any]], since: datetime, owners: Mapping[str, Sequence[str]]
) -> list[HeldLine]:
    """기간 안 `session_entry_*` 사건을 한 줄씩 — 문 · 그 문을 선언한 다리 · 값 대 문턱."""
    out: list[HeldLine] = []
    for ev in events:
        kind = str(ev.get("event_type", ""))
        at = when(ev.get("ts"))
        if not kind.startswith("session_entry") or at is None or at < since:
            continue
        pl = _dict(ev.get("payload"))
        gate = gate_of(kind)
        bits = [f"{k}={pl[k]}" for k in pl if k not in _TEXT_KEYS][:4]
        why = str(pl.get("note") or pl.get("why") or pl.get("by") or pl.get("reason") or "")
        detail = " · ".join(bits) + (f" — {why[:70]}" if why else "")
        who = ", ".join(owners.get(gate, [])) or "—"
        out.append(HeldLine(at, str(pl.get("symbol") or ""), gate, who, detail.strip(" ·")))
    return sorted(out, key=lambda h: h.at or since)


def previews_in(
    events: Sequence[Mapping[str, Any]], since: datetime, run_symbol: Mapping[str, str]
) -> list[tuple[datetime | None, str, dict[str, str]]]:
    """기간 안 예비 신호(`preview_found.previews`) — (시각, 종목, 칸). 화면이 열렸을 때만 남는다."""
    out: list[tuple[datetime | None, str, dict[str, str]]] = []
    for ev in events:
        at = when(ev.get("ts"))
        if str(ev.get("event_type", "")) != "preview_found" or at is None or at < since:
            continue
        for p in _rows_of(_dict(ev.get("payload")).get("previews")):
            run = str(p.get("run", ""))
            out.append((at, run_symbol.get(run, run), {k: str(v) for k, v in p.items()}))
    return out


# ── 그리기 ────────────────────────────────────────────────────────────────


def _table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    return [
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
        *("| " + " | ".join(r) + " |" for r in rows),
    ]


def _first_lines(rows: Sequence[Row], trades: Sequence[TradeCheck]) -> list[str]:
    flagged = sorted(
        (r for r in rows if r.status in ("bad", "warn")), key=lambda r: r.status != "bad"
    )
    t_flag = [c for c in trades if c.flags]
    if not flagged and not t_flag:
        return ["- 없음 — 1절 항목이 전부 기준 안이고 최근 매매 점검 표시도 없다."]
    out = [f"- {MARK[r.status]} **{r.item}** — {r.value} _(기준: {r.basis})_" for r in flagged]
    for c in t_flag:
        t = c.money.trade
        out.append(f"- ⚠️ 매매 {t.symbol} {t.leg} {kst(t.opened_at)} — {' · '.join(c.flags)}")
    return out


def _position_table(checks: Sequence[PositionCheck]) -> list[str]:
    if not checks:
        return ["- 포지션 없음."]
    head = (
        "종목",
        "수량",
        "진입가",
        "지금가",
        "평가손익",
        "증거금",
        "배율",
        "청산가",
        "걸린 손절",
        "계획 손절",
        "문제",
    )
    return _table(
        head,
        [
            (
                c.symbol,
                str(c.size),
                _f(c.entry, 6),
                _f(c.mark, 6),
                _f(c.upnl, sign=True),
                _f(c.margin),
                c.leverage,
                _f(c.liq, 6),
                _f(c.stop_trigger, 6),
                _f(c.trade.planned_stop if c.trade else None, 6),
                " / ".join(c.problems) or "없음",
            )
            for c in checks
        ],
    )


def _trade_section(snap: Snapshot, trades: Sequence[TradeCheck], since: datetime) -> list[str]:
    closed = [c for c in trades if c.money.trade.closed]
    wins = sum(1 for c in closed if (c.money.pnl or 0) > 0)
    pnl = sum((c.money.pnl or Decimal(0) for c in closed), Decimal(0))
    eq = dec(_dict(snap.fund.get("twr")).get("equity"))
    entered = sum(1 for c in trades if (c.money.trade.opened_at or since) >= since)
    opened = sum(1 for c in trades if not c.money.trade.closed)
    line = f"- 진입 {entered} · 닫힘 {len(closed)} · 이김 {wins} · 짐 {len(closed) - wins}"
    line += f" · 실현 합 {_f(pnl, sign=True)} USDT"
    line += f"({_f(pnl / eq * 100, sign=True)}% · 지금 장부 {_f(eq)})" if eq else ""
    line += f" · 열림 {opened}"
    out = [line, ""]
    if not trades:
        return [*out, "- 기간 안 매매 없음."]
    head = (
        "진입",
        "종목",
        "다리",
        "방향",
        "결과",
        "진입가",
        "청산가",
        "계획 손절",
        "손익(출처)",
        "R",
        "점검",
    )
    out += _table(
        head,
        [
            (
                kst(c.money.trade.opened_at),
                c.money.trade.symbol,
                c.money.trade.leg,
                c.money.trade.direction,
                c.money.trade.outcome or "열림",
                _f(c.money.trade.entry, 6),
                _f(c.money.trade.exit_price, 6),
                _f(c.money.trade.planned_stop, 6),
                f"{_f(c.money.pnl, sign=True)}({c.money.source})",
                _f(c.money.r, sign=True),
                " · ".join(c.flags) or "—",
            )
            for c in trades
        ],
    )
    by_leg: dict[str, list[TradeCheck]] = defaultdict(list)
    for c in closed:
        by_leg[c.money.trade.leg].append(c)
    if not by_leg:
        return out
    leg_rows: list[tuple[str, ...]] = []
    for leg, cs in sorted(by_leg.items()):
        rs = [c.money.r for c in cs if c.money.r is not None]
        total = sum((c.money.pnl or Decimal(0) for c in cs), Decimal(0))
        mean_r = sum(rs, Decimal(0)) / len(rs) if rs else None
        won = sum(1 for c in cs if (c.money.pnl or 0) > 0)
        leg_rows.append((leg, str(len(cs)), str(won), _f(total, sign=True), _f(mean_r, sign=True)))
    return [
        *out,
        "",
        "다리별(닫힌 것):",
        "",
        *_table(("다리", "건수", "이김", "합 USDT", "평균 R"), leg_rows),
    ]


def _story_lines(st: Story) -> list[str]:
    c, t = st.check, st.check.trade
    leg = t.leg if t else "(원장 없음)"
    side = "롱" if c.size > 0 else "숏"
    opened = kst(t.opened_at if t else None)
    rel = (
        f" · BTC 대비 {_f(st.sym_pct - st.btc_pct, sign=True)}%p"
        if st.sym_pct is not None and st.btc_pct is not None
        else ""
    )
    trend = " · ".join(st.sma_notes) or "—"
    trend += f" · {st.macd_note}" if st.macd_note else ""
    target = _f(t.planned_target if t else None, 6)
    upnl, upm = _f(c.upnl, sign=True), _f(st.upnl_of_margin, sign=True)
    ret, r_now = _f(st.ret_pct, sign=True), _f(st.r_now, sign=True)
    mfe, mae = _f(st.mfe_pct, sign=True), _f(st.mae_pct, sign=True)
    btc, sym = _f(st.btc_pct, sign=True), _f(st.sym_pct, sign=True)
    return [
        f"**{c.symbol} {leg} {side}** — 진입 {opened}({_f(st.held_h, 1)}시간 보유)",
        f"- 손익: 평가 {upnl} USDT = 증거금 대비 {upm}% · 가격 {ret}% · 지금 R {r_now}",
        f"- 경로(진입 뒤 1H): 최대 유리 {mfe}% · 최대 불리 {mae}%"
        + f" · 손절까지 {_f(st.to_stop_pct)}% · 목표 {target}",
        f"- 시장: 같은 기간 BTC {btc}% · 종목 {sym}%{rel}",
        f"- 추세 재료: {trend}",
        "",
    ]


def _signal_section(
    snap: Snapshot,
    trades: Sequence[TradeCheck],
    held: Sequence[HeldLine],
    previews: Sequence[tuple[datetime | None, str, dict[str, str]]],
    since: datetime,
) -> list[str]:
    out = ["### 3-2 들어간 것(기간 안)", ""]
    for c in trades:
        t = c.money.trade
        if (t.opened_at or since) >= since:
            pnl = _f(c.money.pnl, sign=True)
            what = f"{t.symbol} {t.leg} {t.direction}"
            out.append(f"- {kst(t.opened_at)} {what} — {t.outcome or '열림'} {pnl} USDT")
    if len(out) == 2:
        out.append("- 없음.")
    out += ["", "### 3-3 예비 신호(마감 전 · 펀드 화면이 열려 있을 때만 기록)", ""]
    for at, sym, p in previews[-20:]:
        what = f"{p.get('leg', '')} {p.get('side', '')} {p.get('kind', '')}"
        out.append(f"- {kst(at)} {sym} {what} 진입 {p.get('entry', '')} · 손절 {p.get('stop', '')}")
    if not previews:
        out.append("- 기록 없음(화면을 안 열면 계산도 안 한다 — 없다고 신호가 없었던 것은 아니다).")
    out += ["", "### 3-4 후보가 문에 막힌 것(사건 · 기간 안)", ""]
    if not held:
        out.append("- 없음(후보가 안 났거나 다 통과했다).")
    else:
        by_gate: dict[tuple[str, str], list[HeldLine]] = defaultdict(list)
        for h in held:
            by_gate[(h.gate, h.owners)].append(h)
        gate_rows = [
            (
                g,
                o,
                str(len(hs)),
                str(len({h.symbol for h in hs})),
                kst(max((h.at for h in hs if h.at), default=None)),
                hs[-1].detail[:120],
            )
            for (g, o), hs in sorted(by_gate.items(), key=lambda kv: -len(kv[1]))
        ]
        out += _table(("문", "선언한 다리", "건수", "종목 수", "마지막", "보기"), gate_rows)
        out += ["", "최근 10건:", ""]
        out += [f"- {kst(h.at)} {h.symbol} {h.gate} — {h.detail[:140]}" for h in held[-10:]]
    out += ["", "### 3-5 다리별 깔때기(판이 열린 뒤 누적 · 1.40.0 부터 저장)", ""]
    why = why_no_entry(snap.runs)
    if not why:
        out.append("- 저장된 깔때기 없음.")
    for pid, (legs, shared) in why.items():
        rows = [
            (
                lf.leg,
                str(lf.candidates),
                str(lf.entered),
                str(lf.blocked),
                " · ".join(
                    f"{k} {v}" for k, v in sorted(lf.blocked_by.items(), key=lambda kv: -kv[1])[:4]
                )
                or "—",
            )
            for lf in legs
        ]
        out += [
            f"묶음 `{pid}`",
            "",
            *_table(("다리", "후보", "진입", "막힘", "막힌 이유(키)"), rows),
        ]
        if shared:
            out += [
                "",
                "다리 이름 없는 문 · 조건: "
                + " · ".join(f"{k} {v}" for k, v in list(shared.items())[:10]),
            ]
        out.append("")
    declared = [
        str(leg.get("attribution") or leg.get("playbook") or "")
        for leg in _rows_of(snap.fund.get("legs"))
    ]
    if declared:
        last: dict[str, datetime] = {}
        for t in snap.trades:
            if t.opened_at is not None:
                last[t.leg] = max(last.get(t.leg, t.opened_at), t.opened_at)
        out += ["### 3-6 다리별 마지막 진입(스냅샷 기간 안)", ""]
        out += [f"- {n.split('@')[0]}: {kst(last.get(n.split('@')[0]))}" for n in declared]
        out.append("")
    return out


def render_check(
    snap: Snapshot,
    rows: Sequence[Row],
    checks: Sequence[PositionCheck],
    trades: Sequence[TradeCheck],
    stories: Sequence[Story],
    held: Sequence[HeldLine],
    previews: Sequence[tuple[datetime | None, str, dict[str, str]]],
    days: int,
    now: datetime,
) -> str:
    """판단 재료 보고서(마크다운). 0 먼저 볼 것 → 1 지금 → 2 최근 매매 → 3 신호 · 이유."""
    since = now - timedelta(days=days)
    legend = "✅ 기준 안 · ⚠️ 확인 필요 · ⛔ 이상 · · 참고"
    out: list[str] = [
        f"# 라이브 점검 — {kst(now)}",
        "",
        f"> 판단 재료다(판정은 읽는 쪽이). {legend}. 스냅샷 {kst(snap.taken_at)} · 서버는 읽기만 · "
        f"기간 최근 {days}일.",
        "",
        "## 0. 먼저 볼 것",
        "",
        *_first_lines(rows, trades),
        "",
        "## 1. 지금 라이브",
        "",
        *_table(
            ("항목", "상태", "값", "기준"),
            [(r.item, MARK[r.status], r.value, r.basis) for r in rows],
        ),
        "",
        "### 1-1 거래소 포지션 ↔ 원장 ↔ 손절",
        "",
        *_position_table(checks),
        "",
        f"## 2. 최근 {days}일 매매({kst(since)} ~)",
        "",
        *_trade_section(snap, trades, since),
        "",
        "## 3. 신호 · 진입 · 손익 · 이유",
        "",
        "### 3-1 지금 보유 — 손익과 왜 그런가(재료)",
        "",
    ]
    if not stories:
        out.append("- 보유 없음.")
    for st in stories:
        out += _story_lines(st)
    out += _signal_section(snap, trades, held, previews, since)
    return "\n".join(out)


def check_json(
    rows: Sequence[Row],
    trades: Sequence[TradeCheck],
    checks: Sequence[PositionCheck],
    now: datetime,
) -> dict[str, Any]:
    """기계가 읽을 요약 — 상태별 수 · 항목 · 매매 점검 표시 · 포지션 문제."""
    return {
        "counts": dict(Counter(r.status for r in rows)),
        "rows": [
            {
                "section": r.section,
                "item": r.item,
                "status": r.status,
                "value": r.value,
                "basis": r.basis,
            }
            for r in rows
        ],
        "trade_flags": [
            {
                "symbol": c.money.trade.symbol,
                "leg": c.money.trade.leg,
                "opened_at": c.money.trade.opened_at.isoformat()
                if c.money.trade.opened_at
                else None,
                "flags": list(c.flags),
            }
            for c in trades
            if c.flags
        ],
        "positions": [
            {"symbol": c.symbol, "size": c.size, "problems": list(c.problems)} for c in checks
        ],
        "made_at": now.astimezone(UTC).isoformat(),
    }
