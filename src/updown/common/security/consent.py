"""동의 문구의 단일 출처 — 어떤 문장에 동의했는지가 기록의 뜻이다 (T247 · 2026-09-09).

화면(`web/src/shell/disclaimer.ts`)과 **같은 문장·같은 버전**이어야 한다 — `tests/test_assistant.py`
가 두 파일을 대조한다. 문장을 고치면 버전을 올리고, 옛 버전으로 온 동의는 받지 않는다(400) —
사람이 보지 않은 문장에 동의시키지 않는다.
"""

from __future__ import annotations

DISCLAIMER_VERSION = "2026-09-09.1"
DISCLAIMER_TEXT = (
    "이 화면의 숫자·순위·후보는 정보 제공이며 투자 권유가 아닙니다. "
    "투자 판단과 그 결과의 책임은 본인에게 있습니다."
)


def consent_is_current(version: object) -> bool:
    """받은 동의 버전이 지금 문장의 것인가.

    Args:
        version: 화면이 보낸 버전.

    Returns:
        같으면 참.
    """
    return isinstance(version, str) and version == DISCLAIMER_VERSION


__all__ = ["DISCLAIMER_TEXT", "DISCLAIMER_VERSION", "consent_is_current"]
