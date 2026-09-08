"""체결 계약 — **원장이 "샀나" 를 계산하지 않게** (T19 ②).

## 왜 있는가

2026-08-19 사고 ③ 은 원장이 *"얼마에 팔았나"* 를 **계산**해서 났다. 닿은 적도 없는
계획가로 28건을 전부 이긴 매매로 셌고, 같은 표본을 실제 청산가로 재면 1승이었다.

지정가 진입을 넣으면 같은 병이 **진입 쪽에** 생긴다:

```
③  "얼마에 팔았나" 를 계산했다  →  거짓 손익
⑧  "샀나" 를 계산하면          →  유령 포지션 (원장은 보유중, 거래소는 없음)
```

⇒ **체결은 출력이 아니라 입력이다.**

## 왜 프로토콜인가

봉도 밖에서 오는 값이고, 그래서 `Feed` 라는 계약 하나에 구현 둘을 뒀다. 체결도 같다:

```
Feed      봉을 먹인다      SealedFeed(과거)    /  LiveFeed(지금)
Filler    체결을 알려준다   SealedFiller(모형)  /  LiveFiller(우편함)
```

세션은 어느 쪽인지 모른다 (원칙 P3). **판정하는 놈이 하나면 갈릴 구조가 없다** —
지금까지의 사고가 전부 *"두 곳이 같은 것을 다르게 말한다"* 였다.

## 🔴 동기다

`Session.step()` 은 동기이고 그것이 결정론 코어의 모양이다. 라이브 체결은 네트워크
왕복이라 비동기지만, **그것을 세션 안으로 들이지 않는다** — 러너가 비동기로 물어
`LiveFiller` 에 넣어 두고 세션은 그 **우편함을 동기로 읽는다.**

`LiveFeed.push()` 가 봉에 대해 하는 일과 정확히 같은 모양이며, 그래서 세션의 계약이
과거와 라이브에서 하나로 유지된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from updown.common.domain.candle import Candle


@dataclass(frozen=True, slots=True)
class Fill:
    """채워진 한 조각.

    Note:
        ⚠️ **수량이 아니라 비중이다.** 원장은 *"한 번에 한 포지션, 전액"* 을 전제로
        비율로 셈한다 — 수량 산정은 RiskManager 소관이고, 여기서 흉내 내면 SSoT 가
        둘이 된다 (절대 규칙 #4).
    """

    price: Decimal
    """실제로 채워진 가격."""

    ratio: Decimal
    """계획의 얼마를 채웠나 (0~1). 두 다리 사다리에서 한 다리는 0.5 다."""


@runtime_checkable
class Filler(Protocol):
    """주문 하나가 채워졌는지 **알려주는 것**.

    Note:
        🔴 **계약이 셋뿐이다.** 넓게 잡으면 구현할 수 없는 것이 예외로 채워지고,
        그러면 프로토콜이 계약이 아니게 된다.

        ⛔ **가격을 정하지 않는다.** 어디에 걸지는 계획(`plan_for`)이 정하고 이것은
        *"거기 채워졌나"* 에만 답한다 — 값을 만들면 SSoT 가 또 갈라진다.
    """

    def place(self, ticket: str, *, price: Decimal, ratio: Decimal, long: bool) -> None:
        """지정가 하나를 건다.

        Args:
            ticket: 이 주문을 부를 이름 — 매매 id + 다리 번호.
            price: 지정가.
            ratio: 채워지면 계획의 얼마인가.
            long: 롱인가. 체결 판정의 방향이 뒤집힌다.
        """
        ...

    def poll(self, ticket: str, bar: Candle) -> Fill | None:
        """채워졌나.

        Args:
            ticket: 주문 이름.
            bar: 방금 닫힌 봉. **봉인 급전만 쓴다** — 라이브는 거래소가 이미 답을
                줬으므로 무시한다.

        Returns:
            채워졌으면 그 조각. 아직이면 None.

        Note:
            ⚠️ **한 번 답한 표는 다시 답하지 않는다.** 두 번 세면 비중이 두 배가 된다.
        """
        ...

    def rejected(self, ticket: str) -> bool:
        """이 표를 거래소가 **거절**했는가 (2026-08-25).

        Args:
            ticket: 표 이름.

        Returns:
            거절됐으면 True.

        Note:
            봉인 급전은 거절이 없으므로 항상 False. 라이브에서 참이면 세션은 그 표의
            체결을 기다리지 않는다 — 안 그러면 포스트온리 크로스 거절 뒤 진입이
            교착된다 (실측).
        """
        ...

    def cancel(self, ticket: str) -> None:
        """건 것을 거둔다.

        Args:
            ticket: 주문 이름.

        Note:
            ⚠️ **없는 표를 거둬도 조용하다.** 이미 채워졌거나 만료된 표를 거두는 것은
            정상적인 경로이고, 거기서 터지면 청산 경로가 멎는다.
        """
        ...
