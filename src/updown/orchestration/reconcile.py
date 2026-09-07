"""거래소 대조 — **원장이 말하는 것과 거래소에 실제로 있는 것을 맞춰 본다**.

사용자 요구 2026-08-30 (배포 준비): *"거래소의 원장과 현재 내 주문들을 비교해서 싱크를
맞춰주는 기능. 배포했을 때 어떤 오류로 거래소 원장만 남아있는 경우 큰 손실을 볼 수 있다."*

## ⛔ "싱크" 가 아니라 "대조" 다

**자동으로 맞추지 않는다.** 원장과 거래소가 갈렸을 때 어느 쪽이 진실인지는 상황마다
다르고, 잘못 고르면 둘 다 나쁘다:

    원장을 자동으로 고친다   → 결정론이 깨진다 (규칙 #5). 같은 입력에 다른 원장이 된다
    거래소를 자동으로 닫는다 → **손익이 사람 모르게 확정된다**. 되돌릴 수 없다

⇒ 이 모듈은 **갈린 사실을 이름 붙여 내놓기만** 한다. 무엇을 할지는 사람이 정한다.
  예외는 축 C(주인 없는 주문) 하나인데, 그것은 포지션이 없을 때만이라 안전하다.

## 세 가지 갈림

| 축 | 거래소 | 원장 | 뜻 | 위험 |
|---|---|---|---|---|
| **A 무주공산 포지션** | 있다 | 없다 | 아무도 관리 안 하는 돈 | 🔴 **손절이 없다** |
| **B 유령 원장** | 없다 | 있다 | 원장이 거짓말한다 | 🔴 성과·사이징이 다 틀린다 |
| **C 주인 없는 주문** | 주문만 | 없다 | 지난 판의 잔재 | 🟡 새 포지션을 닫아 버린다 |
| **D 부분 무방비** | 있다 | 있다 | 손절이 **일부만** 덮는다 | 🔴 그만큼이 무방비 |

A 가 사용자가 말한 바로 그 경우다. 6x 에서 손절 없는 포지션은 16% 움직임에 청산이다.

D 는 그 뒤에 실물로 나왔다 (2026-08-26 이력): 손절이 발동했는데 **118 계약 중 35 만
체결되고 83 이 Gate 의 가격보호(`price_rate_proteced`)에 죽었다.** 남은 83 은 그때부터
무방비였고, 화면에는 조건부가 걸려 있으니 **지켜지는 것처럼 보였다.**

⚠️ **부분 보호가 무보호보다 더 위험하다** — 무보호는 눈에 띄지만 부분 보호는 안 띈다.

## ⛔ 포지션이 있으면 주문을 건드리지 않는다

조건부는 그 포지션의 **유일한 보호막**이다 (`leftovers.py` 와 같은 원칙). 잔재를
치우려다 무방비 포지션을 만드는 것은 잔재를 남기는 것보다 훨씬 나쁘다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from updown.common.numeric import zero

ORPHAN = "reconcile_orphan_position"
GHOST = "reconcile_ghost_ledger"
STRAY = "reconcile_stray_orders"
NAKED = "reconcile_unprotected_size"
FILL_PENDING = "reconcile_fill_pending"

UNKNOWN = -1
"""보호 물량을 **못 셌다** — 0(보호가 없다)과 다르다.

⛔ 둘을 섞으면 조회 실패가 곧 "무방비" 가 되어 멀쩡한 판의 진입을 막는다.
"""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """(거래소, 종목) 한 칸의 사실.

    Attributes:
        market: 거래소.
        symbol: 종목.
        held: 거래소가 말하는 계약 수 (0 이면 없다 · 부호는 방향).
        orders: 거래소에 걸려 있는 주문 id 들 (조건부 + 지정가).
        owned: **어느 판이 이 종목을 맡고 있나** — 없으면 빈 문자열.
        on_book: 그 판의 원장이 **보유 중**이라고 말하나.
        protected: 조건부(손절)가 덮고 있는 계약 수. `UNKNOWN`(-1)이면 못 셌다.
        pending: 그 판이 이 종목에 **진입 지정가를 걸어 두고 기다리는 중**인가. 거래소에
            포지션이 있는데 원장이 모르면, 이것이 참일 때는 고아가 아니라 봉 사이 체결의
            **반영 대기**다 (2026-09-06 사고).
    """

    market: str
    symbol: str
    held: int
    orders: tuple[str, ...]
    owned: str
    on_book: bool
    protected: int = UNKNOWN
    pending: bool = False


@dataclass(frozen=True, slots=True)
class Finding:
    """갈린 사실 하나 — 화면의 `watch` 배너에 그대로 실을 수 있는 모양이다."""

    code: str
    market: str
    symbol: str
    level: str
    detail: str
    orders: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """(거래소, 종목) 열쇠 — 진입 차단이 이것으로 걸린다."""
        return f"{self.market}:{self.symbol}"

    def as_watch(self, at: datetime) -> dict[str, str]:
        """콘솔 배너가 읽는 모양 (`WATCHED`/`ORPHANS` 와 같은 열쇠).

        Args:
            at: 대조 시각.

        Returns:
            `run`·`code`·`detail`·`at`·`market`·`symbol`.

        Note:
            ⭐ `market`·`symbol` 을 따로 싣는다 — 배너의 *이어받기* 단추가 `run`
            문자열("MARKET SYMBOL")을 되쪼개지 않고 그대로 쓰게 한다 (2026-09-01).
        """
        return {
            "run": f"{self.market} {self.symbol}",
            "code": self.code,
            "detail": self.detail,
            "at": at.isoformat(),
            "market": self.market,
            "symbol": self.symbol,
        }


def covered(resting: list[dict[str, str]], held: int) -> int:
    """조건부(손절)가 덮고 있는 **계약 수**를 센다 (축 D).

    Args:
        resting: 그 종목에 걸려 있는 주문들 (조건부 + 지정가).
        held: 거래소가 말하는 보유 계약 (부호 포함).

    Returns:
        덮인 계약 수. 숫자를 못 읽으면 `UNKNOWN`.

    Note:
        ⚠️ **`size: 0` 은 "0 계약" 이 아니라 "전량 닫기" 다** (Gate 의미론 ·
        `leftovers.py` 가 같은 함정을 적어 뒀다). 0 으로 세면 멀쩡한 전량 손절이
        무방비로 읽혀 진입이 막힌다.

        ⛔ **`trigger_price` 가 있는 것만 센다.** 지정가 익절은 손절이 아니다 —
        불리하게 갈 때 안 채워지므로 보호로 세면 거짓 안심이다.

        ⚠️ 방향은 안 본다. 우리가 조건부로 거는 것은 손절뿐이고, 방향까지 따지려면
        표시가가 필요한데 그 값이 여기 없다 — **모르는 것으로 판정하지 않는다.**
    """
    if held == 0:
        return 0
    total = 0
    for row in resting:
        if not row.get("trigger_price"):
            continue
        try:
            size = int(Decimal(str(row.get("size", "0") or "0")))
        except (ArithmeticError, ValueError):
            return UNKNOWN
        total += abs(held) if size == 0 else abs(size)
    return total


def partial_fills(rows: list[dict[str, str]], since: float) -> tuple[dict[str, int], float]:
    """**다 못 채워진 채 끝난 주문**을 센다 (2026-08-30 · 실계좌 전 빈도 파악).

    Args:
        rows: 거래소 체결 이력.
        since: 이미 센 마지막 시각(epoch). 그보다 새 것만 센다.

    Returns:
        `({분류: 건수}, 새 시각)`. 분류는 `손절:{사유}` 또는 `우리주문:{사유}`.

    Note:
        🔴 **왜 세나**: 2026-08-26 에 손절이 발동했는데 118 계약 중 35 만 체결되고
        83 이 Gate 의 `price_rate_proteced` 에 죽었다. 러너가 1초 뒤 남은 83 에 손절을
        다시 걸어 사고로 이어지진 않았지만, **그 일이 있었다는 사실을 아무도 안 셌다.**

        실계좌로 갈 때 이 빈도가 판단 재료다 — 테스트넷이라 호가가 얇아서 나는 것인지,
        실계좌에서도 나는 것인지 알아야 배율을 유지할지 정할 수 있다.

        ⚠️ **경보가 아니라 계수기다.** 재장착은 러너가 이미 한다(`_guard_stop` · 1초).
        여기서 진입을 막거나 배너를 띄우지 않는다 — 이미 처리된 일에 경보를 띄우면
        진짜 경보가 묻힌다.

        ⭐ `ao-` 로 시작하는 이름은 **조건부가 발동해 거래소가 만든 주문**이다
        (`ui.tsx` 가 같은 규칙을 쓴다). 그것이 부분 체결로 끝난 것과 우리가 낸 청소
        주문이 부분 체결된 것은 **무게가 다르다** — 앞은 보호막이 뚫린 것이다.
    """
    counts: dict[str, int] = {}
    latest = since
    for row in rows:
        try:
            when = float(row.get("finish_time") or 0)
        except (TypeError, ValueError):
            continue
        if when <= since:
            continue
        latest = max(latest, when)
        if zero(row.get("left")):
            continue
        who = "손절" if str(row.get("text", "")).startswith("ao-") else "우리주문"
        why = str(row.get("finish_as") or row.get("status") or "?")
        key = f"{who}:{why}"
        counts[key] = counts.get(key, 0) + 1
    return counts, latest


def compare(snapshots: list[Snapshot]) -> list[Finding]:
    """스냅샷들을 훑어 갈린 것만 골라낸다 — **순수 함수**다 (I/O 없음).

    Args:
        snapshots: (거래소, 종목) 칸들.

    Returns:
        갈린 것들. 아무것도 없으면 빈 목록.

    Note:
        🔴 **순수 함수로 둔 이유**: 이 판정이 틀리면 무방비 포지션을 놓치거나(A 를 놓침)
        멀쩡한 판의 진입을 막는다(가짜 B). 둘 다 비싸므로 거래소 없이 표로 시험할 수
        있어야 한다 — `tests/test_reconcile.py` 가 칸마다 하나씩 짚는다.
    """
    out: list[Finding] = []
    for shot in snapshots:
        # ── A'. 거래소에 있는데 원장에 없다 — 그런데 그 판이 **지정가를 걸고 기다리는
        #    중**이다 ───────────────────────────────────────────────────────
        #
        # 🔴 2026-09-06 실계좌: 4h 축의 NEAR 지정가가 봉 중간에 채워져 원장이 4시간 동안
        #    몰랐다. 그것은 아무도 관리하지 않는 포지션이 아니라 **반영이 늦은 우리 포지션**
        #    이다 — 러너가 30초 점검에서 옮긴다. 고아로 분류하면 콘솔이 "닫기/되받기" 를
        #    권하고, 되받기는 이미 그 판의 것인 포지션을 두 번 잡는다.
        #
        # ⚠️ warn 이라 `blocked_keys` 는 안 막는다 — 새 진입은 세션의 대기 표(`_waiting`)
        #    가 이미 자리를 점유해 막고 있고, 손절은 러너가 원장에 옮기는 순간 건다.
        if shot.held != 0 and not shot.on_book and shot.pending:
            out.append(
                Finding(
                    code=FILL_PENDING,
                    market=shot.market,
                    symbol=shot.symbol,
                    level="warn",
                    detail=(
                        f"거래소에 {shot.held:+d} 계약이 있고 판 {shot.owned or shot.symbol} 의 "
                        "지정가가 대기 중이다 — 봉 사이에 채워진 진입이며 러너가 30초 안에 "
                        "원장에 옮기고 손절을 건다. 계속 남으면 고아로 본다"
                    ),
                    orders=shot.orders,
                )
            )
            continue
        # ── A. 거래소에 있는데 원장에 없다 ──────────────────────────────
        if shot.held != 0 and not shot.on_book:
            who = f"판 {shot.owned} 의 원장" if shot.owned else "어느 판도"
            out.append(
                Finding(
                    code=ORPHAN,
                    market=shot.market,
                    symbol=shot.symbol,
                    level="error",
                    detail=(
                        f"거래소에 {shot.held:+d} 계약이 있는데 {who} 그것을 모른다 — "
                        "**아무도 관리하지 않는 포지션**이다. 손절이 걸려 있다는 보장이 "
                        "없으므로 콘솔에서 확인하고 닫거나 이어받는다"
                    ),
                    orders=shot.orders,
                )
            )
            continue
        # ── D. 들고 있는데 **손절이 그만큼을 못 덮는다** ────────────────
        #
        # 🔴 2026-08-26 실측: 손절이 발동했는데 118 계약 중 **35 만 체결되고 83 이
        #    Gate 의 가격보호(`price_rate_proteced`)에 죽었다.** 남은 83 은 그때부터
        #    무방비다. 지금 대조는 "포지션이 있나/없나" 만 봐서 이것을 못 봤다.
        #
        # ⚠️ **부분 보호가 무보호보다 더 위험하다** — 화면에 조건부가 보이므로
        #    사람은 지켜지고 있다고 읽는다.
        if shot.held != 0 and shot.protected != UNKNOWN and shot.protected < abs(shot.held):
            gap = abs(shot.held) - shot.protected
            out.append(
                Finding(
                    code=NAKED,
                    market=shot.market,
                    symbol=shot.symbol,
                    level="error",
                    detail=(
                        f"{abs(shot.held)} 계약을 들고 있는데 조건부는 "
                        f"{shot.protected} 계약만 덮는다 — **{gap} 계약이 무방비**다. "
                        "손절이 부분 체결로 끝났거나(가격보호·얇은 호가) 아예 안 걸렸다"
                    ),
                    orders=shot.orders,
                )
            )
            continue
        # ── B. 원장은 들고 있다는데 거래소가 비었다 ────────────────────
        if shot.held == 0 and shot.on_book:
            out.append(
                Finding(
                    code=GHOST,
                    market=shot.market,
                    symbol=shot.symbol,
                    level="error",
                    detail=(
                        f"판 {shot.owned} 의 원장은 보유 중이라는데 거래소에 포지션이 "
                        "없다 — 원장이 사실과 갈렸다. 성과도 사이징도 이 원장을 쓰므로 "
                        "**그대로 두면 없는 돈으로 다음 판을 잰다**"
                    ),
                    orders=shot.orders,
                )
            )
            continue
        # ── C. 포지션은 없는데 주문만 남았다 ───────────────────────────
        # ⚠️ 판이 맡고 있으면 잔재가 아니다 — 진입 지정가가 걸려 있는 정상 상태다.
        if shot.held == 0 and shot.orders and not shot.owned:
            out.append(
                Finding(
                    code=STRAY,
                    market=shot.market,
                    symbol=shot.symbol,
                    level="warn",
                    detail=(
                        f"포지션 없이 주문 {len(shot.orders)}건이 남아 있다 — 지난 판의 "
                        "잔재다. 같은 종목으로 새 판을 띄우면 그 트리거가 **새 포지션을 "
                        "통째로 닫는다**. 회수해도 안전하다 (포지션이 없다)"
                    ),
                    orders=shot.orders,
                )
            )
    return out


def blocked_keys(findings: list[Finding]) -> frozenset[str]:
    """**신규 진입을 보류할** (거래소, 종목) 열쇠들.

    Args:
        findings: `compare` 의 결과.

    Returns:
        `"GATE:BTC_USDT"` 꼴의 열쇠 집합.

    Note:
        🔴 **경보만으로는 모자란다.** 사람이 자는 동안 갈린 상태 위에 새 포지션이
        쌓이면 사고가 곱해진다 — 관리 안 되는 포지션 옆에 관리되는 포지션을 하나 더
        놓는 셈이다.

        ⛔ **나가는 길은 절대 안 막는다** (§1.2.1). 이 집합은 `session.step()` 의
        진입 판단에만 쓰이고, 손절·청산·스탑 상향은 그 위에서 이미 끝나 있다.

        ⚠️ `warn`(축 C)은 안 막는다 — 주문만 남은 것은 회수하면 되고, 그것 때문에
        정상 종목의 진입을 멈추면 사람이 배너를 끄려고 급하게 누르게 된다.
    """
    return frozenset(item.key for item in findings if item.level == "error")
