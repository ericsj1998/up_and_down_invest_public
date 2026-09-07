"""상관 클러스터링 — 같은 정보를 두 번 세지 않는다 (T154 §1).

⚠️ 여기서 안 줄이면 T155 의 다중검정이 통제 불가능해진다. 그리고 잘못 안 묶으면
같은 정보를 두 번 세어 **보정이 물러진다** — 그쪽이 잘못 묶는 것보다 나쁘다.
"""

import math

import pytest

from updown.orchestration.discovery.cluster import Cluster, cluster, correlate_daily


def wave(days: int, shift: float = 0.0, noise: float = 0.0, seed: int = 1) -> dict[str, float]:
    """날짜 → 값. `shift` 로 위상을 바꾸고 `noise` 로 흐린다."""
    import random

    dice = random.Random(seed)
    return {
        f"2026-01-{index + 1:02d}": math.sin((index + shift) / 5) + dice.gauss(0, noise)
        for index in range(days)
    }


class TestCorrelateDaily:
    def test_identical_series_correlate_one(self) -> None:
        one = wave(100)
        got = correlate_daily({"a": one, "b": dict(one)}, min_shared=10)
        assert got[("a", "b")] == pytest.approx(1.0)

    def test_opposite_series_correlate_minus_one(self) -> None:
        one = wave(100)
        flipped = {day: -value for day, value in one.items()}
        got = correlate_daily({"a": one, "b": flipped}, min_shared=10)
        assert got[("a", "b")] == pytest.approx(-1.0)

    def test_too_few_shared_days_are_skipped(self) -> None:
        """🔴 겹치는 날이 적으면 상관이 우연히 크게 나온다 — 묶지 않는다."""
        left = wave(100)
        right = {
            day: value for day, value in wave(100, shift=1).items() if day.endswith(("1", "2"))
        }
        got = correlate_daily({"a": left, "b": right}, min_shared=60)
        assert got == {}

    def test_only_shared_days_are_compared(self) -> None:
        """🔴 안 터진 날을 0 으로 채우면 *"둘 다 조용했다"* 가 상관이 된다."""
        left = wave(100)
        right = {day: left[day] for day in list(left)[:70]}
        got = correlate_daily({"a": left, "b": right}, min_shared=60)
        assert got[("a", "b")] == pytest.approx(1.0)

    def test_a_constant_series_has_no_correlation(self) -> None:
        flat = {f"2026-01-{i + 1:02d}": 1.0 for i in range(100)}
        got = correlate_daily({"a": wave(100), "b": flat}, min_shared=10)
        assert got == {}


class TestCluster:
    def test_tight_pairs_merge(self) -> None:
        pairs = {("a", "b"): 0.9, ("a", "c"): 0.1, ("b", "c"): 0.2}
        got = cluster(pairs, {"a": 100, "b": 500, "c": 50})
        biggest = got[0]
        assert set(biggest.members) == {"a", "b"}
        assert biggest.leader == "b", "표본이 많은 쪽이 대표다"

    def test_the_leader_is_by_sample_not_score(self) -> None:
        """🔴 성적으로 고르면 클러스터마다 **가장 운 좋은 칸**이 뽑힌다."""
        pairs = {("poor", "rich"): 0.95}
        got = cluster(pairs, {"poor": 9000, "rich": 10})
        assert got[0].leader == "poor"

    def test_it_does_not_chain(self) -> None:
        """🔴 A-B 0.8 · B-C 0.8 이어도 A-C 가 낮으면 셋이 한 덩어리가 아니다.

        ⚠️ 처음에 단일 연결로 썼다가 실측에서 무너졌다 (2026-08-31): 1,004칸이
        **365칸·351칸 두 덩어리**로 뭉쳤다. 지평만 다른 칸끼리 상관이 0.999 라
        그 고리를 타고 전부 이어진 것이다 — *"롱인 것은 전부 같은 하나"* 는
        쓸모 있는 답이 아니다.
        """
        pairs = {("a", "b"): 0.8, ("b", "c"): 0.8, ("a", "c"): 0.1}
        got = cluster(pairs, {"a": 1, "b": 2, "c": 3})
        assert len(got) == 2
        biggest = max(got, key=len)
        assert set(biggest.members) == {"a", "b"}

    def test_a_fully_linked_triple_does_merge(self) -> None:
        """⭐ 셋이 서로 다 높으면 한 덩어리다 — 완전 연결의 정의."""
        pairs = {("a", "b"): 0.8, ("b", "c"): 0.8, ("a", "c"): 0.75}
        got = cluster(pairs, {"a": 1, "b": 2, "c": 3})
        assert len(got) == 1
        assert set(got[0].members) == {"a", "b", "c"}

    def test_an_unmeasured_pair_blocks_the_merge(self) -> None:
        """⚠️ 상관을 **못 잰** 쌍은 낮은 것으로 친다 — 모르는 것을 묶지 않는다."""
        pairs = {("a", "b"): 0.9, ("b", "c"): 0.9}
        got = cluster(pairs, {"a": 1, "b": 1, "c": 1})
        assert len(got) == 2

    def test_loose_pairs_stay_apart(self) -> None:
        pairs = {("a", "b"): 0.5, ("a", "c"): 0.4, ("b", "c"): 0.3}
        got = cluster(pairs, {"a": 1, "b": 1, "c": 1})
        assert len(got) == 3
        assert all(len(one) == 1 for one in got)

    def test_orphans_are_their_own_cluster(self) -> None:
        """⚠️ 상관을 못 잰 칸이 사라지면 격자 크기가 안 맞는다."""
        got = cluster({}, {"a": 1, "b": 2})
        assert len(got) == 2

    def test_output_is_deterministic(self) -> None:
        """절대 규칙 #5 — 같은 입력에 같은 대표."""
        pairs = {("a", "b"): 0.9, ("c", "d"): 0.9}
        weights = {"a": 5, "b": 5, "c": 1, "d": 1}
        assert cluster(pairs, weights) == cluster(pairs, weights)

    def test_a_tie_on_weight_is_broken_by_name(self) -> None:
        """⚠️ 표본이 같으면 이름으로 가른다 — 안 그러면 돌릴 때마다 대표가 바뀐다."""
        got = cluster({("b", "a"): 0.9}, {"a": 10, "b": 10})
        assert got[0].leader in {"a", "b"}
        assert cluster({("b", "a"): 0.9}, {"a": 10, "b": 10})[0].leader == got[0].leader

    def test_it_records_the_tightest_link(self) -> None:
        got = cluster({("a", "b"): 0.85, ("b", "c"): 0.92}, {"a": 1, "b": 1, "c": 1})
        assert got[0].tightest == pytest.approx(0.92)

    def test_a_cluster_knows_its_size(self) -> None:
        one = Cluster(leader="a", members=("a", "b"), tightest=0.9)
        assert len(one) == 2
