"""Email provider abstraction.

Deliberately small and configured entirely through environment variables. No
credential is ever hard-coded, and no provider key is ever logged.
"""

from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass


@dataclass
class Attachment:
    filename: str
    content: bytes
    content_type: str = "application/pdf"


@dataclass
class EmailMessage:
    to: str
    subject: str
    text_body: str
    html_body: str | None = None
    attachments: list[Attachment] | None = None


@dataclass
class SendResult:
    ok: bool
    provider: str
    message_id: str | None = None
    error_code: str | None = None
    error_message_safe: str | None = None


class EmailProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    async def send(self, message: EmailMessage) -> SendResult: ...


def hash_email(address: str) -> str:
    """Stable hash used for duplicate-send protection and audit rows."""
    return hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()


def mask_email(address: str) -> str:
    """Mask an address for display and logs: a***@example.com."""
    address = (address or "").strip()
    if "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"
