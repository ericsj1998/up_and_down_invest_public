"""LLM 응답 파서 — **어긴 것을 어겼다고 하는가**가 전부다.

소형 모델은 스키마를 자주 어긴다. 그것을 고쳐서 통과시키면 "이 모델은 형식을 못 지킨다"
는 사실이 사라지는데, 그게 비교의 핵심 정보다 (§5-4). 그래서 거부 테스트가 대부분이다.
"""

import json
from decimal import Decimal

import pytest

from updown.llm.schema import SchemaError, TrendCall, parse_analysis

ENTRY = Decimal("100")


def payload(**overrides: object) -> str:
    """정상 응답 하나를 만든다.

    Args:
        overrides: 덮어쓸 최상위 칸.

    Returns:
        JSON 문자열.
    """
    body: dict[str, object] = {
        "trend": "UP",
        "levels": [{"kind": "support", "price": "95", "label": "단기 지지"}],
        "zones": [
            {"kind": "demand", "low": "90", "high": "94", "timeframe": "4h", "label": "@4h OB"}
        ],
        "trendlines": [
            {
                "kind": "support",
                "from": {"ts": "2026-08-01T00:00:00Z", "price": "88"},
                "to": {"ts": "2026-08-09T00:00:00Z", "price": "96"},
            }
        ],
        "plan": {
            "stop_loss": "94",
            "take_profit_half": "108",
            "take_profit_full": "120",
            "conviction_pct": 72,
        },
        "reasoning": "4시간 수요 구간에서 반등",
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def test_valid_response_parses() -> None:
    """정상 응답은 그대로 통과한다."""
    analysis = parse_analysis(payload(), ENTRY)

    assert analysis.trend is TrendCall.UP
    assert analysis.plan.stop_loss == Decimal("94")
    assert analysis.zones[0].label == "@4h OB"
    assert analysis.trendlines[0].start.price == Decimal("88")


def test_json_inside_a_code_fence_is_accepted() -> None:
    """```json 펜스와 잡담은 벗긴다 — 내용은 고치지 않는다."""
    wrapped = f"분석 결과입니다.\n```json\n{payload()}\n```\n감사합니다."

    assert parse_analysis(wrapped, ENTRY).trend is TrendCall.UP


def test_stop_above_entry_is_rejected() -> None:
    """🔴 손절이 진입 위면 롱 온리 전제 위반이다 (절대 규칙 #10)."""
    broken = payload(
        plan={
            "stop_loss": "105",
            "take_profit_half": "108",
            "take_profit_full": "120",
            "conviction_pct": 50,
        }
    )
    with pytest.raises(SchemaError, match="롱 온리"):
        parse_analysis(broken, ENTRY)


def test_target_below_entry_is_rejected() -> None:
    """🔴 익절이 진입 이하면 **진입 즉시 익절**로 세어져 승률이 부풀려진다 (§1-0s)."""
    broken = payload(
        plan={
            "stop_loss": "94",
            "take_profit_half": "99",
            "take_profit_full": "120",
            "conviction_pct": 50,
        }
    )
    with pytest.raises(SchemaError, match="진입"):
        parse_analysis(broken, ENTRY)


def test_full_target_below_half_is_rejected() -> None:
    """전체 익절이 절반 익절보다 낮으면 사다리가 뒤집힌 것이다."""
    broken = payload(
        plan={
            "stop_loss": "94",
            "take_profit_half": "120",
            "take_profit_full": "108",
            "conviction_pct": 50,
        }
    )
    with pytest.raises(SchemaError):
        parse_analysis(broken, ENTRY)


def test_naive_timestamp_is_rejected() -> None:
    """🔴 시간대 없는 시각을 UTC 로 가정하지 않는다 — 그만큼 선이 밀린다 (절대 규칙 #7)."""
    broken = payload(
        trendlines=[
            {
                "kind": "support",
                "from": {"ts": "2026-08-01T00:00:00", "price": "88"},
                "to": {"ts": "2026-08-09T00:00:00Z", "price": "96"},
            }
        ]
    )
    with pytest.raises(SchemaError, match="시간대"):
        parse_analysis(broken, ENTRY)


def test_conviction_out_of_range_is_rejected() -> None:
    """추천도는 0~100 이다 — 벗어난 값을 잘라 맞추지 않는다."""
    broken = payload(
        plan={
            "stop_loss": "94",
            "take_profit_half": "108",
            "take_profit_full": "120",
            "conviction_pct": 250,
        }
    )
    with pytest.raises(SchemaError, match="0~100"):
        parse_analysis(broken, ENTRY)


def test_unknown_trend_is_rejected() -> None:
    """UP/DOWN/RANGE 밖의 값을 임의로 매핑하지 않는다."""
    with pytest.raises(SchemaError, match="trend"):
        parse_analysis(payload(trend="BULLISH"), ENTRY)


def test_missing_plan_is_rejected() -> None:
    """계획이 없는 분석은 "숫자 없는 매수 추천"이다 (§4.3.1)."""
    body = json.loads(payload())
    del body["plan"]

    with pytest.raises(SchemaError, match="plan"):
        parse_analysis(json.dumps(body), ENTRY)


def test_prose_only_response_is_rejected() -> None:
    """JSON 이 아예 없으면 거부한다 — 문장을 해석해 값을 지어내지 않는다."""
    with pytest.raises(SchemaError, match="JSON"):
        parse_analysis("상승 추세로 보이며 100원 부근 매수를 추천합니다.", ENTRY)


def test_inverted_zone_is_rejected() -> None:
    """구간의 low 가 high 이상이면 그릴 수 없는 면이다."""
    broken = payload(zones=[{"kind": "demand", "low": "94", "high": "90", "timeframe": "4h"}])

    with pytest.raises(SchemaError, match="low"):
        parse_analysis(broken, ENTRY)


def test_optional_drawings_may_be_absent() -> None:
    """선·면을 못 찾는 것은 **정상**이다 — 계획만 있으면 유효한 분석이다."""
    body = json.loads(payload())
    for key in ("levels", "zones", "trendlines"):
        del body[key]

    analysis = parse_analysis(json.dumps(body), ENTRY)

    assert analysis.levels == ()
    assert analysis.plan.conviction_pct == 72


def test_numeric_prices_are_accepted_without_precision_loss() -> None:
    """모델이 숫자로 줘도 받되 **Decimal 로** 담는다 — float 경유는 값을 바꾼다."""
    body = json.loads(payload())
    body["plan"]["stop_loss"] = 94.1

    analysis = parse_analysis(json.dumps(body), ENTRY)

    assert analysis.plan.stop_loss == Decimal("94.1")
