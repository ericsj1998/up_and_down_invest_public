"""급전 계약 — `Session` 이 과거와 라이브를 구별하지 않게 (원칙 P3).

## 왜 프로토콜인가

`Session` 이 `SealedFeed` 를 구체 타입으로 들고 있었다. 그러면 라이브 급전을 끼울 수
없고, 끼우려면 **세션을 하나 더 만들게** 된다 — 그 순간 판정 로직이 두 벌이 되고,
"과거에서는 맞는데 라이브에서는 틀리다" 같은 유령을 쫓게 된다.

이 프로젝트가 같은 사고를 이미 세 번 겪었다 (화면 200봉 vs 셋업 80봉, 점검기
`trend={}`, 작도 캐시). 판정 경로는 **하나**여야 한다.

## ✅ `Session.feed` 주석을 넓혔다 (2026-08-17)

처음에는 못 넓혔다. API 층이 `feed.seal` 에 결합돼 있어 16곳이 깨졌고, 그때는
`LiveFeed` 에 그 개념이 없었다.

⇒ **`LiveFeed` 가 `seal` 을 갖게 해서 풀었다.** 가짜를 만든 것이 아니다 — 라이브도
  커서 밖(미래)으로는 못 가고 `view` 가 `SealBreachError` 를 던진다. `Seal` 의 뜻이
  *"이 밖으로는 못 간다"* 이므로 정직하다. 다른 것은 **끝이 고정인지 자라는지**뿐이다.

⛔ `end` 를 먼 미래로 두지 않는다 — 그러면 진행률이 0 에 붙어 화면이 "아직 시작도 안
   했다" 로 보인다. 커서를 그대로 쓴다.

## `Session` 이 실제로 쓰는 것만 담는다

`SealedFeed` 의 다른 메서드는 넣지 않는다. 넓게 잡으면 라이브 급전이 쓰지도 않는 것을
구현해야 하고, 구현할 수 없는 것은 예외로 채워진다 — 그러면 프로토콜이 계약이 아니게
된다 (`marketdata/provider.py` 가 `list_markets` 를 프로토콜에 안 올린 것과 같은 이유).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.orchestration.walkforward.sealed import Seal


@runtime_checkable
class Feed(Protocol):
    """세션에 봉을 먹이는 것.

    Note:
        🔴 **`advance` 의 뜻이 두 구현에서 다르다.**

            `SealedFeed`  커서를 한 봉 **민다** (다음 봉은 이미 배열에 있다)
            `LiveFeed`    이미 도착한 새 봉을 **소비한다** (밀 수 없다 — 기다려야 온다)

        같은 이름을 쓰는 이유는 세션이 그 차이를 몰라야 하기 때문이다. 둘 다 계약은
        같다 — **True 면 판정할 새 봉이 있고, False 면 없다.**

        ⛔ 뒤로 가는 `advance` 는 어느 구현에도 없다. 커서를 되돌리면 이미 본 봉을 다시
        판정하게 되고 그것은 미래 참조와 같다.
    """

    @property
    def cursor(self) -> datetime:
        """지금 "현재"로 치는 시각."""
        ...

    @property
    def seal(self) -> "Seal":
        """볼 수 있는 구간.

        Note:
            봉인 급전은 **고정**이고 라이브는 **자란다** (끝이 커서를 따라 밀린다).
            둘 다 "이 밖으로는 못 간다" 는 뜻이므로 같은 이름을 쓴다.
        """
        ...

    @property
    def finished(self) -> bool:
        """더 걸어갈 곳이 없는가. 라이브는 늘 False 다."""
        ...

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        """들고 있는 시간축들."""
        ...

    @property
    def received_at(self) -> datetime:
        """**어느 축이든** 마지막으로 봉이 들어온 시각 (T15-2).

        Note:
            🔴 **`cursor` 와 다른 시계다.** 커서는 진입 축 봉이 마감될 때만 전진하므로,
            그것 하나로 "살아 있는가" 를 재면 진입 축이 15분 동안 화면 전체를 얼린다 —
            10초봉이 90개 들어와도 커서는 그대로다.

            봉인 급전에는 "지금" 이 없으므로 `cursor` 와 같은 값을 낸다.
        """
        ...

    def judged(self, frame: Timeframe, *, at: datetime | None = None) -> Sequence[Candle]:
        """**판정용 보기** — 그 시각까지 마감된 봉만, 커서까지.

        Args:
            frame: 시간축.
            at: 기준 시각. 안 주면 현재 커서.

        Returns:
            `ts` 오름차순 캔들.

        Note:
            🔴 **결정론의 근거다** (절대 규칙 #5). 커서 밖을 보면 미래 참조이고, 그것은
            백테스트 성적을 통째로 무의미하게 만든다.

            ⛔ **화면·감사가 이것을 쓰면 안 된다.** 커서가 진입 축에 묶여 있어서, 감사가
            이 보기로 봉 나이를 재다가 **막힌 눈으로** 보고 있었다 (2026-08-18) — 축이
            동결됐는데 감사는 정상이라고 답했다.
        """
        ...

    def observed(self, frame: Timeframe) -> Sequence[Candle]:
        """**화면·감사용 보기** — 받아 둔 마감 봉 전부, 커서로 자르지 않는다 (T15-1).

        Args:
            frame: 시간축.

        Returns:
            `ts` 오름차순 캔들.

        Note:
            🔴 **이름이 계약을 강제한다.** `view()` 라는 이름이 어느 쪽인지 말하지
            않아서, 하루에 나온 결함 넷이 전부 그 자리에서 나왔다.

            ⚠️ **화면과 판정이 다른 봉을 보는 것은 정상이다** — 라이브에서만. 사람은
            지금을 보고 판정은 마감된 것만 본다. 화면이 그 차이를 말해야 한다
            (진행 중 봉을 다르게 그린다).

            봉인 급전은 `judged` 와 **같은 값**을 낸다 — 봉인 구간에는 "지금" 이 없다.
        """
        ...

    def advance(self, frame: Timeframe) -> bool:
        """판정할 새 봉을 하나 확보한다.

        Args:
            frame: 전진 단위 시간축.

        Returns:
            새 봉이 생겼으면 True.
        """
        ...

    def progress(self) -> float:
        """걸어간 비율 0~1.

        Returns:
            진행 비율. 라이브는 끝이 없어 늘 0 이다.
        """
        ...
