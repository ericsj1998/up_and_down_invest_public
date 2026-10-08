"""매매법이 설계대로 돌았나 — 깔때기(문) · 사건 로그 · 매매법 버전 구간 (T444 · 순수)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from updown.orchestration.live_review.snapshot import Run, Trade, when

NOISE_EVENTS = frozenset(
    {
        "analysis_frame",
        "live_step_slow",
        "notify_sent",
        "equity_snapshot_recorded",
        "market_data_adapter_created",
    }
)


def funnel_totals(runs: list[Run]) -> dict[str, dict[str, int]]:
    """판별 깔때기(`meta_json["funnel"]`)를 **매매법 묶음(playbook_id)별**로 합친다.

    Returns:
        `{playbook_id: {깔때기 키: 합}}` — 키는 `gate:fit:boost` · `blocked:…` 같은 러너 어휘.
    """
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for run in runs:
        funnel = run.meta.get("funnel")
        if not isinstance(funnel, dict):
            continue
        for key, val in funnel.items():  # pyright: ignore[reportUnknownVariableType]
            try:
                out[run.playbook_id][str(key)] += int(val)  # pyright: ignore[reportUnknownArgumentType]
            except (TypeError, ValueError):
                continue
    return {k: dict(v.most_common()) for k, v in out.items()}


@dataclass(frozen=True, slots=True)
class EventSummary:
    """사건 로그 요약."""

    by_type: dict[str, int]
    audit_codes: dict[str, int]
    """`live_audit_found` 의 code 별 수(종목 무관)."""
    reconcile_codes: dict[str, int]
    errors: dict[str, int]
    """level error · warning 인 사건 이름별 수."""
    entry_gates: dict[str, int]
    """`session_entry_gate_*` 의 why/by/reason 별 수 — 진입이 왜 줄거나 막혔나."""
    first_at: datetime | None
    last_at: datetime | None


def summarize_events(events: list[dict[str, Any]]) -> EventSummary:
    """구조화 사건 줄을 센다 — 잡음 사건은 뺀다."""
    by_type: Counter[str] = Counter()
    audit: Counter[str] = Counter()
    recon: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    gates: Counter[str] = Counter()
    first: datetime | None = None
    last: datetime | None = None
    for ev in events:
        kind = str(ev.get("event_type", ""))
        if not kind or kind in NOISE_EVENTS or kind.startswith("HTTP"):
            continue
        by_type[kind] += 1
        raw = ev.get("payload")
        payload: dict[str, Any] = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        at = when(ev.get("ts"))
        if at is not None:
            first = at if first is None or at < first else first
            last = at if last is None or at > last else last
        if kind == "live_audit_found":
            audit[str(payload.get("code", "?"))] += 1
        if kind == "reconcile_finding":
            recon[str(payload.get("code", "?"))] += 1
        if str(ev.get("level", "")).lower() in ("error", "warning"):
            errors[kind] += 1
        if kind.startswith("session_entry_gate"):
            why = payload.get("why") or payload.get("by") or payload.get("reason") or "?"
            gates[f"{kind}:{why}"] += 1
    return EventSummary(
        by_type=dict(by_type.most_common()),
        audit_codes=dict(audit.most_common()),
        reconcile_codes=dict(recon.most_common()),
        errors=dict(errors.most_common()),
        entry_gates=dict(gates.most_common()),
        first_at=first,
        last_at=last,
    )


@dataclass(frozen=True, slots=True)
class PlaybookSpan:
    """매매법(묶음 · 다리 귀속 키)이 라이브에서 돈 구간 — 라이브 매매법은 바뀐다."""

    playbook_id: str
    leg: str
    first: datetime
    last: datetime
    trades: int


def playbook_spans(trades: list[Trade]) -> list[PlaybookSpan]:
    """매매가 남긴 (묶음, 다리) 구간 — 첫 진입 ~ 마지막 진입 · 건수. 시각 순."""
    acc: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for t in trades:
        if t.opened_at is not None:
            acc[(t.run_playbook_id, t.playbook)].append(t.opened_at)
    out = [PlaybookSpan(pid, leg, min(ts), max(ts), len(ts)) for (pid, leg), ts in acc.items()]
    return sorted(out, key=lambda s: (s.first, s.leg))


def entry_latency_s(trade: Trade) -> float | None:
    """신호 봉 마감(정시) 뒤 진입까지 초 — `opened_at` 이 정시에서 얼마나 뒤인가.

    5분봉 낡음 원장(T372)은 0 근처로 보인다.
    """
    if trade.opened_at is None:
        return None
    return float(trade.opened_at.minute * 60 + trade.opened_at.second) % 3600


FUNNEL_GLOSSARY: dict[str, str] = {
    "cand:": "탐지기가 후보를 냈다(다리별)",
    "entered:": "진입했다(다리별 · flip_ = 반대 포지션 뒤집기)",
    "blocked:": "막힘 — 다리 이름이면 세션 규칙(반대 몫 · 미대조 · 펀드 대기 · 장대봉)",
    "gate:": "펀드 문이 막음(자리 · 상한 · 브레이크 · 폭 · 기울기 · 띠 · 신선도) · fit = 줄여 진입",
    "ref_sma": "기준(BTC) 4H 이평이 내려가는 중이 아니다(T304 · 지금 매매법엔 선언 없음)",
    "ref_band": "기준(BTC) 수익률이 띠 밖(T290 · 삼각 숏 다리의 문)",
    "ref_volpct": "BTC 4H 변동성 백분위 < 0.816(T329 · 급락 되돌림의 문 · 급락장 아니면 안 든다)",
    "ref_surge": "기준(BTC) 급등 문",
    "stop_too_tight": "손절 거리가 너무 좁아 버림",
    "stop_liq_cap": "손절이 청산선 밖이라 버림",
    "wick_stop": "꼬리 손절 규칙",
    "vol:up": "거래량 조건 통과",
    "vol_unknown": "거래량을 못 읽음",
    "sma_tilt:": "이평 띠 기울기(in 통과 · out 안 맞음 · unknown 모름)",
    "bar_tilt:": "닫힌 상위 봉 기울기(in · out · stale · unknown)",
    "size_windows:": "G2 월봉 창(in · stale)",
    "new_high:": "신고가 조건",
    "depth:": "호가 깊이(down 얕음 · unknown 모름)",
    "funding:settlements": "펀딩 정산 횟수",
    "bars:": "국면별 봉 수",
    "early_": "조기 진입(T345)",
    "add": "불타기(T308)",
    "quiet_add:": "조용한 불타기",
}


@dataclass(frozen=True, slots=True)
class LegFunnel:
    """다리 하나의 깔때기 — 후보 → 막힘 → 진입."""

    leg: str
    candidates: int
    entered: int
    blocked: int
    blocked_by: dict[str, int]


def why_no_entry(runs: list[Run]) -> dict[str, tuple[list[LegFunnel], dict[str, int]]]:
    """왜 안 들어갔나 — 묶음별 다리 깔때기(후보 · 진입 · 막힘)와 다리 이름 없는 문 · 조건 수.

    Args:
        runs: 실계좌 판들(깔때기는 판이 열린 뒤 누적이다 — "지금 이 봉" 이 아니라 "그동안").

    Returns:
        `{playbook_id: ([LegFunnel…], {다리 이름 없는 키: 합})}`. 후보 0 인 다리는 **탐지기가
        한 번도 안 울린 것**이고,
        후보는 있는데 진입 0 이면 문 · 조건이 막은 것이다 — 둘을 가르는 것이 이 표의 목적.
    """
    totals = funnel_totals(runs)
    out: dict[str, tuple[list[LegFunnel], dict[str, int]]] = {}
    for pid, keys in totals.items():
        legs: set[str] = set()
        for key in keys:
            for head in ("cand:", "entered:", "blocked:"):
                if key.startswith(head):
                    name = key[len(head) :]
                    if name.startswith("flip_"):
                        name = name[5:]
                    if "_" in name and not name.startswith(
                        ("marubozu", "awaiting", "unreconciled", "spinning", "share_")
                    ):
                        legs.add(name.split("@")[0])
        rows: list[LegFunnel] = []
        for leg in sorted(legs):
            cand = sum(
                v for k, v in keys.items() if k.startswith("cand:") and k[5:].split("@")[0] == leg
            )
            entered = sum(
                v
                for k, v in keys.items()
                if k.startswith("entered:") and k[8:].replace("flip_", "").split("@")[0] == leg
            )
            blocked_keys = {
                k: v
                for k, v in keys.items()
                if k.startswith("blocked:") and k[8:].split("@")[0] == leg
            }
            rows.append(LegFunnel(leg, cand, entered, sum(blocked_keys.values()), blocked_keys))
        shared = {
            k: v
            for k, v in keys.items()
            if not k.startswith(("cand:", "entered:"))
            and not (k.startswith("blocked:") and k[8:].split("@")[0] in legs)
            and not k.startswith("bars:")
        }
        out[pid] = (rows, dict(sorted(shared.items(), key=lambda kv: -kv[1])))
    return out


@dataclass(frozen=True, slots=True)
class HeldEvent:
    """진입을 붙든 사건 — 어떤 문이 어느 종목을 몇 번 · 마지막은 언제."""

    kind: str
    why: str
    symbols: int
    count: int
    last_at: datetime | None
    sample_symbols: tuple[str, ...]


def held_events(events: list[dict[str, Any]]) -> list[HeldEvent]:
    """`session_entry_*_held` · `_gate_fit` 사건을 (사건, 사유)별로 묶는다 — 많은 순."""
    acc: dict[tuple[str, str], list[tuple[datetime | None, str]]] = defaultdict(list)
    for ev in events:
        kind = str(ev.get("event_type", ""))
        if not kind.startswith("session_entry"):
            continue
        raw = ev.get("payload")
        payload: dict[str, Any] = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        why = str(
            payload.get("why")
            or payload.get("by")
            or payload.get("reason")
            or payload.get("note")
            or ""
        )[:60]
        acc[(kind, why)].append(
            (when(ev.get("ts")), str(payload.get("symbol") or ev.get("symbol") or ""))
        )
    out: list[HeldEvent] = []
    for (kind, why), items in acc.items():
        syms = sorted({s for _, s in items if s})
        last = max((t for t, _ in items if t is not None), default=None)
        out.append(HeldEvent(kind, why, len(syms), len(items), last, tuple(syms[:8])))
    return sorted(out, key=lambda h: -h.count)


GATE_ATTRS: dict[str, str] = {
    "ref_gate": "entry_ref_ma_gate",
    "ref_band": "entry_ref_return_band",
    "ref_surge": "entry_ref_surge_cap",
    "ref_sma": "entry_ref_sma_down",
    "ref_volpct": "entry_ref_vol_pct",
    "funding": "funding_cap",
}
"""보류 사유 이름 → 매매법 선언의 칸 이름. 어느 다리가 그 문을 선언했는지 여기로 찾는다."""


def gate_owners(books: Sequence[object]) -> dict[str, list[str]]:
    """보류 사유마다 그 문을 선언한 다리들 — 실측: ref_volpct 는 급락 되돌림 · ref_band 는 삼각 숏.

    Args:
        books: 매매법 선언들(`load_playbooks()`). `playbook_id` · `short_label` · 문 칸을 읽는다.

    Returns:
        `{사유: [이름, …]}` — 선언한 다리가 없는 사유는 빈 목록(지금 매매법엔 그 문이 없다).
    """
    out: dict[str, list[str]] = {k: [] for k in GATE_ATTRS}
    for book in books:
        name = str(
            getattr(book, "short_label", "")
            or getattr(book, "label", "")
            or getattr(book, "playbook_id", "?")
        )
        for gate, attr in GATE_ATTRS.items():
            if getattr(book, attr, None) is not None and name not in out[gate]:
                out[gate].append(name)
    return out


def gate_of(kind: str) -> str:
    """사건 이름 → 보류 사유(`session_entry_ref_volpct_held` → `ref_volpct`). 모르면 그대로."""
    core = kind.removeprefix("session_entry_").removesuffix("_held")
    return core if core in GATE_ATTRS else kind
