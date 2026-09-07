"""구조물 골든 스냅샷 회귀 테스트 (P1-1-1, P1-1-7 · tasks.md P1-1 DoD).

## 무엇을 지키는가

tasks.md P1-1 DoD: "고정 캔들 입력 → 고정 스윙/추세선 출력 스냅샷 일치".
입력이 불변(커밋된 JSON)이므로 **스냅샷 불일치는 언제나 코드 변경을 뜻한다** (원칙 P1).

## 스냅샷 갱신 절차

의도한 변경이면 갱신한다. 의도하지 않았으면 **버그다**.

```bash
UPDOWN_UPDATE_GOLDEN=1 uv run pytest tests/test_structures_golden.py
git diff tests/golden/structures/   # ← 반드시 눈으로 확인한다
```

`git diff` 를 읽지 않고 갱신하면 골든 테스트는 아무것도 지키지 않는다. 자세한 절차와
"언제 갱신이 정당한가"는 `docs/rules/structure_rules.md` §6 에 있다.
"""

import json
import os
from decimal import Decimal
from typing import Any

import pytest

from fixture_loader import (
    EXPECTED_FIXTURES,
    FIXTURE_DIR,
    GOLDEN_DIR,
    CandleFixture,
    load_all,
    load_fixture,
)
from updown.analysis.structures.box import detect_boxes
from updown.analysis.structures.confluence import find_confluence, zones_at
from updown.analysis.structures.params import StructureParams, load_params
from updown.analysis.structures.swing import (
    contiguous_segments,
    find_pivots,
    prior_swings,
)
from updown.analysis.structures.tolerance import from_candles, rescaled
from updown.analysis.structures.trendline import build_channel, detect_trendlines

UPDATE_ENV = "UPDOWN_UPDATE_GOLDEN"
"""이 환경변수가 설정되면 스냅샷을 덮어쓴다."""

_PRICE_QUANTUM = Decimal("0.00000001")
"""스냅샷에 적을 가격 자릿수.

가격 자체는 Decimal 로 정확하지만 **선 가격은 나눗셈 결과**다. 자릿수를 고정하지 않으면
표현 차이가 diff 로 보여 "값은 같은데 스냅샷이 다른" 잡음이 생긴다.
"""


def _num(value: Decimal) -> str:
    """Decimal 을 스냅샷용 문자열로 — 항상 같은 자릿수다."""
    return str(value.quantize(_PRICE_QUANTUM))


def _snapshot(fixture: CandleFixture, params: StructureParams) -> dict[str, Any]:
    """픽스처 하나에서 구조물 전체를 계산해 직렬화한다.

    Args:
        fixture: 입력 픽스처.
        params: 구조물 파라미터.

    Returns:
        스냅샷 딕셔너리. 키 순서와 정렬을 고정해 diff 가 의미를 갖게 한다.

    Note:
        합류는 **마지막 봉 기준**으로 계산한다. 추세선·채널은 봉마다 가격이 달라
        기준 봉이 필요하고, 마지막 봉이 "지금 진입을 판단하는 자리"다.

        `pivots` 와 `prior_swings` 를 **둘 다** 굳힌다. 전자는 구조물 작도 입력이고
        후자는 전저점·전고점(spec §6.4)이라 용도가 다르며, 한쪽만 굳히면 다른 쪽의
        회귀를 놓친다.
    """
    candles = fixture.candles
    pivots = find_pivots(candles, fixture.timeframe, params.swing)
    reduced = prior_swings(candles, fixture.timeframe, params.swing)
    # 허용 오차는 ATR 배수다 (Phase01 §1-0h). 운영과 같은 경로로 만들어야 스냅샷이
    # 실제 동작을 대리한다.
    touch = from_candles(candles, params.trendline.touch_atr_multiple)
    trendlines = detect_trendlines(candles, pivots, params.trendline, touch)
    boxes = detect_boxes(pivots, params.box, rescaled(touch, params.box.cluster_atr_multiple))
    channels = [
        channel
        for channel in (
            build_channel(candles, trendline, pivots, params.channel, params.trendline, touch)
            for trendline in trendlines
        )
        if channel is not None
    ]
    last = len(candles) - 1
    zones = zones_at(
        last, trendlines, channels, boxes, rescaled(touch, params.confluence.zone_atr_multiple)
    )

    return {
        "fixture": fixture.name,
        "rule_version": params.rule_version,
        "bar_count": len(candles),
        "shape": fixture.shape,
        # JSON 은 튜플을 리스트로 되돌리므로 처음부터 리스트로 굳힌다 — 그러지 않으면
        # 방금 만든 스냅샷과의 비교조차 항상 불일치한다.
        "segments": [list(segment) for segment in contiguous_segments(candles, fixture.timeframe)],
        "pivots": [
            {"i": s.index, "ts": s.ts.isoformat(), "kind": s.kind.value, "price": _num(s.price)}
            for s in pivots
        ],
        "prior_swings": [
            {"i": s.index, "kind": s.kind.value, "price": _num(s.price)} for s in reduced
        ],
        "trendlines": [
            {
                "kind": t.kind.value,
                "touches": [touch.index for touch in t.touches],
                "slope_per_bar": _num(t.line.slope_per_bar),
                "price_at_last": _num(t.line.price_at(last)),
                "body_violations": t.body_violations,
            }
            for t in trendlines
        ],
        "channels": [
            {
                "basis_kind": c.basis.kind.value,
                "basis_touches": [touch.index for touch in c.basis.touches],
                "lower_at_last": _num(c.lower.price_at(last)),
                "upper_at_last": _num(c.upper.price_at(last)),
                "opposite_touches": [touch.index for touch in c.opposite_touches],
            }
            for c in channels
        ],
        "boxes": [
            {
                "kind": b.kind.value,
                "level": _num(b.level),
                "low": _num(b.price_range.low),
                "high": _num(b.price_range.high),
                "touches": [touch.index for touch in b.touches],
            }
            for b in boxes
        ],
        "confluence_at_last_bar": [
            {
                "price": _num(c.price),
                "score": c.score,
                "zone_count": c.zone_count,
                "kinds": [kind.value for kind in c.kinds],
            }
            for c in find_confluence(zones)
        ],
    }


@pytest.fixture(scope="module")
def params() -> StructureParams:
    """`config/structures.yml` 의 표준값 — 스냅샷이 이 값에 묶여 있다."""
    return load_params()


@pytest.mark.parametrize("name", EXPECTED_FIXTURES)
def test_golden_snapshot_matches(name: str, params: StructureParams) -> None:
    """고정 입력 → 고정 출력 (tasks.md P1-1 DoD 1)."""
    produced = _snapshot(load_fixture(name), params)
    target = GOLDEN_DIR / f"{name}.json"

    if os.environ.get(UPDATE_ENV):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(produced, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        pytest.skip(f"{UPDATE_ENV} 로 스냅샷을 갱신했다 — git diff 를 확인하라")

    assert target.exists(), (
        f"{target} 가 없다 — `{UPDATE_ENV}=1 uv run pytest {__file__}` 로 생성하고 "
        f"git diff 를 확인한 뒤 커밋하라"
    )
    assert produced == json.loads(target.read_text(encoding="utf-8")), (
        f"{name}: 구조물 출력이 골든 스냅샷과 다르다. 의도한 변경이면 "
        f"{UPDATE_ENV}=1 로 갱신하고 diff 를 확인하라 (docs/rules/structure_rules.md §6)"
    )


def test_fixture_set_is_pinned() -> None:
    """픽스처가 조용히 사라지면 골든 테스트는 아무것도 검증하지 않는다."""
    on_disk = tuple(sorted(path.stem for path in FIXTURE_DIR.glob("*.json")))
    assert on_disk == EXPECTED_FIXTURES, (
        f"픽스처 목록이 D1-2 확정값과 다르다.\n  디스크: {on_disk}\n  기대  : {EXPECTED_FIXTURES}"
    )


def test_snapshot_is_deterministic(params: StructureParams) -> None:
    """동일 입력 2회 실행 → 완전 동일 출력 (P1-1 DoD 4 · 원칙 P1)."""
    for fixture in load_all():
        assert _snapshot(fixture, params) == _snapshot(fixture, params), (
            f"{fixture.name}: 같은 입력에서 다른 출력이 나왔다 — 어딘가 순서·시각·난수 의존이 있다"
        )


def test_every_fixture_produces_structures(params: StructureParams) -> None:
    """모든 픽스처에서 스윙·추세선이 나온다 — 빈 결과로 통과하는 스냅샷을 막는다."""
    for fixture in load_all():
        pivots = find_pivots(fixture.candles, fixture.timeframe, params.swing)
        assert len(pivots) >= 10, (
            f"{fixture.name}: 스윙이 {len(pivots)}개뿐이다. 스냅샷이 빈 결과를 굳히고 있을 수 있다"
        )
        assert detect_trendlines(
            fixture.candles,
            pivots,
            params.trendline,
            from_candles(fixture.candles, params.trendline.touch_atr_multiple),
        ), f"{fixture.name}: 추세선이 0개다. 스냅샷이 검증하는 것이 없다"
