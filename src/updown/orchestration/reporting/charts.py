"""매매 차트 산출물 읽기 — `logs/charts/*.json` (P5 §5-13 A9).

## 왜 여기로 올렸나

이 로직은 `scripts/progress_server.py` 안에만 있었고, 그래서 **React 화면이 매매 차트를
못 봤다**. §5-9 A2 가 `GET /backtest/charts/{run_id}` 를 선언했는데 구현이 빠진 이유가
그것이다 — 8765 서버에서 이미 보이니 아쉬움이 없었다 (§5-13).

API 쪽에 같은 코드를 다시 쓰면 A1 이 경고한 **두 벌**이 된다. 파일 형식은 측정
스크립트가 소유하므로, 읽는 쪽은 한 군데여야 한다.

## ⛔ 여기서 판정하지 않는다

`store.py` 와 같은 규칙이다. 거래가 좋았는지 나빴는지는 이 계층의 일이 아니고,
차트는 **사람이 배선을 확인하는 진단**이다 (절대 규칙 #11 — 눈으로 채점하지 않는다).
"""

import json
from pathlib import Path
from typing import Any, cast

from updown.common.paths import under

DEFAULT_CHARTS_DIR = under("charts")
"""차트 위치 — `measure_run.py --export-charts` 가 쓰는 곳이다."""

type Chart = dict[str, Any]
"""차트 JSON 한 건.

`Any` 인 이유는 `store.Report` 와 같다 — 스키마의 SSoT 는
`orchestration/backtest/chart.py` 이고 여기는 읽기만 한다.
"""


def inside(root: Path, target: Path) -> bool:
    """`target` 이 `root` 안에 있는가 — 경로 탈출 방어.

    Args:
        root: 허용 디렉터리.
        target: 검사할 경로.

    Returns:
        안에 있으면 True.

    Note:
        `run_id` 는 URL 에서 온다. 이름을 그대로 경로로 쓰지 않고 **해석한 뒤 소속을
        확인**한다 — `../../.env` 같은 이름이 들어와도 여기서 멈춘다.
    """
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def list_charts(root: Path = DEFAULT_CHARTS_DIR) -> list[Chart]:
    """차트 파일 목록 — 거래는 **개수만** 싣는다.

    Args:
        root: 차트 디렉터리.

    Returns:
        `{run_id, display, timeframe, preset, trades}` 목록. 디렉터리가 없으면 빈
        목록이다 — 아직 `--export-charts` 를 안 준 정상 상태다.

    Note:
        거래 배열을 빼는 이유는 크기다. 한 거래가 400봉이라 목록에 다 실으면 수 MB 가
        되고, 목록 화면은 개수만 필요하다.

        🔴 깨진 파일을 **조용히 건너뛰지 않는다.** `_error` 를 달아 목록에 남긴다 —
        빠뜨리면 "그 실행은 차트를 안 만들었다"와 구분되지 않는다 (절대 규칙 #8).
    """
    if not root.is_dir():
        return []
    items: list[Chart] = []
    for path in sorted(root.glob("*.json")):
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            items.append({"run_id": path.stem, "_error": str(exc)[:200]})
            continue
        if not isinstance(raw, dict):
            items.append({"run_id": path.stem, "_error": "최상위가 객체가 아니다"})
            continue
        data = cast(Chart, raw)
        trades = cast(list[object], data.get("trades") or [])
        items.append(
            {
                "run_id": data.get("run_id", path.stem),
                "display": data.get("display", ""),
                "timeframe": data.get("timeframe", ""),
                "preset": data.get("preset", ""),
                "trades": len(trades),
            }
        )
    return items


def read_chart(run_id: str, root: Path = DEFAULT_CHARTS_DIR) -> bytes | None:
    """차트 파일 원본을 읽는다.

    Args:
        run_id: 실행 식별자.
        root: 차트 디렉터리.

    Returns:
        JSON **바이트**. 없거나 허용되지 않는 이름이면 None.

    Note:
        파싱하지 않고 바이트를 그대로 흘린다. 수 MB 를 dict 로 만들었다가 다시
        직렬화할 이유가 없고, 그 과정에서 `Decimal` 문자열이 손상될 여지도 없앤다.
    """
    path = root / f"{run_id}.json"
    if not inside(root, path) or not path.exists():
        return None
    return path.read_bytes()
