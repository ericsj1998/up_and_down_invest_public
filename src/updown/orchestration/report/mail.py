"""SMTP 발송 + 발송 이력 (T35).

🔴 **안 보내진 것을 모르면 안 된다** (규칙 #8). 성공이든 실패든 한 줄을 남긴다 —
`logs/report_sends.jsonl`. 실패는 던지지 않고 거짓을 돌려준다: 리포트는 리스크 증가
행동이 아니라서 매매를 막지 않는다 (§1.2.1).

⚠️ 이것은 **외부 전송**이다. 본문에는 집계만 싣고(키·잔고 상세 금지), 수신자는 부르는
쪽이 확인 단계를 거친 값이어야 한다 — 오타 하나가 남의 수신함이다.
"""

from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING

from updown.common.logging.setup import get_logger
from updown.common.paths import under

if TYPE_CHECKING:
    from collections.abc import Sequence

_logger = get_logger("orchestration.report.mail")

SEND_LOG = under("report_sends.jsonl")
"""발송 이력 — 언제 · 누구에게 · 성공 여부. 컨테이너가 죽어도 남는다."""


@dataclass(frozen=True, slots=True)
class MailSettings:
    """발송 설정 — 값은 `.env` 에서만 온다 (절대 규칙 #1)."""

    host: str
    port: int
    user: str
    password: str
    sender: str


def record_send(
    *,
    to: Sequence[str],
    subject: str,
    ok: bool,
    error: str | None,
    path: Path = SEND_LOG,
    sent_at: datetime | None = None,
) -> None:
    """발송 결과 한 줄을 남긴다.

    Args:
        to: 수신자들.
        subject: 제목.
        ok: 성공 여부.
        error: 실패 사유. 성공이면 None.
        path: 이력 파일.
        sent_at: 시각(UTC). None 이면 지금.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "sent_at": (sent_at or datetime.now(UTC)).isoformat(),
        "to": list(to),
        "subject": subject,
        "ok": ok,
        "error": error,
    }
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def send_mail(
    settings: MailSettings,
    *,
    to: Sequence[str],
    subject: str,
    body: str,
    html: str | None = None,
    log_path: Path = SEND_LOG,
) -> bool:
    """메일을 보낸다 (STARTTLS). `html` 을 주면 **멀티파트**로 보낸다.

    Args:
        settings: SMTP 설정.
        to: 수신자들. 비어 있으면 보내지 않고 거짓.
        subject: 제목.
        body: 평문 본문 — HTML 을 못 읽는 클라이언트의 대체(fallback)로도 쓴다.
        html: HTML 본문. 주면 평문+HTML 멀티파트로 보낸다 (T55). None 이면 평문만.
        log_path: 발송 이력 파일.

    Returns:
        보냈으면 참. 실패는 **던지지 않고** 거짓 — 이력과 로그에 남긴다.

    Note:
        🔴 **평문을 먼저 넣고 HTML 을 대체로 얹는다.** `set_content` 뒤 `add_alternative`
        순서라야 클라이언트가 HTML 을 우선 고르고, 못 읽으면 평문으로 떨어진다.
    """
    recipients = [name.strip() for name in to if name.strip()]
    if not recipients:
        record_send(to=(), subject=subject, ok=False, error="수신자가 없다", path=log_path)
        return False

    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    if html is not None:
        message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(settings.host, settings.port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(settings.user, settings.password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        _logger.error("report_mail_failed", payload={"to": recipients, "error": reason})
        record_send(to=recipients, subject=subject, ok=False, error=reason, path=log_path)
        return False

    _logger.info("report_mail_sent", payload={"to": recipients, "subject": subject})
    record_send(to=recipients, subject=subject, ok=True, error=None, path=log_path)
    return True
