"""Gmail SMTP 발송."""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import GMAIL_APP_PASSWORD, GMAIL_SENDER, REPORT_RECIPIENT, get_logger

log = get_logger(__name__)


def send_email(subject: str, html_body: str, recipient: str | None = None) -> None:
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_SENDER 또는 GMAIL_APP_PASSWORD가 .env에 설정되지 않았습니다."
        )

    to = recipient or REPORT_RECIPIENT
    if not to:
        raise RuntimeError("REPORT_RECIPIENT 미설정")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    log.info(f"메일 발송 시작 → {to}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_SENDER, [to], msg.as_string())
    log.info("메일 발송 완료")
