"""거래소가 채운 것을 세션이 **동기로 읽는 우편함** (T19 ⑤).

## 왜 우편함인가

`Session.step()` 은 동기이고 그것이 결정론 코어의 모양이다. 라이브 체결은 네트워크
왕복이라 비동기지만, **그것을 세션 안으로 들이지 않는다.**

```
러너(비동기)   거래소에 묻는다  →  note() 로 넣어 둔다
세션(동기)     poll() 로 꺼낸다  →  판정은 하나의 모양으로 남는다
```

`LiveFeed.push()` 가 봉에 대해 하는 일과 정확히 같다. 그래서 세션의 계약이 과거와
라이브에서 하나로 유지된다 (원칙 P3).

## 🔴 이것이 "샀나" 를 **판정하지 않는다**

봉인 급전의 `SealedFiller` 는 봉을 보고 **판정**하지만, 여기서는 거래소가 이미 답을
줬다. 이 클래스는 그 답을 **옮기기만** 한다 — 여기서 추측을 섞으면 2026-08-19 사고
③ 과 같은 병(원장이 사실 아닌 값을 든다)이 진입 쪽에 생긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.orchestration.walkforward.fill_protocol import Fill


@dataclass(frozen=True, slots=True)
class Want:
    """러너가 아직 **거래소에 안 보낸** 주문 하나."""

    ticket: str
    price: Decimal
    ratio: Decimal
    long: bool


class LiveFiller:
    """거래소 체결을 세션에 전달한다.

    Note:
        ⚠️ **두 얼굴이다.** 세션은 `place`·`poll`·`cancel` 만 부르고(동기), 러너는
        `to_place`·`sent`·`to_cancel`·`dropped`·`note` 를 부른다(비동기 루프에서).
        섞으면 세션이 네트워크를 기다리게 되고 결정론 코어가 무너진다.

        🔴 **보낸 것과 안 보낸 것을 가른다.** 안 가르면 걸음마다 같은 주문을 다시
        보내고, 거래소에는 같은 자리에 호가가 쌓인다.
    """

    def __init__(self) -> None:
        """빈 우편함으로 시작한다."""
        self._want: dict[str, Want] = {}
        """아직 안 보낸 것."""

        self._live: dict[str, tuple[str, Decimal]] = {}
        """보낸 것 — `{표: (거래소 주문 id, 비중)}`.

        ⚠️ **비중도 같이 든다.** 거래소는 *"채워졌다"* 까지만 말하고 그것이 계획의
        얼마인지는 모른다 — 우리가 부를 때 정한 값이라 여기서 기억해야 한다.
        """

        self._drop: list[str] = []
        """거둬야 하는 것."""

        self._box: dict[str, Fill] = {}
        """거래소가 채웠다고 알려 온 것 — 세션이 꺼내 간다."""

        self.filled = 0
        """채워진 표 수."""

        self.cancelled = 0
        """거둔 표 수 — **역선택을 보는 값**이다 (`SealedFiller` 와 같은 이유)."""

        self._failed: set[str] = set()
        """거래소가 **거절한** 표 — 세션이 이걸 보고 계획을 접는다 (2026-08-25).

        🔴 거절된 표는 다시 안 보내는 것이 맞지만(규격 문제 반복 방지), 그 사실을
        세션이 모르면 영원히 올 수 없는 체결을 기다린다 — 포스트온리 크로스 거절
        (-5022/POC_IMMEDIATE) 뒤 진입이 교착된 실측 사고의 원인.
        """

    # ── 세션이 부르는 쪽 (동기) ──────────────────────────────

    def place(self, ticket: str, *, price: Decimal, ratio: Decimal, long: bool) -> None:
        """지정가 하나를 **걸어 달라고 부탁한다**.

        Args:
            ticket: 주문 이름.
            price: 지정가.
            ratio: 채워지면 계획의 얼마인가.
            long: 롱인가.

        Note:
            ⚠️ **여기서 주문이 나가지 않는다.** 세션은 네트워크를 기다리면 안 되므로
            부탁만 적어 두고, 러너가 다음 걸음에 보낸다.
        """
        self._want[ticket] = Want(ticket=ticket, price=price, ratio=ratio, long=long)

    def poll(self, ticket: str, bar: Candle) -> Fill | None:
        """거래소가 채웠나.

        Args:
            ticket: 주문 이름.
            bar: **안 쓴다** — 거래소가 이미 답을 줬다. 계약을 맞추려 받는다.

        Returns:
            채워졌으면 그 조각. 아직이면 None.

        Note:
            🔴 **봉을 보지 않는다.** 봉으로 추측하면 거래소와 갈리고, 그 갈림은 아무
            신호 없이 손익에만 나타난다 — 이 클래스가 존재하는 이유가 그것이다.

            ⚠️ **한 번 꺼내면 없어진다.** 두 번 세면 비중이 두 배가 된다.
        """
        del bar
        got = self._box.pop(ticket, None)
        if got is not None:
            self.filled += 1
        return got

    def cancel(self, ticket: str) -> None:
        """건 것을 거둬 달라고 부탁한다.

        Args:
            ticket: 주문 이름.
        """
        if self._want.pop(ticket, None) is not None:
            # 아직 안 보낸 것은 그냥 지운다 — 거래소는 이 표를 모른다.
            self.cancelled += 1
            return
        if ticket in self._live and ticket not in self._drop:
            self._drop.append(ticket)
            self.cancelled += 1

    def resting(self) -> tuple[str, ...]:
        """지금 거래소에 걸려 있다고 아는 표들.

        Returns:
            이름 목록.
        """
        return tuple(self._live)

    # ── 러너가 부르는 쪽 (비동기 루프에서) ────────────────────

    def to_place(self) -> tuple[Want, ...]:
        """아직 안 보낸 주문들.

        Returns:
            보낼 것들.
        """
        return tuple(self._want.values())

    def sent(self, ticket: str, order_id: str) -> None:
        """보냈다고 표시한다.

        Args:
            ticket: 주문 이름.
            order_id: 거래소 주문 id.

        Note:
            ⚠️ **id 를 들고 있어야 거둘 수 있다.** 없으면 취소할 대상을 못 찾아
            지정가가 거래소에 남고, 계획이 사라진 자리에서 채워진다.
        """
        want = self._want.pop(ticket, None)
        if want is not None:
            self._live[ticket] = (order_id, want.ratio)

    def failed(self, ticket: str) -> None:
        """보내려다 거절당했다.

        Args:
            ticket: 주문 이름.

        Note:
            🔴 **다시 안 보낸다.** 거절은 대개 규격 문제(호가 단위·수량)라 그대로
            다시 보내면 같은 거절이 반복된다 — 2026-08-19 에 손절이 그 모양으로
            27번 거절됐다. 그 자리는 버리고 세는 쪽이 정직하다.

            ⭐ 대신 **거절됐다는 사실은 남긴다** — 세션이 이걸 보고 계획을 접고
            다음 봉에서 새 값으로 다시 계획한다 (포스트온리 재시도 의미론).
        """
        self._want.pop(ticket, None)
        self._failed.add(ticket)

    def rejected(self, ticket: str) -> bool:
        """이 표를 거래소가 거절했는가 — 세션이 대기 유지 여부를 정할 때 본다.

        Args:
            ticket: 표 이름.

        Returns:
            거절 목록에 있으면 True.
        """
        return ticket in self._failed

    def to_cancel(self) -> tuple[tuple[str, str], ...]:
        """거둬야 하는 것들.

        Returns:
            `(표, 거래소 주문 id)` 목록.
        """
        return tuple(
            (ticket, self._live[ticket][0]) for ticket in self._drop if ticket in self._live
        )

    def dropped(self, ticket: str) -> None:
        """거뒀다고 표시한다.

        Args:
            ticket: 주문 이름.
        """
        self._live.pop(ticket, None)
        if ticket in self._drop:
            self._drop.remove(ticket)

    def note(self, ticket: str, *, price: Decimal, ratio: Decimal) -> None:
        """거래소가 **채웠다**고 알려 온다.

        Args:
            ticket: 주문 이름.
            price: 실제 체결가.
            ratio: 계획의 얼마를 채웠나.

        Note:
            🔴 **거래소가 말한 가격을 그대로 넣는다.** 우리가 부른 값으로 바꾸면
            슬리피지가 사라져 원장이 사실보다 나은 말을 한다 (사고 ③ 과 같은 병).
        """
        self._live.pop(ticket, None)
        self._box[ticket] = Fill(price=price, ratio=ratio)

    def inherit(self, ticket: str, order_id: str, ratio: Decimal) -> None:
        """다시 뜬 판이 **이미 거래소에 걸려 있는** 표를 자기 것으로 심는다 (T218).

        Args:
            ticket: 주문 이름.
            order_id: 거래소 주문 id.
            ratio: 채워지면 계획의 얼마인가.

        Note:
            `sent()` 와 같은 결과지만 부탁(`_want`)을 거치지 않는다 — 부탁은 이 프로세스가
            보낸 적이 없다. 이 뒤로는 평소처럼 `to_cancel`·`note`·`mine` 이 다 통한다.
        """
        self._live[ticket] = (order_id, ratio)

    def pending_want(self, ticket: str) -> tuple[Decimal, Decimal, bool] | None:
        """아직 안 보낸 표의 (가격, 비중, 롱) — 영속화용.

        Args:
            ticket: 표 이름.

        Returns:
            `(가격, 비중, 롱)`. 모르는 표면 None.
        """
        want = self._want.get(ticket)
        return None if want is None else (want.price, want.ratio, want.long)

    def mine(self, ticket: str) -> str | None:
        """그 표의 거래소 주문 id.

        Args:
            ticket: 주문 이름.

        Returns:
            id. 아직 안 보냈거나 이미 끝났으면 None.
        """
        found = self._live.get(ticket)
        return None if found is None else found[0]

    def ratio_of(self, ticket: str) -> Decimal | None:
        """그 표가 채워지면 계획의 얼마인가.

        Args:
            ticket: 주문 이름.

        Returns:
            비중. 모르는 표면 None.

        Note:
            🔴 **거래소는 이 값을 모른다.** *"채워졌다"* 까지만 말하고, 그것이 계획의
            절반인지 전부인지는 우리가 부를 때 정한 것이다 — 지어내면 `filled_ratio` 가
            거짓이 되고 손익이 그만큼 틀린다.
        """
        found = self._live.get(ticket)
        return None if found is None else found[1]
