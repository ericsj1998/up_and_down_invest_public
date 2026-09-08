"""백필 정합성 검증 — **"완료"가 "채웠다"를 뜻하게 만든다** (P1 §1-0t).

## 왜 만들었나 — 40분을 쓰고 1봉도 안 늘었다

미국 주식 1h 재백필이 `완료` 를 찍었고, 그 로그를 근거로 세션 인계에 ✅ 로 적었다.
그런데 DB 커버리지는 **1봉도 늘지 않았다** (AAPL 1h 는 여전히 2025-04-02 시작).

기존 무결성 검사(`integrity.py`)가 이것을 못 잡은 이유가 중요하다:

| 검사 | 묻는 것 | AAPL 1h 46봉일 때 |
|---|---|---|
| `integrity.py` | **받아 온 봉들 안에** 구멍이 있나 | "missing_bars 1건" — 거의 통과 |
| **이 모듈** | **요청한 구간을** 실제로 받았나 | 🔴 "요청 시작보다 241일 늦다" |

전자는 **가져온 것**을 검사하고 후자는 **못 가져온 것**을 검사한다. 후자가 없으면
어댑터가 3% 만 주고도 성공으로 보고된다 (실제로 기대 1,489봉에 46봉이 왔다).

## 무엇으로 판정하는가 — 달력을 모델링하지 않는다

주식은 휴장·조기마감이 있어 "기대 봉 수"를 시각만으로 계산하면 항상 틀린다. 그래서
**두 가지만** 본다:

1. **경계** (게이트) — 요청 시작보다 적재 시작이 늦은 일수. 달력과 무관하게 명확하다.
2. **밀도** (보고) — 같은 종목의 **기준 시간축**과 비교한 봉 수 비율. 같은 종목이면
   휴장일이 같으므로 달력을 몰라도 된다: 1h 봉 수는 15m 봉 수의 약 1/4 여야 한다.

밀도로 **막지는 않는다** — 합성 경계·조기마감 때문에 정확히 1/4 가 아닐 수 있고,
거기에 임계값을 두면 그 값이 곧 새 자유 파라미터가 된다 (§5.6.7). 대신 **찍는다**.

## ⛔ 조용히 넘어가지 않는다

경계 위반은 **exit code 를 바꾼다** (절대 규칙 #8). 백필 스크립트가 여러 조합을 돌 때
중간 하나가 못 채웠으면 그 사실이 마지막 줄에 남아야 한다 — 스크롤을 거슬러 올라가야
보이는 경고는 없는 것과 같다.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval

HEAD_TOLERANCE = timedelta(days=1)
"""적재 시작이 요청 시작보다 늦어도 봐 주는 폭.

**1일인 근거**: 앵커는 날짜 경계(00:00 UTC)인데 시장은 그날 늦게 열린다 — 미국 주식이면
13:30 UTC 다. 하루 안쪽 차이는 **세션 오프셋**이지 결손이 아니다. 그보다 크면 요청한
날짜의 봉 자체가 없다는 뜻이므로 진짜 구멍이다.

⚠️ 조정 값이 아니다. 늘리면 "며칠까지 구멍을 봐 줄 것인가"라는 답할 수 없는 질문이
생긴다 (축 J1·L3 이 탈락한 이유와 같다).
"""

TAIL_TOLERANCE = timedelta(days=3)
"""적재 끝이 요청 끝보다 이른 것을 봐 주는 폭.

**3일인 근거**: 주말 + 공휴일 하나. 금요일 마감 뒤에 돌리면 최신 봉이 사흘 전이 되는
것이 정상이다. 이것도 조정 값이 아니라 **주말의 길이**다.
"""


@dataclass(frozen=True, slots=True)
class CoverageVerdict:
    """요청 구간 대비 실제 적재 판정.

    Attributes:
        symbol: 종목 코드.
        timeframe: 시간축.
        requested_start: 요청한 시작 (UTC).
        requested_end: 요청한 끝 (UTC).
        stored_start: 실제 적재된 첫 봉. 하나도 없으면 None.
        stored_end: 실제 적재된 마지막 봉. 없으면 None.
        bars: 이 시간축 적재 봉 수.
        reference_bars: **같은 종목**의 기준 시간축 봉 수. 없으면 None.
        reference: 기준 시간축.

    Note:
        `reference_bars` 를 같은 종목에서 가져오는 것이 요점이다. 다른 종목과 비교하면
        휴장일·상장일이 달라 밀도가 의미를 잃는다.
    """

    symbol: str
    timeframe: Timeframe
    requested_start: datetime
    requested_end: datetime
    stored_start: datetime | None
    stored_end: datetime | None
    bars: int
    reference_bars: int | None = None
    reference: Timeframe | None = None
    reference_start: datetime | None = None

    @property
    def head_gap(self) -> timedelta:
        """요청 시작보다 얼마나 늦게 시작하는가. 적재가 없으면 구간 전체."""
        if self.stored_start is None:
            return self.requested_end - self.requested_start
        return max(timedelta(0), self.stored_start - self.requested_start)

    @property
    def tail_gap(self) -> timedelta:
        """요청 끝보다 얼마나 일찍 끝나는가."""
        if self.stored_end is None:
            return self.requested_end - self.requested_start
        return max(timedelta(0), self.requested_end - self.stored_end)

    @property
    def density(self) -> float | None:
        """기준 시간축 대비 봉 수 비율. 기준이 없으면 None.

        Note:
            1.0 에 가까워야 정상이다. 1h 이 15m 의 1/4 이어야 한다는 사실을 이미
            나눗셈에 넣었으므로, 여기서 나오는 값은 **간격 차이를 보정한 뒤**의 비율이다.
        """
        if not self.reference_bars or self.reference is None:
            return None
        ratio = interval(self.timeframe) / interval(self.reference)
        expected = self.reference_bars / ratio
        return self.bars / expected if expected else None

    @property
    def head_covered_by_synthesis(self) -> bool:
        """앞쪽 구멍을 **더 촘촘한 TF 합성으로 덮을 수 있는가**.

        Note:
            🔴 **고칠 수 없는 것에 빨간불을 켜지 않는다.** 토스는 미국 주식의 과거 1h 을
            거의 주지 않는다 (기대 1,489봉에 46봉). 백필을 몇 번 돌려도 안 채워지므로
            그것을 계속 실패로 표시하면 **항상 울리는 경보**가 되고, 그러면 진짜 결손이
            섞여도 아무도 안 본다.

            그런데 측정은 이미 이 경우를 다룬다 — `measure_run.candles_for` 가
            **커버리지가 넓은 쪽**(15m 합성)을 쓴다. 검증이 던져야 할 질문은
            "이 TF 가 DB 에 다 있나"가 아니라 **"측정이 앵커 구간을 볼 수 있나"** 다.

            ⇒ 기준 TF 가 요청 시작을 덮으면 통과시키되 `render()` 에 **합성으로
            덮인다**고 남긴다. 조용히 넘기는 것과 다르다.
        """
        if self.reference_start is None:
            return False
        return self.reference_start <= self.requested_start + HEAD_TOLERANCE

    @property
    def ok(self) -> bool:
        """**측정이 앵커 구간을 볼 수 있는가** — 이것이 게이트다.

        Note:
            밀도는 판정에 넣지 않는다. 합성 경계·조기마감 때문에 정확히 맞지 않을 수
            있고, 임계값을 두면 그것이 새 자유 파라미터가 된다. 대신 `render()` 가 찍는다.
        """
        head_ok = self.head_gap <= HEAD_TOLERANCE or self.head_covered_by_synthesis
        return head_ok and self.tail_gap <= TAIL_TOLERANCE

    @property
    def reason(self) -> str:
        """통과하지 못한 이유. 통과했으면 빈 문자열."""
        if self.stored_start is None and not self.head_covered_by_synthesis:
            return "적재가 하나도 없다"
        parts: list[str] = []
        if self.head_gap > HEAD_TOLERANCE and not self.head_covered_by_synthesis:
            late = self.stored_start.date() if self.stored_start else "없음"
            parts.append(f"시작이 {self.head_gap.days}일 늦다 ({late})")
        if self.tail_gap > TAIL_TOLERANCE:
            parts.append(
                f"끝이 {self.tail_gap.days}일 이르다 ({self.stored_end and self.stored_end.date()})"
            )
        return " · ".join(parts)

    def render(self) -> str:
        """사람이 읽는 한 줄.

        Returns:
            표식(✅/🔴) · 저장 구간 · 수치를 한 줄로.
        """
        mark = "✅" if self.ok else "🔴"
        span = (
            f"{self.stored_start.date()} ~ {self.stored_end.date()}"
            if self.stored_start and self.stored_end
            else "없음"
        )
        density = ""
        if (value := self.density) is not None:
            warn = "  ⚠️ 성기다" if value < 0.9 else ""
            density = (
                f" · 밀도 {value * 100:.0f}%(vs {self.reference and self.reference.value}){warn}"
            )
        note = ""
        if self.head_gap > HEAD_TOLERANCE and self.head_covered_by_synthesis:
            note = (
                f"  ⚠️ DB 는 {self.head_gap.days}일 늦게 시작하지만 "
                f"{self.reference and self.reference.value} 합성이 덮는다 (원천 한계)"
            )
        tail = f"  🔴 {self.reason}" if not self.ok else note
        return (
            f"  {mark} {self.symbol} {self.timeframe.value}: {self.bars:,}봉 {span}{density}{tail}"
        )


class CoverageShortfallError(RuntimeError):
    """요청한 구간을 채우지 못했다.

    Note:
        경고가 아니라 예외인 이유: 백필은 **다음 단계의 입력**을 만드는 작업이고,
        모자란 채로 성공을 보고하면 그 사실이 측정 리포트까지 조용히 흘러간다.
        실제로 미국 주식 1h 이 그렇게 16개월치로 측정됐다 (§1-0t).
    """


def verdict_for(
    symbol: str,
    timeframe: Timeframe,
    requested_start: datetime,
    requested_end: datetime,
    stored_start: datetime | None,
    stored_end: datetime | None,
    bars: int,
    reference_bars: int | None = None,
    reference: Timeframe | None = None,
    reference_start: datetime | None = None,
) -> CoverageVerdict:
    """판정을 만든다 — 시각을 UTC 로 맞춰서.

    Args:
        symbol: 종목 코드.
        timeframe: 검사할 시간축.
        requested_start: 요청 시작.
        requested_end: 요청 끝.
        stored_start: 적재 첫 봉.
        stored_end: 적재 마지막 봉.
        bars: 적재 봉 수.
        reference_bars: 기준 시간축의 봉 수.
        reference: 기준 시간축.
        reference_start: 기준 시간축의 첫 봉. 앞쪽 구멍을 합성으로 덮을 수 있는지
            판정하는 데 쓴다.

    Returns:
        판정.

    Raises:
        ValueError: naive datetime 이 들어온 경우 (절대 규칙 #7).
    """
    for label, value in (
        ("requested_start", requested_start),
        ("requested_end", requested_end),
        ("stored_start", stored_start),
        ("stored_end", stored_end),
    ):
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{label} 가 naive 다 — 타임스탬프는 UTC 여야 한다 (절대 규칙 #7)")
    return CoverageVerdict(
        symbol=symbol,
        timeframe=timeframe,
        requested_start=requested_start.astimezone(UTC),
        requested_end=requested_end.astimezone(UTC),
        stored_start=stored_start.astimezone(UTC) if stored_start else None,
        stored_end=stored_end.astimezone(UTC) if stored_end else None,
        bars=bars,
        reference_bars=reference_bars,
        reference=reference,
        reference_start=reference_start.astimezone(UTC) if reference_start else None,
    )
