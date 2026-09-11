-- ============================================================================
-- Kavach schema v3 — run in the Supabase SQL editor.
-- Additive only. Safe to run ONLY on top of the v2 schema in db/events.py.
--
-- ⚠  If you get: ERROR 42P01 relation "audit_events" does not exist
--    your project has no base schema yet — run  schema_full.sql  instead
--    (it creates base + v3 in one idempotent script).
-- ============================================================================

-- ── audit_events: new columns ───────────────────────────────────────────────
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS reputation_json   JSONB;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS education_note     TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS stage_traces_json  JSONB;

-- ── immunity_signatures: canonical (pitch-only) embedding ───────────────────
ALTER TABLE immunity_signatures ADD COLUMN IF NOT EXISTS canonical_embedding vector(1536);

-- Dual-vector search: match against the identifier-stripped pitch embedding.
CREATE OR REPLACE FUNCTION match_immunity_canonical(
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
        1 - (s.canonical_embedding <=> query_embedding) AS similarity,
        s.semantic_shape,
        s.domain_fingerprint,
        s.source_member_id,
        s.hit_count,
        s.created_at
    FROM immunity_signatures s
    WHERE s.group_id = group_id_filter
      AND s.canonical_embedding IS NOT NULL
      AND 1 - (s.canonical_embedding <=> query_embedding) >= match_threshold
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;

-- ── sender_reputation ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sender_reputation (
    sender_key    TEXT NOT NULL,          -- dlt:VM-HDFCBK | phone:9876543210 | host:foo.xyz | upi:x@y
    group_id      TEXT NOT NULL,
    seen_count    INTEGER DEFAULT 0,
    scam_count    INTEGER DEFAULT 0,
    safe_count    INTEGER DEFAULT 0,
    last_verdict  TEXT,                   -- scam | safe | unknown
    first_seen    TIMESTAMPTZ DEFAULT NOW(),
    last_seen     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sender_key, group_id)
);

CREATE INDEX IF NOT EXISTS idx_reputation_group_scam
    ON sender_reputation(group_id, scam_count DESC);

-- ── pending_escalations ────────────────────────────────────────────────────
-- Correlates a WhatsApp reply (BLOCK/SAFE/REPORT) back to the exact escalation.
CREATE TABLE IF NOT EXISTS pending_escalations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    to_number   TEXT NOT NULL,            -- normalized, no 'whatsapp:' prefix
    message_id  TEXT NOT NULL,            -- correlation id of the inbound message
    event_id    UUID,
    resolved    BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_lookup
    ON pending_escalations(to_number, resolved, created_at DESC);
