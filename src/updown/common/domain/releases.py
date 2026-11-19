"""예정된 큰 지표 발표 일정 — 진입 문이 읽는 사실 입력 (`config/market_releases.yml`).

발표 시각은 미리 공개되는 일정이다. 세션은 네트워크도 파일도 모르므로(원칙 P3) 러너 · 재현
도구가 이 일정을 읽어 넣는다(`Session.release_schedule`).

## 🔴 "모른다" 를 "발표 없음" 으로 접지 않는다

일정에는 끝이 있다(`horizon` — 이 시각까지의 발표가 다 적혀 있다). 그 너머를 물으면 "발표가
없다" 가 아니라 **모른다**(None)고 답한다 — 일정 갱신을 잊은 것을 "창 밖" 으로 읽으면 문이 조용히
꺼진다(절대 규칙 #8). 일정이 시작하기 전(`since` 앞)도 같다.

## ⛔ 시각을 코드에 박지 않는다

발표 시각은 설정에 **UTC** 로 적는다(절대 규칙 #7). 뉴욕 08:30 · 14:00 은 서머타임에 따라 UTC 가
달라지므로 만드는 쪽이 날짜마다 바꿔 적는다.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import yaml

DEFAULT_CONFIG_PATH = Path("config/market_releases.yml")


class ReleaseConfigError(ValueError):
    """발표 일정 설정이 말이 안 된다."""


@dataclass(frozen=True, slots=True)
class ReleaseSchedule:
    """발표 일정 한 벌.

    Attributes:
        since: 이 시각부터의 발표가 다 적혀 있다.
        horizon: 이 시각까지의 발표가 다 적혀 있다.
        times: 발표 종류 → 발표 시각(UTC · 오름차순).
    """

    since: datetime
    horizon: datetime
    times: Mapping[str, tuple[datetime, ...]]

    def inside(self, now: datetime, kinds: Sequence[str], hours: Decimal) -> bool | None:
        """`now` 가 어느 발표의 앞뒤 창 `[발표 - hours, 발표 + hours)` 안인가.

        Args:
            now: 묻는 시각(UTC).
            kinds: 볼 발표 종류.
            hours: 창의 반폭(시간).

        Returns:
            안이면 True · 밖이면 False. 일정이 `now` 앞뒤 창을 다 덮지 못하거나(`since` ·
            `horizon` 밖) 묻는 종류의 일정이 없으면 None(모름).
        """
        span = timedelta(hours=float(hours))
        if now - span < self.since or now + span > self.horizon:
            return None
        for kind in kinds:
            listed = self.times.get(kind)
            if listed is None:
                return None
            k = bisect_right(listed, now + span)
            if k > 0 and now < listed[k - 1] + span:
                return True
        return False


def load_release_schedule(path: Path | None = None) -> ReleaseSchedule:
    """발표 일정 설정을 읽는다.

    Args:
        path: 설정 경로. None 이면 `DEFAULT_CONFIG_PATH`.

    Returns:
        일정.

    Raises:
        ReleaseConfigError: 파일이 없거나 · 시각에 시간대가 없거나 · 끝이 시작보다 앞인 경우.

    Note:
        꼴: `{since: "2022-01-01T00:00:00+00:00", horizon: "…", events: {cpi: ["…", …], …}}`.
        시각은 전부 시간대가 붙은 ISO 문자열이다. 종류 안에서 정렬 · 중복 제거는 여기서 한다.
    """
    target = path or DEFAULT_CONFIG_PATH
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseConfigError(f"발표 일정 설정을 못 읽었다: {target}") from exc
    if not isinstance(raw, dict):
        raise ReleaseConfigError(f"발표 일정 설정 꼴이 틀렸다: {target}")
    body = cast("dict[str, object]", raw)
    events = body.get("events")
    if not isinstance(events, dict):
        raise ReleaseConfigError(f"발표 일정 설정 꼴이 틀렸다: {target}")
    try:
        since = _at(body["since"])
        horizon = _at(body["horizon"])
        times: dict[str, tuple[datetime, ...]] = {}
        for kind, items in cast("dict[object, object]", events).items():
            if not isinstance(items, list):
                raise TypeError(f"{kind} 는 목록이어야 한다")
            times[str(kind)] = tuple(sorted({_at(item) for item in cast("list[object]", items)}))
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseConfigError(f"발표 일정 설정 값이 틀렸다: {exc}") from exc
    if horizon <= since:
        raise ReleaseConfigError("발표 일정의 끝이 시작보다 앞이다")
    return ReleaseSchedule(since=since, horizon=horizon, times=times)


def _at(text: object) -> datetime:
    """시간대가 붙은 ISO 시각."""
    got = text if isinstance(text, datetime) else datetime.fromisoformat(str(text))
    if got.tzinfo is None:
        raise ValueError(f"시간대가 없는 시각: {text}")
    return got
