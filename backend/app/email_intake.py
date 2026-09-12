"""
Email intake — IMAP polling. No OAuth, no phone: give Kavach a mailbox + an app
password and it checks for unread mail on an interval and runs each new message
through the same pipeline as SMS/WhatsApp (source="email").

Enable with EMAIL_INTAKE_ENABLED=true + IMAP_USER + IMAP_PASSWORD in .env.
Gmail: create an App Password at https://myaccount.google.com/apppasswords
(needs 2-Step Verification on). Wired into the APScheduler in main.py.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from typing import Awaitable, Callable

from app.config import settings
from app.models import InboundMessage

logger = logging.getLogger("kavach.email")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_running = False


def _decode(v) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _plain_body(msg: email.message.Message) -> str:
    """Prefer text/plain; fall back to de-tagged text/html."""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                continue
            if ctype == "text/plain" and not plain:
                plain = text
            elif ctype == "text/html" and not html:
                html = text
    else:
        try:
            text = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            text = msg.get_payload() or ""
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text

    body = plain or _TAG_RE.sub(" ", html)
    body = "\n".join(_WS_RE.sub(" ", ln).strip() for ln in body.splitlines())
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:4000]


def _fetch_unseen() -> list[tuple[str, InboundMessage]]:
    """Blocking IMAP fetch. Returns [(uid, InboundMessage)] for UNSEEN mail."""
    out: list[tuple[str, InboundMessage]] = []
    member_id = settings.EMAIL_MEMBER_ID or settings.IMAP_USER or "email-inbox"

    conn = imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT)
    try:
        conn.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
        conn.select(settings.IMAP_FOLDER)
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return out
        uids = data[0].split()[:20]  # cap per poll
        for uid in uids:
            typ, msg_data = conn.fetch(uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get("Subject"))
            sender = _decode(msg.get("From"))
            mid = (msg.get("Message-ID") or f"email-{uid.decode()}").strip("<> ")
            body = _plain_body(msg)
            raw_text = f"Subject: {subject}\nFrom: {sender}\n\n{body}".strip()
            out.append((uid.decode(), InboundMessage(
                member_id=member_id,
                member_name=sender[:80] or "Email",
                raw_text=raw_text,
                source="email",
                message_id=mid,
                received_at=datetime.now(timezone.utc),
            )))
        # mark processed as \Seen so we don't re-ingest
        for uid, _ in out:
            try:
                conn.store(uid.encode(), "+FLAGS", "\\Seen")
            except Exception:
                pass
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


async def poll_once(handler: Callable[[InboundMessage], Awaitable]) -> int:
    """Fetch unseen mail and hand each to `handler` (main.process_message). Returns count."""
    global _running
    if _running:
        return 0
    if not (settings.IMAP_USER and settings.IMAP_PASSWORD):
        logger.warning("email intake enabled but IMAP_USER / IMAP_PASSWORD not set")
        return 0
    _running = True
    try:
        items = await asyncio.to_thread(_fetch_unseen)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"email poll failed: {exc}")
        return 0
    finally:
        _running = False

    for _, msg in items:
        logger.info(f"📧 new email from {msg.member_name[:40]}")
        from app.tasks import spawn
        spawn(handler(msg), name="email_ingest")
    return len(items)
