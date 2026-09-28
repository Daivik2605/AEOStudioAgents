CREATE TABLE visibility_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    probe_run_id UUID NOT NULL REFERENCES probe_runs(id),
    formula_version TEXT NOT NULL,
    visibility_score NUMERIC,
    gap_score NUMERIC,
    opportunity_score NUMERIC,
    accuracy_score NUMERIC,
    recognition_rate NUMERIC,
    tier TEXT
        CHECK (tier IN ('Invisible', 'Barely Visible', 'Partially Visible', 'Visible', 'Dominant')),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (probe_run_id, formula_version)
);
