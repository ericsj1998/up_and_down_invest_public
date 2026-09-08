# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""판 하나의 **주석 달린 캔들 차트** → PNG (T55 · 사용자 2026-08-24).

한 장에 담는 것:
- 캔들 (양봉 초록 · 음봉 빨강)
- 매매마다 **진입·1차익절·목표·손절** 선, **롱/숏 화살표**, **이득 구간 초록 박스 · 손해 붉은 박스**
- 위쪽 제목줄: 종목 · 매매법 · 기간 · 24시간 전 금액 · 손익률(색) · 손절/익절 횟수(색)

원칙:
- 🔴 **AI 가 값을 만들지 않는다** — 그리는 값은 전부 원장·거래소 사실이다 (규칙 #2·#11).
- Agg 백엔드(헤드리스) — 서버에 디스플레이가 없다. import 시 강제한다.
"""

from __future__ import annotations

import io
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from updown.common.logging.setup import get_logger
from updown.orchestration.report.equity import EquityPoint

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

# 🔴 matplotlib 은 **그릴 때 처음** import 한다 (2026-09-05 · 메모리 점검). 모듈 최상위에서 부르면
#    이 파일을 import 하는 API 프로세스가 기동 직후부터 matplotlib+PIL 37 MB 를 쥔다 — 실측: 순수
#    import 121 MB 중 37 MB (지연 뒤 93 MB). 리포트는 하루 한 번·요청 때만 그리므로 첫 렌더 때
#    올리고 그 뒤 재사용한다(모듈 캐시). 1 GB 서버에 api 둘(실계좌·데모)이 도는 구조라 프로세스당
#    37 MB 가 곧 74 MB 다.
_font_ready = False


def _mpl() -> ModuleType:
    """Pyplot 을 (필요하면 지금 import 해서) 돌려준다 — 한글 폰트는 처음 한 번만 잡는다."""
    global _font_ready
    import matplotlib

    matplotlib.use("Agg")  # 🔴 pyplot import 전에 — 서버엔 디스플레이가 없다
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    if not _font_ready:
        _ensure_korean_font(plt, fm)
        _font_ready = True
    return plt


_logger = get_logger("orchestration.report.chart")

# 한글 폰트를 못 찾으면 제목·라벨이 두부(□)가 된다 — 조용히 깨지지 않게 한 번 세팅한다.
_KOREAN_NAMES = ("NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", "AppleGothic")
_KOREAN_PATHS = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",  # 컨테이너 (fonts-nanum)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/mnt/c/Windows/Fonts/malgun.ttf",  # WSL 개발
)


def _ensure_korean_font(plt: ModuleType, fm: ModuleType) -> None:
    """맷플롯립이 한글을 그릴 폰트를 하나 잡아 rcParams 에 건다.

    Args:
        plt: `matplotlib.pyplot` (호출자가 지금 import 한 것).
        fm: `matplotlib.font_manager`.

    Note:
        컨테이너는 `fonts-nanum`, WSL 개발은 윈도우 Malgun 을 쓴다. 못 찾으면 경고만 남기고
        진행한다 — 리포트는 리스크 증가 행동이 아니라서 두부가 나더라도 죽이지 않는다 (§1.2.1).
    """
    available = {f.name for f in fm.fontManager.ttflist}
    for name in _KOREAN_NAMES:
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return
    for path in _KOREAN_PATHS:
        try:
            fm.fontManager.addfont(path)
        except (FileNotFoundError, OSError):
            continue
        plt.rcParams["font.family"] = fm.FontProperties(fname=path).get_name()
        plt.rcParams["axes.unicode_minus"] = False
        return
    _logger.warning("report_chart_no_korean_font", payload={"tried": list(_KOREAN_NAMES)})


# 색 토큰 — 화면(web)과 같은 결. 이득 초록 · 손해 빨강.
GREEN = "#16a34a"
RED = "#dc2626"
INK = "#1f2933"
KST_TZ = ZoneInfo("Asia/Seoul")
FAINT = "#94a3b8"
STOP = "#dc2626"
TARGET = "#16a34a"
ENTRY = "#2563eb"


@dataclass(frozen=True, slots=True)
class ChartCandle:
    """차트에 그릴 봉 하나 (도메인 Candle 과 분리 — 차트는 DB 를 모른다)."""

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class ChartTrade:
    """차트에 그릴 매매 하나 — 원장 기록에서 뽑은 사실만."""

    direction: str  # "롱" 또는 "숏"
    entry: Decimal
    stop: Decimal
    target: Decimal
    first: Decimal | None
    opened_at: datetime
    closed_at: datetime | None
    exit: Decimal | None
    gain_pct: Decimal | None
    outcome: str

    @property
    def won(self) -> bool | None:
        """이득인가 — `gain_pct` 부호. 아직 안 닫혔으면 None."""
        if self.gain_pct is None:
            return None
        return self.gain_pct > 0


@dataclass(frozen=True, slots=True)
class ChartMeta:
    """제목줄 수치 — 색은 부호가 정한다."""

    symbol: str
    playbook: str
    period_label: str
    equity_before: Decimal | None  # 24시간 전(구간 시작) 금액. 못 읽으면 None.
    gain_pct: Decimal | None  # 구간 손익률
    stops: int  # 손절 횟수
    takes: int  # 익절 횟수


def _x_of(times: Sequence[datetime], when: datetime) -> float:
    """시각을 캔들 인덱스(x 좌표)로 — 없으면 가장 가까운 앞 봉. 등간격 축을 쓴다."""
    idx = bisect_left(times, when)
    if idx >= len(times):
        return float(len(times) - 1)
    return float(idx)


def _draw_candles(ax: Axes, candles: Sequence[ChartCandle]) -> None:
    """캔들 — 심지 얇은 선 + 몸통 막대. 양봉 초록·음봉 빨강."""
    from matplotlib.patches import Rectangle  # 지연 import — `_mpl()` 뒤라 이미 올라와 있다

    for i, c in enumerate(candles):
        up = c.close >= c.open
        color = GREEN if up else RED
        o, cl = float(c.open), float(c.close)
        ax.plot([i, i], [float(c.low), float(c.high)], color=color, linewidth=0.6, zorder=2)
        lo_body, hi_body = (o, cl) if up else (cl, o)
        ax.add_patch(
            Rectangle(
                (i - 0.3, lo_body),
                0.6,
                max(hi_body - lo_body, 1e-9),
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
                zorder=3,
            )
        )


def _draw_trade(ax: Axes, times: Sequence[datetime], t: ChartTrade, n: int) -> None:
    """매매 하나 — 구간 박스 · 진입/손절/익절 선 · 롱숏 화살표."""
    x0 = _x_of(times, t.opened_at)
    x1 = _x_of(times, t.closed_at) if t.closed_at else float(n - 1)
    if x1 <= x0:
        x1 = min(x0 + 1.0, float(n - 1))

    # 이득/손해 구간 박스 — 부호가 색을 정한다. 아직 안 닫혔으면 회색.
    won = t.won
    box = FAINT if won is None else (GREEN if won else RED)
    ax.axvspan(x0, x1, color=box, alpha=0.10, zorder=1)
    # 구간 손익 % 를 박스 위쪽에 적는다 (사용자 요구 2026-08-24). x 는 데이터, y 는 축비율.
    label = "보유중" if t.gain_pct is None else f"{t.gain_pct:+.2f}%"
    ax.text(
        (x0 + x1) / 2,
        0.965,
        label,
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="top",
        fontsize=8.5,
        fontweight="bold",
        color=box,
        zorder=7,
        bbox={"boxstyle": "round,pad=0.18", "fc": "white", "ec": "none", "alpha": 0.75},
    )

    long = t.direction == "롱"
    # 진입선(파랑) · 손절선(빨강 점선) · 목표선(초록 점선) — 매매 구간에만 긋는다.
    ax.hlines(float(t.entry), x0, x1, color=ENTRY, linewidth=1.1, zorder=4)
    ax.hlines(float(t.stop), x0, x1, color=STOP, linewidth=1.0, linestyle=":", zorder=4)
    ax.hlines(float(t.target), x0, x1, color=TARGET, linewidth=1.0, linestyle="--", zorder=4)
    if t.first is not None and t.first != t.target:
        ax.hlines(
            float(t.first), x0, x1, color=TARGET, linewidth=0.8, linestyle=(0, (1, 2)), zorder=4
        )

    # 롱/숏 화살표 — 진입 지점에서. 롱은 위로 초록 △, 숏은 아래로 빨강 ▽.
    ax.annotate(
        "",
        xy=(x0, float(t.entry)),
        xytext=(x0, float(t.entry) * (0.997 if long else 1.003)),
        arrowprops={
            "arrowstyle": "-|>",
            "color": GREEN if long else RED,
            "linewidth": 1.6,
        },
        zorder=6,
    )
    entry_color = GREEN if long else RED
    ax.plot(
        x0, float(t.entry), marker="^" if long else "v", color=entry_color, markersize=8, zorder=6
    )
    # 진입 지점 텍스트 — 화살표 반대쪽에 둔다 (겹치지 않게).
    ax.annotate(
        f"진입 {t.direction}",
        xy=(x0, float(t.entry)),
        xytext=(-5, 9 if long else -9),
        textcoords="offset points",
        ha="right",
        va="bottom" if long else "top",
        fontsize=7.5,
        fontweight="bold",
        color=entry_color,
        zorder=7,
    )
    # 청산 지점 점 + 결말 텍스트(익절/손절 등) — 이득/손해 색.
    if t.closed_at is not None and t.exit is not None:
        ax.plot(x1, float(t.exit), marker="o", color=box, markersize=6, zorder=6)
        ax.annotate(
            t.outcome,
            xy=(x1, float(t.exit)),
            xytext=(5, 9 if (won) else -9),
            textcoords="offset points",
            ha="left",
            va="bottom" if won else "top",
            fontsize=7.5,
            fontweight="bold",
            color=box,
            zorder=7,
        )


def render_run_chart(
    *,
    candles: Sequence[ChartCandle],
    trades: Sequence[ChartTrade],
    meta: ChartMeta,
) -> bytes:
    """한 판의 주석 차트를 PNG 바이트로 돌려준다.

    Args:
        candles: 시각 오름차순 봉들.
        trades: 이 판의 매매들 (진입 시각이 구간 안).
        meta: 제목줄 수치.

    Returns:
        PNG 바이트. 봉이 없으면 "봉 없음" 한 장을 그린다 — 빈 첨부로 조용히 죽지 않는다.
    """
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=130)
    fig.subplots_adjust(left=0.06, right=0.99, top=0.80, bottom=0.10)

    if not candles:
        ax.text(0.5, 0.5, "봉 없음", ha="center", va="center", color=FAINT, fontsize=14)
        ax.axis("off")
    else:
        times = [c.ts for c in candles]
        _draw_candles(ax, candles)
        for t in trades:
            _draw_trade(ax, times, t, len(candles))
        ax.set_xlim(-1, len(candles))
        ax.margins(y=0.08)
        ax.grid(True, color="#eef2f7", linewidth=0.6, zorder=0)
        ax.tick_params(labelsize=8, colors=FAINT)
        for spine in ax.spines.values():
            spine.set_color("#e2e8f0")
        # x 눈금 — 봉 인덱스에 시각 라벨을 몇 개만.
        step = max(1, len(candles) // 6)
        ticks = list(range(0, len(candles), step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([times[i].strftime("%m-%d %H:%M") for i in ticks], fontsize=7)

    _title(fig, meta)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()


def _title(fig: Figure, meta: ChartMeta) -> None:
    """제목줄 — 종목·매매법·기간(검정) + 손익률·손절·익절(색)."""
    gain = meta.gain_pct
    gain_color = INK if gain is None else (GREEN if gain > 0 else RED if gain < 0 else INK)
    fig.text(
        0.06,
        0.94,
        f"{meta.symbol}  ·  {meta.playbook}",
        fontsize=13,
        fontweight="bold",
        color=INK,
    )
    fig.text(0.06, 0.885, meta.period_label, fontsize=9, color=FAINT)
    # 왼쪽 셋째 줄 — 익절(초록)·손절(빨강)을 색으로. 고정 오프셋이라 안 겹친다.
    fig.text(0.06, 0.84, f"익절 {meta.takes}", fontsize=9.5, color=GREEN, fontweight="bold")
    fig.text(0.14, 0.84, f"손절 {meta.stops}", fontsize=9.5, color=RED, fontweight="bold")

    before = "—" if meta.equity_before is None else f"{meta.equity_before:.2f}"
    gain_txt = "—" if gain is None else f"{gain:+.2f}%"
    # 오른쪽 — 손익률(큰 글씨·부호 색) + 24시간 전 금액.
    fig.text(0.99, 0.935, gain_txt, fontsize=16, fontweight="bold", color=gain_color, ha="right")
    fig.text(0.99, 0.87, f"24h 전 {before} USDT", fontsize=8.5, color=FAINT, ha="right")


def render_equity_chart(points: Sequence[EquityPoint], *, title: str = "계좌 총액") -> bytes:
    """일일 자산 스냅샷을 꺾은선 PNG 로 (사용자 2026-09-07: 한 달 단위 자산 변화).

    Args:
        points: 시각 순 스냅샷. `total` 이 None 인 점은 건너뛴다.
        title: 그림 제목.

    Returns:
        PNG 바이트. 점이 둘 미만이면 "쌓이는 중" 한 장 — 빈 첨부로 조용히 죽지 않는다.

    Note:
        x 축은 달 경계(KST)에 눈금을 둔다 — 사람이 읽는 단위가 달이라서다. 값은 매일 한 점이라
        달 안의 굴곡도 보인다. 스냅샷은 배포 뒤부터 쌓이므로 과거는 그리지 않는다(지어내지 않는다).
    """
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(10, 3.4), dpi=130)
    fig.subplots_adjust(left=0.08, right=0.99, top=0.86, bottom=0.16)
    rows = [(p.at.astimezone(KST_TZ), p.total) for p in points if p.total is not None]
    if len(rows) < 2:
        days = len(rows)
        ax.text(
            0.5,
            0.5,
            f"자산 기록 {days}일치 — 매일 한 점씩 쌓여 꺾은선이 된다",
            ha="center",
            va="center",
            color=FAINT,
            fontsize=12,
        )
        ax.axis("off")
    else:
        xs = [at for at, _ in rows]
        ys = [float(total) for _, total in rows]
        ax.plot(xs, ys, color="#0f7b6c", linewidth=1.8, zorder=3)
        ax.fill_between(xs, ys, min(ys), color="#0f7b6c", alpha=0.08, zorder=2)
        ax.grid(True, color="#eef2f7", linewidth=0.6, zorder=0)
        ax.tick_params(labelsize=8, colors=FAINT)
        for spine in ax.spines.values():
            spine.set_color("#e2e8f0")
        months = sorted({(at.year, at.month) for at in xs})
        ticks = [datetime(y, m, 1, tzinfo=KST_TZ) for y, m in months]
        ax.set_xticks(ticks)
        ax.set_xticklabels([f"{y}-{m:02d}" for y, m in months], fontsize=8)
        ax.set_xlim(min(xs), max(xs))
        ax.annotate(
            f"{ys[-1]:,.2f}",
            (xs[-1], ys[-1]),
            textcoords="offset points",
            xytext=(-4, 6),
            ha="right",
            fontsize=8,
            color=INK,
        )
    fig.suptitle(
        f"{title} · USDT · 매일 00:05 KST 스냅샷", fontsize=10, color=INK, x=0.08, ha="left"
    )
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()
