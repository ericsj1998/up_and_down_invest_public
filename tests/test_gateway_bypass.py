"""게이트 우회 차단 — 정적 검사 (P0-5-4b · 절대 규칙 #0 · plan D-12).

`OrderGateway` 가 어댑터 획득을 독점하는지 **소스를 파싱해** 확인한다. 런타임 테스트로는
"부르지 않은 경로"를 잡을 수 없기 때문이다 — 우회 코드는 정의상 게이트를 부르지 않는다.

Phase 0 에는 구체 어댑터가 아직 없어 위반이 0건이다. 그래서 **검사기 자체가 위반을
잡는지**도 함께 검증한다. 아무것도 잡지 못하는 검사기는 통과해도 의미가 없다.
"""

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "updown"

#: 구체 어댑터가 사는 패키지. 이들을 import 하는 것이 곧 "직접 획득"이다.
CONCRETE_ADAPTER_PACKAGES = (
    "updown.marketdata.upbit",
    "updown.marketdata.toss",
    "updown.marketdata.paper",
    "updown.marketdata.backtest",
)

#: 타입 표기용으로 import 해도 되는 것 — 프로토콜은 구현체가 아니다.
PROTOCOL_NAMES = frozenset({"BrokerAdapter", "DerivativesAdapter", "Capability"})

#: 구체 어댑터를 다뤄도 되는 파일 (저장소 루트 기준 상대 경로).
#:
#: **둘뿐이고, 용도가 갈린다.**
#:
#: | 파일 | 경로 | Phase 0~1 에 반환하는 것 |
#: |---|---|---|
#: | `execution/gateway.py` | **주문** | 아무것도 (예외를 던진다 — plan D-12) |
#: | `marketdata/provider.py` | **조회** | 조회 전용 어댑터 |
#:
#: 조회 지점을 왜 여는가: 캔들 수집(P0-8-6)은 주문과 무관한데, 검사가 구체 어댑터의 모든
#: import 를 잡으므로 `apps/engine/main.py` 가 걸린다. 그 파일을 허용하면 **가드의 이빨이
#: 빠진다** — 이후 그 파일에서 무엇을 import 해도 통과한다. 대신 조회 전용 획득 지점을
#: 하나 두고 **거기만** 허용한다.
#:
#: 이것이 규칙 #0 을 약화시키지 않는 이유: 조회 경로로 얻은 어댑터도 `submit_order` 는
#: `OrderPathNotAvailableError` 를 던진다 (P0-7-6). 차단이 어댑터 자체에 있다.
ALLOWLIST = frozenset(
    {
        "src/updown/execution/gateway.py",  # 주문 경로의 유일한 획득 지점
        "src/updown/marketdata/provider.py",  # 조회 경로의 유일한 획득 지점
    }
)


def _is_concrete_adapter_module(module: str) -> bool:
    return any(module == pkg or module.startswith(f"{pkg}.") for pkg in CONCRETE_ADAPTER_PACKAGES)


def owning_adapter_package(rel_path: str) -> str | None:
    """이 파일이 **어느 구체 어댑터 패키지 안에** 있는지 알려준다.

    Args:
        rel_path: 저장소 루트 기준 상대 경로 (`src/updown/marketdata/upbit/client.py`).

    Returns:
        소속 패키지의 모듈 경로. 어댑터 패키지 밖이면 None.

    Note:
        **어댑터 패키지는 스스로를 조립할 수 있어야 한다** — `upbit/adapter.py` 가
        `upbit/client.py` 를 import 하는 것은 "게이트 우회 획득"이 아니라 패키지 내부
        구성이다. 절대 규칙 #0 이 막는 것은 **외부 코드가 어댑터를 손에 넣는 것**이다.
    """
    dotted = rel_path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    for pkg in CONCRETE_ADAPTER_PACKAGES:
        if dotted == pkg or dotted.startswith(f"{pkg}."):
            return pkg
    return None


def find_adapter_acquisitions(source: str, rel_path: str) -> list[str]:
    """소스에서 구체 어댑터 획득 흔적을 찾는다.

    Args:
        source: 파이썬 소스 코드.
        rel_path: 보고용 상대 경로. 소속 패키지 판정에도 쓴다.

    Returns:
        위반 설명 목록. 없으면 빈 리스트.

    Note:
        두 가지를 본다 — 구체 어댑터 **모듈 import** 와, `*Adapter` 이름의
        **직접 호출(생성)**. 프로토콜(`BrokerAdapter` 등)은 타입 표기에 필요하므로
        제외한다.

        **자기 패키지 내부 import 는 위반이 아니다** (`owning_adapter_package` 참조).
        다른 어댑터 패키지를 건드리면 여전히 위반이다 — `upbit/` 가 `paper/` 를
        import 하면 어댑터 간 결합이 생겨 교체 가능성이 깨진다.
    """
    violations: list[str] = []
    tree = ast.parse(source, filename=rel_path)
    own_package = owning_adapter_package(rel_path)

    def is_violation(module: str) -> bool:
        if not _is_concrete_adapter_module(module):
            return False
        if own_package is None:
            return True
        return not (module == own_package or module.startswith(f"{own_package}."))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_violation(alias.name):
                    violations.append(f"{rel_path}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if is_violation(module):
                names = ", ".join(a.name for a in node.names)
                violations.append(f"{rel_path}:{node.lineno} from {module} import {names}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if isinstance(func, ast.Attribute):
                name = func.attr
            if name and name.endswith("Adapter") and name not in PROTOCOL_NAMES:
                violations.append(f"{rel_path}:{node.lineno} {name}(...) 직접 생성")

    return violations


def test_no_code_outside_the_gateway_acquires_an_adapter() -> None:
    """DoD 2-1 — 게이트 외부에서 구체 어댑터를 획득하는 코드가 0건이어야 한다."""
    repo_root = SRC_ROOT.parent.parent
    violations: list[str] = []

    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        if rel in ALLOWLIST:
            continue
        violations.extend(find_adapter_acquisitions(path.read_text(encoding="utf-8"), rel))

    assert violations == [], (
        "브로커 어댑터는 execution/gateway.py 의 OrderGateway 를 통해서만 얻는다 "
        "(절대 규칙 #0, spec §12.4). 위반:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from updown.marketdata.upbit.adapter import UpbitAdapter\n",
            id="구체 어댑터 from-import",
        ),
        pytest.param(
            "import updown.marketdata.paper.adapter\n",
            id="구체 어댑터 모듈 import",
        ),
        pytest.param(
            "def f():\n    return UpbitAdapter(client=None)\n",
            id="어댑터 직접 생성",
        ),
        pytest.param(
            "def f(reg):\n    return reg.PaperAdapter()\n",
            id="속성 경유 생성",
        ),
    ],
)
def test_checker_actually_detects_violations(source: str) -> None:
    """검사기가 실효성이 있는지 확인한다.

    Phase 0 에는 실제 위반이 0건이라 위 테스트는 아무것도 안 해도 통과한다.
    이 테스트가 없으면 "검사기가 망가져서 통과한 것"과 구분할 수 없다 —
    경계 위반 재현(P0-2-4)과 같은 이유다.
    """
    assert find_adapter_acquisitions(source, "synthetic.py") != []


def test_checker_allows_protocol_imports() -> None:
    """프로토콜 import 는 위반이 아니다 — 타입 표기에 필요하다."""
    source = "from updown.marketdata.adapter import BrokerAdapter, Capability\n"
    assert find_adapter_acquisitions(source, "synthetic.py") == []


def test_adapter_package_may_assemble_itself() -> None:
    """어댑터 패키지 내부 import 는 위반이 아니다 (P0-7 에서 확인).

    `upbit/adapter.py` 가 `upbit/client.py` 를 쓰는 것은 게이트 우회 획득이 아니라
    패키지 내부 구성이다. 이것까지 막으면 어댑터를 여러 모듈로 나눌 수 없다.
    """
    source = (
        "from updown.marketdata.upbit.client import UpbitClient\n"
        "from updown.marketdata.upbit.mapping import to_candle\n"
    )
    assert find_adapter_acquisitions(source, "src/updown/marketdata/upbit/adapter.py") == []


def test_cross_adapter_imports_are_still_violations() -> None:
    """**면제는 자기 패키지에만 적용된다.**

    `upbit/` 가 `paper/` 를 import 하면 어댑터 간 결합이 생겨 "브로커 교체가 상위
    로직에 영향을 주지 않는다"는 전제가 깨진다 (spec §4.2).
    """
    source = "from updown.marketdata.paper.adapter import PaperAdapter\n"
    assert find_adapter_acquisitions(source, "src/updown/marketdata/upbit/adapter.py") != []


def test_outside_code_importing_an_adapter_is_still_a_violation() -> None:
    """면제가 외부로 새지 않는지 확인한다 — 이게 규칙 #0 의 본체다."""
    source = "from updown.marketdata.upbit.adapter import UpbitAdapter\n"
    for caller in (
        "src/updown/decision/risk_manager.py",
        "src/updown/orchestration/backtest.py",
        "src/updown/marketdata/ingest.py",  # 같은 marketdata 라도 upbit 패키지 밖이다
    ):
        assert find_adapter_acquisitions(source, caller) != [], caller


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("src/updown/marketdata/upbit/adapter.py", "updown.marketdata.upbit"),
        ("src/updown/marketdata/upbit/__init__.py", "updown.marketdata.upbit"),
        ("src/updown/marketdata/adapter.py", None),
        ("src/updown/execution/gateway.py", None),
    ],
)
def test_owning_package_resolution(rel_path: str, expected: str | None) -> None:
    """경로 → 소속 패키지 판정이 정확해야 면제 범위가 새지 않는다."""
    assert owning_adapter_package(rel_path) == expected


def test_allowlist_contents_are_pinned() -> None:
    """허용 목록이 **조용히** 늘어나는 것을 막는다.

    파일을 추가하려면 이 테스트를 고쳐야 하고, 그러면 리뷰에 드러난다.
    P0-9-7 에서 조회 지점(`marketdata/provider.py`)이 추가됐고, 그때 이 테스트가 실제로
    걸려서 결정이 리뷰에 올라왔다 — 장치가 의도대로 동작했다.
    """
    assert (
        frozenset(
            {
                "src/updown/execution/gateway.py",
                "src/updown/marketdata/provider.py",
            }
        )
        == ALLOWLIST
    )


def test_engine_does_not_import_a_concrete_adapter() -> None:
    """**engine 이 어댑터를 직접 만들지 않는지** 실제 파일로 확인한다.

    조회 지점을 둔 이유가 이것이다 — `apps/engine/main.py` 를 허용 목록에 넣었다면
    이후 그 파일에서 무엇을 import 해도 통과했을 것이다.
    """
    repo_root = SRC_ROOT.parent.parent
    path = SRC_ROOT / "apps" / "engine" / "main.py"
    rel = path.relative_to(repo_root).as_posix()
    assert rel not in ALLOWLIST, "engine 진입점이 허용 목록에 들어가면 가드가 무의미해진다"
    assert find_adapter_acquisitions(path.read_text(encoding="utf-8"), rel) == []


def test_read_only_provider_cannot_place_orders() -> None:
    """조회 지점이 규칙 #0 의 우회로가 아님을 계약으로 고정한다.

    조회 경로로 얻은 어댑터도 주문 메서드는 예외를 던진다 (P0-7-6). 차단이 **어댑터 자체**에
    있으므로, 조회용 획득 지점을 열어도 주문 경로가 생기지 않는다.
    """
    import asyncio

    from updown.common.domain.instrument import Market
    from updown.marketdata.provider import MarketDataProvider
    from updown.marketdata.upbit.adapter import OrderPathNotAvailableError

    async def check() -> None:
        provider = MarketDataProvider()
        try:
            adapter = provider.adapter_for(Market.UPBIT)
            with pytest.raises(OrderPathNotAvailableError):
                await adapter.get_balance()
        finally:
            await provider.aclose()

    asyncio.run(check())
