-- PLAN.md section 9: the diagnoses table, plus two new client_journal entry
-- types ('diagnosed', 'decline_recommended'). This is the minimal slice that
-- `aeo diagnose` needs; probe_results / probe_runs / questions changes belong
-- to the migration that ships with `aeo audit`.

CREATE TABLE diagnoses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nullable on purpose: `aeo diagnose <url>` can run on a bare URL with no
    -- business attached. When --business is given, this is set.
    business_id UUID REFERENCES businesses(id),
    checkpoint TEXT,
    url TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    platform TEXT,
    platform_version TEXT,
    platform_plan TEXT,
    robots_txt TEXT,            -- raw, exactly as served
    existing_jsonld JSONB,      -- raw blocks, as found
    crawler_access JSONB,       -- per bot: UA, status, verdict
    render JSONB,               -- word count, h1 texts, which details present
    findings JSONB,             -- [{code, severity, what, evidence, fixable_on_platform, where_to_fix, source}]
    verdict TEXT NOT NULL
        CHECK (verdict IN ('serve', 'conditional', 'decline')),
    verdict_reason TEXT,
    readiness_score NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_diagnoses_business_checkpoint ON diagnoses (business_id, checkpoint);

-- Postgres cannot edit a CHECK in place, so drop and re-add it with the two
-- new values. The old list (migration 011) is kept exactly; nothing removed.
ALTER TABLE client_journal DROP CONSTRAINT client_journal_entry_type_check;

ALTER TABLE client_journal ADD CONSTRAINT client_journal_entry_type_check
    CHECK (entry_type IN (
        'business_added', 'status_change', 'profile_created', 'questionnaire_received',
        'gate_checked', 'gate_decision', 'probe_started', 'probe_completed',
        'score_computed', 'audit_generated', 'audit_sent', 'recommendations_generated',
        'schema_deployed', 'content_published', 'note', 'error',
        'diagnosed', 'decline_recommended'
    ));
