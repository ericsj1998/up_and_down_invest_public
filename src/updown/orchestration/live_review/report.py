"""보고서(마크다운) — 사실을 표로 · 기대값은 나란히 · 판단은 안 적는다 (T444)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from updown.orchestration.live_review.attribution import LegRow, TradeMoney
from updown.orchestration.live_review.excursion import Excursion
from updown.orchestration.live_review.health import (
    FUNNEL_GLOSSARY,
    EventSummary,
    HeldEvent,
    LegFunnel,
    PlaybookSpan,
)
from updown.orchestration.live_review.snapshot import Snapshot

KST = timedelta(hours=9)
GridMean = dict[tuple[Decimal, Decimal], tuple[Decimal, int]]


def kst(at: datetime | None) -> str:
    """UTC → `MM-DD HH:MM` KST. 없으면 em dash."""
    return "—" if at is None else (at.astimezone(UTC) + KST).strftime("%m-%d %H:%M")


def num(v: Decimal | float | None, digits: int = 2) -> str:
    """부호 붙인 수. 0 은 부호 없이 · None 은 em dash."""
    if v is None:
        return "—"
    return f"{float(v):+.{digits}f}" if float(v) != 0 else f"{0:.{digits}f}"


def pct(v: Decimal | float | None, digits: int = 1) -> str:
    """백분율 문자열. None 은 em dash."""
    return "—" if v is None else f"{float(v):.{digits}f}%"


def _head(snap: Snapshot, money: list[TradeMoney], outside: Decimal | None, days: int) -> list[str]:
    closed = [m for m in money if m.pnl is not None]
    total = sum((m.pnl for m in closed if m.pnl is not None), Decimal(0))
    opened = sum(1 for m in money if m.source == "open")
    stale = sum(1 for m in money if m.source == "stale")
    from_ex = sum(1 for m in closed if m.source == "exchange")
    from_ledger = sum(1 for m in closed if m.source == "ledger")
    out = [
        f"# 라이브 매매 분석 — 최근 {days}일 · 스냅샷 {kst(snap.taken_at)} KST",
        "",
        f"- 매매 {len(money)}건(닫힘 {len(closed)} · 열림 {opened} · 판 닫힌 찌꺼기 줄 {stale}) · "
        f"매매법 실현 합 **{num(total)} USDT** · 거래소 기록으로 맞춘 건 {from_ex} · "
        f"원장 추정 {from_ledger}",
    ]
    if outside is not None:
        out.append(
            f"- 같은 계좌 **매매법 밖 주문**(사람) 손익 · 수수료 합: {num(outside)} USDT"
            " — 매매법 성적에 안 넣는다(거래소 자금 원장 최근 1000줄 기준)"
        )
    acct_raw: Any = snap.exchange.get("account")
    if isinstance(acct_raw, dict):
        acct = cast("dict[str, Any]", acct_raw)
        raw_pos: list[Any] = snap.exchange.get("positions") or []
        positions: list[dict[str, Any]] = []
        for p in raw_pos:
            if isinstance(p, dict):
                row = cast("dict[str, Any]", p)
                if str(row.get("size", "0")) not in ("0", ""):
                    positions.append(row)
        out.append(f"- 거래소 계좌 총액 {acct.get('total', '—')} · 포지션 {len(positions)}")
    return out


def _spans(spans: list[PlaybookSpan]) -> list[str]:
    out = [
        "## 1. 라이브 매매법 구간 (바뀔 수 있다 — 구간별로 읽는다)",
        "",
        "| 묶음 | 다리 | 첫 진입 | 마지막 진입 | 건 |",
        "|---|---|---|---|---|",
    ]
    for s in spans:
        leg = s.leg.split("@")[0]
        out.append(f"| {s.playbook_id} | {leg} | {kst(s.first)} | {kst(s.last)} | {s.trades} |")
    out.append("")
    return out


def _why(
    why: dict[str, tuple[list[LegFunnel], dict[str, int]]] | None, held: list[HeldEvent] | None
) -> list[str]:
    out = [
        "## 1-1. 왜 안 들어갔나 — 다리별 후보 → 막힘 → 진입 (판이 열린 뒤 누적 · 40판 합)",
        "",
    ]
    if not why:
        out.append("- 깔때기 기록이 없다.")
    for pid, (rows, shared) in (why or {}).items():
        out += [
            f"**{pid}**",
            "",
            "| 다리 | 후보 | 진입 | 다리 규칙 막힘 | 막힌 사유 |",
            "|---|---|---|---|---|",
        ]
        for r in rows:
            by = (
                " · ".join(
                    f"{k.split(':', 1)[1] if ':' in k else k} {v}" for k, v in r.blocked_by.items()
                )
                or "—"
            )
            out.append(f"| {r.leg} | {r.candidates} | {r.entered} | {r.blocked} | {by} |")
        out.append("")
        if shared:
            cells = " · ".join(f"`{k}` {v}" for k, v in list(shared.items())[:24])
            out += [f"문 · 조건(다리 이름 없음 · 후보가 문까지 갔을 때 센다): {cells}", ""]
    out += [
        "읽는 법: **후보 0 = 탐지기가 한 번도 안 울렸다**(조건 자체가 안 맞음 · 문 탓이 아니다) · "
        "후보 > 진입 = 그 차이만큼 문 · 조건이 막았다. "
        "숏 다리는 `ref_sma`(기준 BTC 4H 이평이 내려가는 중이어야 연다 · T304)가 가장 흔한 사유다.",
        "",
    ]
    if held:
        out += [
            "진입을 붙든 사건(로그 · 사유별 · 많은 순):",
            "",
            "| 사건 | 사유 | 횟수 | 종목 수 | 마지막 KST | 종목 보기 |",
            "|---|---|---|---|---|---|",
        ]
        for h in held[:14]:
            out.append(
                f"| {h.kind} | {h.why or '—'} | {h.count} | {h.symbols} | {kst(h.last_at)} | "
                f"{' '.join(h.sample_symbols)} |"
            )
        out.append("")
    out += ["깔때기 어휘: " + " · ".join(f"`{k}` {v}" for k, v in FUNNEL_GLOSSARY.items()), ""]
    return out


def _legs(legs: list[LegRow], expectations: dict[str, dict[str, Any]]) -> list[str]:
    out = [
        "## 2. 다리별 손익 · 손실 몫 (닫힌 매매 · 수수료 · 펀딩 포함) · 기대값",
        "",
        "| 다리 | n | 승 | 패 | 승률 | 손익 USDT | 손실 합 | **손실 몫** | 평균 R | "
        "기대 승률 | 기대 평균 R | 기대 손실 몫 | 열림 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in legs:
        exp = expectations.get(row.leg, {})
        out.append(
            f"| {row.leg} | {row.n} | {row.wins} | {row.losses} | {pct(row.win_rate_pct)} | "
            f"{num(row.pnl)} | {num(-row.loss_sum)} | **{pct(row.loss_share_pct)}** | "
            f"{num(row.mean_r)} | {pct(exp.get('win_rate_pct'))} | {num(exp.get('mean_r'))} | "
            f"{pct(exp.get('loss_share_pct'))} | {row.open_n} |"
        )
    out += [
        "",
        "기대값은 `config/live_review_expectations.yml`(연구 · 펀드 재현에서 적어 둔 값). "
        "비어 있으면 아직 안 적은 것이다. 표본 30 미만 칸은 분포만 읽고 판정하지 않는다.",
        "",
    ]
    return out


def _funnel(funnel: dict[str, dict[str, int]], events: EventSummary) -> list[str]:
    out = ["## 3. 문 · 깔때기 — 진입이 왜 줄거나 막혔나 (판 meta.funnel 합 · 묶음별)", ""]
    if not funnel:
        out.append("- 깔때기 기록 없음(판 meta 에 funnel 이 없다).")
    for pid, keys in funnel.items():
        top = " · ".join(f"{k} {v}" for k, v in list(keys.items())[:16])
        out.append(f"- **{pid}**: {top}")
    out.append("")
    if events.entry_gates:
        out.append("진입 문 사건(로그):")
        out += [f"- {k}: {v}" for k, v in list(events.entry_gates.items())[:12]]
        out.append("")
    return out


def _events(events: EventSummary) -> list[str]:
    out = [
        "## 4. 사건 로그 — 감사 · 대조 · 오류",
        "",
        f"- 사건 {sum(events.by_type.values())}줄 · "
        f"{kst(events.first_at)} ~ {kst(events.last_at)} KST",
    ]
    if events.audit_codes:
        cells = " · ".join(f"{k} {v}" for k, v in events.audit_codes.items())
        out.append(f"- 자가 점검(live_audit_found): {cells}")
    if events.reconcile_codes:
        cells = " · ".join(f"{k} {v}" for k, v in events.reconcile_codes.items())
        out.append(f"- 거래소 대조(reconcile_finding): {cells}")
    if events.errors:
        cells = " · ".join(f"{k} {v}" for k, v in list(events.errors.items())[:12])
        out.append(f"- error · warning: {cells}")
    top = " · ".join(f"{k} {v}" for k, v in list(events.by_type.items())[:14])
    out += [f"- 사건 종류 상위: {top}", ""]
    return out


def _flow(money: list[TradeMoney], flow: dict[str, dict[str, Any]]) -> list[str]:
    out = ["## 5. 수집기(호가 · 체결 · 통계)가 진입 순간에 본 것 (로컬 T283 · 분 단위)", ""]
    if not flow:
        out += [
            "- 진입 시각에 맞는 수집기 관측이 없다"
            "(그 종목 · 그 시각을 안 받았거나 로컬이 꺼져 있었다).",
            "",
        ]
        return out
    out += [
        "| 매매 | 종목 | 다리 | 진입 KST | 스프레드 bp | 불균형5 | 매수 비율 | "
        "미결제(USD) | 롱숏(테이커) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for m in money:
        f = flow.get(m.trade.trade_id)
        if not f:
            continue
        oi = f.get("open_interest_usd")
        oi_text = "—" if oi is None else f"{float(oi) / 1e6:.1f}M"
        out.append(
            f"| {m.trade.trade_id[:8]} | {m.trade.symbol} | {m.trade.leg} | "
            f"{kst(m.trade.opened_at)} | "
            f"{f.get('spread_bp', '—')} | {f.get('imb5', '—')} | {f.get('buy_ratio', '—')} | "
            f"{oi_text} | {f.get('lsr_taker', '—')} |"
        )
    out.append("")
    return out


def _paths(money: list[TradeMoney], paths: dict[str, Excursion]) -> list[str]:
    out = ["## 6. 보유 구간 경로 — MFE · MAE · 청산 뒤 (R 단위 · 1R = 계획 손절 거리)", ""]
    if not paths:
        out += ["- 봉을 못 받았거나(`--no-candles`) 손절 계획이 없는 매매뿐이다.", ""]
        return out
    out += [
        "| 매매 | 종목 | 다리 | 결과 | 실현 R | MFE | MAE | **첫 1h MAE** | 보유 봉 | "
        "청산 뒤 최고 | 청산 뒤 최악 | 돈 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in money:
        p = paths.get(m.trade.trade_id)
        if p is None:
            continue
        out.append(
            f"| {m.trade.trade_id[:8]} | {m.trade.symbol} | {m.trade.leg} | {m.trade.outcome} | "
            f"{num(p.exit_r)} | {num(p.mfe_r)} | {num(p.mae_r)} | {num(p.early_mae_r)} | "
            f"{p.bars_held} | {num(p.after_best_r)} | {num(p.after_worst_r)} | {num(m.pnl)} |"
        )
    out += [
        "",
        "읽는 법: MFE 가 크고 실현 R 이 작으면 익절이 늦거나 되돌림에 잡힌 것 · "
        "청산 뒤 최고가 크면 일찍 나온 것 · MAE 가 1 근처면 손절 바로 앞까지 갔다 · "
        "**첫 1h MAE** 가 크면 진입 직후 바로 아래로 물린 것(돌파 첫 봉 고점 매수).",
    ]
    by_leg: dict[str, list[Decimal]] = {}
    for m in money:
        p = paths.get(m.trade.trade_id)
        if p is not None:
            by_leg.setdefault(m.trade.leg, []).append(p.early_mae_r)
    for leg, vals in by_leg.items():
        s = sorted(vals)
        deep = sum(1 for v in s if v > Decimal("0.5"))
        out.append(
            f"- {leg}: 첫 1h MAE 중앙 {num(s[len(s) // 2])}R · 0.5R 넘게 물린 건 {deep}/{len(s)}"
        )
    out.append("")
    return out


def _grid(grid_by_leg: dict[str, GridMean], actual: dict[str, Decimal | None]) -> list[str]:
    out = ["## 7. 대안 익절 · 손절 격자 — 다리별 평균 R (같은 봉은 손절 먼저 · 비관)", ""]
    if not grid_by_leg:
        out.append("- 격자 없음.")
    for leg, cells in grid_by_leg.items():
        tps = sorted({k[0] for k in cells})
        sls = sorted({k[1] for k in cells})
        n = max((n for _, n in cells.values()), default=0)
        out += [f"**{leg}** — 실제 평균 R {num(actual.get(leg))} · 표본 {n}", ""]
        out.append("| 익절 R / 손절 R | " + " | ".join(f"-{s}" for s in sls) + " |")
        out.append("|---|" + "---|" * len(sls))
        for tp in tps:
            row = " | ".join(num(cells.get((tp, s), (None, 0))[0]) for s in sls)
            out.append(f"| +{tp} | {row} |")
        out.append("")
    out += [
        "⚠️ 격자는 **같은 매매(진입)에 다른 청산을 얹은 것**이라 표본이 같다 — "
        "비교는 되지만 새 규칙의 근거는 연구 엔진 · 창 밖 검증이 정한다.",
        "",
    ]
    return out


def _trades(money: list[TradeMoney]) -> list[str]:
    out = [
        "## 8. 매매 목록 (최근 순)",
        "",
        "| 매매 | 종목 | 다리 | 방향 | 진입 KST | 청산 KST | 진입가 | 청산가 | 손절 | 결과 | "
        "R | 돈 | 출처 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    floor = datetime.min.replace(tzinfo=UTC)
    for m in sorted(money, key=lambda x: x.trade.opened_at or floor, reverse=True)[:60]:
        t = m.trade
        out.append(
            f"| {t.trade_id[:8]} | {t.symbol} | {t.leg} | {t.direction} | {kst(t.opened_at)} | "
            f"{kst(t.closed_at)} | {t.entry if t.entry is not None else '—'} | "
            f"{t.exit_price if t.exit_price is not None else '—'} | "
            f"{t.planned_stop if t.planned_stop is not None else '—'} | {t.outcome or '—'} | "
            f"{num(m.r)} | {num(m.pnl)} | {m.source} |"
        )
    out.append("")
    return out


def render(
    snap: Snapshot,
    money: list[TradeMoney],
    legs: list[LegRow],
    expectations: dict[str, dict[str, Any]],
    spans: list[PlaybookSpan],
    funnel: dict[str, dict[str, int]],
    events: EventSummary,
    paths: dict[str, Excursion],
    grid_by_leg: dict[str, GridMean],
    actual_r_by_leg: dict[str, Decimal | None],
    flow: dict[str, dict[str, Any]],
    outside_pnl: Decimal | None,
    days: int,
    notes: list[str],
    why: dict[str, tuple[list[LegFunnel], dict[str, int]]] | None = None,
    held: list[HeldEvent] | None = None,
) -> str:
    """마크다운 한 장 — 절 순서는 사용자가 묻는 순서(왜 안 들어갔나 → 손실 몫 → 경로)."""
    out = _head(snap, money, outside_pnl, days)
    out += [f"- ⚠️ {note}" for note in notes]
    out.append("")
    out += _spans(spans)
    out += _why(why, held)
    out += _legs(legs, expectations)
    out += _funnel(funnel, events)
    out += _events(events)
    out += _flow(money, flow)
    out += _paths(money, paths)
    out += _grid(grid_by_leg, actual_r_by_leg)
    out += _trades(money)
    return "\n".join(out)
