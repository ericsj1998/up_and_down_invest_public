"""캔들 무결성 검사 — **순수 함수** (P0-8-4 · spec §12.1, §7 / plan D-9, D-14).

## 왜 순수한가

원칙 P1(동일 입력 → 동일 출력)의 적용이다. 이 검사기는 DB 도, 현재시각도, 난수도 보지
않는다 — 캔들 목록과 임계값만 받아 위반 목록을 돌려준다. 그래서 같은 데이터에 대해
언제 돌려도 같은 결과가 나오고, 테스트에 DB 가 필요 없다.

`detected_at` 은 여기서 만들지 않는다. DB 의 `server_default` 가 채운다 — 검사기가
시각을 만들면 결정론이 깨진다.

## 왜 구간(range) 단위인가 (D-14)

**결측 봉은 `candles` 에 행 자체가 없다.** 컬럼 플래그로는 "빠진 봉"에 표시를 달 수 없어
구간으로 기록해야 한다. 연속된 결측을 한 구간으로 묶는 것도 같은 이유다 — 1년치 백필에
하루 구멍이 나면 288개 이슈가 아니라 1개 구간이어야 읽을 수 있다.

## Phase 0 은 기록만 한다 (D-9)

임계값이 느슨해 오탐이 섞일 수 있고, 처음부터 차단하면 오탐이 백필을 막는다.
차단 승격은 임계값 확정 후다 (`docs/rules/candle_integrity_rules.md` §5).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from updown.common.db.base import JsonDict
from updown.common.db.models.enums import QualityIssueType
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Instrument, Timeframe
from updown.marketdata.ingest.sessions import NOMINAL_TRADING_DAY, SessionCalendar
from updown.marketdata.ingest.timeframes import interval

DEFAULT_CONFIG_PATH = Path("config/candle_integrity.yml")
"""기본 임계값 파일 경로 (plan D-9 — 코드에 박지 않는다)."""


class IntegrityConfigError(ValueError):
    """임계값 설정을 읽을 수 없다.

    Note:
        기본값으로 조용히 넘어가지 않는다 (spec §7). 설정 파일이 깨진 채로 검사가
        돌면 "검사했는데 아무것도 안 잡혔다"와 "검사가 사실상 꺼졌다"를 구분할 수 없다.
    """


@dataclass(frozen=True, slots=True)
class IntegrityThresholds:
    """검사 임계값 (plan D-9).

    Attributes:
        price_spike_pct: 직전 봉 종가 대비 허용 변동률. 0.30 이면 ±30%.
        volume_spike_multiple: 직전 N봉 평균 대비 허용 배수.
        volume_spike_window: 거래량 평균에 쓸 직전 봉 개수.
        check_missing_bars: 결측 봉 검사 여부.
        max_issues_per_run: 한 번의 검사에서 보고할 최대 이슈 수.

    Note:
        **OHLC 논리 위반·음수 거래량·0 이하 가격에는 임계값이 없다** — 데이터 손상이라
        조절할 여지가 없다 (D-9). 그래서 이 클래스에 해당 필드가 없다.
    """

    price_spike_pct: Decimal = Decimal("0.30")
    volume_spike_multiple: Decimal = Decimal(50)
    volume_spike_window: int = 20
    check_missing_bars: bool = True
    max_issues_per_run: int = 500

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "IntegrityThresholds":
        """Dict 에서 만든다.

        Args:
            raw: YAML 파싱 결과.

        Returns:
            임계값.

        Raises:
            IntegrityConfigError: 값이 수치가 아니거나 범위를 벗어난 경우.

        Note:
            비율·배수를 `Decimal` 로 만들 때 **`str()` 을 거친다** — `Decimal(0.3)` 은
            이진 부동소수 오차를 그대로 옮긴다.
        """
        try:
            thresholds = cls(
                price_spike_pct=Decimal(str(raw.get("price_spike_pct", "0.30"))),
                volume_spike_multiple=Decimal(str(raw.get("volume_spike_multiple", 50))),
                volume_spike_window=int(raw.get("volume_spike_window", 20)),
                check_missing_bars=bool(raw.get("check_missing_bars", True)),
                max_issues_per_run=int(raw.get("max_issues_per_run", 500)),
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise IntegrityConfigError(f"무결성 임계값을 해석할 수 없다: {exc}") from exc

        if thresholds.price_spike_pct <= 0:
            raise IntegrityConfigError("price_spike_pct 는 0 보다 커야 한다")
        if thresholds.volume_spike_multiple <= 1:
            raise IntegrityConfigError("volume_spike_multiple 은 1 보다 커야 한다")
        if thresholds.volume_spike_window < 1:
            raise IntegrityConfigError("volume_spike_window 는 1 이상이어야 한다")
        if thresholds.max_issues_per_run < 1:
            raise IntegrityConfigError("max_issues_per_run 은 1 이상이어야 한다")
        return thresholds

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "IntegrityThresholds":
        """YAML 파일에서 읽는다.

        Args:
            path: 설정 파일 경로.

        Returns:
            임계값.

        Raises:
            IntegrityConfigError: 파일 부재·파싱 실패·형식 오류.
        """
        try:
            raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise IntegrityConfigError(f"임계값 파일을 읽을 수 없다({path}): {exc}") from exc
        except yaml.YAMLError as exc:
            raise IntegrityConfigError(f"임계값 YAML 파싱 실패({path}): {exc}") from exc
        if not isinstance(raw, dict):
            raise IntegrityConfigError(f"임계값 파일이 매핑이 아니다({path}): {type(raw).__name__}")
        return cls.from_mapping(raw)  # pyright: ignore[reportUnknownArgumentType]


def _empty_detail() -> JsonDict:
    return {}


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    """위반 **구간** 1건 (spec §9 `candle_quality_issues`, plan D-14).

    Attributes:
        instrument: 대상 종목.
        timeframe: 시간축.
        issue_type: 위반 종류.
        ts_start: 구간 시작 (UTC, 포함).
        ts_end: 구간 끝 (UTC, 포함). 단일 봉이면 `ts_start` 와 같다.
        detail: 근거 수치. 임계값 조정 시 **왜 잡혔는지** 되짚는 재료다.
    """

    instrument: Instrument
    timeframe: Timeframe
    issue_type: QualityIssueType
    ts_start: datetime
    ts_end: datetime
    detail: JsonDict = field(default_factory=_empty_detail)


def check_ohlc_logic(candle: Candle) -> QualityIssueType | None:
    """단일 봉의 논리 정합성을 본다 (spec §12.1).

    Args:
        candle: 검사할 봉.

    Returns:
        위반 종류. 정상이면 None.

    Note:
        **임계값이 없다.** `high < low` 같은 것은 시장 상황이 아니라 데이터 손상이므로
        조절할 여지가 없다 (plan D-9).

        검사 순서에 의미가 있다 — 가격이 0 이하면 OHLC 비교 자체가 무의미하므로 먼저 본다.
    """
    if min(candle.open, candle.high, candle.low, candle.close) <= 0:
        return QualityIssueType.NON_POSITIVE_PRICE
    if candle.volume < 0:
        return QualityIssueType.NEGATIVE_VOLUME
    if candle.high < candle.low:
        return QualityIssueType.OHLC_VIOLATION
    if candle.high < max(candle.open, candle.close):
        return QualityIssueType.OHLC_VIOLATION
    if candle.low > min(candle.open, candle.close):
        return QualityIssueType.OHLC_VIOLATION
    return None


def find_missing_ranges(
    candles: list[Candle],
    timeframe: Timeframe,
    sessions: SessionCalendar | None = None,
) -> list[tuple[datetime, datetime]]:
    """관측된 봉 **사이의** 결측 구간을 찾는다.

    Args:
        candles: `ts` 오름차순 봉 목록.
        timeframe: 시간축.
        sessions: 거래 세션 달력. 생략하면 **24시간 장**으로 본다 (코인의 기존 동작).

    Returns:
        `(첫 결측 ts, 마지막 결측 ts)` 목록. 연속 결측은 하나로 묶인다.

    Note:
        **구간의 양 끝은 보지 않는다.** 요청 범위의 시작 전·끝 후를 결측으로 세면
        진행 중인 백필의 경계와 아직 마감되지 않은 봉이 매번 결측으로 잡힌다 — 오탐이
        백필을 막는 상황(D-9 이 피하려는 것)이 된다. 범위 전체가 비었는지는 호출부가
        "기대 봉 수 vs 실제 적재 수"로 따로 본다.

        🔴 **주식은 매일 밤·주말에 정상적으로 구멍이 난다.** `sessions` 를 주면 **같은
        거래일 세션 안의 구멍만** 결함으로 센다 (P1 §1-0j). 안 주면 모든 구멍이 결함이며,
        그것이 코인에는 맞다 (spec §7).

        ⚠️ 세션 달력은 "장 마감 구멍"만 걸러낸다. **거래일인데 봉이 통째로 없는 날**은
        여기 걸리지 않으므로 `find_empty_trading_days` 가 따로 본다.
    """
    step = interval(timeframe)
    calendar = sessions or SessionCalendar.always()
    gaps: list[tuple[datetime, datetime]] = []

    for previous, current in pairwise(candles):
        expected = previous.ts + step
        if current.ts <= expected:
            continue
        if not calendar.same_session(previous.ts, current.ts):
            # 장이 닫혀 있던 구간이다 — 결측이 아니라 정상이다.
            continue
        gaps.append((expected, current.ts - step))

    return gaps


def find_empty_trading_days(
    candles: list[Candle], sessions: SessionCalendar
) -> list[tuple[datetime, datetime]]:
    """일봉은 있는데 하위 봉이 **하나도 없는** 거래일을 찾는다.

    Args:
        candles: `ts` 오름차순 봉 목록.
        sessions: 거래 세션 달력.

    Returns:
        `(거래일 시작, 거래일 시작)` 목록.

    Note:
        **이것이 세션 필터의 사각지대다.** 하루가 통째로 비면 그 앞뒤 봉은 서로 다른
        세션에 속하므로 `find_missing_ranges` 가 "정상적인 마감"으로 넘긴다. 즉 세션
        필터를 넣는 순간 이 검사를 함께 넣지 않으면 **결측을 놓치는 쪽으로 조용히
        기울어진다** (절대 규칙 #8).

        24시간 장 달력에는 거래일 경계가 없으므로 항상 빈 목록이다.
    """
    if sessions.always_open or not candles:
        return []
    observed = {sessions.trading_day(bar.ts) for bar in candles}
    covered = observed - {None}
    return [
        (day, day)
        for day in sessions.trading_days_in(candles[0].ts, candles[-1].ts)
        if day not in covered
    ]


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """검사 결과 (P0-8-5).

    Attributes:
        inspected: 검사한 봉 수.
        issues: 보고된 위반 구간. `max_issues_per_run` 으로 잘릴 수 있다.
        total_issues: 잘리기 전 전체 위반 수.

    Note:
        `total_issues` 를 따로 두는 이유: 상한에 걸려 잘렸을 때 **잘렸다는 사실이 드러나야**
        한다. 잘린 목록만 주면 리포트가 "몇 건 없다"로 읽힌다 (spec §7 조용한 실패 금지).
        가짜 `issue_type` 을 만들어 끼워 넣는 방법도 있지만, 그러면 위반 종류 열거형이
        메타 정보로 오염된다.
    """

    inspected: int
    issues: tuple[IntegrityIssue, ...]
    total_issues: int

    @property
    def truncated(self) -> bool:
        """상한에 걸려 잘렸는가."""
        return self.total_issues > len(self.issues)

    @property
    def is_clean(self) -> bool:
        """위반이 없는가 (DoD 2 판정)."""
        return self.total_issues == 0


def inspect_candles(
    candles: list[Candle],
    *,
    thresholds: IntegrityThresholds,
    sessions: SessionCalendar | None = None,
) -> IntegrityReport:
    """캔들 목록의 무결성을 검사한다 (P0-8-4).

    Args:
        candles: 같은 종목·같은 TF 의 봉. `ts` 오름차순이어야 한다.
        sessions: 거래 세션 달력. **주식은 반드시 넘긴다** — 생략하면 24시간 장으로
            보아 매일 밤·주말이 결측으로 잡힌다 (P1 §1-0j).
        thresholds: 임계값.

    Returns:
        검사 리포트.

    Raises:
        ValueError: 종목·TF 가 섞여 있거나 정렬이 어긋난 경우.

    Note:
        섞인 입력을 거부하는 이유: 결측·스파이크 판정이 "직전 봉"에 의존하므로, 종목이
        섞이면 A 종목의 봉을 B 종목의 직전 봉으로 비교하게 된다. 조용히 틀린 답을 내는
        대신 거부한다 (spec §7).
    """
    if not candles:
        return IntegrityReport(inspected=0, issues=(), total_issues=0)

    instrument = candles[0].instrument
    timeframe = candles[0].timeframe
    for candle in candles:
        if candle.instrument != instrument or candle.timeframe != timeframe:
            raise ValueError(
                "inspect_candles 는 단일 종목·단일 TF 만 받는다 — 섞이면 직전 봉 비교가 "
                f"틀린 짝을 본다 (기대={instrument.symbol}/{timeframe}, "
                f"발견={candle.instrument.symbol}/{candle.timeframe})"
            )
    if any(b.ts <= a.ts for a, b in pairwise(candles)):
        raise ValueError("inspect_candles 는 ts 오름차순·중복 없는 입력을 요구한다")

    issues: list[IntegrityIssue] = []

    def issue(
        issue_type: QualityIssueType, ts_start: datetime, ts_end: datetime, detail: JsonDict
    ) -> None:
        """결함 하나를 목록에 넣는다 — 종목·시간축은 바깥 것을 쓴다.

        Args:
            issue_type: 결함 종류.
            ts_start: 구간 시작.
            ts_end: 구간 끝.
            detail: 종류별 부가 정보 (JSON).
        """
        issues.append(
            IntegrityIssue(
                instrument=instrument,
                timeframe=timeframe,
                issue_type=issue_type,
                ts_start=ts_start,
                ts_end=ts_end,
                detail=detail,
            )
        )

    # 1) 봉별 논리 검사 — 임계값 없음
    for candle in candles:
        violation = check_ohlc_logic(candle)
        if violation is not None:
            issue(
                violation,
                candle.ts,
                candle.ts,
                {
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                    "volume": str(candle.volume),
                },
            )

    # 2) 가격 스파이크 — 직전 봉 종가 대비
    for previous, current in pairwise(candles):
        if previous.close <= 0:
            continue  # 이미 NON_POSITIVE_PRICE 로 잡혔다. 0 나눗셈을 피한다.
        change = abs(current.close - previous.close) / previous.close
        if change > thresholds.price_spike_pct:
            issue(
                QualityIssueType.PRICE_SPIKE,
                current.ts,
                current.ts,
                {
                    "previous_close": str(previous.close),
                    "close": str(current.close),
                    "change_pct": str(change.quantize(Decimal("0.0001"))),
                    "threshold_pct": str(thresholds.price_spike_pct),
                },
            )

    # 3) 거래량 스파이크 — 직전 N봉 평균 대비
    window = thresholds.volume_spike_window
    for index in range(window, len(candles)):
        recent = candles[index - window : index]
        mean = sum((c.volume for c in recent), Decimal(0)) / window
        if mean <= 0:
            # 표본이 전부 0 이면 배수가 정의되지 않는다. 거래 없는 구간을 스파이크로
            # 잡지 않기 위해 건너뛴다 — 진짜 문제라면 결측 검사가 잡는다.
            continue
        current = candles[index]
        multiple = current.volume / mean
        if multiple > thresholds.volume_spike_multiple:
            issue(
                QualityIssueType.VOLUME_SPIKE,
                current.ts,
                current.ts,
                {
                    "volume": str(current.volume),
                    "window_mean": str(mean.quantize(Decimal("0.00000001"))),
                    "multiple": str(multiple.quantize(Decimal("0.01"))),
                    "threshold_multiple": str(thresholds.volume_spike_multiple),
                    "window": window,
                },
            )

    # 4) 결측 구간 — 연속 결측을 하나로 묶는다 (D-14)
    if thresholds.check_missing_bars:
        step = interval(timeframe)
        for gap_start, gap_end in find_missing_ranges(candles, timeframe, sessions):
            missing = int((gap_end - gap_start) / step) + 1
            issue(
                QualityIssueType.MISSING_BARS,
                gap_start,
                gap_end,
                {"missing_bars": missing, "interval_seconds": int(step.total_seconds())},
            )
        # 4-1) 통째로 빈 거래일 — 세션 필터의 사각지대다 (`find_empty_trading_days`)
        if sessions is not None:
            per_day = int(NOMINAL_TRADING_DAY / step)
            for day_start, day_end in find_empty_trading_days(candles, sessions):
                issue(
                    QualityIssueType.MISSING_BARS,
                    day_start,
                    day_end,
                    {
                        "missing_bars": per_day,
                        "interval_seconds": int(step.total_seconds()),
                        "empty_trading_day": True,
                    },
                )

    issues.sort(key=lambda i: (i.ts_start, i.issue_type.value))
    return IntegrityReport(
        inspected=len(candles),
        issues=tuple(issues[: thresholds.max_issues_per_run]),
        total_issues=len(issues),
    )
