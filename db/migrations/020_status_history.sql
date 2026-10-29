-- PLAN.md step 12 (section 7): the history of every business's relationship status.
-- businesses.status stays the current value (and keeps its CHECK); this table keeps how it got there, who set it and why.
-- Rows are never edited or deleted. The current status is the newest row (highest seq).

CREATE TABLE status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seq BIGINT GENERATED ALWAYS AS IDENTITY,   -- orders rows made in the same instant
    business_id UUID NOT NULL REFERENCES businesses(id),
    status TEXT NOT NULL
        CHECK (status IN ('prospect', 'client', 'delivered', 'rejected', 'declined')),
    reason TEXT,
    set_by TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_status_history_business ON status_history (business_id, seq);

-- Nothing is ever deleted, and a saved row is a record of what was decided: a correction is a new row.
CREATE OR REPLACE FUNCTION forbid_status_history_edits()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'status_history rows are never edited or deleted (row %): set the status again instead', OLD.id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_status_history_no_edit
    BEFORE UPDATE OR DELETE ON status_history
    FOR EACH ROW
    EXECUTE FUNCTION forbid_status_history_edits();

-- One row for every business the moment it is added, and one for every later change, whoever makes it
-- (aeo status, the decline flow, or raw SQL), the same way migration 013 logs to client_journal.
-- `aeo status` passes the person and the reason through two transaction-local settings; with none, it is 'system'.
CREATE OR REPLACE FUNCTION log_business_status_history()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO status_history (business_id, status, reason, set_by)
        VALUES (
            NEW.id,
            NEW.status,
            NULLIF(current_setting('aeo.status_reason', true), ''),
            COALESCE(NULLIF(current_setting('aeo.status_set_by', true), ''), 'system')
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_businesses_status_history_insert
    AFTER INSERT ON businesses
    FOR EACH ROW
    EXECUTE FUNCTION log_business_status_history();

CREATE TRIGGER trg_businesses_status_history_update
    AFTER UPDATE ON businesses
    FOR EACH ROW
    EXECUTE FUNCTION log_business_status_history();

-- Businesses that already exist: rebuild their history from the journal, so nothing before today is missing.
-- First the status each one was added with (the 'from' of its first change, else its current status)...
INSERT INTO status_history (business_id, status, reason, set_by, set_at)
SELECT b.id,
       COALESCE((SELECT j.details->>'from' FROM client_journal j
                 WHERE j.business_id = b.id AND j.entry_type = 'status_change'
                 ORDER BY j.created_at, j.id LIMIT 1), b.status),
       NULL, 'system (backfilled)', b.created_at
FROM businesses b
ORDER BY b.created_at, b.id;

-- ...then every change that was logged, with the reason from the note linked to it (PLAN.md section 7) when there is one.
INSERT INTO status_history (business_id, status, reason, set_by, set_at)
SELECT j.business_id,
       j.details->>'to',
       (SELECT n.details->>'reason' FROM client_journal n
        WHERE n.related_table = 'client_journal' AND n.related_id = j.id AND n.entry_type = 'note'
        ORDER BY n.created_at, n.id LIMIT 1),
       CASE WHEN j.actor = 'system' THEN 'system (backfilled)' ELSE j.actor END,
       j.created_at
FROM client_journal j
WHERE j.entry_type = 'status_change'
ORDER BY j.created_at, j.id;
