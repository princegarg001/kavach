"""
Daily digest — a once-a-day WhatsApp summary to Mother + Natu of everything Kavach
did in the background, so the routine work is visible without any per-message noise.

Scheduled by APScheduler in main.py at settings.DIGEST_HOUR; also exposed as
POST /digest/send for the demo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db.database import get_supabase
from app.models import DigestMessage
from app.notifier.whatsapp import send_digest

logger = logging.getLogger("kavach.digest")


async def build_digest(hours: int = 24) -> DigestMessage:
    """Summarise the last `hours` of audit events."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    d = DigestMessage(period_start=start, period_end=end)

    try:
        db = get_supabase()
        rows = (
            db.table("audit_events")
            .select("policy_action,immunity_matched,doer_json,education_note,raw_text,triage_json")
            .gte("created_at", start.isoformat())
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        ).data or []
    except Exception as exc:
        logger.warning(f"digest query failed: {exc}")
        return d

    d.total_messages = len(rows)
    for r in rows:
        action = r.get("policy_action")
        if action == "silent_kill":
            d.scams_blocked += 1
        if action in ("escalate_mother", "escalate_natu", "escalate_natu_urgent"):
            d.escalations += 1
        if r.get("immunity_matched"):
            d.immunity_hits += 1
        if r.get("doer_json"):
            d.doer_actions += 1

    for r in rows:
        note = r.get("education_note")
        if note and len(d.highlights) < 3:
            d.highlights.append(note.strip().split("\n")[0][:160])

    return d


async def send_daily_digest() -> bool:
    if not settings.DIGEST_ENABLED:
        return False
    digest = await build_digest(24)
    ok = True
    for to in {settings.MOTHER_PHONE, settings.NATU_PHONE}:
        if to and "0000000000" not in to:
            ok = await send_digest(digest, to) and ok
    logger.info(
        f"📊 Daily digest sent — {digest.total_messages} msgs, {digest.scams_blocked} blocked, "
        f"{digest.escalations} escalations, {digest.doer_actions} agent actions"
    )
    return ok
