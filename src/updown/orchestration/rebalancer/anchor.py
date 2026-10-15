"""펀드 자동 앵커 — 펀드 총자본을 거래소 계좌에 스스로 맞춘다 (T285 · 2026-09-17).

## 왜
펀드 장부는 "지난 잔고 + 이번 실현 증분" 으로만 움직인다(`Coordinator`). 그 셈이 한 번
틀리면(2026-09-17 누수 · 실계좌 장부 132 vs 계좌 298) 코드가 스스로 되돌리지 못하고, 사람이
값을 넣어야 했다. 사용자 결정: **"무조건 자동화"** — 펀드가 틱마다 거래소 잔고를 읽어 자기
총자본을 그 사실에 맞춘다.

## 무엇이 어려운가
거래소는 "이 펀드의 돈" 을 모른다. 계좌 총액 하나뿐이고 거기엔 펀드 밖 유휴 현금·다른 펀드·
단독 판이 섞일 수 있다. 그래서 앵커는 **펀드가 그 거래소의 유일한 소유자일 때만** 돌고(다른
펀드·단독 판이 있으면 증분 모형 그대로 · 이유를 로그에), 유휴 현금은 두 모드로 다룬다:

```
account  펀드 = 계좌. 총자본 = 계좌 총액.
         거래소 입출금(dnw)은 펀드 입출금으로 자동 기록(성과 아님).
drift    계좌 안에 펀드 밖 유휴 현금(idle)이 있다. 총자본 = 계좌 총액 - idle.
         거래소 입출금은 idle 로 간다(펀드 몫이 아니다).
         펀드 입출금은 화면에서 기록하면 idle 에서 옮겨 온다.
```

모드는 첫 앵커 때 한 번 정한다: 계좌 총액이 투입 원금(contributed)의 ±20% 안이면
`account`(실계좌처럼 계좌 전부가 펀드), 아니면 `drift`(테스트넷 1만 USDT 에 1,000 짜리
데모처럼 계좌 대부분이 펀드 밖).

## 앵커가 총자본을 바꾸는 방식
앵커값은 그 기간의 **입출금 직전 진짜 평가금액**으로 `TwrLedger.step` 에 들어간다. 누수로
낮아진 장부(132)가 진짜(298)로 뛰면 그 기간 수익률에 그대로 잡혀 누적 TWR 이 스스로
교정된다(0.44 x 298/132 ≈ 0.99 → -0.6%). 따로 "조정" 항목을 두지 않는다 — 장부가 틀렸던
것이지 돈이 들어온 것이 아니다.

⚠️ 미실현 손익은 앵커에 넣지 않는다. 계좌 총액은 **거래소가 직접 말하는 지갑 총액**이다
(가용 + 포지션 증거금 + 대기 주문 증거금). `가용 + 포지션 증거금` 으로 재구성하면 걸려 있는
지정가 주문의 증거금이 빠져 없는 손실이 장부에 적힌다(2026-09-17~18 실계좌 실측 · 298 → 264).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MODE_ACCOUNT = "account"
MODE_DRIFT = "drift"
DNW_TYPES = frozenset({"dnw", "TRANSFER"})
"""거래소 자금 원장에서 입출금을 뜻하는 `type` — Gate `dnw` · Binance(`/income`) `TRANSFER`."""
ACCOUNT_TOLERANCE = Decimal("0.2")
"""첫 앵커에서 계좌 총액이 투입 원금의 이 비율 안이면 `account` 모드 — 계좌 전부가 펀드다."""


@dataclass(frozen=True, slots=True)
class AnchorState:
    """펀드 하나의 앵커 상태 (펀드 파일에 저장).

    Attributes:
        mode: `account` | `drift`.
        idle: `drift` 모드의 펀드 밖 유휴 현금. `account` 는 0.
        at: 마지막 앵커 시각 — 거래소 입출금은 이 뒤의 것만 센다.
        seen: 이미 흡수한 입출금 행 열쇠(`시각:변화`) — 같은 행을 두 번 안 센다.
    """

    mode: str
    idle: Decimal = Decimal(0)
    at: datetime | None = None
    seen: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """저장용 표현.

        Returns:
            문자열 값의 dict — `mode`·`idle`·`at`(ISO8601)·`seen`(최근 200개).
        """
        return {
            "mode": self.mode,
            "idle": str(self.idle),
            "at": None if self.at is None else self.at.isoformat(),
            "seen": list(self.seen[-200:]),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnchorState:
        """저장 표현에서 복원한다.

        Args:
            data: `to_dict` 가 만든 dict. 빠진 키는 기본값(`drift` · 0 · None · 빈 seen).

        Returns:
            같은 값의 `AnchorState`.
        """
        raw_at = data.get("at")
        return cls(
            mode=str(data.get("mode", MODE_DRIFT)),
            idle=Decimal(str(data.get("idle", "0") or "0")),
            at=None if not raw_at else datetime.fromisoformat(str(raw_at)),
            seen=tuple(str(k) for k in data.get("seen", [])),
        )


@dataclass(frozen=True, slots=True)
class Anchored:
    """한 틱의 앵커 결과.

    Attributes:
        equity_before_flow: 이 기간 입출금 직전의 진짜 평가금액 — `TwrLedger.step` 의 입력.
        flow: 이 기간에 자동으로 기록할 펀드 입출금(`account` 모드의 거래소 입출금 합). 0 이면 없음.
        state: 갱신된 앵커 상태.
    """

    equity_before_flow: Decimal
    flow: Decimal
    state: AnchorState
    outside: Decimal = Decimal(0)
    """`flow` 중 **매매법 밖 주문**(사람)의 손익 · 수수료 몫 — 기록 메모를 가르는 데만 쓴다."""


def choose_mode(account_total: Decimal, contributed: Decimal) -> str:
    """첫 앵커의 모드 — 계좌가 곧 펀드인가(account), 펀드 밖 돈이 큰가(drift).

    Args:
        account_total: 거래소 계좌 총액(현금 + 포지션 증거금).
        contributed: 펀드 순 투입 원금.

    Returns:
        `account` 또는 `drift`.
    """
    if contributed <= 0:
        return MODE_DRIFT
    gap = abs(account_total - contributed) / contributed
    return MODE_ACCOUNT if gap <= ACCOUNT_TOLERANCE else MODE_DRIFT


def initial_state(account_total: Decimal, contributed: Decimal, balance: Decimal) -> AnchorState:
    """펀드의 첫 앵커 상태.

    Args:
        account_total: 계좌 총액.
        contributed: 순 투입 원금.
        balance: 지금 장부 잔고 — `drift` 모드의 유휴 현금(계좌 - 장부)을 잡는 기준.

    Returns:
        `account` 면 idle 0, `drift` 면 idle = 계좌 - 장부(음수면 0).
    """
    mode = choose_mode(account_total, contributed)
    idle = Decimal(0) if mode == MODE_ACCOUNT else max(account_total - balance, Decimal(0))
    return AnchorState(mode=mode, idle=idle)


def row_time(raw: object) -> datetime | None:
    """자금 원장 행의 시각 — Gate 는 epoch 초(소수), Binance 는 ms 문자열.

    Args:
        raw: 행의 `time` 값.

    Returns:
        UTC 시각. 못 읽으면 None.
    """
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if value <= 0:
        return None
    if value > Decimal(10**11):  # ms
        value /= Decimal(1000)
    return datetime.fromtimestamp(float(value), tz=UTC)


def dnw_since(
    rows: list[dict[str, Any]], after: datetime | None, seen: tuple[str, ...]
) -> tuple[Decimal, tuple[str, ...]]:
    """거래소 자금 원장에서 아직 안 센 입출금 합.

    Args:
        rows: `account_book` 행들 (`type` · `change` · `time`).
        after: 이 시각 이후의 행만 (None 이면 전부 — 첫 앵커는 호출자가 빈 목록을 준다).
        seen: 이미 흡수한 열쇠.

    Returns:
        `(합, 새 seen)`. 열쇠는 `시각:변화` 라 같은 행이 다시 와도 두 번 안 센다.
    """
    total = Decimal(0)
    keys = list(seen)
    for row in rows:
        if str(row.get("type", "")) not in DNW_TYPES:
            continue
        at = row_time(row.get("time"))
        if at is None or (after is not None and at <= after):
            continue
        try:
            change = Decimal(str(row.get("change", "0") or "0"))
        except (InvalidOperation, ValueError):
            continue
        key = f"{row.get('time')}:{change}"
        if key in keys:
            continue
        keys.append(key)
        total += change
    return total, tuple(keys[-200:])


def outside_since(
    rows: list[dict[str, Any]],
    after: datetime | None,
    members: frozenset[str],
    order_texts: Mapping[str, str],
    stop_texts: Mapping[str, str],
) -> Decimal:
    """마지막 앵커 뒤 **매매법 밖 주문**(사람)의 손익 · 수수료 합 (순수).

    사용자 2026-10-15: *"내가 수동으로 이것저것 했는데 그거 다 빼고싶어"* — 같은 계좌의 사람 매매
    손익을 성과가 아니라 입출금으로 본다(10-09 · 10-15 에 손으로 하던 재분류를 매 틱 자동으로).

    Args:
        rows: `account_book` 행들(`type` · `change` · `time` · `contract` ·
            `text` = `계약:주문번호`).
        after: 이 시각 뒤의 행만. None 이면 0(첫 앵커는 과거를 세지 않는다).
        members: 펀드 종목 — 아닌 계약의 손익 · 수수료 · 펀딩은 전부 사람 몫이다.
        order_texts: 끝난 주문 `{번호: text}`.
        stop_texts: 끝난 조건부 `{조건부 id: 원래 주문 text}` — 거래소가 발동시킨 `ao-{id}`
            를 가른다.

    Returns:
        사람 몫 합(손실 음수 · 이익 양수). 주문을 못 찾은 줄은 **매매법 몫으로 둔다** — 모르는
        손실을 흐름으로 숨기지 않는다.

    Note:
        펀드 종목 계약에서 `t-` 이름 주문 = 매매법 · `ao-{id}` 는 그 조건부의 원래 이름이 `t-` 면
        우리 손절(매매법) · 그 밖 이름(`web…` · `app…`)은 사람. 펀드 종목의 펀딩 줄은 매매법이다.
    """
    if after is None:
        return Decimal(0)
    total = Decimal(0)
    for row in rows:
        kind = str(row.get("type", ""))
        if kind not in ("pnl", "fee", "fund"):
            continue
        at = row_time(row.get("time"))
        if at is None or at <= after:
            continue
        text = str(row.get("text") or "")
        contract = str(row.get("contract") or text.split(":")[0])
        try:
            change = Decimal(str(row.get("change", "0") or "0"))
        except (InvalidOperation, ValueError):
            continue
        if contract and contract not in members:
            total += change
            continue
        if kind == "fund":
            continue
        _, sep, order_id = text.partition(":")
        name = order_texts.get(order_id.strip()) if sep else None
        if name is None or name.startswith("t-"):
            continue
        if name.startswith("ao-"):
            origin = stop_texts.get(name[3:])
            if origin is None or origin.startswith("t-"):
                continue
        total += change
    return total


def anchored(
    state: AnchorState,
    account_total: Decimal,
    dnw: Decimal,
    now: datetime,
    outside: Decimal = Decimal(0),
) -> Anchored:
    """이번 틱의 앵커값 — 모드에 따라 거래소 입출금을 펀드 입출금으로 보거나 유휴 현금으로 보낸다.

    Args:
        state: 지금 앵커 상태.
        account_total: 계좌 총액(현금 + 증거금 · 미실현 제외).
        dnw: 마지막 앵커 뒤 거래소 입출금 합(+입금 -출금).
        now: 앵커 시각.
        outside: 마지막 앵커 뒤 매매법 밖 주문(사람)의 손익 · 수수료(`outside_since`) —
            입출금처럼 다룬다.

    Returns:
        `Anchored` — 입출금 직전 평가금액 · 자동 기록할 입출금 · 새 상태.

    Note:
        account: 계좌 총액에서 방금 들어온 입출금(과 사람 매매 손익)을 빼면 그 직전의 평가금액이고,
            그 합은 흐름으로 적는다 — 사람 손익은 성과(TWR · 낙폭)에 안 든다.
        drift:   입출금 · 사람 손익은 유휴 현금으로 가고(펀드 몫 아님) 총자본 = 계좌 - 유휴.
            흐름은 0.
    """
    if state.mode == MODE_ACCOUNT:
        flow = dnw + outside
        before = account_total - flow
        return Anchored(before, flow, replace(state, at=now), outside)
    idle = max(state.idle + dnw + outside, Decimal(0))
    return Anchored(account_total - idle, Decimal(0), replace(state, idle=idle, at=now), outside)


def manual_flow(state: AnchorState, amount: Decimal) -> AnchorState:
    """화면에서 기록한 펀드 입출금 — `drift` 모드에선 유휴 현금에서 펀드로 옮겨 온 것이다.

    Args:
        state: 앵커 상태.
        amount: +입금 -출금.

    Returns:
        idle 을 옮긴 새 상태. `account` 모드는 그대로(거래소 입출금이 자동으로 흐름이 된다).
    """
    if state.mode == MODE_ACCOUNT:
        return state
    return replace(state, idle=max(state.idle - amount, Decimal(0)))
