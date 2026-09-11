"""
Sender reputation memory.

Scam campaigns reuse the same sender (a DLT header, a mobile number, a domain)
across many messages and many family members. This module remembers what the
group has already learned about each sender so the policy engine can treat a
known repeat offender differently from a first-time unknown.

Backed by a Supabase table (`sender_reputation`); degrades to a neutral result
if the table is missing so it never blocks the pipeline.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.db.database import get_supabase
from app.models import ReputationResult, TriageEntities

logger = logging.getLogger("kavach.reputation")


def derive_sender_key(entities: TriageEntities, member_id: str) -> str:
    """
    Pick the most stable identifier for the sender, in priority order:
    DLT sender id > first phone number > first URL host > 'unknown'.
    """
    if entities.sender_id:
        return f"dlt:{entities.sender_id.strip().upper()}"
    if entities.phone_numbers:
        digits = "".join(c for c in entities.phone_numbers[0] if c.isdigit())[-10:]
        if digits:
            return f"phone:{digits}"
    if entities.urls:
        from urllib.parse import urlparse
        u = entities.urls[0]
        try:
            host = urlparse(u if "://" in u else f"http://{u}").netloc
        except Exception:
            host = ""
        if host:
            return f"host:{host.lower().lstrip('www.')}"
    if entities.upi_ids:
        return f"upi:{entities.upi_ids[0].lower()}"
    return "unknown"


async def check_reputation(sender_key: str, group_id: str) -> ReputationResult:
    """Fast read of what the group knows about this sender. Runs in parallel with triage."""
    t0 = time.monotonic()
    if sender_key == "unknown":
        return ReputationResult(sender_key=sender_key, lookup_ms=0)

    try:
        db = get_supabase()
        res = (
            db.table("sender_reputation")
            .select("*")
            .eq("sender_key", sender_key)
            .eq("group_id", group_id)
            .limit(1)
            .execute()
        )
        lookup_ms = int((time.monotonic() - t0) * 1000)
        if not res.data:
            return ReputationResult(sender_key=sender_key, lookup_ms=lookup_ms)

        row = res.data[0]
        scam_count = int(row.get("scam_count", 0) or 0)
        first_seen = None
        if row.get("first_seen"):
            try:
                first_seen = datetime.fromisoformat(str(row["first_seen"]).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        result = ReputationResult(
            sender_key=sender_key,
            seen_count=int(row.get("seen_count", 0) or 0),
            scam_count=scam_count,
            last_verdict=row.get("last_verdict"),
            first_seen=first_seen,
            is_repeat_offender=scam_count >= settings.REPUTATION_REPEAT_OFFENDER_THRESHOLD,
            lookup_ms=lookup_ms,
        )
        if result.is_repeat_offender:
            logger.info(
                f"👤 Repeat offender: {sender_key} — {scam_count} prior scams, "
                f"{result.seen_count} total sightings"
            )
        return result
    except Exception as exc:
        logger.warning(f"reputation lookup failed for {sender_key}: {exc}")
        return ReputationResult(sender_key=sender_key, lookup_ms=int((time.monotonic() - t0) * 1000))


async def record_sighting(sender_key: str, group_id: str, verdict: str) -> None:
    """
    Upsert a sighting after a message is processed.
    verdict: 'scam' | 'safe' | 'unknown'
    """
    if sender_key == "unknown":
        return
    try:
        db = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        existing = (
            db.table("sender_reputation")
            .select("seen_count,scam_count,safe_count,first_seen")
            .eq("sender_key", sender_key)
            .eq("group_id", group_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            row = existing.data[0]
            payload = {
                "seen_count": int(row.get("seen_count", 0) or 0) + 1,
                "scam_count": int(row.get("scam_count", 0) or 0) + (1 if verdict == "scam" else 0),
                "safe_count": int(row.get("safe_count", 0) or 0) + (1 if verdict == "safe" else 0),
                "last_verdict": verdict,
                "last_seen": now,
            }
            db.table("sender_reputation").update(payload).eq(
                "sender_key", sender_key
            ).eq("group_id", group_id).execute()
        else:
            db.table("sender_reputation").insert({
                "sender_key": sender_key,
                "group_id": group_id,
                "seen_count": 1,
                "scam_count": 1 if verdict == "scam" else 0,
                "safe_count": 1 if verdict == "safe" else 0,
                "last_verdict": verdict,
                "first_seen": now,
                "last_seen": now,
            }).execute()
    except Exception as exc:
        logger.warning(f"reputation write failed for {sender_key}: {exc}")


async def mark_confirmed_scam(sender_key: str, group_id: str) -> None:
    """Human pressed BLOCK — force this sender to repeat-offender territory."""
    if sender_key == "unknown":
        return
    try:
        db = get_supabase()
        existing = (
            db.table("sender_reputation")
            .select("scam_count")
            .eq("sender_key", sender_key)
            .eq("group_id", group_id)
            .limit(1)
            .execute()
        )
        bump = settings.REPUTATION_REPEAT_OFFENDER_THRESHOLD
        if existing.data:
            new_count = max(int(existing.data[0].get("scam_count", 0) or 0) + 1, bump)
            db.table("sender_reputation").update(
                {"scam_count": new_count, "last_verdict": "scam"}
            ).eq("sender_key", sender_key).eq("group_id", group_id).execute()
        else:
            now = datetime.now(timezone.utc).isoformat()
            db.table("sender_reputation").insert({
                "sender_key": sender_key, "group_id": group_id,
                "seen_count": 1, "scam_count": bump, "safe_count": 0,
                "last_verdict": "scam", "first_seen": now, "last_seen": now,
            }).execute()
        logger.info(f"👤 {sender_key} marked confirmed scam by human")
    except Exception as exc:
        logger.warning(f"mark_confirmed_scam failed for {sender_key}: {exc}")
