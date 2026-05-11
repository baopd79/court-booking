"""Async SMTP email sender."""

import logging
from email.message import EmailMessage

import aiosmtplib

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        use_tls: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls

    async def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        await aiosmtplib.send(
            msg,
            hostname=self._host,
            port=self._port,
            username=self._username or None,
            password=self._password or None,
            use_tls=self._use_tls,
            start_tls=False,
        )
        logger.info("Email sent to %s: %s", to, subject)


class NoOpEmailSender:
    """Used in tests — logs but never actually sends."""

    async def send(self, to: str, subject: str, body: str) -> None:
        logger.info("[NoOp] Email to %s: %s", to, subject)


def get_email_sender() -> EmailSender:
    from app.core.config import get_settings
    s = get_settings()
    return EmailSender(
        host=s.smtp_host,
        port=s.smtp_port,
        username=s.smtp_user,
        password=s.smtp_password,
        sender=s.smtp_from,
    )
