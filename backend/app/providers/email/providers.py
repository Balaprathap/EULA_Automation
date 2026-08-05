"""Concrete email providers: console (default), SMTP (stdlib), Resend (httpx).

None of these adds a dependency. Console is the default so the feature degrades
safely - and visibly - when no provider is configured.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from app.core.logging import get_logger
from app.providers.email.base import Attachment, EmailMessage, EmailProvider, SendResult, mask_email

logger = get_logger(__name__)


class ConsoleEmailProvider(EmailProvider):
    """Logs a redacted line and sends nothing. Development and tests only."""

    @property
    def name(self) -> str:
        return "console"

    async def send(self, message: EmailMessage) -> SendResult:
        logger.info(
            "email suppressed (console provider)",
            extra={
                "recipient_masked": mask_email(message.to),
                "subject": message.subject,
                "attachments": len(message.attachments or []),
            },
        )
        return SendResult(ok=True, provider=self.name, message_id="console-suppressed")


class SmtpEmailProvider(EmailProvider):
    """Standard-library SMTP. Works with Mailgun, SendGrid, Resend, Gmail, etc."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        use_tls: bool = True,
        reply_to: str | None = None,
    ) -> None:
        if not host:
            raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp.")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls
        self._reply_to = reply_to

    @property
    def name(self) -> str:
        return "smtp"

    def _build(self, message: EmailMessage):
        from email.message import EmailMessage as MimeMessage

        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        if self._reply_to:
            mime["Reply-To"] = self._reply_to
        mime.set_content(message.text_body)
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")
        for attachment in message.attachments or []:
            maintype, _, subtype = attachment.content_type.partition("/")
            mime.add_attachment(
                attachment.content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=attachment.filename,
            )
        return mime

    def _send_blocking(self, mime) -> str:
        import contextlib
        import smtplib
        import ssl

        context = ssl.create_default_context()
        server: smtplib.SMTP | smtplib.SMTP_SSL
        if self._port == 465:
            server = smtplib.SMTP_SSL(self._host, self._port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(self._host, self._port, timeout=30)
        try:
            if self._port != 465 and self._use_tls:
                server.starttls(context=context)
            if self._username and self._password:
                server.login(self._username, self._password)
            server.send_message(mime)
        finally:
            # Closing is best effort; a failure here must not mask a successful send.
            with contextlib.suppress(Exception):
                server.quit()
        return str(mime.get("Message-ID") or "")

    async def send(self, message: EmailMessage) -> SendResult:
        try:
            mime = self._build(message)
            # smtplib is blocking; keep the worker event loop responsive.
            message_id = await asyncio.to_thread(self._send_blocking, mime)
            return SendResult(ok=True, provider=self.name, message_id=message_id or None)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "smtp send failed",
                extra={
                    "error_type": type(exc).__name__,
                    "recipient_masked": mask_email(message.to),
                },
            )
            # Never surface provider internals to the caller or the database.
            return SendResult(
                ok=False,
                provider=self.name,
                error_code=type(exc).__name__,
                error_message_safe="The email provider rejected or could not deliver the message.",
            )


class ResendEmailProvider(EmailProvider):
    """Resend HTTP API, using the httpx client already in the dependency set."""

    ENDPOINT = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str, reply_to: str | None = None) -> None:
        if not api_key:
            raise ValueError("RESEND_API_KEY is required when EMAIL_PROVIDER=resend.")
        self._api_key = api_key
        self._sender = sender
        self._reply_to = reply_to

    @property
    def name(self) -> str:
        return "resend"

    async def send(self, message: EmailMessage) -> SendResult:
        import httpx

        payload: dict[str, object] = {
            "from": self._sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text_body,
        }
        if message.html_body:
            payload["html"] = message.html_body
        if self._reply_to:
            payload["reply_to"] = self._reply_to
        if message.attachments:
            payload["attachments"] = [
                {
                    "filename": attachment.filename,
                    "content": base64.b64encode(attachment.content).decode("ascii"),
                }
                for attachment in message.attachments
            ]

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
            if response.status_code >= 400:
                logger.error(
                    "resend send failed",
                    extra={
                        "status": response.status_code,
                        "recipient_masked": mask_email(message.to),
                    },
                )
                return SendResult(
                    ok=False,
                    provider=self.name,
                    error_code=f"HTTP_{response.status_code}",
                    error_message_safe="The email provider rejected the message.",
                )
            return SendResult(
                ok=True, provider=self.name, message_id=str(response.json().get("id") or "") or None
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("resend request failed", extra={"error_type": type(exc).__name__})
            return SendResult(
                ok=False,
                provider=self.name,
                error_code=type(exc).__name__,
                error_message_safe="The email provider could not be reached.",
            )


class SesEmailProvider(EmailProvider):
    """Amazon SES via the v2 API.

    Uses SendEmail with a raw MIME payload so attachments work identically to
    the SMTP provider. boto3 is synchronous, so the call runs in a worker thread.

    SES never chooses the recipient - the address is resolved server-side by
    ReportDeliveryService from the authenticated profile, exactly as with every
    other provider.
    """

    def __init__(
        self,
        region: str,
        sender: str,
        *,
        configuration_set: str | None = None,
        reply_to: str | None = None,
        client: Any = None,
    ) -> None:
        if not sender:
            raise ValueError("AWS_SES_FROM_EMAIL is required when AWS_SES_ENABLED is true.")
        self._region = region
        self._sender = sender
        self._configuration_set = configuration_set
        self._reply_to = reply_to
        self._client = client

    @property
    def name(self) -> str:
        return "ses"

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "sesv2",
                region_name=self._region,
                config=Config(
                    retries={"max_attempts": 2, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=30,
                ),
            )
        return self._client

    def build_mime(self, message: EmailMessage) -> bytes:
        """Assemble the raw MIME payload. Kept public so tests can inspect it."""
        from email.message import EmailMessage as MimeMessage

        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        if self._reply_to:
            mime["Reply-To"] = self._reply_to
        mime.set_content(message.text_body)
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")
        for attachment in message.attachments or []:
            maintype, _, subtype = attachment.content_type.partition("/")
            mime.add_attachment(
                attachment.content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=attachment.filename,
            )
        return mime.as_bytes()

    async def send(self, message: EmailMessage) -> SendResult:
        try:
            raw = self.build_mime(message)

            def _send() -> str:
                request: dict[str, Any] = {
                    "FromEmailAddress": self._sender,
                    "Destination": {"ToAddresses": [message.to]},
                    "Content": {"Raw": {"Data": raw}},
                }
                if self._configuration_set:
                    request["ConfigurationSetName"] = self._configuration_set
                response = self._get_client().send_email(**request)
                return str(response.get("MessageId") or "")

            message_id = await asyncio.to_thread(_send)
            return SendResult(ok=True, provider=self.name, message_id=message_id or None)

        except Exception as exc:  # noqa: BLE001
            code = type(exc).__name__
            # botocore surfaces the SES error code in the response envelope.
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                code = str(response.get("Error", {}).get("Code") or code)
            logger.error(
                "ses send failed",
                extra={
                    "error_type": type(exc).__name__,
                    "error_code": code,
                    "recipient_masked": mask_email(message.to),
                },
            )
            # Never surface provider internals to the caller or the database.
            return SendResult(
                ok=False,
                provider=self.name,
                error_code=code,
                error_message_safe="The email provider rejected or could not deliver the message.",
            )


def build_email_provider(settings) -> EmailProvider:
    """Select the provider from configuration. Never from request input.

    AWS_SES_ENABLED takes precedence when set, so SES can be switched on without
    touching EMAIL_PROVIDER. Everything else is unchanged, and `console` remains
    the safe default.
    """
    if getattr(settings, "aws_ses_enabled", False):
        return SesEmailProvider(
            region=settings.aws_region,
            sender=settings.aws_ses_from_email or settings.email_from,
            configuration_set=settings.aws_ses_configuration_set,
            reply_to=settings.email_reply_to,
        )
    if settings.email_provider == "smtp":
        return SmtpEmailProvider(
            host=settings.smtp_host or "",
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.email_from,
            use_tls=settings.smtp_use_tls,
            reply_to=settings.email_reply_to,
        )
    if settings.email_provider == "resend":
        return ResendEmailProvider(
            api_key=settings.resend_api_key or "",
            sender=settings.email_from,
            reply_to=settings.email_reply_to,
        )
    return ConsoleEmailProvider()


__all__ = [
    "Attachment",
    "ConsoleEmailProvider",
    "EmailMessage",
    "ResendEmailProvider",
    "SesEmailProvider",
    "SmtpEmailProvider",
    "build_email_provider",
]
