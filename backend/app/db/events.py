"""
Audit event persistence — write and read from Supabase.

Schema (run in Supabase SQL editor):

  CREATE EXTENSION IF NOT EXISTS vector;

  CREATE TABLE IF NOT EXISTS audit_events (
      id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      message_id     TEXT NOT NULL,
      member_id      TEXT NOT NULL,
      member_name    TEXT,
      raw_text       TEXT NOT NULL,
      source         TEXT,
      received_at    TIMESTAMPTZ,
      triage_json    JSONB,
      immunity_json  JSONB,
      investigation_json JSONB,
      doer_json      JSONB,
      policy_action  TEXT,
      policy_json    JSONB,
      immunity_matched BOOLEAN DEFAULT FALSE,
      human_decision TEXT,              -- block, safe, report
      total_time_ms  INTEGER,
      created_at     TIMESTAMPTZ DEFAULT NOW()
  );

  CREATE INDEX idx_audit_member ON audit_events(member_id);
  CREATE INDEX idx_audit_policy ON audit_events(policy_action);
  CREATE INDEX idx_audit_immunity ON audit_events(immunity_matched);
  CREATE INDEX idx_audit_created ON audit_events(created_at DESC);

  CREATE TABLE IF NOT EXISTS enrolled_members (
      member_id   TEXT PRIMARY KEY,
      member_name TEXT,
      enrolled    BOOLEAN DEFAULT TRUE,
      enrolled_at TIMESTAMPTZ DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS immunity_signatures (
      id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      group_id            TEXT NOT NULL,
      message_hash        TEXT NOT NULL,
      embedding           vector(1536),
      sender_pattern      TEXT,
      domain_fingerprint  TEXT,
      semantic_shape      TEXT,
      cluster_id          TEXT,
      original_text_snippet TEXT,
      source_member_id    TEXT,
      hit_count           INTEGER DEFAULT 0,
      created_at          TIMESTAMPTZ DEFAULT NOW()
  );

  CREATE INDEX idx_immunity_group ON immunity_signatures(group_id);
  CREATE INDEX idx_immunity_hash ON immunity_signatures(message_hash);
  CREATE INDEX idx_immunity_cluster ON immunity_signatures(cluster_id);

  -- pgvector similarity search function
  CREATE OR REPLACE FUNCTION match_immunity_signatures(
      query_embedding vector(1536),
      group_id_filter TEXT,
      match_threshold FLOAT,
      match_count INT
  )
  RETURNS TABLE (
      id UUID,
      similarity FLOAT,
      semantic_shape TEXT,
      domain_fingerprint TEXT,
      source_member_id TEXT,
      hit_count INTEGER,
      created_at TIMESTAMPTZ
  )
  LANGUAGE plpgsql
  AS $$
  BEGIN
      RETURN QUERY
      SELECT
          s.id,
          1 - (s.embedding <=> query_embedding) AS similarity,
          s.semantic_shape,
          s.domain_fingerprint,
          s.source_member_id,
          s.hit_count,
          s.created_at
      FROM immunity_signatures s
      WHERE s.group_id = group_id_filter
        AND 1 - (s.embedding <=> query_embedding) >= match_threshold
      ORDER BY similarity DESC
      LIMIT match_count;
  END;
  $$;
"""
import logging
from typing import Optional

from app.db.database import get_supabase
from app.models import AuditEvent

logger = logging.getLogger("kavach.db")


async def save_audit_event(event: AuditEvent) -> Optional[dict]:
    """Persist audit event to Supabase. Returns saved row."""
    db = get_supabase()
    try:
        row = {
            "message_id": event.message_id,
            "member_id": event.member_id,
            "member_name": event.member_name,
            "raw_text": event.raw_text,
            "source": event.source,
            "received_at": event.received_at.isoformat(),
            "triage_json": event.triage.model_dump() if event.triage else None,
            "immunity_json": event.immunity.model_dump() if event.immunity else None,
            "reputation_json": event.reputation.model_dump(mode="json") if event.reputation else None,
            "investigation_json": event.investigation.model_dump() if event.investigation else None,
            "doer_json": event.doer.model_dump() if event.doer else None,
            "policy_action": event.policy.action.value if event.policy else None,
            "policy_json": event.policy.model_dump() if event.policy else None,
            "education_note": event.education_note,
            "stage_traces_json": [t.model_dump() for t in event.stage_traces] if event.stage_traces else None,
            "immunity_matched": event.immunity.matched if event.immunity else False,
            "human_decision": event.human_decision,
            "total_time_ms": event.total_time_ms,
        }
        try:
            result = db.table("audit_events").insert(row).execute()
        except Exception as insert_exc:
            # New columns may not exist on an un-migrated DB — drop them and retry.
            for k in ("reputation_json", "education_note", "stage_traces_json"):
                row.pop(k, None)
            logger.warning(f"audit insert retried without new columns: {insert_exc}")
            result = db.table("audit_events").insert(row).execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.error(f"Failed to save audit event: {exc}")
        return None


async def get_audit_events(
    limit: int = 50, offset: int = 0, member_id: Optional[str] = None
) -> list[dict]:
    """Fetch audit events for dashboard."""
    db = get_supabase()
    try:
        q = (
            db.table("audit_events")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .offset(offset)
        )
        if member_id:
            q = q.eq("member_id", member_id)
        result = q.execute()
        return result.data or []
    except Exception as exc:
        logger.error(f"Failed to fetch audit events: {exc}")
        return []
