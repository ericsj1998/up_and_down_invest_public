"""근거 원장과 §4.14 한계 기여 — "그 근거가 얼마나 유효한가"를 잰다.

## 무엇을 재는가 — 개수가 아니라 유효성

목표는 "근거 4개니까 확률 70%"가 **아니다.** 근거를 세는 것은 각 근거가 똑같이
유익하고 서로 독립이라고 가정하는 것인데, 둘 다 대체로 거짓이다.

재는 것은 **한계 기여**다 (spec §4.14): 그 근거가 있을 때와 없을 때 결과가 얼마나
달라지는가.

    lift = P(익절 | 근거 있음) - P(익절 | 근거 없음)

§1-0i 가 이미 "단독 수익성"을 관문으로 세운 것을 오류로 기록했다. 여기서는 그것을
반복하지 않는다 — **같은 진입 집합 안에서** 켜고 끈 차이만 본다.

## 🔴 "거짓"과 "평가 안 함"은 다르다

근거 목록에 없다고 거짓이 아니다. 룰이 꺼져 있어 **평가되지 않은 것**일 수도 있다.
둘을 섞으면 `¬E` 집단에 "실은 참이었을 수도 있는" 진입이 섞여 lift 가 희석된다.

그래서 원장이 **평가된 플래그 전체**(`evaluated`)와 **그중 참인 것**(`present`)을
따로 든다. 이 구분이 없으면 조용히 틀린 숫자가 나온다 (절대 규칙 #8).

## 조합을 다 재지 않는다 — 2^N 은 불가능하다

근거 10개면 조합이 1,024가지고, §12.9 최소 표본 30건을 곱하면 30,720건이 필요하다.
3년치로도 안 된다.

⇒ **한 번에 하나씩** 잰다 (기준선 대비). 근거 N개면 측정도 N개다.

## ⚠️ 상관을 함께 봐야 해석이 된다

오더블록과 상승추세가 같이 뜨는 경우가 많으면, "오더블록 있을 때 승률"에 추세의 공이
섞여 들어간다. 둘 다 +12%p 로 나오는데 실은 **같은 +12%p 를 두 번 센** 것일 수 있다.

`co_occurrence` 가 그 해석 재료다. lift 만 보고 근거를 채택하면 안 된다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.evaluation.follow_through import MIN_SAMPLE, Outcome
from updown.common.numeric import fixed_context


class EvidenceLedgerError(ValueError):
    """원장이 성립하지 않는다.

    Note:
        참인 근거가 평가 목록에 없다는 것은 기록 경로가 어긋났다는 뜻이다. 조용히
        넘기면 그 진입만 `¬E` 로 잘못 분류되어 lift 가 미세하게 틀린다 — 찾기 어려운
        종류의 오류다 (spec §7).
    """


@dataclass(frozen=True, slots=True)
class EvidenceLedger:
    """진입 하나에서 각 근거가 참이었는지 (P1 §1-0k).

    Attributes:
        evaluated: **평가된** 근거 이름 전체. 여기 없으면 "모른다"이지 "거짓"이 아니다.
        present: 그중 **참**이었던 것.

    Note:
        `evaluated` 를 따로 드는 것이 이 타입의 존재 이유다. `present` 만 있으면
        "없음"이 거짓인지 미평가인지 구분할 수 없고, 룰을 껐다 켤 때마다 과거 기록의
        의미가 조용히 바뀐다.
    """

    evaluated: frozenset[str]
    present: frozenset[str]

    def __post_init__(self) -> None:
        """참인 근거가 평가 목록 안에 있는지 확인한다.

        Raises:
            EvidenceLedgerError: `present` 가 `evaluated` 의 부분집합이 아닌 경우.
        """
        unknown = self.present - self.evaluated
        if unknown:
            raise EvidenceLedgerError(
                f"평가 목록에 없는 근거가 참으로 기록됐다: {sorted(unknown)} — "
                "기록 경로가 어긋났다 (P1 §1-0k)"
            )

    def state_of(self, flag: str) -> bool | None:
        """이 근거의 상태.

        Args:
            flag: 근거 이름.

        Returns:
            참/거짓. **평가되지 않았으면 None** — 집계에서 제외해야 한다.
        """
        if flag not in self.evaluated:
            return None
        return flag in self.present


@dataclass(frozen=True, slots=True)
class EvidenceOutcome:
    """진입 하나의 원장 + 결과 — 기여 계산의 원자료.

    Attributes:
        ledger: 근거 원장.
        outcome: 판정 결과.
        realized_r: 실현 손익 (R). 미결이면 None.
        symbol: 종목 코드. 종목별 집계에 쓴다 (G1 이 종목별 판정을 요구한다).
    """

    ledger: EvidenceLedger
    outcome: Outcome
    realized_r: Decimal | None
    symbol: str


@dataclass(frozen=True, slots=True)
class Arm:
    """한쪽 집단(근거 있음 / 없음)의 성적.

    Attributes:
        sample: 표본 수.
        wins: 익절 건수.
        expectancy: 평균 실현 R.
    """

    sample: int
    wins: int
    expectancy: Decimal

    @property
    def win_rate(self) -> Decimal:
        """익절 비율. 표본이 없으면 0."""
        if not self.sample:
            return Decimal(0)
        with fixed_context():
            return Decimal(self.wins) / Decimal(self.sample)


@dataclass(frozen=True, slots=True)
class Contribution:
    """근거 하나의 한계 기여 (spec §4.14).

    Attributes:
        flag: 근거 이름.
        with_flag: 근거가 참이었던 집단.
        without_flag: 거짓이었던 집단.

    Note:
        **판정하지 않고 수치만 낸다.** 채택 여부는 상관 행렬과 함께 사람이 검토하고
        (§5.6.7 "규칙 명세가 타당한지 사람이 검토하는 것은 필수"), out-of-sample 로
        확정한다.
    """

    flag: str
    with_flag: Arm
    without_flag: Arm

    @property
    def win_rate_lift(self) -> Decimal:
        """승률 차이 — 이것이 "얼마나 유효한가"의 1차 답이다."""
        return self.with_flag.win_rate - self.without_flag.win_rate

    @property
    def expectancy_lift(self) -> Decimal:
        """평균 R 차이.

        Note:
            승률과 갈릴 수 있다 — 승률은 올리는데 이기는 폭이 작아지는 근거가 있다.
            둘 다 봐야 하는 이유이며, 최종 판단은 R 쪽이 가깝다 (성과는 R 로 난다).
        """
        return self.with_flag.expectancy - self.without_flag.expectancy

    @property
    def is_judgeable(self) -> bool:
        """**양쪽 모두** 최소 표본을 채웠는가 (§12.9).

        Note:
            한쪽만 30건이면 판정할 수 없다. 특히 `without_flag` 가 비면 그 근거는
            "거의 항상 참"이라는 뜻이고, 그때 lift 는 계산되지 않는 것이 아니라
            **의미가 없다** — 그것 자체가 "이 플래그는 필터 기능이 없다"는 진단이다.
        """
        return self.with_flag.sample >= MIN_SAMPLE and self.without_flag.sample >= MIN_SAMPLE

    @property
    def shortfall(self) -> str:
        """**어느 팔이** 모자란가 — 판정 불가의 사유를 갈라 적는다.

        Returns:
            사람이 읽는 사유. 판정 가능하면 빈 문자열.

        Note:
            🔴 "표본부족" 한 마디로 뭉치면 **두 가지 다른 사실이 같은 말이 된다**:

            | 모양 | 뜻 | 해야 할 일 |
            |---|---|---|
            | 유 12 / 무 8 | 그냥 표본이 적다 | **기다린다** (종목·기간 추가) |
            | 유 24 / 무 3 | 거의 항상 참 = **필터가 아니다** | 룰을 고친다 (축 등록) |

            전자는 시간이 풀어 주지만 후자는 **표본을 늘려도 안 풀린다** — 비율이
            그대로면 무 팔도 같은 비율로만 는다. 실측에서 `fvg` 가 정확히 후자였고,
            그것을 "표본부족"으로 적어 두면 다음 회차에 또 기다리게 된다.
        """
        if self.is_judgeable:
            return ""
        low_with = self.with_flag.sample < MIN_SAMPLE
        low_without = self.without_flag.sample < MIN_SAMPLE
        if low_with and low_without:
            return "표본부족(양팔)"
        # 한쪽만 모자라면 **비율 문제**다 — 그 팔이 구조적으로 안 생긴다는 뜻이다.
        return "무 팔 부족 — 거의 항상 참" if low_without else "유 팔 부족 — 거의 항상 거짓"


def _arm(records: Sequence[EvidenceOutcome]) -> Arm:
    """한 집단의 성적을 집계한다.

    Args:
        records: 그 집단의 진입들. **미결은 이미 걸러져 있어야 한다.**

    Returns:
        집단 성적.
    """
    if not records:
        return Arm(sample=0, wins=0, expectancy=Decimal(0))
    wins = sum(1 for item in records if item.outcome is Outcome.FOLLOWED)
    total = sum((item.realized_r or Decimal(0)) for item in records)
    with fixed_context():
        expectancy = total / Decimal(len(records))
    return Arm(sample=len(records), wins=wins, expectancy=expectancy)


def marginal_contribution(records: Sequence[EvidenceOutcome], flag: str) -> Contribution:
    """근거 하나의 한계 기여를 잰다 (spec §4.14).

    Args:
        records: 진입 원장들.
        flag: 잴 근거 이름.

    Returns:
        기여. 표본 부족이면 `is_judgeable` 이 False 이고 수치는 그대로 실린다.

    Note:
        **미평가 진입은 양쪽 어디에도 넣지 않는다.** `¬E` 에 넣으면 "실은 참이었을
        수도 있는" 진입이 섞여 lift 가 희석된다.

        **미결(`UNRESOLVED`)도 뺀다.** 손익이 없으므로 어느 쪽 성적도 만들지 못한다.
        만료(`EXPIRED`)는 **포함**한다 — 실제로 손익이 난 결과다.
    """
    realized = [item for item in records if item.outcome.is_realized]
    with_flag = [item for item in realized if item.ledger.state_of(flag) is True]
    without_flag = [item for item in realized if item.ledger.state_of(flag) is False]
    return Contribution(flag=flag, with_flag=_arm(with_flag), without_flag=_arm(without_flag))


def rank_contributions(records: Sequence[EvidenceOutcome]) -> list[Contribution]:
    """평가된 모든 근거의 기여를 재서 정렬한다.

    Args:
        records: 진입 원장들.

    Returns:
        `expectancy_lift` 내림차순. 판정 불가(표본 부족)도 **빼지 않고** 뒤에 붙인다.

    Note:
        판정 불가를 목록에서 빼면 "재봤는데 없었다"와 "표본이 모자라 몰랐다"가
        구분되지 않는다 (절대 규칙 #8).
    """
    flags: set[str] = set()
    for item in records:
        flags |= item.ledger.evaluated
    scored = [marginal_contribution(records, flag) for flag in sorted(flags)]
    return sorted(
        scored,
        key=lambda item: (item.is_judgeable, item.expectancy_lift),
        reverse=True,
    )


def co_occurrence(records: Sequence[EvidenceOutcome]) -> dict[tuple[str, str], Decimal]:
    """근거 쌍이 함께 참인 비율 — lift 해석의 필수 재료다.

    Args:
        records: 진입 원장들.

    Returns:
        `(근거 A, 근거 B) → 둘 다 평가된 진입 중 둘 다 참인 비율`. A < B 인 쌍만 담는다.

    Note:
        ⚠️ **이 표 없이 lift 를 읽으면 안 된다.** 두 근거가 90% 함께 뜬다면 각자의
        lift 는 상당 부분 같은 효과를 두 번 센 것이다. 그 경우 둘을 다 채택해도
        성과가 두 배가 되지 않는다.
    """
    flags: set[str] = set()
    for item in records:
        flags |= item.ledger.evaluated
    ordered = sorted(flags)

    result: dict[tuple[str, str], Decimal] = {}
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            both_evaluated = [
                item
                for item in records
                if item.ledger.state_of(left) is not None
                and item.ledger.state_of(right) is not None
            ]
            if not both_evaluated:
                continue
            both_true = sum(
                1
                for item in both_evaluated
                if item.ledger.state_of(left) and item.ledger.state_of(right)
            )
            with fixed_context():
                result[(left, right)] = Decimal(both_true) / Decimal(len(both_evaluated))
    return result
