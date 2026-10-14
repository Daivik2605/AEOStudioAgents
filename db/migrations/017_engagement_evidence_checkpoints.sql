-- PLAN.md step 13c (sections 2 and 10): engagement type and access level, the evidence table,
-- the four fixed checkpoint labels, and two new journal entry types ('work', 'event').

-- 1. Engagement. Both are nullable: NULL means "not recorded yet", which is different from
--    access_level 'none' ("we have no access").
ALTER TABLE businesses
    ADD COLUMN engagement_type TEXT
        CHECK (engagement_type IN ('aeo_only', 'web_dev_aeo', 'web_dev_only', 'migration_pitch', 'prospect')),
    ADD COLUMN access_level TEXT
        CHECK (access_level IN ('none', 'cms', 'hosting', 'full'));

-- 2. Journal entry types. Postgres cannot edit a CHECK in place, so drop and re-add it. The list from
--    migration 015 is kept exactly; only 'work' and 'event' are added.
ALTER TABLE client_journal DROP CONSTRAINT client_journal_entry_type_check;
ALTER TABLE client_journal ADD CONSTRAINT client_journal_entry_type_check
    CHECK (entry_type IN (
        'business_added', 'status_change', 'profile_created', 'questionnaire_received',
        'gate_checked', 'gate_decision', 'probe_started', 'probe_completed',
        'score_computed', 'audit_generated', 'audit_sent', 'recommendations_generated',
        'schema_deployed', 'content_published', 'note', 'error',
        'diagnosed', 'decline_recommended',
        'work', 'event'
    ));

-- 3. Checkpoints. Rename the two old labels we know about. Any OTHER value is left exactly as it is
--    (not guessed at): `aeo files migrate` lists them.
UPDATE diagnoses  SET checkpoint = 'before'     WHERE checkpoint = 'old_site';
UPDATE diagnoses  SET checkpoint = 'after_site' WHERE checkpoint = 'new_site_no_aeo';
UPDATE probe_runs SET checkpoint = 'before'     WHERE checkpoint = 'old_site';
UPDATE probe_runs SET checkpoint = 'after_site' WHERE checkpoint = 'new_site_no_aeo';

-- NOT VALID: the rule applies to every new row, but rows already in the table are not checked, so an
-- unknown old value cannot make this migration fail. NULL stays allowed (a bare-URL diagnosis has none).
ALTER TABLE diagnoses ADD CONSTRAINT diagnoses_checkpoint_check
    CHECK (checkpoint IS NULL OR checkpoint IN ('before', 'after_site', 'after_aeo', 'check')) NOT VALID;
ALTER TABLE probe_runs ADD CONSTRAINT probe_runs_checkpoint_check
    CHECK (checkpoint IS NULL OR checkpoint IN ('before', 'after_site', 'after_aeo', 'check')) NOT VALID;

-- 4. Evidence: things that came in. The file itself lives in clients/<domain>/evidence/; this row is the record.
CREATE TABLE evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    file_path TEXT NOT NULL,            -- relative to the client's folder, e.g. evidence/2026-10-14_cdn_report.pdf
    evidence_type TEXT,                 -- free text, e.g. cdn_report, ai_answer_screenshot, client_email
    source TEXT CHECK (source IN ('us', 'client')),
    captured_on DATE,                   -- NULL = not stated; never guessed
    period_start DATE,
    period_end DATE,
    checkpoint TEXT NOT NULL
        CHECK (checkpoint IN ('before', 'after_site', 'after_aeo', 'check')),
    note TEXT,
    finding_code TEXT,                  -- checked against diagnose/codes.py by the application
    client_visible BOOLEAN NOT NULL DEFAULT true,
    operator TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((period_start IS NULL) = (period_end IS NULL)),
    CHECK (period_end IS NULL OR period_end >= period_start)
);

CREATE INDEX idx_evidence_business ON evidence (business_id, checkpoint);
