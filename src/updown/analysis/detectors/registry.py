"""셋업 플러그인 레지스트리 (P1-3-1 · spec §4.3.1).

> 새로운 차트 개념을 **파일 하나 추가하는 수준**으로 언제든 등록할 수 있어야 한다.

플러그인 추가 = **탐지 파일 1개(`register()`) + entry point 1줄(`pyproject.toml` 의
`[project.entry-points."updown.detectors"]`) + `config/rules/<id>.yml` 1개** (T224 · 2026-09-07).
표는 코드가 아니라 **설치된 패키지 메타데이터**다 — 비공개 전략 패키지가 자기 것을 더한다.

## 왜 데코레이터 등록이 아닌가

`@register` 데코레이터는 "1줄"에 더 가까워 보이지만 **import 부수효과에 의존**한다.
탐지 모듈을 import 하지 않으면 등록이 안 되고, 그 실수는 "테스트에서는 되는데 운영에서
플러그인이 없다"로 나타난다. 명시적 표가 그 경로를 없앤다.

## dict 리터럴이 아니라 튜플 목록인 이유 ⚠️

파이썬은 `{"a": f1, "a": f2}` 를 **조용히** 뒤쪽으로 덮어쓴다. 즉 dict 로 등록표를 만들면
id 중복이 에러가 아니라 **한 플러그인의 소멸**이 된다. `(id, factory)` 튜플 목록으로 두면
중복을 검사할 수 있다.

## id 이중 확인

`detector.id` 가 등록 키와 같은지도 검사한다. 파일을 복사해 새 플러그인을 만들다 `id` 를
안 바꾸면, 표에는 두 개인데 실제로는 같은 룰이 두 번 도는 상태가 된다 — 성과 귀속
(§4.14)이 뒤섞이므로 잡아야 한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from updown.analysis import plugins
from updown.analysis.detectors.base import DetectorFactory, SetupDetector
from updown.analysis.detectors.rules import RuleConfig
from updown.common.domain.instrument import Bucket
from updown.common.logging.setup import get_logger

_logger = get_logger("analysis.detectors.registry")


def discovered_detectors() -> tuple[tuple[str, DetectorFactory], ...]:
    """Entry point `updown.detectors` 로 등록된 (룰 id, 팩토리) 전부 — 옛 `BUILTIN_DETECTORS` 자리.

    Returns:
        설치된 패키지들이 등록한 목록. 플랫폼은 어느 모듈이 등록했는지 모른다.

    Note:
        ⚠️ `(id, factory)` 튜플 목록인 이유는 그대로다 — dict 로 받으면 두 패키지가 같은 id 를
        등록한 사실이 **한 플러그인의 소멸**로 조용히 사라진다. 중복은 `SetupRegistry` 가 잡는다.
    """
    return plugins.discovered_detectors()


"""내장 플러그인 등록표 — **플러그인 추가 시 여기에 한 줄**.

남은 예정 플러그인: `cup_handle`(§6.7) · `diamond`(§6.8) — P1-7.

한 줄마다 `config/rules/<id>.yml` 이 짝으로 있어야 한다. 없으면 부팅이 예외로 멈춘다 —
출처 불명 파라미터로 도는 것보다 낫다 (D1-1).
"""


class RegistryError(RuntimeError):
    """플러그인 등록·조회 오류."""


class UnknownRuleError(RegistryError):
    """등록되지 않았거나 설정이 없는 룰이다."""


@dataclass(frozen=True, slots=True)
class RegisteredDetector:
    """등록·구성이 끝난 플러그인 하나.

    Attributes:
        detector: 플러그인 인스턴스.
        config: 이 인스턴스를 만든 설정.

    Note:
        설정을 함께 들고 다니는 이유: `enabled`·`buckets` 판단이 호출부마다 필요하고,
        `detector` 만 넘기면 그 정보를 다시 찾아야 한다.
    """

    detector: SetupDetector
    config: RuleConfig

    @property
    def rule_id(self) -> str:
        """룰 식별자."""
        return self.config.rule_id

    @property
    def rule_version(self) -> str:
        """`rule_id@version` — 성과 귀속 표기 (spec §4.14)."""
        return f"{self.config.rule_id}@{self.config.version}"


class SetupRegistry:
    """플러그인 등록/조회 (spec §4.3.1).

    Note:
        **설정에 없는 플러그인은 만들지 않는다.** 등록표에 있어도 `config/rules/<id>.yml`
        이 없으면 파라미터를 어디서 가져올지 알 수 없고, 기본값으로 채우면 D1-1 이 막으려는
        "출처 불명 파라미터"가 된다.

        반대로 설정만 있고 등록표에 없는 것도 거부한다 — 설정을 만들어 두고 코드를 안
        붙였는데 조용히 무시되면, 사용자는 룰이 도는 줄 안다 (절대 규칙 #8).
    """

    def __init__(
        self,
        factories: Sequence[tuple[str, DetectorFactory]],
        catalog: dict[str, RuleConfig],
    ) -> None:
        """등록표와 설정을 맞춰 플러그인을 구성한다.

        Args:
            factories: `(rule_id, factory)` 목록.
            catalog: `rules.load_rules()` 결과.

        Raises:
            RegistryError: id 중복 / 설정 누락 / 코드 누락 / `detector.id` 불일치.
        """
        seen: set[str] = set()
        built: dict[str, RegisteredDetector] = {}
        for rule_id, factory in factories:
            if rule_id in seen:
                raise RegistryError(
                    f"플러그인 id 가 중복 등록됐다: '{rule_id}' — 성과 귀속(§4.14)이 뒤섞인다"
                )
            seen.add(rule_id)
            config = catalog.get(rule_id)
            if config is None:
                raise RegistryError(
                    f"'{rule_id}' 의 설정이 없다 — `config/rules/{rule_id}.yml` 을 만들어야 한다. "
                    f"기본값으로 채우면 출처 불명 파라미터가 된다 (D1-1)"
                )
            detector = factory(config.to_rule_params())
            if detector.id != rule_id:
                raise RegistryError(
                    f"등록 키와 detector.id 가 다르다: '{rule_id}' vs '{detector.id}' — "
                    f"파일을 복사해 만들다 id 를 안 바꾼 경우다"
                )
            built[rule_id] = RegisteredDetector(detector=detector, config=config)

        orphans = sorted(set(catalog) - seen)
        if orphans:
            raise RegistryError(
                f"설정만 있고 등록되지 않은 룰이 있다: {orphans} — "
                f"탐지 모듈의 `register()` 와 entry point 에 더해야 한다. 조용히 무시하면 "
                f"사용자는 룰이 도는 줄 안다"
            )

        self._detectors = built
        _logger.info(
            "registry.built",
            count=len(built),
            rules=[item.rule_version for item in built.values()],
        )

    @classmethod
    def from_plugins(cls, catalog: dict[str, RuleConfig]) -> "SetupRegistry":
        """내장 등록표로 만든다.

        Args:
            catalog: 룰 설정.

        Returns:
            레지스트리.
        """
        return cls(discovered_detectors(), catalog)

    from_builtins = from_plugins
    """옛 이름 — 호출처가 다 옮겨지면 지운다 (T224)."""

    def available(self) -> tuple[str, ...]:
        """등록된 모든 룰 id.

        Returns:
            정렬 고정 튜플 — 같은 등록이면 같은 순서 (결정론, 규칙 #5).
        """
        return tuple(sorted(self._detectors))

    def get(self, rule_id: str) -> RegisteredDetector:
        """룰 하나를 가져온다.

        Args:
            rule_id: 룰 식별자.

        Returns:
            등록된 플러그인.

        Raises:
            UnknownRuleError: 등록되지 않은 id.
        """
        found = self._detectors.get(rule_id)
        if found is None:
            raise UnknownRuleError(
                f"등록되지 않은 룰: '{rule_id}' (등록됨: {list(self.available())})"
            )
        return found

    def enabled_for(self, bucket: Bucket) -> tuple[RegisteredDetector, ...]:
        # 🔴 **아무도 안 부른다** (2026-08-19 확인). 판정 경로는 `get()` 으로 바로
        #    꺼내 쓰고, 이 함수는 보고 화면(`admin.py`·`rule_docs.py`)만 쓴다 —
        #    즉 `enabled: false` 가 **판정을 안 막는다.**
        #
        # ⚠️ 지금 무해한 것은 꺼진 룰을 선언한 플레이북이 없어서지 이 함수 덕이
        #    아니다. `playbooks.yml` 에 한 줄 들어가면 그 순간 뚫린다.
        #
        # ⛔ 살릴지 없앨지는 T22 가 정한다 — 브레이커가 룰 단위가 아니라 **RUN
        #    단위**라면 이 칸 자체가 필요 없을 수 있다.
        """해당 버킷에서 **활성**인 플러그인들 (spec §4.3.1).

        Args:
            bucket: 전략 버킷.

        Returns:
            `rule_id` 순 플러그인들. 없으면 빈 튜플이다 — "이 버킷에 쓸 룰이 없음"도
            유효한 상태다.

        Note:
            `enabled=false` 는 여기서 걸러진다. §4.6 브레이커 ②(자동 비활성)가
            설정을 끄면 탐지가 실제로 돌지 않아야 한다 (P1-3 DoD 3).
        """
        return tuple(
            self._detectors[rule_id]
            for rule_id in self.available()
            if self._detectors[rule_id].config.applies_to(bucket)
        )
