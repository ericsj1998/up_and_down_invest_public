"""거래 하나를 차트로 볼 수 있게 굳힌다 — 구조물 + 진입·손절·익절 (P1 §1-0n).

## 왜 필요한가 — 숫자만으로는 "어떻게 들어갔는지"를 모른다

사용자 요구: *"추세선·오더블록·FVG 등 구조물과 분석을 모두 해두고, **진입을 어디서
했는지, 손절 및 익절을 어디서 했는지** 그래프 차트로 직접 볼 수 있게 구성해줘.
**돌파매매를 했는지 확인해봐야겠어.**"*

이행률·기대 R 은 "얼마나 맞았나"를 답하지만 **"어떤 자리에서 들어갔나"** 는 답하지
못한다. 되돌림을 기다려 받은 것과 돌파를 확인하고 따라간 것은 성과가 같아도 완전히
다른 전략이고, 그 구분은 그림으로 보는 것이 가장 빠르다.

## ⚠️ 사람 눈 라벨링이 아니다 (절대 규칙 #11)

이 뷰어는 **판정 도구가 아니라 진단 도구**다. 차트를 보고 정탐/오탐을 라벨링하거나
구간을 손으로 고르면 그것이 §5.6.6 위반이다. 여기서 하는 일은 이미 **코드가 내린
판정**을 그대로 그리는 것이며, 사람이 보는 것은 "룰 명세가 타당한가"이다 — 그 검토는
§5.6.6 이 **필수**라고 못박은 항목이다.

## 그리는 것은 **측정에 실제로 쓰인 것**이어야 한다

구조물을 다시 계산하되 **같은 창·같은 파라미터**로만 계산한다. 조금이라도 다른 창을
쓰면 차트가 측정과 다른 그림을 보여 주고, 그러면 이 도구는 진단이 아니라 착각의
원천이 된다. 그래서 `detected_index` 와 `lookback_bars` 를 함께 받는다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from updown.analysis.structures.bundle import StructureBundle
from updown.analysis.structures.swing import SwingKind
from updown.analysis.structures.trendline import Trendline
from updown.analysis.structures.zone_map import ConfluentZone, TimeframeZone
from updown.common.domain.candle import Candle
from updown.common.domain.setup import TradeSetup

PAD_BARS = 12
"""청산 이후에 더 보여 줄 봉 수 — 청산 직후 가격이 어디로 갔는지 보이게.

손절 직후 반등했는지(손절이 노이즈였다) 계속 빠졌는지(손절이 옳았다)는 **청산 봉에서
끊으면 볼 수 없다**. 이 값은 표시용이라 판정에 전혀 쓰이지 않는다.
"""


@dataclass(frozen=True, slots=True)
class TradeChart:
    """거래 한 건의 차트 데이터.

    Attributes:
        index: 이 실행 안에서의 거래 번호.
        offset: 창 첫 봉의 **전역** 봉 번호. 창 좌표와 전역 좌표를 잇는 유일한 값이다.
        candles: 표시할 봉들 (탐지 창 시작 ~ 청산 + 여유).
        detected_at: 셋업이 확정된 봉 (창 좌표).
        entry_at: 진입 봉 (창 좌표).
        exit_at: 청산 봉 (창 좌표).
        entry: 진입가 (계획 평단).
        stop: 확정 손절가.
        target: 확정 1차 익절가.
        outcome: 판정 결과 문자열.
        realized_r: 실현 R.
        trigger: 진입 트리거 이름 — **돌파인지 되돌림인지 가르는 값**이다.
        depth: 손절 근거의 중첩 깊이.
        fell_back: 합류대를 못 찾아 공식으로 폴백했는가.
        stop_source: 손절 근거 설명.
        target_source: 익절 근거 설명.
        geometry: 탐지 시점의 진입 TF 작도 결과.
        zones: 멀티 TF 수평 띠.
        bands: 합류대.
        setup_box: 셋업 자신의 구조물 박스 (오더블록 등). 없으면 None.
        overlays: 같은 창에서 다른 탐지기가 낸 구조물 (fvg 등).
    """

    index: int
    offset: int
    candles: tuple[Candle, ...]
    detected_at: int
    entry_at: int
    exit_at: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    outcome: str
    realized_r: Decimal
    trigger: str
    depth: int
    fell_back: bool
    stop_source: str
    target_source: str
    geometry: StructureBundle
    zones: tuple[TimeframeZone, ...]
    bands: tuple[ConfluentZone, ...]
    setup_box: tuple[Decimal, Decimal] | None = None
    overlays: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    moving_averages: Mapping[str, Sequence[Decimal | None]] = field(
        default_factory=lambda: dict[str, Sequence[Decimal | None]]()
    )
    rsi14: Sequence[float | None] = field(default_factory=tuple)
    volume_ratio: Sequence[float | None] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """화면이 읽을 dict 로 만든다.

        Returns:
            JSON 직렬화 가능한 dict.

        Note:
            `Decimal` 을 **문자열로** 낸다 (`RunReport.to_dict` 와 같은 이유). 다만
            좌표 계산은 브라우저에서 `Number()` 로 바꿔 쓴다 — 표시 목적이라 이진 오차가
            픽셀에 묻힌다.

            추세선·채널은 **두 끝점의 가격**으로 낸다. 기울기와 절편을 그대로 보내면
            화면이 좌표계를 다시 유도해야 하고, 그 유도가 틀리면 선이 조용히 어긋난다.
        """
        # 창 좌표다 — `geometry` 는 **탐지 창**에서 작도됐고 그 창의 첫 봉이 곧 이
        # 차트의 첫 봉이다 (`window_for`). 선은 **정의역 안에서만** 그린다 (`_extend_to`).
        last = len(self.candles) - 1
        return {
            "index": self.index,
            "offset": self.offset,
            "detected_at": self.detected_at,
            "entry_at": self.entry_at,
            "exit_at": self.exit_at,
            "entry": str(self.entry),
            "stop": str(self.stop),
            "target": str(self.target),
            "outcome": self.outcome,
            "realized_r": str(self.realized_r),
            "trigger": self.trigger,
            "depth": self.depth,
            "fell_back": self.fell_back,
            "stop_source": self.stop_source,
            "target_source": self.target_source,
            "candles": [
                {
                    "ts": candle.ts.isoformat(),
                    "o": str(candle.open),
                    "h": str(candle.high),
                    "l": str(candle.low),
                    "c": str(candle.close),
                }
                for candle in self.candles
            ],
            "setup_box": (
                None
                if self.setup_box is None
                else {"low": str(self.setup_box[0]), "high": str(self.setup_box[1])}
            ),
            "boxes": [
                {
                    "low": str(box.price_range.low),
                    "high": str(box.price_range.high),
                    "kind": "resistance" if box.kind is SwingKind.HIGH else "support",
                    "touches": box.touch_count,
                }
                for box in self.geometry.boxes
            ],
            "trendlines": [line_dict(line, last) for line in self.geometry.trendlines],
            "channels": [
                {
                    "basis": channel.basis.kind.value,
                    **{
                        f"{edge}{point}": str(getattr(channel, edge).price_at(index))
                        for edge in ("lower", "upper")
                        for point, index in (
                            (1, channel.basis.touches[0].index),
                            (2, extend_to(channel.basis, last)),
                        )
                    },
                    "x1": channel.basis.touches[0].index,
                    "x2": extend_to(channel.basis, last),
                    "anchor_x2": channel.basis.touches[-1].index,
                }
                for channel in self.geometry.channels
            ],
            "zones": [
                {
                    "tf": zone.timeframe.value,
                    "low": str(zone.low),
                    "high": str(zone.high),
                    "kind": zone.kind.value,
                    "source": zone.source,
                }
                for zone in self.zones
            ],
            "bands": [
                {
                    "low": str(band.low),
                    "high": str(band.high),
                    "depth": band.depth,
                    "timeframes": [tf.value for tf in band.timeframes],
                    "label": band.describe(),
                }
                for band in self.bands
            ],
            "overlays": [dict(item) for item in self.overlays],
            # 🔴 이평선·RSI·거래량비를 **함께 싣는다.** 지금 이 값들은 계산은 되지만
            #    셋업 판정에 연결돼 있지 않다 (CLAUD.md §1-0i "종합 배선 누락"). 화면에
            #    띄우는 이유가 그것이다 — 안 쓰이고 있다는 사실이 눈에 보여야 한다.
            "ma": {
                name: [None if value is None else str(value) for value in series]
                for name, series in self.moving_averages.items()
            },
            "rsi14": list(self.rsi14),
            "volume_ratio": list(self.volume_ratio),
        }


def extend_to(line: Trendline, last_index: int) -> int:
    """선을 앞으로 어디까지 늘려 그릴지 — **앵커 구간 길이만큼**.

    Args:
        line: 추세선.
        last_index: 차트 마지막 봉 번호.

    Returns:
        연장 끝 봉 번호.

    Note:
        🔴 **차트 끝까지 늘리면 안 된다.** 실측에서 그렇게 그렸다가 53,500원짜리 종목의
        추세선이 **-520,032 / +183,259** 로 나와 11개 중 0개가 화면에 들어왔다.

        원인은 계산 오류가 아니라 **선의 정의역**이다. `detect_trendlines` 는 두 앵커가
        같은 연속 구간(`_segment_index`)에 있을 때만 선을 긋는데, 국내 주식 15m 은 하루
        6.5시간이라 한 구간이 **26봉(하루)** 이다. 하루 안에서 맞춘 기울기를 459봉에
        걸쳐 외삽하면 가격이 발산하는 것이 당연하다.

        ⇒ 연장 폭을 **앵커 구간 길이와 같게** 둔다. 새 상수를 만들지 않으면서 "선이
        존재하는 만큼만 그린다"를 지키는 규칙이며, 표시에만 쓰이고 판정에는 영향이 없다.

        선의 정의역이 하루라는 사실 자체는 **결함이고 축 L 소관**이다
        (`docs/rules/rule_candidates.md`). 여기서는 그 사실이 화면에 **보이게** 만든다 —
        선이 하루치만 그려지면 누구든 즉시 알아본다 (절대 규칙 #8).
    """
    span = line.touches[-1].index - line.touches[0].index
    return min(last_index, line.touches[-1].index + max(1, span))


def line_dict(line: Trendline, last_index: int) -> dict[str, Any]:
    """추세선 하나를 화면용 dict 로 — **앵커 좌표를 함께 낸다**.

    Args:
        line: 추세선.
        last_index: 차트 마지막 봉 번호.

    Returns:
        직렬화용 dict. `x1..anchor_x2` 가 실제 접점 구간이고 `anchor_x2..x2` 가 연장분이다.

    Note:
        x 좌표를 내는 것이 핵심이다. 이전에는 y 값 둘만 보내 화면이 **창 전체**에 걸쳐
        선을 그렸고, 그래서 정의역 밖까지 외삽됐다 (`_extend_to` 참조).
    """
    first = line.touches[0].index
    anchor_end = line.touches[-1].index
    end = extend_to(line, last_index)
    return {
        "kind": line.kind.value,
        "touches": line.touch_count,
        "x1": first,
        "anchor_x2": anchor_end,
        "x2": end,
        "y1": str(line.line.price_at(first)),
        "y_anchor2": str(line.line.price_at(anchor_end)),
        "y2": str(line.line.price_at(end)),
    }


def window_for(
    candles: Sequence[Candle],
    detected_index: int,
    exit_index: int,
    lookback_bars: int,
) -> tuple[int, int]:
    """차트에 실을 봉 구간 `[start, end)`.

    Args:
        candles: 전체 캔들.
        detected_index: 셋업 확정 봉.
        exit_index: 청산 봉.
        lookback_bars: 탐지 창 크기.

    Returns:
        시작·끝 봉 번호.

    Note:
        시작을 **탐지 창의 시작**으로 잡는다. 탐지기가 본 것과 화면이 보여 주는 것이
        같아야 "왜 여기서 잡았나"를 물을 수 있다 — 창보다 짧게 자르면 근거가 된
        스윙이 화면 밖으로 나간다.
    """
    start = max(0, detected_index - lookback_bars + 1)
    end = min(len(candles), exit_index + PAD_BARS + 1)
    return start, end


def setup_box_of(setup: TradeSetup) -> tuple[Decimal, Decimal] | None:
    """셋업 자신의 구조물 박스 — 진입 레벨과 구조 손절 사이.

    Args:
        setup: 탐지가 낸 셋업.

    Returns:
        `(하단, 상단)`. 후보가 없으면 None.

    Note:
        오더블록의 박스를 그대로 들고 다니지 않는 이유는 `TradeSetup` 이 셋업 종류를
        모르는 공용 계약이기 때문이다 (§4.3.1 "플러그인을 추가할 때 공용 타입을 고치지
        않는다"). 대신 **모든 셋업이 갖는 값**으로 근사한다: 구조 손절 ~ 최상단 진입가가
        곧 "가격이 반응할 것으로 본 구역"이다.

        ⚠️ 근사라는 것을 이름과 여기 주석에 남긴다. 정확한 박스가 필요해지면 그것은
        셋업 계약에 필드를 더하는 일이고, 화면을 위해 조용히 지어낼 값이 아니다.
    """
    if not setup.stop_candidates:
        return None
    low = min(candidate.price for candidate in setup.stop_candidates)
    high = max(leg.price for leg in setup.entry_plan)
    return (low, high) if high > low else None
