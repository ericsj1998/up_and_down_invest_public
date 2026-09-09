"""AI 투자 어시스턴트 — 첫 접속 온보딩 위저드 API (T247 · 2026-09-09).

    GET  /assistant/draft            내 초안 + 처음인가 + 동의 문구
    PUT  /assistant/draft            초안 저장 (`step` · `answers` · 처음 한 번 `consent_version`)
    GET  /assistant/preview?group=&tier=   성향에 맞는 매매법 후보와 과거 창 실측 · 기본 선택
    POST /assistant/create           초안대로 펀드를 만든다 (기존 펀드 API · 관문 그대로)

처음엔 AI 가 아니라 **사전 세팅**이다 — 답이 곧 펀드 생성 페이로드가 된다. 숫자는 전부 저장소의
과거 창 실측이고 "예상" 이라는 말은 쓰지 않는다. 동의 없이는 다음 단계가 없다(서버가 400).

게스트(`guest@demo`)는 한 행을 여럿이 공유하므로 **초안을 서버에 두지 않는다** — 화면이 브라우저에
들고, 응답의 `persisted: false` 가 그 사실을 말한다. 게스트는 데모 펀드만 만든다(T230 기본값
그대로).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException, Request

from updown.analysis.playbook.select import load_playbooks
from updown.apps.api import auth, rebalancer, walkforward
from updown.apps.api import evidence as ev
from updown.apps.api.assistant_pick import WINDOWS, pick_default, window_stats
from updown.apps.api.auth import Caller, caller_of
from updown.common.db.models.accounts import AssistantDraft
from updown.common.db.models.enums import LogLevel
from updown.common.db.models.ops import EventLog
from updown.common.domain.instrument import Market, MarketGroup
from updown.common.logging.context import get_trace_id, new_trace_id
from updown.common.logging.setup import get_logger
from updown.common.security.consent import DISCLAIMER_TEXT, DISCLAIMER_VERSION, consent_is_current
from updown.common.security.markets import GROUP_LABELS, GROUPS
from updown.common.security.redact import redact_pnl
from updown.common.security.roles import Role
from updown.orchestration.report import evidence_charts as ec
from updown.orchestration.report.risk import TIER_LABELS, Tier

_logger = get_logger("api.assistant")

router = APIRouter(prefix="/assistant", tags=["assistant"])

STEPS: tuple[str, ...] = ("consent", "capital", "profile", "setup", "review", "done")
"""단계 순서 — 화면과 서버가 같은 이름을 쓴다."""

_GROUP_OF: dict[str, MarketGroup] = {
    "coin": MarketGroup.COIN,
    "domestic": MarketGroup.DOMESTIC_STOCK,
    "foreign": MarketGroup.FOREIGN_STOCK,
}


def _draft_json(row: AssistantDraft | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "step": row.step,
        "answers": dict(row.answers),
        "consent_version": row.consent_version,
        "consent_at": None if row.consent_at is None else row.consent_at.isoformat(),
        "fund_id": row.fund_id,
        "updated_at": row.updated_at.isoformat(),
    }


async def _who(request: Request) -> Caller | None:
    found = getattr(request.state, "caller", None)
    if isinstance(found, Caller):
        return found
    return await caller_of(request)


@router.get("/draft")
async def read_draft(request: Request) -> dict[str, Any]:
    """내 초안.

    Args:
        request: 요청.

    Returns:
        `{first, persisted, draft, disclaimer: {version, text}, steps}` — `first` 는 초안도
        펀드도 없음.
    """
    who = await _who(request)
    disclaimer = {"version": DISCLAIMER_VERSION, "text": DISCLAIMER_TEXT}
    if who is None or who.role is Role.GUEST:
        return {
            "first": True,
            "persisted": False,
            "draft": None,
            "disclaimer": disclaimer,
            "steps": STEPS,
        }
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        row = await session.get(AssistantDraft, who.email)
    return {
        "first": row is None or (row.fund_id is None and row.step != "done"),
        "persisted": True,
        "draft": _draft_json(row),
        "disclaimer": disclaimer,
        "steps": STEPS,
    }


@router.put("/draft")
async def save_draft(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """초안을 저장한다 — 동의는 처음 한 번, 같은 트랜잭션에 `event_logs.consent_given`.

    Args:
        request: 요청.
        payload: `{step, answers, consent_version?}`.

    Returns:
        `{persisted, draft}`.

    Raises:
        HTTPException: 400 모르는 단계 · 동의 버전이 지금 문장이 아님 · 동의 없이 다음 단계.
    """
    step = str(payload.get("step") or "consent")
    if step not in STEPS:
        raise HTTPException(400, f"모르는 단계: {step} ({' → '.join(STEPS)})")
    answers_raw = payload.get("answers")
    answers = cast("dict[str, Any]", answers_raw) if isinstance(answers_raw, dict) else {}
    version = payload.get("consent_version")
    who = await _who(request)
    if who is None or who.role is Role.GUEST:
        # 게스트 — 문장 버전만 검사하고 저장하지 않는다 (공유 계정).
        if version is not None and not consent_is_current(version):
            raise HTTPException(
                400, f"동의 문구가 바뀌었다 — 지금 버전 {DISCLAIMER_VERSION} 을 다시 읽는다"
            )
        return {"persisted": False, "draft": {"step": step, "answers": answers}}
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session, session.begin():
        row = await session.get(AssistantDraft, who.email)
        consented = row is not None and row.consent_version is not None
        if version is not None and not consented:
            if not consent_is_current(version):
                raise HTTPException(
                    400, f"동의 문구가 바뀌었다 — 지금 버전 {DISCLAIMER_VERSION} 을 다시 읽는다"
                )
            consented = True
        if step != "consent" and not consented:
            raise HTTPException(400, "동의 없이는 다음 단계가 없다")
        now = datetime.now(UTC)
        if row is None:
            row = AssistantDraft(email=who.email, step=step, answers=answers)
            session.add(row)
        else:
            row.step = step
            row.answers = answers
        if version is not None and row.consent_version is None:
            row.consent_version = DISCLAIMER_VERSION
            row.consent_at = now
            session.add(
                EventLog(
                    trace_id=get_trace_id() or new_trace_id(),
                    actor=who.email,
                    module="apps.api.assistant",
                    level=LogLevel.INFO,
                    event_type="consent_given",
                    payload_json={
                        "email": who.email,
                        "version": DISCLAIMER_VERSION,
                        "text": DISCLAIMER_TEXT,
                        "at": now.isoformat(),
                    },
                )
            )
        await session.flush()
        # ⚠️ 새 행의 `updated_at` 은 서버 기본값이라 flush 뒤에도 파이썬 객체엔 None 이다 —
        #    그대로 직렬화하면 500 (2026-09-10 사용자 신고 · 자본 단계). refresh 로 읽어 온다.
        await session.refresh(row)
        body = _draft_json(row)
    return {"persisted": True, "draft": body}


def _market_for(group: str) -> Market | None:
    """갈래의 첫 라이브 시장 — 펀드가 나갈 곳."""
    wanted = _GROUP_OF.get(group)
    for name in walkforward._live_markets():  # pyright: ignore[reportPrivateUsage]
        market = Market(name)
        if wanted is not None and MarketGroup.of(market) is wanted:
            return market
    return None


def _candidates(who: Caller | None, group: str) -> list[dict[str, Any]]:
    """그 갈래에서 고를 수 있는(listed · view) 매매법 + 저장소 실측 + 창."""
    wanted = _GROUP_OF.get(group)
    out: list[dict[str, Any]] = []
    for book in load_playbooks():
        if not book.listed or wanted not in book.market_groups:
            continue
        if who is not None and not who.playbook(book.playbook_id).view:
            continue
        row: dict[str, Any] = {
            "id": book.playbook_id,
            "label": book.label or book.attribution,
            "leverage": None if book.leverage is None else float(book.leverage),
            "recommended": book.recommended,
            "backtest_note": book.backtest_note,
            "store": None,
        }
        if book.playbook_id in ev.BACKTESTS:
            try:
                store = ev._backtest_store(book.playbook_id)  # pyright: ignore[reportPrivateUsage]
            except HTTPException:
                store = None
            if store is not None:
                head = ec.bt_summary(store)
                ts, values = ec.bt_equity(store)
                trades = ec.bt_trades(store)
                windows: dict[str, Any] = {}
                for key, days in WINDOWS.items():
                    got = window_stats(ts, values, trades, days=days)
                    windows[key] = None if got is None else asdict(got)
                summary: dict[str, Any] = {
                    "years": head.get("years"),
                    "total_pct": head.get("total_pct"),
                    "cagr_pct": head.get("cagr_pct"),
                    "mdd_pct": head.get("mdd_pct"),
                    "calmar": head.get("calmar"),
                    "trades_count": head.get("trades_count"),
                    "liquidations": head.get("liquidations"),
                    "underwater_pct": head.get("underwater_pct"),
                    "risk_tier": head.get("risk_tier"),
                    "risk_tier_label": head.get("risk_tier_label"),
                    "symbols": head.get("symbols"),
                    "frame": head.get("frame"),
                    "windows": windows,
                }
                # T230 — 그 매매법의 백테스트 권한이 없으면 손익을 가린다 (근거 화면과 같은 규칙).
                if who is not None and not who.playbook(book.playbook_id).backtest:
                    summary = redact_pnl(summary)
                row["store"] = summary
        out.append(row)
    return out


@router.get("/preview")
async def preview(request: Request, group: str = "coin", tier: str = "balanced") -> dict[str, Any]:
    """성향에 맞는 매매법 후보와 과거 창 실측.

    Args:
        request: 요청.
        group: 갈래 (`coin` · `domestic` · `foreign`).
        tier: 성향 (`safe` · `balanced` · `aggressive`).

    Returns:
        `{group, tier, market, candidates: [...], chosen, windows, note}` — `chosen` 은 성향
        규칙으로 고른 id.

    Raises:
        HTTPException: 400 모르는 갈래/성향.
    """
    return preview_for(await _who(request), group, tier)


def preview_for(who: Caller | None, group: str, tier: str) -> dict[str, Any]:
    """`preview` 의 몸통 — 채팅 도구(`recommend_by_budget`)가 요청 없이 부른다 (T248 3차).

    Args:
        who: 호출자. None 이면 게스트(권한 없는 매매법의 손익은 가려진다).
        group: 갈래.
        tier: 성향.

    Returns:
        `preview` 와 같은 모양.

    Raises:
        HTTPException: 400 모르는 갈래/성향.
    """
    if group not in GROUPS:
        raise HTTPException(400, f"모르는 갈래: {group}")
    if tier not in TIER_LABELS:
        raise HTTPException(400, f"모르는 성향: {tier}")
    chosen_tier: Tier = tier
    rows = _candidates(who, group)
    picked = pick_default(
        chosen_tier,
        [
            {
                "id": r["id"],
                **{
                    k: r["store"].get(k)
                    for k in ("total_pct", "calmar", "mdd_pct", "underwater_pct")
                },
            }
            for r in rows
            if isinstance(r.get("store"), dict)
        ],
    )
    market = _market_for(group)
    return {
        "group": group,
        "group_label": GROUP_LABELS.get(group, group),
        "tier": tier,
        "tier_label": TIER_LABELS[chosen_tier],
        "market": None if market is None else market.value,
        "candidates": rows,
        "chosen": picked,
        "windows": list(WINDOWS),
        "note": (
            "숫자는 세션 엔진 저장소의 과거 창 실측이다(λ=선언 배율 · MDD 병기). 예상이 아니다."
        ),
    }


@router.post("/create")
async def create(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """초안대로 펀드를 만든다 — 기존 펀드 API 를 그대로 부른다(관문 T230 · T242 포함).

    Args:
        request: 요청.
        payload: `{answers?, label?}` — 게스트(초안 없음)는 답을 몸에 담아 보낸다.

    Returns:
        펀드 현황 (`/rebalancer` 와 같은 모양) + `draft`.

    Raises:
        HTTPException: 400 동의·갈래·매매법·자본이 빠짐 · 그 갈래에 라이브 시장 없음.
    """
    who = await _who(request)
    answers_raw = payload.get("answers")
    answers = cast("dict[str, Any]", answers_raw) if isinstance(answers_raw, dict) else {}
    row: AssistantDraft | None = None
    if who is not None and who.role is not Role.GUEST:
        factory = auth._store()  # pyright: ignore[reportPrivateUsage]
        async with factory() as session:
            row = await session.get(AssistantDraft, who.email)
        if row is None or row.consent_version is None:
            raise HTTPException(400, "동의가 없다 — 위저드 첫 단계부터")
        answers = {**dict(row.answers), **answers}
    elif not consent_is_current(payload.get("consent_version")):
        raise HTTPException(400, "동의가 없다 — 위저드 첫 단계부터")
    group = str(answers.get("group") or "")
    playbook = str(answers.get("playbook") or "")
    capital = str(answers.get("capital") or "0")
    if group not in GROUPS or not playbook:
        raise HTTPException(400, "갈래와 매매법을 골라야 한다")
    market = _market_for(group)
    if market is None:
        raise HTTPException(400, f"{GROUP_LABELS.get(group, group)} 갈래에 지금 열린 시장이 없다")
    members, missing = rebalancer._default_basket(market.value)  # pyright: ignore[reportPrivateUsage]
    body = {
        "label": str(payload.get("label") or answers.get("label") or "어시스턴트 펀드"),
        "total_cash": capital,
        "playbook": playbook,
        "market": market.value,
        "members": members,
    }
    made = await rebalancer.create(request, body)
    fund_id = str(made.get("fund_id") or "")
    if row is not None and who is not None:
        factory = auth._store()  # pyright: ignore[reportPrivateUsage]
        async with factory() as session, session.begin():
            kept = await session.get(AssistantDraft, who.email)
            if kept is not None:
                kept.fund_id = fund_id
                kept.step = "done"
                kept.answers = answers
    _logger.info(
        "assistant_fund_created",
        payload={
            "fund_id": fund_id,
            "group": group,
            "playbook": playbook,
            "market": market.value,
            "missing": missing,
        },
    )
    made["draft"] = {"step": "done", "answers": answers, "fund_id": fund_id}
    return made


__all__ = ["STEPS", "router"]
