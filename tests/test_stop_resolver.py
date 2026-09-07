"""손절 확정 검증 (P1-7 · spec §5.1, §6.1 · 절대 규칙 #3·#4·#5·#10).

네 가지를 지킨다:

1. **순수 함수** — 같은 인자면 같은 값. `k` 는 인자이지 설정 조회가 아니다
   (이 성질이 P1-8 총 소요를 2.3시간과 16.7시간으로 가른다)
2. **축 G 해석** — "더 보수적"이 무엇인지 스펙이 정의하지 않아 후보가 셋이다
3. **축 ②** — 상위 봉 후보가 없으면 **대체하지 않고 예외**다. 대체하면 두 셀이 같은
   값을 재게 되어 실험이 무의미해진다
4. **롱 온리** — 확정 손절이 진입가 이상이면 예외 (절대 규칙 #10)
"""

from decimal import Decimal

import pytest

from updown.common.domain.instrument import Timeframe
from updown.common.domain.setup import (
    EntryLeg,
    EntryTrigger,
    StopCandidate,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)
from updown.decision.risk.stop_resolver import (
    StopBasis,
    StopInterpretation,
    StopResolutionError,
    StopResolver,
    StructuralAtrStopResolver,
)

ENTRY = Decimal(100)


def setup_with(*candidates: StopCandidate, entry: Decimal = ENTRY) -> TradeSetup:
    """후보 목록만 지정한 셋업.

    Note:
        `stop_loss` 는 첫 후보로 둔다 — `__post_init__` 이 "`stop_loss` ∈ 후보"를
        강제하기 때문이며, 확정 로직은 이 값을 **쓰지 않아야** 한다 (확정 2).
    """
    return TradeSetup(
        setup_type="TEST",
        rule_version="test@1.0",
        entry_trigger=EntryTrigger.TOUCH,
        entry_plan=(EntryLeg(price=entry, ratio=Decimal(1)),),
        avg_entry=entry,
        stop_loss=candidates[0].price,
        stop_candidates=candidates,
        tp_ladder=(TakeProfitStep(price=entry + Decimal(5), ratio=Decimal(1), then=None),),
        stop_policy_hint=StopPolicyHint(never_lower=True, trailing=None),
        rr_ratio=Decimal(2),
        confidence=0.5,
        evidence=(),
    )


def candidate(price: str, timeframe: Timeframe, source: str = "test") -> StopCandidate:
    return StopCandidate(price=Decimal(price), timeframe=timeframe, source=source)


# ---------------------------------------------------------------------------
# 1. 계약 — 순수 함수 · k 는 인자
# ---------------------------------------------------------------------------


def test_implementation_satisfies_the_protocol() -> None:
    """P1-8 재생 경로가 이 형태를 기대한다 — 벗어나면 타입 검사가 잡는다."""
    resolver: StopResolver = StructuralAtrStopResolver()
    assert callable(resolver.resolve_stop)


def test_same_arguments_give_the_same_value() -> None:
    """절대 규칙 #5. 재현 불가능하면 §4.14 성과 귀속이 무너진다."""
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97", Timeframe.M15))
    first = resolver.resolve_stop(setup, Decimal(1), Decimal("1.5"), StopBasis.DISCOVERY_BAR)
    second = resolver.resolve_stop(setup, Decimal(1), Decimal("1.5"), StopBasis.DISCOVERY_BAR)
    assert first == second


def test_k_changes_the_result_without_touching_configuration() -> None:
    """**성능 계약의 핵심.** 한 번의 탐지로 k 세 후보를 전부 재생할 수 있어야 한다."""
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("99.9", Timeframe.M15))  # 구조가 얕아 ATR 이 이긴다
    stops = {
        k: resolver.resolve_stop(setup, Decimal(1), Decimal(k), StopBasis.DISCOVERY_BAR)
        for k in ("1.5", "2.0", "2.5")
    }
    assert stops["1.5"] == Decimal("98.5")
    assert stops["2.0"] == Decimal("98.0")
    assert stops["2.5"] == Decimal("97.5")


def test_resolver_reads_no_configuration_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정을 읽으면 k 를 바꿀 때마다 탐지부터 다시 돌아야 한다.

    `config/risk.yml` 로더가 불리면 즉시 실패시킨다.
    """
    import updown.decision.risk.policy as policy

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("stop_resolver 가 설정을 읽었다 — k 는 인자여야 한다")

    monkeypatch.setattr(policy, "load_settings", boom)
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97", Timeframe.M15))
    assert resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)


# ---------------------------------------------------------------------------
# 2. 축 G — "더 보수적인 쪽"의 해석
# ---------------------------------------------------------------------------


def test_g1_farther_takes_the_wider_stop() -> None:
    """G1 잠정 기본값 — 진입가에서 **더 먼** 쪽.

    구조 손절 거리 3 vs ATR 거리 2 → 더 먼 3 을 쓴다 → 손절가 97.
    """
    resolver = StructuralAtrStopResolver(StopInterpretation.FARTHER)
    setup = setup_with(candidate("97", Timeframe.M15))
    stop = resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)
    assert stop == Decimal(97)


def test_g1_uses_atr_when_structure_is_shallower() -> None:
    """구조가 얕으면 ATR 이 이긴다 — 좁은 손절의 실패(1R = 비용의 1/15)를 막는 방향이다."""
    resolver = StructuralAtrStopResolver(StopInterpretation.FARTHER)
    setup = setup_with(candidate("99.5", Timeframe.M15))  # 거리 0.5
    stop = resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)
    assert stop == Decimal(98)  # ATR 거리 2 가 이긴다


def test_g2_nearer_takes_the_tighter_stop() -> None:
    resolver = StructuralAtrStopResolver(StopInterpretation.NEARER)
    setup = setup_with(candidate("97", Timeframe.M15))
    stop = resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)
    assert stop == Decimal(98)  # 더 가까운 ATR 거리 2


def test_g3_ignores_structure_entirely() -> None:
    resolver = StructuralAtrStopResolver(StopInterpretation.ATR_ONLY)
    setup = setup_with(candidate("90", Timeframe.M15))  # 아주 깊은 구조
    stop = resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)
    assert stop == Decimal(98)


def test_default_interpretation_is_the_declared_provisional_one() -> None:
    """잠정 기본값이 코드에도 G1 이어야 문서와 어긋나지 않는다."""
    assert StructuralAtrStopResolver().interpretation is StopInterpretation.FARTHER


def test_interpretation_is_constructor_not_argument() -> None:
    """축 G 를 인자로 넣으면 성능 계약(시그니처)이 흔들린다 — 생성자로 받는다."""
    import inspect

    params = list(inspect.signature(StructuralAtrStopResolver.resolve_stop).parameters)
    assert params == ["self", "setup", "atr", "k", "basis"]


# ---------------------------------------------------------------------------
# 3. 축 ② — 발견 봉 vs 상위 봉
# ---------------------------------------------------------------------------


def test_discovery_bar_is_the_fastest_timeframe_candidate() -> None:
    """발견 봉 = 후보 중 가장 빠른 시간축. `stop_loss`(분석의 선택)를 따르지 않는다."""
    resolver = StructuralAtrStopResolver()
    setup = setup_with(
        candidate("95", Timeframe.H1),  # stop_loss 로 지정되지만 발견 봉이 아니다
        candidate("97", Timeframe.M15),
    )
    stop = resolver.resolve_stop(setup, Decimal(1), Decimal("0.5"), StopBasis.DISCOVERY_BAR)
    assert stop == Decimal(97)


def test_higher_bar_uses_the_highest_timeframe() -> None:
    """축 ②의 의도가 "손절폭을 넓히는 다른 경로"이므로 중간을 고르면 의도가 흐려진다."""
    resolver = StructuralAtrStopResolver()
    setup = setup_with(
        candidate("97", Timeframe.M15),
        candidate("95", Timeframe.H1),
        candidate("93", Timeframe.H4),
    )
    stop = resolver.resolve_stop(setup, Decimal(1), Decimal("0.5"), StopBasis.HIGHER_BAR)
    assert stop == Decimal(93)


def test_missing_higher_bar_raises_instead_of_falling_back() -> None:
    """**대체하면 축 ②의 두 셀이 같은 값을 재게 된다** — 실험이 무의미해진다."""
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97", Timeframe.M15))
    with pytest.raises(StopResolutionError, match="상위 봉 손절 후보가 없다"):
        resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.HIGHER_BAR)


def test_string_ordering_of_timeframes_would_be_wrong() -> None:
    """`Timeframe` 은 StrEnum 이라 문자열 정렬이면 `15m < 1h < 4h < 5m` 이다.

    5m 이 가장 뒤로 가므로, 문자열로 비교했다면 발견 봉을 5m 이 아니라 15m 으로 잡는다.
    """
    assert sorted(["15m", "1h", "4h", "5m"]) == ["15m", "1h", "4h", "5m"]

    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97", Timeframe.M5), candidate("95", Timeframe.M15))
    # 발견 봉은 5m 이어야 한다 (문자열 정렬이면 15m 을 골랐을 것이다).
    stop = resolver.resolve_stop(setup, Decimal(1), Decimal("0.5"), StopBasis.DISCOVERY_BAR)
    assert stop == Decimal(97)


def test_empty_candidates_raise() -> None:
    """후보가 비었다는 것은 탐지가 구조를 안 실었다는 뜻이다 (절대 규칙 #8)."""
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97", Timeframe.M15))
    stripped = TradeSetup(
        setup_type=setup.setup_type,
        rule_version=setup.rule_version,
        entry_trigger=setup.entry_trigger,
        entry_plan=setup.entry_plan,
        avg_entry=setup.avg_entry,
        stop_loss=setup.stop_loss,
        stop_candidates=(),
        tp_ladder=setup.tp_ladder,
        stop_policy_hint=setup.stop_policy_hint,
        rr_ratio=setup.rr_ratio,
        confidence=setup.confidence,
        evidence=setup.evidence,
    )
    with pytest.raises(StopResolutionError, match="손절 후보가 비어 있다"):
        resolver.resolve_stop(stripped, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)


def test_atr_only_does_not_need_candidates_of_that_basis() -> None:
    """G3 는 구조를 안 보므로 상위 봉 후보가 없어도 성립한다."""
    resolver = StructuralAtrStopResolver(StopInterpretation.ATR_ONLY)
    setup = setup_with(candidate("97", Timeframe.M15))
    assert resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.HIGHER_BAR) == Decimal(98)


# ---------------------------------------------------------------------------
# 4. 불변식 — 롱 온리 · 입력 검증
# ---------------------------------------------------------------------------


def test_structure_at_or_above_entry_is_rejected() -> None:
    """롱 온리(절대 규칙 #10) — 진입가 위의 손절은 성립하지 않는다."""
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("101", Timeframe.M15))
    with pytest.raises(StopResolutionError, match="롱 온리"):
        resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)


@pytest.mark.parametrize("atr", [Decimal(0), Decimal(-1)])
def test_non_positive_atr_is_rejected(atr: Decimal) -> None:
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97", Timeframe.M15))
    with pytest.raises(ValueError, match="ATR 은 0 보다"):
        resolver.resolve_stop(setup, atr, Decimal(2), StopBasis.DISCOVERY_BAR)


@pytest.mark.parametrize("k", [Decimal(0), Decimal("-1.5")])
def test_non_positive_k_is_rejected(k: Decimal) -> None:
    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97", Timeframe.M15))
    with pytest.raises(ValueError, match="ATR 손절 배수"):
        resolver.resolve_stop(setup, Decimal(1), k, StopBasis.DISCOVERY_BAR)


def test_resolved_stop_is_always_below_entry() -> None:
    """어떤 해석·기준에서도 손절은 진입가 아래다."""
    setup = setup_with(candidate("97", Timeframe.M15), candidate("94", Timeframe.H1))
    for interpretation in StopInterpretation:
        resolver = StructuralAtrStopResolver(interpretation)
        for basis in StopBasis:
            if basis is StopBasis.HIGHER_BAR and interpretation is not StopInterpretation.ATR_ONLY:
                pass  # 상위 봉 후보가 있으므로 성립한다
            stop = resolver.resolve_stop(setup, Decimal(1), Decimal(2), basis)
            assert stop < setup.avg_entry


def test_decimal_precision_is_fixed_not_context_dependent() -> None:
    """호출자의 decimal 컨텍스트가 결과를 바꾸면 결정론이 환경에 의존하게 된다."""
    from decimal import Context, localcontext

    resolver = StructuralAtrStopResolver()
    setup = setup_with(candidate("97.123456789012345678901", Timeframe.M15))
    baseline = resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)
    with localcontext(Context(prec=6)):
        narrow = resolver.resolve_stop(setup, Decimal(1), Decimal(2), StopBasis.DISCOVERY_BAR)
    assert baseline == narrow
