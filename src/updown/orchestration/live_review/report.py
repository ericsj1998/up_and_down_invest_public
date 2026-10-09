"""보고서(마크다운) — 사실을 표로 · 기대값은 나란히 · 판단은 안 적는다 (T444)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from updown.orchestration.live_review.attribution import LegRow, TradeMoney
from updown.orchestration.live_review.conditions import (
    BreakoutProbe,
    ConditionView,
    GateRow,
    GateSpan,
    NextBarNeed,
    ReplayGap,
    TiltRow,
)
from updown.orchestration.live_review.excursion import Excursion
from updown.orchestration.live_review.health import (
    FUNNEL_GLOSSARY,
    EventSummary,
    HeldEvent,
    LegFunnel,
    PlaybookSpan,
)
from updown.orchestration.live_review.regime import (
    DEF_NAME,
    LABEL_NAME,
    RegimeReference,
    RegimeView,
)
from updown.orchestration.live_review.snapshot import Snapshot
from updown.orchestration.live_review.structure import (
    Num,
    StructureReference,
    StructureRow,
    StructureView,
)

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
    why: dict[str, tuple[list[LegFunnel], dict[str, int]]] | None,
    held: list[HeldEvent] | None,
    owners: dict[str, list[str]] | None = None,
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
            "| 사건 | 어느 다리의 문 | 사유 | 횟수 | 종목 수 | 마지막 KST | 종목 보기 |",
            "|---|---|---|---|---|---|---|",
        ]
        for h in held[:14]:
            out.append(
                f"| {h.kind} | {_owner(h.kind, owners)} | {h.why or '—'} | {h.count} | "
                f"{h.symbols} | {kst(h.last_at)} | "
                f"{' '.join(h.sample_symbols)} |"
            )
        out.append("")
    out += ["깔때기 어휘: " + " · ".join(f"`{k}` {v}" for k, v in FUNNEL_GLOSSARY.items()), ""]
    return out


def px(v: Decimal | None) -> str:
    """가격 — 크기에 맞춘 자릿수. None 은 em dash."""
    if v is None:
        return "—"
    f = float(v)
    if abs(f) >= 1000:
        return f"{f:,.1f}"
    if abs(f) >= 1:
        return f"{f:.4f}"
    return f"{f:.6f}"


def _gate_value(row: GateRow, v: Decimal | None, *, margin: bool = False) -> str:
    if v is None:
        return "모름"
    if row.percent:
        return f"{float(v) * 100:+.2f}%" + ("p" if margin else "")
    return f"{float(v):+.3f}" if margin else f"{float(v):.3f}"


def _span_text(span: GateSpan) -> str:
    state = "열림" if span.state else ("닫힘" if span.state is False else "모름")
    if span.bars == 1:
        return f"{state} {kst(span.last_end)}(1봉)"
    return f"{state} {kst(span.first_end)} ~ {kst(span.last_end)}({span.bars}봉)"


def _gates(view: ConditionView | None, days: int) -> list[str]:
    out = ["## 1-2. 지금 문 상태 — 펀드 다리 기준(BTC) 문 · 지금 값 · 문턱까지", ""]
    if view is None or not view.series:
        reason = view.skipped if view is not None and view.skipped else "봉 없이 돌렸다"
        out += [f"- 생략: {reason}.", ""]
        return out
    now = view.series[-1]
    out += [
        f"BTC 4H 마감 봉 끝 **{kst(now.end)} KST** 기준 · 묶음 `{view.fund_id}` · "
        "값 = 러너와 같은 `reference_regime` · 판정 = 선언의 `holds` + 세션 `entry_hold` 그대로.",
        "",
        "| 다리 | 문 | 무엇 | 지금 값 | 문턱 | 통과 | 문턱까지(+ 여유 · - 모자람) | "
        "세션 판정(기준 문) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    seen: set[str] = set()
    for row in now.rows:
        verdict = ""
        if row.leg_id not in seen:
            seen.add(row.leg_id)
            hold = view.holds.get(row.leg_id)
            verdict = f"보류 `{hold}`" if hold else "통과"
        passed = "통과" if row.passed else ("**막힘**" if row.passed is False else "모름")
        if not row.gate:
            out.append(f"| {row.leg} | — | {row.what} | | | | | {verdict} |")
            continue
        out.append(
            f"| {row.leg} | `{row.gate}` | {row.what} | {_gate_value(row, row.value)} | "
            f"{row.rule} | {passed} | {_gate_value(row, row.margin, margin=True)} | {verdict} |"
        )
    out += ["", f"최근 {days}일 열림 · 닫힘 (BTC 4H 봉 · 시각은 봉 끝 KST · 최근 8 구간):", ""]
    labels = {(r.leg_id, r.gate): r.leg for r in now.rows if r.gate}
    for (leg_id, gate), spans in view.spans.items():
        opened = sum(s.bars for s in spans if s.state)
        total = sum(s.bars for s in spans)
        tail = spans[-8:]
        head = f"앞 {len(spans) - len(tail)} 구간 생략 · " if len(spans) > len(tail) else ""
        out.append(
            f"- {labels.get((leg_id, gate), leg_id)} `{gate}`: 열린 봉 {opened}/{total} — "
            + head
            + " → ".join(_span_text(s) for s in tail)
        )
    out += [
        "",
        "읽는 법: 기준 문은 BTC 4H 봉이 닫힐 때(KST 01 · 05 · 09 · 13 · 17 · 21시)만 바뀐다. "
        "**문턱까지** 가 - 면 그만큼 움직여야 열린다(띠 · 급등은 %p · 백분위는 0 ~ 1). "
        "기준 문이 없는 다리는 종목 탐지기 · 종목 문(이평 띠 · 봉 기울기) · "
        "펀드 문(자리 · 브레이크 · 폭 · 명목 상한)만 본다 — 돌파 롱은 아래 1-3, "
        "문이 막은 사건은 1-1 사건 표.",
        "",
    ]
    return out


def _tilt_text(rows: tuple[TiltRow, ...]) -> str:
    if not rows:
        return "—"
    cells: list[str] = []
    for t in rows:
        value = "모름" if t.value is None else f"{float(t.value):+.2f}%"
        state = "모름" if t.inside is None else ("안" if t.inside else "밖")
        effect = ""
        if t.inside:
            effect = " → **건너뜀**" if t.mult == 0 else f" → x{t.mult}"
        cells.append(f"{t.what} {value} {t.band} {state}{effect}")
    return " · ".join(cells)


def _probe_row(p: BreakoutProbe) -> str:
    gap = (
        "—" if p.upper is None or p.upper == 0 else f"{float((p.close / p.upper - 1) * 100):+.2f}%"
    )
    need = (
        "—" if p.need is None or p.need_pct is None else f"{px(p.need)}({float(p.need_pct):+.2f}%)"
    )
    return (
        f"| {p.symbol.removesuffix('_USDT')} | {kst(p.bar_ts)} | {px(p.close)} | {px(p.upper)} | "
        f"{gap} | {num(p.pen_atr)} | "
        f"{'—' if p.vol_ratio is None else f'{float(p.vol_ratio):.2f}'} | {p.direction:+d} | "
        f"{'—' if p.stop_pct is None else f'{float(p.stop_pct):.2f}'} | "
        f"{'**든다**' if p.fired else '—'} | {' · '.join(p.missing) or '—'} | {need} |"
    )


def _need_row(n: NextBarNeed, tilts: tuple[TiltRow, ...]) -> str:
    need = (
        "—" if n.need is None or n.need_pct is None else f"{px(n.need)}({float(n.need_pct):+.2f}%)"
    )
    vol = "—" if n.need_volume is None else f"{float(n.need_volume):,.1f}"
    four = f"{n.direction:+d}" + (" · 이 봉이 4H 를 닫음" if n.closes_4h else "")
    drop = "—" if n.low_drop_pct is None else f"{float(n.low_drop_pct):.2f}%"
    return (
        f"| {n.symbol.removesuffix('_USDT')} | {kst(n.bar_ts)} | {need} | {vol} | {four} | "
        f"{drop} | {_tilt_text(tilts)} |"
    )


def _probes(view: ConditionView | None) -> list[str]:
    out = ["## 1-3. 돌파 롱 근접 — 핵심 6종 1H 마지막 마감 봉 (탐지기 그대로 · Gate 공개 봉)", ""]
    if view is None or not view.probes:
        reason = view.skipped if view is not None and view.skipped else "봉 없이 돌렸다"
        out += [f"- 생략: {reason}.", ""]
        return out
    out += [
        f"조건(룰 설정 그대로): {view.rule_text}",
        "",
        "| 종목 | 봉 시작 KST | 종가 | BB 상단 | 상단 대비 | 관통 ATR | 거래량 배 | 4H | "
        "손절폭 % | 탐지기 | 모자란 것 | 가격 조건 종가(그 봉) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    out += [_probe_row(p) for p in view.probes]
    out += [
        "",
        "다음 봉(진행 중)이 들려면 — 다른 값이 그대로일 때의 문턱:",
        "",
        "| 종목 | 다음 봉 시작 KST | 종가 ≥ (마지막 종가 대비) | 거래량 ≥ | 4H 지금 | "
        "저가가 종가보다 ≥ 이만큼 아래 | 이평 띠(돌파 롱 선언 · 닫힌 봉) |",
        "|---|---|---|---|---|---|---|",
    ]
    for n in view.next_needs:
        out.append(_need_row(n, view.tilts.get(n.symbol, ())))
    out += [
        "",
        "읽는 법: **탐지기** 열은 탐지기를 그 봉에 그대로 부른 결과다(표의 다른 열은 설명). "
        "**가격 조건 종가** = 그 봉의 다른 값(거래량 · 4H · 손절폭)은 그대로 두고 종가만 올렸을 때 "
        "`BB 상단 밖 + 관통 하한` 을 채우는 최소 종가 — BB 상단도 그 종가를 품고 같이 오른다. "
        "4H 는 -1(하락)만 막는다. "
        "이평 띠가 **건너뜀**이면 탐지기가 울려도 세션이 그 진입을 건너뛴다. "
        "펀드 문(자리 · 브레이크 · 폭 · 명목 상한)과 같은 종목 보유는 이 표 밖이다.",
        "",
    ]
    return out


def _sval(kind: str, v: Num) -> str:
    """시장 구조 값 글꼴 — 줄의 `kind` 대로."""
    if v is None:
        return "—"
    if kind == "corr":
        return f"{v:.3f}"
    if kind == "signed":
        return f"{v:+.3f}"
    if kind == "pct":
        return f"{v:.0f}%"
    if kind == "pctchg":
        return f"{v:+.1f}%"
    if kind == "share":
        return f"{v:.2f}"
    if kind == "bn":
        return f"{v:.1f}"
    if kind == "regime":
        return f"{v * 100:.0f}%"
    return f"{v:.0f}"


def _scell(row: StructureRow, v: Num, denom: int | None, *, now: bool = False) -> str:
    if now and row.kind == "regime":
        return "—" if v is None else ("**되돌림**" if v >= 1 else "**이어짐**")
    text = _sval(row.kind, v)
    if row.kind == "count" and v is not None and denom is not None:
        text += f"/{denom}"
    return text


def _ref_cell(row: StructureRow, ref: StructureReference, k: int) -> str:
    vals = ref.values.get(row.key)
    v = vals[k] if vals else None
    text = _sval(row.kind, v)
    shares = ref.values.get("up50_share") if row.key == "up50_n" else None
    share = shares[k] if shares else None
    if v is not None and share is not None:
        text += f"({share * 100:.0f}%)"
    return text


def _span(values: tuple[Num, ...] | None, fmt: str) -> str:
    got = [v for v in values or () if v is not None]
    return "—" if not got else f"{fmt.format(min(got))} ~ {fmt.format(max(got))}"


def structure_summary(view: StructureView | None) -> str:
    """보고서 맨 위 요약 한 줄 — 시장 구조(관찰용)."""
    if view is None or view.skipped:
        reason = view.skipped if view is not None and view.skipped else "봉 없이 돌렸다"
        return f"- 시장 구조(관찰용 · 1-4): 생략 — {reason}"
    m = view.row("m_alt_corr")
    ac = view.row("ac_mean")
    vol = view.row("btc_vol30")
    up = view.row("up50_n")
    rb = view.row("rbig_n")
    regime = "—"
    if ac is not None and ac.now is not None:
        regime = f"{'되돌림' if ac.now < 0 else '이어짐'}({ac.now:+.3f})"
    events = _sval("count", rb.last30 if rb else None)
    return (
        f"- 시장 구조(관찰용 · 판정 아님 · 1-4): M {_sval('corr', m.now if m else None)} · "
        f"AC {regime} · BTC 변동성 {_sval('pct', vol.now if vol else None)} · "
        f"큰 알트 움직임 {_sval('count', up.now if up else None)}"
        f"(30일 +50% 종목 · 지금) · 잔차 사건 {events}(최근 30일)"
    )


def _lab(x: str | None) -> str:
    return "—" if x is None else f"{x} {LABEL_NAME.get(x, '')}"


def regime_summary(view: RegimeView | None) -> str:
    """보고서 맨 위 요약 한 줄 — 국면(관찰용)."""
    if view is None or view.skipped or not view.days:
        reason = view.skipped if view is not None and view.skipped else "봉 없이 돌렸다"
        return f"- 국면(관찰용 · 1-5): 생략 — {reason}"
    last = view.days[-1]
    runs = view.runs or {}
    return (
        f"- 국면(관찰용 · 판정 아님 · 1-5 · BTC 일봉 {last.day.isoformat()}): "
        f"이평 {_lab(last.m)}({runs.get('M', 0)}일째) · "
        f"구조 {_lab(last.x)}({runs.get('X', 0)}일째) · "
        f"회귀 {_lab(last.g)}({runs.get('G', 0)}일째)"
    )


def _regime(view: RegimeView | None, ref: RegimeReference | None) -> list[str]:
    out = [
        "## 1-5. 국면 · 다리 그림자 성적 — 관찰용 · 판정 아님 "
        "(T459 · Gate 공개 BTC 일봉 · 같은 정의)",
        "",
    ]
    if view is None or view.skipped or not view.days:
        reason = view.skipped if view is not None and view.skipped else "봉 없이 돌렸다"
        out += [f"- 생략: {reason}.", ""]
        return out
    out += [
        "> T459 결과: 세 판단기 모두 30일 앞 BTC 수익 순서를 2018 ~ 2026 네 창 중 "
        "한 번도 못 맞췄고, 차트를 다 본 뒤의 국면과 위 · 횡보 · 아래 세 묶음으로도 "
        "40 ~ 54% 만 같았다. 다리의 지난 30일 성적은 그 다리 · 다른 다리의 다음 매매를 "
        "가르지 못했다. 국면별로 네 창 모두 건당이 다리 평균보다 높았던 것은 BTC 상승 "
        "국면(이평 · 회귀)의 급락 되돌림 · 이평 약상승-횡보의 돌파 롱뿐이다.",
        "",
        "| 날(UTC) | 이평 M | 구조 X | 회귀 G |",
        "|---|---|---|---|",
    ]
    for d in view.days:
        out.append(f"| {d.day.isoformat()} | {_lab(d.m)} | {_lab(d.x)} | {_lab(d.g)} |")
    out.append("")
    last = view.days[-1]
    if ref is not None and ref.cells:
        out += [
            "**지금 국면에서 다리별 백테스트 건당**"
            "(net % · n · 다리 전체 건당과 나란히 · 네 창 합친 값 · 참고만):",
            "",
            "| 다리 | 전체 | "
            + " | ".join(
                f"{DEF_NAME[d]} {getattr(last, d.lower()) or '—'}" for d in ("M", "X", "G")
            )
            + " |",
            "|---|---|---|---|---|",
        ]
        for leg, name in ref.leg_name.items():
            cells: list[str] = []
            for d in ("M", "X", "G"):
                lab = cast(str | None, getattr(last, d.lower()))
                v = ref.cells.get(d, {}).get(leg, {}).get(lab or "")
                cells.append("—" if v is None else f"{v[0]:+.2f}%(n {v[1]})")
            out.append(
                f"| {name} | {ref.leg_all.get(leg, float('nan')):+.2f}% | "
                + " | ".join(cells)
                + " |"
            )
        out += ["", f"참고값 출처: {ref.source} (`config/live_review_regime.yml`)", ""]
    out += [
        "**다리 그림자 성적**(지난 30일 안 청산 · 실현 손익 USDT · R 합 · 이긴 수 / 매매 수):",
        "",
    ]
    if not view.shadow:
        out += ["- 지난 30일 청산된 매매가 없다.", ""]
        return out
    out += ["| 다리 | 매매 | 이김 | 실현 손익 | R 합 |", "|---|---|---|---|---|"]
    for s in view.shadow:
        out.append(f"| {s.leg} | {s.n} | {s.wins} | {num(s.pnl)} | {num(s.r_sum)} |")
    out.append("")
    return out


def _structure(view: StructureView | None, ref: StructureReference | None) -> list[str]:
    out = ["## 1-4. 시장 구조 — 관찰용 · 판정 아님 (T454 수치 · Gate 공개 봉 · 같은 정의)", ""]
    if view is None or view.skipped or not view.rows:
        reason = view.skipped if view is not None and view.skipped else "봉 없이 돌렸다"
        out += [f"- 생략: {reason}.", ""]
        return out
    gone = f" · 봉 못 받음: {' '.join(view.missing)}" if view.missing else ""
    day = "—" if view.day is None else view.day.isoformat()
    out += [
        f"마지막 마감 일봉 **{day}**(UTC 날 · KST 09시 시작) · "
        f"4H 마감 **{kst(view.slot_end)} KST** · "
        f"우주 = 삼각 숏 범위 {len(view.symbols)}종(`config/baskets.yml` tri18 · "
        f"M 은 BTC 뺀 알트) · 지금 M 알트 {view.alts_m} · AC 종목 {view.syms_ac}{gone}",
        "",
        "> T454 결론: 시장은 바뀌었다(큰 알트 움직임 감소 · 2025 ~ 4H 되돌림 · "
        "ETH 가 BTC 거래대금 몫을 채움). 그러나 이 수치로 다리 비중을 미리 맞히지 못했다"
        "(§5 · §6 ⛔ · PBO 0.518) — **값만 적는다. 매매 판단에 쓰지 않는다.**",
        "",
    ]
    years = ref.years if ref is not None else ()
    head = "| 수치 | 지금 | 최근 30일 | 최근 90일 |" + "".join(f" T454 {y} |" for y in years)
    out += [head, "|---|---|---|---|" + "---|" * len(years)]
    for row in view.rows:
        cells = [
            _scell(row, row.now, row.denoms[0], now=True),
            _scell(row, row.last30, row.denoms[1]),
            _scell(row, row.last90, row.denoms[2]),
        ]
        if ref is not None:
            cells += [_ref_cell(row, ref, k) for k in range(len(years))]
        out.append(f"| {row.what} | " + " | ".join(cells) + " |")
    out.append("")
    run_days = view.ac_run_bars * 4 / 24
    if ref is not None:
        out.append(
            "⚠️ AC 국면은 짧다 — T454 연도별 국면 길이 중앙 되돌림 "
            f"{_span(ref.values.get('ac_run_neg_med_days'), '{:.1f}')}일 · 이어짐 "
            f"{_span(ref.values.get('ac_run_pos_med_days'), '{:.1f}')}일 · 4H 한 칸마다 부호가 "
            f"{_span(ref.values.get('ac_flip_4h'), '{:.0%}')} 바뀐다(T454 §5). "
            f"지금 부호는 4H {view.ac_run_bars}칸({run_days:.1f}일)째."
        )
    else:
        out.append(
            f"⚠️ AC 국면은 짧다(T454 §5). 지금 부호는 4H {view.ac_run_bars}칸({run_days:.1f}일)째."
        )
    out += [
        "",
        "읽는 법: **지금** = M 은 마지막 마감 날까지 90일 · AC 는 마지막 마감 4H · "
        "나머지는 마지막 마감 일봉(거래대금 합 · 몫은 기간 값만). **최근 30 · 90일** = "
        "그 기간 날(4H 칸)마다 값의 평균 · 몫은 기간 합의 비 · 종목 수와 잔차 사건 수는 "
        "기간 안에서 센다(T454 연도 값은 1년 창이라 수가 더 크다 · +50% 칸 괄호는 그해 몫). "
        "잔차 큰 움직임 '지금' = 30일 잔차 합이 ln 1.5 이상인 알트 / 잔차 합이 있는 알트. "
        "T454 열은 바이낸스(2018 ~ 19 현물 · 2020 ~ 선물) — 정의는 같지만 거래소가 달라 "
        "거래대금 수준 · 몫은 다를 수 있다(아래 대조). "
        "거래대금 = 거래량(Gate 계약 수 x 공개 계약 명세 `quanto_multiplier` = 코인 수) x 종가.",
    ]
    if ref is not None:
        if ref.notes:
            out += ["", *(f"- ⚠️ {note}" for note in ref.notes), ""]
        if ref.source:
            out.append(f"T454 열 출처: {ref.source} (`config/live_review_market_structure.yml`)")
    out.append("")
    return out


def _replay(gap: ReplayGap | None, command: str) -> list[str]:
    out = ["## 2-1. 라이브 대 재현 돈 차이 (T447 · 같은 매매의 라이브 손익 - 재현 손익)", ""]
    if gap is None:
        out += [f"- 재현 없음 — `{command}`", ""]
        return out
    p = gap.parts
    out += [
        f"- 결과 파일 `logs/t279/t447_replay/gap.json` · 만든 시각 {kst(gap.made_at)} KST — "
        "재현 걷기의 스냅샷 · 구간은 그 도구 기본값이라 이 보고서의 스냅샷과 다를 수 있다.",
        f"- 짝 {gap.pairs}(같은 봉 {gap.pairs_strict} · 느슨 {gap.pairs - gap.pairs_strict}) · "
        f"재현만 {gap.rep_only} · 라이브만 {gap.live_only} · "
        f"라이브 시스템 진입 {gap.live_system} · 재현 진입 {gap.rep_trades}",
        f"- 차이 합(명목 대비 %) **{num(gap.gap_sum)}** = 진입 {num(p.get('entry_part'))} · "
        f"청산 {num(p.get('exit_part'))} · 수수료 {num(p.get('fee_part'))} · "
        f"펀딩 {num(p.get('fund_part'))} · 나머지 {num(p.get('rest_part'))} · "
        f"라이브 명목으로 {num(p.get('gap_usdt'))} USDT · 차이 R {num(p.get('gap_r'))}",
        f"- 짝의 라이브 손익 합 {num(gap.live_usdt)} USDT · 주인 없는 펀딩 줄 {gap.orphan_funding}",
        "",
    ]
    if gap.leg_table:
        out += [*gap.leg_table, ""]
    out += [f"다시 재기: `{command}`", ""]
    return out


def _legs(
    legs: list[LegRow], expectations: dict[str, dict[str, Any]], source: str = ""
) -> list[str]:
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
    ]
    if source:
        out.append(f"기대값 출처: {source}")
    out.append("")
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
    owners: dict[str, list[str]] | None = None,
    conditions: ConditionView | None = None,
    replay: ReplayGap | None = None,
    replay_command: str = "",
    expect_source: str = "",
    structure: StructureView | None = None,
    structure_ref: StructureReference | None = None,
    regime: RegimeView | None = None,
    regime_ref: RegimeReference | None = None,
) -> str:
    """마크다운 한 장 — 절 순서는 사용자가 묻는 순서(왜 안 들어갔나 → 손실 몫 → 경로).

    1-2(지금 문 상태) · 1-3(돌파 롱 근접) · 2-1(라이브 대 재현)은 T451 G(T445 5단계) 절이다 —
    `conditions` · `replay` 가 없으면 "생략" · "재현 없음" 한 줄로 나온다.
    1-4(시장 구조)는 T454 관찰 절이다 — `structure` 가 없으면 "생략" 이고, 맨 위 요약에
    한 줄을 더한다(판정 아님). 1-5(국면 · 그림자 성적)는 T459 관찰 절이다 — 같은 꼴.
    """
    out = _head(snap, money, outside_pnl, days)
    out.append(structure_summary(structure))
    out.append(regime_summary(regime))
    out += [f"- ⚠️ {note}" for note in notes]
    out.append("")
    out += _spans(spans)
    out += _why(why, held, owners)
    out += _gates(conditions, days)
    out += _probes(conditions)
    out += _structure(structure, structure_ref)
    out += _regime(regime, regime_ref)
    out += _legs(legs, expectations, expect_source)
    out += _replay(replay, replay_command)
    out += _funnel(funnel, events)
    out += _events(events)
    out += _flow(money, flow)
    out += _paths(money, paths)
    out += _grid(grid_by_leg, actual_r_by_leg)
    out += _trades(money)
    return "\n".join(out)


def _owner(kind: str, owners: dict[str, list[str]] | None) -> str:
    """사건 이름이 가리키는 문을 선언한 다리들 — 없으면 em dash."""
    from updown.orchestration.live_review.health import gate_of

    if not owners:
        return "—"
    names = owners.get(gate_of(kind))
    return " · ".join(names) if names else "—"
