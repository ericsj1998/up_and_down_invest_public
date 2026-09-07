"""선언을 읽고, 지금 도는 플레이북을 고른다 (T06b).

## ⛔ 조용한 실패를 만들지 않는다

선언 파일이 없거나 값이 틀리면 **즉시 터뜨린다.** 기본값으로 흘리면 "플레이북이
안 도는데 아무도 모르는" 상태가 되고, 그 상태의 측정은 옛 무플레이북 측정과 구분이
안 된다 (절대 규칙 #8).

## 왜 "고른 것"이 아니라 "고른 것들"인가

지금은 하나뿐이지만 나중에 같은 자리에서 둘이 돌 수 있다 — 상승장에서 눌림목과 돌파는
동시에 성립한다. 하나만 돌려주면 그때 이 함수를 다시 짜게 되고, 더 나쁘게는 **먼저
선언된 쪽이 조용히 이긴다.**
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml

from updown.analysis import plugins
from updown.analysis.playbook.types import (
    ConflictAction,
    ConflictRule,
    ConflictSide,
    Playbook,
    PlaybookRegime,
)
from updown.common.domain.evidence import Family, Grade
from updown.common.domain.instrument import MarketGroup, Timeframe
from updown.common.domain.reports import TrendDirection

DEFAULT_CONFIG_PATH = plugins.DEFAULT_PLAYBOOKS
"""선언의 단일 출처. `costs.yml` 과 같은 자리에 둔다."""


class PlaybookConfigError(ValueError):
    """선언이 없거나 읽을 수 없다.

    Note:
        기본값으로 흘리지 않는 이유: 플레이북이 안 도는 측정은 **옛 무플레이북
        측정과 구분이 안 된다.** 그러면 층을 넣은 효과를 영영 못 잰다.
    """


def _grade(raw: Any) -> Grade:
    """숫자를 등급으로. 범위를 벗어나면 터뜨린다."""
    try:
        return Grade(int(raw))
    except (TypeError, ValueError) as exc:
        raise PlaybookConfigError(f"등급 값이 잘못됐다: {raw!r} (-3~+3)") from exc


def _enum[T](kind: type[T], raw: Any, label: str) -> T:
    """문자열을 열거형으로. 모르는 값은 터뜨린다.

    Note:
        ⚠️ 오타를 조용히 넘기면 그 항목이 **선언에서 통째로 빠진다.** 계열 하나가
        빠져도 점수는 나오므로 아무도 눈치채지 못한다.
    """
    try:
        return kind(raw)  # type: ignore[call-arg]
    except ValueError as exc:
        raise PlaybookConfigError(f"{label} 에 모르는 값: {raw!r}") from exc


def _mapping(raw: object, label: str) -> Mapping[str, Any]:
    """YAML 이 준 것을 매핑으로 좁힌다.

    Args:
        raw: `yaml.safe_load` 결과의 한 조각.
        label: 오류 문구에 넣을 자리 이름.

    Returns:
        문자열 키 매핑.

    Raises:
        PlaybookConfigError: 매핑이 아닌 경우.

    Note:
        🔴 **경계에서 한 번만 좁힌다.** `yaml.safe_load` 는 `Any` 를 주므로 여기를
        안 막으면 그 `Any` 가 아래로 번져 타입 검사가 통째로 무력해진다 —
        `costs.py` 가 같은 자리에서 같은 방식을 쓴다.
    """
    if not isinstance(raw, dict):
        raise PlaybookConfigError(f"{label} 은 매핑이어야 한다 — 받은 값: {type(raw)}")
    return cast(Mapping[str, Any], raw)


def _items(raw: object, label: str) -> Sequence[Any]:
    """YAML 이 준 것을 열로 좁힌다.

    Args:
        raw: `yaml.safe_load` 결과의 한 조각.
        label: 오류 문구에 넣을 자리 이름.

    Returns:
        열. 값이 없으면 빈 열이다.

    Raises:
        PlaybookConfigError: 열이 아닌 경우.

    Note:
        ⚠️ 문자열도 열이라 `for` 가 돈다 — `setups: order_block` 처럼 대괄호를 빠뜨리면
        **글자 하나하나가 셋업이 된다.** 그래서 문자열을 따로 막는다.
    """
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, list):
        raise PlaybookConfigError(f"{label} 은 목록이어야 한다 — 받은 값: {raw!r}")
    return cast(Sequence[Any], raw)


def _short_gate(raw: object, name: str) -> str:
    """숏의 문 선언을 읽는다 — `none` 또는 `structure` 만 (T52 ⑨)."""
    text = str(raw or "none")
    if text not in ("none", "structure"):
        raise PlaybookConfigError(f"{name}: short_gate 는 none|structure 여야 한다: {text!r}")
    return text


def _regime_source(raw: object, name: str) -> str:
    """`regime_source` 를 읽는다 — 모르는 값은 터진다.

    조용히 `trend` 로 떨어뜨리면 `box` 를 적었다고 믿는 측정이 판정기로 돈다 — 축이 안 갈린다.
    """
    value = str(raw)
    if value not in ("trend", "box"):
        raise PlaybookConfigError(f"{name}.regime_source 는 trend|box 다 — {value!r} 는 모른다")
    return value


def _conflict(raw: Mapping[str, Any]) -> ConflictRule:
    """충돌 규칙 한 줄.

    Note:
        ⛔ `by_direction` 기본값은 **거짓**이다 — 안 적은 매매법의 판정이 바뀌면
        동결 버전의 비교 대상이 사라진다 (§5.6.2).
    """
    return ConflictRule(
        family=_enum(Family, raw["family"], "conflicts.family"),
        at_or_below=_grade(raw["at_or_below"]),
        action=_enum(ConflictAction, raw.get("action", ConflictAction.RECORD), "conflicts.action"),
        by_direction=bool(raw.get("by_direction", False)),
        side=_enum(ConflictSide, raw.get("side", ConflictSide.BOTH), "conflicts.side"),
    )


def _load_file(target: Path) -> list[Playbook]:
    """선언 파일 하나를 읽는다.

    Args:
        target: 선언 파일.

    Returns:
        그 파일의 플레이북 전부. 선언 순서를 지킨다.

    Raises:
        PlaybookConfigError: 파일이 없거나 값이 잘못된 경우.

    Note:
        🔴 **선언 순서를 지킨다.** 정렬하면 파일을 읽는 사람이 보는 순서와 리포트에
        찍히는 순서가 갈라진다.
    """
    if not target.exists():
        raise PlaybookConfigError(
            f"{target} 이 없다 — 플레이북이 안 도는 측정은 옛 무플레이북 측정과 "
            f"구분이 안 된다. 선언을 만들어라"
        )
    try:
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlaybookConfigError(f"{target} 를 읽을 수 없다: {exc}") from exc
    declared = _mapping(parsed, f"{target} 최상위").get("playbooks")
    if not declared:
        raise PlaybookConfigError(f"{target} 에 playbooks 항목이 비어 있다")

    out: list[Playbook] = []
    for name, raw_body in _mapping(declared, "playbooks").items():
        body = _mapping(raw_body, f"playbooks.{name}")
        try:
            out.append(
                Playbook(
                    playbook_id=str(name),
                    version=str(body["version"]),
                    market_groups=tuple(
                        _enum(MarketGroup, group, "market_groups")
                        for group in _items(body["market_groups"], "market_groups")
                    ),
                    timeframe=_enum(Timeframe, body["timeframe"], "timeframe"),
                    regimes=tuple(
                        _enum(PlaybookRegime, regime, "regimes")
                        for regime in _items(body["regimes"], "regimes")
                    ),
                    primary_family=_enum(Family, body["primary_family"], "primary_family"),
                    primary_flags=tuple(
                        str(flag) for flag in _items(body.get("primary_flags"), "primary_flags")
                    ),
                    supporting=tuple(
                        _enum(Family, family, "supporting")
                        for family in _items(body.get("supporting"), "supporting")
                    ),
                    setups=tuple(str(setup) for setup in _items(body.get("setups"), "setups")),
                    conflicts=tuple(
                        _conflict(_mapping(rule, "conflicts"))
                        for rule in _items(body.get("conflicts"), "conflicts")
                    ),
                    backtest_note=str(body.get("backtest_note", "")),
                    # ⭐ 펀드 배분 선언 (T201 · 2.0.0) — 없으면 None = 기존 동작 불변.
                    weight_mode=(
                        None if body.get("weight_mode") is None else str(body["weight_mode"])
                    ),
                    alt_leverage=(
                        None
                        if body.get("alt_leverage") is None
                        else Decimal(str(body["alt_leverage"]))
                    ),
                    # ⛔ 기본값 거짓 — 안 적은 동결 버전의 판정이 바뀌면 안 된다 (§5.6.2).
                    guard_holdings=bool(body.get("guard_holdings", False)),
                    trail_stops=bool(body.get("trail_stops", False)),
                    hold_while_trend=bool(body.get("hold_while_trend", False)),
                    needs_delta=bool(body.get("needs_delta", False)),
                    hold_level=bool(body.get("hold_level", False)),
                    ratchet_boxes=bool(body.get("ratchet_boxes", False)),
                    trail_ma=(None if body.get("trail_ma") is None else int(body["trail_ma"])),
                    adx_exit_long=(
                        None if body.get("adx_exit_long") is None else int(body["adx_exit_long"])
                    ),
                    adx_exit_short=(
                        None if body.get("adx_exit_short") is None else int(body["adx_exit_short"])
                    ),
                    adx_exit_above_long=(
                        None
                        if body.get("adx_exit_above_long") is None
                        else int(body["adx_exit_above_long"])
                    ),
                    ma_exit_below_long=(
                        None
                        if body.get("ma_exit_below_long") is None
                        else int(body["ma_exit_below_long"])
                    ),
                    entry_ref_ma_gate=(
                        None
                        if body.get("entry_ref_ma_gate") is None
                        else int(body["entry_ref_ma_gate"])
                    ),
                    bundle=tuple(str(x) for x in _items(body.get("bundle"), "bundle")),
                    risk_pct=(
                        None if body.get("risk_pct") is None else Decimal(str(body["risk_pct"]))
                    ),
                    relever=bool(body.get("relever", False)),
                    relever_band=Decimal(str(body.get("relever_band", "0.05"))),
                    relever_grow=bool(body.get("relever_grow", True)),
                    maker_exit_bars=int(body.get("maker_exit_bars", 0)),
                    maker_exit_offset=Decimal(str(body.get("maker_exit_offset", "0"))),
                    funding_cap=(
                        None
                        if body.get("funding_cap") is None
                        else Decimal(str(body["funding_cap"]))
                    ),
                    short_size_mult=(
                        None
                        if body.get("short_size_mult") is None
                        else Decimal(str(body["short_size_mult"]))
                    ),
                    flip_on_turn=bool(body.get("flip_on_turn", False)),
                    full_ride=bool(body.get("full_ride", False)),
                    hold_through_turn=bool(body.get("hold_through_turn", False)),
                    long_gate=_short_gate(body.get("long_gate", "none"), name),
                    short_gate=_short_gate(body.get("short_gate", "none"), name),
                    listed=bool(body.get("listed", False)),
                    recommended=bool(body.get("recommended", False)),
                    leverage=(
                        None if body.get("leverage") is None else Decimal(str(body["leverage"]))
                    ),
                    consec_cut=bool(body.get("consec_cut", False)),
                    label=str(body.get("label", "") or ""),
                    flip_on_opposite=bool(body.get("flip_on_opposite", False)),
                    regime_source=_regime_source(body.get("regime_source", "trend"), name),
                )
            )
        except KeyError as exc:
            raise PlaybookConfigError(f"{name} 선언에 {exc} 가 없다") from exc
    return out


def load_playbooks(path: Path | None = None) -> tuple[Playbook, ...]:
    """선언된 플레이북 전부 — 접점 순서로 파일들을 합친다 (T224).

    Args:
        path: 이 파일만 읽는다 (시험·도구용). None 이면 전략 패키지(entry point
            `updown.playbooks`) → 환경 변수 `UPDOWN_PLAYBOOK_FILES` → `config/playbooks.yml` 순.

    Returns:
        선언 순서를 지킨 전부. 파일이 여럿이면 접점 순서가 곧 파일 순서다.

    Raises:
        PlaybookConfigError: 파일이 하나도 없거나 · 같은 id 가 두 파일에 있거나 · 값이 잘못된 경우.

    Note:
        🔴 **선언 순서를 지킨다.** 정렬하면 파일을 읽는 사람이 보는 순서와 리포트에 찍히는
        순서가 갈라진다. `default_playbook` 이 첫 `recommended` 를 고르므로 **전략 패키지가
        앞**이다 — 공개 저장소의 예시 매매법은 전략 패키지가 없을 때만 기본이 된다.
    """
    files = plugins.playbook_files(path)
    if not files:
        raise PlaybookConfigError(
            "플레이북 선언이 없다 — config/playbooks.yml 도, 전략 패키지(entry point "
            "updown.playbooks)도 없다. 플레이북이 안 도는 측정은 옛 무플레이북 측정과 "
            "구분이 안 된다"
        )
    out: list[Playbook] = []
    seen: dict[str, Path] = {}
    for target in files:
        for book in _load_file(target):
            if book.playbook_id in seen:
                raise PlaybookConfigError(
                    f"플레이북 id 가 겹친다: '{book.playbook_id}' "
                    f"({seen[book.playbook_id]} · {target})"
                )
            seen[book.playbook_id] = target
            out.append(book)
    return tuple(out)


def default_playbook(path: Path | None = None) -> str:
    """화면·API 가 아무 지정 없이 쓸 기본 플레이북 id.

    `recommended: true` 인 첫 항목이 답이다 — 기본 선택의 SSoT 는 코드가 아니라
    playbooks.yml 이다 (T63 ②). 대청소 때 플레이북 이름 리터럴이 API·웹·스크립트
    세 군데에 박혀 있어 수동 교체했고, 한 곳(랜덤 백테스트)은 놓쳐서 아카이브된
    이름이 기본값으로 남아 있었다 — 그 사고의 재발 방지다.

    Args:
        path: 설정 파일. None 이면 기본 경로.

    Returns:
        플레이북 id.

    Raises:
        PlaybookConfigError: recommended 도 listed 도 없을 때. 조용한 폴백 리터럴은
            두지 않는다 — 낡은 문자열이 곧 사고다 (규칙 #8 정신).
    """
    books = load_playbooks(path)
    for item in books:
        if item.recommended:
            return item.playbook_id
    for item in books:
        if item.listed:
            return item.playbook_id
    raise PlaybookConfigError("playbooks.yml 에 recommended/listed 항목이 하나도 없다")


def active_playbooks(
    playbooks: Sequence[Playbook],
    *,
    market_group: MarketGroup,
    timeframe: Timeframe,
    trend: TrendDirection | None,
    has_box: bool,
) -> tuple[Playbook, ...]:
    """지금 이 자리에서 도는 플레이북들.

    Args:
        playbooks: 선언 전체.
        market_group: 종목의 운용 갈래.
        timeframe: 지금 시간축.
        trend: 상위 TF 주 추세. 판정 불가면 None.
        has_box: 유효한 박스가 있는가.

    Returns:
        도는 것들. 하나도 없으면 빈 튜플이다.

    Note:
        🔴 **빈 튜플이 정상적인 답이다.** "이 자리에서는 아는 매매법이 없다"는 사실이
        결과이고, 억지로 하나를 고르면 국면과 안 맞는 매매법이 돈다.

        ⚠️ 여러 개가 도는 것도 정상이다 — 상승장에서 눌림목과 돌파는 동시에 성립한다.
        하나만 돌려주면 **먼저 선언된 쪽이 조용히 이긴다.**
    """
    return tuple(
        playbook
        for playbook in playbooks
        if playbook.runs_in(
            market_group=market_group,
            timeframe=timeframe,
            trend=trend,
            has_box=has_box,
        )
    )
