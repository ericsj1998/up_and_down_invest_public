"""시간축 계산 — **순수 함수** (P0-8 · spec §12.3, §4.2).

간격·봉 경계·기대 봉 수는 `Timeframe` 의 성질이며 브로커와 무관하다. 무결성 검사기와
백필 러너가 같은 규칙을 써야 하므로 한 곳에 둔다 — 각자 계산하면 "검사기는 288봉을
기대하는데 백필은 289봉을 넣는" 어긋남이 생긴다.

`now` 를 인자로 받는다. 현재시각 직접 참조는 결정론 코어 금지 사항이고(원칙 P1),
테스트가 임의 시점을 재현할 수 있어야 한다.
"""

from datetime import UTC, datetime, timedelta

from updown.common.domain.instrument import Timeframe

#: Timeframe 별 봉 간격(초).
#:
#: 업비트 매핑에도 같은 표가 있지만 그쪽은 **브로커 파일**이다. 무결성 검사와 봉 수
#: 계산은 브로커를 몰라야 하므로 여기에 별도로 둔다 — 중복이 아니라 경계다.
_INTERVAL_SECONDS: dict[Timeframe, int] = {
    # ⭐ 하위 축(10s~1m)과 30m·8h 는 **보기 전용**이다 (사용자 확정 2026-08-18).
    #    표는 채워 둔다 — 비워 두면 화면에 띄우는 순간 예외가 난다.
    # ⛔ 적재 대상은 `config/backfill.yml` 이 정하고, 거기에는 넣지 않았다.
    Timeframe.S10: 10,
    Timeframe.S30: 30,
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.H8: 28800,
    Timeframe.D1: 86400,
}


class TimeframeError(ValueError):
    """간격이 정의되지 않은 시간축이다."""


def interval(timeframe: Timeframe) -> timedelta:
    """봉 간격.

    Args:
        timeframe: 시간축.

    Returns:
        간격.

    Raises:
        TimeframeError: 정의되지 않은 시간축 (예: 주봉 추가 시 표를 안 채운 경우).
    """
    seconds = _INTERVAL_SECONDS.get(timeframe)
    if seconds is None:
        raise TimeframeError(
            f"{timeframe} 의 간격이 정의되지 않았다 — timeframes.py 의 표를 채워야 한다"
        )
    return timedelta(seconds=seconds)


def interval_seconds(timeframe: Timeframe) -> int:
    """봉 간격(초) — `interval` 의 정수 표현.

    Args:
        timeframe: 시간축.

    Returns:
        초.

    Note:
        거래소 무관 SSoT 다 (T63 §2b). 러너·조립부가 gate.mapping 의 같은 이름을
        쓰던 월경 import 를 이걸로 끊었다 — 브로커 파일의 표는 그 API 가 받는 축을
        선언하는 **경계**라 별개로 남는다 (위 표 주석 참조).
    """
    return int(interval(timeframe).total_seconds())


def floor_to_interval(moment: datetime, timeframe: Timeframe) -> datetime:
    """시각을 봉 시작 경계로 내린다.

    Args:
        moment: 기준 시각 (UTC aware).
        timeframe: 시간축.

    Returns:
        해당 시각이 속한 봉의 시작 시각.

    Raises:
        ValueError: naive datetime.

    Note:
        **유닉스 epoch 기준으로 나눈다.** 업비트의 봉 경계가 실측상 그 기준과 맞는다 —
        일봉은 00:00 UTC, 4시간봉은 00/04/08/12/16/20 UTC (docs/platform/upbit_api_notes.md §3).
        "자정부터 세기"로 구현하면 4h 처럼 하루를 균등 분할하지 않는 간격에서 어긋난다.
    """
    if moment.tzinfo is None:
        raise ValueError(f"floor_to_interval 은 timezone-aware 를 요구한다: {moment!r}")
    step = int(interval(timeframe).total_seconds())
    epoch_seconds = int(moment.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch_seconds - (epoch_seconds % step), tz=UTC)


def ceil_to_interval(moment: datetime, timeframe: Timeframe) -> datetime:
    """시각을 봉 시작 경계로 **올린다**.

    Args:
        moment: 기준 시각 (UTC aware).
        timeframe: 시간축.

    Returns:
        `moment` 이후(같으면 그대로)의 첫 봉 시작 시각.

    Raises:
        ValueError: naive datetime.

    Note:
        구간의 **시작** 쪽에 필요하다. 조회가 `ts >= start` 이므로 `start` 보다 앞서
        시작하는 봉은 결과에 없는데, 내림을 쓰면 그 봉을 세어 기대 수가 1 커진다.
        그 1 이 재개 판정(`stored >= expected`)을 영원히 미달로 만들어 같은 창을 계속
        다시 받게 한다 — P0-8-3 재개가 무력화되는 지점이다.
    """
    floored = floor_to_interval(moment, timeframe)
    return floored if floored == moment else floored + interval(timeframe)


def last_closed_ts(now: datetime, timeframe: Timeframe) -> datetime:
    """**마감된** 마지막 봉의 시작 시각.

    Args:
        now: 현재 시각 (UTC aware). 인자로 받는다 (원칙 P1).
        timeframe: 시간축.

    Returns:
        마감이 끝난 가장 최근 봉의 `ts`.

    Note:
        **이 경계가 필요한 이유**: 브로커는 지금 만들어지고 있는 봉도 응답에 넣어 준다
        (업비트 실측). 그 봉은 아직 종가가 없는데도 완성된 봉과 형태가 같아서, 저장하면
        지표·구조물이 **미완성 봉을 진짜 봉으로 읽는다.**

        spec §4.2 는 "탐지는 봉마감 기준"이라고 못박았다. 그 전제를 데이터 계층에서
        지키는 것이 이 함수다.
    """
    return floor_to_interval(now, timeframe) - interval(timeframe)


def expected_bar_count(start: datetime, end: datetime, timeframe: Timeframe) -> int:
    """`[start, end]` 안에 **시작 시각이 들어가는** 봉의 수.

    Args:
        start: 시작 (포함).
        end: 끝 (포함).
        timeframe: 시간축.

    Returns:
        기대 봉 수. 구간에 봉 경계가 하나도 없으면 0.

    Note:
        **시작은 올림, 끝은 내림**이다. 조회가 `ts >= start AND ts <= end` 이므로
        경계에 걸치는 봉은 결과에 없고, 그것을 세면 기대 수가 부풀어 재개 판정이
        영원히 미달이 된다 (`ceil_to_interval` 참조).

        **24시간 장 전제다.** 코인은 휴장이 없어 이 산수가 정확하다 (spec §7) — 그래서
        "1일치 5m → 288봉" 검증이 성립한다. 주식(P3)에는 그대로 쓸 수 없다:
        장 운영시간 캘린더가 필요하며, 없이 쓰면 매일 밤이 결측으로 잡힌다.
    """
    if end < start:
        return 0
    aligned_start = ceil_to_interval(start, timeframe)
    aligned_end = floor_to_interval(end, timeframe)
    if aligned_end < aligned_start:
        return 0
    return int((aligned_end - aligned_start) / interval(timeframe)) + 1
