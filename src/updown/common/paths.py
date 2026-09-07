"""산출물이 쌓이는 곳 — **한 곳에서만 정한다**.

## 🔴 왜 이 모듈이 생겼나

백테스트는 **호스트에서** 돌고(`scripts/run_matrix.py`) API 는 **컨테이너에서** 돈다.
그런데 양쪽 코드가 각자 `Path("logs/...")` 라고 적어 뒀고, 상대 경로는 CWD 기준으로
풀린다 — 컨테이너의 CWD 는 `/app` 이고 거기 `/app/logs` 는 **명명 볼륨**이었다.

결과: 매트릭스가 호스트 `logs/progress` 에 열심히 쓰는 동안 API 는 빈 볼륨을 훑으며
`{"jobs": []}` 를 돌려줬다. 화면은 **정상적으로 "아무것도 없음"** 을 그렸다.

같은 이유로 `results`·`charts`·`pool` 도 전부 빈 화면이었다. 새 기능을 붙일 때마다
같은 자리에서 다시 터졌고, 그때마다 그 기능만 고쳤다 — **원인이 아니라 증상을 고친
것이다.**

## 규칙 셋

1. **경로는 여기서만 만든다.** 새 산출물 디렉터리는 `under()` 로 얻는다.
2. **절대 경로로 푼다.** CWD 가 어디든 같은 곳을 가리킨다.
3. **어디를 보는지 말한다.** API 가 시작할 때 남기고 화면에도 싣는다 — 다음에 이런
   일이 나면 **한눈에** 보여야 한다 (절대 규칙 #8).

## 배포마다 다른 것은 마운트뿐이다

    dev          호스트 `logs/` 를 api·engine 에 바인드 — 호스트 스크립트와 같은 곳
    paper/live   **하나의** 공유 볼륨을 api·engine 이 함께 — 컨테이너끼리도 갈리면
                 같은 사고가 난다 (실제로 `apilogs`·`enginelogs` 로 갈려 있었다)
"""

import os
from pathlib import Path

ENV_VAR = "UPDOWN_LOGS_DIR"
"""산출물 루트를 덮어쓰는 환경변수.

컨테이너와 호스트가 **같은 값**을 봐야 한다. 안 맞으면 이 모듈이 막으려는 사고가
그대로 재현된다.
"""


def logs_root() -> Path:
    """산출물 루트 (절대 경로).

    Returns:
        `UPDOWN_LOGS_DIR` 이 있으면 그것, 없으면 CWD 기준 `logs`. 항상 절대 경로다.

    Note:
        디렉터리를 **만들지 않는다.** 없는 것도 정보다 — 없는데 조용히 만들면
        "여기가 맞나"를 확인할 기회가 사라지고, 빈 디렉터리를 훑으며 "결과 없음"을
        보고하게 된다. 만드는 것은 실제로 쓰는 쪽의 일이다.
    """
    return Path(os.environ.get(ENV_VAR) or "logs").resolve()


def under(*parts: str) -> Path:
    """산출물 루트 아래 경로.

    Args:
        *parts: 하위 경로 조각들.

    Returns:
        절대 경로.
    """
    return logs_root().joinpath(*parts)


def describe() -> dict[str, object]:
    """지금 어디를 보고 있는지 — 화면·로그에 그대로 싣는다.

    Returns:
        `{root, exists, results, progress}`.

    Note:
        🔴 **개수까지 함께 낸다.** 경로만 내면 "맞는 경로 같은데 왜 비었지"에서 멈추고,
        개수를 함께 보면 "여긴 0건인데 저긴 120건"이 바로 보인다. 이 사고의 정체가
        정확히 그것이었다.
    """
    root = logs_root()
    results = root / "results"
    progress = root / "progress"
    return {
        "root": str(root),
        "exists": root.exists(),
        "results": len(list(results.glob("*.json"))) if results.exists() else 0,
        "progress": len(list(progress.glob("*.json"))) if progress.exists() else 0,
    }
