"""실거래 관문 — **코드·DB·설정에서 계산**한다 (2026-09-04 · 사용자 결정 A).

> *"지금 라이브 콘솔이 깡통인 상태 아닌가?"* — 맞다. `LiveTab` 은 콘솔이 아니라 실거래 개시
> 전 관문 목록인데, 그 목록이 **손으로 적은 상수**라 낡아 있었다 (T14·T15·T22 가 끝났는데
> 미완으로 보였다). 여기서는 판정을 **실행 시점에 코드·DB·설정에서 읽는다** — 낡을 수가 없다.

⛔ 여기에 문을 여는 경로는 없다. 관문을 통과했다고 화면이 말해도 실주문은 `OrderGateway`
   의 LIVE 분기와 `.env.live` 실키, 그리고 사람의 결정(T157)이 연다.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa
import yaml
from fastapi import APIRouter, Request

from updown.common import paths
from updown.common.config import AppEnv, load_settings
from updown.common.costs import DEFAULT_CONFIG_PATH, SlippageSource, load_cost_table
from updown.common.db.models.walkforward import (
    WalkforwardCalibration,
    WalkforwardRun,
    WalkforwardTrade,
)
from updown.common.domain.instrument import Market
from updown.common.security.live_gate import UserLiveToggle
from updown.execution import gateway as gateway_mod
from updown.execution.gateway import LiveOrderBlockedError, OrderGateway

router = APIRouter(prefix="/admin/gates", tags=["admin-gates"])

LIVE_SAMPLE_MIN = 30
"""실거래 개시 전 라이브(테스트넷) 청산 표본 하한 (§5.6.7 · CLAUDE.md "표본 30 은 바닥")."""


@dataclass(frozen=True, slots=True)
class Facts:
    """관문 판정의 입력 — 전부 실측이다. 추측을 섞으면 이 목록이 안심을 주는 장식이 된다."""

    app_env: str
    live_adapter_exists: bool
    live_branch_blocked: bool
    live_keys_readable: bool
    breaker_wired: bool
    reconcile_age_s: float | None
    slippage_source: dict[str, str]
    live_closed_trades: int
    calibration_rows: int
    mdd_limit_configured: bool
    decision: dict[str, Any] | None = None
    """`config/live_decision.yml` — 사람이 내린 실거래 개시 결정. 없으면 None."""


@dataclass(frozen=True, slots=True)
class Gate:
    """관문 한 줄. `blocking=False` 는 참고 항목(통과 여부가 문을 막지 않는다)."""

    title: str
    done: bool
    blocking: bool
    detail: str
    source: str  # code | db | config | file | human


def evaluate(f: Facts) -> list[Gate]:
    """사실 → 관문 목록 (순수 함수 · 시험 대상).

    Args:
        f: `collect` 가 모은 사실.

    Returns:
        관문들 — 이름·통과 여부·차단 여부·근거. 순서는 화면 순서다.
    """
    gate_slip = f.slippage_source.get("GATE", "?")
    bn_slip = f.slippage_source.get("BINANCE", "?")
    d: dict[str, Any] = f.decision or {}
    decided = bool(d.get("decided_at")) and bool(d.get("by"))
    mdd_waived = str(d.get("mdd_waived_reason") or "") if decided else ""
    evidence = str(d.get("testnet_evidence") or "") if decided else ""
    return [
        Gate(
            "라이브 주문 어댑터",
            f.live_adapter_exists,
            True,
            "만드는 함수가 없다"
            if not f.live_adapter_exists
            else "gateway.live_adapter — GateLiveAdapter (테스트넷과 같은 코드 · 잠금만 반대)",
            "code",
        ),
        Gate(
            "OrderGateway LIVE 분기",
            not f.live_branch_blocked,
            True,
            "차단 — APP_ENV=live · LIVE_ORDERS=1 · GATE_API_* 셋 중 빠진 것이 있다 (T157)"
            if f.live_branch_blocked
            else "열림 — APP_ENV=live · LIVE_ORDERS=1 · 실키 확인. 주문은 실계좌로 간다",
            "code",
        ),
        Gate(
            "라이브 키를 읽는 경로",
            f.live_keys_readable,
            True,
            "Settings 에 실계좌 키 필드가 없다"
            if not f.live_keys_readable
            else "Settings.gate_api_key/secret (SecretStr) — live_adapter 만 읽는다",
            "code",
        ),
        Gate(
            "브레이커(일일 손실 한도) 라이브 배선",
            f.breaker_wired,
            True,
            "Session.breaker_tripped_at · live_breaker_tripped (T22)"
            if f.breaker_wired
            else "라이브 경로에 브레이커가 없다",
            "code",
        ),
        Gate(
            "거래소 대조(reconcile)가 돈다",
            f.reconcile_age_s is not None and f.reconcile_age_s < 3600,
            True,
            f"logs/reconcile/latest.json {f.reconcile_age_s / 60:.0f}분 전 (T20)"
            if f.reconcile_age_s is not None
            else "대조 결과 파일이 없다",
            "file",
        ),
        Gate(
            "Gate 슬리피지 실측",
            gate_slip == "measured",
            True,
            f"config/costs.yml GATE slippage_source = {gate_slip}"
            + (" — 실측 전엔 판정이 낙관" if gate_slip != "measured" else ""),
            "config",
        ),
        Gate(
            "Binance 슬리피지 실측",
            bn_slip == "measured",
            True,
            f"config/costs.yml BINANCE slippage_source = {bn_slip}",
            "config",
        ),
        Gate(
            "페이퍼 교정 원장 (T185)",
            f.calibration_rows > 0,
            False,
            f"wf_calibration {f.calibration_rows} 행 — 백테스트 가정을 실측으로 바꾸는 표",
            "db",
        ),
        Gate(
            f"테스트넷 청산 표본 {LIVE_SAMPLE_MIN}건",
            f.live_closed_trades >= LIVE_SAMPLE_MIN or bool(evidence),
            True,
            f"테스트넷(live 판) 청산 매매 {f.live_closed_trades}/{LIVE_SAMPLE_MIN} "
            "(wf_trades · closed_at 있음 · 실계좌 아님)"
            + (f" · 결정 파일의 근거: {evidence}" if evidence else ""),
            "db" if f.live_closed_trades >= LIVE_SAMPLE_MIN else "human",
        ),
        Gate(
            "MDD 허용치 확정 (D1-5)",
            f.mdd_limit_configured or bool(mdd_waived),
            True,
            "config/risk.yml 에 최대 낙폭 허용치 키가 있다"
            if f.mdd_limit_configured
            else (
                f"두지 않기로 결정 — {mdd_waived}"
                if mdd_waived
                else "config/risk.yml 에 max_drawdown 류 키가 없다 — T8 · 사람이 정한다"
            ),
            "config" if f.mdd_limit_configured else "human",
        ),
        Gate(
            "실거래 개시 결정 (G1 · 사람)",
            decided,
            True,
            (
                f"config/live_decision.yml — {d.get('decided_at')} · {d.get('by')} · "
                f"{d.get('mode')} · {d.get('exchange')} {d.get('symbols')}종목 · "
                f"{d.get('capital_usdt')} USDT"
            )
            if decided
            else "코드로 열 수 없다 — 사람이 `config/live_decision.yml` 을 쓴다 (T157 소액 실거래: "
            "최소 사이즈 100회 · 백테스트 대비 -30% 이내면 증량)",
            "human",
        ),
    ]


async def _db_counts(factory: Any) -> tuple[int, int]:
    """라이브 판의 끝난 매매 수와 캘리브레이션 행 수 — 100회 판정(T157)의 분모.

    Args:
        factory: DB 세션 팩토리.

    Returns:
        `(끝난 라이브 매매, 캘리브레이션 행)`. DB 를 못 읽으면 `(-1, -1)` — 0 은 "아직 없음" 이라
        못 읽음과 섞이면 안 된다.
    """
    try:
        async with factory() as session:
            trades = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(WalkforwardTrade)
                    .join(WalkforwardRun, WalkforwardRun.id == WalkforwardTrade.run_id)
                    .where(WalkforwardRun.live.is_(True))
                    .where(WalkforwardTrade.closed_at.is_not(None))
                )
            ).scalar_one()
            calib = (
                await session.execute(
                    sa.select(sa.func.count()).select_from(WalkforwardCalibration)
                )
            ).scalar_one()
        return int(trades), int(calib)
    except Exception:
        return -1, -1


def _live_branch_blocked() -> bool:
    """지금 이 프로세스에서 실주문이 나갈 수 있나 — `order_adapter` 가 보는 조건 그대로 (T157)."""
    s = load_settings()
    armed = s.app_env is AppEnv.LIVE and s.live_orders
    keyed = s.gate_api_key is not None and s.gate_api_secret is not None
    # 빈 게이트가 여전히 막는지도 함께 확인한다 — 주입 없는 LIVE 판정은 예외여야 한다
    try:
        OrderGateway(AppEnv.LIVE).resolve_adapter(
            UserLiveToggle(user_id="gate-probe", live_enabled=True)
        )
        return False  # 주입 없이 어댑터가 나왔다 — 게이트가 뚫렸다
    except LiveOrderBlockedError:
        pass
    return not (armed and keyed)


def _slippage_sources() -> dict[str, str]:
    """코인 시장별 슬리피지 출처(실측 · 가정) — 비용표를 못 읽으면 빈 dict (판정은 `evaluate` 가).

    Returns:
        시장 이름 → `SlippageSource` 값.
    """
    try:
        table = load_cost_table(DEFAULT_CONFIG_PATH)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for market in (Market.GATE, Market.BINANCE):
        mc = table.markets.get(market)
        if mc is not None:
            src: SlippageSource = mc.slippage_source
            out[market.value] = src.value
    return out


def _mdd_configured() -> bool:
    """`config/risk.yml` 최상위 키에 낙폭 허용치가 있나 — 텍스트 검색이다 (키 이름 미확정 · T8)."""
    try:
        raw: object = yaml.safe_load(Path("config/risk.yml").read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    keys: list[str] = [str(k) for k in cast("dict[object, object]", raw)]
    text = " ".join(keys).lower()
    return "drawdown" in text or "mdd" in text


def _decision() -> dict[str, Any] | None:
    """`config/live_decision.yml` — 사람이 쓴 실거래 개시 결정. 없거나 못 읽으면 None (= 미결정)."""
    try:
        raw: object = yaml.safe_load(Path("config/live_decision.yml").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    return {str(k): v for k, v in cast("dict[object, object]", raw).items()}


def _breaker_wired() -> bool:
    """세션에 브레이커 필드(`breaker_tripped_at`)가 배선돼 있나 — 코드 사실이라 import 로 본다.

    Returns:
        있으면 True. 모듈을 못 읽으면 False (배선 안 됨과 같은 판정).
    """
    try:
        session_mod = importlib.import_module("updown.orchestration.walkforward.session")
        return "breaker_tripped_at" in getattr(
            session_mod.Session, "__dataclass_fields__", {}
        ) or hasattr(session_mod.Session, "breaker_tripped_at")
    except Exception:
        return False


async def collect(factory: Any) -> Facts:
    """코드·설정·파일·DB 에서 사실을 모은다 — 판정은 `evaluate` 가 한다.

    Args:
        factory: DB 세션 팩토리 (매매·캘리브레이션 수를 센다).

    Returns:
        판정 입력 사실. 여기서는 아무것도 판단하지 않는다 — 시험은 `evaluate` 에 표를 넣는다.
    """
    settings = load_settings()
    fields = getattr(type(settings), "model_fields", {})
    live_keys = any(
        name.startswith(("gate_api", "binance_api", "gate_live", "binance_live")) for name in fields
    )
    latest = paths.under("reconcile", "latest.json")
    age = (time.time() - latest.stat().st_mtime) if latest.exists() else None
    trades, calib = await _db_counts(factory)
    return Facts(
        app_env=settings.app_env.value,
        live_adapter_exists=hasattr(gateway_mod, "live_adapter"),
        live_branch_blocked=_live_branch_blocked(),
        live_keys_readable=live_keys,
        breaker_wired=_breaker_wired(),
        reconcile_age_s=age,
        slippage_source=_slippage_sources(),
        live_closed_trades=trades,
        calibration_rows=calib,
        mdd_limit_configured=_mdd_configured(),
        decision=_decision(),
    )


@router.get("")
async def gates(request: Request) -> dict[str, Any]:
    """실거래 관문 — 읽기 권한이면 본다 (돈 데이터가 없다).

    Args:
        request: 요청. 앱 상태의 세션 팩토리를 꺼낸다.

    Returns:
        환경 · 주문이 가는 곳 · 차단 수 · 관문 목록.
    """
    facts = await collect(request.app.state.updown.session_factory)
    rows = evaluate(facts)
    return {
        "env": facts.app_env,
        "orders_go_to": "testnet (페이크머니)"
        if facts.live_branch_blocked
        else "🔴 실계좌 (진짜 돈)",
        "blocked": sum(1 for g in rows if g.blocking and not g.done),
        "passed": sum(1 for g in rows if g.done),
        "gates": [asdict(g) for g in rows],
        "facts": asdict(facts),
    }
