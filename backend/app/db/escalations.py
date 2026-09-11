"""
Pending-escalation correlation.

When Kavach asks Mother or Natu a question on WhatsApp, it records which message
it was about, keyed by the recipient's number. When that person replies
BLOCK / SAFE / REPORT, we resolve it back to the exact event instead of guessing
"the most recent event in the whole system" (which was racy and cross-member).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.database import get_supabase

logger = logging.getLogger("kavach.escalations")


def _norm(number: str) -> str:
    return number.strip().replace("whatsapp:", "").replace(" ", "")


async def record_pending(to_number: str, message_id: str, event_id: Optional[str] = None) -> None:
    try:
        get_supabase().table("pending_escalations").insert({
            "to_number": _norm(to_number),
            "message_id": message_id,
            "event_id": event_id,
            "resolved": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as exc:
        logger.warning(f"could not record pending escalation: {exc}")


async def resolve_latest(from_number: str) -> Optional[dict]:
    """Return + mark-resolved the newest unresolved escalation sent to this number."""
    try:
        db = get_supabase()
        res = (
            db.table("pending_escalations")
            .select("*")
            .eq("to_number", _norm(from_number))
            .eq("resolved", False)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        row = res.data[0]
        db.table("pending_escalations").update({"resolved": True}).eq("id", row["id"]).execute()
        return row
    except Exception as exc:
        logger.warning(f"resolve_latest failed: {exc}")
        return None
