"""T445 — 깔때기 영속화의 증분 산수(순수). 재기동 뒤 0 부터 세는 깔때기를 DB 누적에 더하기만."""

from updown.orchestration.walkforward.live_runner import funnel_delta


def test_delta_is_only_the_increase() -> None:
    assert funnel_delta({"cand:a": 3, "gate:brake": 1}, {}) == {"cand:a": 3, "gate:brake": 1}
    assert funnel_delta({"cand:a": 5, "gate:brake": 1}, {"cand:a": 3, "gate:brake": 1}) == {
        "cand:a": 2
    }
    assert funnel_delta({"cand:a": 3}, {"cand:a": 3}) == {}


def test_a_restart_never_subtracts() -> None:
    # 재기동 뒤 세션은 0 부터 — saved 도 0 부터라 첫 걸음의 늘어난 몫만 더해진다
    assert funnel_delta({"cand:a": 1}, {}) == {"cand:a": 1}
    # 메모리 값이 저장값보다 작은 일(있어선 안 되지만)은 음수를 내지 않는다
    assert funnel_delta({"cand:a": 1}, {"cand:a": 4}) == {}
