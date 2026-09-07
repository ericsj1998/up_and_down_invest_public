"""라이브 비교 실험의 **원장** — 제안과 판정을 따로, 불변으로 남긴다 (Phase 5 §5-5).

## 왜 파일을 두 벌로 나누는가

한 파일에 제안을 쓰고 나중에 판정을 덧쓰면 **되돌릴 수 없는 라이브 구간의 기록을
수정하는 것**이 된다. §5-5 가 못박은 조건이 그 반대다:

> ⚠️ 라이브 구간은 되돌릴 수 없다. 한 번 지나간 시점은 다시 못 잰다 — 그래서 스냅샷·
> 프롬프트·원문 응답을 **전부** 남긴다.

그래서 T+0 에 `cycles/` 에 한 번 쓰고 **다시 열지 않는다.** 24시간 뒤 판정은 별도
`verdicts/` 에 새 파일로 쓴다. 판정 로직을 고쳐 다시 매기더라도 제안 원본은 그대로다 —
`event_logs` 에 UPDATE 를 금지한 것(절대 규칙 #8-2)과 같은 이유다.

## 🔴 `ABSTAINED` 와 `FAILED` 를 섞지 않는다

우리 알고리즘은 대부분의 시점에 셋업이 없다. 그것은 **관망**이지 무효응답이 아니다.
둘을 한 칸에 넣으면 "우리 알고리즘 무효응답률 92%" 같은 거짓말이 나오고, 동시에
"LLM 은 언제나 답을 낸다"는 진짜 차이가 표에서 사라진다. 그 차이가 비교의 핵심이다 —
LLM 은 물으면 반드시 사겠다고 하고, 우리 알고리즘은 대부분 안 산다.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from updown.common.domain.instrument import Market, Timeframe
from updown.common.paths import under

DEFAULT_ROOT = under("ai_experiment")
"""원장 위치. 백테스트 결과(`logs/results`)와 나란히 둔다."""

CYCLES_DIR = "cycles"
VERDICTS_DIR = "verdicts"


class ExperimentError(ValueError):
    """원장을 읽거나 쓸 수 없다. **기본값으로 때우지 않는다** (절대 규칙 #8)."""


class ParticipantKind(StrEnum):
    """참가자 종류 — 비교표의 행 묶음.

    Attributes:
        LLM: 모델 하나.
        ALGORITHM: 우리 탐지기 묶음.
        BASELINE: 동전 던지기 기준선 (G-AI-2).
    """

    LLM = "LLM"
    ALGORITHM = "ALGORITHM"
    BASELINE = "BASELINE"


class CycleSource(StrEnum):
    """회차가 생긴 경로.

    Attributes:
        SCHEDULED: 스케줄러가 4시간 격자에 발사했다. **비교표의 표본**이다.
        MANUAL: 사람이 화면에서 눌렀다. 채점은 같지만 비교표 기본 집계에서는 뺀다
            (`Cycle` 의 Note — 사람이 누르는 시점은 무작위가 아니다).
    """

    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class Stance(StrEnum):
    """그 회차에 참가자가 한 일.

    Attributes:
        PROPOSED: 매수 계획을 냈다 — 채점 대상이다.
        ABSTAINED: 셋업이 없어 관망했다. **무효응답이 아니다** (모듈 docstring).
        FAILED: 호출 실패 또는 스키마 위반. 무효응답률의 분자다 (G-AI-4).
    """

    PROPOSED = "PROPOSED"
    ABSTAINED = "ABSTAINED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Proposal:
    """참가자 하나가 한 회차에 내놓은 것.

    Attributes:
        participant: 참가자 이름. LLM 이면 모델 id, 우리 것이면 `우리-알고리즘`.
        kind: 종류.
        stance: 제안/관망/실패.
        stop_loss: 손절가. `PROPOSED` 가 아니면 None.
        take_profit_first: **1차 익절가** — 채점 목표다 (§5.6.6 정의).
        take_profit_full: 전체 익절가. 기록만 하고 채점에 쓰지 않는다.
        avg_entry: **계획 평단** — R 의 분모다 (§4.6). 시장가 참가자는 스냅샷 현재가다.
        entry_price: 트리거가 걸리는 레벨 (첫 레그). 시장가면 평단과 같다.
        trigger: `MARKET` 또는 `EntryTrigger` 값. 채점 시 진입 봉을 정하는 키다.
        rule: 룰 버전 `rule_id@version`. LLM·기준선은 빈 문자열.
        conviction_pct: 확신도 0~100. 우리 알고리즘은 `confidence x 100`.
        latency_ms: 지연.
        detail: 실패 사유 또는 근거 요약.
        raw_text: 모델 원문. **파서를 고친 뒤 다시 해석할 수 있어야 한다** (§5-2 F-4).

    Note:
        🔴 채점 목표를 `take_profit_first` 로 고정한 것은 **측정 전 선언**이다 (§5.6.2).
        전체 익절로 재면 목표가 멀어 승률이 내려가고 RR 이 올라간다 — 둘 중 유리한 쪽을
        결과를 보고 고르면 그것이 자동조율이다. §5.6.6 의 이행 정의가 "1차 익절가"이므로
        우리 셋업과 같은 자에 놓으려면 1차이어야 한다.

        🔴 **진입 방식을 참가자마다 다르게 두는 것이 공정하다.** LLM 은 "지금 어떤가"를
        답하므로 시장가 진입이고, 우리 셋업은 지정가 레그로 **가격이 내려오기를 기다린다**.
        우리 셋업을 시장가로 바꿔 채점하면 손절폭이 계획보다 넓어져 R 자체가 달라진다 —
        같은 자에 놓으려던 것이 오히려 다른 것을 재게 된다. 대신 기다리다 못 사는 경우가
        생기므로 **미진입률을 표에 싣는다** (`FollowThroughStats.no_entry` 와 같은 취급).
    """

    participant: str
    kind: ParticipantKind
    stance: Stance
    stop_loss: Decimal | None
    take_profit_first: Decimal | None
    take_profit_full: Decimal | None
    avg_entry: Decimal | None
    entry_price: Decimal | None
    trigger: str
    rule: str
    conviction_pct: int | None
    latency_ms: int
    detail: str
    raw_text: str

    @property
    def actionable(self) -> bool:
        """채점할 수 있는 제안인가."""
        return (
            self.stance is Stance.PROPOSED
            and self.stop_loss is not None
            and self.take_profit_first is not None
            and self.avg_entry is not None
        )


@dataclass(frozen=True, slots=True)
class Cycle:
    """한 시점의 회차 — 모든 참가자가 **같은 스냅샷**을 봤다.

    Attributes:
        run_id: 회차 id. `{symbol}_{taken_at}` 이라 정렬하면 시간순이다.
        symbol: 종목.
        market: 시장 — 비용 테이블 조회 키다.
        timeframe: **채점 시간축**. 15m 고정 (§5-5: 4시간마다 16봉이 새로 생긴다).
        taken_at: 스냅샷 시각 (UTC).
        digest: 캔들 해시 — 전원이 같은 것을 봤다는 증거.
        entry: 진입가 = 스냅샷의 현재가. 전원 공통이다.
        last_bar_ts: 스냅샷 마지막 채점봉의 시각. **채점은 이 다음 봉부터** 한다.
        atr: 채점 시간축의 ATR14 — 기준선 구성값이다.
        hold_bars: 보유 기한(봉). 만료 시 그 봉 종가로 턴다.
        prompt_version: 프롬프트 판 번호 (G-AI-5).
        source: 이 회차가 어떻게 생겼는가 — `SCHEDULED`(4시간 격자) 또는 `MANUAL`(사람이
            화면에서 눌렀다). **비교표는 기본으로 `SCHEDULED` 만 센다** (아래).
        proposals: 참가자별 제안.

    Note:
        `last_bar_ts` 를 따로 남기는 이유: 스냅샷의 마지막 봉은 **진행 중**이라 고가·저가에
        스냅샷 **이전** 움직임이 섞여 있다. 그 봉을 채점에 넣으면 이미 지나간 가격으로
        손절·익절이 잡혀 결과가 조용히 오염된다. 그래서 채점은 `ts > last_bar_ts` 인
        봉만 쓴다 — 진입가는 그 진행 중 봉의 종가(=현재가)이고, 그 이후는 전부 미래다.

        🔴 **`source` 를 나누는 이유는 표본 편향이다.** 사람이 화면에서 누르는 시점은
        "지금 뭔가 일어나는 것 같다" 는 순간이라 무작위가 아니다. 그것을 4시간 격자
        표본과 섞으면 비교표가 **모델이 아니라 사람의 타이밍 감각**을 함께 재게 된다.
        그래서 같은 원장에 같은 방식으로 저장하고 **같은 판정기로 채점하되**, 비교표의
        기본 집계에서는 뺀다. 사람이 자기 분석을 나중에 확인하는 목적은 그대로 달성된다.
    """

    run_id: str
    symbol: str
    market: Market
    timeframe: Timeframe
    taken_at: datetime
    digest: str
    entry: Decimal
    last_bar_ts: datetime
    atr: Decimal
    hold_bars: int
    prompt_version: str
    proposals: tuple[Proposal, ...]
    source: CycleSource = CycleSource.SCHEDULED

    @property
    def invalid_rate(self) -> Decimal:
        """LLM 무효응답률 — 관망은 분자에 넣지 않는다 (G-AI-4)."""
        models = [item for item in self.proposals if item.kind is ParticipantKind.LLM]
        if not models:
            return Decimal(0)
        bad = sum(1 for item in models if item.stance is Stance.FAILED)
        return Decimal(bad) / Decimal(len(models))


def make_run_id(symbol: str, taken_at: datetime) -> str:
    """회차 id.

    Args:
        symbol: 종목.
        taken_at: 스냅샷 시각 (UTC).

    Returns:
        `KRW-BTC_20260810T120000Z` 꼴.
    """
    return f"{symbol}_{taken_at.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def _proposal_dict(item: Proposal) -> dict[str, Any]:
    """제안 하나를 JSON 으로 — 가격은 문자열이다.

    Args:
        item: 제안.

    Returns:
        직렬화용 dict.
    """
    return {
        "participant": item.participant,
        "kind": item.kind.value,
        "stance": item.stance.value,
        "stop_loss": None if item.stop_loss is None else str(item.stop_loss),
        "take_profit_first": (
            None if item.take_profit_first is None else str(item.take_profit_first)
        ),
        "take_profit_full": None if item.take_profit_full is None else str(item.take_profit_full),
        "avg_entry": None if item.avg_entry is None else str(item.avg_entry),
        "entry_price": None if item.entry_price is None else str(item.entry_price),
        "trigger": item.trigger,
        "rule": item.rule,
        "conviction_pct": item.conviction_pct,
        "latency_ms": item.latency_ms,
        "detail": item.detail,
        "raw_text": item.raw_text,
    }


def _optional_price(raw: object) -> Decimal | None:
    """None 을 허용하는 가격 한 칸.

    Args:
        raw: 원본.

    Returns:
        Decimal 또는 None.
    """
    return None if raw is None else Decimal(str(raw))


def _proposal_of(raw: dict[str, Any]) -> Proposal:
    """직렬화된 제안을 되살린다.

    Args:
        raw: 직렬화된 제안.

    Returns:
        제안.

    Raises:
        ExperimentError: 칸이 없거나 값이 열거형 밖인 경우.
    """
    try:
        return Proposal(
            participant=str(raw["participant"]),
            kind=ParticipantKind(str(raw["kind"])),
            stance=Stance(str(raw["stance"])),
            stop_loss=_optional_price(raw.get("stop_loss")),
            take_profit_first=_optional_price(raw.get("take_profit_first")),
            take_profit_full=_optional_price(raw.get("take_profit_full")),
            avg_entry=_optional_price(raw.get("avg_entry")),
            entry_price=_optional_price(raw.get("entry_price")),
            trigger=str(raw.get("trigger", "MARKET")),
            rule=str(raw.get("rule", "")),
            conviction_pct=(
                None if raw.get("conviction_pct") is None else int(str(raw["conviction_pct"]))
            ),
            latency_ms=int(str(raw.get("latency_ms", 0))),
            detail=str(raw.get("detail", "")),
            raw_text=str(raw.get("raw_text", "")),
        )
    except (KeyError, ValueError) as exc:
        raise ExperimentError(f"제안 항목을 읽을 수 없다: {exc}") from exc


def cycle_dict(cycle: Cycle) -> dict[str, Any]:
    """회차를 JSON 으로.

    Args:
        cycle: 회차.

    Returns:
        직렬화용 dict.
    """
    return {
        "run_id": cycle.run_id,
        "symbol": cycle.symbol,
        "market": cycle.market.value,
        "timeframe": cycle.timeframe.value,
        "taken_at": cycle.taken_at.isoformat(),
        "digest": cycle.digest,
        "entry": str(cycle.entry),
        "last_bar_ts": cycle.last_bar_ts.isoformat(),
        "atr": str(cycle.atr),
        "hold_bars": cycle.hold_bars,
        "prompt_version": cycle.prompt_version,
        "source": cycle.source.value,
        "proposals": [_proposal_dict(item) for item in cycle.proposals],
    }


def cycle_of(raw: dict[str, Any]) -> Cycle:
    """직렬화된 회차를 되살린다.

    Args:
        raw: 직렬화된 회차.

    Returns:
        회차.

    Raises:
        ExperimentError: 형식이 틀린 경우.
    """
    try:
        return Cycle(
            run_id=str(raw["run_id"]),
            symbol=str(raw["symbol"]),
            market=Market(str(raw["market"])),
            timeframe=Timeframe(str(raw["timeframe"])),
            taken_at=datetime.fromisoformat(str(raw["taken_at"])),
            digest=str(raw["digest"]),
            entry=Decimal(str(raw["entry"])),
            last_bar_ts=datetime.fromisoformat(str(raw["last_bar_ts"])),
            atr=Decimal(str(raw["atr"])),
            hold_bars=int(str(raw["hold_bars"])),
            prompt_version=str(raw.get("prompt_version", "")),
            proposals=tuple(
                _proposal_of(cast(dict[str, Any], item)) for item in raw.get("proposals", [])
            ),
            # 낡은 기록에는 칸이 없다. 그때는 스케줄러만 있었으므로 그렇게 읽는다.
            source=CycleSource(str(raw.get("source", CycleSource.SCHEDULED.value))),
        )
    except (KeyError, ValueError) as exc:
        raise ExperimentError(f"회차를 읽을 수 없다: {exc}") from exc


class Ledger:
    """제안·판정 원장.

    Attributes:
        root: 원장 루트.

    Note:
        DB 가 아니라 파일인 이유는 **불변성이 공짜**이기 때문이다. 판정을 나중에 붙이려면
        테이블에서는 UPDATE 가 필요한데, 그건 우리가 `event_logs` 에서 금지한 동작이다
        (절대 규칙 #8-2). 파일 두 벌이면 쓰기가 언제나 create 다.
    """

    def __init__(self, root: Path | None = None) -> None:
        """원장을 연다.

        Args:
            root: 루트. 기본은 `logs/ai_experiment`.
        """
        self.root = root or DEFAULT_ROOT

    def _dir(self, name: str) -> Path:
        """하위 디렉터리를 보장한다.

        Args:
            name: `cycles` 또는 `verdicts`.

        Returns:
            경로.
        """
        target = self.root / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def save_cycle(self, cycle: Cycle) -> Path:
        """회차를 쓴다 — **덮어쓰지 않는다**.

        Args:
            cycle: 회차.

        Returns:
            쓴 파일 경로.

        Raises:
            ExperimentError: 같은 id 가 이미 있는 경우. 라이브 기록을 덮는 것은
                되돌릴 수 없는 손실이다.
        """
        target = self._dir(CYCLES_DIR) / f"{cycle.run_id}.json"
        if target.exists():
            raise ExperimentError(f"{cycle.run_id} 가 이미 있다 — 라이브 기록은 덮지 않는다")
        target.write_text(
            json.dumps(cycle_dict(cycle), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    def cycles(self) -> list[Cycle]:
        """모든 회차를 시간순으로.

        Returns:
            회차들.
        """
        found: list[Cycle] = []
        for path in sorted(self._dir(CYCLES_DIR).glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            found.append(cycle_of(cast(dict[str, Any], raw)))
        return sorted(found, key=lambda item: item.taken_at)

    def has_verdict(self, run_id: str) -> bool:
        """이 회차가 이미 판정됐는가.

        Args:
            run_id: 회차 id.

        Returns:
            판정 파일 존재 여부.
        """
        return (self._dir(VERDICTS_DIR) / f"{run_id}.json").exists()

    def save_verdict(self, run_id: str, payload: dict[str, Any]) -> Path:
        """판정을 쓴다.

        Args:
            run_id: 회차 id.
            payload: 판정 본문.

        Returns:
            쓴 파일 경로.

        Raises:
            ExperimentError: 이미 판정된 경우. 다시 매기려면 **원장을 새로 만든다** —
                조용히 덮으면 "언제 무엇으로 판정했나"가 사라진다.
        """
        target = self._dir(VERDICTS_DIR) / f"{run_id}.json"
        if target.exists():
            raise ExperimentError(f"{run_id} 는 이미 판정됐다 — 판정 기록은 덮지 않는다")
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def verdict(self, run_id: str) -> dict[str, Any] | None:
        """회차 하나의 판정.

        Args:
            run_id: 회차 id.

        Returns:
            판정 본문. 아직 판정되지 않았으면 None.
        """
        target = self._dir(VERDICTS_DIR) / f"{run_id}.json"
        if not target.exists():
            return None
        return cast(dict[str, Any], json.loads(target.read_text(encoding="utf-8")))

    def verdicts(self) -> list[dict[str, Any]]:
        """모든 판정.

        Returns:
            판정 본문들.
        """
        found: list[dict[str, Any]] = []
        for path in sorted(self._dir(VERDICTS_DIR).glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            found.append(cast(dict[str, Any], raw))
        return found
