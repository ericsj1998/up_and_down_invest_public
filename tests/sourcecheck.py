"""원문 검사용 — **코드만 남기고 설명글을 걷어낸다** (2026-08-30).

## 왜 필요했나

배선을 지키는 시험은 원문을 볼 수밖에 없다 (값이 아니라 **코드의 모양**이 문제이므로).
그런데 원문에는 주석과 독스트링이 같이 있고, 이 프로젝트의 주석은 길다 — 하루에 세 번
**시험이 자기 설명글에 걸렸다**:

    ① `test_chart_order`     주석의 `contracts_for` 를 "쓰고 있다" 로 읽었다
    ② `test_shared_overlays` 독스트링의 `playbooks.yml` 을 "판을 안다" 로 읽었다
    ③ `test_binance_venue`   주석의 *"testnet 호가창은…"* 을 "testnet 을 쓴다" 로 읽었다

셋 다 **거짓 실패**였지만, 반대 방향도 똑같이 가능하다 — 주석에 우연히 든 단어 때문에
**거짓 통과**하는 시험은 아무것도 안 지키면서 지키는 것처럼 보인다. 그쪽이 더 나쁘다.

⚠️ 파일마다 손으로 걷어내면 걷는 방법이 파일마다 달라진다. 한 곳에 둔다.
"""

from __future__ import annotations

import ast
from pathlib import Path


def code(path: Path | str) -> str:
    """이 파일의 **코드만** — 주석과 독스트링을 뺀다.

    Args:
        path: 읽을 파이썬 파일.

    Returns:
        설명글을 걷어낸 원문.

    Note:
        🔴 주석은 줄 단위로 걷고, 독스트링은 **구문 트리**로 찾아 지운다. 문자열
        리터럴을 눈으로 찾으면 평범한 문자열까지 지워져, 정작 검사하려던 코드가 사라진다.

        ⚠️ 완벽한 파서가 아니다 — 여러 줄 문자열 안의 `#` 은 살아남는다. 그래도 이
        프로젝트에서 문제였던 **주석·독스트링**은 확실히 걷는다.
    """
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    # 독스트링이 차지한 줄 번호를 모은다 — 지우지 않고 **비운다** (줄 수를 지켜야
    # 아래 주석 제거가 같은 줄을 본다).
    blank: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and first.end_lineno is not None:
            blank.update(range(first.lineno, first.end_lineno + 1))

    kept: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if number in blank:
            kept.append("")
            continue
        head = line.split("#", 1)[0]
        kept.append(head)
    return "\n".join(kept)
