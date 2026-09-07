"""재표집 해상도 함정 — **두 번 밟은 자리라 시험으로 못 박는다** (사용자 지시).

## 무슨 함정인가

부트스트랩 p 는 `(1 + #{재표집 >= 관측}) / (B + 1)` 이라 **0 이 될 수 없다**.
그래서 `B` 가 정하는 바닥 `1/(B+1)` 이 판정 문턱보다 크면 어떤 결과도 통과할 수
없고, 그때 나오는 *"통과 0칸"* 은 **결과가 아니라 해상도**다.

    Gross 경로   2,000회 바닥 5.0e-4  >  문턱 6.0e-5   -> 0칸 (해상도)
    MAE  경로    2,000회 바닥 5.0e-4  >  문턱 6.2e-5   -> 0칸 (해상도 · **재발**)

한 번 고치고도 다른 경로에서 그대로 반복했다. 그래서 이 시험이 있다.
"""

from __future__ import annotations

import pytest

from updown.orchestration.discovery.stats import p_floor, require_resolution


def test_p_floor_is_one_over_replicates_plus_one() -> None:
    assert p_floor(2_000) == pytest.approx(1 / 2_001)
    assert p_floor(20_000) == pytest.approx(1 / 20_001)


def test_p_floor_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="1 이상"):
        p_floor(0)


def test_the_trap_we_actually_fell_into() -> None:
    """🔴 실제로 두 번 밟은 그 조합이 반드시 걸려야 한다.

    1,609칸에 Bonferroni 0.10 이면 문턱이 6.2e-5 인데, 재표집 2,000회의 바닥은
    5.0e-4 다. 이 조합에서 "0칸" 이 나왔고 두 번 다 결과로 읽었다.
    """
    threshold = 0.10 / 1_609
    with pytest.raises(ValueError, match="해상도"):
        require_resolution(2_000, threshold)


def test_the_fix_we_shipped_was_itself_marginal() -> None:
    """🔴 20,000회로 올린 것도 **여유 기준으로는 빠듯했다**.

    바닥 5.0e-5 가 문턱 6.2e-5 보다 작기는 하다. 그런데 p 가 가질 수 있는 다음 값이
    2/20,001 = 1.0e-4 라 문턱을 이미 넘는다 — 즉 통과할 수 있는 p 는 **바닥 하나뿐**
    이다. `p7_gate` 의 "150칸 통과" 는 *"150칸의 p 가 바닥에 붙었다"* 와 같은 말이다.

    그 판정 자체는 유효하다 (p <= 5.0e-5 는 참이다). 다만 등급이 아니라 **on/off**
    였다는 것을 알고 인용해야 한다. 결론은 안 바뀐다 — Tier B 를 죽인 것은
    Bonferroni 가 아니라 `Net > 0` 이었다.
    """
    with pytest.raises(ValueError, match="여유"):
        require_resolution(20_000, 0.10 / 1_609)
    # 여유를 안 따지면(=바닥이 문턱보다 작기만 하면) 통과한다 — 실제로 그 상태였다.
    require_resolution(20_000, 0.10 / 1_609, margin=1.0)


def test_single_hypothesis_at_five_percent_is_easy() -> None:
    """단일 가설 0.05 면 5,000회로 충분하다 (바닥 2.0e-4)."""
    require_resolution(5_000, 0.05)


def test_margin_is_not_merely_less_than() -> None:
    """바닥이 문턱보다 **조금** 작은 것으로는 부족하다.

    바닥과 문턱이 거의 같으면 통과할 수 있는 칸이 '정확히 바닥인 것' 뿐이라
    사실상 판정이 안 된다. 그래서 여유 배수를 요구한다.
    """
    threshold = 1 / 1_000  # 바닥 1/1001 보다 아주 조금 크다
    with pytest.raises(ValueError, match="여유"):
        require_resolution(1_000, threshold)


def test_error_message_says_how_many_are_needed() -> None:
    with pytest.raises(ValueError) as caught:
        require_resolution(2_000, 0.10 / 1_609)
    # 필요한 횟수를 알려 주지 않으면 다음 사람이 또 감으로 올린다.
    assert "161,000회 이상" in str(caught.value) or "회 이상" in str(caught.value)


def test_threshold_must_be_positive() -> None:
    with pytest.raises(ValueError, match="양수"):
        require_resolution(10_000, 0.0)
