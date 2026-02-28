"""Email reader skill — reads recent emails via IMAP.

Requires the following environment variables:

* ``LIGHTAGENT_EMAIL_HOST`` — IMAP server hostname (e.g. ``imap.gmail.com``)
* ``LIGHTAGENT_EMAIL_USER`` — Email address / username
* ``LIGHTAGENT_EMAIL_PASSWORD`` — App password or IMAP password
* ``LIGHTAGENT_EMAIL_PORT`` — IMAP port (default: 993)
* ``LIGHTAGENT_EMAIL_FOLDER`` — Mailbox folder (default: INBOX)

If any required variable is missing the tool returns a helpful error message
without raising an exception (graceful degradation).

Example::

    skill = EmailReaderSkill()
    tools = skill.get_tools()
    result = tools[0].invoke({"max_emails": 5, "unread_only": True})
"""

from __future__ import annotations

import email
import imaplib
import os
from email.header import decode_header
from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool, tool

if TYPE_CHECKING:
    from email.message import Message

from lightagent.core.logging import get_logger
from lightagent.skills.base import BaseSkill, SkillMetadata

logger = get_logger("lightagent.skills.email_reader")

_DEFAULT_PORT = 993
_DEFAULT_FOLDER = "INBOX"


def _decode_header_value(value: str | bytes | None) -> str:
    """Decode an RFC 2047-encoded email header value.

    Args:
        value: Raw header value (str, bytes, or None).

    Returns:
        Decoded UTF-8 string.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    parts = decode_header(value)
    decoded_parts: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(part)
    return "".join(decoded_parts)


def _get_body(msg: Message) -> str:
    """Extract plain-text body from an email message.

    Args:
        msg: Parsed email.message.Message object.

    Returns:
        Plain text body string (truncated to 500 chars).
    """
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    break
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            body = payload.decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
    return body[:500]


def _fetch_emails(
    host: str,
    user: str,
    password: str,
    port: int,
    folder: str,
    max_emails: int,
    unread_only: bool,
) -> str:
    """Connect to IMAP and fetch recent emails.

    Args:
        host: IMAP server hostname.
        user: Email address / username.
        password: IMAP password.
        port: IMAP port number.
        folder: Mailbox folder name.
        max_emails: Maximum number of emails to fetch.
        unread_only: If True, fetch only unseen messages.

    Returns:
        Formatted string of email summaries.
    """
    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)
            imap.select(folder, readonly=True)

            criterion = "UNSEEN" if unread_only else "ALL"
            _, data = imap.search(None, criterion)
            if not data or not data[0]:
                return "No emails found."

            ids = data[0].split()
            # Fetch most recent first
            ids = ids[-max_emails:][::-1]

            summaries: list[str] = []
            for msg_id in ids:
                _, msg_data = imap.fetch(msg_id, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0]
                if not isinstance(raw, tuple):
                    continue
                msg = email.message_from_bytes(raw[1])
                subject = _decode_header_value(msg.get("Subject"))
                sender = _decode_header_value(msg.get("From"))
                date = msg.get("Date", "")
                body = _get_body(msg)
                summaries.append(
                    f"From: {sender}\nDate: {date}\nSubject: {subject}\n{body}"
                )

            return "\n\n---\n\n".join(summaries) if summaries else "No emails found."

    except imaplib.IMAP4.error as exc:
        return f"[email_reader] IMAP error: {exc}"
    except OSError as exc:
        return f"[email_reader] Connection error: {exc}"


class EmailReaderSkill(BaseSkill):
    """Reads recent emails from an IMAP mailbox."""

    @property
    def metadata(self) -> SkillMetadata:
        """Return email reader skill metadata."""
        return SkillMetadata(
            name="email_reader",
            description="Read recent emails from an IMAP mailbox",
            version="1.0.0",
            author="lightagent",
            safe_to_auto_activate=False,
            requires_permissions=["network.request", "credentials.read"],
            tags=["utility", "email", "imap", "communication"],
        )

    def validate(self) -> bool:
        """Check that required environment variables are present.

        Returns:
            True if IMAP credentials are configured, False otherwise.
        """
        return bool(
            os.getenv("LIGHTAGENT_EMAIL_HOST")
            and os.getenv("LIGHTAGENT_EMAIL_USER")
            and os.getenv("LIGHTAGENT_EMAIL_PASSWORD")
        )

    def get_tools(self) -> list[BaseTool]:
        """Return email reading tools.

        Returns:
            List containing the read_emails tool.
        """

        @tool
        def read_emails(max_emails: int = 10, unread_only: bool = False) -> str:
            """Read recent emails from the configured IMAP mailbox.

            Args:
                max_emails: Maximum number of emails to fetch (default: 10).
                unread_only: If True, only fetch unread/unseen messages.

            Returns:
                Formatted email summaries (From, Date, Subject, Body snippet).
            """
            host = os.getenv("LIGHTAGENT_EMAIL_HOST", "")
            user = os.getenv("LIGHTAGENT_EMAIL_USER", "")
            password = os.getenv("LIGHTAGENT_EMAIL_PASSWORD", "")
            port = int(os.getenv("LIGHTAGENT_EMAIL_PORT", str(_DEFAULT_PORT)))
            folder = os.getenv("LIGHTAGENT_EMAIL_FOLDER", _DEFAULT_FOLDER)

            if not host or not user or not password:
                return (
                    "[email_reader] Missing configuration. "
                    "Set LIGHTAGENT_EMAIL_HOST, LIGHTAGENT_EMAIL_USER, "
                    "and LIGHTAGENT_EMAIL_PASSWORD."
                )

            logger.info("email_reader_fetch", user=user, folder=folder, max=max_emails)
            return _fetch_emails(
                host, user, password, port, folder, max_emails, unread_only
            )

        return [read_emails]
