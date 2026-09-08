"""룰 설명을 **코드와 설정에서 뽑는다** (Phase 5 §5-7).

> *"현재 구현된 로직의 docs 내 description 설명을 출력해준다."*

손으로 쓴 설명은 반드시 코드와 어긋난다. 어긋난 순간을 아무도 모르는 것이 더 나쁘다 —
화면이 "눌림 인정 구간 10봉"이라 적고 있는데 설정은 14 인 상태가 몇 주 갈 수 있다.

그래서 **런타임에 추출**한다:

| 조각 | 출처 |
|---|---|
| 요약 | 탐지기 모듈 docstring 의 **첫 문단** |
| 파라미터 값 | `config/rules/*.yml` 의 실제 값 |
| 파라미터 설명 | 그 값 **바로 위의 주석 블록** |

## 왜 YAML 주석을 직접 파싱하는가

`yaml.safe_load` 는 주석을 버린다. 그런데 우리 설정의 근거는 전부 주석에 있다 —
"10 인 이유: `fast_ma` 의 절반이다" 같은 문장이 값 자체보다 중요하다. 주석을 별도
`description:` 칸으로 옮기는 방법도 있지만, 그러면 **같은 설명이 두 벌**이 되어 다시
어긋난다. 원본 텍스트를 읽는 쪽이 어긋날 자리가 없다.

## 값이 없는 파라미터는 지어내지 않는다

설정에 없는 키는 결과에도 없다. 기본값을 채워 보여주면 화면이 "설정된 값"과 "코드
기본값"을 구분하지 못하게 되고, 사용자가 설정 파일을 열었을 때 화면과 다른 것을 본다.
"""

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from updown.analysis.detectors.registry import discovered_detectors

RULES_DIR = Path("config/rules")


def detector_modules() -> dict[str, str]:
    """룰 id → 탐지기 모듈 경로. 요약문(모듈 docstring 첫 문단)의 출처다.

    Returns:
        entry point `updown.detectors` 가 등록한 전부.

    Note:
        옛 하드코딩 표를 대신한다 (T224). 등록은 설정과 무관하므로 `enabled: false` 인 룰의
        설명도 나온다 — 화면은 **꺼진 룰의 설명도 보여줘야 한다** (왜 꺼졌는지가 그 룰에서
        가장 중요한 정보다).
    """
    return {rule_id: factory.__module__ for rule_id, factory in discovered_detectors()}


class RuleDocError(ValueError):
    """설명을 조립할 수 없다."""


@dataclass(frozen=True, slots=True)
class ParamDoc:
    """파라미터 한 칸.

    Attributes:
        name: 키.
        value: 설정된 값 (문자열로).
        description: 값 위의 주석 블록. 없으면 빈 문자열.
        inherited: 설명을 **바로 앞 키에서 물려받았는가**. `fast_ma`/`slow_ma` 처럼
            한 주석이 두 값을 함께 설명하는 경우가 있어, 화면이 그 사실을 표시해야
            "이 설명이 이 값만의 것"이라는 오해가 없다.
    """

    name: str
    value: str
    description: str
    inherited: bool


@dataclass(frozen=True, slots=True)
class RuleDoc:
    """룰 하나의 설명.

    Attributes:
        rule_id: 룰 id.
        version: 버전.
        enabled: 활성 여부.
        header: 파일 맨 위 주석 블록 — 룰의 성격·경고가 여기 있다.
        disabled_reason: `enabled:` **바로 위** 주석 블록. 꺼진 룰이면 "왜 껐는가"와
            "언제 되살리는가"가 여기 있다.
        summary: 탐지기 모듈 docstring 첫 문단.
        params: 파라미터들.
        source: 설정 파일 경로 — 화면이 "어디서 왔나"를 밝힌다.

    Note:
        🔴 `disabled_reason` 을 `header` 와 **따로 두는 이유**: 처음에는 "꺼진 이유가
        헤더 주석에 있다"고 적어 뒀는데 사실이 아니었다. 헤더는 파일 맨 위의 일반
        경고("값은 표준 이론값이며 성과를 보고 미세조정하지 않는다")이고, 진짜 이유인
        `⛔ 비활성 출하` 블록은 `enabled:` 바로 위에 따로 있다.

        그래서 화면에는 일반 경고만 뜨고 **정작 알아야 할 것이 안 보였다** — 사용자가
        "왜 fvg 와 trendline_channel 을 안 쓰냐"고 물어야 했고, 그 질문이 나온 순간
        §5-7("설명을 코드에서 뽑는다")은 절반만 동작하고 있던 것이다.
    """

    rule_id: str
    version: str
    enabled: bool
    header: str
    disabled_reason: str
    summary: str
    params: tuple[ParamDoc, ...]
    source: str


def _clean(lines: list[str]) -> str:
    """주석 줄들에서 `#` 과 장식선을 걷어낸다.

    Args:
        lines: `#` 로 시작하는 줄들.

    Returns:
        사람이 읽는 문단. 비면 빈 문자열.

    Note:
        `── ① 추세 정렬 ──` 같은 구분선의 괘선만 지운다. 제목 자체는 남긴다 — 그것이
        "이 값이 무엇을 하는가"의 첫 줄이기 때문이다.
    """
    out: list[str] = []
    for line in lines:
        text = line.strip().lstrip("#").strip()
        text = text.strip("─").strip()
        out.append(text)
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _first_paragraph(doc: str | None) -> str:
    """모듈 docstring 의 첫 문단을 꺼낸다.

    Args:
        doc: 모듈 docstring.

    Returns:
        첫 문단. 없으면 빈 문자열.

    Note:
        제목 줄(`오더블록 셋업 (spec §6.3)`)만 쓰면 한 줄짜리 설명이 되고, 전체를 쓰면
        화면에 수십 줄이 들어간다. 첫 문단은 우리 docstring 규약상 "무엇을 하는가"가
        적히는 자리다.
    """
    if not doc:
        return ""
    blocks = [block.strip() for block in doc.strip().split("\n\n") if block.strip()]
    return blocks[0] if blocks else ""


def _parse_comments(text: str) -> tuple[str, str, dict[str, tuple[str, bool]]]:
    """설정 원문에서 헤더·비활성 사유·파라미터별 주석을 뽑는다.

    Args:
        text: `config/rules/*.yml` 원문.

    Returns:
        `(헤더 주석, enabled 위 주석, {키: (설명, 물려받았는가)})`.

    Note:
        `params:` 블록 안의 키만 본다. 최상위 키(`rule_id`·`version`)는 값이 자명하고,
        섞으면 화면의 파라미터 목록에 룰 메타데이터가 끼어든다.

        🔴 **`enabled:` 만 예외다.** 그 위 주석 블록에 "왜 껐는가 · 언제 되살리는가"가
        적혀 있고, 꺼진 룰에서 그것이 가장 알아야 할 정보다. 다른 최상위 키처럼 버리면
        화면에는 파일 맨 위의 일반 경고만 남아 정작 필요한 답이 사라진다 (`RuleDoc`).

        빈 줄은 주석 버퍼를 **비운다**. 비우지 않으면 파일 위쪽 헤더가 한참 아래의
        첫 파라미터 설명으로 붙어 버린다.
    """
    header: list[str] = []
    buffer: list[str] = []
    found: dict[str, tuple[str, bool]] = {}
    enabled_note = ""
    previous = ""
    in_params = False
    seen_key = False

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if not seen_key:
                header = header or buffer[:]
            buffer = []
            # 🔴 물려주기도 여기서 끊는다. 빈 줄 뒤의 키는 앞 주석의 설명 대상이 아니다 —
            #    끊지 않으면 한참 위 블록이 무관한 값의 설명으로 붙는다.
            previous = ""
            continue
        if stripped.startswith("#"):
            buffer.append(stripped)
            continue

        seen_key = True
        if stripped.startswith("params:"):
            in_params = True
            buffer = []
            continue
        if not raw.startswith((" ", "\t")):
            # 최상위 키로 돌아왔다 — `params:` 블록이 끝났다.
            in_params = False
        if not in_params or ":" not in stripped:
            # 🔴 `enabled:` 위 주석만 살린다 — 꺼진 룰의 "왜 껐나"가 여기 있다.
            if buffer and ":" in stripped and stripped.split(":", 1)[0].strip() == "enabled":
                enabled_note = _clean(buffer)
            buffer = []
            continue

        key = stripped.split(":", 1)[0].strip()
        if buffer:
            description = _clean(buffer)
            found[key] = (description, False)
            previous = description
            buffer = []
        elif previous:
            # 앞 키의 주석이 이 값도 함께 설명한다 (`fast_ma`/`slow_ma`).
            found[key] = (previous, True)
        else:
            found[key] = ("", False)
    return _clean(header), enabled_note, found


def load_rule_doc(rule_id: str, rules_dir: Path | None = None) -> RuleDoc:
    """룰 하나의 설명을 조립한다.

    Args:
        rule_id: 룰 id.
        rules_dir: 설정 디렉터리. 기본은 `config/rules`.

    Returns:
        설명.

    Raises:
        RuleDocError: 설정 파일이 없거나 형식이 틀린 경우.
    """
    target = (rules_dir or RULES_DIR) / f"{rule_id}.yml"
    try:
        text = target.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise RuleDocError(f"{target} 를 읽을 수 없다: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuleDocError(f"{target} 최상위가 매핑이 아니다")

    document: dict[str, Any] = cast(dict[str, Any], raw)
    header, enabled_note, comments = _parse_comments(text)
    values = document.get("params")
    params: dict[str, Any] = cast(dict[str, Any], values) if isinstance(values, dict) else {}

    summary = ""
    module_path = detector_modules().get(rule_id)
    if module_path:
        try:
            summary = _first_paragraph(importlib.import_module(module_path).__doc__)
        except ImportError as exc:  # pragma: no cover - 배치 오류일 때만 난다
            raise RuleDocError(f"{module_path} 를 임포트할 수 없다: {exc}") from exc

    return RuleDoc(
        rule_id=str(document.get("rule_id", rule_id)),
        version=str(document.get("version", "")),
        enabled=bool(document.get("enabled", True)),
        header=header,
        disabled_reason=enabled_note,
        summary=summary,
        params=tuple(
            ParamDoc(
                name=name,
                value=str(value),
                description=comments.get(name, ("", False))[0],
                inherited=comments.get(name, ("", False))[1],
            )
            for name, value in params.items()
        ),
        source=str(target),
    )


def list_rule_docs(rules_dir: Path | None = None) -> list[RuleDoc]:
    """설정 디렉터리의 모든 룰 설명.

    Args:
        rules_dir: 설정 디렉터리.

    Returns:
        룰 id 순 설명들. **꺼진 룰도 포함**한다 — 왜 꺼졌는지가 화면에 필요하다.
    """
    directory = rules_dir or RULES_DIR
    return [load_rule_doc(path.stem, directory) for path in sorted(directory.glob("*.yml"))]


def rule_doc_dict(doc: RuleDoc) -> dict[str, Any]:
    """설명을 JSON 으로.

    Args:
        doc: 설명.

    Returns:
        직렬화용 dict.
    """
    return {
        "rule_id": doc.rule_id,
        "version": doc.version,
        "enabled": doc.enabled,
        "header": doc.header,
        "disabled_reason": doc.disabled_reason,
        "summary": doc.summary,
        "source": doc.source,
        "params": [
            {
                "name": item.name,
                "value": item.value,
                "description": item.description,
                "inherited": item.inherited,
            }
            for item in doc.params
        ],
    }
