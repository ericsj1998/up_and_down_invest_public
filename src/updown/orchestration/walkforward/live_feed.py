"""라이브 급전 — 실시간 봉을 `Session` 에 먹인다 (T13 ③).

## `SealedFeed` 와 같은 모양, 다른 성질

`Session` 은 `feed.judged(frame)` · `advance(frame)` · `cursor` 만 쓴다. 그 모양을 지키면
**같은 세션 코드가 과거와 라이브에서 똑같이 돈다** (원칙 P3). 그래서 새 세션을 만들지
않고 급전만 바꿔 끼운다.

다만 성질이 셋 다르고, 그 차이가 이 파일의 전부다.

| | `SealedFeed` | `LiveFeed` |
|---|---|---|
| 끝 | 봉인 끝이 정해져 있다 | **없다** — 시장이 계속 돈다 |
| 다음 봉 | 이미 배열에 있다 | **기다려야 온다** |
| 데이터 부족 | 생성 시 예외 | 웹소켓이 끊기면 **구멍**이 생긴다 |

## 🔴 미마감 봉을 원장에 넣지 않는다

Gate 는 진행 중인 봉도 계속 보낸다 (`marketdata/gate/ws.py` 실측). 그것을 봉으로
받아들이면 같은 시각의 봉이 여러 값으로 들어가고 지표가 매 틱 흔들린다 —
백테스트에서는 있을 수 없는 상태이므로 **라이브만 다른 판단을 하게 된다.**

⇒ `closed=True` 만 시리즈에 넣는다. 미마감 봉은 `pending` 에 따로 두고 **화면만** 본다.

## 🔴 구멍을 조용히 두지 않는다

웹소켓이 끊기면 그 사이 봉이 안 온다. 빈 구간은 "거래가 없었다"와 구별되지 않는다.
`gaps` 로 세고, 메우는 것은 REST 를 든 소비처의 일이다 — 이 급전은 **알린다**.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.logging.setup import get_logger
from updown.marketdata.ingest.timeframes import interval_seconds
from updown.orchestration.walkforward.sealed import Seal, SealBreachError

_logger = get_logger("walkforward.live_feed")


class LiveFeedError(RuntimeError):
    """라이브 급전을 쓸 수 없는 상태.

    Note:
        조용히 빈 시리즈로 돌지 않는다 (절대 규칙 #8). 봉이 없는 라이브는 "시장이
        조용하다"가 아니라 **연결이 죽은 것**이다.
    """


class LiveFeed:
    """실시간 봉을 담고 `SealedFeed` 와 같은 얼굴로 내준다.

    Note:
        ⭐ **시드를 REST 로 받아 시작한다.** 웹소켓만 쓰면 지표가 데워질 때까지 몇백 봉을
        기다려야 하고, 15m 이면 며칠이다. 과거는 REST, 이후는 웹소켓이다.

        ⚠️ 두 출처가 **같은 봉을 다르게** 줄 수 있다 (REST 는 확정, 웹소켓은 진행 중).
        같은 `ts` 가 오면 **나중 것이 이긴다** — 확정값이 나중에 오기 때문이다.
    """

    def __init__(
        self,
        seed: dict[Timeframe, Sequence[Candle]],
        entry: Timeframe,
    ) -> None:
        """급전을 만든다.

        Args:
            seed: 시간축별 시드 캔들 (REST 로 받은 과거). **전부 마감된 봉**이어야 한다.
            entry: 진입 시간축 — 커서 전진의 단위다.

        Raises:
            LiveFeedError: 시드가 비었거나 진입 시간축이 없는 경우.

        Note:
            🔴 **시드가 비면 지금 터진다.** 걸어가다 빈 봉을 만나면 "기회가 없었다"로
            읽히는데 사실은 데이터가 없는 것이다 — `SealedFeed` 와 같은 방침이다 (T13 ④).
        """
        if entry not in seed:
            raise LiveFeedError(
                f"진입 시간축 {entry.value} 의 시드가 없다 — 커서를 전진시킬 단위가 없다"
            )
        self._rows: dict[Timeframe, dict[datetime, Candle]] = {}
        for frame, rows in seed.items():
            if not rows:
                raise LiveFeedError(f"{frame.value} 시드가 비었다")
            self._rows[frame] = {item.ts: item for item in rows}
        self._entry = entry
        self.judge_on: set[Timeframe] = {entry}
        """**판정을 깨우는 축들** — 기본은 진입 축 하나다 (T17).

        🔴 0.4 는 여기에 방아쇠 축(10s)을 더한다. 그러면 10초봉이 마감될 때마다 세션이
        한 걸음 돌고, 띠에 닿은 지 **최대 10초** 만에 진입한다 — 15분을 기다리면 그
        사이 되돌아간 만큼을 통째로 손해 보고 들어간다 (실측 0.31%).

        ⛔ **0.1 은 손대지 않는다.** 기본값이 진입 축 하나이므로 선언이 없으면 지금과
        한 줄도 다르지 않게 돈다 (§5.6.2 동결).
        """
        self._pending: dict[Timeframe, Candle] = {}
        self._cursor = max(self._rows[entry]) + self._span(entry)
        # 🔴 시드는 **이미 판정된 것으로 본다.** True 로 시작하면 세션이 시드 마지막 봉을
        #    다시 판정하고, 라이브 첫 걸음에 유령 주문이 난다.
        self._arrived = False
        # ⭐ `seal.start` 로 쓴다 — 화면이 "어디부터 보고 있나" 를 그리는 근거다.
        self._origin = min(self._rows[entry])
        # 🔴 **두 번째 시계** (T15-2). 커서는 진입 축 봉이 마감될 때만 전진하므로,
        #    그것 하나로 "살아 있는가" 를 재면 진입 축이 15분 동안 화면 전체를 얼린다 —
        #    10초봉이 90개 들어와도 커서는 그대로다.
        self._received = self._cursor
        self._received_per: dict[Timeframe, datetime] = {}
        """축마다 마지막으로 **마감 봉이 들어온** 시각.

        ⚠️ 진행 중 봉은 안 센다 — 그 값은 매 틱 바뀌므로 "봉이 흐르는가" 의 답이 아니다.
        """
        self.gaps = 0
        """빠진 봉 수 — **0 이 아니면 시리즈에 구멍이 있다.**

        ⚠️ 구멍을 메우는 것은 소비처의 일이다 (REST 재조회). 이 급전은 세기만 한다 —
        메우려 들면 급전이 네트워크를 알게 되고, 그러면 테스트가 네트워크를 요구한다.
        """

    def _span(self, frame: Timeframe) -> timedelta:
        """그 시간축 한 봉의 길이."""
        return timedelta(seconds=interval_seconds(frame))

    @property
    def cursor(self) -> datetime:
        """지금 "현재"로 치는 시각 — 마지막 마감 봉의 **다음** 봉 시작이다.

        Note:
            🔴 마지막 봉의 `ts` 가 아니라 그 **다음** 시작이다. `ts` 로 두면 `view` 가
            그 봉을 미래로 보고 잘라낸다 — 마감된 봉이 안 보이는 급전이 된다.
        """
        return self._cursor

    @property
    def received_at(self) -> datetime:
        """**어느 축이든** 마지막으로 마감 봉이 들어온 시각 (T15-2).

        Note:
            🔴 **`cursor` 와 다른 시계다.** 커서는 진입 축에 묶여 있어서, 그것으로
            생존을 재면 정상 대기 중인 판이 죽은 것처럼 보인다.

            ⚠️ **봉의 시각이 아니라 우리가 받은 시각이다** — 벽시계다. 결정론 코어에
            넣지 않는다 (절대 규칙 #5); 감사와 화면만 쓴다.
        """
        return self._received

    def received_for(self, frame: Timeframe) -> datetime | None:
        """그 축이 마지막으로 마감 봉을 받은 시각.

        Args:
            frame: 시간축.

        Returns:
            시각. 시드 뒤로 한 번도 안 받았으면 None.

        Note:
            🔴 **None 이 곧 경보다.** 시드 뒤로 아무 봉도 안 왔다는 뜻이고, 그것이
            2026-08-18 에 5분봉이 9시간 얼어 있던 그 상태다.
        """
        return self._received_per.get(frame)

    @property
    def seal(self) -> Seal:
        """지금 **볼 수 있는 구간** — 시드 시작부터 커서까지.

        Note:
            🔴 **거짓이 아니다.** `Seal` 의 뜻은 *"이 밖으로는 못 간다"* 이고, 라이브도
            커서 밖(미래)으로는 못 간다 — `view` 가 `SealBreachError` 를 던진다.
            봉인 백테스트와 다른 것은 **끝이 고정인지 자라는지**뿐이다.

            ⭐ 이것이 있어야 `Session.feed` 주석을 `Feed` 프로토콜로 넓힐 수 있다.
            API 층이 `feed.seal` 로 화면 구간을 그리는데(정당한 결합), 라이브에 그 개념이
            없으면 16곳이 깨진다 (`feed_protocol.py` 참고).

            ⚠️ **끝이 자란다.** 봉이 마감될 때마다 `end` 가 밀린다 — 화면이 이 값을
            캐시하면 낡는다.

            ⛔ 가짜 미래를 넣지 않는다. `end` 를 먼 미래로 두면 진행률이 0 에 붙어
            화면이 "아직 시작도 안 했다" 로 보인다.
        """
        return Seal(start=self._origin, end=self._cursor)

    @property
    def entry(self) -> Timeframe:
        """전진 단위 시간축."""
        return self._entry

    @property
    def finished(self) -> bool:
        """라이브는 **끝나지 않는다**.

        Note:
            ⛔ 늘 False 다. `SealedFeed` 는 봉인 끝이 있지만 시장은 계속 돈다 —
            화면·러너가 "끝났다"로 멈추면 라이브가 조용히 죽는다.
        """
        return False

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        """들고 있는 시간축들."""
        return tuple(self._rows)

    def pending(self, frame: Timeframe) -> Candle | None:
        """진행 중인 봉 — **화면 전용**.

        Args:
            frame: 시간축.

        Returns:
            미마감 봉. 없으면 None.

        Note:
            🔴 **원장·분석에 넣지 않는다.** 이 값은 매 틱 바뀌므로 판정에 쓰면 같은
            상황에서 다른 결론이 나온다 (절대 규칙 #5). 화면이 "지금 이렇게 움직이고
            있다"를 보여주는 데만 쓴다.
        """
        return self._pending.get(frame)

    def observed(self, frame: Timeframe) -> list[Candle]:
        """**화면용 보기** — 커서로 자르지 않는다.

        Args:
            frame: 시간축.

        Returns:
            받아 둔 봉 **전부** (마감된 것만).

        Raises:
            KeyError: 이 급전이 그 시간축을 받지 않는다.

        Note:
            🔴 **커서가 화면을 막고 있었다** (2026-08-18 실측). `view()` 는 커서까지만
            돌려주는데, 커서는 **진입 축(15m) 봉이 마감될 때만** 움직인다. 그래서
            10초봉을 아무리 받아 와도 최대 15분 동안 화면에 안 나타났다 —
            *"축 진행 자체가 멈춘 것 같은데?"* 가 그 모습이다.

            ⇒ 봉은 들어오고 있었다. **보여 주지 않았을 뿐이다.**

            ⛔ **판정은 여전히 `view()` 를 쓴다.** 판정이 커서 밖을 보면 미래 참조이고,
            그것은 백테스트 성적을 통째로 무의미하게 만든다 (절대 규칙 #5).

            ⚠️ 그래서 **화면과 판정이 다른 봉을 본다.** 이것은 라이브에서만 참이고
            의도된 것이다 — 사람은 지금을 보고, 판정은 마감된 것만 본다. 화면이 그
            사실을 말해야 한다 (진행 중 봉 표시가 그 역할이다).
        """
        if frame not in self._rows:
            raise KeyError(f"{frame.value} 는 이 급전에 없다")
        return [self._rows[frame][ts] for ts in sorted(self._rows[frame])]

    def judged(self, frame: Timeframe, *, at: datetime | None = None) -> list[Candle]:
        """그 시각까지 **마감된** 봉만.

        Args:
            frame: 시간축.
            at: 기준 시각. 안 주면 현재 커서.

        Returns:
            `ts` 오름차순 캔들.

        Raises:
            SealBreachError: `at` 이 커서보다 미래인 경우.
            KeyError: 안 들고 있는 시간축.

        Note:
            🔴 커서보다 미래를 요구하면 터진다 — `SealedFeed` 와 **같은 예외**를 쓴다.
            라이브라고 다른 예외를 쓰면 상위가 두 경우를 따로 처리해야 하고, 그 분기가
            생기는 순간 "라이브에서만 나는 버그"의 자리가 만들어진다.
        """
        moment = self._cursor if at is None else at
        if moment > self._cursor:
            raise SealBreachError(
                f"미래를 요구했다 — {moment.isoformat()} > 커서 {self._cursor.isoformat()}"
            )
        if frame not in self._rows:
            raise KeyError(f"{frame.value} 는 이 급전에 없다 — 들고 있는 것: {self.timeframes}")
        return [self._rows[frame][key] for key in sorted(self._rows[frame]) if key < moment]

    def push(self, frame: Timeframe, candle: Candle, *, closed: bool) -> bool:
        """봉 하나를 받는다.

        Args:
            frame: 시간축.
            candle: 봉.
            closed: 마감됐는가 (`marketdata.gate.ws.LiveCandle.closed`).

        Returns:
            **마감 봉이 새로 들어와 커서가 움직였으면** True.

        Raises:
            KeyError: 안 들고 있는 시간축.

        Note:
            🔴 **미마감 봉은 시리즈에 안 들어간다.** `pending` 에만 담긴다.

            ⭐ 같은 `ts` 가 다시 오면 **덮어쓴다.** REST 시드와 웹소켓이 겹칠 수 있고,
            나중에 오는 값이 확정값이다.

            ⚠️ **구멍을 센다.** 새 봉의 `ts` 가 마지막 봉 + 한 칸보다 멀면 그 사이가
            비었다는 뜻이다. 메우지는 않는다 — 세고 로그에 남긴다.
        """
        if frame not in self._rows:
            raise KeyError(f"{frame.value} 는 이 급전에 없다")
        if not closed:
            self._pending[frame] = candle
            return False

        rows = self._rows[frame]
        span = self._span(frame)
        fresh = candle.ts not in rows
        if fresh and rows:
            expected = max(rows) + span
            if candle.ts > expected:
                missing = int((candle.ts - expected) / span)
                self.gaps += missing
                _logger.warning(
                    "live_feed_gap",
                    payload={
                        "timeframe": frame.value,
                        "missing_bars": missing,
                        "from": expected.isoformat(),
                        "to": candle.ts.isoformat(),
                        "gaps_total": self.gaps,
                        "note": "REST 로 메워야 한다 — 빈 봉은 '거래 없음'과 구별되지 않는다",
                    },
                )
        rows[candle.ts] = candle
        # 🔴 **두 번째 시계를 올린다** (T15-2). 진입 축이 아니어도 올린다 — 그것이
        #    커서와 갈라지는 이유 전부다. 이 값이 안 움직이면 그 축은 동결이다.
        #
        # ⚠️ 벽시계가 아니라 **봉 시각**을 쓴다. 벽시계를 쓰면 결정론 코어가 실행 시각에
        #    따라 달라지고(절대 규칙 #5), 시드를 되짚는 테스트가 재현되지 않는다.
        #    "얼마나 오래 안 왔나" 는 감사가 `frame_ages` 로 따로 잰다.
        landed = candle.ts + span
        self._received_per[frame] = landed
        self._received = max(self._received, landed)
        # 마감된 봉이 들어왔으니 그 봉은 이제 진행 중이 아니다.
        held = self._pending.get(frame)
        if held is not None and held.ts == candle.ts:
            del self._pending[frame]

        # 🔴 **판정을 깨우는 축이 여럿일 수 있다** (T17). 방아쇠 축 봉이 마감돼도
        #    한 걸음 돈다 — 그것이 "닿는 순간 들어간다" 의 실체다.
        if frame in self.judge_on:
            self._arrived = True
        if frame is not self._entry:
            # ⚠️ **커서는 진입 축만 민다.** 방아쇠 축으로 커서를 밀면 판정용 보기가
            #    10초마다 넓어져, 15분봉이 아직 마감 안 됐는데 마감된 것으로 보인다.
            return frame in self.judge_on
        moved = candle.ts + span > self._cursor
        if moved:
            self._cursor = candle.ts + span
            # ⭐ `advance` 가 소비할 플래그다. 커서만 봐서는 "이미 판정했나" 를 알 수 없다.
            self._arrived = True
        return moved

    def wake(self) -> None:
        """**판정할 것이 생겼다고 표시한다** (T17).

        Note:
            🔴 **`backfill` 은 판정을 안 깨운다** (2026-08-19 실측으로 잡았다). 방아쇠
            축은 웹소켓이 아니라 REST 로 채우는데, `refresh()` 가 `backfill` 을 쓰므로
            10초봉이 들어와도 `_arrived` 가 안 켜졌다 — 걸음이 35초 동안 0 이었다.

            ⛔ **`backfill` 안에서 켜지 않는다.** 그 함수는 구멍 메우기(`_heal`)에도
            쓰이고, 거기서 켜면 **0.1 이 구멍을 메울 때마다 한 걸음 더 돈다** — 동결
            대상의 동작이 바뀐다 (§5.6.2).

            ⇒ 방아쇠를 쫓는 쪽이 **명시적으로** 부른다. 누가 왜 깨웠는지가 코드에 남는다.
        """
        self._arrived = True

    def advance(self, frame: Timeframe) -> bool:
        """도착한 새 봉을 **소비한다** (`Feed` 계약).

        Args:
            frame: 세션이 묻는 시간축. **무시한다** — 진입축 기준으로 답한다
                (아래 Note 참고).

        Returns:
            판정할 새 봉이 있었으면 True. 소비하면 플래그가 내려간다.

        Note:
            🔴 **`SealedFeed` 와 뜻이 다르다.** 봉인 급전은 커서를 *민다* (다음 봉이 이미
            배열에 있다). 라이브는 밀 수 없다 — 봉은 기다려야 오고, 커서는 `push` 가
            이미 움직였다. 그래서 여기서 하는 일은 **"새 봉이 왔다" 는 사실을 소비**하는
            것이다.

            계약은 같다: True 면 판정할 새 봉이 있고 False 면 없다. 세션이 그 차이를
            몰라야 판정 경로가 하나로 유지된다 (원칙 P3).

            ⛔ **플래그를 안 내리면 같은 봉을 두 번 판정한다.** 세션은 True 를 받으면
            무조건 한 번 판정하므로, 새 봉 없이 True 를 주면 같은 상황에서 주문이 두 번
            난다.
        """
        # 🔴 **어느 축으로 묻든 진입축 기준으로 답한다** (실주행 2026-08-17 에서 잡았다).
        #
        #    `Session.step()` 은 `feed.advance(STEP_FRAME)` 로 묻고 `STEP_FRAME` 은
        #    **5m** 이다. 진입축(15m)과 비교해 False 를 돌려주니 세션이 40분 동안 한 번도
        #    판정하지 않았다 — 봉은 흐르고(799→802) 구멍도 재연결도 없는데 걸음이 0 이었다.
        #
        #    ⚠️ **예외가 안 나는 종류다.** 부품별로는 다 정상이라 조립해서 돌려 보고서야
        #      알았다.
        #
        #    ⛔ `STEP_FRAME` 을 바꾸지 않는다 — 그것은 세션(매매 로직)이고 동결 대상이다.
        #    ⛔ 5m 을 구독하지도 않는다 — 판정은 15m 이고 5m 을 받으면 3배 자주 판정한다.
        #
        #    계약은 "판정할 새 봉이 있는가" 이고(`Feed` 프로토콜), 그 답은 진입축이 낸다.
        del frame
        if not self._arrived:
            return False
        self._arrived = False
        return True

    def progress(self) -> float:
        """라이브에는 진행률이 없다.

        Returns:
            늘 0.0.

        Note:
            ⛔ **0 을 주는 것이 의도다.** 끝이 없으니 비율이 성립하지 않는다. 임의로
            1.0 을 주면 화면이 "끝났다"로 그리고, 임의의 값을 주면 그 값이 뜻을 가진
            것처럼 보인다 (절대 규칙 #8).
        """
        return 0.0

    def backfill(self, frame: Timeframe, candles: Sequence[Candle]) -> int:
        """구멍을 메운다 — 소비처가 REST 로 받아 온 봉들.

        Args:
            frame: 시간축.
            candles: 채울 봉들. 마감된 것만 넘긴다.

        Returns:
            새로 채워진 봉 수.

        Raises:
            KeyError: 안 들고 있는 시간축.

        Note:
            ⭐ 메운 만큼 `gaps` 를 줄인다. 줄이지 않으면 "구멍이 있다"가 영구히 남아,
            나중에 이 값을 보는 사람이 지금도 구멍이 있다고 믿는다.

            ⚠️ 커서는 **움직이지 않는다.** 과거를 채우는 일이고, 커서를 뒤로 돌리면 이미
            본 봉을 다시 판정하게 된다 (미래 참조와 같은 문제다).
        """
        if frame not in self._rows:
            raise KeyError(f"{frame.value} 는 이 급전에 없다")
        rows = self._rows[frame]
        added = 0
        for item in candles:
            if item.ts not in rows:
                rows[item.ts] = item
                added += 1
        self.gaps = max(0, self.gaps - added)
        if added:
            _logger.info(
                "live_feed_backfilled",
                payload={"timeframe": frame.value, "added": added, "gaps_left": self.gaps},
            )
        return added
