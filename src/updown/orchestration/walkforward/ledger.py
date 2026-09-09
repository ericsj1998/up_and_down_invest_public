"""매매 기록 — **계획과 실제를 나란히** 남긴다 (T13 ⑧).

## 🔴 결과만 재는 표는 계획 단계 결함에 눈이 없다

사용자가 요구한 항목에 *"진입시 손익비"* 와 *"실제 손익비"* 가 **둘 다** 있고, 그
비율이 **손익비 달성률**이다. 이것이 이 파일의 존재 이유다.

실제로 이런 계획이 나온 적이 있다:

```
RR 174.63   진입 70,577,250 · 손절 70,471,000
```

손절폭이 왕복 비용(0.157%)보다 좁아서 **분모가 부서진** 것인데, 결과만 재는 표에서는
그냥 "RR 이 아주 높은 계획"으로 보인다. 달성률을 같이 재면 *"계획은 174 인데 실제는
0"* 이 되어 결함이 드러난다 (§1-0s 관측 규약).

## 사람과 시스템을 한 원장에 섞지 않는다

`actor` 로 가른다. 사람의 클릭은 **정답지가 아니다** (절대 규칙 #11) — 두 열을 나란히
두는 목적은 채점이 아니라 **차이 목록**을 만드는 것이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast
from uuid import uuid4

from updown.common.domain.evidence import Evidence, Family, Grade
from updown.decision.sizing import MAINTENANCE_MARGIN as SIZING_MAINTENANCE_MARGIN

COST_UNKNOWN = Decimal(0)
"""비용을 못 읽었을 때 쓰는 값.

⚠️ 0 을 **성적에 유리한 쪽으로** 쓰는 것이라, 여기 걸리면 리포트에 표시해야 한다.
지금은 코인만 다루고 `config/costs.yml` 에 UPBIT 가 있으므로 실제로는 안 걸린다.
"""


class Direction(StrEnum):
    """매매 방향.

    Attributes:
        LONG: 사서 오르면 번다.
        SHORT: 팔아서 내리면 번다.

    Note:
        🔴 **절대 규칙 #10(롱 온리)이 2026-08-17 개정됐다.** 숏은 모의 라이브와
        Gate.io 선물 경로에서만 열린다. 현물(업비트)로 숏 계획을 보내면 예외다 —
        현물에는 숏이 없고, 조용히 롱으로 바뀌면 손익 부호가 통째로 뒤집힌다.
    """

    LONG = "롱"
    SHORT = "숏"

    @property
    def sign(self) -> int:
        """손익 부호 — 롱은 +1, 숏은 -1.

        Note:
            ⭐ 방향 분기를 이 한 곳으로 모은다. `if 롱 ... else ...` 를 손익·손절·익절
            세 군데에 흩으면 한 곳만 안 고쳐도 부호가 조용히 틀린다.
        """
        return 1 if self is Direction.LONG else -1


class Actor(StrEnum):
    """이 매매를 누가 냈나.

    Attributes:
        SYSTEM: 플레이북이 자동으로 낸 것.
        HUMAN: 사람이 손으로 누른 것.

    Note:
        🔴 **섞지 않는다.** 사람의 판단으로 룰을 학습시키지 않으며, 두 열을 나란히
        두는 목적은 *"시스템이 왜 이 자리를 놓쳤나"* 를 보는 것뿐이다 (T13 ④).
    """

    SYSTEM = "시스템"
    HUMAN = "사람"
    ADOPTED = "이어받음"
    """거래소에 이미 열려 있던 포지션을 **주워 온** 기록 (2026-08-18).

    🔴 판이 죽으면 원장이 통째로 사라지는데 거래소 포지션은 남는다. 그 상태로는
    반익·본절 상향·손절 재장착이 전부 멈춘다 — 사용자 지적:
    *"이런식으로 연결이 끊겨버리면 1차 익절이나 그런 대응 자체가 불가능하잖아."*

    ⛔ **성과 표본에 섞지 않는다.** 이 기록은 판정으로 만들어진 것이 아니라 거래소에서
    되읽은 것이고, 진입 시점·근거가 우리 것이 아니다. `SYSTEM` 과 같은 열에 세면
    플레이북 성적이 오염된다.
    """


class Outcome(StrEnum):
    """매매가 어떻게 끝났나.

    Attributes:
        PENDING: 주문을 걸어 두고 체결을 기다린다.
        OPEN: 체결돼 보유 중.
        TAKE_PROFIT: 익절.
        STOP_LOSS: 손절.
        CANCELLED: 체결 전에 계획이 사라져 주문을 거뒀다.

    Note:
        🔴 **라벨은 "무슨 일이 있었나" 이고 승패는 돈이 정한다** (사용자 확정
        2026-08-17). 반익 뒤 본절을 `STOP_LOSS` 로 적었더니 **승률 20% 인데 손익
        +2.23%** 가 나왔다 — 라벨로 승패를 세면 이런 어긋남이 계속 생긴다.

        ⇒ `win_rate` 는 `gain_pct > 0` 으로 센다. 라벨은 경로를 설명할 뿐이다.

        🔴 **`PENDING` 이 있어야 화면이 정직하다.** 예전에는 체결돼야 항목이 생겨서,
        시스템이 자리를 잡고 지정가를 걸어 둔 구간이 화면에서 **아무 일도 없는 것**처럼
        보였다 — 사용자 지적: *"뭐가 됐던 주문 진입하면 바로 아이템이 생겨야 한다."*

        ⛔ `PENDING`·`OPEN`·`CANCELLED` 는 승패에 세지 않는다. 특히 **취소를 손절로
        세면 안 된다** — 잃은 돈이 없다.
    """

    PENDING = "대기"
    OPEN = "보유중"
    TAKE_PROFIT = "목표 익절"
    SIGNAL_EXIT = "전환 익절"
    HALF_BREAKEVEN = "반익반본"
    LEVEL_EXIT = "레벨 이탈"
    """뚫린 레벨(새 지지/저항)을 몸통이 다시 잃어 나온 청산 (T44).

    전환 익절과 **따로** 적는 이유: 같은 라벨이면 "캔들 색에 끊긴 것"과 "구조가 깨져
    나온 것"이 분해에서 뭉개진다. 집계는 문자열 Counter 라 새 값이 조용히 빠지지 않는다.
    """
    CONFIRM_FAIL = "확인 실패"
    """리테스트 체결 뒤 **첫 진입 TF 마감**이 띠 안으로 돌아와 바로 나온 청산 (T46).

    실측 2026-08-22: 첫 마감이 띠 안이면 이행 24.4%(45건) — 들고 있을 이유가 없다.
    🔴 `Ledger.closed` 에 같이 넣는다 — 빠지면 잔고가 거짓말한다 (같은 날 LEVEL_EXIT 사고).
    """
    STOP_LOSS = "손절"
    LIQUIDATED = "강제청산"
    CANCELLED = "취소"


MAINTENANCE_MARGIN = SIZING_MAINTENANCE_MARGIN
"""유지증거금률 — 청산가 계산에 쓴다 (0.5%).

⭐ Gate.io 무기한 선물 BTC 하위 티어의 표준값대다. 티어별로 오르지만 우리가 다루는
규모는 최저 티어에 있다.

```
롱  청산가 = 진입 x (1 - 1/L + mmr)
숏  청산가 = 진입 x (1 + 1/L - mmr)
```

⚠️ L=1 이면 청산가가 진입의 0.5% 라 사실상 없다 — 현물에 청산이 없는 것과 맞는다.

⛔ 성과를 보고 조정하지 않는다. 거래소가 정하는 값이며, Gate.io 연동 때 실제 티어
표로 교체한다.
"""


def liquidation_price(
    entry: Decimal, leverage: Decimal, *, long: bool, remaining: Decimal = Decimal(1)
) -> Decimal | None:
    """강제 청산가.

    Args:
        entry: 진입가.
        leverage: 배율.
        long: 롱이면 True.
        remaining: 남은 수량 비율. 반익 뒤면 0.5.

    Returns:
        청산가. 배율이 1 이하라 청산이 성립하지 않으면 None.

    Note:
        🔴 **격리 마진 기준이다.** 교차 마진이면 계좌 전체가 담보라 청산가가 훨씬
        멀지만, 그러면 한 포지션이 계좌를 통째로 날릴 수 있어 모의에서 다루기 위험하다.

        🔴 **반익하면 청산가가 멀어진다.** 수량은 절반이 되는데 증거금은 그대로이므로
        견딜 수 있는 폭이 두 배가 된다:

        ```
        여유 = (1/배율 - 유지증거금) / 남은 비율
        ```

        ⛔ 한때 `remaining` 없이 **진입 수량 기준**으로 잡고 *"보수적이라 괜찮다"* 고
        뒀다가 고쳤다. 125배에서는 무해하지 않았다 — 청산 거리가 0.3% 라 반익 뒤
        본절 스탑(0%)보다 **먼저** 걸렸고, 그래서 이익을 낸 매매가 `강제청산` 으로
        끝나면서 손익이 `(1차익절 + 청산가)/2` 로 섞여 **+167%** 같은 값이 나왔다
        (사용자 발견 2026-08-17).
    """
    if leverage <= 1 or remaining <= 0:
        return None
    room = (Decimal(1) / leverage - MAINTENANCE_MARGIN) / remaining
    if room <= 0:
        return None
    return entry * (Decimal(1) - room) if long else entry * (Decimal(1) + room)


class HalfBy(StrEnum):
    """**반익이 왜 일어났나** — 라벨 하나에 섞여 있던 두 경로를 가른다.

    Attributes:
        TARGET: 1차 익절 가격에 닿아서 (계획대로).
        SIGNAL: 하락 전환 신호(3연속 음봉 · 하락 장악)를 보고 (문서 ⑤).

    Note:
        🔴 **사용자 의심이 맞았다** (2026-08-17):

        > *"설마 1차 익절까지 가다가 반익반본하는 이유가, 추세전환으로 인한 3틱 음봉이나
        > 장악캔들에 걸려서 터는건가?"*

        `반익반본` 이라는 한 라벨이 *"계획한 1차 익절에 닿고 본절로 돌아왔다"* 와
        *"전환 신호에 절반을 덜고 본절로 돌아왔다"* 를 **똑같이** 적고 있었다. 둘은
        전혀 다른 사건이고, 후자가 많다면 그것은 **전환 신호가 너무 예민하다**는 뜻이지
        박스가 얇다는 뜻이 아니다.

        ⛔ 라벨을 뭉개면 원인을 못 가른다 — 관측 규약(§1-0s)이 말하는 그대로다.
    """

    TARGET = "1차 익절"
    SIGNAL = "전환 신호"


def plan_fault(record: TradeRecord) -> str:
    """계획 기하가 **성립하는지** 본다.

    Args:
        record: 검사할 매매 기록.

    Returns:
        어긋난 이유. 성립하면 빈 문자열.

    Note:
        🔴 **이 검사가 없어서 4건이 -4.48% 로 끝났다** (2026-08-18 실측). 롱인데
        1차 익절(64425.2)이 진입(64441.7)보다 **아래**였다 — 이익을 내려면 가격이
        내려가야 하는 익절이다. 기하가 뒤집혔으면 그 계획은 **매매가 아니다.**

        성립 조건:

            롱   손절 < 진입 < 1차익절 <= 목표
            숏   목표 <= 1차익절 < 진입 < 손절

        ⚠️ `<=` 인 이유 — 사다리가 한 칸이면 1차와 목표가 같을 수 있다. 그것은 정상이다.

        🔴 **본절은 예외다** (2026-08-20). 반익이 발동하면 손절이 진입가로 올라가
        `손절 == 진입` 이 되는데, 그것은 **매매법이 시킨 정상 상태**다. 그런데 이
        검사가 매 걸음 *"롱인데 손절이 진입 위다"* 라고 외쳤고, 사람이 진짜 이상과
        구별할 수 없게 만들었다 (자가 점검이 늘 붉으면 아무도 안 본다).

        ⇒ `half_at` 이 있으면 같은 값을 허용한다. 새 계획에는 `half_at` 이 없으므로
          진입 전 검사는 그대로 엄하다.

        🔴 **원장에 있는 이유** (2026-08-19 사고 ⑦). 러너에 있던 시절에는 세션이 못
        불렀고 — 러너가 세션을 import 하므로 — 검사가 **기록을 만든 뒤**에 돌았다.
        그러면 주문만 막히고 원장에는 **보유중인데 아무도 안 가진** 기록이 남는다.
        실측에서 자가 점검이 *"롱인데 손절이 진입 위다"* 로 외친 것이 그 잔해다.

        ⇒ 이제 세션이 **기록을 만들기 전에** 부른다. 성립 안 하면 그 자리는 없다.
    """
    long = record.direction is Direction.LONG
    entry = record.entry
    stop = record.planned_stop
    first = record.planned_first
    target = record.planned_target
    # ⭐ 반익이 지나갔으면 손절이 진입가까지 올라와 있을 수 있다 (본절).
    even = record.half_at is not None
    if long:
        if not (stop <= entry if even else stop < entry):
            return f"롱인데 손절({stop})이 진입({entry}) 위다"
        if not entry < first:
            return f"롱인데 1차 익절({first})이 진입({entry}) 아래다"
        if not first <= target:
            return f"롱인데 목표({target})가 1차 익절({first}) 아래다"
        return ""
    if not (entry <= stop if even else entry < stop):
        return f"숏인데 손절({stop})이 진입({entry}) 아래다"
    if not first < entry:
        return f"숏인데 1차 익절({first})이 진입({entry}) 위다"
    if not target <= first:
        return f"숏인데 목표({target})가 1차 익절({first}) 위다"
    return ""


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """매매 한 건 (T13 ⑧ 항목 그대로).

    Attributes:
        trade_id: 로그 ID.
        playbook: 성과 귀속 키 (`sample_ma_cross@0.1.0`).
        actor: 시스템인가 사람인가.
        direction: 롱인가 숏인가.
        placed_at: 주문을 건 시각.
        opened_at: 체결 시각. 아직 대기면 None.
        closed_at: 청산 시각. 보유 중이면 None.
        entry: 실제 평단.
        exit_price: 청산가. 보유 중이면 None.
        planned_stop: 진입 시점에 선언한 손절가.
        planned_first: 진입 시점에 선언한 **1차 익절가** (반익).
        planned_target: 진입 시점에 선언한 **2차 익절가** (목표).
        outcome: 결과.
        half_at: 1차 익절 체결 시각. 아직이면 None.
        cost_pct: 왕복 비용 비율. 실현 수익에서 뺀다.
        leverage: 레버리지 배율. 손익률에 그대로 곱한다.
        evidence: **왜 들어갔는가** — 탐지기가 낸 근거 그대로 (T16 ①).
        note: 왜 이렇게 끝났나. **취소는 이유가 없으면 읽을 수 없다.**

    Note:
        🔴 **계획값을 진입 시점에 박아 둔다.** 나중에 레벨이 바뀌어도 이 값은 안
        움직인다 — 움직이면 달성률이 언제나 100% 가 되어 아무것도 못 잰다.
    """

    trade_id: str
    playbook: str
    actor: Actor
    placed_at: datetime
    entry: Decimal
    opened_at: datetime | None = None
    planned_stop: Decimal = Decimal(0)
    planned_target: Decimal = Decimal(0)
    planned_first: Decimal = Decimal(0)
    direction: Direction = Direction.LONG
    outcome: Outcome = Outcome.PENDING
    closed_at: datetime | None = None
    exit_price: Decimal | None = None
    half_at: datetime | None = None
    cost_pct: Decimal = COST_UNKNOWN
    leverage: Decimal = Decimal(1)
    hold_level: Decimal | None = None
    """**뚫린 레벨** — 돌파 매매만 든다 (T44). 청산이 캔들 색 대신 이 값을 본다.

    진입 시점의 셋업(`TradeSetup.hold_level`)에서 그대로 옮긴다. 비어 있으면 레벨 보유
    청산의 대상이 아니다 — 박스권 왕복과 옛 기록이 그렇다.
    """
    confirmed: bool | None = None
    """체결 뒤 첫 진입 TF 마감이 추세 쪽이었나 (T46). None = 아직 안 봤다 · 옛 기록.

    한 번만 판정한다 — 그래서 값이 `None` 에서 `True/False` 로 한 번 바뀌고 끝이다.
    """
    funding_paid: Decimal = Decimal(0)
    """거래소가 뗀 펀딩 누적 (USDT · 양수 = 냈다) — 라이브가 정산 기록에서 붙인다 (T226)."""
    funding_pct: Decimal = Decimal(0)
    """펀딩 누적을 **명목 대비 비율**로 (양수 = 비용) — `gain_pct` 가 `cost_pct` 처럼 뺀다.

    라이브는 정산액/명목, 모형(백테스트·페이퍼)은 정산 경계마다 요율을 더한다 (T226). 옛 기록은 0.
    """
    realized_adjust: Decimal = Decimal(0)
    """재레버로 **줄이며** 그 자리에서 실현된 손익 누적 (USDT · 부호째 · T229 · 0116).

    거래소는 감축 계약의 손익을 즉시 적는데 원장은 청산까지 안 셌다 (NEAR 29→26 · BTC 3→1 실측).
    `_walk` 가 이 값을 그 매매의 손익에 더한다 — 원장 실현 = 거래소 실현. 옛 기록은 0.
    """
    fee_actual: Decimal | None = None
    """거래소가 실제로 뗀 수수료 (USDT · 양수 · 왕복 합) — 라이브가 청산 이력에서 붙인다 (T236).

    붙이면서 `cost_pct` 를 **실제 비율**(수수료 / 진입 명목)로 바꾼다 — 원장 산식은 그대로고
    입력만 실측이 된다.
    None 이면 아직 모형 비용(costs.yml 테이커 왕복)이다. 백테스트는 늘 None.
    """
    funding_keys: tuple[str, ...] = ()
    """이미 붙인 정산 열쇠들(`시각:변화`) — 재시작이 같은 정산을 다시 붙이지 않게 (T226 · 0114).

    메모리에만 두면 블루그린 승격마다 비어서 열린 구간의 정산이 전부 다시 붙는다 (2026-09-08 실측:
    원장이 거래소 합의 2.7배). 비어 있는데 `funding_paid` 가 0 이 아니면 그 시절 기록이라 러너가
    첫 동기화에서 거래소 합으로 다시 맞춘다.
    """
    entry_fills: tuple[tuple[Decimal, Decimal], ...] = ()
    """**무엇이 얼마나 채워졌나** — `((가격, 비중), ...)` (T19 ①).

    🔴 **사다리 진입의 준비물이다.** 계획(`plan_for`)은 진입을 두 다리로 만드는데
    — 1차는 지지 띠 위쪽, 2차는 아래쪽 — 원장에 진입가 칸이 하나뿐이라 그것을
    **적을 수가 없어서 통째로 버려지고** 있었다. 그래서 시장가 한 방으로 사고,
    방아쇠 조건상 그 봉의 가장 나쁜 가격에 샀다.

    ⭐ **비어 있으면 다리 하나짜리다** (`entry` 에 전액). 0.1 과 지금까지의 모든
    기록이 그 모양이며, 값이 한 비트도 안 달라진다 (§5.6.2 동결).

    ⚠️ **비중은 계획 대비다.** 절반만 채워지면 `(가격, 0.5)` 하나이고, 그 매매는
    계획의 절반만 들어간 것이다 — 손익도 절반이어야 한다 (`filled_ratio`).
    """

    half_by: HalfBy | None = None
    half_price: Decimal | None = None
    """**얼마에** 절반을 덜었나 (2026-08-19 사고 ③).

    🔴 **없어서 계획가로 대신 쓰고 있었다.** `half_at`(언제)·`half_by`(왜)는 있는데
    이 값이 없어서 `exit_average` 가 반익 절반을 **언제나 `planned_first`** 로 쳤다.
    그런데 전환 신호 반익은 그 가격에 **닿은 적이 없다** — 실측 28건이 전부 그랬고,
    같은 표본이 원장에서는 28전 28승 +2.255%, 실제 청산가로는 1승 -3.725% 였다.

    ⚠️ **거래소 체결가가 아니라 원장이 본 가격**이다 (`entry` 와 같은 규칙). 거래소
    응답으로 원장을 되돌리면 결정론 코어가 계좌 상태에 따라 달라진다 (절대 규칙 #5) —
    체결가와의 차이는 슬리피지이고 `price_drift` 감사가 따로 잰다.
    """
    evidence: tuple[Evidence, ...] = ()
    """**왜 들어갔는가** — 탐지기가 낸 근거를 진입 시점에 그대로 박는다 (T16 ①).

    🔴 **근거는 거래소에 없다.** 진입가·손절·익절은 포지션과 주문에서 되읽을 수 있지만
    (`LiveRunner.adopt`), *"어떤 박스의 어떤 방아쇠였나"* 는 원장에만 있었다. 여기서
    안 적으면 판이 죽는 순간 **영영 복구할 수 없고**, 없을 때 돌아간 매매를 복기할
    방법이 사라진다 — T16 이 이것을 선행 조건으로 못박은 이유다.

    ⚠️ **등급의 부호는 이 매매의 방향이 아니다.** 탐지기가 숏 셋업에도 `PRIMARY`(+2)를
    싣는다 — 등급은 *근거가 얼마나 확인됐나*이고 방향은 `direction` 이 말한다.

    ⛔ 비어 있는 것이 곧 결함은 아니다. 이어받은 기록(`Actor.ADOPTED`)과 사람이 손으로
    낸 기록에는 근거가 **원래 없다**. 화면이 그 둘을 갈라 보여 준다.
    """
    note: str = ""

    @property
    def planned_rr(self) -> Decimal | None:
        """진입시 손익비 — 계획한 보상 ÷ 계획한 리스크.

        Returns:
            손익비. 손절폭이 0 이하면 None (계획이 성립하지 않는다).
        """
        risk = (self.entry - self.planned_stop) * self.direction.sign
        reward = (self.planned_target - self.entry) * self.direction.sign
        return None if risk <= 0 else reward / risk

    def __post_init__(self) -> None:
        """진입가가 채워진 것들의 **가중 평단인지** 확인한다.

        Raises:
            ValueError: `entry` 가 `entry_fills` 의 평단과 다른 경우.

        Note:
            🔴 **저장한 값과 사실이 갈리는 것을 구조로 막는다** (2026-08-19 사고 ③).
            그때는 `exit_average` 가 반익을 **계획가**로 계산해 28건을 전부 이긴
            매매로 만들었다 — 사실이 아닌 값을 들고 있었기 때문이다.

            여기서는 `entry` 를 계산값으로 두고 싶지만, 그러면 기존 생성자 전부가
            바뀐다. ⇒ **값은 그대로 받되 어긋나면 터뜨린다.** 조용히 다른 것보다
            시끄럽게 못 만드는 편이 낫다 (절대 규칙 #8).
        """
        if not self.entry_fills:
            return
        weight = sum((ratio for _, ratio in self.entry_fills), Decimal(0))
        if weight <= 0:
            raise ValueError(f"진입 비중이 {weight} 다 — 채워진 것이 없으면 매매가 아니다")
        average = sum((price * ratio for price, ratio in self.entry_fills), Decimal(0)) / weight
        # ⚠️ 나눗셈이라 정확히 안 떨어질 수 있다. 상대 오차로 본다.
        if abs(average - self.entry) > abs(self.entry) * Decimal("1e-9"):
            raise ValueError(
                f"진입가 {self.entry} 가 채워진 것들의 평단 {average} 과 다르다 — "
                "둘 중 하나는 사실이 아니다"
            )

    @property
    def filled_ratio(self) -> Decimal:
        """계획 대비 **얼마나 채워졌나** (0~1).

        Returns:
            비중 합. 다리가 없으면 1 (전액).

        Note:
            🔴 **돈이 이 값을 쓴다.** 절반만 채워진 매매는 증거금도 절반만 들어간
            것이므로 손익도 절반이다 — 전액으로 세면 **있지도 않은 자본으로 낸
            수익**이 된다 (§1-0s 와 같은 종류의 거짓말).
        """
        if not self.entry_fills:
            return Decimal(1)
        return sum((ratio for _, ratio in self.entry_fills), Decimal(0))

    @property
    def exit_average(self) -> Decimal | None:
        """실현 평균 청산가 — **반익절을 반영한다**.

        Returns:
            절반은 1차 익절가, 절반은 최종 청산가로 가중한 평균. 아직이면 None.

        Note:
            🔴 **반익을 안 세면 성과가 통째로 거짓이 된다.** 1차 익절에 닿아 절반을
            덜었는데 나머지가 본절로 돌아오면 실제로는 이익인데, 최종가만 보면
            본전으로 기록된다 — 실측에서 1차·2차 익절선에 다 닿았는데 "보유중" 으로
            남아 있던 것이 이 결함의 앞단이었다.
        """
        if self.exit_price is None:
            return None
        if self.half_at is None:
            return self.exit_price
        # 🔴 **어느 가격에 덜었는지는 `half_price` 만 안다** (2026-08-19 사고 ③).
        #    계획가로 떨어지는 것은 옛 기록(그 칸이 없던 것)뿐이며, 그때도 목표 도달
        #    반익이면 계획가가 곧 체결가라 옳다. 틀린 것은 **전환 신호** 쪽이었다.
        taken = self.planned_first if self.half_price is None else self.half_price
        return (taken + self.exit_price) / Decimal(2)

    @property
    def realized_rr(self) -> Decimal | None:
        """실제 손익비 — 실현 손익 ÷ 계획한 리스크.

        Returns:
            손익비. 아직 안 끝났거나 계획이 성립하지 않으면 None.

        Note:
            🔴 **분모는 계획 리스크다.** 실제 손실로 나누면 손절 한 건의 R 이 항상
            -1 이 되어 *"계획보다 깊게 밀렸다"* 를 못 본다.

            ⭐ 분자는 **반익 반영 평균**이다 (`exit_average`).
        """
        risk = (self.entry - self.planned_stop) * self.direction.sign
        average = self.exit_average
        if average is None or risk <= 0:
            return None
        return (average - self.entry) * self.direction.sign / risk

    @property
    def achievement(self) -> Decimal | None:
        """손익비 달성률 — 실제 ÷ 계획.

        Returns:
            달성률. 계획 손익비가 없거나 아직 안 끝났으면 None.

        Note:
            🔴 **이 표의 핵심이다.** 계획 RR 이 높아도 달성률이 낮으면 그 계획은
            *"닿지 못할 목표"* 를 적어 둔 것이다. 실제로 RR 174 짜리 계획이 나온 적이
            있는데, 그것은 성과가 아니라 **분모가 부서졌다는 신호**였다.

            ⚠️ 손절이면 음수다. 평균을 낼 때 부호를 뭉개지 않는다.
        """
        planned = self.planned_rr
        realized = self.realized_rr
        if planned is None or realized is None or planned == 0:
            return None
        return realized / planned

    @property
    def gain_pct(self) -> Decimal | None:
        """이득률 — **비용을 뺀** 실현 수익률.

        Returns:
            비율(%). 아직 안 끝났으면 None.

        Note:
            🔴 비용을 안 빼면 왕복 0.157% 를 못 갚는 매매가 이긴 것으로 세어진다.
            5m 단독 진입이 그 함정에 걸렸었다.

            🔴 **강제청산은 -100% 다** (사용자 확정 2026-08-17):

            > *"마이너스를 허락하는 청산 방법이 아니라, 일단 청산되고 그 다음에 다시
            > 자본금을 채워서 재주문 진입하는 식으로 하는 전략인거야."*

            지갑 전체가 그 포지션의 증거금이므로 청산되면 **지갑이 빈다.** 가격으로
            계산하면 유지증거금이 남은 것처럼 나와(125배에서 -57%) 실제보다 덜 잃은
            것이 된다 — 거래소는 그 나머지를 돌려주지 않는다.

            ⚠️ 반익으로 이미 챙긴 이익도 같이 사라진다. 그 이익은 **같은 지갑**으로
            들어가 다음 포지션의 증거금이 되어 있었기 때문이다.
        """
        if self.outcome is Outcome.LIQUIDATED:
            return Decimal(-100)
        average = self.exit_average
        if average is None or self.entry <= 0:
            return None
        raw = (average - self.entry) * self.direction.sign / self.entry * Decimal(100)
        # ⭐ 펀딩(T226) — 보유가 길수록 는다. 명목 대비 비율이라 cost_pct 와 같은 자리에서 뺀다.
        return (raw - (self.cost_pct + self.funding_pct) * Decimal(100)) * self.leverage

    def closed(
        self,
        *,
        at: datetime,
        price: Decimal,
        outcome: Outcome,
        cost_pct: Decimal | None = None,
    ) -> TradeRecord:
        """청산된 사본.

        Args:
            at: 청산 시각.
            price: 청산가.
            outcome: 익절인가 손절인가.
            cost_pct: **청산 유형으로 다시 센 왕복 비용** (T42 ④). 안 주면 진입 때
                적은 값 그대로다 — 동결 경로는 한 비트도 안 달라진다.

        Returns:
            새 기록. 원본은 안 바꾼다.

        Raises:
            ValueError: `OPEN` 으로 닫으려는 경우.
        """
        if outcome in (Outcome.OPEN, Outcome.PENDING):
            raise ValueError(f"청산인데 결과가 {outcome.value} 일 수 없다")
        return TradeRecord(
            trade_id=self.trade_id,
            playbook=self.playbook,
            actor=self.actor,
            direction=self.direction,
            placed_at=self.placed_at,
            opened_at=self.opened_at,
            entry=self.entry,
            # ⚠️ **다리를 잃지 않는다.** 여기서 빠뜨리면 청산된 순간 평단의 근거가
            #    사라지고 `filled_ratio` 가 1 로 돌아가 손익이 두 배가 된다.
            entry_fills=self.entry_fills,
            planned_stop=self.planned_stop,
            planned_target=self.planned_target,
            planned_first=self.planned_first,
            outcome=outcome,
            closed_at=at,
            exit_price=price,
            half_at=self.half_at,
            cost_pct=self.cost_pct if cost_pct is None else cost_pct,
            leverage=self.leverage,
            hold_level=self.hold_level,
            confirmed=self.confirmed,
            # ⚠️ **반익 사유를 안 옮기면 청산되는 순간 사라진다** — 원장에 남는 것은
            #    이 사본이고, 그러면 표에서 두 경로가 다시 뭉개진다.
            half_by=self.half_by,
            # 🔴 **어느 값에 덜었는지도 옮긴다** (사용자 신고 2026-08-20).
            #
            #    이 한 줄이 빠져서 **닫는 순간 반익 체결가가 사라졌다.** 그러면
            #    `exit_average` 가 계획가로 떨어지는데, 그 폴백은 *"그 칸이 없던 옛
            #    기록"* 을 위한 것이지 지금 기록을 위한 것이 아니다:
            #
            #      2d74cd  숏 진입 1.22000 청산 1.22010  실제 반익 1.21790
            #              계획가 1.13722 로 떨어져  →  화면 **+64.77%**
            #              실제 값                    →        **-1.36%**
            #
            #    ⚠️ 닫힌 매매 **37건 전부**가 그 상태였다 (실측). 반익이 난 매매의
            #      성적표가 통째로 부풀려져 있었고, `원장 +28.99 vs 거래소 -24.13` 로
            #      부호가 갈린 것도 여기서 나왔다.
            half_price=self.half_price,
            # ⚠️ **근거도 옮긴다** — 바로 위 `half_by` 와 똑같은 이유다. 원장에 남는
            #    것은 이 사본이므로, 여기서 빠지면 청산되는 순간 "왜 들어갔는지"가
            #    사라진다. 그러면 **끝난 매매만** 근거가 없어져 더 나쁘다 (복기 대상은
            #    보유 중인 것이 아니라 끝난 것이다).
            evidence=self.evidence,
            note=self.note,
        )


def new_trade_id() -> str:
    """로그 ID — 짧고 눈으로 구분되는 형태.

    Returns:
        uuid4 앞 12자리 hex.
    """
    return uuid4().hex[:12]


EvidenceRow = dict[str, str | int | None]
"""근거 한 줄의 직렬화 모양 — 저널과 화면이 **같은 것**을 읽는다."""


def evidence_rows(items: Sequence[Evidence]) -> list[EvidenceRow]:
    """근거를 저널·화면이 함께 쓰는 한 가지 모양으로 편다 (T16 ①).

    Args:
        items: 진입 시점에 박아 둔 근거들.

    Returns:
        JSON 으로 바로 나가는 줄 목록.

    Note:
        🔴 **모양을 한 곳에 둔다.** 저널(`Session.journal`)과 화면(`_record`)이 각자
        펴면 한쪽만 필드를 늘렸을 때 *"화면에는 있는데 저장된 판에는 없는 값"* 이 생긴다
        — 반익 시각·사유가 실제로 그랬다.

        ⚠️ `grade` 를 이름이 아니라 **정수**로 낸다. `IntEnum` 이라 크기 비교가 뜻을
        갖고, 이름(`MEDIUM_BULL`)은 방향까지 말해 버려 숏 매매에서 오해를 부른다.
    """
    return [
        {
            "source": item.source,
            "family": item.family.value,
            "grade": int(item.grade),
            "detail": item.detail,
            "price": None if item.price is None else str(item.price),
        }
        for item in items
    ]


def evidence_from_rows(rows: Sequence[object]) -> tuple[Evidence, ...]:
    """저장된 줄을 근거로 되돌린다 — `evidence_rows` 의 역 (T16 ②).

    Args:
        rows: 저널·DB 에서 읽은 줄들.

    Returns:
        근거들. 모양이 다른 줄은 **건너뛴다**.

    Note:
        ⚠️ **여기서는 터지지 않는다.** 근거는 되짚기용이라, 옛 판에 근거가 없거나 모양이
        달라도 그 판을 이어받는 것 자체는 되어야 한다 — 못 이어받으면 거래소 포지션이
        고아가 되고, 그것이 훨씬 나쁘다.

        ⛔ 다만 **조용히 지어내지도 않는다.** 읽을 수 없는 줄은 그냥 없는 것이 된다.
    """
    made: list[Evidence] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = cast("dict[str, object]", row)
        family = item.get("family")
        grade = item.get("grade")
        if not isinstance(family, str) or not isinstance(grade, int):
            continue
        try:
            price = item.get("price")
            made.append(
                Evidence(
                    source=str(item.get("source", "")),
                    family=Family(family),
                    grade=Grade(grade),
                    detail=str(item.get("detail", "")),
                    price=None if price is None else Decimal(str(price)),
                )
            )
        except (ValueError, ArithmeticError):
            continue
    return tuple(made)


class Funding(StrEnum):
    """**돈이 어디서 오는가** — 두 모형을 이름으로 가른다 (T14-1).

    Attributes:
        SEED_REFILL: 잔고가 시드 아래로 가면 시드까지 채운다. 밖에서 넣는다고 **가정**한다.
        WALLET: 지갑(거래소 잔액)에서만 채운다. 모자라면 **멈춘다**.

    Note:
        🔴 **두 모형을 한 표에 넣지 않는다.** 재투입 규칙이 다르므로 누적 손익률의
        정의가 다르고, 섞으면 **모형 차이가 전략 차이로 읽힌다** (§1-0s 관측 규약).
        그래서 값 옆에 늘 이 이름을 적는다.

        ⛔ **`SEED_REFILL` 은 라이브에서 거짓말이다.** 아무도 입금하지 않는데 원장만
        채워지므로, 청산 뒤에 원장과 계좌가 **가장 크게** 갈린다. 실측 (2026-08-18):

        ```
        원장 cash      1000.00   시드까지 스스로 채웠다
        원장 refilled   200.00   밖에서 넣었다고 가정한 돈
        거래소 실제     800.00   실제로 남은 것
        → 원장 기준 주문 300계약(1000 USDT) 이 잔고를 넘어 거부됐다
        ```

        ⭐ **그런데 `SEED_REFILL` 을 지우지 않는다.** 사용자가 확정한 백테스트 모형이고
        (*"시드가 천만원인데 백만원 손해 놨을 때 재투자 금액으로 백만원을 더한다"*),
        지우면 4년치 실측을 **다시 낼 수조차 없다**. 라벨을 붙여 함께 둔다.
    """

    SEED_REFILL = "시드 충전"
    WALLET = "지갑"


@dataclass(frozen=True, slots=True)
class Purse:
    """원장이 계산한 **돈의 상태** — 한 번 걸어간 결과.

    Attributes:
        rolling: 굴리는 돈. `SEED_REFILL` 에서는 잔고, `WALLET` 에서는 증거금.
        added: **밖에서** 넣었다고 가정한 총액. `WALLET` 에서는 늘 0 이다.
        reserved: 유보로 빼 둔 총액.
        drawn: 금고에서 꺼내 쓴 총액.
        topped_up: **지갑에서 증거금으로** 채워 넣은 총액 (`WALLET` 전용).
        wallet: 지갑에 남은 돈 (`WALLET` 전용).
        earned: 끝난 매매들이 실제로 **번(잃은) 금액의 합**.
        halted_at: 증거금을 못 채워 멈춘 매매의 id. 안 멈췄으면 None.
        drawdown_pct: 지금 고점 대비 낙폭(%). **이 판이 번 돈**(증거금 + 누적 손익) 기준 (T22).
        max_drawdown_pct: 걸어오는 동안 가장 깊었던 낙폭(%).
        tripped_at: 낙폭이 브레이커 문턱에 닿은 매매의 id. 안 닿았으면 None.

    Note:
        🔴 **`halted_at` 이 있으면 그 뒤 매매는 세지 않았다.** 돈이 없어 못 했을 매매를
        성적에 넣으면 *"있지도 않은 자본으로 낸 수익"* 이 된다.
    """

    rolling: Decimal
    added: Decimal = Decimal(0)
    reserved: Decimal = Decimal(0)
    drawn: Decimal = Decimal(0)
    topped_up: Decimal = Decimal(0)
    wallet: Decimal = Decimal(0)
    earned: Decimal = Decimal(0)
    """🔴 **손익률의 합이 아니라 금액의 합이다** (2026-08-20).

    감사가 원장과 거래소를 대조할 때 *"지금 증거금 x 손익률 합"* 으로 셌는데, 증거금은
    매매마다 달랐다 — 벌면 커지고 청산나면 채워진다. 그래서 오래 돈 판일수록 원장 쪽
    숫자가 부풀었고, 실제로 `원장 +94.26 vs 거래소 -10.19` 를 냈다.

    ⇒ 굴러가는 그 자리에서 **그때의 증거금으로** 센 값을 여기 쌓는다.
    """
    halted_at: str | None = None
    drawdown_pct: Decimal = Decimal(0)
    max_drawdown_pct: Decimal = Decimal(0)
    tripped_at: str | None = None


@dataclass(slots=True)
class Ledger:
    """세션 하나의 매매 원장.

    Attributes:
        seed_cash: 시작 금액 (T13 ①, 기본 1,000만원).
        records: 매매 기록들. 진입 순서를 지킨다.

    Note:
        ⚠️ 수량·복리를 계산하지 않는다. **한 번에 한 포지션, 전액**을 전제로 손익률만
        곱해 나간다 — 수량 산정은 RiskManager 소관이고(절대 규칙 #4), 여기서 흉내내면
        SSoT 가 둘이 된다.
    """

    seed_cash: Decimal = Decimal(10_000_000)
    leverage: Decimal = Decimal(1)
    """레버리지 배율 (T13 · 사용자 요구 2026-08-17).

    🔴 **손익률에 그대로 곱한다.** 5배면 손절 -1% 가 -5% 가 된다.

    ✅ 강제청산을 센다 (`liquidation_price`) — 격리 마진 · 유지증거금 0.5%.
    """

    margin_budget: Decimal | None = None
    """**굴리는 돈(증거금)** — 주문 크기를 정하는 예산 (T14-3 · 사용자 확정 2026-08-18).

    🔴 **왜 `seed_cash` 와 따로 두는가.** 사용자가 짚었다:

    > *"계좌 잔액에서 결국 증거금으로 옮겨야 하잖아. (...) 계좌 잔액에 남아있는게
    > 수익이라는 증거 아닌가?"*

    ```
    계좌 잔액 (지갑)   거래소가 말하는 available — 사실이다
      └ 증거금        이 판이 굴리는 돈 — **사람이 정한다**
    ```

    `None` 이면 예전처럼 `equity` 전액을 쓴다 (백테스트 기존 동작 보존). 값이 있으면
    **그 금액만** 주문 크기 계산에 쓴다.

    ⚠️ **거래소 주문 API 에는 증거금 필드가 없다** (2026-08-18 실측 — 계약 명세의
    margin 관련 필드는 `leverage_min/max/cross_leverage_default/voucher_leverage` 뿐).
    주문은 `size`(계약수)로 내고, 격리 마진에서 필요한 증거금은 거래소가 잡는다.
    그래서 증거금은 **우리가 계약수를 계산할 때 쓰는 예산**이다:

        계약수 = floor(증거금 x 레버리지 / (가격 x 승수))

    ⛔ 이 값으로 손익률을 계산하지 않는다 — 손익률은 여전히 `seed_cash` 기준이다.
      둘을 섞으면 증거금을 바꿀 때마다 과거 성적이 재계산된다.
    """

    profit_line: Decimal | None = None
    """**이 선을 넘은 부분에서만** 이익을 실현한다 (T21 ⑤).

    🔴 **없으면 모든 이익에서 뗀다.** 지금까지가 그랬고, 그러면 벌자마자 계속 빠져나가
    복리가 안 붙는다. 사용자 설계:

    ```
    최소 100만 · 수익선 200만 · 실현 50%

    150만 → 250만   수익선 초과분 50만의 절반인 25만을 금고로 → 225만으로 계속
    ```

    ⚠️ **이익 전체가 아니라 초과분이다.** 250-150=100 의 절반이 아니라 250-200=50 의
    절반이다 — 수익선 아래에서는 한 푼도 안 뺀다.
    """

    budget_cap: Decimal | None = None
    """가용자금의 **천장** — 이 위는 전부 금고로 (T21 ⑥).

    🔴 **청산 한 번의 최대 손실을 고정한다.** 격리 마진에서 청산은 그 증거금 전액
    손실이므로, 굴리는 돈에 상한이 없으면 **손실에도 상한이 없다.**

    ⚠️ **대가는 복리다.** 좋은 전략일수록 이 천장이 비싸다 — 3배 성장에서 멈추는
    선택이며, 그 값어치는 성과로 판정한다 (§5.6.7).
    """

    drawdown_stop_pct: Decimal | None = None
    """브레이커 — 고점 대비 낙폭이 이 %에 닿으면 **새 진입을 멈춘다** (T22).

    🔴 **횟수가 아니라 낙폭이다** (사용자 확정 2026-08-20: *"손절을 3번해도 이득이 되긴
    해"* — 연속 손절 횟수로 끄는 것은 기각). 재는 것은 **이 판이 번 돈**(증거금 + 누적
    손익)이 **고점에서 얼마나 내려왔나**다. 지갑은 안 더한다 — 판 다섯이 공유하는 돈이라
    더하면 낙폭이 판 수만큼 희석되고 백테스트 낙폭(증거금 기준)과 비교가 안 된다.

    ⚠️ **`refill_cap` 과 다르다.** 저것은 *"금고에서 이만큼 넘게 꺼내면"* 이라 고점을
    모른다 — +30% 벌었다가 +5% 로 내려온 판은 금고를 한 푼도 안 꺼냈지만 낙폭은 19% 다.

    ⛔ 원장은 **표시만** 한다 (`tripped_at`). 새 진입을 실제로 막는 것은 라이브 러너이고,
    멈춘 판을 다시 켜는 것은 사람이다 — 자동 재개는 브레이커가 아니라 지연 장치다.
    None 이면 지금까지와 한 줄도 다르지 않게 돈다 (§5.6.2 동결).
    """

    refill_cap: Decimal | None = None
    """금고에서 꺼내 쓸 수 있는 **누적 총액** (T21 ⑦).

    🔴 **이것이 없으면 금고가 아니라 탄창이다.** 청산 → 채움 → 청산 → 채움 이 무한히
    돌면, 금고는 잃는 속도를 배로 늘리는 장치가 된다. 엣지가 없는 전략에서 특히 그렇다 —
    2026-08-19 에 28건이 -100 USDT 를 냈는데, 자동 재충전이 있었으면 계속 잃었을 것이다.

    ⚠️ **`halted_at` 과 다르다.** 저것은 *"금고가 비어서"* 멈추는 것이라 **다 잃은
    뒤**에 온다. 이 값은 그 전에 멈추는 선이다.

    ⭐ **모든 판이 같은 값을 쓴다** (전역 설정). 판마다 다르면 어느 판이 금고를 얼마나
    태웠는지 사람이 못 따라간다.
    """

    skim_pct: Decimal = Decimal(0)
    """이익이 난 매매에서 **금고로 빼둘 비율** (0~1). 0 이면 전액 재투자.

    🔴 사용자 요구 2026-08-17 — *"수익금의 몇 퍼센트를 따로 빼둘지 (...) 수익률은
    줄겠지만 청산으로 인한 리스크도 줄인다고 생각하는 거지. 음의 복리도 좀 관리하고."*

    ```
    0.0   전액 재투자 — 복리가 가장 세고 깡통도 가장 가깝다
    0.3   이익의 30% 를 뺀다
    1.0   이익을 전부 뺀다 — 굴리는 돈이 시드에 고정된다 (고정 베팅)
    ```

    ⚠️ 빼둔 돈은 **손익률에 포함**된다. 금고를 성과에서 빼면 유보를 켤수록 성적이
    나빠 보이는데, 그것은 회계 오류지 전략 차이가 아니다.
    """
    funding: Funding = Funding.SEED_REFILL
    """**어느 돈 모형으로 걸어가나** (T14-1). 기본은 백테스트가 쓰던 것이다.

    ⛔ 라이브는 반드시 `WALLET` 이다 — `SEED_REFILL` 은 아무도 입금하지 않는데 원장만
    채우므로, 그 숫자로 낸 주문이 거래소에서 거부된다.
    """

    refill: bool = True
    """`WALLET` 모형에서 손실 뒤 **지갑에서 목표치까지 채우는가** (T235 · 2026-09-09).

    참(기본): 채우고, 못 채우면 `halted_at` — 단독 판(지갑 = 계좌 잔액)의 규칙 (T14-2).
    거짓: **채우지 않고 몫 안에서 굴린다** — 펀드 멤버. 펀드 멤버는 `wallet_start=0` 으로 격리돼
    있어 채울 돈이 구조적으로 없다. 참으로 두면 손실 한 번에 영구히 새 진입이 멈춘다
    (실계좌 BTC 실측 2026-09-08~09). 거짓이면 증거금 = 몫 + 누적 실현으로 굴러가고,
    멈춤은 증거금이 0 이하로 갈 때만이다.
    """
    wallet_start: Decimal = Decimal(0)
    """`WALLET` 모형의 **지갑 시작 잔액** — 라이브는 RUN 을 열 때 거래소에서 읽는다.

    ⚠️ **여기서 갱신하지 않는다.** 매 걸음 거래소 값을 넣으면 `_walk()` 가 계좌 상태에
    따라 달라져 결정론이 깨진다 (절대 규칙 #5). 원장은 **모형**을 계산하고, 거래소는
    **사실**이며, 둘의 차이는 감사가 본다.
    """

    records: list[TradeRecord] = field(default_factory=list[TradeRecord])

    def add(self, record: TradeRecord) -> None:
        """기록을 넣는다.

        Args:
            record: 매매 기록.
        """
        self.records.append(record)

    def replace(self, record: TradeRecord) -> None:
        """같은 ID 의 기록을 갈아 끼운다 (청산 반영).

        Args:
            record: 새 기록.

        Raises:
            KeyError: 그 ID 가 원장에 없는 경우.
        """
        for index, item in enumerate(self.records):
            if item.trade_id == record.trade_id:
                self.records[index] = record
                return
        raise KeyError(f"{record.trade_id} 가 원장에 없다")

    def find(self, trade_id: str) -> TradeRecord | None:
        """ID 로 찾는다 — 로그 클릭 시 그 시점으로 이동하는 데 쓴다.

        Args:
            trade_id: 매매 id.

        Returns:
            기록. 없으면 None.
        """
        return next((item for item in self.records if item.trade_id == trade_id), None)

    @property
    def closed(self) -> list[TradeRecord]:
        """**손익이 확정된** 매매만 — 익절·손절.

        Note:
            ⛔ 대기·보유중·취소는 안 센다. 특히 **취소를 손절로 세면 안 된다** —
            잃은 돈이 없다.
        """
        return [
            item
            for item in self.records
            if item.outcome
            in (
                Outcome.TAKE_PROFIT,
                # ⚠️ **전환 익절도 확정이다.** 새 라벨을 만들고 여기 안 넣으면 그 건들이
                #    승률·달성률 표에서 통째로 사라진다.
                Outcome.SIGNAL_EXIT,
                # 🔴 **레벨 이탈도 확정이다** (T44). 2026-08-22 에 이 줄이 없어서 47건 중
                #    45건이 잔고에서 사라지고 -8% 판이 +2% 로 보였다 — 위 경고가 적힌 그대로
                #    재발했다. `tests/test_session_level_exit.py` 가 잠근다.
                Outcome.LEVEL_EXIT,
                Outcome.CONFIRM_FAIL,
                Outcome.HALF_BREAKEVEN,
                Outcome.STOP_LOSS,
                Outcome.LIQUIDATED,
            )
        ]

    @property
    def wins(self) -> int:
        """**돈을 번** 건수.

        Note:
            🔴 **라벨이 아니라 이득률로 센다.** 반익 뒤 본절은 라벨상 손절 계열인데
            실제로는 이익이다 — 라벨로 세면 승률과 손익이 어긋난다 (실측 승률 20% ·
            손익 +2.23%).
        """
        return sum(1 for item in self.closed if (item.gain_pct or Decimal(0)) > 0)

    @property
    def half_breakevens(self) -> int:
        """반익반본 건수.

        Note:
            ⚠️ **따로 센다.** 반익반본은 구조상 거의 항상 소액 플러스라 승률을
            부풀린다 — 승률이 이것으로 채워졌는지 보여야 한다 (§1-0s 관측 규약).
        """
        return sum(1 for item in self.closed if item.outcome is Outcome.HALF_BREAKEVEN)

    @property
    def win_rate(self) -> Decimal | None:
        """승률 — 끝난 매매 기준.

        Returns:
            비율(%). 끝난 매매가 없으면 None.

        Note:
            ⛔ 보유 중인 것을 분모에도 분자에도 넣지 않는다. 넣으면 창 끝에서 물려
            있는 포지션이 승률을 흔든다.
        """
        done = self.closed
        return None if not done else Decimal(self.wins) / Decimal(len(done)) * Decimal(100)

    def _walk(self) -> Purse:
        """자본을 순서대로 굴린다 — 모형에 따라 **채우는 곳이 다르다**.

        Returns:
            걸어간 결과.

        Note:
            🔴 **두 모형이 있고, 값 옆에는 늘 그 이름이 붙는다** (`Funding`). 재투입
            규칙이 다르므로 누적 손익률의 정의가 다르고, 섞으면 모형 차이가 전략
            차이로 읽힌다 (§1-0s 관측 규약).
        """
        if self.funding is Funding.WALLET:
            return self._walk_wallet()
        return self._walk_seed()

    def _walk_wallet(self) -> Purse:
        """**계좌가 진실이다** — 지갑에서만 채우고, 모자라면 멈춘다 (T14-1).

        Returns:
            걸어간 결과. 증거금을 못 채운 지점이 `halted_at` 에 남는다.

        Note:
            🔴 사용자 확정 2026-08-18:

            > *"계좌 잔액에서 결국 증거금으로 옮겨야 하잖아. 내가 주문에서 이득을 봤어,
            > 그러면 그 증거금으로 옮기는 금액이 오히려 실제 투자 가능한 금액이고,
            > 계좌 잔액에 남아있는게 수익이라는 증거 아닌가?"*

            ```
            이익   지갑으로 간다 → 그중 (1 - 유보) 만큼만 증거금으로 되돌린다
            손실   증거금에서 빠진다 → 지갑에서 목표치까지 다시 채운다
            부족   **멈춘다** — 밖에서 넣었다고 가정하지 않는다
            ```

            ⭐ **금고가 처음으로 실체를 갖는다.** 지금까지 금고는 원장 안의 장부 항목이라
            거래소에 대응물이 없었다. 여기서 금고 = **지갑 잔액**이고, 그래서 거래소에
            물어 검증할 수 있다.

            ⛔ **`halted_at` 뒤의 매매는 안 센다.** 돈이 없어 못 했을 매매를 성적에
            넣으면 *"있지도 않은 자본으로 낸 수익"* 이 된다.
        """
        target = self.margin_budget if self.margin_budget is not None else self.seed_cash
        margin = target
        wallet = self.wallet_start
        reserved = Decimal(0)
        topped = Decimal(0)
        earned = Decimal(0)
        peak = target
        drawdown = Decimal(0)
        deepest = Decimal(0)
        tripped: str | None = None
        for item in self.closed:
            gain = item.gain_pct
            if gain is None:
                continue
            # 🔴 **한 번에 한 포지션, 전액**이 이 원장의 전제다 (클래스 docstring).
            #    수량 산정은 RiskManager 소관이고, 여기서 흉내내면 SSoT 가 둘이 된다.
            # ⚠️ **채워진 만큼만 걸려 있었다** (T19 ①). 사다리 진입에서 다리 하나만
            #    채워지면 증거금도 절반만 들어간 것이라, 전액으로 세면 *있지도 않은
            #    자본으로 낸 수익*이 된다. 다리가 없으면 1 이라 지금과 같다.
            pnl = margin * item.filled_ratio * gain / Decimal(100)
            # ⭐ T229 — 재레버 감축으로 이미 실현된 몫. 거래소가 그 자리에서 적은 USDT 그대로.
            pnl += item.realized_adjust
            margin += pnl
            # ⭐ **그때의 증거금으로 센다** — 나중에 곱하면 규모가 달라진다.
            earned += pnl
            # ⭐ 이익의 일부는 **지갑에 남긴다** — 다시 굴리지 않는 것이 유보의 뜻이다.
            #
            # 🔴 **수익선 위에서만 뗀다** (T21 ⑤). 없으면 예전처럼 모든 이익에서 뗀다 —
            #    그러면 벌자마자 계속 빠져나가 복리가 안 붙는다.
            line: Decimal | None = self.profit_line
            if self.skim_pct > 0:
                over = (margin - line) if line is not None else pnl
                if over > 0:
                    taken = over * self.skim_pct
                    margin -= taken
                    wallet += taken
                    reserved += taken
            # 🔴 **천장을 넘은 것은 전부 금고로** (T21 ⑥). 굴리는 돈에 상한이 없으면
            #    청산 한 번의 손실에도 상한이 없다.
            cap: Decimal | None = self.budget_cap
            if cap is not None and margin > cap:
                spill = margin - cap
                margin = cap
                wallet += spill
                reserved += spill
            # ⭐ T22 — 고점 대비 낙폭. 기준은 **이 판이 번 돈** (증거금 + 누적 손익)이다.
            #    지갑을 더하면 안 된다 — 지갑은 판 다섯이 공유하므로 낙폭이 판 수만큼
            #    희석되고, 백테스트 낙폭(증거금 기준)과 비교가 안 된다. 채움은 지갑에서
            #    증거금으로 **옮기는** 것이라 `earned` 를 안 바꾼다.
            worth = target + earned
            peak = max(peak, worth)
            drawdown = (peak - worth) / peak * Decimal(100) if peak > 0 else Decimal(0)
            deepest = max(deepest, drawdown)
            stop = self.drawdown_stop_pct
            if tripped is None and stop is not None and drawdown >= stop:
                tripped = item.trade_id
            if not self.refill:
                # ⭐ T235 — 펀드 멤버: 채우지 않는다. 몫이 다 없어졌을 때만 멈춘다.
                if margin <= 0:
                    return Purse(
                        rolling=Decimal(0),
                        reserved=reserved,
                        topped_up=topped,
                        wallet=wallet,
                        earned=earned,
                        halted_at=item.trade_id,
                        drawdown_pct=drawdown,
                        max_drawdown_pct=deepest,
                        tripped_at=tripped,
                    )
                continue
            if margin >= target:
                continue
            # ⚠️ 마이너스는 무시한다 — 격리 마진은 지갑에 있던 것까지만 잃고 빚이 되지 않는다.
            need = target - max(margin, Decimal(0))
            # 🔴 **금고를 다 태우지 않는다** (T21 ⑦). 남은 충전 여력이 곧 이 판의 수명이다.
            room = need if self.refill_cap is None else max(self.refill_cap - topped, Decimal(0))
            drawn_now = min(need, max(wallet, Decimal(0)), room)
            wallet -= drawn_now
            margin = max(margin, Decimal(0)) + drawn_now
            topped += drawn_now
            if margin < target:
                # 🔴 **여기서 멈춘다.** 이것이 사용자가 요구한 *"포지션 금액이 다
                #    떨어지면 매매가 멈춘다"* 의 실체다.
                #
                # ⚠️ 이유가 둘이다 — **금고가 비었거나**(옛 경로) **충전 상한에
                #    닿았거나**(T21 ⑦). 후자는 금고에 돈이 남아 있어도 멈춘다.
                return Purse(
                    rolling=margin,
                    reserved=reserved,
                    topped_up=topped,
                    wallet=wallet,
                    earned=earned,
                    halted_at=item.trade_id,
                    drawdown_pct=drawdown,
                    max_drawdown_pct=deepest,
                    tripped_at=tripped,
                )
        return Purse(
            rolling=margin,
            reserved=reserved,
            topped_up=topped,
            wallet=wallet,
            earned=earned,
            drawdown_pct=drawdown,
            max_drawdown_pct=deepest,
            tripped_at=tripped,
        )

    def _walk_seed(self) -> Purse:
        """**시드가 바닥이다** — 백테스트가 쓰던 모형 (사용자 확정 2026-08-17).

        Returns:
            걸어간 결과.

        Note:
            🔴 **레버리지 청산이 생기면서 잔고가 0 이하로 갈 수 있다.** 예전 계산은 그냥
            곱하기만 해서 음수 잔고로도 계속 굴렸고, 그것은 현실에 없는 계좌다.

            ⭐ 사용자 확정 — *"청산됐을 때, 그 돈을 다시 채운다고 가정하고, 재투자
            금액도 손익률 옆에 표기"*. 그래서 0 이하가 되면 **시드만큼 다시 채우고**
            얼마를 넣었는지 따로 센다.

            > *"시드가 천만원인데 백만원 손해 놨을 때, 재투자 금액으로 백만원을 더한다."*

            깡통일 때만이 아니라 **시드 아래로 내려갈 때마다** 채운다.

            ⭐ **금고에서 먼저 꺼낸다** (사용자 확정 2026-08-17):

            > *"지금 재투자 했을 때, 금고에서 가져와서 쓰고 있는거지? 그러기 위해서
            > 금고가 있는 거거든."*

            유보의 목적이 **깡통을 자기 돈으로 메우는 것**이므로, 금고를 놔두고 밖에서
            새 돈을 넣으면 유보가 아무 일도 안 한 셈이 된다.

            ⛔ **라이브에 쓰면 거짓말이 된다** — 아무도 입금하지 않는다. `Funding` 참고.
        """
        equity = self.seed_cash
        added = Decimal(0)
        reserved = Decimal(0)
        drawn = Decimal(0)
        earned = Decimal(0)
        peak = self.seed_cash
        drawdown = Decimal(0)
        deepest = Decimal(0)
        tripped: str | None = None
        for item in self.closed:
            gain = item.gain_pct
            if gain is None:
                continue
            before = equity
            # ⚠️ 채워진 만큼만 굴렀다 (T19 ① — 위와 같은 이유).
            equity *= Decimal(1) + gain * item.filled_ratio / Decimal(100)
            earned += equity - before
            if self.skim_pct > 0 and equity > before:
                taken = (equity - before) * self.skim_pct
                reserved += taken
                equity -= taken
            if equity < self.seed_cash:
                # ⛔ 마이너스는 무시한다 (사용자 확정): *"지갑 내 금액 없으면 청산으로
                #    마무리되는 룰"* — 격리 마진이 실제로 그렇다.
                need = self.seed_cash - max(equity, Decimal(0))
                from_vault = min(need, reserved)
                reserved -= from_vault
                drawn += from_vault
                added += need - from_vault
                equity = self.seed_cash
            # ⭐ T22 — 기준은 이 판이 번 돈(시드 + 누적 손익). 밖에서 넣은 돈은 안 센다.
            worth = self.seed_cash + earned
            peak = max(peak, worth)
            drawdown = (peak - worth) / peak * Decimal(100) if peak > 0 else Decimal(0)
            deepest = max(deepest, drawdown)
            stop = self.drawdown_stop_pct
            if tripped is None and stop is not None and drawdown >= stop:
                tripped = item.trade_id
        return Purse(
            rolling=equity,
            added=added,
            reserved=reserved,
            drawn=drawn,
            earned=earned,
            drawdown_pct=drawdown,
            max_drawdown_pct=deepest,
            tripped_at=tripped,
        )

    @property
    def cash(self) -> Decimal:
        """**굴리는 돈** — 빼둔 금고는 여기 없다.

        Note:
            ⚠️ 모형에 따라 뜻이 다르다. `SEED_REFILL` 은 잔고이고 `WALLET` 은 **증거금**이다.
        """
        return self._walk().rolling

    @property
    def wallet(self) -> Decimal:
        """지갑에 남은 돈 — `WALLET` 모형에서 **거래소 잔액에 대응한다**.

        Note:
            🔴 이것이 **모형이 말하는 값**이고 거래소가 말하는 것이 사실이다. 둘이
            갈리는 것을 감사가 본다 — 예전에는 갈려도 아무도 몰랐다.
        """
        return self._walk().wallet

    @property
    def topped_up(self) -> Decimal:
        """지갑에서 증거금으로 **채워 넣은 총액** (`WALLET`).

        Note:
            ⭐ `refilled`(밖에서 넣었다고 가정한 돈)의 후임이다. 이쪽은 **가정이 아니라
            내 돈을 옮긴 것**이므로 손익률에서 빼지 않는다.
        """
        return self._walk().topped_up

    @property
    def halted_at(self) -> str | None:
        """증거금을 못 채워 **멈춘** 매매의 id. 안 멈췄으면 None.

        Note:
            🔴 **화면이 이유를 말해야 한다.** 조용히 멈추면 "판정 0회" 와 구별되지
            않는다 (절대 규칙 #8).
        """
        return self._walk().halted_at

    @property
    def drawdown_pct(self) -> Decimal:
        """지금 고점 대비 낙폭(%) — 판의 재산 기준 (T22)."""
        return self._walk().drawdown_pct

    @property
    def max_drawdown_pct(self) -> Decimal:
        """걸어오는 동안 가장 깊었던 낙폭(%) (T22)."""
        return self._walk().max_drawdown_pct

    @property
    def tripped_at(self) -> str | None:
        """브레이커가 **닿은** 매매의 id. 안 닿았으면 None (T22).

        Note:
            🔴 `halted_at` 과 다르다 — 저것은 돈이 없어 멈춘 것이고 이것은 *너무 잃어서*
            멈춘 것이다. 둘 다 화면이 이유를 말해야 한다 (절대 규칙 #8).
        """
        return self._walk().tripped_at

    @property
    def withdrawn(self) -> Decimal:
        """깡통을 메우려고 **금고에서 꺼내 쓴 총액** (`SEED_REFILL`)."""
        return self._walk().drawn

    @property
    def reserved(self) -> Decimal:
        """수익에서 떼어 **금고에 넣은 총액** (`skim_pct`).

        Note:
            🔴 잃은 돈이 아니다. `cash + reserved` 가 실제로 가진 전부이며, 손익률은
            둘을 합쳐 낸다 — 금고만 빼면 유보를 켤수록 성적이 나빠 보인다.
        """
        return self._walk().reserved

    @property
    def refilled(self) -> Decimal:
        """청산으로 깡통이 나 **밖에서 다시 넣은 총액** (`SEED_REFILL` 전용).

        Note:
            🔴 **이것이 0 이 아니면 손익률만으로는 성과를 못 읽는다.** 원금을 계속
            넣어 가며 낸 수익률이기 때문이다.

            ⛔ `WALLET` 모형에서는 **늘 0** 이다 — 밖에서 넣지 않고 멈춘다.
        """
        return self._walk().added

    @property
    def realized_cash(self) -> Decimal:
        """끝난 매매들이 실제로 **번(잃은) 금액**.

        Returns:
            금액. 아직 끝난 매매가 없으면 0.

        Note:
            🔴 **손익률의 합에 지금 증거금을 곱하면 안 된다** (2026-08-20). 증거금은
            매매마다 달라진다 — 벌면 커지고 청산나면 금고에서 채워진다. 지금 값으로
            과거를 곱하면 오래 돈 판일수록 부풀고, 실제로 감사가 `원장 +94.26 vs
            거래소 -10.19` 라는 **부호까지 틀린** 대조를 냈다.

            ⇒ `_walk()` 가 굴러가는 그 자리에서 센 값을 그대로 쓴다. 그것이 거래소의
              실현 손익과 **같은 단위**다.

            ⚠️ 그래도 정확히 같지는 않다 — 슬리피지·펀딩비·반올림이 있다. 감사가
              크기는 넉넉히 보고 **부호**를 엄하게 보는 이유가 그것이다.
        """
        return self._walk().earned

    @property
    def sizing_base(self) -> Decimal:
        """주문 크기를 정할 때 쓰는 금액.

        Returns:
            `WALLET` 이면 지금 굴리는 증거금, 아니면 예산과 `equity` 중 작은 쪽.

        Note:
            🔴 **`equity` 를 그대로 쓰면 금고 돈이 증거금이 된다.** 실측(2026-08-18):
            유보 100% 를 켜도 주문이 2.4% 만 줄었다 — 금고로 뺀 돈이 그대로 증거금으로
            들어가기 때문이다. 유보의 목적이 *"굴리는 돈을 줄여 청산을 멀리한다"* 인데
            아무 일도 하지 않았다.

            ⚠️ 증거금이 `equity` 보다 크면 `equity` 로 자른다 — 없는 돈으로 주문을 내면
            거래소가 거부하고, 그 실패는 화면에서 "주문 0건" 으로만 보인다 (규칙 #8).
        """
        if self.funding is Funding.WALLET:
            # ⭐ 굴리는 돈이 곧 증거금이다 — 지갑은 여기 안 들어온다.
            return max(self._walk().rolling, Decimal(0))
        if self.margin_budget is None:
            return self.equity
        return min(self.margin_budget, self.equity)

    @property
    def equity(self) -> Decimal:
        """실제로 가진 전부.

        Note:
            `SEED_REFILL` 은 **굴리는 돈 + 금고**, `WALLET` 은 **증거금 + 지갑**이다.
            둘 다 *"지금 내 것 전부"* 라는 같은 뜻이다.
        """
        purse = self._walk()
        if self.funding is Funding.WALLET:
            return purse.rolling + purse.wallet
        return purse.rolling + purse.reserved

    @property
    def liquidations(self) -> int:
        """강제 청산 건수."""
        return sum(1 for item in self.closed if item.outcome is Outcome.LIQUIDATED)

    @property
    def return_pct(self) -> Decimal:
        """누적 손익률(%).

        Note:
            🔴 **분모가 모형마다 다르다.**

            ```
            SEED_REFILL   시드. 넣은 돈은 분자에서 빼되 분모에 더하지 않는다 —
                          더하면 먹인 만큼 분모가 커져 손실이 작아 보인다
            WALLET        증거금 + 지갑 시작. 처음에 가진 전부다
            ```

            ⛔ **두 값을 같은 표에 넣지 않는다** (§1-0s).
        """
        purse = self._walk()
        if self.funding is Funding.WALLET:
            target = self.margin_budget if self.margin_budget is not None else self.seed_cash
            base = target + self.wallet_start
            if base <= 0:
                return Decimal(0)
            return (purse.rolling + purse.wallet - base) / base * Decimal(100)
        if self.seed_cash <= 0:
            return Decimal(0)
        return (
            (purse.rolling + purse.reserved - self.seed_cash - purse.added)
            / self.seed_cash
            * Decimal(100)
        )

    @property
    def mean_achievement(self) -> Decimal | None:
        """평균 손익비 달성률.

        Returns:
            평균. 잴 수 있는 매매가 없으면 None.

        Note:
            🔴 **부호를 뭉개지 않는다.** 손절이면 음수이고, 그것이 섞여야 *"계획대로
            간 비율"* 이 정직하게 나온다.
        """
        values = [item.achievement for item in self.closed if item.achievement is not None]
        return None if not values else sum(values, Decimal(0)) / Decimal(len(values))
