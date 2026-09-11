"""
Immunity Ledger — the crown jewel of Kavach.

When one group member is hit by a scam, every other member becomes immune.
Uses Supabase pgvector for vector similarity search.

v2 enhancements:
- Signature deduplication (check message_hash before writing)
- Confidence boosting (hit_count increments on re-matches)
- Signature decay (old signatures get lower priority)
- Multi-vector search (raw embedding + semantic similarity)

The point: scammers rotate URLs and phone numbers but reuse the PITCH.
Vector similarity catches variants that blocklists miss.

Instrumented: logs `check_time_ms` per member so you can show
Member 1 → 8,231ms (full investigation)
Member 2 → 41ms (immunity hit, short-circuited)
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

from app import llm
from app.config import settings
from app.db.database import get_supabase
from app.models import (
    ImmunityCheckResult,
    ScamSignature,
    TriageEntities,
    InvestigatorResult,
)
from app.memory.signature import extract_signature, _canonicalize

logger = logging.getLogger("kavach.immunity")


async def _embed(text: str) -> list[float]:
    """Provider-agnostic text embedding (see app/llm.py)."""
    return await llm.embed(text)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    va = np.array(a)
    vb = np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def _decay(similarity: float, created_at_str: str) -> float:
    """Apply confidence decay to an old signature's similarity score."""
    if not created_at_str:
        return similarity
    try:
        created_at = datetime.fromisoformat(str(created_at_str).replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - created_at).days
        if age_days > settings.IMMUNITY_DECAY_DAYS:
            decay_factor = max(0.85, 1.0 - (age_days - settings.IMMUNITY_DECAY_DAYS) * 0.001)
            return similarity * decay_factor
    except (ValueError, TypeError):
        pass
    return similarity


async def check_immunity(
    raw_text: str,
    group_id: str,
) -> ImmunityCheckResult:
    """
    Check this message against the immunity ledger with a DUAL-VECTOR search:

      1. raw embedding  vs  stored raw embedding   (threshold IMMUNITY_SIMILARITY_THRESHOLD)
      2. canonical embedding (identifiers stripped) vs stored canonical embedding
         (higher bar IMMUNITY_CANONICAL_THRESHOLD)

    The canonical pass is what catches a reworded variant that rotates every URL,
    amount and phone number but keeps the same pitch. Runs in parallel with triage.
    """
    t0 = time.monotonic()

    try:
        db = get_supabase()

        # ── Pass 1: raw embedding ────────────────────────────────────────────
        embedding = await _embed(raw_text)
        result = db.rpc(
            "match_immunity_signatures",
            {
                "query_embedding": embedding,
                "group_id_filter": group_id,
                "match_threshold": settings.IMMUNITY_SIMILARITY_THRESHOLD,
                "match_count": 3,
            },
        ).execute()

        # ── Pass 2: canonical (pitch-only) embedding ─────────────────────────
        canonical_hit = None
        canonical = _canonicalize(raw_text)
        if canonical and len(canonical) > 20:
            try:
                canon_embedding = await _embed(canonical)
                canon_result = db.rpc(
                    "match_immunity_canonical",
                    {
                        "query_embedding": canon_embedding,
                        "group_id_filter": group_id,
                        "match_threshold": settings.IMMUNITY_CANONICAL_THRESHOLD,
                        "match_count": 3,
                    },
                ).execute()
                if canon_result.data:
                    m = canon_result.data[0]
                    adj = _decay(float(m.get("similarity", 0.0)), m.get("created_at", ""))
                    if adj >= settings.IMMUNITY_CANONICAL_THRESHOLD:
                        canonical_hit = (m, adj)
            except Exception as exc:
                logger.debug(f"canonical immunity pass skipped: {exc}")

        check_time_ms = int((time.monotonic() - t0) * 1000)

        raw_best = None
        if result.data:
            m = result.data[0]
            adj = _decay(float(m.get("similarity", 0.0)), m.get("created_at", ""))
            if adj >= settings.IMMUNITY_SIMILARITY_THRESHOLD:
                raw_best = (m, adj)

        # Pick the stronger of the two passes
        winner, match_vector = None, None
        if raw_best and canonical_hit:
            winner, match_vector = (raw_best, "raw") if raw_best[1] >= canonical_hit[1] else (canonical_hit, "canonical")
        elif raw_best:
            winner, match_vector = raw_best, "raw"
        elif canonical_hit:
            winner, match_vector = canonical_hit, "canonical"

        if winner:
            best_match, adjusted_similarity = winner
            matched_id = str(best_match.get("id", ""))
            semantic_shape = best_match.get("semantic_shape", "")
            hit_count = int(best_match.get("hit_count", 0))
            try:
                db.table("immunity_signatures").update(
                    {"hit_count": hit_count + 1}
                ).eq("id", matched_id).execute()
            except Exception:
                pass
            logger.info(
                f"🛡️  IMMUNITY HIT ({match_vector}): similarity={adjusted_similarity:.3f} "
                f"shape='{semantic_shape[:50]}' hits={hit_count + 1} in {check_time_ms}ms"
            )
            return ImmunityCheckResult(
                matched=True,
                similarity_score=adjusted_similarity,
                matched_signature_id=matched_id,
                matched_semantic_shape=semantic_shape,
                hit_count=hit_count + 1,
                check_time_ms=check_time_ms,
                match_vector=match_vector,
            )

        logger.debug(f"🔓 Immunity: no match in {check_time_ms}ms")
        return ImmunityCheckResult(matched=False, similarity_score=0.0, check_time_ms=check_time_ms)

    except Exception as exc:
        check_time_ms = int((time.monotonic() - t0) * 1000)
        logger.error(f"Immunity check error: {exc}")
        return ImmunityCheckResult(matched=False, similarity_score=0.0, check_time_ms=check_time_ms)


async def write_immunity(
    raw_text: str,
    group_id: str,
    member_id: str,
    entities: TriageEntities,
    investigation: Optional[InvestigatorResult],
) -> bool:
    """
    Write a new scam signature to the immunity ledger.
    Called after a scam is confirmed — protects ALL other group members.

    v2: Deduplication — checks message_hash before writing to avoid duplicates.
    """
    try:
        signature = await extract_signature(
            raw_text=raw_text,
            entities=entities,
            investigation=investigation,
            member_id=member_id,
            group_id=group_id,
        )

        db = get_supabase()

        # ── Deduplication check ────────────────────────────────────────────────
        existing = db.table("immunity_signatures").select("id").eq(
            "message_hash", signature.message_hash
        ).eq("group_id", group_id).execute()

        if existing.data and len(existing.data) > 0:
            logger.info(
                f"⏭️  Immunity: duplicate signature skipped "
                f"(hash={signature.message_hash[:12]}...)"
            )
            return True  # Not an error, just a skip

        # ── Write new signature ────────────────────────────────────────────────
        row = {
            "group_id": signature.group_id,
            "message_hash": signature.message_hash,
            "embedding": signature.embedding,
            "sender_pattern": signature.sender_pattern,
            "domain_fingerprint": signature.domain_fingerprint,
            "semantic_shape": signature.semantic_shape,
            "original_text_snippet": signature.original_text_snippet,
            "source_member_id": signature.source_member_id,
            "hit_count": 0,
        }

        # Add cluster_id if available
        if signature.cluster_id:
            row["cluster_id"] = signature.cluster_id

        # Store the canonical (pitch-only) embedding for the dual-vector search.
        if signature.canonical_embedding:
            row["canonical_embedding"] = signature.canonical_embedding

        try:
            db.table("immunity_signatures").insert(row).execute()
        except Exception as insert_exc:
            # Column may not exist yet on older schemas — retry without it.
            if "canonical_embedding" in row:
                row.pop("canonical_embedding", None)
                db.table("immunity_signatures").insert(row).execute()
            else:
                raise insert_exc

        logger.info(
            f"✍️  Immunity written: group={group_id} "
            f"shape='{signature.semantic_shape}' "
            f"cluster={signature.cluster_id}"
        )
        return True

    except Exception as exc:
        logger.error(f"Immunity write error: {exc}")
        return False
