-- ============================================================================
-- Kavach — COMPLETE schema (base + v3). Run this once in the Supabase SQL editor.
-- Fully idempotent: safe to re-run. Use this instead of schema_v3.sql on a fresh
-- project (schema_v3.sql only ALTERs tables that must already exist).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ── audit_events ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_events (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id         TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    member_name        TEXT,
    raw_text           TEXT NOT NULL,
    source             TEXT,
    received_at        TIMESTAMPTZ,
    triage_json        JSONB,
    immunity_json      JSONB,
    investigation_json JSONB,
    doer_json          JSONB,
    policy_action      TEXT,
    policy_json        JSONB,
    immunity_matched   BOOLEAN DEFAULT FALSE,
    human_decision     TEXT,                 -- block | safe | report
    total_time_ms      INTEGER,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    -- v3 additions
    reputation_json    JSONB,
    education_note     TEXT,
    stage_traces_json  JSONB
);
-- v3 columns for a DB that already had the v2 table
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS reputation_json   JSONB;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS education_note     TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS stage_traces_json  JSONB;

CREATE INDEX IF NOT EXISTS idx_audit_member   ON audit_events(member_id);
CREATE INDEX IF NOT EXISTS idx_audit_policy   ON audit_events(policy_action);
CREATE INDEX IF NOT EXISTS idx_audit_immunity ON audit_events(immunity_matched);
CREATE INDEX IF NOT EXISTS idx_audit_created  ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_message  ON audit_events(message_id);

-- ── enrolled_members ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enrolled_members (
    member_id     TEXT PRIMARY KEY,
    member_name   TEXT,
    enrolled      BOOLEAN DEFAULT TRUE,
    enrolled_at   TIMESTAMPTZ DEFAULT NOW(),
    consent_scope TEXT,
    paused_at     TIMESTAMPTZ
);
ALTER TABLE enrolled_members ADD COLUMN IF NOT EXISTS consent_scope TEXT;
ALTER TABLE enrolled_members ADD COLUMN IF NOT EXISTS paused_at     TIMESTAMPTZ;

-- ── immunity_signatures ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS immunity_signatures (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id              TEXT NOT NULL,
    message_hash          TEXT NOT NULL,
    embedding             vector(384),
    canonical_embedding   vector(384),      -- v3: pitch-only embedding
    sender_pattern        TEXT,
    domain_fingerprint    TEXT,
    semantic_shape        TEXT,
    cluster_id            TEXT,
    original_text_snippet TEXT,
    source_member_id      TEXT,
    hit_count             INTEGER DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE immunity_signatures ADD COLUMN IF NOT EXISTS canonical_embedding vector(384);

CREATE INDEX IF NOT EXISTS idx_immunity_group   ON immunity_signatures(group_id);
CREATE INDEX IF NOT EXISTS idx_immunity_hash    ON immunity_signatures(message_hash);
CREATE INDEX IF NOT EXISTS idx_immunity_cluster ON immunity_signatures(cluster_id);
-- (no ivfflat index — the RPCs do exact cosine search, matching the base `embedding`
--  column. Add ivfflat/hnsw later if the ledger grows past a few thousand rows.)

-- raw-vector similarity search (base)
CREATE OR REPLACE FUNCTION match_immunity_signatures(
    query_embedding vector(384),
    group_id_filter TEXT,
    match_threshold FLOAT,
    match_count INT
)
RETURNS TABLE (
    id UUID, similarity FLOAT, semantic_shape TEXT, domain_fingerprint TEXT,
    source_member_id TEXT, hit_count INTEGER, created_at TIMESTAMPTZ
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT s.id, 1 - (s.embedding <=> query_embedding) AS similarity,
           s.semantic_shape, s.domain_fingerprint, s.source_member_id,
           s.hit_count, s.created_at
    FROM immunity_signatures s
    WHERE s.group_id = group_id_filter
      AND 1 - (s.embedding <=> query_embedding) >= match_threshold
    ORDER BY similarity DESC
    LIMIT match_count;
END; $$;

-- canonical-vector similarity search (v3)
CREATE OR REPLACE FUNCTION match_immunity_canonical(
    query_embedding vector(384),
    group_id_filter TEXT,
    match_threshold FLOAT,
    match_count INT
)
RETURNS TABLE (
    id UUID, similarity FLOAT, semantic_shape TEXT, domain_fingerprint TEXT,
    source_member_id TEXT, hit_count INTEGER, created_at TIMESTAMPTZ
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT s.id, 1 - (s.canonical_embedding <=> query_embedding) AS similarity,
           s.semantic_shape, s.domain_fingerprint, s.source_member_id,
           s.hit_count, s.created_at
    FROM immunity_signatures s
    WHERE s.group_id = group_id_filter
      AND s.canonical_embedding IS NOT NULL
      AND 1 - (s.canonical_embedding <=> query_embedding) >= match_threshold
    ORDER BY similarity DESC
    LIMIT match_count;
END; $$;

-- ── sender_reputation (v3) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sender_reputation (
    sender_key   TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    seen_count   INTEGER DEFAULT 0,
    scam_count   INTEGER DEFAULT 0,
    safe_count   INTEGER DEFAULT 0,
    last_verdict TEXT,
    first_seen   TIMESTAMPTZ DEFAULT NOW(),
    last_seen    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sender_key, group_id)
);
CREATE INDEX IF NOT EXISTS idx_reputation_group_scam
    ON sender_reputation(group_id, scam_count DESC);

-- ── pending_escalations (v3) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pending_escalations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    to_number  TEXT NOT NULL,
    message_id TEXT NOT NULL,
    event_id   UUID,
    resolved   BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pending_lookup
    ON pending_escalations(to_number, resolved, created_at DESC);
