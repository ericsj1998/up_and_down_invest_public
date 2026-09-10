"""위저드-인-채팅 — 온보딩 단계를 **카드**로 (T271 · 사용자 2026-09-10).

순수 모듈이다. 단계 · 막는 이유 · 답 합치기 · 카드 모양만 안다. 저장(`assistant_drafts`) · 미리보기
(`preview_for`) · 펀드 생성은 API 층이 한다 — 여기 값은 전부 API 가 넘겨 준 것이다.

## 왜 카드인가 (자유 텍스트가 아니라)

- **동의**는 어떤 문구 버전에 동의했는지가 감사 기록이다 — 채팅 "응" 으로 받으면 버전이 흐려진다.
- **성향**은 T249 채점의 축이다 — 3택으로 고정해야 표본이 갈리지 않는다.
- **펀드 만들기**는 주문이다 — 단추 하나 · 재인증 · 사람 확인(규칙 #2).

모델은 카드를 **띄우기만** 한다(`profile_wizard` 도구). 단계를 넘기는 것은 카드의 단추이고 그 요청은
모델을 거치지 않는다(`POST /ai/chat/threads/{id}/wizard`) — 빠르고 결정론적이다.
"""

from __future__ import annotations

from typing import Any, cast

STEPS: tuple[str, ...] = ("consent", "capital", "profile", "setup", "review", "done")
"""단계 순서 — `apps/api/assistant.STEPS` 와 같다(둘 다 이 값을 써야 한다)."""

GROUP_OPTIONS: tuple[tuple[str, str], ...] = (
    ("coin", "코인"),
    ("domestic", "국내주식"),
    ("foreign", "미국주식"),
)
TIER_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("aggressive", "공격적 투자", "손익이 가장 큰 매매법을 기본으로"),
    ("balanced", "균형 투자", "MDD 대비 손익(Calmar)이 가장 좋은 매매법을 기본으로"),
    ("safe", "안전 투자", "MDD·수면 아래가 가장 작은 매매법을 기본으로"),
)
CADENCE_OPTIONS: tuple[tuple[str, str], ...] = (("week", "주"), ("month", "월"), ("year", "년"))
ANSWER_KEYS: frozenset[str] = frozenset(
    {"capital", "contribution", "cadence", "years", "group", "tier", "playbook", "label"}
)
NUMBER_KEYS: frozenset[str] = frozenset({"capital", "contribution", "years"})
STEP_TITLE: dict[str, str] = {
    "consent": "동의",
    "capital": "자본",
    "profile": "성향",
    "setup": "매매 설정",
    "review": "검토",
    "done": "완료",
}


def next_step(step: str) -> str:
    """다음 단계 — 끝이면 그대로.

    Args:
        step: 지금 단계.

    Returns:
        다음 단계 이름.
    """
    index = STEPS.index(step) if step in STEPS else 0
    return STEPS[min(index + 1, len(STEPS) - 1)]


def prev_step(step: str) -> str:
    """이전 단계 — 처음이면 그대로.

    Args:
        step: 지금 단계.

    Returns:
        이전 단계 이름.
    """
    index = STEPS.index(step) if step in STEPS else 0
    return STEPS[max(index - 1, 0)]


def merge_answers(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """답을 합친다 — 아는 키만, 숫자는 숫자로. 모르는 키는 버린다.

    모델·화면이 지어낸 키가 저장되지 않게 하기 위해서다.

    Args:
        old: 저장돼 있던 답.
        new: 이번에 온 답.

    Returns:
        합친 답.
    """
    out: dict[str, Any] = {k: v for k, v in old.items() if k in ANSWER_KEYS}
    for key, value in new.items():
        if key not in ANSWER_KEYS or value is None or value == "":
            continue
        if key in NUMBER_KEYS:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                continue
        else:
            out[key] = str(value)
    return out


def blockers(step: str, answers: dict[str, Any], *, consented: bool) -> list[str]:
    """이 단계에서 다음으로 갈 수 없는 이유 — 비면 갈 수 있다 (`web/src/assistant.ts` 와 같은 규칙).

    Args:
        step: 단계.
        answers: 지금까지의 답.
        consented: 동의했나.

    Returns:
        막는 이유 문장들.
    """
    out: list[str] = []
    if step == "consent" and not consented:
        out.append("동의가 필요하다")
    if step == "capital":
        capital = answers.get("capital")
        if not isinstance(capital, int | float) or capital <= 0:
            out.append("시작 금액은 0 보다 커야 한다")
    if step == "profile":
        if answers.get("group") not in {g for g, _ in GROUP_OPTIONS}:
            out.append("종목 갈래를 고른다")
        if answers.get("tier") not in {t for t, _, _ in TIER_OPTIONS}:
            out.append("성향을 고른다")
    if step == "setup" and not answers.get("playbook"):
        out.append("매매법을 고른다")
    return out


def _fmt_pct(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(cast('float', value)):+.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _candidate_options(preview: dict[str, Any] | None, chosen: str) -> list[dict[str, Any]]:
    if not preview:
        return []
    rows = cast("list[dict[str, Any]]", preview.get("candidates") or [])
    default = str(preview.get("chosen") or "")
    out: list[dict[str, Any]] = []
    for row in rows:
        store = row.get("store")
        hint = "저장소 없음"
        if isinstance(store, dict):
            s = cast("dict[str, Any]", store)
            hint = (
                f"{s.get('years') or '?'}년 실측 · 손익 {_fmt_pct(s.get('total_pct'))} · "
                f"MDD {_fmt_pct(s.get('mdd_pct'))} · 매매 {s.get('trades_count') or '?'}"
            )
        pid = str(row.get("id") or "")
        out.append(
            {
                "value": pid,
                "label": str(row.get("label") or pid),
                "hint": hint,
                "recommended": bool(row.get("recommended")),
                "default": pid == default,
                "selected": pid == (chosen or default),
            }
        )
    return out


def card_for(
    step: str,
    answers: dict[str, Any],
    *,
    consented: bool,
    disclaimer_text: str,
    disclaimer_version: str,
    preview: dict[str, Any] | None = None,
    fund_id: str | None = None,
    error: str = "",
) -> dict[str, Any]:
    """단계 하나의 카드 — 화면이 그대로 그린다.

    Args:
        step: 단계.
        answers: 지금까지의 답.
        consented: 동의했나.
        disclaimer_text: 동의 문구.
        disclaimer_version: 동의 문구 버전.
        preview: 성향에 맞는 후보(`preview_for` 모양) — `setup`·`review` 에서.
        fund_id: 만든 펀드 id — `done` 에서.
        error: 막힌 이유(있으면 카드 위에).

    Returns:
        `{kind: "wizard", step, index, total, title, text, fields, groups, options,
        summary, actions, …}`.
    """
    index = STEPS.index(step) if step in STEPS else 0
    card: dict[str, Any] = {
        "kind": "wizard",
        "step": step,
        "index": min(index + 1, len(STEPS) - 1),  # 완료는 5/5 — 6/5 로 보이지 않게
        "total": len(STEPS) - 1,
        "title": STEP_TITLE.get(step, step),
        "answers": dict(answers),
        "consented": consented,
        "error": error,
        "fields": [],
        "groups": [],
        "options": [],
        "summary": [],
        "actions": [],
    }
    back = {"action": "prev", "label": "이전"}
    if step == "consent":
        card["text"] = disclaimer_text
        card["version"] = disclaimer_version
        card["actions"] = (
            [{"action": "next", "label": "다음", "primary": True}]
            if consented
            else [{"action": "consent", "label": "읽었고 동의한다", "primary": True}]
        )
    elif step == "capital":
        card["text"] = (
            "시작 금액과 납입 계획 — 수익률은 곱하지 않는다(예상 금지). 납입 누계만 계산한다."
        )
        card["fields"] = [
            {
                "key": "capital",
                "label": "시작 금액",
                "type": "number",
                "value": answers.get("capital"),
            },
            {
                "key": "contribution",
                "label": "추가 납입(선택)",
                "type": "number",
                "value": answers.get("contribution"),
            },
            {
                "key": "cadence",
                "label": "납입 주기",
                "type": "select",
                "options": [{"value": v, "label": lbl} for v, lbl in CADENCE_OPTIONS],
                "value": answers.get("cadence") or "month",
            },
            {"key": "years", "label": "기간(년)", "type": "number", "value": answers.get("years")},
        ]
        card["actions"] = [back, {"action": "next", "label": "다음", "primary": True}]
    elif step == "profile":
        card["text"] = "어느 갈래를 어떤 성향으로 — 성향은 매매법 기본값을 고르는 규칙이다."
        card["groups"] = [
            {
                "key": "group",
                "label": "종목 갈래",
                "options": [
                    {"value": g, "label": lbl, "selected": answers.get("group") == g}
                    for g, lbl in GROUP_OPTIONS
                ],
            },
            {
                "key": "tier",
                "label": "성향",
                "options": [
                    {"value": t, "label": lbl, "hint": hint, "selected": answers.get("tier") == t}
                    for t, lbl, hint in TIER_OPTIONS
                ],
            },
        ]
        card["actions"] = [back, {"action": "next", "label": "다음", "primary": True}]
    elif step == "setup":
        note = str((preview or {}).get("note") or "숫자는 과거 창 실측이다. 예상이 아니다.")
        market = (preview or {}).get("market")
        card["text"] = f"성향에 맞는 매매법 — {note}" + (
            f" 시장 {market}." if market else " 이 갈래에 지금 열린 시장이 없다."
        )
        card["options"] = _candidate_options(preview, str(answers.get("playbook") or ""))
        card["actions"] = [back, {"action": "next", "label": "다음", "primary": True}]
    elif step == "review":
        group_label = dict(GROUP_OPTIONS).get(str(answers.get("group") or ""), "—")
        tier_label = {t: lbl for t, lbl, _ in TIER_OPTIONS}.get(str(answers.get("tier") or ""), "—")
        playbook = str(answers.get("playbook") or "—")
        for opt in _candidate_options(preview, playbook):
            if opt["value"] == playbook:
                playbook = str(opt["label"])
        market = (preview or {}).get("market")
        cadence = dict(CADENCE_OPTIONS).get(str(answers.get("cadence") or "month"), "월")
        card["summary"] = [
            f"시작 금액 {answers.get('capital') or 0:,.0f} · 추가 납입 "
            f"{answers.get('contribution') or 0:,.0f}/{cadence} · {answers.get('years') or 0:g}년",
            f"갈래 {group_label} · 성향 {tier_label}",
            f"매매법 {playbook}" + (f" · 시장 {market}" if market else ""),
        ]
        if market:
            card["text"] = (
                "이대로 펀드를 만든다 — 만들기는 사람이 누르고, 최근 인증(재인증)을 요구한다. "
                "만든 뒤 판은 콘솔에서 본다."
            )
            create = {"action": "create", "label": "펀드 만들기", "primary": True, "confirm": True}
        else:
            # ⭐ 만들기 단추가 왜 안 먹는지 말한다 (사용자 2026-09-11 "생성이 안 먹히네").
            card["text"] = (
                f"이 서버에는 {group_label} 시장이 열려 있지 않아 펀드를 만들 수 없다 — "
                "관리자가 서버 UPDOWN_MARKETS 에 시장을 더해야 한다. 코인 갈래는 지금도 된다."
            )
            create = {"action": "create", "label": "펀드 만들기", "primary": True, "disabled": True}
        card["actions"] = [back, {"action": "restart", "label": "처음부터"}, create]
    else:  # done
        card["text"] = (
            f"펀드 {fund_id} 가 만들어졌다 — 콘솔에서 판을 본다."
            if fund_id
            else "끝났다. 새로 시작하려면 아래를 누른다."
        )
        card["fund_id"] = fund_id
        card["actions"] = [{"action": "restart", "label": "새로 시작"}]
    return card


def text_for(card: dict[str, Any]) -> str:
    """카드 한 장의 한 줄 요약 — assistant 메시지 본문(대화 기록 · 모델 문맥용).

    Args:
        card: `card_for` 결과.

    Returns:
        예: `[2/5 자본] 시작 금액과 납입 계획 …`.
    """
    head = f"[{card.get('index')}/{card.get('total')} {card.get('title')}]"
    if card.get("step") == "consent":
        return f"{head} 아래 문구(버전 {card.get('version')})를 읽고 동의해 주세요."
    if card.get("step") == "review":
        return f"{head} " + " · ".join(cast("list[str]", card.get("summary") or []))
    return f"{head} {card.get('text', '')}".strip()


def action_line(action: str, step: str, answers: dict[str, Any]) -> str:
    """단추 클릭을 대화 기록에 남기는 사용자 줄 — 모델이 다음 턴에 문맥으로 읽는다.

    Args:
        action: `consent` · `next` · `prev` · `restart` · `create`.
        step: 눌렀을 때의 단계.
        answers: 그 단계에서 낸 답.

    Returns:
        예: `성향: 갈래=미국주식 · 성향=균형 투자 → 다음`.
    """
    label = STEP_TITLE.get(step, step)
    if action == "consent":
        return f"{label}: 동의함"
    if action == "create":
        return f"{label}: 펀드 만들기"
    if action == "restart":
        return "위저드 처음부터"
    if action == "prev":
        return f"{label}: 이전"
    parts: list[str] = []
    if step == "capital":
        parts.append(f"시작 금액 {answers.get('capital') or 0:,.0f}")
        if answers.get("contribution"):
            parts.append(
                f"납입 {answers.get('contribution'):,.0f}/"
                f"{dict(CADENCE_OPTIONS).get(str(answers.get('cadence') or 'month'), '월')}"
            )
        if answers.get("years"):
            parts.append(f"{answers.get('years'):g}년")
    if step == "profile":
        parts.append(f"갈래={dict(GROUP_OPTIONS).get(str(answers.get('group') or ''), '—')}")
        parts.append(
            "성향="
            + {t: lbl for t, lbl, _ in TIER_OPTIONS}.get(str(answers.get("tier") or ""), "—")
        )
    if step == "setup":
        parts.append(f"매매법={answers.get('playbook') or '—'}")
    return f"{label}: " + (" · ".join(parts) if parts else "") + " → 다음"


__all__ = [
    "ANSWER_KEYS",
    "GROUP_OPTIONS",
    "STEPS",
    "TIER_OPTIONS",
    "action_line",
    "blockers",
    "card_for",
    "merge_answers",
    "next_step",
    "prev_step",
    "text_for",
]
