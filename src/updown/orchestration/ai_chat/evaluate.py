"""채팅 시험 엔진 — 질문 묶음을 실제 루프에 넣고 **도구가 전부 불리는지** 잰다 (T258).

시험 사례는 도구마다 하나 이상이다(추천 질문과 같은 문장). 판정은 순수(`judge`)하다: 기대한 도구가
불렸나 · 그 도구가 성공했나 · 모델이 답을 냈나 · (있다면) 대시보드를 냈나. 결과는 AI 리포트가
보여 주고 `event_logs.ai_chat_eval` 에 남는다.

⛔ 여기서 답의 "질" 을 사람 눈으로 채점하지 않는다(규칙 #11). 재는 것은 도구 호출 · 성공 · 지연 ·
토큰 · 근거 없는 대시보드 칸 수뿐이다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from updown.llm.port import ChatClient
from updown.orchestration.ai_chat.agent import ChatResult, run_chat
from updown.orchestration.ai_chat.tools import ToolContext


@dataclass(frozen=True, slots=True)
class EvalCase:
    """시험 사례 하나.

    Attributes:
        name: 짧은 이름.
        question: 사용자 질문 그대로.
        expect_tools: 이 중 **하나라도** 불려야 한다.
        expect_dashboard: 답에 대시보드가 있어야 하나 (규칙 8).
    """

    name: str
    question: str
    expect_tools: tuple[str, ...]
    expect_dashboard: bool = False


CASES: tuple[EvalCase, ...] = (
    EvalCase("symbol_resolve", "테슬라 종목 코드가 뭐야?", ("symbol_resolve",)),
    EvalCase("market_view", "비트코인 지금 추세 어때?", ("market_view",)),
    EvalCase("valuation", "애플 지금 저렴해, 비싸?", ("valuation",)),
    EvalCase("positions", "내 포지션 몇 % 이득이야?", ("positions",)),
    EvalCase("extremes", "엔비디아 고점 근처야?", ("extremes",)),
    EvalCase("base_rate", "엔비디아 지금 자리에서 오를 확률 얼마나 돼?", ("base_rate",)),
    EvalCase(
        "playbook_expectation",
        "sample_ma_cross 매매법 과거 성과 알려줘",
        ("playbook_expectation",),
    ),
    EvalCase("propose_order", "NVDA 224 에 사고 217 손절, 234 목표로 제안해줘", ("propose_order",)),
    EvalCase(
        "recommend_by_budget",
        "200만원으로 미국주식 시작하려는데 뭐가 좋아?",
        ("recommend_by_budget",),
    ),
    EvalCase("portfolio_exposure", "내 비중에 쏠림 있어?", ("portfolio_exposure",)),
    EvalCase("trade_journal", "AI 매매일지 보여줘", ("trade_journal",)),
    EvalCase("screen", "미국주식 저평가 순위 상위 10개 보여줘", ("screen",), expect_dashboard=True),
    EvalCase("macro_view", "지금 공포지수(VIX) 얼마야? 환율이랑 미국 금리도", ("macro_view",)),
    EvalCase("profile_wizard", "투자 처음인데 성향 진단부터 도와줘", ("profile_wizard",)),
    EvalCase(
        "buy_question",
        "구글 주식 지금 살만 해?",
        ("market_view", "extremes", "valuation"),
        expect_dashboard=True,
    ),
    EvalCase(
        "composite",
        "200만원으로 미국주식 시작하려는데 뭐가 좋아? 그리고 내 비중도 봐줘",
        ("recommend_by_budget", "portfolio_exposure"),
        expect_dashboard=True,
    ),
)
"""도구마다 하나 + 합성 하나. 도구를 더하면 여기도 더한다 — `test_ai_chat_eval` 이 빠짐을 잡는다."""


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """사례 하나의 판정.

    Attributes:
        name: 사례 이름.
        question: 질문.
        expect_tools: 기대한 도구들.
        called: 실제로 불린 도구들 (순서대로).
        hit: 기대 도구 중 하나가 불렸나.
        tools_ok: 불린 도구가 전부 성공했나 (하나도 안 불렸으면 참).
        answered: 모델 실패 없이 답이 있나.
        dashboard: 대시보드가 있나.
        dashboard_missing: 대시보드의 근거 없는 칸 수.
        passed: hit · tools_ok · answered (· expect_dashboard 면 dashboard) 전부.
        ms: 걸린 시간.
        rounds: 모델 왕복.
        tokens: 입력+출력 토큰.
        failure: 모델 실패 사유.
        excerpt: 답 앞부분.
    """

    name: str
    question: str
    expect_tools: tuple[str, ...]
    called: tuple[str, ...]
    hit: bool
    tools_ok: bool
    answered: bool
    dashboard: bool
    dashboard_missing: int
    passed: bool
    ms: int
    rounds: int
    tokens: int
    failure: str | None
    excerpt: str

    def as_json(self) -> dict[str, Any]:
        """저장·화면 모양.

        Returns:
            필드 그대로의 dict.
        """
        return {
            "name": self.name,
            "question": self.question,
            "expect_tools": list(self.expect_tools),
            "called": list(self.called),
            "hit": self.hit,
            "tools_ok": self.tools_ok,
            "answered": self.answered,
            "dashboard": self.dashboard,
            "dashboard_missing": self.dashboard_missing,
            "passed": self.passed,
            "ms": self.ms,
            "rounds": self.rounds,
            "tokens": self.tokens,
            "failure": self.failure,
            "excerpt": self.excerpt,
        }


def judge(case: EvalCase, result: ChatResult, ms: int) -> CaseOutcome:
    """결과 → 판정 (순수).

    Args:
        case: 사례.
        result: 루프 결과.
        ms: 걸린 시간.

    Returns:
        판정.
    """
    called = tuple(e.name for e in result.tool_events)
    hit = any(name in case.expect_tools for name in called)
    tools_ok = (
        all(e.ok for e in result.tool_events if e.name in case.expect_tools) if called else True
    )
    answered = result.failure is None and bool(result.text.strip())
    dashboard = result.dashboard is not None and bool(result.dashboard.get("blocks"))
    passed = hit and tools_ok and answered and (dashboard or not case.expect_dashboard)
    return CaseOutcome(
        name=case.name,
        question=case.question,
        expect_tools=case.expect_tools,
        called=called,
        hit=hit,
        tools_ok=tools_ok,
        answered=answered,
        dashboard=dashboard,
        dashboard_missing=len(result.dashboard_missing),
        passed=passed,
        ms=ms,
        rounds=result.rounds,
        tokens=result.prompt_tokens + result.completion_tokens,
        failure=result.failure,
        excerpt=result.text[:240],
    )


@dataclass(slots=True)
class EvalReport:
    """한 번의 시험 묶음.

    Attributes:
        model: 모델.
        prompt_version: 프롬프트 버전.
        cases: 판정들.
        started_at: 시작 ISO 시각.
        ms: 전체 걸린 시간.
    """

    model: str
    prompt_version: str
    cases: list[CaseOutcome] = field(default_factory=list[CaseOutcome])
    started_at: str = ""
    ms: int = 0

    def coverage(self, tool_names: Sequence[str]) -> dict[str, str]:
        """도구별 상태.

        `passed`(불리고 성공) · `called`(불렸지만 실패/미완) · `missed`(안 불림) ·
        `untested`(사례 없음).

        Args:
            tool_names: 등록된 도구 이름들.

        Returns:
            도구 → 상태.
        """
        out: dict[str, str] = {}
        for name in tool_names:
            relevant = [c for c in self.cases if name in c.expect_tools]
            if not relevant:
                called_anywhere = any(name in c.called for c in self.cases)
                out[name] = "called" if called_anywhere else "untested"
                continue
            if any(c.passed and name in c.called for c in relevant):
                out[name] = "passed"
            elif any(name in c.called for c in relevant):
                out[name] = "called"
            else:
                out[name] = "missed"
        return out

    def as_json(self, tool_names: Sequence[str]) -> dict[str, Any]:
        """저장·화면 모양.

        Args:
            tool_names: 등록된 도구 이름들 (커버리지용).

        Returns:
            `{model, prompt_version, started_at, ms, n, passed, coverage, cases}`.
        """
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "started_at": self.started_at,
            "ms": self.ms,
            "n": len(self.cases),
            "passed": sum(1 for c in self.cases if c.passed),
            "coverage": self.coverage(tool_names),
            "cases": [c.as_json() for c in self.cases],
        }


async def run_eval(
    cases: Sequence[EvalCase],
    *,
    client: ChatClient,
    model: str,
    ctx_factory: Callable[[], ToolContext],
    prompt_version: str,
    timeout_seconds: float = 60.0,
    fallbacks: Sequence[str] = (),
    report: Callable[[str], None] | None = None,
) -> EvalReport:
    """사례들을 차례로 돈다 — 사례마다 새 컨텍스트(도구 결과가 섞이지 않게) · 빈 이력.

    Args:
        cases: 사례들.
        client: 모델 포트.
        model: 모델 id.
        ctx_factory: 사례마다 새 `ToolContext`.
        prompt_version: 프롬프트 버전(기록용).
        timeout_seconds: 모델 호출 타임아웃.
        fallbacks: 폴백 모델들.
        report: 진행 문장 콜백.

    Returns:
        시험 묶음.
    """
    made = EvalReport(model=model, prompt_version=prompt_version)
    made.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    started = time.perf_counter()
    for i, case in enumerate(cases, 1):
        if report is not None:
            report(f"[{i}/{len(cases)}] {case.name} — {case.question[:40]}")
        t0 = time.perf_counter()
        result = await run_chat(
            [],
            case.question,
            client=client,
            model=model,
            ctx=ctx_factory(),
            timeout_seconds=timeout_seconds,
            fallbacks=fallbacks,
        )
        outcome = judge(case, result, int((time.perf_counter() - t0) * 1000))
        made.cases.append(outcome)
        if report is not None:
            report(
                f"  → {'통과' if outcome.passed else '실패'} · 도구 {list(outcome.called)} · "
                f"{outcome.ms}ms{' · ' + outcome.failure if outcome.failure else ''}"
            )
    made.ms = int((time.perf_counter() - started) * 1000)
    return made


__all__ = ["CASES", "CaseOutcome", "EvalCase", "EvalReport", "judge", "run_eval"]
