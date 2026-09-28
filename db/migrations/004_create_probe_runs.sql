CREATE TABLE probe_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id),
    business_profile_id UUID NOT NULL REFERENCES business_profiles(id),
    gate_check_id UUID NOT NULL REFERENCES gate_checks(id),
    run_type TEXT NOT NULL
        CHECK (run_type IN ('visibility', 'accuracy')),
    mode TEXT NOT NULL
        CHECK (mode IN ('manual', 'automatic')),
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'complete', 'failed')),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
