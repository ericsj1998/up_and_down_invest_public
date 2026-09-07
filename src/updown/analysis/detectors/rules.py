"""룰 설정 로딩 — **YAML 우선, DB 오버라이드 없음** (P1-3-2·3 · D1-1 · spec §4.3.1, §5.6).

## D1-1 확정 근거를 코드로 지킨다

파라미터의 1차 소스는 `config/rules/*.yml` 이고 **DB 오버라이드 경로를 만들지 않는다.**
편의성 판단이 아니라 §5.6.2 를 구조로 강제하는 선택이다 —

> 실행 중에 파라미터를 바꿀 수 있으면 그것이 곧 "성과를 보고 몰래 조율하는" 경로다.
> 관리자 화면의 슬라이더는 커밋도 리뷰도 남기지 않는다.

YAML 은 변경이 **커밋으로 남아** 누가·언제·왜가 보이고, 커밋 해시 하나로 당시 파라미터가
복원된다. 파라미터를 바꾸려면 사람이 백테스트로 검증하고 `version` 을 올려야 한다.

## on/off 는 설정에 있어도 된다

§4.6 브레이커 ②(룰 자동 비활성)와 §5.6.1 ④가 허용하는 **굵은 단위** 적응이다. 값을
조금씩 미세조정하는 것과 룰을 끄는 것은 성질이 다르다 — 후자는 이력이 남고 되돌리기 쉽다.

그래서 이 모듈은 `enabled`·`buckets` 를 다루고 **`params` 값은 파일에서만** 온다.

## 파일 1개 = 룰 1개

`config/rules/order_block.yml` 처럼 룰마다 파일을 둔다. 그래야 "탐지 파일 1개 + 레지스트리
1줄"(§4.3.1)과 대칭이 맞고, PR diff 가 어느 룰을 건드렸는지 바로 보인다. 파일명과
`rule_id` 가 다르면 거부한다 — 복사해서 만들다 `rule_id` 를 안 바꾸는 실수를 잡는다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

import yaml

from updown.analysis import plugins
from updown.analysis.detectors.base import ParamValue, RuleParams
from updown.common.domain.instrument import Bucket

DEFAULT_RULES_DIR = plugins.DEFAULT_RULES_DIR
"""룰 설정 디렉터리."""

_ALLOWED_KEYS = frozenset({"rule_id", "version", "enabled", "buckets", "params"})
_REQUIRED_KEYS = frozenset({"rule_id", "version", "buckets"})


class RuleConfigError(ValueError):
    """룰 설정을 읽을 수 없다.

    Note:
        기본값으로 조용히 넘어가지 않는다 (절대 규칙 #8). 설정이 깨진 채로 탐지가 돌면
        "룰이 이렇게 판단했다"와 "설정을 못 읽어 다른 값으로 돌았다"를 구분할 수 없고,
        그 판단에 근거한 주문까지 오염된다.
    """


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """룰 하나의 설정 (spec §4.3.1).

    Attributes:
        rule_id: 룰 식별자. 파일명과 같아야 한다.
        version: 룰 버전. 성과가 `rule_id@version` 단위로 귀속된다 (§4.14).
        enabled: 활성 여부. §4.6 브레이커 ②가 끌 수 있는 스위치로 만들었다.

            🔴 **지금은 아무것도 막지 않는다** (2026-08-19 확인). 판정 경로가
            `registry.get(rule_id).detector.detect(...)` 로 **바로 꺼내 쓰고**,
            거르는 `SetupRegistry.enabled_for` 를 부르는 곳은 보고 화면뿐이다
            (`apps/api/admin.py` · `orchestration/rule_docs.py`).

            ⚠️ **우연히 무해하다.** 꺼져 있는 룰을 셋업으로 선언한 플레이북이 하나도
            없어서 안 도는 것이지, 이 스위치가 막아서가 아니다 — `playbooks.yml` 에
            한 줄 들어가는 순간 *"꺼져 있다고 적힌 룰"* 이 조용히 돈다.

            ⛔ **살릴지 없앨지는 [T22](../../../docs/planning/tasks/T22_breaker.md)
            에서 정한다.** 브레이커가 사실 **RUN 단위**(많이 잃은 판을 멈춘다)라는
            것이 사용자 정정으로 드러났고, 그렇다면 룰의 이 칸은 브레이커와 무관해진다.
        buckets: 적용 버킷. 비어 있으면 **어디에도 적용되지 않는다**.
        params: 임계값·배수. **값은 이 파일에서만 온다** (D1-1).

    Note:
        `buckets` 를 필수로 둔 이유: 기본값을 "전 버킷"으로 하면 단타용 룰이 장투에
        조용히 적용된다. 어디에 쓸지는 룰을 만든 사람이 명시해야 한다.
    """

    rule_id: str
    version: str
    enabled: bool
    buckets: frozenset[Bucket]
    params: Mapping[str, ParamValue]

    def to_rule_params(self) -> RuleParams:
        """플러그인에 주입할 `RuleParams` 로 바꾼다.

        Returns:
            룰 id·버전·값을 담은 매개변수 묶음. 탐지기는 설정 파일의 모양을 모른다.
        """
        return RuleParams(rule_id=self.rule_id, version=self.version, values=self.params)

    def applies_to(self, bucket: Bucket) -> bool:
        """이 버킷에서 쓰이는가.

        Args:
            bucket: 단타 · 스윙 · 장투.

        Returns:
            활성 룰이고 그 버킷에 선언돼 있으면 True. 비활성이면 항상 False.
        """
        return self.enabled and bucket in self.buckets


def _coerce(value: object, rule_id: str, key: str) -> ParamValue:
    """YAML 스칼라를 `ParamValue` 로 좁힌다.

    Args:
        value: 파싱된 값.
        rule_id: 오류 메시지용.
        key: 오류 메시지용.

    Returns:
        허용 타입의 값.

    Raises:
        RuleConfigError: 스칼라가 아닌 경우 (중첩 구조는 파라미터가 아니다).

    Note:
        float 를 **Decimal 로 승격**한다. 임계값이 가격과 비교되는 경우가 많고,
        float 로 들고 있으면 그 비교에 이진 오차가 들어온다. `str()` 을 거치는 것이
        핵심이다 — `Decimal(0.001)` 은 이진 오차를 그대로 가져온다.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        return value
    raise RuleConfigError(
        f"{rule_id}.params.{key} 는 스칼라여야 한다 (받은 값: {type(value).__name__}). "
        f"중첩 구조가 필요하면 그것은 파라미터가 아니라 룰 설계 문제다"
    )


def parse_rule(raw: object, expected_id: str) -> RuleConfig:
    """파싱된 매핑에서 `RuleConfig` 를 만든다.

    Args:
        raw: YAML 파싱 결과.
        expected_id: 파일명에서 온 기대 `rule_id`.

    Returns:
        룰 설정.

    Raises:
        RuleConfigError: 최상위가 매핑이 아니거나, 필수 키 누락, 알 수 없는 키,
            `rule_id` 불일치, 알 수 없는 버킷.
    """
    if not isinstance(raw, dict):
        raise RuleConfigError(f"{expected_id}: 최상위는 매핑이어야 한다 (받은 값: {type(raw)})")
    block = cast(dict[str, object], raw)

    unknown = set(block) - _ALLOWED_KEYS
    if unknown:
        raise RuleConfigError(
            f"{expected_id}: 알 수 없는 키 {sorted(unknown)} — 허용: {sorted(_ALLOWED_KEYS)}. "
            f"오타를 조용히 넘기면 '설정을 바꿨는데 동작이 안 바뀐다'가 된다"
        )
    missing = _REQUIRED_KEYS - set(block)
    if missing:
        raise RuleConfigError(f"{expected_id}: 필수 키 누락 {sorted(missing)}")

    rule_id = str(block["rule_id"])
    if rule_id != expected_id:
        raise RuleConfigError(
            f"파일명과 rule_id 가 다르다: 파일 '{expected_id}' vs rule_id '{rule_id}' — "
            f"복사해서 만들다 rule_id 를 안 바꾼 경우다"
        )

    raw_buckets = block["buckets"]
    if not isinstance(raw_buckets, list):
        raise RuleConfigError(f"{rule_id}: buckets 는 리스트여야 한다")
    buckets: set[Bucket] = set()
    for item in cast(list[object], raw_buckets):
        try:
            buckets.add(Bucket(str(item)))
        except ValueError as exc:
            raise RuleConfigError(
                f"{rule_id}: 알 수 없는 버킷 '{item}' — 가능: {[bucket.value for bucket in Bucket]}"
            ) from exc

    raw_params: object = block.get("params") or {}
    if not isinstance(raw_params, dict):
        raise RuleConfigError(f"{rule_id}: params 는 매핑이어야 한다")
    params = {
        str(key): _coerce(value, rule_id, str(key))
        for key, value in cast(dict[str, object], raw_params).items()
    }

    enabled = block.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RuleConfigError(f"{rule_id}: enabled 는 true/false 여야 한다 (받은 값: {enabled!r})")

    return RuleConfig(
        rule_id=rule_id,
        version=str(block["version"]),
        enabled=enabled,
        buckets=frozenset(buckets),
        params=params,
    )


def load_rules(directory: Path | None = None) -> dict[str, RuleConfig]:
    """디렉터리의 룰 설정을 전부 읽는다.

    Args:
        directory: 룰 디렉터리. None 이면 `DEFAULT_RULES_DIR`.

    Returns:
        `rule_id` → 설정. 디렉터리가 없으면 빈 dict.

    Raises:
        RuleConfigError: 파일이 있는데 파싱·검증에 실패한 경우.

    Note:
        디렉터리 부재는 허용한다 — 셋업 플러그인이 아직 없는 Phase 1 초반의 정상
        상태다. 파일이 **있는데 깨진** 경우만 거부한다.

        정렬된 순서로 읽는다. 순서가 결과에 영향을 주면 안 되지만, 오류 메시지가
        실행마다 달라지는 것도 디버깅을 방해한다 (원칙 P1 의 정신).
    """
    catalog: dict[str, RuleConfig] = {}
    # T224 — 접점 순서로 합친다: 전략 패키지(entry point) → 환경 변수 → 저장소 기본.
    #        없는 디렉토리는 건너뛴다.
    for target in plugins.rule_dirs(directory):
        if not target.is_dir():
            continue
        for path in sorted(target.glob("*.yml")) + sorted(target.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise RuleConfigError(f"{path} 를 읽을 수 없다: {exc}") from exc
            config = parse_rule(raw, path.stem)
            if config.rule_id in catalog:
                raise RuleConfigError(
                    f"룰 id 가 중복됐다: '{config.rule_id}' ({path}) — "
                    "다른 설정 디렉토리와 겹치거나 .yml 과 .yaml 이 함께 있다"
                )
            catalog[config.rule_id] = config
    return catalog
