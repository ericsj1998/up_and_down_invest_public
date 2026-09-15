# ruff: noqa: E501
"""이메일 HTML 렌더 — 콘솔(전체) · 판(단일). 목업 디자인의 이메일판 (T55).

두 종류:
- **콘솔**: 계좌 전체(전체 금액·손익·금고) + 판별 세부(차트·주문표) 취합.
- **판**: 그 판 하나(차트·주문표)만.

원칙:
- 🔴 **AI 가 값을 안 만든다** — 전부 원장·거래소 사실 (규칙 #2·#11).
- 🔴 **못 읽은 값은 "—"** — 0 으로 꾸미지 않는다 (규칙 #8). 그래서 금액 필드는 전부 `| None`.
- 이메일 클라이언트는 외부 CSS·flex 를 잘 못 받는다 → **인라인 스타일 + 표**로 짠다.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# 색 — 화면·차트와 같은 결. 이득 초록 · 손해 빨강 · 진입 파랑.
INK = "#1b2530"
MUTED = "#6b7a8d"
FAINT = "#97a4b4"
LINE = "#e4e9f0"
PANEL = "#ffffff"
HERO = "#f4f7fb"
BAND = "#101a2b"
BAND_INK = "#dfe7f3"
UP = "#16a34a"
DOWN = "#dc2626"
FONT = "'Segoe UI',-apple-system,BlinkMacSystemFont,'Malgun Gothic',sans-serif"
MONO = "'SFMono-Regular',Consolas,'Liberation Mono',monospace"


def _esc(text: str) -> str:
    """HTML 이스케이프 — 종목·매매법 이름은 사용자 입력이라 그대로 박지 않는다."""
    return _html.escape(str(text))


def _sign(value: Decimal | None) -> str:
    """부호 색 — 못 읽으면 잉크색(중립)."""
    if value is None:
        return INK
    return UP if value > 0 else DOWN if value < 0 else INK


def _usdt(value: Decimal | None, digits: int = 2) -> str:
    """USDT 금액 — None 이면 '—' (모른다)."""
    return "—" if value is None else f"{value:,.{digits}f}"


def _pct(value: Decimal | None) -> str:
    """부호 붙은 % — None 이면 '—' (모른다)."""
    return "—" if value is None else f"{value:+.2f}%"


@dataclass(frozen=True, slots=True)
class TradeRow:
    """주문표 한 줄 — 사실만."""

    direction: str  # "롱"/"숏"
    entry: Decimal
    exit: Decimal | None
    gain_pct: Decimal | None
    outcome: str
    leverage: Decimal | None
    when: str  # 이미 사람이 읽는 시각 문자열


@dataclass(frozen=True, slots=True)
class RunSection:
    """판 하나 — 차트 + 제목 수치 + 주문표."""

    symbol: str
    playbook: str
    period_label: str
    gain_pct: Decimal | None
    equity_before: Decimal | None
    takes: int
    stops: int
    chart_b64: str  # data URI 없이 base64 본문만
    trades: Sequence[TradeRow] = field(default_factory=tuple)
    fund_label: str | None = None
    """이 판을 소유한 리밸런싱 펀드 이름 — 없으면 개별 판 (T211 후속 · 사용자 2026-09-04
    *"펀드 개념도 적용 안 되어 있는 것 같아"*). 리포트는 이 값으로 판을 펀드별로 묶는다."""


@dataclass(frozen=True, slots=True)
class AccountSummary:
    """계좌 전체 스냅샷 — 콘솔 총 자산과 **같은 공식**. 못 읽은 값은 None.

    Attributes:
        total: 계좌 전체 금액 = 잔액 + Σ(모든 판 증거금).
        available: 잔액(지갑).
        locked: Σ(모든 판 증거금).
        realized: 구간 실현 손익 (거래소 pnl 합).
        unrealized: 지금 미실현 손익 (Σ 포지션).
        vault: 금고 잔고 = reserved 합 빼기 withdrawn 합. 못 구하면 None.
        win_rate: 승률(%).
        trades: 매매 수.
        runs: 판 수.
        diverged: 원장·거래소 대조가 갈렸나.
        note: 대조 한 줄.
    """

    total: Decimal | None
    available: Decimal | None
    locked: Decimal | None
    realized: Decimal | None
    unrealized: Decimal | None
    vault: Decimal | None
    win_rate: Decimal | None
    trades: int
    runs: int
    diverged: bool
    note: str


@dataclass(frozen=True, slots=True)
class FundSection:
    """리밸런싱 펀드 하나 — 계좌와 판 사이의 **중간 층** (사용자 2026-09-07).

    Attributes:
        label: 펀드 이름.
        market: 거래소.
        playbook: 전략.
        balance: 잔고 (예산+손익).
        twr_pct: 시간가중 수익률(%) — 입출금과 분리.
        max_drawdown_pct: 최대 낙폭(%).
        money_gain: 금액 손익.
        symbols: 종목 수.
        runs: 이 펀드가 소유한 판들의 섹션 (이 구간 주문이 있는 것만).
    """

    label: str
    market: str
    playbook: str
    balance: Decimal
    twr_pct: Decimal
    max_drawdown_pct: Decimal
    money_gain: Decimal
    symbols: int
    runs: Sequence[RunSection] = field(default_factory=tuple)


def _fund_card(fund: FundSection) -> str:
    """펀드 카드 — 제목줄 · 4칸 수치(잔고·TWR·MDD·금액 손익) · 그 펀드의 판 카드들.

    MDD 0 은 "—" 로 그린다(낙폭 없음). 이 구간 주문이 없는 펀드는 빈 카드 대신 그 사실을 한
    줄로 적는다.
    """
    cells = [
        ("펀드 잔고", _usdt(fund.balance), INK),
        ("성과 (TWR)", _pct(fund.twr_pct), _sign(fund.twr_pct)),
        (
            "최대 낙폭",
            "—" if fund.max_drawdown_pct == 0 else f"-{fund.max_drawdown_pct:.2f}%",
            DOWN,
        ),
        ("금액 손익", _usdt(fund.money_gain), _sign(fund.money_gain)),
    ]
    tds = "".join(
        f'<td style="padding:10px 14px;background:{HERO};border-right:1px solid {LINE};width:25%;">'
        f'<div style="font-size:11px;color:{FAINT};">{_esc(k)}</div>'
        f'<div style="font-size:17px;font-weight:600;color:{c};font-family:{MONO};margin-top:2px;">{_esc(v)}</div></td>'
        for k, v, c in cells
    )
    inner = (
        "".join(_run_card(r) for r in fund.runs)
        if fund.runs
        else f'<div style="color:{FAINT};font-size:12px;padding:10px 16px;">이 구간 주문 없음 — 전 종목 현금 또는 보유 유지</div>'
    )
    return (
        f'<div style="border:1px solid {LINE};border-radius:14px;overflow:hidden;margin-bottom:18px;background:{PANEL};">'
        f'<div style="padding:12px 16px 6px;">'
        f'<span style="font-weight:600;font-size:15px;color:{INK};">{_esc(fund.label)}</span>'
        f'<span style="color:{MUTED};font-size:12px;"> {_esc(fund.market)} · {_esc(fund.playbook)} · {fund.symbols}종 · 판 {len(fund.runs)}</span></div>'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
        f'border-top:1px solid {LINE};border-bottom:1px solid {LINE};"><tr>{tds}</tr></table>'
        f'<div style="padding:10px 12px 0;">{inner}</div></div>'
    )


def _equity_block(equity_b64: str | None) -> str:
    """계좌 총액 시계열 PNG 블록 — 기록이 없으면 그 사실을 한 줄로 (빈 그림을 안 그린다)."""
    if equity_b64 is None:
        return (
            f'<div style="color:{FAINT};font-size:12px;margin:10px 4px 0;">'
            "자산 시계열: 아직 기록이 없다 — 매일 00:05 KST 한 점씩 쌓여 월별 꺾은선이 된다</div>"
        )
    return (
        f'<div style="margin:14px 0 0;border:1px solid {LINE};border-radius:12px;overflow:hidden;">'
        f'<img src="data:image/png;base64,{equity_b64}" alt="계좌 총액 시계열" '
        f'style="display:block;width:100%;height:auto;background:#fff;" /></div>'
    )


def _chart_img(section: RunSection) -> str:
    """판 차트 — base64 를 data URI 로 인라인 (이메일은 외부 이미지를 막는다)."""
    src = f"data:image/png;base64,{section.chart_b64}"
    return (
        f'<img src="{src}" alt="{_esc(section.symbol)} 차트" '
        f'style="display:block;width:100%;height:auto;border-top:1px solid {LINE};'
        f'border-bottom:1px solid {LINE};background:#fff;" />'
    )


def _stat_grid(section: RunSection) -> str:
    """판 카드의 4칸 수치줄 — 24h 전 잔고 · 손익률 · 익절 수 · 손절 수."""
    g = _sign(section.gain_pct)
    tk = UP if section.takes else MUTED
    st = DOWN if section.stops else MUTED
    cells = [
        ("24h 전", _usdt(section.equity_before), INK),
        ("손익률", _pct(section.gain_pct), g),
        ("익절", str(section.takes), tk),
        ("손절", str(section.stops), st),
    ]
    tds = "".join(
        f'<td style="padding:11px 14px;border-right:1px solid {LINE};width:25%;">'
        f'<div style="font-size:11px;color:{FAINT};">{_esc(k)}</div>'
        f'<div style="font-size:15px;font-weight:600;color:{c};font-family:{MONO};">{_esc(v)}</div>'
        f"</td>"
        for k, v, c in cells
    )
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="border-collapse:collapse;border-top:1px solid {LINE};"><tr>{tds}</tr></table>'
    )


def _trade_table(section: RunSection) -> str:
    """판 카드의 주문 내역 표 — 방향·진입·청산·손익·결말·배율·시각. 주문이 없으면 빈 문자열."""
    if not section.trades:
        return ""
    head = "".join(
        f'<th style="text-align:{a};font-weight:500;color:{FAINT};font-size:11px;'
        f'padding:8px 12px;border-bottom:1px solid {LINE};white-space:nowrap;">{_esc(h)}</th>'
        for h, a in (
            ("방향", "left"),
            ("진입가", "right"),
            ("청산가", "right"),
            ("손익", "right"),
            ("결말", "left"),
            ("배율", "right"),
            ("시각", "right"),
        )
    )
    rows = ""
    for t in section.trades:
        dcol = UP if t.direction == "롱" else DOWN
        gcol = _sign(t.gain_pct)
        ocol = UP if "익절" in t.outcome else (DOWN if t.outcome in ("손절", "강제청산") else MUTED)
        lev = "—" if t.leverage is None else f"{t.leverage:g}x"
        cells = [
            (t.direction, "left", dcol, False),
            (_usdt(t.entry, 4), "right", INK, True),
            (_usdt(t.exit, 4) if t.exit is not None else "미실현", "right", INK, True),
            (_pct(t.gain_pct), "right", gcol, True),
            (t.outcome, "left", ocol, False),
            (lev, "right", MUTED, True),
            (t.when, "right", FAINT, False),
        ]
        tds = "".join(
            f'<td style="padding:9px 12px;border-bottom:1px solid {LINE};text-align:{a};'
            f'color:{c};white-space:nowrap;{"font-family:" + MONO + ";" if mono else ""}">{_esc(v)}</td>'
            for v, a, c, mono in cells
        )
        rows += f"<tr>{tds}</tr>"
    return (
        f'<div style="padding:8px 14px 2px;color:{FAINT};font-size:11px;">주문 내역</div>'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="border-collapse:collapse;font-size:12px;"><thead><tr>{head}</tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _run_card(section: RunSection) -> str:
    """판 카드 — 제목줄(종목·매매법·손익률) · 구간 · 차트 · 수치줄 · 주문표."""
    g = _sign(section.gain_pct)
    return (
        f'<div style="border:1px solid {LINE};border-radius:14px;overflow:hidden;'
        f'margin-bottom:16px;background:{PANEL};">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="padding:14px 16px 4px;">'
        f'<span style="font-weight:600;font-size:15px;color:{INK};">{_esc(section.symbol)}</span>'
        f'<span style="color:{MUTED};font-size:13px;"> {_esc(section.playbook)}</span></td>'
        f'<td style="padding:14px 16px 4px;text-align:right;font-size:18px;font-weight:600;'
        f'font-family:{MONO};color:{g};">{_pct(section.gain_pct)}</td></tr></table>'
        f'<div style="color:{FAINT};font-size:12px;padding:0 16px 12px;">{_esc(section.period_label)}</div>'
        f"{_chart_img(section)}{_stat_grid(section)}{_trade_table(section)}"
        f"</div>"
    )


def _band(title: str, when: str) -> str:
    """머리띠 — 왼쪽 제목 · 오른쪽 시각."""
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="background:{BAND};"><tr>'
        f'<td style="padding:18px 24px;color:{BAND_INK};font-weight:600;font-size:16px;">'
        f"● {_esc(title)}</td>"
        f'<td style="padding:18px 24px;text-align:right;color:#9fb2cc;font-size:12px;">{_esc(when)}</td>'
        f"</tr></table>"
    )


def _shell(inner: str) -> str:
    """바깥 틀 — 회색 바탕 위 가운데 정렬된 680px 패널."""
    return (
        f'<div style="background:#eef1f5;padding:24px 12px;font-family:{FONT};">'
        f'<div style="max-width:680px;margin:0 auto;background:{PANEL};border:1px solid {LINE};'
        f'border-radius:16px;overflow:hidden;">{inner}</div></div>'
    )


def _hero(account: AccountSummary) -> str:
    """계좌 요약 3칸 — 전체 금액 · 24h 손익(실현+미실현) · 금고. 각 칸에 공식 한 줄을 붙인다."""
    cells = [
        ("계좌 전체 금액", _usdt(account.total), INK, "잔액 + 모든 판 증거금"),
        (
            "계좌 전체 손익 · 24h",
            _usdt(_add(account.realized, account.unrealized)),
            _sign(_add(account.realized, account.unrealized)),
            f"실현 {_usdt(account.realized)} · 미실현 {_usdt(account.unrealized)}",
        ),
        (
            "금고 · 스킴 잔고",
            _usdt(account.vault),
            _sign(account.vault),
            "수익선 넘겨 뺀 돈 (총 자산엔 안 더함)",
        ),
    ]
    tds = "".join(
        f'<td style="padding:16px 18px;background:{HERO};border-right:1px solid {LINE};width:33%;'
        f'vertical-align:top;">'
        f'<div style="font-size:11px;color:{FAINT};">{_esc(k)}</div>'
        f'<div style="font-size:22px;font-weight:600;color:{c};font-family:{MONO};margin-top:4px;">{_esc(v)}'
        f'<span style="font-size:12px;color:{MUTED};font-family:{FONT};"> USDT</span></div>'
        f'<div style="font-size:11px;color:{FAINT};margin-top:5px;">{_esc(sub)}</div></td>'
        for k, v, c, sub in cells
    )
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="border-collapse:collapse;border:1px solid {LINE};border-radius:12px;'
        f'overflow:hidden;"><tr>{tds}</tr></table>'
    )


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """둘 다 모르면 None, 하나라도 알면 아는 것만 더한다 — 모르는 값을 0 으로 꾸미지 않는다."""
    if a is None and b is None:
        return None
    return (a or Decimal(0)) + (b or Decimal(0))


def _subrow(account: AccountSummary) -> str:
    """요약 아래 한 줄 — 잔액 · 승률 · 매매 수 · 판 수 · 원장/거래소 대조 결과."""
    ok_col = UP if not account.diverged else DOWN
    ok_txt = "원장·거래소 대조 일치" if not account.diverged else "🔴 원장·거래소 대조 불일치"
    wr = "—" if account.win_rate is None else f"{account.win_rate:.0f}%"
    return (
        f'<div style="padding:12px 4px 2px;font-size:13px;color:{MUTED};">'
        f'잔액 <b style="color:{INK};font-family:{MONO};">{_usdt(account.available)}</b> · '
        f'승률 <b style="color:{INK};font-family:{MONO};">{_esc(wr)}</b> · '
        f'매매 <b style="color:{INK};font-family:{MONO};">{account.trades}</b>건 · '
        f'판 <b style="color:{INK};font-family:{MONO};">{account.runs}</b> · '
        f'<span style="color:{ok_col};">{_esc(ok_txt)}</span></div>'
    )


def render_console_email(
    account: AccountSummary,
    runs: Sequence[RunSection],
    *,
    when: str,
    funds: Sequence[FundSection] = (),
    equity_b64: str | None = None,
) -> str:
    """전체 취합 이메일 — 계좌 요약 → (자산 시계열) → **펀드 종합** → 판별 세부.

    Args:
        account: 계좌 전체 스냅샷.
        runs: 판별 섹션들 (차트·주문표 포함). 펀드에 속한 판은 그 펀드 카드 안에 들어간다.
        when: 헤더에 적을 시각 문자열 (예: "2026-08-24 09:05 KST · 지난 24시간").
        funds: 펀드 카드들 (사용자 2026-09-07 — 계좌와 판 사이의 중간 층).
        equity_b64: 계좌 총액 시계열 PNG(base64). None 이면 "기록 없음" 한 줄.

    Returns:
        완결 HTML 문서.
    """
    head_css = (
        f"font-size:12px;letter-spacing:.05em;color:{FAINT};font-weight:600;margin:22px 4px 12px;"
    )
    fund_labels = {f.label for f in funds}
    loose = [r for r in runs if (r.fund_label or "") not in fund_labels]
    parts: list[str] = []
    if funds:
        parts.append(f'<div style="{head_css}">리밸런싱 펀드 · {len(funds)}개</div>')
        parts.extend(_fund_card(f) for f in funds)
    if not runs and not funds:
        # 🔴 주문이 없으면 "판별 세부 · 11판" 아래 빈 공간을 주지 않는다 (사용자 2026-09-04).
        #    사람이 본 것은 제목만 있고 내용이 없는 화면이었다 — 그건 고장으로 읽힌다.
        parts.append(
            f'<div style="{head_css}">이 구간 주문 없음</div>'
            f'<div style="color:{FAINT};font-size:13px;margin:0 4px 12px;">'
            f"판 {account.runs}개 전부 현금 또는 보유 유지 — 새 진입·청산이 없었다.</div>"
        )
    elif loose:
        # 펀드 밖의 판 — 펀드가 없는 판은 "개별 판" 아래. (펀드 카드에 못 붙은 fund_label 도 여기 이름으로)
        groups: dict[str, list[RunSection]] = {}
        for r in loose:
            groups.setdefault(r.fund_label or "개별 판", []).append(r)
        parts.extend(
            f'<div style="{head_css}">{_esc(label)} · {len(items)}판</div>'
            f"{''.join(_run_card(r) for r in items)}"
            for label, items in groups.items()
        )
    body = (
        f'<div style="padding:22px 24px;">{_hero(account)}{_subrow(account)}'
        f"{_equity_block(equity_b64)}{''.join(parts)}</div>"
    )
    return _doc(_shell(_band("업앤다운", when) + body))


def render_run_email(section: RunSection, *, when: str) -> str:
    """판 하나만 담는 이메일.

    Args:
        section: 그 판 섹션.
        when: 헤더 시각 문자열.

    Returns:
        완결 HTML 문서.
    """
    body = f'<div style="padding:22px 24px;">{_run_card(section)}</div>'
    title = f"업앤다운 · {section.symbol}"
    return _doc(_shell(_band(title, when) + body))


def _doc(inner: str) -> str:
    """완결 HTML 문서 — doctype · charset · viewport 만 얹는다."""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'</head><body style="margin:0;background:#eef1f5;">{inner}</body></html>'
    )
