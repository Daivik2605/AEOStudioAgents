-- PLAN.md steps 6-7: what `aeo audit --mode manual` needs to store.
--
-- probe_runs.gate_check_id becomes nullable. Gate checks belong to the old
-- four-agent pipeline; business-profiler and formal gate checks are deferred
-- (PLAN.md section 15), and manual mode has no API spend to gate on.
-- business_profile_id stays NOT NULL: `aeo add` always creates a real profile.
ALTER TABLE probe_runs ALTER COLUMN gate_check_id DROP NOT NULL;

-- Free text, e.g. old_site, new_site_no_aeo, post_aeo, quarterly_2027_Q1.
ALTER TABLE probe_runs ADD COLUMN checkpoint TEXT;

-- retrieval_activated is nullable on purpose: NULL means "the operator could
-- not tell whether the engine searched the web".
ALTER TABLE probe_results
    ADD COLUMN retrieval_activated BOOLEAN,
    ADD COLUMN engine_version TEXT,         -- model AND reasoning mode, e.g. gpt-5-thinking
    ADD COLUMN logged_in_state TEXT,
    ADD COLUMN question_set_version TEXT,
    ADD COLUMN named_any_business BOOLEAN,  -- derived by the extraction step
    ADD COLUMN recommended BOOLEAN;         -- derived: our business proposed as the answer
