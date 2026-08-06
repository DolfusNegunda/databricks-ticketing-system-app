-- ===========================================================================
-- Nexus Support :: Lakebase (Databricks-managed Postgres) operational schema
--
-- __SCHEMA__ is substituted at runtime with the validated value of
-- LAKEBASE_SCHEMA (see support_app/db.py). Every statement is idempotent so
-- the app can run this on every boot.
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS __SCHEMA__;

-- ---------------------------------------------------------------------------
-- tickets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS __SCHEMA__.tickets (
    ticket_id   BIGSERIAL   PRIMARY KEY,
    title       TEXT        NOT NULL,
    description TEXT,
    status      TEXT        NOT NULL DEFAULT 'open',
    priority    TEXT        NOT NULL DEFAULT 'medium',
    category    TEXT        NOT NULL DEFAULT 'general',
    created_by  TEXT        NOT NULL,
    assigned_to TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,

    CONSTRAINT tickets_title_len   CHECK (char_length(btrim(title)) BETWEEN 3 AND 200),
    CONSTRAINT tickets_status_enum CHECK (status   IN ('open', 'in_progress', 'resolved', 'closed')),
    CONSTRAINT tickets_prio_enum   CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    CONSTRAINT tickets_cat_enum    CHECK (category IN ('general', 'billing', 'technical', 'account', 'feature_request')),
    CONSTRAINT tickets_created_by  CHECK (char_length(btrim(created_by)) > 0)
);

-- ---------------------------------------------------------------------------
-- ticket_messages -- child of tickets via FK, cascades on delete
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS __SCHEMA__.ticket_messages (
    message_id   BIGSERIAL   PRIMARY KEY,
    ticket_id    BIGINT      NOT NULL,
    message_text TEXT        NOT NULL,
    author       TEXT        NOT NULL,
    author_role  TEXT        NOT NULL DEFAULT 'customer',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ticket_messages_ticket_fk
        FOREIGN KEY (ticket_id) REFERENCES __SCHEMA__.tickets (ticket_id) ON DELETE CASCADE,
    CONSTRAINT ticket_messages_text_len CHECK (char_length(btrim(message_text)) BETWEEN 1 AND 5000),
    CONSTRAINT ticket_messages_author   CHECK (char_length(btrim(author)) > 0),
    CONSTRAINT ticket_messages_role     CHECK (author_role IN ('customer', 'agent', 'system'))
);

-- ---------------------------------------------------------------------------
-- ticket_status_history -- append-only audit of every status transition
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS __SCHEMA__.ticket_status_history (
    history_id  BIGSERIAL   PRIMARY KEY,
    ticket_id   BIGINT      NOT NULL,
    from_status TEXT,
    to_status   TEXT        NOT NULL,
    changed_by  TEXT        NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ticket_status_history_ticket_fk
        FOREIGN KEY (ticket_id) REFERENCES __SCHEMA__.tickets (ticket_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- Indexes for the access patterns the UI actually uses
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_tickets_status      ON __SCHEMA__.tickets (status);
CREATE INDEX IF NOT EXISTS idx_tickets_priority    ON __SCHEMA__.tickets (priority);
CREATE INDEX IF NOT EXISTS idx_tickets_category    ON __SCHEMA__.tickets (category);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at  ON __SCHEMA__.tickets (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_updated_at  ON __SCHEMA__.tickets (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_ticket     ON __SCHEMA__.ticket_messages (ticket_id, created_at);
CREATE INDEX IF NOT EXISTS idx_status_hist_ticket  ON __SCHEMA__.ticket_status_history (ticket_id, changed_at DESC);

-- ---------------------------------------------------------------------------
-- Keep tickets.updated_at honest without trusting the application layer
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION __SCHEMA__.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_tickets_updated_at ON __SCHEMA__.tickets;
CREATE TRIGGER trg_tickets_updated_at
    BEFORE UPDATE ON __SCHEMA__.tickets
    FOR EACH ROW
    EXECUTE FUNCTION __SCHEMA__.set_updated_at();

-- ---------------------------------------------------------------------------
-- Convenience view: one row per ticket with thread activity rolled up.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW __SCHEMA__.ticket_overview AS
SELECT t.ticket_id,
       t.title,
       t.status,
       t.priority,
       t.category,
       t.created_by,
       t.assigned_to,
       t.created_at,
       t.updated_at,
       t.resolved_at,
       COALESCE(m.message_count, 0)                        AS message_count,
       GREATEST(t.updated_at, COALESCE(m.last_message_at, t.updated_at)) AS last_activity_at
FROM __SCHEMA__.tickets t
LEFT JOIN (
    SELECT ticket_id,
           COUNT(*)        AS message_count,
           MAX(created_at) AS last_message_at
    FROM __SCHEMA__.ticket_messages
    GROUP BY ticket_id
) m ON m.ticket_id = t.ticket_id;

-- ---------------------------------------------------------------------------
-- Lakebase Change Data Feed readiness.
-- REPLICA IDENTITY FULL lets Lakebase CDF publish complete before/after row
-- images into Unity Catalog Delta tables, so the same operational rows become
-- available to analytics and to downstream AI agents without an ETL job.
-- ---------------------------------------------------------------------------
-- Wrapped so a non-owner role cannot fail the whole bootstrap transaction.
DO $cdf$
BEGIN
    ALTER TABLE __SCHEMA__.tickets               REPLICA IDENTITY FULL;
    ALTER TABLE __SCHEMA__.ticket_messages       REPLICA IDENTITY FULL;
    ALTER TABLE __SCHEMA__.ticket_status_history REPLICA IDENTITY FULL;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Skipped REPLICA IDENTITY FULL: current role does not own these tables.';
END;
$cdf$;
