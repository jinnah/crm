import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


class MailerNotConfiguredError(RuntimeError):
    pass


class Mailer(Protocol):
    def send_password_reset(self, to_email: str, reset_url: str) -> None: ...


class SmtpMailer:
    """Transactional SMTP delivery for password-recovery email only.

    TLS certificates are verified normally. Reset links and tokens are never
    logged; callers must catch delivery errors and keep responses generic.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send_password_reset(self, to_email: str, reset_url: str) -> None:
        s = self._settings
        if not s.smtp_host or not s.smtp_sender:
            raise MailerNotConfiguredError("SMTP is not configured")

        message = EmailMessage()
        message["Subject"] = f"{s.app_name} password reset"
        message["From"] = s.smtp_sender
        message["To"] = to_email
        message.set_content(
            "A password reset was requested for your account.\n\n"
            f"Reset your password here (link expires in 30 minutes):\n{reset_url}\n\n"
            "If you did not request this, you can ignore this email."
        )

        context = ssl.create_default_context()
        if s.smtp_tls == "ssl":
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context) as client:
                self._login_and_send(client, message)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port) as client:
                if s.smtp_tls == "starttls":
                    client.starttls(context=context)
                self._login_and_send(client, message)

    def _login_and_send(self, client: smtplib.SMTP, message: EmailMessage) -> None:
        s = self._settings
        if s.smtp_username:
            client.login(s.smtp_username, s.smtp_password)
        client.send_message(message)
