-- PLAN.md step 19 (`aeo recommend`). The recommendations table (migration 008) could not hold two things
-- the step needs:
--
--   * a recommendation made WITHOUT an audit (`--no-audit`): visibility_probe_run_id was NOT NULL.
--   * which diagnosis it was built from, the robots.txt it produced, and the files it wrote.
--
-- The columns that were already there keep their meaning:
--   priority_fixes        the ordered fix list (access blockers, target questions, open findings, hygiene)
--   fact_fixes            the wrong facts the audit found
--   off_site_sources      domains AI cited where the client was absent
--   generated_jsonld      the validated schema.json (NULL if validation failed)
--   llms_txt              the generated llms.txt
--   validation_status     'valid' | 'invalid' | 'not_built'
--   validation_errors     why, when it is not valid
ALTER TABLE recommendations ALTER COLUMN visibility_probe_run_id DROP NOT NULL;

ALTER TABLE recommendations
    ADD COLUMN diagnosis_id UUID REFERENCES diagnoses(id),
    ADD COLUMN robots_txt TEXT,
    ADD COLUMN files JSONB,
    ADD COLUMN prioritised_by_audit BOOLEAN NOT NULL DEFAULT TRUE;

-- Every recommendation is for a diagnosis we can name; an audit is optional.
CREATE INDEX idx_recommendations_business ON recommendations (business_id, created_at);
