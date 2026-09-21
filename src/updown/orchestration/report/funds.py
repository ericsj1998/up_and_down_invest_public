"""리밸런싱 펀드 스냅샷 — 파일(`logs/funds/*.json`)에서 읽는 펀드 요약 (리포트 · 대시보드용).

러너가 없는 프로세스에서도 읽혀야 하므로 메모리(`rebalancer.FUNDS`)가 아니라 **파일**을 본다 —
`_save_fund` 가 생성·편집·입출금·틱마다 쓰는 그 파일이다. TWR 원장(`portfolio/performance.py`)이
`equity · contributed · twr · twr_peak · deepest` 를 남기므로 낙폭까지 파일만으로 복원된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class FundSnapshot:
    """펀드 하나의 마지막 저장 시점 요약.

    Attributes:
        fund_id: 펀드 id.
        label: 화면 이름.
        market: 거래소.
        playbook: 각 세션이 도는 전략.
        leverage: 선언 배율(문자열 — 표기용).
        weight_mode: 비중 방식(`static` · `rank60`).
        balance: 펀드 잔고 (= TWR 원장 equity · 예산+손익).
        contributed: 넣은 돈의 합 (입금 - 출금).
        twr_pct: 시간가중 수익률(%) — 입출금과 분리된 성과.
        drawdown_pct: 지금 낙폭(%) — TWR 고점 대비.
        max_drawdown_pct: 최대 낙폭(%).
        money_gain: 금액 손익 = 잔고 - 넣은 돈.
        symbols: 바스켓 종목 (비중 순).
        weights: 종목 → 비중 문자열.
        run_keys: 종목 → 세션 핸들 — 리포트가 판 섹션을 펀드에 붙이는 열쇠.
    """

    fund_id: str
    label: str
    market: str
    playbook: str
    leverage: str
    weight_mode: str
    balance: Decimal
    contributed: Decimal
    twr_pct: Decimal
    drawdown_pct: Decimal
    max_drawdown_pct: Decimal
    money_gain: Decimal
    symbols: tuple[str, ...]
    weights: dict[str, str]
    run_keys: dict[str, str]


def funds_root() -> Path:
    """펀드 파일 디렉토리 — `rebalancer.FUNDS_ROOT` 와 같은 규칙 (기본 `logs/funds`).

    Returns:
        환경변수 `FUNDS_ROOT` 가 있으면 그 경로, 없으면 `logs/funds`.
    """
    return Path(os.environ.get("FUNDS_ROOT", "logs/funds"))


def _dec(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def load_fund_snapshots(root: Path | None = None) -> list[FundSnapshot]:
    """열린 펀드 파일 전부를 읽는다 — 못 읽는 파일은 건너뛴다 (닫힌 펀드는 `closed/` 라 안 잡힌다).

    Args:
        root: 펀드 파일 디렉토리. None 이면 `funds_root()`.

    Returns:
        파일명 순.

    Note:
        낙폭 산식은 `TwrLedger.drawdown_pct` · `max_drawdown_pct` 와 같다 — 화면과 메일이 다른 값을
        말하면 안 된다. `twr_peak <= 0` 이면 낙폭 0.
    """
    found: list[FundSnapshot] = []
    for path in sorted((root or funds_root()).glob("*.json")):
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        data = cast("dict[str, Any]", raw)
        twr = cast("dict[str, Any]", data.get("twr") or {})
        basket = cast("dict[str, Any]", data.get("basket") or {})
        members = cast("list[dict[str, Any]]", basket.get("members") or [])
        equity = _dec(twr.get("equity"))
        contributed = _dec(twr.get("contributed"))
        ratio = _dec(twr.get("twr"), "1")
        peak = _dec(twr.get("twr_peak"), "1")
        deepest = _dec(twr.get("deepest"))
        drawdown = max(peak - ratio, Decimal(0)) / peak * Decimal(100) if peak > 0 else Decimal(0)
        runs = cast("dict[str, Any]", data.get("runs") or {})
        found.append(
            FundSnapshot(
                fund_id=str(data.get("fund_id", path.stem)),
                label=str(data.get("label", path.stem)),
                market=str(data.get("market", "?")),
                playbook=str(data.get("playbook", "?")),
                leverage=str(data.get("leverage", "?")),
                weight_mode=str(data.get("weight_mode", "static")),
                balance=equity,
                contributed=contributed,
                twr_pct=(ratio - Decimal(1)) * Decimal(100),
                drawdown_pct=drawdown,
                max_drawdown_pct=max(deepest * Decimal(100), drawdown),
                money_gain=equity - contributed,
                symbols=tuple(str(m.get("symbol", "")) for m in members),
                weights={str(m.get("symbol", "")): str(m.get("weight", "")) for m in members},
                run_keys={str(k): str(v) for k, v in runs.items()},
            )
        )
    return found


LEGACY_FUND_MARKET = "GATE"
"""`market` 칸이 없는 **옛 펀드 파일**의 거래소 — 복원(`_restore_one`)과 같은 기본값이다."""


def fund_of_member(symbol: str, market: str, root: Path | None = None) -> str | None:
    """이 (거래소, 종목)을 **바스켓에 둔 열린 펀드**의 id — 없으면 None (T293).

    Args:
        symbol: 종목.
        market: 거래소 코드.
        root: 펀드 파일 디렉토리. None 이면 `funds_root()`.

    Returns:
        펀드 id 또는 None.

    Note:
        🔴 **판 저장소(`wf_runs.meta_json`)에는 펀드 id 가 없다.** 멤버 관계는 펀드 파일에만 있고,
        메모리의 `FUNDS` 는 복원이 끝나야 찬다 — 그래서 되살아나는 판이 *"나는 펀드 멤버인가"* 를
        알 방법이 파일뿐이다.

        ⚠️ **핸들이 아니라 (거래소, 종목)으로 맞춘다.** 복원도 그렇게 한다(`_running_handle`) —
        그 종목으로 도는 세션이 있으면 핸들이 달라도 펀드가 그것을 가져간다. 판정 기준이 복원과
        다르면 *"막았는데 아무도 안 풀어 주는 판"* 이 생긴다.
    """
    for snap in load_fund_snapshots(root):
        home = LEGACY_FUND_MARKET if snap.market == "?" else snap.market
        if home == market and symbol in snap.symbols:
            return snap.fund_id
    return None
